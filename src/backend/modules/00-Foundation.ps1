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
# OSCaption is resolved LAZILY (Get-OSCaption, below) - Get-CimInstance talks
# to the WMI provider host, which is a genuine (and highly variable - a cold
# provider host, AV real-time scanning, or a flaky WMI repository can turn
# this into several seconds) per-PROCESS cost. Every core.ps1 -Task launch
# dot-sources this file fresh, so an eager call here taxed EVERY task - GUI
# tasks with a tight timeout (like the Startup Manager's scan) were the ones
# that could actually time out because of it, even though the caption is
# only ever read by a rarely-hit edition-support warning and the interactive
# console banner (neither is on the hot path of a GUI task).
$Script:OSCaption = $null
$Script:WindowsEditionID = try { (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -Name EditionID -ErrorAction Stop).EditionID } catch { "Unknown" }

function Get-OSCaption {
    <# Computes-and-caches Win32_OperatingSystem.Caption on first actual
       use instead of paying the WMI round-trip on every task launch. #>
    if ($null -eq $Script:OSCaption) {
        $Script:OSCaption = try { (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Caption } catch { "Unknown Windows Edition" }
    }
    return $Script:OSCaption
}

# ============================================================
#  TRUSTED BINARY PATHS  (v1.0 - PATH-hijack hardening)
# ============================================================
# PULSE RUNS ELEVATED, AND A BARE EXECUTABLE NAME IS A SEARCH, NOT A PATH.
#
# `Start-Process explorer` or `& winget` resolves the name through
# $env:PATH, and PATH is assembled from HKCU as well as HKLM - a registry
# hive the UNELEVATED user owns and can write freely. Anything that drops
# an "explorer.exe" into a directory sitting earlier in PATH than the real
# one gets it launched WITH PULSE'S ADMINISTRATOR TOKEN, from a script the
# user believes only touches Windows' own tools. Naming the absolute path
# deletes the search, and with it the substitution.
#
# Resolved from the environment rather than a literal "C:\Windows": the
# system drive is not guaranteed to be C:, and GetFolderPath reads the same
# value the loader itself uses. On a 32-bit host process 'System' correctly
# yields SysWOW64, which is where that process's stock tools genuinely are.
$Script:WindowsDir  = [System.Environment]::GetFolderPath('Windows')
$Script:System32Dir = [System.Environment]::GetFolderPath('System')

# The stock tools Pulse shells out to. explorer.exe lives in the Windows
# ROOT, not System32 - a detail worth encoding once here rather than
# rediscovering at each call site.
$Script:SystemBinaries = @{
    'powershell' = Join-Path $Script:System32Dir 'WindowsPowerShell\v1.0\powershell.exe'
    'explorer'   = Join-Path $Script:WindowsDir  'explorer.exe'
    'taskmgr'    = Join-Path $Script:System32Dir 'taskmgr.exe'
    'ie4uinit'   = Join-Path $Script:System32Dir 'ie4uinit.exe'
    'msiexec'    = Join-Path $Script:System32Dir 'msiexec.exe'
    'rundll32'   = Join-Path $Script:System32Dir 'rundll32.exe'
    'cmd'        = Join-Path $Script:System32Dir 'cmd.exe'
    'sc'         = Join-Path $Script:System32Dir 'sc.exe'
    'reg'        = Join-Path $Script:System32Dir 'reg.exe'
    # v1.1: the WORKER tools. Every one of these was being invoked by bare
    # name from an elevated process - the exact hole the block above exists
    # to close, missed because the guard test only looked for the
    # `Start-Process x` / `& x` shapes and these are mostly direct calls
    # (`powercfg /list`, `ipconfig /flushdns`). powercfg is the worst of
    # them: the state probe reads the active scheme with it on launch AND
    # after every task, making it the most frequently executed shell-out
    # in the app.
    'powercfg'   = Join-Path $Script:System32Dir 'powercfg.exe'
    'sfc'        = Join-Path $Script:System32Dir 'sfc.exe'
    # Dism.exe, not DISM - the file really is mixed-case on disk. The name
    # is irrelevant to Windows but not to a future reader diffing this.
    'dism'       = Join-Path $Script:System32Dir 'Dism.exe'
    'ipconfig'   = Join-Path $Script:System32Dir 'ipconfig.exe'
    'netsh'      = Join-Path $Script:System32Dir 'netsh.exe'
    'cleanmgr'   = Join-Path $Script:System32Dir 'cleanmgr.exe'
    'robocopy'   = Join-Path $Script:System32Dir 'Robocopy.exe'
}

function Get-SystemBinary {
    <#
    .SYNOPSIS
        Absolute path to a stock Windows tool, by short name.

    .DESCRIPTION
        Returns the anchored path unconditionally - including when the file
        is missing. That is deliberate: falling back to the bare name on a
        Test-Path miss would restore the exact PATH search this function
        exists to remove, and would do it precisely on the machines whose
        System32 is already unusual. A genuinely absent stock tool is a
        broken Windows, and the caller's own error handling should say so
        rather than silently running whatever PATH offers instead.

        An unknown name is a programming error, not a runtime condition, so
        it throws.
    #>
    param([Parameter(Mandatory)][string]$Name)

    if (-not $Script:SystemBinaries.ContainsKey($Name)) {
        throw "Get-SystemBinary: '$Name' is not a known system binary."
    }
    $Path = $Script:SystemBinaries[$Name]
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-Log "WARN: system binary '$Name' not found at '$Path'."
    }
    return $Path
}

