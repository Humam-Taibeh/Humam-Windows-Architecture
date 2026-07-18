#Requires -Version 5.1
<#
.SYNOPSIS
    00-Foundation.ps1 - shared runtime foundation for PULSE.

.DESCRIPTION
    Dot-sourced FIRST by src/backend/core.ps1. Everything here lands in the
    single shared script scope of core.ps1, so every later module can use it.

    Provides:
      - OS detection ($Script:OSBuild / IsWin11 / OSCaption / WindowsEditionID)
      - Log path + Write-Log and the whole console output vocabulary
      - Interactive prompt primitives (Ask-User, Read-Choice, ...) that are
        HARD-GUARDED by $Script:NonInteractive: when core.ps1 is launched by
        the GUI with -Task, no console is attached, so these must never block
        on Read-Host or pop UI. That contract is enforced here, once.
      - Invoke-WithRetry, Test-OSSupport / Test-EditionSupport
      - Registry read helper (Get-RegValue)
      - DRY-RUN PRIMITIVES: Test-DryRun / Invoke-Mutation / Set-RegValue /
        Remove-RegValue / Remove-RegKey. Every module routes its system
        mutations through these so `core.ps1 -WhatIf` simulates a full run
        (logging "[WHATIF] ..." lines) without changing the machine.

    CONTRACT: no function in this file mutates system state except through
    the dry-run primitives at the bottom.
#>

