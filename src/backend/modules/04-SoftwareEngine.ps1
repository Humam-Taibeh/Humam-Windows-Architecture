#Requires -Version 5.1
<#
.SYNOPSIS
    04-SoftwareEngine.ps1 - the winget/Chocolatey deployment engine.

.DESCRIPTION
    Smart-Deploy is the single entry point for installing/upgrading any app.
    It is fed exclusively by the catalogs in 01-Catalogs.ps1 (data-driven:
    no per-app functions), and it honors three global modes:
      - $Script:NonInteractive : GUI task mode - never prompts, never pops
        browsers or the Microsoft Store mid-silent-run.
      - $Script:DryRun (-WhatIf): reports what WOULD be installed/upgraded
        and returns Status='Success' without touching the system.
      - Bulk/BulkMethod: category-wide auto or manual handling.

    Also: version probing, store-app detection, hardware matching (GPU /
    motherboard vendor apps) and the interactive category processor.
#>

# ============================================================
#  STORE APP DETECTION
# ============================================================
function Is-StoreApp {
    param([string]$AppId)
    return $AppId -match '^\w{12}$'
}

# ============================================================
#  INSTALLED / LATEST VERSION DETECTION
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

    if (-not $global:WingetAvailable) { return $null }  # no winget -> no probe

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

function Get-LatestVersion {
    param([string]$AppId)
    if (Is-StoreApp $AppId) { return "Store" }
    if (-not $global:WingetAvailable) { return "Unknown" }  # no winget -> no probe
    $Lines = & winget show --id $AppId --exact --accept-source-agreements --disable-interactivity 2>$null
    if (-not $Lines) { return "Unknown" }
    foreach ($Line in $Lines) {
        if ($Line -match '^\s*Version:\s*(\S+)') { return $Matches[1] }
    }
    return "Unknown"
}