function ConvertTo-WqlLiteral {
    <#
    .SYNOPSIS
        Escape a string for safe interpolation into a WQL string literal.

    .DESCRIPTION
        WQL quotes strings with ' and escapes with backslash, so a value
        carrying either character ends the literal early and the remainder
        is parsed as QUERY rather than as data - the WMI equivalent of SQL
        injection. Service and product names reach these filters from the
        registry and from the machine's own installed-software list, i.e.
        from places a caller does not control.

        Backslash MUST be escaped first: doing the quote first would then
        have its own escaping backslash escaped by the second pass, undoing
        it.
    #>
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)

    return $Value.Replace('\', '\\').Replace("'", "\'")
}

function Test-SafeWebUrl {
    <#
    .SYNOPSIS
        Is this a plain http/https URL that is safe to hand to the shell?

    .DESCRIPTION
        `Start-Process $url` is ShellExecute: it launches whatever the
        string resolves to. A value that is not actually a web address -
        a local path, a UNC share, a file:// or ms-settings: URI - runs or
        opens THAT instead, which is not what any caller passing a
        "download page" means. Only http and https survive.
    #>
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Url)

    $Parsed = $null
    if (-not [System.Uri]::TryCreate($Url, [System.UriKind]::Absolute, [ref]$Parsed)) {
        return $false
    }
    return @('http', 'https') -contains $Parsed.Scheme
}