# ============================================================
#  OS DETECTION
# ============================================================
$Script:OSBuild   = [System.Environment]::OSVersion.Version.Build
$Script:IsWin11   = $Script:OSBuild -ge 22000
$Script:OSCaption = try { (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Caption } catch { "Unknown Windows Edition" }
$Script:WindowsEditionID = try { (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -Name EditionID -ErrorAction Stop).EditionID } catch { "Unknown" }

# ============================================================
#  ENSURE LOG DIRECTORY EXISTS
# ============================================================
$LogDir = "$env:USERPROFILE\Desktop"
if (-not (Test-Path $LogDir)) {
    New-Item -Path $LogDir -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
}
$Script:LogPath = Join-Path $LogDir "Pulse_Log.txt"

# ============================================================
#  GLOBAL STATE
# ============================================================
$Global:UIWidth             = 63
$Global:PanelWidth          = 54
$Script:RestorePointCreated = $false
$Script:PendingRestart      = $false
$Script:SessionSuccessCount = 0
$Script:SessionFailCount    = 0
$Script:LastBulkChoice      = $null

# ---- SAFETY NET STATE (v3.3+) ---------------------------------------------
$Script:ScriptRestorePointSeq        = $null
$Script:TweaksBackupRegPath          = "HKCU:\Software\Pulse\TweakBackups"
$Script:ServicesBackupRegPath        = "HKCU:\Software\Pulse\ServiceBackups"
$Script:ServicesDisabledThisSession  = New-Object System.Collections.ArrayList
$Script:SessionLogEntries            = New-Object System.Collections.ArrayList
$Script:EdgeBackupFolder             = "$env:USERPROFILE\Desktop\Pulse_EdgeBackup"
$Script:OneDriveBackupFolder         = "$env:USERPROFILE\Desktop\Pulse_OneDriveBackup"

# ---- ONE-TIME MIGRATION FROM THE PRE-REBRAND IDENTITY (v5.x) --------------
# Machines upgrading from "HTCoreArchitecture" keep their tweak/service
# snapshots and disabled-startup records: the whole legacy registry root is
# copied to HKCU:\Software\Pulse once, then the old root is left untouched.
$LegacyRegRoot = "HKCU:\Software\HTCoreArchitecture"
if ((Test-Path $LegacyRegRoot) -and -not (Test-Path "HKCU:\Software\Pulse")) {
    try { Copy-Item -Path $LegacyRegRoot -Destination "HKCU:\Software\Pulse" -Recurse -ErrorAction Stop } catch {}
}

$Script:BoxTL = [string][char]0x2554
$Script:BoxTR = [string][char]0x2557
$Script:BoxBL = [string][char]0x255A
$Script:BoxBR = [string][char]0x255D
$Script:BoxH  = [string][char]0x2550
$Script:BoxV  = [string][char]0x2551
$Script:LineH = [string][char]0x2500
$Script:Check = [string][char]0x2713
$Script:Cross = [string][char]0x2717

# ============================================================
#  LOGGING & CONSOLE OUTPUT VOCABULARY
# ============================================================
function Write-Log {
    param([string]$Message)
    $Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    try {
        Add-Content -Path $Script:LogPath -Value "[$Stamp] $Message" -ErrorAction SilentlyContinue
    } catch {}
    try {
        [void]$Script:SessionLogEntries.Add("[$Stamp] $Message")
    } catch {}
}

function Write-Divider {
    Write-Host ("   " + ($Script:LineH * $Global:UIWidth)) -ForegroundColor DarkGray
}

function Center-Text {
    param([string]$Text, [int]$Width)
    if ([string]::IsNullOrEmpty($Text)) { return " " * $Width }
    if ($Text.Length -ge $Width) { return $Text }
    $TotalPad = $Width - $Text.Length
    $Left     = [math]::Floor($TotalPad / 2)
    $Right    = $TotalPad - $Left
    return (" " * $Left) + $Text + (" " * $Right)
}

function Get-AutoBoxWidth {
    param([string[]]$Lines, [int]$MinWidth = $Global:PanelWidth)
    $MaxLen = $MinWidth
    foreach ($L in $Lines) {
        if ($null -ne $L -and $L.Length -gt $MaxLen) { $MaxLen = $L.Length }
    }
    return $MaxLen
}

function Write-Banner {
    param([string]$Title, [string]$Subtitle = "")
    Clear-Host
    $BoxWidth = Get-AutoBoxWidth -Lines @($Title, $Subtitle) -MinWidth $Global:UIWidth
    Write-Host ""
    Write-Host ("   " + $Script:BoxTL + ($Script:BoxH * $BoxWidth) + $Script:BoxTR) -ForegroundColor Cyan
    Write-Host ("   " + $Script:BoxV + (Center-Text $Title $BoxWidth) + $Script:BoxV) -ForegroundColor Cyan
    if ($Subtitle) {
        Write-Host ("   " + $Script:BoxV + (Center-Text $Subtitle $BoxWidth) + $Script:BoxV) -ForegroundColor DarkGray
    }
    Write-Host ("   " + $Script:BoxBL + ($Script:BoxH * $BoxWidth) + $Script:BoxBR) -ForegroundColor Cyan
    Write-Host ""
}

function Write-SectionHeader {
    param([string]$Text)
    Write-Host ""
    Write-Host "   $Text" -ForegroundColor Cyan
    Write-Divider
}

function Write-StatusPanel {
    param([string]$Label, [string]$Text, [int]$Width = $Global:PanelWidth)
    $Content  = " ${Label}: $Text"
    $BoxWidth = Get-AutoBoxWidth -Lines @($Content) -MinWidth $Width
    Write-Host ("   " + $Script:BoxTL + ($Script:BoxH * $BoxWidth) + $Script:BoxTR) -ForegroundColor DarkGray
    Write-Host ("   " + $Script:BoxV + $Content.PadRight($BoxWidth) + $Script:BoxV) -ForegroundColor DarkGray
    Write-Host ("   " + $Script:BoxBL + ($Script:BoxH * $BoxWidth) + $Script:BoxBR) -ForegroundColor DarkGray
}

function Write-ModulePreview {
    param([string[]]$Items, [int]$Width = $Global:PanelWidth)
    $Title = "MODULE PREVIEW"
    $Lines = @()
    foreach ($Item in $Items) { $Lines += " - $Item" }
    $BoxWidth = Get-AutoBoxWidth -Lines (@($Title) + $Lines) -MinWidth $Width
    Write-Host ("   " + $Script:BoxTL + ($Script:BoxH * $BoxWidth) + $Script:BoxTR) -ForegroundColor DarkCyan
    Write-Host ("   " + $Script:BoxV + (Center-Text $Title $BoxWidth) + $Script:BoxV) -ForegroundColor DarkCyan
    Write-Host ("   " + $Script:BoxV + (" " * $BoxWidth) + $Script:BoxV) -ForegroundColor DarkCyan
    foreach ($Line in $Lines) {
        Write-Host ("   " + $Script:BoxV + $Line.PadRight($BoxWidth) + $Script:BoxV) -ForegroundColor Gray
    }
    Write-Host ("   " + $Script:BoxBL + ($Script:BoxH * $BoxWidth) + $Script:BoxBR) -ForegroundColor DarkCyan
    Write-Host ""
}

function Write-Info    { param($Text) Write-Host "   $Text" -ForegroundColor DarkGray; Write-Log $Text }
function Write-Success { param($Text) Write-Host "   $Script:Check  $Text" -ForegroundColor Green; Write-Log "SUCCESS: $Text"; $Script:SessionSuccessCount++ }
function Write-Warn    { param($Text) Write-Host "   !  $Text" -ForegroundColor Yellow; Write-Log "WARN: $Text" }
function Write-ErrorX  { param($Text) Write-Host "   $Script:Cross  $Text" -ForegroundColor Red; Write-Log "ERROR: $Text"; $Script:SessionFailCount++ }
function Write-AlreadyOK { param($Text) Write-Host "   $Script:Check  $Text" -ForegroundColor DarkCyan; Write-Log "ALREADY-OK: $Text" }

# ============================================================
#  INTERACTIVE PROMPT PRIMITIVES (NonInteractive-guarded)
# ============================================================
function Ask-User {
    param($Title, $Explanation)
    if ($Script:NonInteractive) {
        # Running as a GUI task: clicking the sidebar button IS the user's
        # confirmation. There is no console for Read-Host to wait on, so we
        # must not block here - auto-confirm and log it instead.
        Write-Log "AUTO-CONFIRM (GUI task, no console attached): $Title"
        return $true
    }
    Write-Host ""
    Write-Divider
    Write-Host "   $Title" -ForegroundColor Yellow
    Write-Host "   $Explanation" -ForegroundColor DarkGray
    Write-Divider
    while ($true) {
        $Response = Read-Host "   Execute this operation? (y/n)"
        switch ($Response.Trim().ToLower()) {
            'y' { return $true }
            'n' { return $false }
            default { Write-Host "   Please enter 'y' or 'n'." -ForegroundColor DarkYellow }
        }
    }
}

function Read-Choice {
    param([string]$Prompt, [string[]]$Valid)
    while ($true) {
        $Ans = (Read-Host $Prompt).Trim().ToLower()
        if ($Valid -contains $Ans) { return $Ans }
        Write-Host "   Invalid choice. Please enter one of: $($Valid -join '/')" -ForegroundColor DarkYellow
    }
}

function Read-NumericChoice {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][int]$Max,
        [string]$CancelKey = 'x'
    )
    if ($Max -lt 1) { return $null }
    $Valid = @()
    for ($n = 1; $n -le $Max; $n++) { $Valid += "$n" }
    $Valid += $CancelKey
    $Ans = Read-Choice -Prompt "$Prompt (1-$Max, or $($CancelKey.ToUpper()) to cancel)" -Valid $Valid
    if ($Ans -eq $CancelKey) { return $null }
    return [int]$Ans
}

