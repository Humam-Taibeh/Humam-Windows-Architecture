#Requires -Version 5.1
<#
.SYNOPSIS
    06-Tweaks.ps1 - the data-driven tweak engine plus every system tweak
    and optimization that mutates registry / power / network state.

.DESCRIPTION
    - Invoke-Tweak consumes $Script:TweakCatalog entries (01-Catalogs.ps1):
      a tweak is DATA (registry entries with On/Off values), never a bespoke
      function. Adding a tweak = adding a catalog entry.
    - Every change snapshots the original value first (02-Safety.ps1) and
      creates the once-per-session restore point.
    - All mutations flow through the dry-run primitives, so -WhatIf walks
      the exact same code paths and reports every write it would perform.
#>

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
            Set-RegValue -Path $E.Path -Name $E.Name -Value $Value -Type $E.Type
        }
        Write-Success "$($Tweak.Key) applied successfully."
    } | Out-Null
}

# ============================================================
#  WINDOWS 11 CLASSIC CONTEXT MENU
# ============================================================
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

    try {
        Set-RegValue -Path $path -Name "(default)" -Value "" -Type String
        Write-Success "Classic context menu restored."

        if (Ask-User "Restart Windows Explorer" "Applies the classic menu immediately by restarting explorer.exe.") {
            Invoke-Mutation -Description "Restart explorer.exe to apply the classic context menu" -Action {
                Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 1
                Start-Process explorer
                Write-Success "Explorer restarted. Classic menu should now be active."
            } | Out-Null
        } else {
            Write-Info "Change will take effect after you sign out or restart Explorer manually."
        }
    } catch {
        Write-ErrorX "Failed to restore classic context menu: $($_.Exception.Message)"
    }
}

# ============================================================
#  SMART SYSTEM TWEAKS
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
        Set-RegValue -Path $Path -Name "MouseSpeed" -Value "0"
        Set-RegValue -Path $Path -Name "MouseThreshold1" -Value "0"
        Set-RegValue -Path $Path -Name "MouseThreshold2" -Value "0"
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
        Set-RegValue -Path $Path -Name "TaskbarAl" -Value 0
        Set-RegValue -Path $Path -Name "TaskbarDa" -Value 0
        Set-RegValue -Path $Path -Name "TaskbarMn" -Value 0
        Write-Success "Taskbar alignments updated."
    } catch {
        Write-Warn "Skipped: Windows 11 taskbar mutation blocked."
    }
}

# ============================================================
#  ONEDRIVE REMOVAL
# ============================================================
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
        Invoke-Mutation -Description "Terminate OneDrive.exe" -Action {
            Stop-Process -Name "OneDrive" -Force -ErrorAction SilentlyContinue
        } | Out-Null
        if (Test-Path $ODSetup) {
            if (Test-DryRun "Run OneDriveSetup.exe /uninstall") { return }
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
#  MICROSOFT EDGE REMOVAL / REINSTALL
# ============================================================
function Remove-MicrosoftEdge {
    Write-SectionHeader "Remove Microsoft Edge"
    New-SystemRestorePoint
    $EdgeUninstaller = "$env:ProgramFiles\Microsoft\Edge\Application\*\Installer\setup.exe"
    $UninstallPath = Get-ChildItem -Path $EdgeUninstaller -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($UninstallPath) {
        Backup-EdgeState
        if (Test-DryRun "Run Edge setup.exe --uninstall --force-uninstall --system-level") { return }
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
    if (Ensure-Winget) {
        Write-Info "Installing Microsoft Edge via winget..."
        $Result = Smart-Deploy "Microsoft.Edge" "Microsoft Edge"
        if ($Result.Status -eq 'Success') {
            Restore-EdgeState
        }
    } elseif ($Script:DryRun) {
        Write-Info "[WHATIF] Would install Microsoft Edge via winget and restore backed-up settings."
    } else {
        Write-Warn "Winget unavailable. Opening official download page for a manual install..."
        Write-Info "Manual install steps: download the installer from the page that opens, run it, then use this menu's [6] Reinstall Edge option again if you want your backed-up settings restored."
        Open-UrlSafe -Url "https://www.microsoft.com/en-us/edge/download"
    }
}

# ============================================================
#  BACKWARD-COMPATIBILITY STUB
#  "Restore Windows Default Settings" lives in 02-Safety.ps1 as
#  Reset-AllTweaksToDefaults (restores YOUR original captured values).
#  This stub keeps the old name working for anything that calls it.
# ============================================================
function Reset-WindowsDefaultSettings {
    Reset-AllTweaksToDefaults
}

# ============================================================
#  PERFORMANCE & GAMING OPTIMIZATION
# ============================================================
function Invoke-NetworkOptimization {
    Write-SectionHeader "Network & Ping Optimizer"
    New-SystemRestorePoint
    if (Test-DryRun "Flush DNS, release/renew IP, reset Winsock and the IP stack") { return }
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
    if (Test-DryRun "Duplicate the hidden Ultimate Performance scheme, rename it 'Humam Ultimate Power Plan' and set it active") { return }
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
