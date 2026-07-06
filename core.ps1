<#
.SYNOPSIS
    H.T. CORE ARCHITECTURE – Ultimate Masterpiece Edition (v3.3)
    Humam Taibeh's Windows Deployment & Optimization Framework
    -----------------------------------------------------------
    Luxury hierarchical menu, crystal-clear prompts, and full
    system optimization suite. Compatible with Windows 10/11.

    CHANGELOG v3.3 (Bulletproof Safety Release):
      - New "Safety & Recovery" hub on the main menu: one-click
        Rollback to this session's restore point, Reset ALL Tweaks
        to Windows Defaults, Restore All Services this tool changed,
        and an in-app Session Log viewer.
      - Every reversible tweak (Dark Mode, Mouse Acceleration,
        Taskbar alignment, Game Mode, Classic Context Menu,
        Telemetry, Advertising ID, Activity History) now snapshots
        its ORIGINAL value to the registry before changing anything,
        so "Reset All Tweaks" restores your real prior settings
        instead of just Microsoft's factory defaults when possible.
      - A System Restore point is created automatically (once per
        session) before the first registry/service/system change in
        ANY module, not only Performance & Gaming.
      - Removing Edge now backs up its version info + Preferences/
        Bookmarks/Favicons before uninstalling; reinstalling Edge
        offers to restore that backup automatically.
      - Removing OneDrive now offers to back up your local OneDrive
        folder to the Desktop first.
      - Every service this tool disables is snapshotted (startup
        type + status) so "Restore All Services" can put them back
        exactly as they were.
      - Failed operations can now be retried in place without
        exiting the menu (SFC/DISM, Edge removal/reinstall, restore
        point creation, rollback all support retry).
      - New-in-3.2 features retained unchanged: Git in the Dev
        catalog, PyCharm/NetBeans dependency auto-offers, hard
        input validation via Read-Choice, the Smart System Tweaks
        [X] fix, and the pending-restart flag on the main menu.
#>

#Requires -Version 5.1

# ============================================================
#  ELEVATION
# ============================================================
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    Exit
}

$Host.UI.RawUI.BackgroundColor = "Black"
$Host.UI.RawUI.ForegroundColor = "Gray"
$ErrorActionPreference = "Stop"
Clear-Host

$Script:ScriptVersion = "3.3"

# ============================================================
#  TWEAK CATALOG (Data-Driven Engine)
# ============================================================
$Script:TweakCatalog = @(
    @{
        Key         = "DarkMode"
        Category    = "Personalization"
        Description = "Switches Windows to dark theme (apps + system)."
        Entries = @(
            @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"; Name = "AppsUseLightTheme";   OnValue = 0; OffValue = 1; Type = "DWord" }
            @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize"; Name = "SystemUsesLightTheme"; OnValue = 0; OffValue = 1; Type = "DWord" }
        )
    },
    @{
        Key         = "GameMode"
        Category    = "Performance"
        Description = "Optimizes Windows for gaming, kills background recording."
        Entries = @(
            @{ Path = "HKCU:\Software\Microsoft\GameBar";           Name = "AllowAutoGameMode";               OnValue = 1; OffValue = 0; Type = "DWord" }
            @{ Path = "HKCU:\Software\Microsoft\GameBar";           Name = "AutoGameModeEnabled";              OnValue = 1; OffValue = 0; Type = "DWord" }
            @{ Path = "HKCU:\System\GameConfigStore";               Name = "GameDVR_Enabled";                  OnValue = 0; OffValue = 1; Type = "DWord" }
            @{ Path = "HKCU:\System\GameConfigStore";               Name = "GameDVR_FSEBehaviorMode";          OnValue = 2; OffValue = 0; Type = "DWord" }
            @{ Path = "HKCU:\System\GameConfigStore";               Name = "GameDVR_HonorUserFSEBehaviorMode"; OnValue = 1; OffValue = 0; Type = "DWord" }
            @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\GameDVR"; Name = "AppCaptureEnabled";   OnValue = 0; OffValue = 1; Type = "DWord" }
        )
    }
)

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
$Script:LogPath = Join-Path $LogDir "HTCoreArchitecture_Log.txt"

# ============================================================
#  PRE-FLIGHT WINGET BOOTSTRAP (silent, robust)
# ============================================================
function Invoke-WingetBootstrap {
    Write-Host ""
    Write-Host "   [*] Winget not found - launching silent bootstrap from Microsoft CDN..." -ForegroundColor DarkGray
    $tempDir = Join-Path $env:TEMP "WingetBootstrap_HTCore"
    New-Item -ItemType Directory -Path $tempDir -Force -ErrorAction SilentlyContinue | Out-Null

    $deps = @(
        @{ Name = "Microsoft.VCLibs.x64.14.00.Desktop.appx"; Url = "https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx" },
        @{ Name = "Microsoft.UI.Xaml.2.8.x64.appx";         Url = "https://aka.ms/Microsoft.UI.Xaml.2.8.x64.appx" }
    )

    foreach ($dep in $deps) {
        $dest = Join-Path $tempDir $dep.Name
        try {
            Write-Host "   [ ] Downloading $($dep.Name)..." -ForegroundColor DarkGray
            Invoke-WebRequest -Uri $dep.Url -OutFile $dest -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
        } catch {
            Write-Host "   [!] Failed to download $($dep.Name) - bootstrap may fail." -ForegroundColor Yellow
        }
    }

    $latestJson = $null
    try {
        $latestJson = Invoke-RestMethod -Uri "https://api.github.com/repos/microsoft/winget-cli/releases/latest" -TimeoutSec 15 -ErrorAction Stop
    } catch {
        Write-Host "   [X] Cannot reach winget-cli GitHub API. Bootstrap aborted." -ForegroundColor Red
        return $false
    }

    $asset = $latestJson.assets | Where-Object { $_.name -like "Microsoft.DesktopAppInstaller_*_8wekyb3d8bbwe.msixbundle" } | Select-Object -First 1
    if (-not $asset) {
        Write-Host "   [X] MSIX bundle asset not found in latest release." -ForegroundColor Red
        return $false
    }

    $bundleName = $asset.name
    $bundleUrl  = $asset.browser_download_url
    $bundleDest = Join-Path $tempDir $bundleName

    Write-Host "   [ ] Downloading $bundleName ..." -ForegroundColor DarkGray
    try {
        Invoke-WebRequest -Uri $bundleUrl -OutFile $bundleDest -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
    } catch {
        Write-Host "   [X] Download of App Installer bundle failed." -ForegroundColor Red
        return $false
    }

    $allPkgs = Get-ChildItem -Path $tempDir -Filter *.appx | Sort-Object Name
    $allPkgs += Get-ChildItem -Path $tempDir -Filter *.msixbundle
    foreach ($pkg in $allPkgs) {
        try {
            Add-AppxPackage -Path $pkg.FullName -ErrorAction Stop
            Write-Host "   [OK] Installed $($pkg.Name)" -ForegroundColor Green
        } catch {
            Write-Host "   [!] Could not install $($pkg.Name) - may already be present." -ForegroundColor Yellow
        }
    }

    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Start-Sleep -Seconds 2

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "   [OK] Winget bootstrapped successfully." -ForegroundColor Green
        return $true
    } else {
        Write-Host "   [X] Winget still unavailable after bootstrap. Manual install required." -ForegroundColor Red
        return $false
    }
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    $bootstrapped = Invoke-WingetBootstrap
    if (-not $bootstrapped) {
        Write-Host "   WARNING: Winget could not be provisioned automatically. App deployment modules will fall back to manual/official download links." -ForegroundColor Red
        Write-Host "   To install winget manually: open the Microsoft Store and search for 'App Installer', or download it directly from https://aka.ms/getwinget" -ForegroundColor DarkGray
        $global:WingetAvailable = $false
    } else {
        $global:WingetAvailable = $true
    }
} else {
    $global:WingetAvailable = $true
}

$global:ChocolateyAvailable = $false
if (Get-Command choco -ErrorAction SilentlyContinue) {
    $global:ChocolateyAvailable = $true
}

# ============================================================
#  GLOBAL STATE & LUXU 
# ============================================================
$Global:UIWidth             = 63
$Global:PanelWidth          = 54
$Script:RestorePointCreated = $false
$Script:PendingRestart      = $false
$Script:SessionSuccessCount = 0
$Script:SessionFailCount    = 0
$Script:LastBulkChoice      = $null

# ---- v3.3 SAFETY NET STATE -----------------------------------------------
$Script:ScriptRestorePointSeq        = $null
$Script:TweaksBackupRegPath          = "HKCU:\Software\HTCoreArchitecture\TweakBackups"
$Script:ServicesBackupRegPath        = "HKCU:\Software\HTCoreArchitecture\ServiceBackups"
$Script:ServicesDisabledThisSession  = New-Object System.Collections.ArrayList
$Script:SessionLogEntries            = New-Object System.Collections.ArrayList
$Script:EdgeBackupFolder             = "$env:USERPROFILE\Desktop\HTCore_EdgeBackup"
$Script:OneDriveBackupFolder         = "$env:USERPROFILE\Desktop\HTCore_OneDriveBackup"

$Script:BoxTL = [string][char]0x2554
$Script:BoxTR = [string][char]0x2557
$Script:BoxBL = [string][char]0x255A
$Script:BoxBR = [string][char]0x255D
$Script:BoxH  = [string][char]0x2550
$Script:BoxV  = [string][char]0x2551
$Script:LineH = [string][char]0x2500
$Script:Check = [string][char]0x2713
$Script:Cross = [string][char]0x2717

trap {
    Write-Host ""
    Write-Host "   $Script:Cross  UNEXPECTED ERROR: $($_.Exception.Message)" -ForegroundColor Red
    try {
        Add-Content -Path $Script:LogPath -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] UNCAUGHT: $($_.Exception.Message)" -ErrorAction SilentlyContinue
    } catch {}
    Start-Sleep -Seconds 2
    continue
}

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
    # Computes the box interior width needed to fit every supplied line
    # in full. Never shrinks below $MinWidth, and never truncates.
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
    # Auto-sizes to the full content - descriptions are never cut off.
    param([string]$Label, [string]$Text, [int]$Width = $Global:PanelWidth)
    $Content  = " ${Label}: $Text"
    $BoxWidth = Get-AutoBoxWidth -Lines @($Content) -MinWidth $Width
    Write-Host ("   " + $Script:BoxTL + ($Script:BoxH * $BoxWidth) + $Script:BoxTR) -ForegroundColor DarkGray
    Write-Host ("   " + $Script:BoxV + $Content.PadRight($BoxWidth) + $Script:BoxV) -ForegroundColor DarkGray
    Write-Host ("   " + $Script:BoxBL + ($Script:BoxH * $BoxWidth) + $Script:BoxBR) -ForegroundColor DarkGray
}

function Write-ModulePreview {
    # Auto-sizes to the longest line across the title and every item -
    # descriptions are always shown in full, never cut off with "...".
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

function Ask-User {
    param($Title, $Explanation)
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
    # Hardened numeric-list input: builds the full set of valid numbers
    # (1..Max) plus a cancel key and routes through Read-Choice so every
    # numbered-list prompt in the script gets the same strict validation
    # as the lettered menus, instead of a bare Read-Host + manual parse.
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
    # Cross-edition safety gate: skips gracefully instead of throwing on
    # editions (Home/Pro/Enterprise/Education/Server) where a feature is
    # known not to apply. Most tweaks in this tool are plain registry
    # values that work identically on every client edition, so this is
    # only used where a genuine edition restriction exists.
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
    # Generic error-recovery wrapper: runs $Action, and on failure logs
    # exactly what happened and offers to retry the SAME operation
    # without ever exiting the surrounding menu loop.
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
            if (-not (Ask-User "Retry '$OperationName'?" "The operation failed and was logged. You can retry it now, or skip it and keep using the menu normally.")) {
                return $false
            }
        }
    } while ($true)
}

# ============================================================
#  TIME-BASED GREETING & EPIC INTRO
# ============================================================
function Show-TimeGreeting {
    $Hour = (Get-Date).Hour
    if ($Hour -lt 12) { return "Good Morning, Humam" }
    elseif ($Hour -lt 18) { return "Good Afternoon, Humam" }
    else { return "Good Evening, Humam" }
}

function Show-EpicIntro {
    Clear-Host
    $greeting = Show-TimeGreeting
    Write-Host ""
    Write-Host "       ██╗  ██╗   ████████╗     ██████╗ ██████╗ ██████╗ ███████╗" -ForegroundColor Cyan
    Write-Host "       ██║  ██║   ╚══██╔══╝    ██╔════╝██╔═══██╗██╔══██╗██╔════╝" -ForegroundColor Cyan
    Write-Host "       ███████║█████╗██║       ██║     ██║   ██║██████╔╝█████╗  " -ForegroundColor Cyan
    Write-Host "       ██╔══██║╚════╝██║       ██║     ██║   ██║██╔══██╗██╔══╝  " -ForegroundColor Cyan
    Write-Host "       ██║  ██║      ██║       ╚██████╗╚██████╔╝██║  ██║███████╗" -ForegroundColor Cyan
    Write-Host "       ╚═╝  ╚═╝      ╚═╝        ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "        $greeting" -ForegroundColor Yellow
    Write-Host "        Humam Taibeh's Ultimate Deployment & Optimization Suite" -ForegroundColor DarkGray
    Write-Host "        v$Script:ScriptVersion  |  $Script:OSCaption (Build $Script:OSBuild)" -ForegroundColor DarkGray
    Write-Host ""
    Start-Sleep -Seconds 2
}

# ============================================================
#  SYSTEM RESTORE
# ============================================================
function New-SystemRestorePoint {
    # MANDATORY SAFETY NET: called at the top of every tweak/service/
    # registry-modifying function in this script. No-ops instantly if a
    # restore point has already been created this session so tweaks stay
    # fast and Windows' restore-point throttling is never hit twice.
    if ($Script:RestorePointCreated) { return }
    Write-Info "Preparing System Restore checkpoint..."
    try {
        $SystemDrive = $env:SystemDrive
        Enable-ComputerRestore -Drive $SystemDrive -ErrorAction SilentlyContinue

        $ThrottlePath = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"
        if (-not (Test-Path $ThrottlePath)) { New-Item -Path $ThrottlePath -Force | Out-Null }
        Set-ItemProperty -Path $ThrottlePath -Name "SystemRestorePointCreationFrequency" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue

        Checkpoint-Computer -Description "Pre-Humam Setup Blueprint" -RestorePointType "MODIFY_SETTINGS" -ErrorAction Stop
        $Script:RestorePointCreated = $true

        try {
            $RP = Get-ComputerRestorePoint -ErrorAction Stop |
                  Where-Object { $_.Description -eq "Pre-Humam Setup Blueprint" } |
                  Sort-Object SequenceNumber -Descending | Select-Object -First 1
            if ($RP) { $Script:ScriptRestorePointSeq = $RP.SequenceNumber }
        } catch {}

        Write-Success "System Restore Point 'Pre-Humam Setup Blueprint' created successfully."
    } catch {
        Write-Warn "Restore Point creation skipped (System Restore may be disabled, throttled, or unsupported on this edition). Tweaks will still proceed, but consider enabling System Restore first: Control Panel > System > System Protection."
    }
}