# ============================================================
#  WINGET / CHOCOLATEY EXECUTION
# ============================================================
function Stop-LockingProcesses {
    param($AppId)
    if ($Script:LockProcessMap.ContainsKey($AppId)) {
        foreach ($ProcName in $Script:LockProcessMap[$AppId]) {
            $Proc = Get-Process -Name $ProcName -ErrorAction SilentlyContinue
            if ($Proc) {
                Invoke-Mutation -Description "Terminate background process '$ProcName' (locks the $AppId installer)" -Action {
                    Write-Warn "Terminating background process '$ProcName'..."
                    Stop-Process -Name $ProcName -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Milliseconds 800
                } | Out-Null
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
    if ($Script:NonInteractive -or $Script:DryRun) {
        # GUI task / dry-run: NEVER pop a browser mid-silent-run. Log the
        # link so the user can find it in the operation log instead.
        if ($url) { Write-Log "FALLBACK-URL for ${AppName}: $url" }
        else      { Write-Log "FALLBACK-URL for ${AppName}: no official URL mapped." }
        return
    }
    if ($url) {
        Write-Info "Opening official download page: $url"
        Start-Process $url
    } else {
        Write-Info "No official URL mapped. Opening search..."
        Start-Process "https://www.google.com/search?q=$AppName download"
    }
}

# ============================================================
#  LOCAL INSTALLER RUNNER (Path C of the generic Tool Install Wizard)
# ============================================================
function Invoke-GuiLocalInstall {
    <#
    .SYNOPSIS
        Runs an installer file the user already downloaded and picked
        through widgets.ToolInstallWizardDialog's Path C (task
        InstallLocalFile). Generic by design - unlike Office's ODT flow,
        "run this installer the user pointed at" needs no tool-specific
        knowledge: .msi goes through msiexec /i, everything else runs
        directly. Most installers self-elevate via their own manifest if
        they need to (Windows shows that UAC prompt regardless of this
        hidden/no-window parent process), so this never forces elevation
        itself - exactly like a user double-clicking the file manually.
    #>
    param([Parameter(Mandatory = $true)][string]$FilePath)

    if (-not (Test-Path -Path $FilePath -PathType Leaf)) {
        Write-ErrorX "Installer file not found: $FilePath"
        return $false
    }

    if (Test-DryRun "Run local installer '$FilePath'") { return $true }

    Write-Info "Running installer: $FilePath"
    try {
        $Ext = [System.IO.Path]::GetExtension($FilePath).ToLowerInvariant()
        if ($Ext -eq ".msi") {
            $Proc = Start-Process -FilePath "msiexec.exe" -ArgumentList @("/i", ('"' + $FilePath + '"')) -Wait -PassThru
        } else {
            $Proc = Start-Process -FilePath $FilePath -Wait -PassThru
        }
        if ($Proc.ExitCode -eq 0 -or $Proc.ExitCode -eq 3010) {
            Write-Success "Installer finished (exit code $($Proc.ExitCode))."
            return $true
        } else {
            Write-ErrorX "Installer exited with code $($Proc.ExitCode)."
            return $false
        }
    } catch {
        Write-ErrorX "Could not run the installer: $($_.Exception.Message)"
        return $false
    }
}

# ============================================================
#  SMART DEPLOY (the one true install path)
# ============================================================
function Smart-Deploy {
    param(
        [string]$AppId,
        [string]$AppName,
        [switch]$Bulk,
        [ValidateSet('auto','manual')]
        [string]$BulkMethod
    )

    if ([string]::IsNullOrWhiteSpace($AppId)) { return @{Status='Skipped'; Message='Empty AppId'} }

    # Lazy winget bootstrap: only software deployment pays for it. Skipped in
    # dry-run - Ensure-Winget itself refuses to download during -WhatIf.
    if (-not (Is-StoreApp $AppId)) { Ensure-Winget | Out-Null }

    if (Is-StoreApp $AppId) {
        Write-Host ""
        Write-StatusPanel -Label "STORE APP" -Text $AppName

        $InstalledVer = Get-InstalledVersion -AppId $AppId -AppName $AppName
        if ($InstalledVer) {
            Write-Success "$AppName is already installed (version $InstalledVer)."
            return @{Status='Success'; Message='Already installed'}
        }

        if ($Script:DryRun) {
            Write-Info "[WHATIF] $AppName is a Microsoft Store app - a real run would require the Store (skipped)."
            return @{Status='Skipped'; Message='Store app (dry-run)'}
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

        if ($Script:NonInteractive) {
            # GUI task: no console to prompt on and no silent install path
            # for Store apps - skip cleanly instead of hanging on Read-Choice.
            Write-Warn "$AppName is a Microsoft Store app - skipped in GUI mode."
            return @{Status='Skipped'; Message='Store app (GUI)'}
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

    # -WhatIf: report the exact action a real run would take, then stop.
    if ($Script:DryRun) {
        $Verb = if ($CurrentVersion) { "upgrade" } else { "install" }
        if (Test-DryRun "winget $Verb --id $AppId ($AppName), silent, with agreements accepted") { }
        return @{Status='Success'; Message='Dry-run (no change)'}
    }

    if ($Bulk) {
        if ($BulkMethod -eq 'manual') {
            Open-FallbackUrl $AppId $AppName
            return @{Status='Success'; Message='Manual URL (bulk)'}
        }
    } elseif ($Script:NonInteractive) {
        # GUI task: the card click IS the confirmation - fall through to
        # the silent winget deployment without prompting.
        Write-Info "GUI mode: proceeding with silent winget deployment."
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
        if ($Result.Message -notmatch '^Already') {
            Test-DevDependencySuggestion -AppId $AppId
        }
        return @{Status='Success'; Message=$Result.Message}
    } else {
        Write-ErrorX "$AppName failed: $($Result.Message)"
        if (-not $Bulk -and -not $Script:NonInteractive) {
            $openFallback = Read-Choice -Prompt "   Auto install failed. Open official website? (y/n)" -Valid @('y','n')
            if ($openFallback -eq 'y') { Open-FallbackUrl $AppId $AppName }
        } else {
            # Bulk/GUI: Open-FallbackUrl is itself NonInteractive-aware
            # (logs the URL instead of opening a browser in GUI mode).
            Open-FallbackUrl $AppId $AppName
        }
        return @{Status='Failed'; Message=$Result.Message}
    }
}

# ============================================================
#  HARDWARE MATCHING (GPU / motherboard vendor apps)
# ============================================================
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
#  CATEGORY PROCESSOR (interactive console flow)
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