# ============================================================
#  USER-HIVE TARGETING  (v1.0 - the split-token problem)
# ============================================================
# HKCU: IS NOT "THE USER" - IT IS "WHOEVER OWNS THIS TOKEN".
#
# When a standard user elevates Pulse by typing a DIFFERENT account's
# administrator credentials into the UAC dialog - the "over the shoulder"
# flow, which is the normal case on managed and Enterprise machines, and on
# any home PC whose daily account is not itself an admin - the elevated
# process belongs to that administrator. HKCU: then resolves to the
# ADMINISTRATOR's hive, not the hive of the person sitting at the desktop.
#
# Every per-user tweak Pulse applies is an HKCU write: dark mode, mouse
# acceleration, taskbar layout, Game Mode, the advertising ID
# ($Script:TweakCatalog in 01-Catalogs.ps1). Under a split token all of them
# landed on a profile nobody was looking at.
#
# THE REASON THIS WAS INVISIBLE, AND WHY IT IS THE WORST KIND OF BUG:
# 11-StateProbe.ps1 reads the same HKCU:, so it read back exactly what had
# just been written and the card lit up "Applied". The rollback snapshots in
# 02-Safety.ps1 went to the administrator's hive too, so "Reset All Tweaks"
# had nothing to restore for the real user. The system reported complete,
# consistent success for work that had no effect on the machine the user saw.
#
# THE FIX: resolve the DESKTOP user's SID and rewrite HKCU: paths to that
# user's subtree of HKEY_USERS. Their hive is already loaded - they are
# signed in - so this needs no `reg load` and no impersonation. When the
# token and the desktop user are the same account (the common case: an admin
# elevating themselves, or an unelevated run) nothing is rewritten at all and
# this costs one cached comparison.
$Script:UserHiveRoot         = $null    # $null = HKCU: is already correct
$Script:IsSplitToken         = $false
$Script:SplitTokenAccount    = $null    # the desktop user, for the notice
$Script:SplitTokenUnresolved = $false   # split detected, hive NOT reachable
$Script:SplitTokenNoticeShown = $false
$Script:_UserHiveResolved    = $false