# ============================================================
#  v3.3 -- TWEAK BACKUP / RESTORE FRAMEWORK
#  Every reversible tweak calls Backup-OriginalRegValue BEFORE it
#  changes anything. The very first captured value per key is kept
#  forever (never overwritten), so "Reset All Tweaks" can always
#  put things back exactly the way they were before this tool ever
#  touched the machine, not just to Microsoft's generic defaults.
# ============================================================
function Backup-OriginalRegValue {
    param(
        [Parameter(Mandatory = $true)][string]$TweakKey,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    try {
        if (-not (Test-Path $Script:TweaksBackupRegPath)) {
            New-Item -Path $Script:TweaksBackupRegPath -Force | Out-Null
        }
        $BackupName = ("$TweakKey--$Name") -replace '[\\:\s]', '_'
        $Existing = Get-RegValue -Path $Script:TweaksBackupRegPath -Name $BackupName
        if ($null -ne $Existing) { return }  # original already captured - never clobber it

        $CurrentVal = Get-RegValue -Path $Path -Name $Name
        $Serialized = if ($null -eq $CurrentVal) { "__NOTSET__" } else { "$CurrentVal" }
        Set-ItemProperty -Path $Script:TweaksBackupRegPath -Name $BackupName -Value $Serialized -Type String -Force
    } catch {
        # Never let a backup failure block the tweak itself.
        Write-Log "BACKUP-WARN: could not snapshot $Path\$Name for '$TweakKey': $($_.Exception.Message)"
    }
}

function Restore-OriginalRegValue {
    # Restores the value captured by Backup-OriginalRegValue, or falls
    # back to $DefaultIfMissing (a safe Windows default) if this tool
    # never captured an original for that key.
    param(
        [Parameter(Mandatory = $true)][string]$TweakKey,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name,
        [string]$DefaultIfMissing = $null,
        [string]$Type = "DWord"
    )
    try {
        $BackupName = ("$TweakKey--$Name") -replace '[\\:\s]', '_'
        $Stored = Get-RegValue -Path $Script:TweaksBackupRegPath -Name $BackupName

        if ($Stored -eq "__NOTSET__") {
            if (Test-Path $Path) { Remove-ItemProperty -Path $Path -Name $Name -ErrorAction SilentlyContinue }
            return $true
        }

        $Value = if ($null -ne $Stored) { $Stored } else { $DefaultIfMissing }
        if ($null -eq $Value) { return $false }

        if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
        Set-ItemProperty -Path $Path -Name $Name -Value $Value -Type $Type -Force
        return $true
    } catch {
        Write-ErrorX "Could not restore $Path\$Name : $($_.Exception.Message)"
        return $false
    }
}

function Reset-AllTweaksToDefaults {
    Write-Banner "RESET ALL TWEAKS TO WINDOWS DEFAULTS"
    Write-ModulePreview -Items @(
        "Restores Dark Mode, Mouse Acceleration, Taskbar alignment, Game Mode,",
        "Classic Context Menu, Telemetry, Advertising ID, and Activity History.",
        "Uses YOUR original captured values when available, otherwise safe",
        "Windows factory defaults. Does NOT reset the entire OS."
    )
    if (-not (Ask-User "Reset ALL Tweaks" "Reverts every tweak this tool can apply back to your original settings (or Windows defaults if no original was captured). A restart or sign-out may be required afterward.")) {
        return
    }

    Invoke-WithRetry -OperationName "Reset Dark Mode" -Action {
        Restore-OriginalRegValue -TweakKey "DarkMode" -Path "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize" -Name "AppsUseLightTheme" -DefaultIfMissing "1" | Out-Null
        Restore-OriginalRegValue -TweakKey "DarkMode" -Path "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize" -Name "SystemUsesLightTheme" -DefaultIfMissing "1" | Out-Null
        Write-Success "Dark Mode reverted."
    } | Out-Null

    Invoke-WithRetry -OperationName "Reset Mouse Acceleration" -Action {
        Restore-OriginalRegValue -TweakKey "MouseAccel" -Path "HKCU:\Control Panel\Mouse" -Name "MouseSpeed" -DefaultIfMissing "1" -Type String | Out-Null
        Restore-OriginalRegValue -TweakKey "MouseAccel" -Path "HKCU:\Control Panel\Mouse" -Name "MouseThreshold1" -DefaultIfMissing "6" -Type String | Out-Null
        Restore-OriginalRegValue -TweakKey "MouseAccel" -Path "HKCU:\Control Panel\Mouse" -Name "MouseThreshold2" -DefaultIfMissing "10" -Type String | Out-Null
        Write-Success "Mouse acceleration reverted."
    } | Out-Null

    if ($Script:IsWin11) {
        Invoke-WithRetry -OperationName "Reset Taskbar" -Action {
            Restore-OriginalRegValue -TweakKey "Taskbar" -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "TaskbarAl" -DefaultIfMissing "1" | Out-Null
            Restore-OriginalRegValue -TweakKey "Taskbar" -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "TaskbarDa" -DefaultIfMissing "1" | Out-Null
            Restore-OriginalRegValue -TweakKey "Taskbar" -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "TaskbarMn" -DefaultIfMissing "1" | Out-Null
            Write-Success "Taskbar layout reverted."
        } | Out-Null
    }

    Invoke-WithRetry -OperationName "Reset Game Mode" -Action {
        Restore-OriginalRegValue -TweakKey "GameMode" -Path "HKCU:\Software\Microsoft\GameBar" -Name "AllowAutoGameMode" -DefaultIfMissing "0" | Out-Null
        Restore-OriginalRegValue -TweakKey "GameMode" -Path "HKCU:\Software\Microsoft\GameBar" -Name "AutoGameModeEnabled" -DefaultIfMissing "0" | Out-Null
        Restore-OriginalRegValue -TweakKey "GameMode" -Path "HKCU:\System\GameConfigStore" -Name "GameDVR_Enabled" -DefaultIfMissing "1" | Out-Null
        Write-Success "Game Mode / Game Bar settings reverted."
    } | Out-Null

    Invoke-WithRetry -OperationName "Reset Classic Context Menu" -Action {
        $ClassicPath = "HKCU:\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"
        if (Test-Path $ClassicPath) { Remove-Item -Path $ClassicPath -Recurse -Force -ErrorAction Stop }
        Write-Success "Windows 11 context menu reverted to modern default."
    } | Out-Null

    Invoke-WithRetry -OperationName "Reset Telemetry" -Action {
        Restore-OriginalRegValue -TweakKey "Telemetry" -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection" -Name "AllowTelemetry" -DefaultIfMissing "3" | Out-Null
        Write-Success "Telemetry policy value reverted."
    } | Out-Null

    Invoke-WithRetry -OperationName "Reset Advertising ID" -Action {
        Restore-OriginalRegValue -TweakKey "AdvertisingID" -Path "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo" -Name "Enabled" -DefaultIfMissing "1" | Out-Null
        Write-Success "Advertising ID reverted."
    } | Out-Null

    Invoke-WithRetry -OperationName "Reset Activity History" -Action {
        Restore-OriginalRegValue -TweakKey "ActivityHistory" -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System" -Name "EnableActivityFeed" -DefaultIfMissing "1" | Out-Null
        Restore-OriginalRegValue -TweakKey "ActivityHistory" -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System" -Name "PublishUserActivities" -DefaultIfMissing "1" | Out-Null
        Restore-OriginalRegValue -TweakKey "ActivityHistory" -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System" -Name "UploadUserActivities" -DefaultIfMissing "1" | Out-Null
        Write-Success "Activity History sync reverted."
    } | Out-Null

    Write-Success "Reset-All-Tweaks pass complete."
    Write-Warn "A restart or sign-out is recommended so every reverted setting takes full effect."
    $Script:PendingRestart = $true
}

# ============================================================
#  v3.3 -- SERVICES SNAPSHOT & RESTORE
# ============================================================
function Backup-ServiceState {
    # Captures a service's ORIGINAL startup type + status the first time
    # this tool ever touches it. Never overwrites a prior capture.
    param([Parameter(Mandatory = $true)][string]$Name)
    try {
        if (-not (Test-Path $Script:ServicesBackupRegPath)) {
            New-Item -Path $Script:ServicesBackupRegPath -Force | Out-Null
        }
        if (Get-RegValue -Path $Script:ServicesBackupRegPath -Name $Name) { return }
        $State = Get-ServiceState -Name $Name
        if (-not $State.Exists) { return }
        Set-ItemProperty -Path $Script:ServicesBackupRegPath -Name $Name -Value "$($State.StartType)|$($State.Status)" -Type String -Force
    } catch {
        Write-Log "BACKUP-WARN: could not snapshot service '$Name': $($_.Exception.Message)"
    }
    if (-not ($Script:ServicesDisabledThisSession -contains $Name)) {
        [void]$Script:ServicesDisabledThisSession.Add($Name)
    }
}

function Restore-AllServicesToPreviousState {
    Write-Banner "RESTORE ALL SERVICES TO PREVIOUS STATE"
    $Names = @()
    if (Test-Path $Script:ServicesBackupRegPath) {
        $Props = Get-ItemProperty -Path $Script:ServicesBackupRegPath -ErrorAction SilentlyContinue
        if ($Props) {
            foreach ($Prop in $Props.PSObject.Properties) {
                if ($Prop.Name -match '^PS(Path|ParentPath|ChildName|Provider)$') { continue }
                $Names += $Prop.Name
            }
        }
    }
    if ($Names.Count -eq 0) {
        Write-AlreadyOK "No service changes have been recorded by this tool - nothing to restore."
        Read-Host "   Press Enter to continue"
        return
    }
    if (-not (Ask-User "Restore $($Names.Count) Service(s)" "Re-enables and, where applicable, restarts every service this tool disabled during any past session, using their originally captured startup type.")) {
        return
    }
    foreach ($Name in $Names) {
        Invoke-WithRetry -OperationName "Restore service '$Name'" -Action {
            $Raw = Get-RegValue -Path $Script:ServicesBackupRegPath -Name $Name
            if (-not $Raw) { throw "No backup data found." }
            $OrigStartType = ($Raw -split '\|')[0]
            if (-not (Get-Service -Name $Name -ErrorAction SilentlyContinue)) {
                Write-Warn "Service '$Name' is no longer present on this system - skipping."
                return
            }
            Set-Service -Name $Name -StartupType $OrigStartType -ErrorAction Stop
            if ($OrigStartType -notin @("Disabled")) {
                Start-Service -Name $Name -ErrorAction SilentlyContinue
            }
            Write-Success "Service '$Name' restored to original startup type '$OrigStartType'."
        } | Out-Null
    }
    Write-Info "Service restoration pass complete."
    Read-Host "   Press Enter to continue"
}

# ============================================================
#  v3.3 -- MICROSOFT EDGE BACKUP / RESTORE
# ============================================================
function Backup-EdgeState {
    Write-Info "Backing up current Edge version and settings before removal..."
    try {
        New-Item -Path $Script:EdgeBackupFolder -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
        $EdgeExe = Get-ChildItem -Path "$env:ProgramFiles\Microsoft\Edge\Application\*\msedge.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        $Version = if ($EdgeExe) { (Get-Item $EdgeExe.FullName).VersionInfo.ProductVersion } else { "Unknown" }
        $UserDataDir = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"

        [PSCustomObject]@{
            BackedUpAt  = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
            EdgeVersion = $Version
            UserDataDir = $UserDataDir
        } | ConvertTo-Json | Set-Content -Path (Join-Path $Script:EdgeBackupFolder "EdgeManifest.json") -Force

        if (Test-Path $UserDataDir) {
            $SettingsBackup = Join-Path $Script:EdgeBackupFolder "UserData_Settings"
            New-Item -Path $SettingsBackup -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
            $LocalState = Join-Path $UserDataDir "Local State"
            if (Test-Path $LocalState) { Copy-Item -Path $LocalState -Destination $SettingsBackup -Force -ErrorAction SilentlyContinue }
            Get-ChildItem -Path $UserDataDir -Directory -Filter "Default*" -ErrorAction SilentlyContinue | ForEach-Object {
                $Dest = Join-Path $SettingsBackup $_.Name
                New-Item -Path $Dest -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
                foreach ($FileName in @("Preferences", "Bookmarks", "Favicons")) {
                    $Src = Join-Path $_.FullName $FileName
                    if (Test-Path $Src) { Copy-Item -Path $Src -Destination $Dest -Force -ErrorAction SilentlyContinue }
                }
            }
        }
        Write-Success "Edge version ($Version) and settings backed up to Desktop\HTCore_EdgeBackup."
    } catch {
        Write-Warn "Edge backup encountered an issue (continuing with removal anyway): $($_.Exception.Message)"
    }
}

function Restore-EdgeState {
    $ManifestPath = Join-Path $Script:EdgeBackupFolder "EdgeManifest.json"
    if (-not (Test-Path $ManifestPath)) {
        Write-Info "No previous Edge backup found - a clean install of the latest stable Edge was performed."
        return
    }
    try {
        $Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
        Write-Info "Found a backup from $($Manifest.BackedUpAt) (was Edge $($Manifest.EdgeVersion))."
        $SettingsBackup = Join-Path $Script:EdgeBackupFolder "UserData_Settings"
        if ((Test-Path $SettingsBackup) -and (Ask-User "Restore Edge Settings" "Copies your backed-up Preferences, Bookmarks, and Favicons back into the freshly installed Edge profile.")) {
            $UserDataDir = "$env:LOCALAPPDATA\Microsoft\Edge\User Data"
            New-Item -Path $UserDataDir -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
            $LocalState = Join-Path $SettingsBackup "Local State"
            if (Test-Path $LocalState) { Copy-Item -Path $LocalState -Destination $UserDataDir -Force -ErrorAction SilentlyContinue }
            Get-ChildItem -Path $SettingsBackup -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                $Dest = Join-Path $UserDataDir $_.Name
                New-Item -Path $Dest -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
                Copy-Item -Path (Join-Path $_.FullName "*") -Destination $Dest -Force -ErrorAction SilentlyContinue
            }
            Write-Success "Edge settings restored from backup."
        }
    } catch {
        Write-Warn "Could not restore Edge settings automatically: $($_.Exception.Message)"
    }
}