# ============================================================
#  SUPPORT / GUARD HELPERS
# ============================================================
function Get-RegValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path $Path)) { return $null }
    try { return (Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop).$Name } catch { return $null }
}

function Test-OSSupport {
    param(
        [string]$FeatureName,
        [int]$MinBuild = 0,
        [int]$MaxBuild = 999999
    )
    if ($Script:OSBuild -lt $MinBuild -or $Script:OSBuild -gt $MaxBuild) {
        Write-Warn "Skipped '$FeatureName': not supported on this Windows build (detected build $Script:OSBuild, edition: $Script:OSCaption)."
        return $false
    }
    return $true
}

function Test-EditionSupport {
    param(
        [string]$FeatureName,
        [string[]]$UnsupportedEditionMatches = @()
    )
    foreach ($Pattern in $UnsupportedEditionMatches) {
        if ($Script:WindowsEditionID -like "*$Pattern*") {
            Write-Warn "Skipped '$FeatureName': not available on this Windows edition ($Script:WindowsEditionID)."
            return $false
        }
    }
    return $true
}

function Invoke-WithRetry {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [Parameter(Mandatory = $true)][string]$OperationName
    )
    do {
        try {
            & $Action
            return $true
        } catch {
            Write-ErrorX "'$OperationName' failed: $($_.Exception.Message)"
            if ($Script:NonInteractive) {
                Write-Log "GUI task: not prompting for retry on '$OperationName' (no console attached). Reporting failure."
                return $false
            }
            if (-not (Ask-User "Retry '$OperationName'?" "The operation failed and was logged. You can retry it now, or skip it and keep using the menu normally.")) {
                return $false
            }
        }
    } while ($true)
}

# ============================================================
#  DRY-RUN PRIMITIVES (-WhatIf engine)
#  Every system mutation in every module flows through one of
#  these four gates (or through an explicit Test-DryRun check),
#  which is what makes `core.ps1 -WhatIf` a complete simulation.
# ============================================================
function Test-DryRun {
    <# Returns $true when -WhatIf is active, after announcing and logging
       the operation that WOULD have run. Callers early-return on $true. #>
    param([Parameter(Mandatory = $true)][string]$Operation)
    if (-not $Script:DryRun) { return $false }
    Write-Host "   [WHATIF] $Operation" -ForegroundColor DarkYellow
    Write-Log "WHATIF: $Operation"
    return $true
}

function Invoke-Mutation {
    <# Generic guarded mutation: runs $Action verbatim, or logs a [WHATIF]
       line and returns $null in dry-run mode. Use for one-off mutations
       (process kills, external tools, file moves) where the original
       error-handling semantics must be preserved exactly. #>
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    if (Test-DryRun $Description) { return $null }
    return (& $Action)
}

function Set-RegValue {
    <# Guarded registry write: creates the key path if missing, then sets
       the value. Throws on failure (callers keep their own try/catch). #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Value,
        [string]$Type
    )
    if (Test-DryRun "Set registry value $Path\$Name = '$Value'") { return }
    if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
    if ($Type) {
        Set-ItemProperty -Path $Path -Name $Name -Value $Value -Type $Type -Force -ErrorAction Stop
    } else {
        Set-ItemProperty -Path $Path -Name $Name -Value $Value -Force -ErrorAction Stop
    }
}

function Remove-RegValue {
    <# Guarded registry value removal (best-effort, mirrors the original
       -ErrorAction SilentlyContinue call sites). #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (Test-DryRun "Remove registry value $Path\$Name") { return }
    if (Test-Path $Path) { Remove-ItemProperty -Path $Path -Name $Name -ErrorAction SilentlyContinue }
}

function Remove-RegKey {
    <# Guarded recursive registry key removal. Throws on failure so callers
       inside Invoke-WithRetry keep their retry semantics. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-DryRun "Remove registry key $Path") { return }
    if (Test-Path $Path) { Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop }
}