function Test-IsElevatedSession {
    <# Cheap, local, no WMI - unlike Get-OSCaption this is safe to call on
       the hot path. #>
    try {
        return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
            [Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Get-InteractiveUserSid {
    <# SID of the account owning the interactive desktop session, or $null.

       Win32_ComputerSystem.UserName is the console session's user; it is
       $null on a machine with nobody signed in interactively (a scheduled
       task, a service, an SSH session), which is a legitimate answer meaning
       "there is no desktop user to target". #>
    try {
        $Account = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).UserName
        if ([string]::IsNullOrWhiteSpace($Account)) { return $null }
        $Sid = (New-Object System.Security.Principal.NTAccount($Account)).Translate(
            [System.Security.Principal.SecurityIdentifier])
        return [PSCustomObject]@{ Account = $Account; Sid = $Sid.Value }
    } catch {
        Write-Log "Could not resolve the interactive desktop user: $($_.Exception.Message)"
        return $null
    }
}

function Initialize-UserHiveTargeting {
    <# Resolves $Script:UserHiveRoot once per process.

       Only pays the WMI cost when the session is actually elevated, because
       an unelevated Pulse cannot be running as anyone but the desktop user -
       there is no split to detect. #>
    if ($Script:_UserHiveResolved) { return }
    $Script:_UserHiveResolved = $true

    if (-not (Test-IsElevatedSession)) { return }

    $TokenSid = try {
        [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    } catch { $null }
    if (-not $TokenSid) { return }

    $Desktop = Get-InteractiveUserSid
    if (-not $Desktop) { return }
    if ($Desktop.Sid -eq $TokenSid) { return }   # same account - nothing to do

    $Script:IsSplitToken = $true
    $Script:SplitTokenAccount = $Desktop.Account

    # The desktop user is signed in, so their hive is mounted under
    # HKEY_USERS. If it somehow is not, do NOT silently fall back to HKCU: -
    # that is the exact wrong-profile write this whole block exists to stop.
    # Flag it instead and let the tweak paths refuse.
    $Candidate = "Registry::HKEY_USERS\$($Desktop.Sid)"
    if (Test-Path $Candidate) {
        $Script:UserHiveRoot = $Candidate
        Write-Log "SPLIT TOKEN: elevated as a different account than the desktop user '$($Desktop.Account)'. Per-user settings will be written to that user's hive ($($Desktop.Sid))."
    } else {
        $Script:SplitTokenUnresolved = $true
        Write-Log "SPLIT TOKEN: desktop user '$($Desktop.Account)' ($($Desktop.Sid)) has no loaded hive under HKEY_USERS. Per-user tweaks cannot be targeted correctly."
    }
}

function Resolve-UserRegPath {
    <# Rewrites an HKCU: path onto the desktop user's hive when this session
       is elevated as somebody else. A no-op in every other case, and
       IDEMPOTENT - an already-rewritten path no longer starts with HKCU:,
       so passing a path through twice is harmless (several call sites do).

       Only HKCU: is touched. HKLM:, HKU: and provider-qualified paths are
       machine-wide or already explicit and are returned verbatim. #>
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not $Script:_UserHiveResolved) { Initialize-UserHiveTargeting }
    if (-not $Script:UserHiveRoot) { return $Path }
    if ($Path -notlike 'HKCU:\*') { return $Path }

    $Relative = $Path.Substring(6)      # strip "HKCU:\"

    # HKCU\Software\Classes is a symlink to HKU\<SID>_Classes, not a child of
    # HKU\<SID>. The classic-context-menu tweak writes a CLSID there, so
    # mapping it to the plain subtree would put the key somewhere Explorer
    # never reads.
    if ($Relative -eq 'Software\Classes' -or $Relative -like 'Software\Classes\*') {
        $Tail = $Relative.Substring('Software\Classes'.Length).TrimStart('\')
        $ClassesRoot = "$($Script:UserHiveRoot)_Classes"
        if ([string]::IsNullOrEmpty($Tail)) { return $ClassesRoot }
        return "$ClassesRoot\$Tail"
    }

    return "$($Script:UserHiveRoot)\$Relative"
}

function Test-UserHiveWritable {
    <# $false only when a split token was detected AND the desktop user's
       hive could not be reached - the one case where an HKCU write would
       silently hit the wrong profile. Callers report this as a failure
       rather than proceeding. #>
    if (-not $Script:_UserHiveResolved) { Initialize-UserHiveTargeting }
    return -not $Script:SplitTokenUnresolved
}

function Write-SplitTokenNotice {
    <# Surfaces the split ONCE per process, on the console and in the GUI's
       live output. Silence here would leave the user with per-user settings
       applied to an account they never see, which is precisely the failure
       mode that made this bug so expensive to find. #>
    if (-not $Script:_UserHiveResolved) { Initialize-UserHiveTargeting }
    if (-not $Script:IsSplitToken -or $Script:SplitTokenNoticeShown) { return }
    $Script:SplitTokenNoticeShown = $true
    if ($Script:SplitTokenUnresolved) {
        Write-Warn "Pulse is elevated as a different account than the signed-in user ($Script:SplitTokenAccount), and that user's settings hive is not reachable. Per-user tweaks will be refused rather than applied to the wrong profile. Sign in as an administrator, or run Pulse without elevating, to change per-user settings."
    } else {
        Write-Info "Elevated as a different account than the signed-in user - per-user settings are being applied to '$Script:SplitTokenAccount' (the desktop user), not to the administrator account."
    }
}

# ============================================================
#  LOG LOCATION (%LOCALAPPDATA%\Pulse\logs) + SIZE ROTATION
# ============================================================
# v6.1: the log moved off the Desktop - on OneDrive-synced Desktops every
# Add-Content line triggered sync traffic, and the file grew without bound.
$LogDir = Join-Path $env:LOCALAPPDATA "Pulse\logs"
if (-not (Test-Path $LogDir)) {
    New-Item -Path $LogDir -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
}
$Script:LogPath = Join-Path $LogDir "Pulse_Log.txt"

# One-time migration: pull an existing v6.0 Desktop log into the new home
# so history is preserved. (v5.x HTCore logs stay where they are.)
$DesktopLog = "$env:USERPROFILE\Desktop\Pulse_Log.txt"
if ((Test-Path $DesktopLog) -and -not (Test-Path $Script:LogPath)) {
    try { Move-Item -Path $DesktopLog -Destination $Script:LogPath -Force -ErrorAction Stop } catch {}
}

# Rotate at 5 MB, keep the 5 newest archives. Runs once per engine start -
# one Test-Path plus one Length read, effectively free.
if (Test-Path $Script:LogPath) {
    try {
        if ((Get-Item $Script:LogPath).Length -gt 5MB) {
            $Archive = Join-Path $LogDir ("Pulse_Log_{0}.txt" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
            Move-Item -Path $Script:LogPath -Destination $Archive -Force
            Get-ChildItem -Path $LogDir -Filter "Pulse_Log_*.txt" -File |
                Sort-Object Name -Descending | Select-Object -Skip 5 |
                Remove-Item -Force -ErrorAction SilentlyContinue
        }
    } catch {}
}

# ============================================================
#  GLOBAL STATE
# ============================================================
$Global:UIWidth             = 63
$Global:PanelWidth          = 54
$Script:RestorePointCreated = $false
$Script:PendingRestart      = $false
$Script:SessionSuccessCount = 0
$Script:SessionFailCount    = 0
$Script:SessionSkipCount    = 0
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

function Write-LogBatch {
    <# Writes many lines in ONE file append instead of N separate Write-Log
       calls. Add-Content opens/writes/closes the log file on every call;
       a scan that logs one line per discovered item (startup audit,
       driver scan, ...) turned that per-call overhead into an
       O(n) pile of file I/O that scales with however much is installed
       on the machine - a single batched append removes it entirely. #>
    param([string[]]$Messages)
    if (-not $Messages -or $Messages.Count -eq 0) { return }
    $Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Lines = @($Messages | ForEach-Object { "[$Stamp] $_" })
    try {
        Add-Content -Path $Script:LogPath -Value $Lines -ErrorAction SilentlyContinue
    } catch {}
    foreach ($Line in $Lines) {
        try { [void]$Script:SessionLogEntries.Add($Line) } catch {}
    }
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

# Strict status vocabulary - three colors, three meanings, everywhere:
#   Green  (check) = succeeded, working, or already in the desired state
#   Red    (cross)  = failed / critical error
#   Yellow (!)      = warning, missing dependency, or a notice
# Write-AlreadyOK intentionally shares Write-Success's green+check (the
# color scheme groups "up to date" under success - see Write-AlreadyOK's
# own note below); the wording is what tells the two apart, not the color.
function Write-Info    { param($Text) Write-Host "   $Text" -ForegroundColor DarkGray; Write-Log $Text }
function Write-Success { param($Text) Write-Host "   $Script:Check  $Text" -ForegroundColor Green; Write-Log "SUCCESS: $Text"; $Script:SessionSuccessCount++ }
function Write-Warn    { param($Text) Write-Host "   !  $Text" -ForegroundColor Yellow; Write-Log "WARN: $Text" }
function Write-ErrorX  { param($Text) Write-Host "   $Script:Cross  $Text" -ForegroundColor Red; Write-Log "ERROR: $Text"; $Script:SessionFailCount++ }
# Smart-skip / "already done" status - same green+check as Write-Success by
# design (previously a mismatched DarkCyan, which is exactly the kind of
# inconsistency a strict 3-color scheme rules out), counted separately so
# the session summary can distinguish "did work" from "nothing to do".
function Write-AlreadyOK { param($Text) Write-Host "   $Script:Check  $Text" -ForegroundColor Green; Write-Log "ALREADY-OK: $Text"; $Script:SessionSkipCount++ }

# ============================================================
#  GUI STRUCTURED DATA CHANNEL (v6.3)
#  Companion to the ##PULSE##SUCCESS|/ERROR| verdict contract: a task that
#  needs to hand the GUI more than one human-readable line (the winget
#  update scan, the startup report) emits exactly one
#      ##PULSE##DATA|<json>
#  line before its verdict. src/utils/helpers.py (PowerShellTask) parses it
#  into TaskResult.data and never prints it to the live console.
# ============================================================
function Write-GuiLine {
    <#
    .SYNOPSIS
        Writes one ##PULSE## wire-protocol line straight to stdout.

    .DESCRIPTION
        [Console]::Out, NOT Write-Output, and the difference is not stylistic.
        Write-Output writes to the PIPELINE - so a payload emitted from inside
        a function becomes part of that function's RETURN VALUE instead of
        reaching the pipe. That is not hypothetical: the streamed update scan
        calls its callbacks from inside Invoke-DeepUpdateScan, and with
        Write-Output every STAGE and ITEM line was silently captured into the
        scan's own result object. The frontend saw none of them, and the
        function's return value quietly became an array of strings with the
        real hashtable buried at the end.

        Console.Out has no such coupling: it is the process's stdout, which is
        the pipe helpers.py reads, regardless of which call frame emits it.
        Flushed explicitly so a streamed line arrives while it is still news -
        the whole point of the incremental channels is latency.
    #>
    param([Parameter(Mandatory = $true)][string]$Line)
    [Console]::Out.WriteLine($Line)
    [Console]::Out.Flush()
}

function Write-GuiData {
    <# Emits $Data (any JSON-serializable object, typically an array of
       PSCustomObjects) as one ##PULSE##DATA| line. ConvertTo-Json on
       Windows PowerShell 5.1 silently unwraps a single-element array to a
       bare JSON object - re-wrapped defensively here so the frontend can
       always assume "array in, array out" without a special case. #>
    param([Parameter(Mandatory = $true)]$Data)
    $Json = $Data | ConvertTo-Json -Depth 8 -Compress
    if ($Data -is [array] -and -not $Json.TrimStart().StartsWith('[')) {
        $Json = "[$Json]"
    }
    Write-GuiLine "##PULSE##DATA|$Json"
}

# ============================================================
#  GUI INCREMENTAL CHANNEL (v10.3)
#  ##PULSE##ITEM|<json>   one result, the moment it is known
#  ##PULSE##STAGE|<text>  what the task is doing right now
#
#  WHY THIS EXISTS. DATA is a single line emitted when a task has finished
#  assembling its whole payload, which is the right shape for a report and
#  the wrong shape for a scan. The update scan spends most of its time in
#  one long winget call, so the GUI sat on a shimmer bar for ~30 seconds
#  with nothing to show and no way to tell a slow scan from a hung one.
#
#  These two channels do not replace DATA - the scan still emits its
#  complete array at the end, so a caller that ignores streaming behaves
#  exactly as before. They are a progressive PREVIEW of that same payload:
#  every ITEM is an element the final DATA array will also contain, so the
#  frontend can render rows as they arrive and then reconcile against the
#  authoritative document without ever showing a row that later vanishes.
#
#  Both are payload lines: helpers.py routes them to their own signals and
#  keeps them out of the live console, exactly like DATA and META.
# ============================================================
function Write-GuiItem {
    <# Emits ONE result object as it is discovered. Never throws and never
       aborts the scan that produced it: a streamed preview row is a
       convenience, and losing one costs a few hundred milliseconds of
       progressive rendering, not a result - the final Write-GuiData payload
       still carries it. #>
    param([Parameter(Mandatory = $true)]$Item)
    try {
        $Json = $Item | ConvertTo-Json -Depth 6 -Compress
        Write-GuiLine "##PULSE##ITEM|$Json"
    } catch {
        Write-Log "Write-GuiItem failed (non-fatal): $($_.Exception.Message)"
    }
}

function Write-GuiStage {
    <# Emits the current phase of a long task as one human-readable line, for
       the GUI to show where a spinner alone would be. Plain text, not JSON:
       it is a sentence for a person, and giving it a schema would invite
       callers to put structure in it that belongs in ITEM. #>
    param([Parameter(Mandatory = $true)][string]$Text)
    # Newlines would split into two lines and orphan the second half of the
    # message as raw console output, so collapse any whitespace run to a space.
    $Clean = ($Text -replace '\s+', ' ').Trim()
    if ($Clean) { Write-GuiLine "##PULSE##STAGE|$Clean" }
}

# ============================================================
#  GUI STRUCTURED VERDICT METRICS (v10.3)
#  A THIRD channel, deliberately separate from ##PULSE##DATA|.
#
#  WHY NOT REUSE DATA: the frontend takes the LAST DATA line it sees, and
#  DATA already belongs to the task — ScanForUpdates returns its version
#  audit through it, StartupReport its startup inventory, GetTweakState its
#  applied-state map. Emitting a metrics envelope on the same channel after
#  the task's own payload would shadow it and silently break the Update
#  Center and the Startup Manager.
#
#  WHAT IT IS NOT: this is not a second opinion on success. The
#  ##PULSE##SUCCESS|/ERROR| verdict line remains the single source of
#  truth for whether a task passed — two competing outcome fields would
#  eventually disagree, and the one the GUI trusts must never be in doubt.
#  META carries measurements only: which task, how long, how much it did.
# ============================================================
function Write-GuiMeta {
    <# Emits one ##PULSE##META|<json> line of measurements about the task
       that just ran. Never throws: a metrics line that broke a task would
       be worse than no metrics at all. #>
    param([Parameter(Mandatory = $true)][hashtable]$Meta)
    try {
        $Json = [PSCustomObject]$Meta | ConvertTo-Json -Depth 6 -Compress
        Write-GuiLine "##PULSE##META|$Json"
    } catch {
        Write-Log "Write-GuiMeta failed (non-fatal): $($_.Exception.Message)"
    }
}

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
    # Resolve-UserRegPath so a read sees the same hive a write targets. If the
    # two ever disagreed, the state probe would report a tweak as applied on
    # the strength of a value in a profile the tweak did not touch.
    $Path = Resolve-UserRegPath $Path
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
        Write-Warn "Skipped '$FeatureName': not supported on this Windows build (detected build $Script:OSBuild, edition: $(Get-OSCaption))."
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

function Assert-UserRegPathTargetable {
    <# Throws when $Path is a per-user path we cannot target correctly.

       Only fires in the narrow split-token-with-unreachable-hive case. It
       throws rather than returning $false because every mutation call site
       already sits inside a try/catch that reports through Write-ErrorX -
       so this surfaces as an honest task failure instead of a write that
       "succeeds" against the administrator's profile. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if ($Path -notlike 'HKCU:\*') { return }
    if (Test-UserHiveWritable) { return }
    Write-SplitTokenNotice
    throw ("Refusing to write '$Path': Pulse is elevated as a different account than the signed-in user " +
           "($Script:SplitTokenAccount), whose settings hive is not reachable. Applying it here would change the " +
           "administrator's profile instead. Run Pulse without elevating to change per-user settings.")
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
    # Dry-run reports the path the USER asked for, not the rewritten one - a
    # [WHATIF] line naming HKEY_USERS\S-1-5-21-... would be accurate and
    # unreadable. The rewrite happens after the gate.
    if (Test-DryRun "Set registry value $Path\$Name = '$Value'") { return }
    Assert-UserRegPathTargetable $Path
    $Path = Resolve-UserRegPath $Path
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
    Assert-UserRegPathTargetable $Path
    $Path = Resolve-UserRegPath $Path
    if (Test-Path $Path) { Remove-ItemProperty -Path $Path -Name $Name -ErrorAction SilentlyContinue }
}

function Remove-RegKey {
    <# Guarded recursive registry key removal. Throws on failure so callers
       inside Invoke-WithRetry keep their retry semantics. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-DryRun "Remove registry key $Path") { return }
    Assert-UserRegPathTargetable $Path
    $Path = Resolve-UserRegPath $Path
    if (Test-Path $Path) { Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop }
}