# ============================================================
#  v3.3 -- ONEDRIVE FILE BACKUP
# ============================================================
function Backup-OneDriveFiles {
    $OneDrivePath = "$env:USERPROFILE\OneDrive"
    if (-not (Test-Path $OneDrivePath)) {
        Write-Info "No local OneDrive folder found - nothing to back up."
        return
    }
    $SizeGB = "Unknown"
    try {
        $SizeGB = [math]::Round(((Get-ChildItem -Path $OneDrivePath -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum) / 1GB, 2)
    } catch {}

    if (-not (Ask-User "Back Up Local OneDrive Files First" "Copies your local OneDrive folder (approx. $SizeGB GB) to Desktop\HTCore_OneDriveBackup before removing OneDrive. Recommended, but can take a while for large folders.")) {
        Write-Warn "Skipping backup at your request - proceeding to remove OneDrive without one."
        return
    }
    try {
        New-Item -Path $Script:OneDriveBackupFolder -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
        Write-Info "Copying files - this may take a while depending on folder size..."
        robocopy $OneDrivePath $Script:OneDriveBackupFolder /E /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
        Write-Success "OneDrive files backed up to Desktop\HTCore_OneDriveBackup."
    } catch {
        Write-ErrorX "OneDrive backup failed: $($_.Exception.Message)"
    }
}

# ============================================================
#  v3.3 -- ROLLBACK TO SCRIPT'S OWN RESTORE POINT
# ============================================================
function Invoke-ScriptRollback {
    Write-Banner "ROLLBACK TO THIS SESSION'S RESTORE POINT"
    if (-not $Script:RestorePointCreated -or -not $Script:ScriptRestorePointSeq) {
        Write-Warn "No restore point has been created by this tool yet."
        Write-Info "A restore point is created automatically the first time you run any tweak, service change, or system optimization."
        Read-Host "   Press Enter to continue"
        return
    }
    Write-Warn "This restores your ENTIRE system to the 'Pre-Humam Setup Blueprint' checkpoint. This affects the whole system, not only this tool's changes, and requires a restart."
    if (-not (Ask-User "Rollback Now" "Restores Windows to the state it was in before this tool made any changes this session, then restarts the PC automatically.")) {
        return
    }
    Invoke-WithRetry -OperationName "System Restore Rollback" -Action {
        Restore-Computer -RestorePoint $Script:ScriptRestorePointSeq -Confirm:$false -ErrorAction Stop
    } | Out-Null
}

# ============================================================
#  v3.3 -- SESSION LOG VIEWER
# ============================================================
function Show-SessionLogViewer {
    do {
        Write-Banner "SESSION LOG"
        if ($Script:SessionLogEntries.Count -eq 0) {
            Write-Info "No log entries recorded yet this session."
        } else {
            $Recent = $Script:SessionLogEntries | Select-Object -Last 25
            foreach ($Entry in $Recent) {
                $Color = switch -Regex ($Entry) {
                    'ERROR|FAILED'  { 'Red' }
                    'WARN'          { 'Yellow' }
                    'SUCCESS'       { 'Green' }
                    default         { 'Gray' }
                }
                Write-Host "   $Entry" -ForegroundColor $Color
            }
            Write-Host ""
            Write-Info "Showing the most recent $($Recent.Count) of $($Script:SessionLogEntries.Count) entries this session. Full history on disk: $Script:LogPath"
        }
        Write-Divider
        Write-Host "   [O]  Open full log file in Notepad" -ForegroundColor White
        Write-Host "   [X]  Back" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select an action" -Valid @('o', 'x')
        if ($Choice -eq 'o') {
            if (Test-Path $Script:LogPath) { Start-Process notepad.exe $Script:LogPath } else { Write-Warn "No log file found yet." }
            Read-Host "   Press Enter to continue"
        }
        if ($Choice -eq 'x') { return }
    } while ($true)
}

# ============================================================
#  v3.3 -- SAFETY & RECOVERY MENU (new main-menu module)
# ============================================================
function Show-SafetyRecoveryMenu {
    do {
        Write-Banner "SAFETY & RECOVERY"
        Write-ModulePreview -Items @(
            "Rollback to this session's System Restore point (whole-system undo)",
            "Reset ALL Tweaks to Windows Defaults (uses your original captured values)",
            "Restore All Services this tool has ever disabled",
            "Session Log: view successes, warnings, and failures with timestamps"
        )
        Write-Host "   [1]  Rollback to Session Restore Point" -ForegroundColor White
        Write-Host "   [2]  Reset ALL Tweaks to Windows Defaults" -ForegroundColor White
        Write-Host "   [3]  Restore All Services to Previous State" -ForegroundColor White
        Write-Host "   [4]  View Session Log" -ForegroundColor White
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (1-4, X)" -Valid @('1', '2', '3', '4', 'x')
        switch ($Choice) {
            '1' { Invoke-ScriptRollback }
            '2' { Reset-AllTweaksToDefaults; Read-Host "   Press Enter to continue" }
            '3' { Restore-AllServicesToPreviousState }
            '4' { Show-SessionLogViewer }
            'x' { return }
        }
    } while ($true)
}

# ============================================================
#  APP DOWNLOAD FALLBACK URLS
# ============================================================
$Script:DownloadUrls = @{
    "Google.Chrome"                 = "https://www.google.com/chrome/"
    "Spotify.Spotify"               = "https://www.spotify.com/download"
    "Discord.Discord"               = "https://discord.com/download"
    "Apple.iTunes"                  = "https://www.apple.com/itunes/download/"
    "Valve.Steam"                   = "https://store.steampowered.com/about/"
    "EpicGames.EpicGamesLauncher"   = "https://store.epicgames.com/en-US/download"
    "RockstarGames.Launcher"        = "https://socialclub.rockstargames.com/rockstar-games-launcher"
    "BlueStacks.BlueStacks"         = "https://www.bluestacks.com/download.html"
    "CPUID.CPU-Z"                   = "https://www.cpuid.com/softwares/cpu-z.html"
    "TechPowerUp.GPU-Z"             = "https://www.techpowerup.com/gpuz/"
    "CPUID.HWMonitor"               = "https://www.cpuid.com/softwares/hwmonitor.html"
    "CrystalDewWorld.CrystalDiskInfo" = "https://crystalmark.info/en/software/crystaldiskinfo/"
    "Guru3D.Afterburner"            = "https://www.msi.com/Landing/afterburner/graphics-cards"
    "Notion.Notion"                 = "https://www.notion.so/desktop"
    "Anysphere.Cursor"              = "https://cursor.sh/"
    "Microsoft.VisualStudioCode"    = "https://code.visualstudio.com/download"
    "JetBrains.PyCharm.Community"   = "https://www.jetbrains.com/pycharm/download/"
    "Apache.NetBeans"               = "https://netbeans.apache.org/download/index.html"
    "MSYS2.MSYS2"                   = "https://www.msys2.org/"
    "Ollama.Ollama"                 = "https://ollama.com/download"
    "7zip.7zip"                     = "https://www.7-zip.org/download.html"
    "VideoLAN.VLC"                  = "https://www.videolan.org/vlc/"
}

# ============================================================
#  STORE APP DETECTION
# ============================================================
function Is-StoreApp {
    param([string]$AppId)
    return $AppId -match '^\w{12}$'
}

# ============================================================
#  IMPROVED INSTALLED VERSION DETECTION
# ============================================================
function Get-InstalledVersion {
    param([string]$AppId, [string]$AppName)

    if (Is-StoreApp $AppId) {
        try {
            $pkg = Get-AppxPackage -Name $AppId -ErrorAction SilentlyContinue
            if ($pkg) { return $pkg.Version }
        } catch {}
        return $null
    }

    $Lines = & winget list --id $AppId --exact --accept-source-agreements --disable-interactivity 2>$null
    if (-not $Lines) {
        $Lines = & winget list --query $AppName --exact --accept-source-agreements --disable-interactivity 2>$null
    }
    if (-not $Lines) { return $null }

    foreach ($Line in $Lines) {
        $Trimmed = $Line.Trim()
        if ([string]::IsNullOrWhiteSpace($Trimmed)) { continue }
        $Cols = [regex]::Split($Trimmed, '\s{2,}')
        if ($Cols.Count -ge 3) {
            if ($Cols[1] -eq $AppId -or $Cols[1] -eq $AppName) {
                return $Cols[2]
            }
        }
    }
    return $null
}

# ============================================================
#  WINGET ENGINE
# ============================================================
$Script:LockProcessMap = @{
    "Discord.Discord"            = @("Discord", "DiscordCanary", "DiscordPTB")
    "Anysphere.Cursor"           = @("Cursor")
    "Microsoft.VisualStudioCode" = @("Code")
    "Spotify.Spotify"            = @("Spotify")
    "Valve.Steam"                = @("steam", "steamwebhelper")
}

function Stop-LockingProcesses {
    param($AppId)
    if ($Script:LockProcessMap.ContainsKey($AppId)) {
        foreach ($ProcName in $Script:LockProcessMap[$AppId]) {
            $Proc = Get-Process -Name $ProcName -ErrorAction SilentlyContinue
            if ($Proc) {
                Write-Warn "Terminating background process '$ProcName'..."
                Stop-Process -Name $ProcName -Force -ErrorAction SilentlyContinue
                Start-Sleep -Milliseconds 800
            }
        }
    }
}

function Invoke-Winget {
    param([string[]]$ArgList)
    $Proc = Start-Process -FilePath "winget" -ArgumentList $ArgList -NoNewWindow -Wait -PassThru
    return $Proc.ExitCode
}

function Invoke-Chocolatey {
    param([string]$AppId)
    try {
        choco install $AppId -y --limit-output | Out-Null
        return 0
    } catch {
        return 1
    }
}

function Resolve-WingetExitCode {
    param([int]$Code)
    switch ($Code) {
        0            { return @{ Success = $true;  Message = "Completed successfully." } }
        3010         { return @{ Success = $true;  Message = "Completed successfully. A reboot is recommended." } }
        -1978335212  { return @{ Success = $true;  Message = "Already up to date." } }
        -1978335215  { return @{ Success = $true;  Message = "No applicable upgrade was found." } }
        -1978335189  { return @{ Success = $false; Message = "Package not found in configured sources." } }
        -1978335153  { return @{ Success = $false; Message = "Target file is in use by another process." } }
        1602         { return @{ Success = $false; Message = "Installer was cancelled." } }
        1            { return @{ Success = $false; Message = "Generic failure (Exit Code 1)." } }
        default      { return @{ Success = $false; Message = "Unhandled exit code ($Code)." } }
    }
}

function Open-FallbackUrl {
    param($AppId, $AppName)
    $url = $Script:DownloadUrls[$AppId]
    if ($url) {
        Write-Info "Opening official download page: $url"
        Start-Process $url
    } else {
        Write-Info "No official URL mapped. Opening search..."
        Start-Process "https://www.google.com/search?q=$AppName download"
    }
}

function Smart-Deploy {
    param(
        [string]$AppId,
        [string]$AppName,
        [switch]$Bulk,
        [ValidateSet('auto','manual')]
        [string]$BulkMethod
    )

    if ([string]::IsNullOrWhiteSpace($AppId)) { return @{Status='Skipped'; Message='Empty AppId'} }

    if (Is-StoreApp $AppId) {
        Write-Host ""
        Write-StatusPanel -Label "STORE APP" -Text $AppName

        $InstalledVer = Get-InstalledVersion -AppId $AppId -AppName $AppName
        if ($InstalledVer) {
            Write-Success "$AppName is already installed (version $InstalledVer)."
            return @{Status='Success'; Message='Already installed'}
        }

        if ($Bulk) {
            if ($BulkMethod -eq 'manual') {
                Write-Info "Opening Store page for $AppName..."
                Start-Process "ms-windows-store://pdp/?ProductId=$AppId"
                return @{Status='Success'; Message='Store opened'}
            } else {
                Write-Warn "$AppName is a Store app and cannot be installed via winget. Skipping."
                return @{Status='Skipped'; Message='Store app'}
            }
        }

        Write-Host "   m = Open Microsoft Store page" -ForegroundColor Yellow
        Write-Host "   n = Skip this app only" -ForegroundColor Yellow
        Write-Host "   b = Back to category" -ForegroundColor Yellow
        Write-Host "   q = Quit to main menu" -ForegroundColor Yellow
        $choice = Read-Choice -Prompt "   Choose (m/n/b/q)" -Valid @('m','n','b','q')
        switch ($choice) {
            'q' { return @{Status='Quit'; Message='User quit to main menu'} }
            'b' { return @{Status='Back'; Message='User returned to category'} }
            'm' {
                Write-Info "Launching Microsoft Store..."
                Start-Process "ms-windows-store://pdp/?ProductId=$AppId"
                Write-Success "Store page opened."
                return @{Status='Success'; Message='Store opened'}
            }
            default { return @{Status='Skipped'; Message='Skipped'} }
        }
    }

    Write-Host ""
    Write-StatusPanel -Label "TARGET" -Text $AppName

    $CurrentVersion = Get-InstalledVersion -AppId $AppId -AppName $AppName
    $LatestVersion  = Get-LatestVersion -AppId $AppId

    if ($CurrentVersion) {
        if ($CurrentVersion -eq $LatestVersion -or $LatestVersion -eq "Unknown") {
            Write-Success "$AppName is already current (v$CurrentVersion)."
            return @{Status='Success'; Message='Already current'}
        }
        Write-Warn "$AppName update available: $CurrentVersion -> $LatestVersion"
    } else {
        Write-Warn "$AppName is not installed. (Latest: $LatestVersion)"
    }

    if ($Bulk) {
        if ($BulkMethod -eq 'manual') {
            Open-FallbackUrl $AppId $AppName
            return @{Status='Success'; Message='Manual URL (bulk)'}
        }
    } else {
        Write-Host "   y = Auto install via winget (silent)" -ForegroundColor Yellow
        Write-Host "   m = Open official website (manual download)" -ForegroundColor Yellow
        Write-Host "   n = Skip this app only" -ForegroundColor Yellow
        Write-Host "   b = Back to category" -ForegroundColor Yellow
        Write-Host "   q = Quit to main menu" -ForegroundColor Yellow
        $choice = Read-Choice -Prompt "   Choose (y/m/n/b/q)" -Valid @('y','m','n','b','q')
        switch ($choice) {
            'q' { return @{Status='Quit'; Message='User quit to main menu'} }
            'b' { return @{Status='Back'; Message='User returned to category'} }
            'n' { Write-Info "Bypassed $AppName."; return @{Status='Skipped'; Message='User skipped'} }
            'm' { Open-FallbackUrl $AppId $AppName; return @{Status='Success'; Message='Manual URL'} }
            'y' { }
        }
    }

    if (-not $global:WingetAvailable) {
        if ($global:ChocolateyAvailable) {
            Write-Info "Installing via Chocolatey..."
            $code = Invoke-Chocolatey $AppId
            if ($code -eq 0) { Write-Success "$AppName installed via Chocolatey."; return @{Status='Success'; Message='Chocolatey'} }
            else { Write-ErrorX "Chocolatey failed."; return @{Status='Failed'; Message='Chocolatey failed'} }
        } else {
            Write-ErrorX "No package manager available."
            Open-FallbackUrl $AppId $AppName
            return @{Status='Failed'; Message='No package manager'}
        }
    }

    Stop-LockingProcesses -AppId $AppId
    Write-Info "Running winget - live progress:"
    if ($CurrentVersion) {
        $Code = Invoke-Winget -ArgList @("upgrade", "--id", $AppId, "--exact", "--include-unknown", "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity")
    } else {
        $Code = Invoke-Winget -ArgList @("install", "--id", $AppId, "--exact", "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity")
    }

    if ($Code -ne 0 -and $Code -ne -1978335212) {
        Write-Warn "First attempt failed. Retrying with force flags..."
        Start-Sleep -Seconds 3
        if ($CurrentVersion) {
            $Code = Invoke-Winget -ArgList @("upgrade", "--id", $AppId, "--exact", "--include-unknown", "--accept-source-agreements", "--accept-package-agreements", "--force", "--disable-interactivity")
        } else {
            $Code = Invoke-Winget -ArgList @("install", "--id", $AppId, "--exact", "--accept-source-agreements", "--accept-package-agreements", "--force", "--disable-interactivity")
        }
    }

    $Result = Resolve-WingetExitCode -Code $Code

    if ($Result.Success) {
        Write-Success "$AppName -> $($Result.Message)"
        if ($Script:DevAppPaths.ContainsKey($AppId)) { Register-DevPath -AppId $AppId -AppName $AppName }
        # Only suggest a missing dependency on a genuine fresh install/upgrade,
        # never when the app was already installed/current.
        if ($Result.Message -notmatch '^Already') {
            Test-DevDependencySuggestion -AppId $AppId
        }
        return @{Status='Success'; Message=$Result.Message}
    } else {
        Write-ErrorX "$AppName failed: $($Result.Message)"
        if (-not $Bulk) {
            $openFallback = Read-Choice -Prompt "   Auto install failed. Open official website? (y/n)" -Valid @('y','n')
            if ($openFallback -eq 'y') { Open-FallbackUrl $AppId $AppName }
        } else {
            Open-FallbackUrl $AppId $AppName
        }
        return @{Status='Failed'; Message=$Result.Message}
    }
}

function Get-LatestVersion {
    param([string]$AppId)
    if (Is-StoreApp $AppId) { return "Store" }
    $Lines = & winget show --id $AppId --exact --accept-source-agreements --disable-interactivity 2>$null
    if (-not $Lines) { return "Unknown" }
    foreach ($Line in $Lines) {
        if ($Line -match '^\s*Version:\s*(\S+)') { return $Matches[1] }
    }
    return "Unknown"
}

function Hardware-Check {
    $GPU = Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name | Select-Object -First 1
    $GPUApp = if ($GPU -match "NVIDIA") { "Nvidia.GeForceExperience" }
              elseif ($GPU -match "AMD|Radeon") { "AdvancedMicroDevices.Adrenalin" }
              elseif ($GPU -match "Intel") { "Intel.IntelGraphicsCommandCenter" } else { "" }

    $Mobo = Get-CimInstance Win32_BaseBoard | Select-Object -ExpandProperty Manufacturer
    $MoboApp = if ($Mobo -match "ASUS") { "Asus.ArmouryCrate" }
               elseif ($Mobo -match "Micro-Star|MSI") { "Micro-Star.MSICenter" }
               elseif ($Mobo -match "Gigabyte") { "Gigabyte.ControlCenter" }
               elseif ($Mobo -match "ASRock") { "ASRock.AppShop" } else { "" }

    return @{ GPUApp = $GPUApp; MoboApp = $MoboApp; MoboName = $Mobo; GPUName = $GPU }
}

function Get-DisplayRefreshRate {
    try {
        $Rates = Get-CimInstance Win32_VideoController -ErrorAction Stop |
                 Where-Object { $_.CurrentRefreshRate -gt 0 } |
                 Select-Object -ExpandProperty CurrentRefreshRate
        return $Rates
    } catch {
        return $null
    }
}

# ============================================================
#  DEVELOPER AUTO-PATHING
# ============================================================
$Script:DevAppPaths = @{
    "JetBrains.PyCharm.Community" = @{ Name = "PyCharm";  ExeName = "pycharm64.exe" }
    "Anysphere.Cursor"            = @{ Name = "Cursor";   ExeName = "Cursor.exe" }
    "Apache.NetBeans"             = @{ Name = "NetBeans";  ExeName = "netbeans64.exe" }
    "MSYS2.MSYS2"                 = @{ Name = "MSYS2";    ExeName = "bash.exe" }
}

# ============================================================
#  DEV DEPENDENCY SUGGESTIONS (post-install helper)
# ============================================================
# Maps an IDE's winget AppId to the interpreter/toolchain command it
# needs on PATH to actually run/compile code, plus how to fetch it.
$Script:DevDependencyMap = @{
    "JetBrains.PyCharm.Community" = @{
        CommandName  = "python"
        FriendlyName = "Python"
        WingetId     = "Python.Python.3.12"
        Url          = "https://www.python.org/downloads/"
    }
    "Apache.NetBeans" = @{
        CommandName  = "javac"
        FriendlyName = "JDK (Eclipse Temurin 21)"
        WingetId     = "EclipseAdoptium.Temurin.21.JDK"
        Url          = "https://adoptium.net/temurin/releases/"
    }
}

function Test-DevDependencySuggestion {
    # Called only after a CONFIRMED, FRESH successful install (never on
    # skip/fail/manual-URL paths). Suggests the missing runtime/toolchain
    # so the IDE the user just installed is immediately usable.
    param([string]$AppId)
    if (-not $Script:DevDependencyMap.ContainsKey($AppId)) { return }
    $Dep = $Script:DevDependencyMap[$AppId]

    try {
        if (Get-Command $Dep.CommandName -ErrorAction SilentlyContinue) { return }
    } catch {
        return
    }

    Write-Host ""
    Write-Warn "$($Dep.FriendlyName) was not found on PATH. It's typically required to run or compile projects with the IDE you just installed."
    if (Ask-User "Install $($Dep.FriendlyName)" "Installs $($Dep.FriendlyName) so your new IDE can build and run code right away.") {
        if ($global:WingetAvailable) {
            $DepResult = Smart-Deploy -AppId $Dep.WingetId -AppName $Dep.FriendlyName -Bulk -BulkMethod 'auto'
            if ($DepResult.Status -ne 'Success') {
                Write-Info "Automatic install of $($Dep.FriendlyName) did not complete. Opening the official manual download page..."
                try { Start-Process $Dep.Url } catch { Write-Warn "Could not open browser automatically. Visit: $($Dep.Url)" }
            }
        } else {
            Write-Info "Winget is unavailable. Opening the official manual download page for $($Dep.FriendlyName)..."
            try { Start-Process $Dep.Url } catch { Write-Warn "Could not open browser automatically. Visit: $($Dep.Url)" }
        }
    } else {
        Write-Info "You can install $($Dep.FriendlyName) later from: $($Dep.Url)"
    }
}

function Add-ToUserPath {
    param([string]$Directory)
    if (-not (Test-Path $Directory)) { return $false }
    $Current = [Environment]::GetEnvironmentVariable("Path", "User")
    $Entries = @($Current -split ";" | Where-Object { $_ -ne "" })
    if ($Entries -contains $Directory) { return $true }
    $NewPath = (@($Entries) + $Directory) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = "$env:Path;$Directory"
    return $true
}

function Register-DevPath {
    param($AppId, $AppName)
    $Config = $Script:DevAppPaths[$AppId]
    if (-not $Config) { return }

    Write-Info "Resolving install path for $AppName ..."
    $SearchRoots = @(
        "$env:ProgramFiles", "${env:ProgramFiles(x86)}",
        "$env:LOCALAPPDATA\Programs", "C:\msys64"
    ) | Where-Object { $_ -and (Test-Path $_) }

    $Found = $null
    foreach ($Root in $SearchRoots) {
        $Hit = Get-ChildItem -Path $Root -Filter $Config.ExeName -Recurse -Depth 4 -ErrorAction SilentlyContinue -File | Select-Object -First 1
        if ($Hit) { $Found = $Hit.DirectoryName; break }
    }

    if ($Found) {
        if (Add-ToUserPath -Directory $Found) { Write-Success "$AppName added to PATH -> $Found" }
        if ($AppId -eq "MSYS2.MSYS2") { Add-ToUserPath -Directory "C:\msys64\mingw64\bin" | Out-Null }
    } else {
        Write-Warn "$AppName installed, but its executable could not be auto-resolved for PATH registration."
    }
}

# ============================================================
#  STARTUP PROGRAM MANAGER
# ============================================================
$Script:StartupDisabledRegPath = "HKCU:\Software\HTCoreArchitecture\DisabledStartup"
$Script:StartupBackupFolder    = "$env:USERPROFILE\Desktop\HTCore_StartupBackup"

function Get-StartupRunKeyItems {
    $Keys = @(
        @{ Hive = "HKCU"; Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" },
        @{ Hive = "HKLM"; Path = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run" }
    )
    $Items = @()
    foreach ($Key in $Keys) {
        if (-not (Test-Path $Key.Path)) { continue }
        $Props = Get-ItemProperty -Path $Key.Path -ErrorAction SilentlyContinue
        if (-not $Props) { continue }
        foreach ($Prop in $Props.PSObject.Properties) {
            if ($Prop.Name -match '^PS(Path|ParentPath|ChildName|Provider)$') { continue }
            $Items += [PSCustomObject]@{
                Type    = "Registry"
                Hive    = $Key.Hive
                RegPath = $Key.Path
                Name    = $Prop.Name
                Command = $Prop.Value
                Enabled = $true
            }
        }
    }
    return $Items
}

function Get-StartupFolderItems {
    $Folders = @(
        "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup",
        "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Startup"
    )
    $Items = @()
    foreach ($Folder in $Folders) {
        if (-not (Test-Path $Folder)) { continue }
        Get-ChildItem -Path $Folder -File -ErrorAction SilentlyContinue | ForEach-Object {
            $Items += [PSCustomObject]@{
                Type    = "Folder"
                Hive    = ""
                RegPath = $Folder
                Name    = $_.Name
                Command = $_.FullName
                Enabled = $true
            }
        }
    }
    return $Items
}

function Get-DisabledStartupItems {
    $Items = @()
    if (Test-Path $Script:StartupDisabledRegPath) {
        $Props = Get-ItemProperty -Path $Script:StartupDisabledRegPath -ErrorAction SilentlyContinue
        if ($Props) {
            foreach ($Prop in $Props.PSObject.Properties) {
                if ($Prop.Name -match '^PS(Path|ParentPath|ChildName|Provider)$') { continue }
                $Items += [PSCustomObject]@{
                    Type    = "Registry"
                    Hive    = "HKCU"
                    RegPath = $Script:StartupDisabledRegPath
                    Name    = $Prop.Name
                    Command = $Prop.Value
                    Enabled = $false
                }
            }
        }
    }
    if (Test-Path $Script:StartupBackupFolder) {
        Get-ChildItem -Path $Script:StartupBackupFolder -File -ErrorAction SilentlyContinue | ForEach-Object {
            $Items += [PSCustomObject]@{
                Type    = "Folder"
                Hive    = ""
                RegPath = $Script:StartupBackupFolder
                Name    = $_.Name
                Command = $_.FullName
                Enabled = $false
            }
        }
    }
    return $Items
}

function Get-AllStartupItems {
    return @(Get-StartupRunKeyItems) + @(Get-StartupFolderItems) + @(Get-DisabledStartupItems)
}

function Show-StartupItemsList {
    param([array]$Items)
    if ($Items.Count -eq 0) {
        Write-Info "No startup items found."
        return
    }
    for ($i = 0; $i -lt $Items.Count; $i++) {
        $it = $Items[$i]
        $StatusTag = if ($it.Enabled) { "ENABLED " } else { "DISABLED" }
        $Color = if ($it.Enabled) { "Green" } else { "DarkGray" }
        Write-Host ("   [{0,2}] [{1}] {2}  ({3})" -f ($i + 1), $StatusTag, $it.Name, $it.Type) -ForegroundColor $Color
    }
}

function Disable-StartupItem {
    param($Item)
    try {
        if ($Item.Type -eq "Registry") {
            if (-not (Test-Path $Script:StartupDisabledRegPath)) {
                New-Item -Path $Script:StartupDisabledRegPath -Force | Out-Null
            }
            Set-ItemProperty -Path $Script:StartupDisabledRegPath -Name $Item.Name -Value $Item.Command -Force
            Remove-ItemProperty -Path $Item.RegPath -Name $Item.Name -ErrorAction Stop
            Write-Success "Disabled startup entry '$($Item.Name)' (backed up for re-enable)."
        } else {
            if (-not (Test-Path $Script:StartupBackupFolder)) {
                New-Item -Path $Script:StartupBackupFolder -ItemType Directory -Force | Out-Null
            }
            Move-Item -Path $Item.Command -Destination $Script:StartupBackupFolder -Force -ErrorAction Stop
            Write-Success "Disabled startup shortcut '$($Item.Name)' (moved to backup folder)."
        }
    } catch {
        Write-ErrorX "Could not disable '$($Item.Name)': $($_.Exception.Message)"
    }
}

function Enable-StartupItem {
    param($Item)
    try {
        if ($Item.Type -eq "Registry") {
            if (-not (Test-Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run")) {
                New-Item -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Force | Out-Null
            }
            Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name $Item.Name -Value $Item.Command -Force
            Remove-ItemProperty -Path $Script:StartupDisabledRegPath -Name $Item.Name -ErrorAction Stop
            Write-Success "Re-enabled startup entry '$($Item.Name)'."
        } else {
            $Dest = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
            Move-Item -Path $Item.Command -Destination $Dest -Force -ErrorAction Stop
            Write-Success "Re-enabled startup shortcut '$($Item.Name)'."
        }
    } catch {
        Write-ErrorX "Could not re-enable '$($Item.Name)': $($_.Exception.Message)"
    }
}

function Show-StartupProgramManager {
    do {
        Write-Banner "STARTUP PROGRAM MANAGER"
        $AllItems      = Get-AllStartupItems
        $EnabledCount  = ($AllItems | Where-Object { $_.Enabled }).Count
        $DisabledCount = ($AllItems | Where-Object { -not $_.Enabled }).Count
        Write-Info "$EnabledCount enabled / $DisabledCount disabled startup item(s) detected."
        Write-Host ""
        Show-StartupItemsList -Items $AllItems
        Write-Divider
        Write-Host "   [D]  Disable an item" -ForegroundColor White
        Write-Host "   [E]  Re-enable a disabled item" -ForegroundColor White
        Write-Host "   [T]  Open Task Manager (Startup tab)" -ForegroundColor White
        Write-Host "   [R]  Refresh list" -ForegroundColor DarkGray
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select an action" -Valid @('d','e','t','r','x')

        switch ($Choice) {
            'd' {
                if (($AllItems | Where-Object { $_.Enabled }).Count -eq 0) {
                    Write-Warn "No enabled items to disable."; Start-Sleep -Seconds 1; continue
                }
                $Idx = Read-NumericChoice -Prompt "   Enter item number to disable (list above)" -Max $AllItems.Count
                if ($null -ne $Idx) {
                    $Target = $AllItems[$Idx - 1]
                    if ($Target.Enabled) {
                        if (Ask-User "Disable '$($Target.Name)'" "Prevents this program from launching at sign-in. A backup is kept so it can be re-enabled.") {
                            Disable-StartupItem -Item $Target
                        }
                    } else {
                        Write-AlreadyOK "'$($Target.Name)' is already disabled."
                    }
                } else {
                    Write-Warn "Invalid item number."
                }
                Start-Sleep -Seconds 1
            }
            'e' {
                if (($AllItems | Where-Object { -not $_.Enabled }).Count -eq 0) {
                    Write-Warn "No disabled items to re-enable."; Start-Sleep -Seconds 1; continue
                }
                $Idx = Read-NumericChoice -Prompt "   Enter item number to re-enable (list above)" -Max $AllItems.Count
                if ($null -ne $Idx) {
                    $Target = $AllItems[$Idx - 1]
                    if (-not $Target.Enabled) {
                        Enable-StartupItem -Item $Target
                    } else {
                        Write-AlreadyOK "'$($Target.Name)' is already enabled."
                    }
                } else {
                    Write-Warn "Invalid item number."
                }
                Start-Sleep -Seconds 1
            }
            't' {
                Write-Info "Opening Task Manager..."
                Start-Process "taskmgr.exe" -ArgumentList "/7" -ErrorAction SilentlyContinue
            }
            'r' { }
            'x' { return }
        }
    } while ($true)
}

# ============================================================
#  SERVICES OPTIMIZER
# ============================================================
$Script:OptionalServices = @(
    @{ Name = "Fax";                                       Label = "Fax";                              Note = "Legacy fax service. Safe to disable on virtually all modern PCs." }
    @{ Name = "RemoteRegistry";                             Label = "Remote Registry";                  Note = "Allows remote registry edits. Disabled by default on most consumer PCs; safe to keep disabled." }
    @{ Name = "MapsBroker";                                 Label = "Downloaded Maps Manager";          Note = "Manages offline Windows Maps data. Safe to disable if you don't use the Maps app." }
    @{ Name = "WMPNetworkSvc";                               Label = "Windows Media Player Network Sharing"; Note = "Shares media libraries over the network. Safe to disable if unused." }
    @{ Name = "RetailDemo";                                  Label = "Retail Demo Service";               Note = "Only used for in-store demo units. Safe to disable." }
    @{ Name = "diagnosticshub.standardcollector.service";    Label = "Microsoft Diagnostics Hub";         Note = "Performance diagnostics collector used mainly by developers/Visual Studio profiling." }
    @{ Name = "SysMain";                                     Label = "SysMain (Superfetch)";              Note = "Pre-loads apps into RAM. Helpful on HDDs, often unnecessary (or counter-productive) on SSDs." }
    @{ Name = "PhoneSvc";                                    Label = "Phone Service";                     Note = "Supports cellular/'Your Phone' features. Safe to disable if you don't link an Android phone." }
)

function Get-ServiceState {
    param([string]$Name)
    $Svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if (-not $Svc) {
        return [PSCustomObject]@{ Exists = $false; Status = "N/A"; StartType = "N/A" }
    }
    $StartType = (Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction SilentlyContinue).StartMode
    return [PSCustomObject]@{ Exists = $true; Status = $Svc.Status; StartType = $StartType }
}

function Disable-OptionalService {
    param([string]$Name, [string]$Label)
    New-SystemRestorePoint
    $State = Get-ServiceState -Name $Name
    if (-not $State.Exists) {
        Write-Warn "Skipped '$Label': service not present on this system/edition."
        return
    }
    if ($State.StartType -eq "Disabled" -and $State.Status -eq "Stopped") {
        Write-AlreadyOK "'$Label' is already disabled."
        return
    }
    Backup-ServiceState -Name $Name
    try {
        if ($State.Status -eq "Running") { Stop-Service -Name $Name -Force -ErrorAction Stop }
        Set-Service -Name $Name -StartupType Disabled -ErrorAction Stop
        Write-Success "'$Label' stopped and disabled."
    } catch {
        Write-ErrorX "Could not disable '$Label': $($_.Exception.Message) (may be protected by policy)."
    }
}

function Enable-OptionalService {
    param([string]$Name, [string]$Label)
    $State = Get-ServiceState -Name $Name
    if (-not $State.Exists) {
        Write-Warn "Skipped '$Label': service not present on this system/edition."
        return
    }
    if ($State.StartType -ne "Disabled") {
        Write-AlreadyOK "'$Label' is already enabled (startup type: $($State.StartType))."
        return
    }
    try {
        Set-Service -Name $Name -StartupType Manual -ErrorAction Stop
        Start-Service -Name $Name -ErrorAction SilentlyContinue
        Write-Success "'$Label' re-enabled (startup type: Manual)."
    } catch {
        Write-ErrorX "Could not re-enable '$Label': $($_.Exception.Message)"
    }
}

function Show-ServicesOptimizer {
    do {
        Write-Banner "SERVICES OPTIMIZER"
        for ($i = 0; $i -lt $Script:OptionalServices.Count; $i++) {
            $Svc   = $Script:OptionalServices[$i]
            $State = Get-ServiceState -Name $Svc.Name
            $Tag   = if (-not $State.Exists) { "N/A     " }
                     elseif ($State.StartType -eq "Disabled") { "DISABLED" }
                     else { "ENABLED " }
            $Color = if (-not $State.Exists) { "DarkGray" } elseif ($Tag -eq "DISABLED") { "DarkGray" } else { "Green" }
            Write-Host ("   [{0,2}] [{1}] {2}" -f ($i + 1), $Tag, $Svc.Label) -ForegroundColor $Color
        }
        Write-Divider
        Write-Host "   [D]  Disable a service" -ForegroundColor White
        Write-Host "   [E]  Re-enable a service" -ForegroundColor White
        Write-Host "   [A]  Disable ALL recommended (bulk)" -ForegroundColor Magenta
        Write-Host "   [I]  Show info note for a service" -ForegroundColor DarkGray
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select an action" -Valid @('d','e','a','i','x')

        switch ($Choice) {
            'd' {
                $Idx = Read-NumericChoice -Prompt "   Enter service number to disable" -Max $Script:OptionalServices.Count
                if ($null -ne $Idx) {
                    $Svc = $Script:OptionalServices[$Idx - 1]
                    if (Ask-User "Disable '$($Svc.Label)'" $Svc.Note) {
                        Disable-OptionalService -Name $Svc.Name -Label $Svc.Label
                    }
                } else { Write-Warn "Invalid service number." }
                Start-Sleep -Seconds 1
            }
            'e' {
                $Idx = Read-NumericChoice -Prompt "   Enter service number to re-enable" -Max $Script:OptionalServices.Count
                if ($null -ne $Idx) {
                    $Svc = $Script:OptionalServices[$Idx - 1]
                    Enable-OptionalService -Name $Svc.Name -Label $Svc.Label
                } else { Write-Warn "Invalid service number." }
                Start-Sleep -Seconds 1
            }
            'a' {
                if (Ask-User "Disable ALL Recommended Services" "Disables every service listed above in one pass. Already-disabled services are reported and skipped.") {
                    foreach ($Svc in $Script:OptionalServices) {
                        Disable-OptionalService -Name $Svc.Name -Label $Svc.Label
                    }
                }
                Read-Host "   Press Enter to continue"
            }
            'i' {
                $Idx = Read-NumericChoice -Prompt "   Enter service number to view info" -Max $Script:OptionalServices.Count
                if ($null -ne $Idx) {
                    $Svc = $Script:OptionalServices[$Idx - 1]
                    Write-StatusPanel -Label $Svc.Label -Text $Svc.Note
                } else { Write-Warn "Invalid service number." }
                Read-Host "   Press Enter to continue"
            }
            'x' { return }
        }
    } while ($true)
}

# ============================================================
#  DISK CLEANUP & OPTIMIZATION
# ============================================================
function Show-DriveSpaceReport {
    Write-SectionHeader "Drive Space Report"
    $Drives = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | Where-Object { $_.Used -ne $null -and $_.Free -ne $null }
    foreach ($Drive in $Drives) {
        $TotalGB   = [math]::Round(($Drive.Used + $Drive.Free) / 1GB, 1)
        $FreeGB    = [math]::Round($Drive.Free / 1GB, 1)
        $PercentFree = if ($TotalGB -gt 0) { [math]::Round(($FreeGB / $TotalGB) * 100, 0) } else { 0 }
        $Color = if ($PercentFree -lt 10) { "Red" } elseif ($PercentFree -lt 20) { "Yellow" } else { "Green" }
        Write-Host ("   {0}:\  {1,6} GB free of {2,6} GB  ({3}% free)" -f $Drive.Name, $FreeGB, $TotalGB, $PercentFree) -ForegroundColor $Color
    }
}

function Remove-WindowsOldFolder {
    $Path = "$env:SystemDrive\Windows.old"
    if (-not (Test-Path $Path)) {
        Write-AlreadyOK "No Windows.old folder present - nothing to remove."
        return
    }
    try {
        $SizeGB = [math]::Round(((Get-ChildItem -Path $Path -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum) / 1GB, 2)
        Write-Info "Windows.old is approximately $SizeGB GB."
        Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
        Write-Success "Windows.old removed, reclaiming approximately $SizeGB GB."
    } catch {
        Write-ErrorX "Could not fully remove Windows.old: $($_.Exception.Message). Try Disk Cleanup's 'Previous Windows installations' option instead."
    }
}

function Set-HibernationState {
    param([bool]$Enable)
    $HiberFile = "$env:SystemDrive\hiberfil.sys"
    $CurrentlyEnabled = Test-Path $HiberFile
    if ($Enable -eq $CurrentlyEnabled) {
        $StateWord = if ($Enable) { "enabled" } else { "disabled" }
        Write-AlreadyOK "Hibernation is already $StateWord."
        return
    }
    try {
        if ($Enable) {
            powercfg /hibernate on | Out-Null
            Write-Success "Hibernation enabled."
        } else {
            powercfg /hibernate off | Out-Null
            Write-Success "Hibernation disabled (hiberfil.sys removed, frees disk space equal to a portion of installed RAM)."
        }
    } catch {
        Write-ErrorX "Could not change hibernation state: $($_.Exception.Message)"
    }
}

function Optimize-AllDrives {
    Write-SectionHeader "Drive Optimization (TRIM / Defrag)"
    $Volumes = Get-Volume -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Fixed' }
    if (-not $Volumes) {
        Write-Warn "No fixed volumes detected to optimize."
        return
    }
    foreach ($Vol in $Volumes) {
        try {
            Write-Info "Optimizing $($Vol.DriveLetter): ..."
            Optimize-Volume -DriveLetter $Vol.DriveLetter -ErrorAction Stop
            Write-Success "$($Vol.DriveLetter): optimized (TRIM for SSD / defrag for HDD, auto-detected)."
        } catch {
            Write-Warn "Skipped $($Vol.DriveLetter): $($_.Exception.Message)"
        }
    }
}

function Invoke-DiskCleanupUtility {
    Write-Info "Launching the native Disk Cleanup utility (cleanmgr.exe)..."
    try {
        Start-Process "cleanmgr.exe" -ErrorAction Stop
        Write-Success "Disk Cleanup launched. Follow its on-screen prompts."
    } catch {
        Write-ErrorX "Could not launch Disk Cleanup: $($_.Exception.Message)"
    }
}

function Show-DiskCleanupModule {
    Show-DriveSpaceReport

    if (Ask-User "Remove Windows.old" "Deletes the previous Windows installation backup folder (if present) to reclaim significant disk space.") {
        Remove-WindowsOldFolder
    }

    if (Ask-User "Toggle Hibernation" "Enables hibernation if currently off, or disables it (and removes hiberfil.sys) if currently on.") {
        $CurrentlyEnabled = Test-Path "$env:SystemDrive\hiberfil.sys"
        Set-HibernationState -Enable (-not $CurrentlyEnabled)
    }

    if (Ask-User "Optimize All Fixed Drives" "Runs TRIM on SSDs and defragmentation on HDDs automatically (Windows auto-detects drive type).") {
        Optimize-AllDrives
    }

    if (Ask-User "Open Native Disk Cleanup Utility" "Launches cleanmgr.exe for a full interactive cleanup pass, including system file cleanup.") {
        Invoke-DiskCleanupUtility
    }
}

# ============================================================
#  SYSTEM INFO DASHBOARD
# ============================================================
function Get-SystemInfoSnapshot {
    $OS   = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    $CPU  = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -First 1
    $CS   = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    $GPUs = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
    $Uptime = if ($OS) { (Get-Date) - $OS.LastBootUpTime } else { $null }
    $ActivePlan = try { (powercfg /getactivescheme) -replace '.*\(([^)]+)\).*', '$1' } catch { "Unknown" }
    $TotalRAMGB = if ($CS) { [math]::Round($CS.TotalPhysicalMemory / 1GB, 1) } else { 0 }
    $FreeRAMGB  = if ($OS) { [math]::Round($OS.FreePhysicalMemory * 1KB / 1GB, 1) } else { 0 }

    return [PSCustomObject]@{
        OSCaption   = if ($OS) { $OS.Caption } else { "Unknown" }
        OSBuild     = $Script:OSBuild
        CPUName     = if ($CPU) { $CPU.Name.Trim() } else { "Unknown" }
        TotalRAMGB  = $TotalRAMGB
        FreeRAMGB   = $FreeRAMGB
        GPUs        = $GPUs
        Uptime      = $Uptime
        PowerPlan   = $ActivePlan
        PSVersion   = $PSVersionTable.PSVersion.ToString()
    }
}

function Show-SystemInfoDashboard {
    do {
        Write-Banner "SYSTEM INFO DASHBOARD"
        $Info = Get-SystemInfoSnapshot

        Write-Host "   Operating System : $($Info.OSCaption) (Build $($Info.OSBuild))" -ForegroundColor White
        Write-Host "   Processor        : $($Info.CPUName)" -ForegroundColor White
        Write-Host "   Memory           : $($Info.FreeRAMGB) GB free of $($Info.TotalRAMGB) GB" -ForegroundColor White
        foreach ($GPU in $Info.GPUs) {
            Write-Host "   Graphics         : $GPU" -ForegroundColor White
        }
        if ($Info.Uptime) {
            Write-Host ("   Uptime           : {0}d {1}h {2}m" -f $Info.Uptime.Days, $Info.Uptime.Hours, $Info.Uptime.Minutes) -ForegroundColor White
        }
        Write-Host "   Active Power Plan: $($Info.PowerPlan)" -ForegroundColor White
        Write-Host "   PowerShell       : $($Info.PSVersion)" -ForegroundColor White
        Write-Host ""
        Show-DriveSpaceReport
        Write-Divider
        Write-Host "   [R]  Refresh" -ForegroundColor DarkGray
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select an action" -Valid @('r','x')
        if ($Choice -eq 'x') { return }
    } while ($true)
}

# ============================================================
#  ADVANCED REPAIR & CACHE CLEAN
# ============================================================
function Invoke-SystemRepair {
    New-SystemRestorePoint
    Write-SectionHeader "System File Checker (SFC)"
    Write-Info "Running sfc /scannow -- live output below. This can take several minutes."
    Invoke-WithRetry -OperationName "SFC Scan" -Action {
        sfc /scannow
        if ($LASTEXITCODE -ne 0) { throw "sfc /scannow exited with code $LASTEXITCODE." }
    } | Out-Null

    Write-SectionHeader "DISM Image Health Restore"
    Write-Info "Running DISM /Online /Cleanup-Image /RestoreHealth -- live output below."
    Invoke-WithRetry -OperationName "DISM RestoreHealth" -Action {
        DISM /Online /Cleanup-Image /RestoreHealth
        if ($LASTEXITCODE -ne 0) { throw "DISM exited with code $LASTEXITCODE." }
    } | Out-Null
}

function Clear-SystemCaches {
    Write-SectionHeader "Temporary File, Prefetch & Windows Update Cleanup"
    $Targets = @(
        $env:TEMP,
        "$env:SystemRoot\Temp",
        "$env:SystemRoot\Prefetch",
        "$env:SystemRoot\SoftwareDistribution\Download"
    ) | Select-Object -Unique

    $TotalFreedBytes = 0
    $LockedCount     = 0
    foreach ($Target in $Targets) {
        if (-not (Test-Path $Target)) { continue }
        Write-Info "Cleaning $Target ..."
        Get-ChildItem -Path $Target -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $Size = if ($_.PSIsContainer) { 0 } else { $_.Length }
                Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction Stop
                $TotalFreedBytes += $Size
            } catch {
                $LockedCount++
            }
        }
    }

    try {
        Write-Info "Emptying Recycle Bin..."
        Clear-RecycleBin -Force -ErrorAction SilentlyContinue
    } catch {}

    $FreedMB = [math]::Round($TotalFreedBytes / 1MB, 2)
    Write-Success "Cache cleanup complete. Approximately $FreedMB MB reclaimed."
    if ($LockedCount -gt 0) {
        Write-Warn "$LockedCount item(s) were skipped because they were locked/in use (normal for active update/prefetch files)."
    }
}

# ============================================================
#  DEBLOAT & PRIVACY
# ============================================================
$Script:BloatApps = @(
    "Microsoft.3DBuilder", "Microsoft.BingFinance", "Microsoft.BingNews", "Microsoft.BingSports",
    "Microsoft.BingWeather", "Microsoft.GetHelp", "Microsoft.Getstarted", "Microsoft.MicrosoftOfficeHub",
    "Microsoft.MicrosoftSolitaireCollection", "Microsoft.MixedReality.Portal", "Microsoft.People",
    "Microsoft.SkypeApp", "Microsoft.WindowsFeedbackHub", "Microsoft.WindowsMaps", "Microsoft.Xbox.TCUI",
    "Microsoft.XboxApp", "Microsoft.XboxGameOverlay", "Microsoft.XboxGamingOverlay", "Microsoft.XboxIdentityProvider",
    "Microsoft.XboxSpeechToTextOverlay", "Microsoft.YourPhone", "Microsoft.ZuneMusic", "Microsoft.ZuneVideo"
)

function Remove-Bloatware {
    Write-SectionHeader "Bloatware Removal"
    New-SystemRestorePoint
    $RemovedAny = $false
    foreach ($Pkg in $Script:BloatApps) {
        $Installed = Get-AppxPackage -Name $Pkg -AllUsers -ErrorAction SilentlyContinue
        if ($Installed) {
            try {
                $Installed | Remove-AppxPackage -AllUsers -ErrorAction Stop
                Write-Success "Removed $Pkg"
                $RemovedAny = $true
            } catch {
                Write-Warn "Could not remove $Pkg (may be protected by policy)."
            }
        }
    }
    if (-not $RemovedAny) {
        Write-AlreadyOK "No listed bloatware packages found - system is already clean."
    }
    Write-Info "Bloatware sweep complete."
}

function Disable-Telemetry {
    Write-SectionHeader "Telemetry & Diagnostics"
    New-SystemRestorePoint
    $Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection"
    $AlreadySet = (Get-RegValue -Path $Path -Name "AllowTelemetry") -eq 0
    $DiagTrackSvc = Get-Service -Name "DiagTrack" -ErrorAction SilentlyContinue
    $AlreadyStopped = (-not $DiagTrackSvc) -or ($DiagTrackSvc.Status -eq "Stopped" -and $DiagTrackSvc.StartType -eq "Disabled")

    if ($AlreadySet -and $AlreadyStopped) {
        Write-AlreadyOK "Telemetry is already disabled."
        return
    }

    Backup-OriginalRegValue -TweakKey "Telemetry" -Path $Path -Name "AllowTelemetry"
    Backup-ServiceState -Name "DiagTrack"
    Backup-ServiceState -Name "dmwappushservice"

    try {
        if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
        Set-ItemProperty -Path $Path -Name "AllowTelemetry" -Value 0 -Type DWord -Force

        Set-Service -Name "DiagTrack" -StartupType Disabled -ErrorAction SilentlyContinue
        Stop-Service -Name "DiagTrack" -Force -ErrorAction SilentlyContinue
        Set-Service -Name "dmwappushservice" -StartupType Disabled -ErrorAction SilentlyContinue

        $Tasks = @(
            @{ Path = "\Microsoft\Windows\Application Experience\"; Name = "Microsoft Compatibility Appraiser" },
            @{ Path = "\Microsoft\Windows\Application Experience\"; Name = "ProgramDataUpdater" },
            @{ Path = "\Microsoft\Windows\Autochk\"; Name = "Proxy" },
            @{ Path = "\Microsoft\Windows\Customer Experience Improvement Program\"; Name = "Consolidator" },
            @{ Path = "\Microsoft\Windows\Customer Experience Improvement Program\"; Name = "UsbCeip" },
            @{ Path = "\Microsoft\Windows\DiskDiagnostic\"; Name = "Microsoft-Windows-DiskDiagnosticDataCollector" }
        )
        foreach ($Task in $Tasks) {
            Disable-ScheduledTask -TaskPath $Task.Path -TaskName $Task.Name -ErrorAction SilentlyContinue | Out-Null
        }
        Write-Success "Telemetry services and scheduled diagnostics disabled."
    } catch {
        Write-ErrorX "Telemetry hardening encountered an issue: $($_.Exception.Message)"
    }
}

function Disable-AdvertisingID {
    New-SystemRestorePoint
    $Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo"
    if ((Get-RegValue -Path $Path -Name "Enabled") -eq 0) {
        Write-AlreadyOK "Advertising ID is already disabled."
        return
    }
    Backup-OriginalRegValue -TweakKey "AdvertisingID" -Path $Path -Name "Enabled"
    try {
        if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
        Set-ItemProperty -Path $Path -Name "Enabled" -Value 0 -Type DWord -Force
        Write-Success "Advertising ID disabled."
    } catch {
        Write-ErrorX "Failed to disable Advertising ID: $($_.Exception.Message)"
    }
}

function Disable-ActivityHistory {
    New-SystemRestorePoint
    $Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System"
    if ((Get-RegValue -Path $Path -Name "EnableActivityFeed") -eq 0) {
        Write-AlreadyOK "Activity History sync is already disabled."
        return
    }
    Backup-OriginalRegValue -TweakKey "ActivityHistory" -Path $Path -Name "EnableActivityFeed"
    Backup-OriginalRegValue -TweakKey "ActivityHistory" -Path $Path -Name "PublishUserActivities"
    Backup-OriginalRegValue -TweakKey "ActivityHistory" -Path $Path -Name "UploadUserActivities"
    try {
        if (-not (Test-Path $Path)) { New-Item -Path $Path -Force | Out-Null }
        Set-ItemProperty -Path $Path -Name "EnableActivityFeed" -Value 0 -Type DWord -Force
        Set-ItemProperty -Path $Path -Name "PublishUserActivities" -Value 0 -Type DWord -Force
        Set-ItemProperty -Path $Path -Name "UploadUserActivities" -Value 0 -Type DWord -Force
        Write-Success "Activity History sync disabled."
    } catch {
        Write-ErrorX "Failed to disable Activity History: $($_.Exception.Message)"
    }
}

# ============================================================
#  PERFORMANCE & GAMING OPTIMIZATION
# ============================================================
function Invoke-NetworkOptimization {
    Write-SectionHeader "Network & Ping Optimizer"
    New-SystemRestorePoint
    Write-Info "Flushing DNS cache and resetting network stack..."
    ipconfig /flushdns
    ipconfig /release
    ipconfig /renew
    netsh winsock reset
    netsh int ip reset
    Write-Success "Network stack reset and DNS flushed. Ping latency should improve."
    Write-Warn "A restart is recommended for the Winsock/IP reset to fully apply."
    $Script:PendingRestart = $true
}

function Enable-UltimatePerformancePowerPlan {
    Write-SectionHeader "Humam Ultimate Power Plan"
    New-SystemRestorePoint
    $Existing = powercfg /list | Out-String
    if ($Existing -match "Humam Ultimate Power Plan" -and $Existing -match '\*') {
        $ActiveLine = ($Existing -split "`n") | Where-Object { $_ -match "Humam Ultimate Power Plan" -and $_ -match '\*' }
        if ($ActiveLine) {
            Write-AlreadyOK "Humam Ultimate Power Plan is already active."
            return
        }
    }
    try {
        $sourceGuid = "e9a42b02-d5df-448d-aa00-03f14749eb61"
        $dupOutput = powercfg /duplicatescheme $sourceGuid 2>&1
        $newGuid = $null
        if ($dupOutput -match '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})') {
            $newGuid = $matches[1]
        }

        if ($newGuid) {
            powercfg /changename $newGuid "Humam Ultimate Power Plan" > $null
            powercfg /setactive $newGuid > $null
            Write-Success "Humam Ultimate Power Plan activated successfully."
        } else {
            if ($Existing -match "Humam Ultimate Power Plan") {
                $pattern = '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}).*Humam Ultimate Power Plan'
                if ($Existing -match $pattern) {
                    powercfg /setactive $matches[1] > $null
                    Write-Success "Humam Ultimate Power Plan activated (existing profile)."
                } else {
                    Write-Warn "Custom plan found but GUID could not be extracted. Please activate manually."
                }
            } else {
                Write-ErrorX "Could not create or activate Humam Ultimate Power Plan."
            }
        }
    } catch {
        Write-ErrorX "Could not activate Humam Ultimate Power Plan: $($_.Exception.Message)"
    }
}

# ============================================================
#  DATA-DRIVEN TWEAK ENGINE
# ============================================================
function Test-TweakAlreadyOn {
    param([hashtable]$Tweak)
    foreach ($E in $Tweak.Entries) {
        $Current = Get-RegValue -Path $E.Path -Name $E.Name
        if ("$Current" -ne "$($E.OnValue)") { return $false }
    }
    return $true
}

function Invoke-Tweak {
    param(
        [Parameter(Mandatory)][hashtable]$Tweak,
        [ValidateSet("On","Off")][string]$State = "On"
    )

    Write-SectionHeader $Tweak.Description

    if ($State -eq "On" -and (Test-TweakAlreadyOn -Tweak $Tweak)) {
        Write-AlreadyOK "$($Tweak.Key) is already applied."
        return
    }

    New-SystemRestorePoint

    Invoke-WithRetry -OperationName "Tweak: $($Tweak.Key)" -Action {
        foreach ($E in $Tweak.Entries) {
            Backup-OriginalRegValue -TweakKey $Tweak.Key -Path $E.Path -Name $E.Name
            $Value = if ($State -eq "On") { $E.OnValue } else { $E.OffValue }
            if (-not (Test-Path $E.Path)) { New-Item -Path $E.Path -Force | Out-Null }
            Set-ItemProperty -Path $E.Path -Name $E.Name -Value $Value -Type $E.Type -Force -ErrorAction Stop
        }
        Write-Success "$($Tweak.Key) applied successfully."
    } | Out-Null
}

function Enable-ClassicContextMenu {
    Write-SectionHeader "Windows 11 Classic Right-Click Menu"
    if (-not (Test-OSSupport -FeatureName "Classic Right-Click Menu" -MinBuild 22000)) { return }
    New-SystemRestorePoint

    $path = "HKCU:\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"
    $CurrentDefault = Get-RegValue -Path $path -Name "(default)"
    if ((Test-Path $path) -and ($CurrentDefault -eq "")) {
        Write-AlreadyOK "Classic context menu is already active."
        return
    }
    # No prior tweak-value backup needed here: the reversal action (below,
    # via Reset-AllTweaksToDefaults) simply deletes the CLSID override key,
    # which is the exact, safe, no-data-loss way to undo this specific tweak.

    try {
        if (-not (Test-Path $path)) {
            New-Item -Path $path -Force | Out-Null
        }
        Set-ItemProperty -Path $path -Name "(default)" -Value "" -Type String -Force
        Write-Success "Classic context menu restored."

        if (Ask-User "Restart Windows Explorer" "Applies the classic menu immediately by restarting explorer.exe.") {
            Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
            Start-Process explorer
            Write-Success "Explorer restarted. Classic menu should now be active."
        } else {
            Write-Info "Change will take effect after you sign out or restart Explorer manually."
        }
    } catch {
        Write-ErrorX "Failed to restore classic context menu: $($_.Exception.Message)"
    }
}

# ============================================================
#  SMART SYSTEM TWEAKS (extended)
# ============================================================
function Disable-MouseAcceleration {
    New-SystemRestorePoint
    $Path = "HKCU:\Control Panel\Mouse"
    $Speed = Get-RegValue -Path $Path -Name "MouseSpeed"
    $Thr1  = Get-RegValue -Path $Path -Name "MouseThreshold1"
    $Thr2  = Get-RegValue -Path $Path -Name "MouseThreshold2"
    if ($Speed -eq "0" -and $Thr1 -eq "0" -and $Thr2 -eq "0") {
        Write-AlreadyOK "Mouse acceleration is already disabled."
        return
    }
    Backup-OriginalRegValue -TweakKey "MouseAccel" -Path $Path -Name "MouseSpeed"
    Backup-OriginalRegValue -TweakKey "MouseAccel" -Path $Path -Name "MouseThreshold1"
    Backup-OriginalRegValue -TweakKey "MouseAccel" -Path $Path -Name "MouseThreshold2"
    try {
        Set-ItemProperty -Path $Path -Name "MouseSpeed" -Value "0" -ErrorAction Stop
        Set-ItemProperty -Path $Path -Name "MouseThreshold1" -Value "0" -ErrorAction Stop
        Set-ItemProperty -Path $Path -Name "MouseThreshold2" -Value "0" -ErrorAction Stop
        Write-Success "Raw pointer precision applied (mouse acceleration fully disabled)."
    } catch {
        Write-Warn "Skipped: Mouse registry keys are restricted."
    }
}

function Enable-MinimalistTaskbar {
    if (-not (Test-OSSupport -FeatureName "Windows 11 Minimalist Taskbar" -MinBuild 22000)) { return }
    New-SystemRestorePoint
    $Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    if ((Get-RegValue -Path $Path -Name "TaskbarAl") -eq 0 -and (Get-RegValue -Path $Path -Name "TaskbarDa") -eq 0) {
        Write-AlreadyOK "Minimalist taskbar layout is already applied."
        return
    }
    Backup-OriginalRegValue -TweakKey "Taskbar" -Path $Path -Name "TaskbarAl"
    Backup-OriginalRegValue -TweakKey "Taskbar" -Path $Path -Name "TaskbarDa"
    Backup-OriginalRegValue -TweakKey "Taskbar" -Path $Path -Name "TaskbarMn"
    try {
        Set-ItemProperty -Path $Path -Name "TaskbarAl" -Value 0 -ErrorAction Stop
        Set-ItemProperty -Path $Path -Name "TaskbarDa" -Value 0 -ErrorAction Stop
        Set-ItemProperty -Path $Path -Name "TaskbarMn" -Value 0 -ErrorAction Stop
        Write-Success "Taskbar alignments updated."
    } catch {
        Write-Warn "Skipped: Windows 11 taskbar mutation blocked."
    }
}

function Remove-OneDrivePackage {
    New-SystemRestorePoint
    $ODSetup = "$env:SystemRoot\SysWOW64\OneDriveSetup.exe"
    $ODInstallFolder = "$env:LOCALAPPDATA\Microsoft\OneDrive"
    if (-not (Test-Path $ODInstallFolder) -and -not (Get-Process -Name "OneDrive" -ErrorAction SilentlyContinue)) {
        Write-AlreadyOK "OneDrive is already removed/not installed."
        return
    }
    Backup-OneDriveFiles
    try {
        Stop-Process -Name "OneDrive" -Force -ErrorAction SilentlyContinue
        if (Test-Path $ODSetup) {
            Start-Process $ODSetup -ArgumentList "/uninstall" -Wait -NoNewWindow
            Write-Success "OneDrive uninstall sequence executed."
        } else {
            Write-Warn "Skipped: OneDrive standalone installer payload not found."
        }
    } catch {
        Write-Warn "Skipped: Active locks prevented OneDrive termination."
    }
}

# ============================================================
#  NEW: MICROSOFT EDGE REMOVAL / REINSTALL
# ============================================================
function Remove-MicrosoftEdge {
    Write-SectionHeader "Remove Microsoft Edge"
    New-SystemRestorePoint
    $EdgeUninstaller = "$env:ProgramFiles\Microsoft\Edge\Application\*\Installer\setup.exe"
    $UninstallPath = Get-ChildItem -Path $EdgeUninstaller -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($UninstallPath) {
        Backup-EdgeState
        $Removed = Invoke-WithRetry -OperationName "Remove Microsoft Edge" -Action {
            Start-Process -FilePath $UninstallPath.FullName -ArgumentList "--uninstall --force-uninstall --system-level" -Wait -NoNewWindow -ErrorAction Stop
        }
        if ($Removed) {
            Write-Success "Microsoft Edge has been uninstalled (a system restart is recommended). A version/settings backup was saved to Desktop\HTCore_EdgeBackup."
            $Script:PendingRestart = $true
        }
    } else {
        Write-Warn "Edge is either a built-in component and cannot be fully removed, or it is not installed as a standalone. You may reset Edge instead."
    }
}

function Install-MicrosoftEdge {
    Write-SectionHeader "Install Microsoft Edge"
    if ($global:WingetAvailable) {
        Write-Info "Installing Microsoft Edge via winget..."
        $Result = Smart-Deploy "Microsoft.Edge" "Microsoft Edge"
        if ($Result.Status -eq 'Success') {
            Restore-EdgeState
        }
    } else {
        Write-Warn "Winget unavailable. Opening official download page for a manual install..."
        Write-Info "Manual install steps: download the installer from the page that opens, run it, then use this menu's [6] Reinstall Edge option again if you want your backed-up settings restored."
        Start-Process "https://www.microsoft.com/en-us/edge/download"
    }
}

# ============================================================
#  NOTE: "Restore Windows Default Settings" now lives in the
#  v3.3 Safety Net block as Reset-AllTweaksToDefaults(), which
#  restores YOUR original captured values (via Backup-OriginalRegValue)
#  instead of only generic Windows defaults. See that function for
#  the full implementation; this stub keeps the old name working
#  for full backward compatibility with anything that might call it.
# ============================================================
function Reset-WindowsDefaultSettings {
    Reset-AllTweaksToDefaults
}

# ============================================================
#  OFFICE DEPLOYMENT – FINAL ENHANCED VERSION
# ============================================================
function Get-SpecialFolderSafe {
    # Wraps [Environment]::GetFolderPath so it NEVER throws.
    # Some PowerShell hosts / .NET runtimes do not support the string-name
    # overload of GetFolderPath (this is the root cause of the
    # "Specified method is not supported." crash seen when opening the
    # Office Deployment module) - the numeric [Environment+SpecialFolder]
    # enum value overload is used instead, which is universally supported.
    param([Parameter(Mandatory = $true)][int]$SpecialFolderCode)
    try {
        $Path = [Environment]::GetFolderPath($SpecialFolderCode)
        if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
        return $Path
    } catch {
        Write-Log "GetFolderPath-WARN: could not resolve special folder code $SpecialFolderCode : $($_.Exception.Message)"
        return $null
    }
}

function Find-OfficeDeploymentFolder {
    # Numeric SpecialFolder codes used deliberately instead of the string
    # names ("Desktop" / "CommonDesktopDirectory") - the string-name
    # overload of GetFolderPath is NOT supported in all PowerShell
    # environments and throws "Specified method is not supported."
    #   0  = Desktop
    #   25 = CommonDesktopDirectory (All Users desktop)
    $CandidateBases = @(
        (Get-SpecialFolderSafe -SpecialFolderCode 0)
        (Get-SpecialFolderSafe -SpecialFolderCode 25)
        "$env:USERPROFILE\OneDrive\Desktop"
        "$env:PUBLIC\Desktop"
        "$env:USERPROFILE\Desktop"
    )

    # Validate every candidate before use: drop nulls/empties and any path
    # that isn't actually a real, reachable directory.
    $Desktops = $CandidateBases |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique |
        Where-Object {
            try { Test-Path -Path $_ -PathType Container -ErrorAction Stop } catch { $false }
        }

    foreach ($Base in $Desktops) {
        try {
            $Candidate = Join-Path -Path $Base -ChildPath "Office"
        } catch {
            continue
        }
        if (Test-OfficeFolderValid -Folder $Candidate) {
            return $Candidate
        }
    }
    return $null
}

function Find-OfficeSetupFile {
    # NOTE: each candidate name is joined individually with its own
    # Join-Path call (previously several names were passed as a single
    # comma-separated ChildPath argument, which PowerShell parses as an
    # array and does NOT produce the intended set of full paths - that
    # silently broke detection of "setup.exe.exe" and similar variants).
    param([string]$Folder)
    if ([string]::IsNullOrWhiteSpace($Folder)) { return $null }
    if (-not (Test-Path -Path $Folder -PathType Container -ErrorAction SilentlyContinue)) { return $null }

    $ExactNames = @("setup.exe", "setup.exe.exe", "Setup.exe", "Setup.exe.exe")
    foreach ($Name in $ExactNames) {
        try {
            $f = Join-Path -Path $Folder -ChildPath $Name
            if (Test-Path -Path $f -PathType Leaf -ErrorAction SilentlyContinue) { return $f }
        } catch {}
    }

    try {
        $Pattern = Join-Path -Path $Folder -ChildPath "officedeploymenttool*.exe"
        $Found = Get-ChildItem -Path $Pattern -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Found) { return $Found.FullName }
    } catch {}

    try {
        $AnyExe = Get-ChildItem -Path $Folder -Filter *.exe -ErrorAction SilentlyContinue
        foreach ($exe in $AnyExe) {
            if ((Test-ValidOfficeSetup -FilePath $exe.FullName) -or (Test-IsSelfExtractor -FilePath $exe.FullName)) {
                return $exe.FullName
            }
        }
    } catch {}

    return $null
}

function Find-OfficeConfigFile {
    # Same fix as Find-OfficeSetupFile: each candidate filename gets its
    # own Join-Path call rather than being bundled into one ChildPath
    # argument via commas (which PowerShell treats as an array and does
    # not resolve to individual full paths).
    param([string]$Folder)
    if ([string]::IsNullOrWhiteSpace($Folder)) { return $null }
    if (-not (Test-Path -Path $Folder -PathType Container -ErrorAction SilentlyContinue)) { return $null }

    $ExactNames = @("configuration.xml", "Configuration.xml", "configuration.xml.xml", "Configuration.xml.xml")
    foreach ($Name in $ExactNames) {
        try {
            $f = Join-Path -Path $Folder -ChildPath $Name
            if (Test-Path -Path $f -PathType Leaf -ErrorAction SilentlyContinue) { return $f }
        } catch {}
    }

    try {
        $AnyXml = Get-ChildItem -Path $Folder -Filter *.xml -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($AnyXml) { return $AnyXml.FullName }
    } catch {}

    return $null
}

function Test-OfficeFolderValid {
    param([string]$Folder)
    if ([string]::IsNullOrWhiteSpace($Folder)) { return $false }
    try {
        if (-not (Test-Path -Path $Folder -PathType Container -ErrorAction Stop)) { return $false }
    } catch { return $false }
    $Setup = Find-OfficeSetupFile -Folder $Folder
    $Config = Find-OfficeConfigFile -Folder $Folder
    return ($null -ne $Setup) -and ($null -ne $Config)
}

function Select-OfficeFolderDialog {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $Dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $Dialog.Description = "Select the folder containing the Office Deployment Tool and configuration.xml"
        $Dialog.ShowNewFolderButton = $false
        if ($Dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            return $Dialog.SelectedPath
        }
    } catch {
        Write-ErrorX "Could not open folder picker: $($_.Exception.Message)"
    }
    return $null
}

function Pick-FileDialog {
    param([string]$Title, [string]$Filter)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $Dialog = New-Object System.Windows.Forms.OpenFileDialog
        $Dialog.Title = $Title
        $Dialog.Filter = $Filter
        $Dialog.CheckFileExists = $true
        if ($Dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
            return $Dialog.FileName
        }
    } catch {
        Write-ErrorX "Could not open file picker: $($_.Exception.Message)"
    }
    return $null
}

function Test-IsSelfExtractor {
    param([string]$FilePath)
    try {
        $result = & $FilePath /? 2>&1 | Out-String
        if ($result -match "/extract") { return $true }
    } catch {}
    return $false
}

function Test-ValidOfficeSetup {
    param([string]$FilePath)
    try {
        $result = & $FilePath /? 2>&1 | Out-String
        if ($result -match "/configure") { return $true }
    } catch {}
    return $false
}

function Invoke-ExtractSelfExtractor {
    param([string]$ExtractorPath, [string]$DestinationFolder)
    Write-Info "Extracting Office Deployment Tool files to '$DestinationFolder'..."
    try {
        $Argument = '/extract:' + [char]34 + $DestinationFolder + [char]34
        $Proc = Start-Process -FilePath $ExtractorPath -ArgumentList $Argument -Wait -NoNewWindow -PassThru
        if ($Proc.ExitCode -eq 0) {
            Write-Success "Extraction complete. The real setup.exe should now be in '$DestinationFolder'."
            return $true
        } else {
            Write-ErrorX "Extraction failed (exit code $($Proc.ExitCode))."
            return $false
        }
    } catch {
        Write-ErrorX "Extraction failed: $($_.Exception.Message)"
        return $false
    }
}

function Invoke-OfficeDeploymentInstall {
    param([string]$SetupExe, [string]$ConfigXml)

    if (-not (Test-ValidOfficeSetup -FilePath $SetupExe)) {
        if (Test-IsSelfExtractor -FilePath $SetupExe) {
            Write-Warn "This is the Office Deployment Tool self-extractor, not the final setup.exe."
            $ExtractDir = Split-Path $SetupExe -Parent
            if (Ask-User "Auto-Extract ODT" "Extracts the real setup.exe into the same folder ('$ExtractDir'). After extraction, installation will continue automatically.") {
                if (Invoke-ExtractSelfExtractor -ExtractorPath $SetupExe -DestinationFolder $ExtractDir) {
                    $RealSetup = Find-OfficeSetupFile -Folder $ExtractDir
                    if (-not $RealSetup) {
                        Write-ErrorX "Extraction succeeded, but could not locate the resulting setup.exe. Please check the folder and try again."
                        return "FAIL"
                    }
                    Write-Info "Using the extracted setup.exe: $RealSetup"
                    $SetupExe = $RealSetup
                } else { return "FAIL" }
            } else {
                Write-Info "You can extract it manually by running: $SetupExe /extract:`"$ExtractDir`""
                return "FAIL"
            }
        } else {
            Write-ErrorX "The selected setup.exe does not support /configure and is not a recognized self-extractor."
            Write-Warn "Please download the correct Office Deployment Tool using option [D] from the menu."
            return "FAIL"
        }
    }

    Write-Success "Office deployment files confirmed:"
    Write-Info "   Setup : $SetupExe"
    Write-Info "   Config: $ConfigXml"

    if (Ask-User "Auto-Install Office" "Runs setup.exe /configure configuration.xml. DO NOT close the setup window.") {
        try {
            Write-Info "Starting Office installation..."
            $Argument = '/configure ' + [char]34 + $ConfigXml + [char]34
            $Proc = Start-Process -FilePath $SetupExe -ArgumentList $Argument -Wait -NoNewWindow -PassThru
            if ($Proc.ExitCode -eq 0) {
                Write-Success "Office installation command completed. Verify Office is installed."
            } else {
                Write-ErrorX "Office installation exited with code $($Proc.ExitCode). Check your configuration."
            }
        } catch {
            Write-ErrorX "Office installation failed: $($_.Exception.Message)"
        }
    } else {
        Write-Info "You can manually run:"
        Write-Info "   $SetupExe /configure $ConfigXml"
    }
    return "OK"
}

function Show-OfficeDeployment {
    Write-Banner "MICROSOFT OFFICE DEPLOYMENT"
    Write-ModulePreview -Items @(
        "Auto-detects the Office Deployment Tool and configuration file (even with default names).",
        "If the self-extractor is present, it will be extracted automatically.",
        "Missing files can be downloaded or selected manually."
    )

    do {
        $OfficeFolder = Find-OfficeDeploymentFolder
        $SetupExe = $null
        $ConfigXml = $null
        if ($OfficeFolder) {
            $SetupExe  = Find-OfficeSetupFile -Folder $OfficeFolder
            $ConfigXml = Find-OfficeConfigFile -Folder $OfficeFolder
            if ($SetupExe -and $ConfigXml) {
                $result = Invoke-OfficeDeploymentInstall -SetupExe $SetupExe -ConfigXml $ConfigXml
                if ($result -eq "OK") {
                    break
                }
            } else {
                Write-Warn "The 'Office' folder was found, but one or both required files are missing."
            }
        } else {
            Write-Warn "Office deployment folder not found automatically."
        }

        $SetupMissing  = -not $SetupExe
        $ConfigMissing = -not $ConfigXml

        Write-Host ""
        if ($SetupMissing)   { Write-ErrorX "❌ Office Deployment Tool (setup.exe / officedeploymenttool*.exe) not found." }
        else                 { Write-Success "✔ Office Deployment Tool found." }
        if ($ConfigMissing)  { Write-ErrorX "❌ configuration.xml not found." }
        else                 { Write-Success "✔ configuration.xml found." }

        Write-Host ""
        Write-Host "   [D]  Download Office Deployment Tool (Self-Extractor)" -ForegroundColor White
        Write-Host "   [C]  Download Office Customization Tool (configuration.xml)" -ForegroundColor White
        Write-Host "   [B]  Browse for Office folder (smart detection)" -ForegroundColor White
        Write-Host "   [R]  Retry auto-detection" -ForegroundColor Yellow
        Write-Host "   [M]  Manual pick: choose setup.exe & configuration.xml separately" -ForegroundColor White
        Write-Host "   [X]  Return to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (B/D/C/R/M/X)" -Valid @('b','d','c','r','m','x')

        switch ($Choice) {
            'b' {
                $Picked = Select-OfficeFolderDialog
                if (-not $Picked) {
                    Write-Info "Folder selection cancelled."
                } else {
                    $SetupExe  = Find-OfficeSetupFile -Folder $Picked
                    $ConfigXml = Find-OfficeConfigFile -Folder $Picked
                    if ($SetupExe -and $ConfigXml) {
                        $result = Invoke-OfficeDeploymentInstall -SetupExe $SetupExe -ConfigXml $ConfigXml
                        if ($result -eq "OK") {
                            Read-Host "   Press Enter to continue"
                            return
                        }
                    } else {
                        Write-ErrorX "The selected folder does not contain both required files."
                        if (-not $SetupExe)  { Write-ErrorX "   Missing: Office Deployment Tool (setup.exe / officedeploymenttool*.exe)" }
                        else { Write-Success "   ✔ Office Deployment Tool found" }
                        if (-not $ConfigXml) { Write-ErrorX "   Missing: configuration.xml" }
                        else { Write-Success "   ✔ configuration.xml found" }
                        Write-Warn "You can download the missing items using [D] or [C], or pick them manually using [M]."
                    }
                }
                Read-Host "   Press Enter to continue"
            }
            'd' {
                Write-Info "Opening Office Deployment Tool download page..."
                Start-Process "https://www.microsoft.com/en-us/download/details.aspx?id=49117"
                Read-Host "   Press Enter to continue"
            }
            'c' {
                Write-Info "Opening Office Customization Tool (configuration.xml)..."
                Start-Process "https://config.office.com/deploymentsettings"
                Read-Host "   Press Enter to continue"
            }
            'r' {
                Write-Info "Retrying auto-detection..."
            }
            'm' {
                Write-Info "Pick the Office Deployment Tool (setup.exe or officedeploymenttool*.exe)..."
                $SetupExe = Pick-FileDialog -Title "Select Office Deployment Tool" -Filter "Executable files (*.exe)|*.exe"
                if (-not $SetupExe) {
                    Write-Info "Operation cancelled."
                    Read-Host "   Press Enter to continue"
                    continue
                }
                Write-Info "Now pick the configuration.xml file..."
                $ConfigXml = Pick-FileDialog -Title "Select configuration.xml" -Filter "XML files (*.xml)|*.xml"
                if (-not $ConfigXml) {
                    Write-Info "Operation cancelled."
                    Read-Host "   Press Enter to continue"
                    continue
                }
                $result = Invoke-OfficeDeploymentInstall -SetupExe $SetupExe -ConfigXml $ConfigXml
                if ($result -eq "OK") {
                    Read-Host "   Press Enter to continue"
                    return
                }
                Read-Host "   Press Enter to continue"
            }
            'x' { return }
        }
    } while ($true)

    Read-Host "   Press Enter to continue"
}

# ============================================================
#  EXPANDED APP CATALOG
# ============================================================
$Apps_Basic = @(
    @("Google.Chrome", "Google Chrome"), @("Spotify.Spotify", "Spotify (Win32)"),
    @("Discord.Discord", "Discord"), @("9NKSQCEZVDDB", "WhatsApp (Store)"),
    @("9PKTQ5699M62", "iCloud (Store)"), @("Apple.iTunes", "iTunes"),
    @("7zip.7zip", "7-Zip"), @("VideoLAN.VLC", "VLC Media Player")
)
$Apps_Dev = @(
    @("Anysphere.Cursor", "Cursor IDE"), @("Microsoft.VisualStudioCode", "VS Code"),
    @("JetBrains.PyCharm.Community", "PyCharm"), @("Apache.NetBeans", "NetBeans IDE"),
    @("MSYS2.MSYS2", "GCC Compiler"), @("Ollama.Ollama", "Ollama AI"),
    @("TheDocumentFoundation.LibreOffice", "LibreOffice"), @("Git.Git", "Git")
)
$Apps_Gaming = @(
    @("Valve.Steam", "Steam"), @("EpicGames.EpicGamesLauncher", "Epic Games"),
    @("RockstarGames.Launcher", "Rockstar Games"), @("BlueStacks.BlueStacks", "BlueStacks 5")
)
$Apps_Tools = @(
    @("CPUID.CPU-Z", "CPU-Z"), @("TechPowerUp.GPU-Z", "GPU-Z"),
    @("CPUID.HWMonitor", "HWMonitor"), @("CrystalDewWorld.CrystalDiskInfo", "CrystalDiskInfo"),
    @("Guru3D.Afterburner", "MSI Afterburner"), @("Notion.Notion", "Notion")
)

# ============================================================
#  CATEGORY PROCESSOR
# ============================================================
function Process-AppCategory {
    param($AppList, $CategoryName)

    Write-SectionHeader $CategoryName

    if ($Script:LastBulkChoice) {
        Write-Host "   Last bulk choice: $($Script:LastBulkChoice.Method). Reuse it for this category?" -ForegroundColor Yellow
        if (Ask-User "Reuse Last Bulk Mode" "Applies the '$($Script:LastBulkChoice.Method)' method to every app in '$CategoryName' without asking again.") {
            foreach ($App in $AppList) {
                $res = Smart-Deploy -AppId $App[0] -AppName $App[1] -Bulk -BulkMethod $Script:LastBulkChoice.Method
                if ($res.Status -eq 'Quit') { break }
            }
            return "OK"
        }
    }

    Write-Host "   y = Bulk auto (winget install all silently)" -ForegroundColor Yellow
    Write-Host "   m = Bulk manual (open official websites for all)" -ForegroundColor Yellow
    Write-Host "   n = Choose individually" -ForegroundColor Yellow
    Write-Host "   b = Back to previous menu" -ForegroundColor Yellow
    Write-Host "   q = Quit to main menu" -ForegroundColor Yellow
    $bulkChoice = Read-Choice -Prompt "   Choose (y/m/n/b/q)" -Valid @('y','m','n','b','q')
    if ($bulkChoice -eq 'q') { return "QUIT" }
    if ($bulkChoice -eq 'b') { return "BACK" }

    if ($bulkChoice -eq 'y' -or $bulkChoice -eq 'm') {
        $method = if ($bulkChoice -eq 'y') { 'auto' } else { 'manual' }
        $Script:LastBulkChoice = @{Method=$method}

        $results = @{}
        foreach ($App in $AppList) {
            $res = Smart-Deploy -AppId $App[0] -AppName $App[1] -Bulk -BulkMethod $method
            if ($res.Status -eq 'Quit') { break }
            $results[$App[1]] = $res.Status
        }

        Write-Divider
        $success = ($results.GetEnumerator() | Where-Object { $_.Value -eq 'Success' }).Count
        $failed  = ($results.GetEnumerator() | Where-Object { $_.Value -eq 'Failed' }).Count
        $skipped = ($results.GetEnumerator() | Where-Object { $_.Value -eq 'Skipped' }).Count
        Write-Info "Bulk summary for '$CategoryName': $success succeeded, $failed failed, $skipped skipped."
        return "OK"
    }

    foreach ($App in $AppList) {
        $result = Smart-Deploy $App[0] $App[1]
        if ($result.Status -eq 'Quit') {
            Write-Warn "Exiting '$CategoryName' and returning to main menu."
            return "QUIT"
        }
        if ($result.Status -eq 'Back') {
            Write-Warn "Returning to category selection."
            return "BACK"
        }
    }
    return "OK"
}

# ============================================================
#  RUNTIMES MODULE
# ============================================================
$Runtimes = @(
    @("Microsoft.DirectX", "DirectX End-User Runtime"),
    @("Microsoft.VCRedist.2015+.x64", "Visual C++ Redistributables"),
    @("Microsoft.DotNet.DesktopRuntime.8", ".NET Desktop Runtime"),
    @("Oracle.JavaRuntimeEnvironment", "Java Runtime Environment")
)

function Show-RuntimesModule {
    if ($global:WingetAvailable) {
        Write-Host "   y = Bulk auto install" -ForegroundColor Yellow
        Write-Host "   m = Bulk manual (official websites)" -ForegroundColor Yellow
        Write-Host "   n = Choose individually" -ForegroundColor Yellow
        Write-Host "   b = Back" -ForegroundColor Yellow
        Write-Host "   q = Quit to main menu" -ForegroundColor Yellow
        $bulkChoice = Read-Choice -Prompt "   Choose (y/m/n/b/q)" -Valid @('y','m','n','b','q')
        switch ($bulkChoice) {
            'q' { return }
            'b' { return }
            'y' {
                foreach ($r in $Runtimes) {
                    Smart-Deploy -AppId $r[0] -AppName $r[1] -Bulk -BulkMethod 'auto'
                }
            }
            'm' {
                foreach ($r in $Runtimes) {
                    Smart-Deploy -AppId $r[0] -AppName $r[1] -Bulk -BulkMethod 'manual'
                }
            }
            'n' {
                foreach ($r in $Runtimes) {
                    $res = Smart-Deploy $r[0] $r[1]
                    if ($res.Status -eq 'Quit' -or $res.Status -eq 'Back') { break }
                }
            }
        }
        Write-Success "Runtimes block finished."
    } else {
        Write-Warn "Winget unavailable; cannot install runtimes automatically."
    }
}

# ============================================================
#  SUB-MENU FUNCTIONS (hierarchical)
# ============================================================

function Show-AppDeploymentHub {
    do {
        Write-Banner "APP DEPLOYMENT HUB"
        Write-ModulePreview -Items @(
            "Essential Apps: $($Apps_Basic.Count) items - Chrome, Spotify, Discord, WhatsApp, iTunes, 7-Zip, VLC...",
            "Programming & AI Core: $($Apps_Dev.Count) items - Cursor, VS Code, PyCharm, NetBeans, MSYS2, Ollama...",
            "Gaming Launchers: $($Apps_Gaming.Count) items - Steam, Epic, Rockstar, BlueStacks + auto GPU app",
            "Hardware Diagnostics: $($Apps_Tools.Count) items - CPU-Z, GPU-Z, HWMonitor... + auto motherboard app"
        )
        do {
            Write-Banner "APP DEPLOYMENT HUB"
            Write-Host "   [A]  Essential Apps" -ForegroundColor White
            Write-Host "   [B]  Programming & AI Core" -ForegroundColor White
            Write-Host "   [C]  Gaming Launchers" -ForegroundColor White
            Write-Host "   [D]  Hardware Diagnostics" -ForegroundColor White
            Write-Host "   [E]  Check & Deploy ALL Categories" -ForegroundColor Magenta
            Write-Host "   [X]  Back to Software Management" -ForegroundColor DarkGray
            Write-Divider

            $AppMenu = Read-Choice -Prompt "   Select Category (A/B/C/D/E/X)" -Valid @('a','b','c','d','e','x')
            $RunAll  = ($AppMenu.ToUpper() -eq 'E')

            if ($AppMenu.ToUpper() -eq 'A' -or $RunAll) {
                $status = Process-AppCategory $Apps_Basic "Essential Apps"
                if ($status -eq "QUIT" -and $RunAll) { break }
                if ($status -eq "BACK" -and $RunAll) { break }
            }
            if ($AppMenu.ToUpper() -eq 'B' -or $RunAll) {
                $status = Process-AppCategory $Apps_Dev "Programming & AI Core"
                if ($status -eq "QUIT" -and $RunAll) { break }
                if ($status -eq "BACK" -and $RunAll) { break }
            }
            if ($AppMenu.ToUpper() -eq 'C' -or $RunAll) {
                $status = Process-AppCategory $Apps_Gaming "Gaming Launchers"
                if ($status -eq "QUIT" -and $RunAll) { break }
                if ($status -eq "BACK" -and $RunAll) { break }
                $HW = Hardware-Check
                if ($HW.GPUApp) {
                    $status = Smart-Deploy $HW.GPUApp "GPU Hardware Software ($($HW.GPUName))"
                    if ($status.Status -eq 'Quit' -and $RunAll) { break }
                }
            }
            if ($AppMenu.ToUpper() -eq 'D' -or $RunAll) {
                $status = Process-AppCategory $Apps_Tools "Hardware Diagnostics"
                if ($status -eq "QUIT" -and $RunAll) { break }
                if ($status -eq "BACK" -and $RunAll) { break }
                $HW = Hardware-Check
                if ($HW.MoboApp) {
                    $status = Smart-Deploy $HW.MoboApp "Official Motherboard App ($($HW.MoboName))"
                    if ($status.Status -eq 'Quit' -and $RunAll) { break }
                }
            }

            if (-not $RunAll -and $AppMenu.ToUpper() -ne 'X') {
                Read-Host "   Press Enter to continue"
            } elseif ($RunAll) {
                Write-Success "All categories processed."
                Read-Host "   Press Enter to continue"
            }
        } while ($AppMenu.ToUpper() -ne 'X' -and -not $RunAll)
    } while ($false)
}

function Show-SoftwareManagementMenu {
    do {
        Write-Banner "📦 SOFTWARE MANAGEMENT"
        Write-ModulePreview -Items @(
            "App Deployment Hub: Essential Apps, Programming & AI Core, Gaming Launchers, Hardware Diagnostics",
            "Core API Runtimes: DirectX, VC++, .NET, Java (bulk or individual)",
            "Startup Program Manager: control which programs launch at boot",
            "Microsoft Office Deployment: auto-install or manual guide"
        )
        Write-Host "   [1]  App Deployment Hub" -ForegroundColor White
        Write-Host "   [2]  Core API Runtimes" -ForegroundColor White
        Write-Host "   [3]  Startup Program Manager" -ForegroundColor White
        Write-Host "   [4]  Microsoft Office Deployment" -ForegroundColor White
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (1-4, X)" -Valid @('1','2','3','4','x')
        switch ($Choice) {
            '1' { Show-AppDeploymentHub }
            '2' { Write-Banner "CORE API RUNTIMES"; Write-ModulePreview -Items @("DirectX, VC++, .NET, Java"); Show-RuntimesModule; Read-Host "   Press Enter to continue" }
            '3' { Show-StartupProgramManager }
            '4' { Show-OfficeDeployment }
            'X' { return }
        }
    } while ($true)
}

function Show-SystemOptimizationMenu {
    do {
        Write-Banner "⚡ SYSTEM OPTIMIZATION"
        Write-ModulePreview -Items @(
            "Smart System Tweaks: Dark mode, mouse, taskbar, OneDrive, Edge, reset defaults",
            "Performance & Gaming: Network optimizer, Humam Ultimate Power Plan, Game Mode, Classic context menu"
        )
        Write-Host "   [1]  Smart System Tweaks" -ForegroundColor White
        Write-Host "   [2]  Performance & Gaming Optimization" -ForegroundColor White
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (1-2, X)" -Valid @('1','2','x')
        switch ($Choice) {
            '1' {
                $ReturnToOptimization = $false
                do {
                    Write-Banner "SMART SYSTEM TWEAKS"
                    Write-ModulePreview -Items @(
                        "Global OS Dark Mode - forces dark theme across apps and system",
                        "Disable Mouse Acceleration - true raw pointer precision (Speed+Thresholds)",
                        "Windows 11 Minimalist Taskbar - left alignment, widget/chat removal (Win11 only)",
                        "Purge Microsoft OneDrive - terminates and uninstalls OneDrive",
                        "Remove Microsoft Edge - uninstall standalone Chromium Edge (if possible)",
                        "Reinstall Microsoft Edge - download and install latest Edge via winget",
                        "Reset ALL Tweaks to Windows Defaults - reverts tweaks to your originals"
                    )
                    Write-Host "   [1]  Global OS Dark Mode" -ForegroundColor White
                    Write-Host "   [2]  Disable Mouse Acceleration" -ForegroundColor White
                    Write-Host "   [3]  Windows 11 Minimalist Taskbar" -ForegroundColor White
                    Write-Host "   [4]  Purge Microsoft OneDrive" -ForegroundColor White
                    Write-Host "   [5]  Remove Microsoft Edge" -ForegroundColor White
                    Write-Host "   [6]  Reinstall Microsoft Edge" -ForegroundColor White
                    Write-Host "   [7]  Reset ALL Tweaks to Windows Defaults" -ForegroundColor White
                    Write-Host "   [X]  Back to System Optimization" -ForegroundColor DarkGray
                    Write-Divider
                    $Choice = Read-Choice -Prompt "   Select option (1-7, X)" -Valid @('1','2','3','4','5','6','7','x')
                    switch ($Choice) {
                        '1' {
    if (Ask-User "Dark Mode" "Force dark theme.") {
        Invoke-Tweak -Tweak ($Script:TweakCatalog | Where-Object { $_.Key -eq "DarkMode" }) -State "On"
    }
    Read-Host "   Press Enter to continue"
}
                        '2' { if (Ask-User "Mouse Acceleration" "Disable mouse acceleration.") { Disable-MouseAcceleration }; Read-Host "   Press Enter to continue" }
                        '3' { if (Ask-User "Taskbar" "Minimalist Win11 taskbar.") { Enable-MinimalistTaskbar }; Read-Host "   Press Enter to continue" }
                        '4' { if (Ask-User "OneDrive" "Purge OneDrive.") { Remove-OneDrivePackage }; Read-Host "   Press Enter to continue" }
                        '5' { if (Ask-User "Remove Edge" "Attempt to uninstall Edge.") { Remove-MicrosoftEdge }; Read-Host "   Press Enter to continue" }
                        '6' { if (Ask-User "Reinstall Edge" "Install Edge via winget.") { Install-MicrosoftEdge }; Read-Host "   Press Enter to continue" }
                        '7' { Reset-AllTweaksToDefaults; Read-Host "   Press Enter to continue" }
                        'x' { $ReturnToOptimization = $true }
                    }
                } while (-not $ReturnToOptimization)
            }
            '2' {
                Write-Banner "PERFORMANCE & GAMING OPTIMIZATION"
                Write-ModulePreview -Items @(
                    "Network & Ping Optimizer - flushes DNS, resets Winsock/IP stack",
                    "Humam Ultimate Power Plan - unlocks hidden High-Performance scheme",
                    "Game Mode & Game Bar - enables Game Mode, disables background recording",
                    "Classic Right-Click Menu - restores the full Windows 10 context menu (Win11 only)"
                )
                New-SystemRestorePoint

                if (Ask-User "Network & Ping Optimizer" "Flushes DNS, resets Winsock and IP stack for lowest latency.") { Invoke-NetworkOptimization }
                if (Ask-User "Activate Humam Ultimate Power Plan" "Unlocks hidden High-Performance plan renamed to your name.") { Enable-UltimatePerformancePowerPlan }
                if (Ask-User "Enable Game Mode & Disable Game Bar" "Optimizes Windows for gaming, kills background recording.") {
    Invoke-Tweak -Tweak ($Script:TweakCatalog | Where-Object { $_.Key -eq "GameMode" }) -State "On"
}
                if (Ask-User "Restore Windows 10 Classic Context Menu" "Brings back the full right-click menu without 'Show more options'. Windows 11 only.") { Enable-ClassicContextMenu }
                Read-Host "   Press Enter to continue"
            }
            'X' { return }
        }
    } while ($true)
}

function Show-MaintenanceRepairMenu {
    do {
        Write-Banner "🔧 MAINTENANCE & REPAIR"
        Write-ModulePreview -Items @(
            "Advanced Repair & Cache Clean: SFC/DISM + temp/update cache cleanup",
            "Disk Cleanup & Optimization: Windows.old, hibernation, drive optimization, cleanmgr",
            "Services Optimizer: safe-to-disable services list"
        )
        Write-Host "   [1]  Advanced Repair & Cache Clean" -ForegroundColor White
        Write-Host "   [2]  Disk Cleanup & Optimization" -ForegroundColor White
        Write-Host "   [3]  Services Optimizer" -ForegroundColor White
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (1-3, X)" -Valid @('1','2','3','x')
        switch ($Choice) {
            '1' {
                Write-Banner "ADVANCED REPAIR & CACHE CLEAN"
                Write-ModulePreview -Items @("SFC + DISM", "Cache Cleanup")
                if (Ask-User "Run SFC + DISM" "Repairs system files.") { Invoke-SystemRepair }
                if (Ask-User "Aggressive Cache Cleanup" "Wipes temp files.") { Clear-SystemCaches }
                Read-Host "   Press Enter to continue"
            }
            '2' {
                Write-Banner "DISK CLEANUP & OPTIMIZATION"
                Write-ModulePreview -Items @("Drive Space Report", "Windows.old", "Hibernation", "TRIM/Defrag", "cleanmgr")
                Show-DiskCleanupModule
                Read-Host "   Press Enter to continue"
            }
            '3' {
                Write-Banner "SERVICES OPTIMIZER"
                Write-ModulePreview -Items @("8 optional services", "Bulk disable", "Info notes")
                Show-ServicesOptimizer
            }
            'X' { return }
        }
    } while ($true)
}

function Show-PrivacySecurityMenu {
    do {
        Write-Banner "🛡️ PRIVACY & SECURITY"
        Write-ModulePreview -Items @(
            "Remove Bloatware - uninstalls $($Script:BloatApps.Count) pre-loaded Store apps",
            "Disable Telemetry & Diagnostics - stops data collection services/tasks",
            "Disable Advertising ID - removes the per-user ad identifier",
            "Disable Activity History Sync - stops Timeline data collection"
        )
        Write-Host "   [1]  Remove Bloatware Packages" -ForegroundColor White
        Write-Host "   [2]  Disable Telemetry & Diagnostics" -ForegroundColor White
        Write-Host "   [3]  Disable Advertising ID" -ForegroundColor White
        Write-Host "   [4]  Disable Activity History Sync" -ForegroundColor White
        Write-Host "   [A]  Apply ALL Privacy Settings (bulk)" -ForegroundColor Magenta
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (1-4, A, X)" -Valid @('1','2','3','4','a','x')
        switch ($Choice) {
            '1' { if (Ask-User "Remove Bloatware" "Uninstalls bloatware.") { Remove-Bloatware }; Read-Host "   Press Enter to continue" }
            '2' { if (Ask-User "Disable Telemetry" "Stops data collection.") { Disable-Telemetry }; Read-Host "   Press Enter to continue" }
            '3' { if (Ask-User "Disable Advertising ID" "Removes ad ID.") { Disable-AdvertisingID }; Read-Host "   Press Enter to continue" }
            '4' { if (Ask-User "Disable Activity History" "Stops timeline.") { Disable-ActivityHistory }; Read-Host "   Press Enter to continue" }
            'A' {
                if (Ask-User "Apply ALL Privacy Settings" "Runs all four privacy actions.") {
                    Remove-Bloatware
                    Disable-Telemetry
                    Disable-AdvertisingID
                    Disable-ActivityHistory
                }
                Read-Host "   Press Enter to continue"
            }
            'X' { return }
        }
    } while ($true)
}

function Show-InformationUtilitiesMenu {
    do {
        Write-Banner "📊 INFORMATION & UTILITIES"
        Write-ModulePreview -Items @(
            "System Info Dashboard: hardware summary, uptime, drive space",
            "Driver Backup: exports current hardware drivers to Desktop",
            "Missing Hardware Drivers Scan: queries Windows Update's driver catalog",
            "Create System Restore Point",
            "View Operation Log"
        )
        Write-Host "   [1]  System Info Dashboard" -ForegroundColor White
        Write-Host "   [2]  Driver Backup" -ForegroundColor White
        Write-Host "   [3]  Missing Hardware Drivers Scan" -ForegroundColor White
        Write-Host "   [4]  Create System Restore Point" -ForegroundColor White
        Write-Host "   [5]  View Operation Log" -ForegroundColor White
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (1-5, X)" -Valid @('1','2','3','4','5','x')
        switch ($Choice) {
            '1' {
                Write-Banner "SYSTEM INFO DASHBOARD"
                Write-ModulePreview -Items @("Read-only snapshot")
                Show-SystemInfoDashboard
            }
            '2' {
                if (Ask-User "Driver Backup" "Exports drivers.") {
                    try {
                        $BackupPath = "$env:USERPROFILE\Desktop\Drivers_Backup_Humam"
                        New-Item -Path $BackupPath -ItemType Directory -Force | Out-Null
                        Export-WindowsDriver -Online -Destination $BackupPath -ErrorAction Stop | Out-Null
                        Write-Success "Backup saved to Desktop\Drivers_Backup_Humam"
                    } catch {
                        Write-Warn "Driver Backup halted."
                    }
                }
                Read-Host "   Press Enter to continue"
            }
            '3' {
                if (Ask-User "Missing Drivers Scan" "Scans Windows Update.") {
                    Write-Info "Querying Windows Update..."
                    try {
                        $UpdateSession  = New-Object -ComObject Microsoft.Update.Session
                        $UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
                        $Missing        = $UpdateSearcher.Search("IsInstalled=0 and Type='Driver'")
                        if ($Missing.Updates.Count -gt 0) {
                            Write-Warn "Found $($Missing.Updates.Count) missing driver(s)."
                            if (Ask-User "Open Settings" "Open Windows Update settings.") {
                                Start-Process "ms-settings:windowsupdate-action"
                            }
                        } else { Write-Success "No missing drivers." }
                    } catch { Write-ErrorX "Driver scan failed." }
                }
                Read-Host "   Press Enter to continue"
            }
            '4' {
                Write-Banner "SYSTEM RESTORE POINT CREATOR"
                Write-ModulePreview -Items @("Creates manual restore point")
                Write-Info "Creating restore point..."
                New-SystemRestorePoint
                Read-Host "   Press Enter to continue"
            }
            '5' {
                if (Test-Path $Script:LogPath) {
                    Write-Info "Opening log file: $Script:LogPath"
                    Start-Process notepad.exe $Script:LogPath
                } else {
                    Write-Warn "No log file found."
                }
                Read-Host "   Press Enter to continue"
            }
            'X' { return }
        }
    } while ($true)
}

# ============================================================
#  MAIN MENU
# ============================================================
function Show-MainMenu {
    Write-Banner "HUMAM TAIBEH'S CORE ARCHITECTURE" "Ultimate Windows Optimization  |  v$Script:ScriptVersion"
    Write-Host "   [1] 📦 Software Management" -ForegroundColor White
    Write-Host "   [2] ⚡ System Optimization" -ForegroundColor White
    Write-Host "   [3] 🔧 Maintenance & Repair" -ForegroundColor White
    Write-Host "   [4] 🛡️ Privacy & Security" -ForegroundColor White
    Write-Host "   [5] 📊 Information & Utilities" -ForegroundColor White
    Write-Host "   [6] 🛟 Safety & Recovery" -ForegroundColor White
    Write-Host "   [0] 🚪 Exit" -ForegroundColor DarkGray
    Write-Divider
    if ($Script:SessionSuccessCount -gt 0 -or $Script:SessionFailCount -gt 0) {
        Write-Host "   📈 Session so far: $Script:SessionSuccessCount succeeded / $Script:SessionFailCount failed" -ForegroundColor DarkGray
        Write-Divider
    }
    if ($Script:PendingRestart) {
        Write-Host "   ⚠  A restart is pending from an earlier operation. Choose [0] Exit to be offered a restart." -ForegroundColor Yellow
        Write-Divider
    }
}

function Show-RestartReminder {
    if ($Script:PendingRestart) {
        Write-Host ""
        Write-Warn "One or more operations require a system restart to complete."
        if (Ask-User "Restart Now" "Reboots the machine immediately so all pending changes take effect.") {
            Write-Info "Restarting in 5 seconds... Press Ctrl+C to abort."
            Start-Sleep -Seconds 5
            Restart-Computer -Force
        }
    }
}

Show-EpicIntro

do {
    Show-MainMenu
    $Selection = Read-Choice -Prompt "   Select Module [0-6]" -Valid @('0','1','2','3','4','5','6')

    switch ($Selection) {
        "1" { Show-SoftwareManagementMenu }
        "2" { Show-SystemOptimizationMenu }
        "3" { Show-MaintenanceRepairMenu }
        "4" { Show-PrivacySecurityMenu }
        "5" { Show-InformationUtilitiesMenu }
        "6" { Show-SafetyRecoveryMenu }
        "0" {
            if (Ask-User "Exit H.T. Core Architecture" "Closes the tool. Any pending restart will still be offered first.") {
                Write-Host ""
                Write-Host "   📊 Session Summary: $($Script:SessionSuccessCount) successes, $($Script:SessionFailCount) failures." -ForegroundColor Cyan
                Show-RestartReminder
                Write-Host "   Thank you for using H.T. CORE ARCHITECTURE, Humam!" -ForegroundColor Yellow
                Write-Host "   Exiting in 3 seconds..." -ForegroundColor DarkGray
                Start-Sleep -Seconds 3
                Exit
            }
        }
        default {
            Write-Warn "Invalid selection."
            Start-Sleep -Seconds 1
        }
    }
} while ($true)