#Requires -Version 5.1
<#
.SYNOPSIS
    30-GuiDispatcher.ps1 - the PySide6 frontend's task engine.

.DESCRIPTION
    CONTRACT (unchanged since v3.3 - the GUI's thread-safety logic in
    src/utils/helpers.py depends on it):
      - The frontend runs `core.ps1 -Task <name> [-AppIds a,b,c] [-WhatIf]`.
      - Every `task` in src/frontend/menu_structure.py maps 1:1 to one
        `switch ($TaskName)` case in Invoke-GuiTask below.
      - Invoke-GuiTask emits EXACTLY ONE final line on stdout:
            SUCCESS|Human readable message
            ERROR|Human readable message
        Silence is the one failure mode we never allow: any unanticipated
        exception is converted to an ERROR| line by the safety net.
      - $Script:NonInteractive is $true for the whole run, so nothing below
        this layer ever blocks on Read-Host or pops UI.

    Under -WhatIf, successful mutating tasks report with a "[DRY-RUN]"
    prefix so the GUI/user can tell simulation from execution.
#>

# --------------------------------------------------------
#  DISPATCHER SUPPORT STATE (computed at load; cheap)
# --------------------------------------------------------
$Script:IsAdminSession = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# GUI checkbox multi-selector sends the chosen AppIds as a comma-separated
# list. Empty/absent means "no selection narrowing" - deploy the full
# category (see Invoke-GuiBulkDeploy's $SelectedIds contract).
$Script:SelectedAppIds = @()
if ($AppIds) {
    $Script:SelectedAppIds = @($AppIds -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

# --------------------------------------------------------
#  RESULT HELPERS
# --------------------------------------------------------
function Complete-GuiTask {
    <# Runs $Action and emits the final contract line. Failure detection
       uses the session fail counter: every Write-ErrorX bumps it, so
       functions that swallow their own exceptions still report honestly.
       In -WhatIf mode a clean pass is reported as "[DRY-RUN]". #>
    param(
        [Parameter(Mandatory)][scriptblock]$Action,
        [Parameter(Mandatory)][string]$SuccessMessage,
        [Parameter(Mandatory)][string]$FailureMessage
    )
    $failsBefore = $Script:SessionFailCount
    & $Action | Out-Null
    if ($Script:SessionFailCount -gt $failsBefore) {
        Write-Output "ERROR|$FailureMessage See Desktop\HTCoreArchitecture_Log.txt for details."
    } elseif ($Script:DryRun) {
        Write-Output "SUCCESS|[DRY-RUN] $SuccessMessage (simulated - no changes were made)"
    } else {
        Write-Output "SUCCESS|$SuccessMessage"
    }
}

function Invoke-GuiBulkDeploy {
    <# Silent bulk winget deployment for one app category, plus an
       optional hardware-matched extra app (GPU / motherboard suite).
       $SelectedIds: when non-empty, only $AppList entries whose AppId
       is in this set are queued - this is how the GUI's checkbox
       multi-selector overlay narrows a category down to exactly the
       apps the user ticked. Empty means "deploy the whole category"
       (back-compat with any caller that doesn't pass a selection). #>
    param($AppList, [string]$CategoryName, [string]$ExtraAppId = "", [string]$ExtraAppName = "", [string[]]$SelectedIds = @())
    if (-not $Script:DryRun) {
        if (-not (Ensure-Winget)) {
            Write-Output "ERROR|winget is unavailable and could not be bootstrapped. Install 'App Installer' from the Microsoft Store, then retry."
            return
        }
    }
    $ok = 0; $failed = 0; $skipped = 0
    $Queue = @()
    foreach ($App in $AppList) {
        if ($SelectedIds.Count -gt 0 -and -not ($SelectedIds -contains $App[0])) { continue }
        $Queue += ,@($App[0], $App[1])
    }
    if ($ExtraAppId) { $Queue += ,@($ExtraAppId, $ExtraAppName) }

    if ($Queue.Count -eq 0) {
        Write-Output "ERROR|No applications were selected for $CategoryName."
        return
    }

    foreach ($App in $Queue) {
        $res = Smart-Deploy -AppId $App[0] -AppName $App[1] -Bulk -BulkMethod 'auto'
        switch ($res.Status) {
            'Success' { $ok++ }
            'Failed'  { $failed++ }
            default   { $skipped++ }
        }
    }
    $Prefix = if ($Script:DryRun) { "[DRY-RUN] " } else { "" }
    if ($failed -eq 0) {
        Write-Output "SUCCESS|$Prefix$CategoryName — $ok installed or already current, $skipped skipped."
    } else {
        Write-Output "ERROR|$CategoryName — $failed failed, $ok succeeded, $skipped skipped. See Desktop\HTCoreArchitecture_Log.txt."
    }
}

function Get-TweakByKey {
    param([string]$Key)
    return ($Script:TweakCatalog | Where-Object { $_.Key -eq $Key })
}

# --------------------------------------------------------
#  TASK DISPATCHER — one case per menu_structure.py task ID.
#  CONTRACT: exactly one final "SUCCESS|..." or "ERROR|..." line.
# --------------------------------------------------------
function Invoke-GuiTask {
    param([string]$TaskName)
    try {
        if (($Script:AdminRequiredTasks -contains $TaskName) -and -not $Script:IsAdminSession) {
            Write-Output "ERROR|'$TaskName' needs Administrator rights. Close the app and choose 'Run as administrator'."
            return
        }

        switch ($TaskName) {

            # ============ 1. SOFTWARE MANAGEMENT ============
            "InstallEssentialApps"   { Invoke-GuiBulkDeploy $Apps_Basic "Essential Apps" -SelectedIds $Script:SelectedAppIds; break }
            "InstallDevApps"         { Invoke-GuiBulkDeploy $Apps_Dev "Programming & AI Core" -SelectedIds $Script:SelectedAppIds; break }
            "InstallGamingApps" {
                $HW = Hardware-Check
                if ($HW.GPUApp) {
                    Invoke-GuiBulkDeploy $Apps_Gaming "Gaming Launchers" -ExtraAppId $HW.GPUApp -ExtraAppName "GPU Software ($($HW.GPUName))" -SelectedIds $Script:SelectedAppIds
                } else {
                    Invoke-GuiBulkDeploy $Apps_Gaming "Gaming Launchers" -SelectedIds $Script:SelectedAppIds
                }
                break
            }
            "InstallDiagnosticApps" {
                $HW = Hardware-Check
                if ($HW.MoboApp) {
                    Invoke-GuiBulkDeploy $Apps_Tools "Hardware Diagnostics" -ExtraAppId $HW.MoboApp -ExtraAppName "Motherboard Suite ($($HW.MoboName))" -SelectedIds $Script:SelectedAppIds
                } else {
                    Invoke-GuiBulkDeploy $Apps_Tools "Hardware Diagnostics" -SelectedIds $Script:SelectedAppIds
                }
                break
            }
            "InstallRuntimes"        { Invoke-GuiBulkDeploy $Runtimes "Core API Runtimes" -SelectedIds $Script:SelectedAppIds; break }
            "StartupReport" {
                $Items = @(Get-AllStartupItems)
                $Enabled  = @($Items | Where-Object { $_.Enabled }).Count
                $Disabled = @($Items | Where-Object { -not $_.Enabled }).Count
                foreach ($It in $Items) {
                    $State = if ($It.Enabled) { "ENABLED " } else { "DISABLED" }
                    Write-Log ("STARTUP [{0}] ({1}) {2} -> {3}" -f $State, $It.Type, $It.Name, $It.Command)
                }
                Write-Output "SUCCESS|Startup audit: $Enabled enabled, $Disabled disabled item(s). Full list saved to the operation log."
                break
            }
            "VerifyEnvironment" {
                $Report = Verify-Environment
                $MissingTxt = ""
                if ($Report.MissingCount -gt 0) {
                    $MissingTxt = " Missing: $($Report.MissingNames -join ', ') (winget ids are in the log)."
                }
                $Prefix = if ($Script:DryRun) { "[DRY-RUN] " } else { "" }
                Write-Output "SUCCESS|${Prefix}Dev environment verified: $($Report.OkCount) tool(s) OK, $($Report.RepairedCount) PATH/env repair(s), $($Report.MissingCount) missing.$MissingTxt"
                break
            }

            # ============ 2. SYSTEM OPTIMIZATION ============
            "DarkMode" {
                Complete-GuiTask -Action { Invoke-Tweak -Tweak (Get-TweakByKey "DarkMode") -State "On" } `
                    -SuccessMessage "Dark Mode enforced across Windows and apps." `
                    -FailureMessage "Dark Mode could not be fully applied."
                break
            }
            "DisableMouseAccel" {
                Complete-GuiTask -Action { Disable-MouseAcceleration } `
                    -SuccessMessage "Mouse acceleration disabled — raw pointer precision active." `
                    -FailureMessage "Mouse acceleration settings could not be changed."
                break
            }
            "MinimalistTaskbar" {
                if (-not $Script:IsWin11) { Write-Output "ERROR|Minimalist Taskbar requires Windows 11 (detected build $Script:OSBuild)."; break }
                Complete-GuiTask -Action { Enable-MinimalistTaskbar } `
                    -SuccessMessage "Minimalist taskbar applied: left-aligned, widgets and chat removed." `
                    -FailureMessage "Taskbar layout could not be changed."
                break
            }
            "ClassicContextMenu" {
                if (-not $Script:IsWin11) { Write-Output "ERROR|Classic Context Menu requires Windows 11 (detected build $Script:OSBuild)."; break }
                Complete-GuiTask -Action { Enable-ClassicContextMenu } `
                    -SuccessMessage "Classic right-click menu restored (Explorer was restarted to apply it)." `
                    -FailureMessage "Classic context menu could not be restored."
                break
            }
            "GameMode" {
                Complete-GuiTask -Action { Invoke-Tweak -Tweak (Get-TweakByKey "GameMode") -State "On" } `
                    -SuccessMessage "Game Mode enabled and background Game DVR recording disabled." `
                    -FailureMessage "Game Mode settings could not be applied."
                break
            }
            "NetworkOptimization" {
                Complete-GuiTask -Action { Invoke-NetworkOptimization } `
                    -SuccessMessage "Network stack reset and DNS flushed. A restart is recommended." `
                    -FailureMessage "Network optimization did not complete."
                break
            }
            "UltimatePowerPlan" {
                Complete-GuiTask -Action { Enable-UltimatePerformancePowerPlan } `
                    -SuccessMessage "Humam Ultimate Power Plan is now the active power scheme." `
                    -FailureMessage "The Ultimate Power Plan could not be activated."
                break
            }
            "RemoveOneDrive" {
                Complete-GuiTask -Action { Remove-OneDrivePackage } `
                    -SuccessMessage "OneDrive removed. Local files were backed up to Desktop\HTCore_OneDriveBackup first." `
                    -FailureMessage "OneDrive removal encountered errors."
                break
            }
            "RemoveEdge" {
                if ($Script:DryRun) {
                    Remove-MicrosoftEdge
                    Write-Output "SUCCESS|[DRY-RUN] Edge removal simulated (backup + uninstall were reported, not executed)."
                    break
                }
                Remove-MicrosoftEdge
                $EdgeStillThere = @(Get-ChildItem -Path "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe" -ErrorAction SilentlyContinue).Count -gt 0
                if ($EdgeStillThere) {
                    Write-Output "ERROR|Windows protected Edge from removal on this build (it is an OS component here). A backup of its settings was still saved."
                } else {
                    Write-Output "SUCCESS|Microsoft Edge uninstalled. Settings backup saved to Desktop\HTCore_EdgeBackup. Restart recommended."
                }
                break
            }
            "ReinstallEdge" {
                if (-not $Script:DryRun -and -not (Ensure-Winget)) { Write-Output "ERROR|winget is unavailable, so Edge cannot be reinstalled automatically. Install 'App Installer' from the Microsoft Store first."; break }
                Complete-GuiTask -Action { Install-MicrosoftEdge } `
                    -SuccessMessage "Microsoft Edge reinstalled via winget; backed-up settings restored where available." `
                    -FailureMessage "Edge reinstallation did not complete."
                break
            }

            # ============ 3. MAINTENANCE & REPAIR ============
            "RunSFC" {
                $RepairOk = Invoke-SystemRepair
                if (-not $RepairOk) {
                    Write-Output "ERROR|SFC/DISM finished with errors. See Desktop\HTCoreArchitecture_Log.txt and C:\Windows\Logs\CBS\CBS.log."
                } elseif ($Script:DryRun) {
                    Write-Output "SUCCESS|[DRY-RUN] SFC and DISM repair simulated (nothing was scanned or repaired)."
                } else {
                    Write-Output "SUCCESS|SFC and DISM repair completed — system files verified healthy."
                }
                break
            }
            "CleanCache" {
                Complete-GuiTask -Action { Clear-SystemCaches } `
                    -SuccessMessage "Caches cleaned: temp files, Prefetch, Windows Update cache and Recycle Bin." `
                    -FailureMessage "Cache cleanup ran into locked or protected files."
                break
            }
            "OptimizeDrives" {
                Complete-GuiTask -Action { Optimize-AllDrives } `
                    -SuccessMessage "All fixed drives optimized (TRIM for SSDs, defrag for HDDs)." `
                    -FailureMessage "One or more drives could not be optimized."
                break
            }
            "RemoveWindowsOld" {
                if (-not (Test-Path "$env:SystemDrive\Windows.old")) {
                    Write-Output "SUCCESS|No Windows.old folder present — nothing to reclaim."
                    break
                }
                Complete-GuiTask -Action { Remove-WindowsOldFolder } `
                    -SuccessMessage "Windows.old removed — disk space reclaimed." `
                    -FailureMessage "Windows.old could not be fully removed (try Disk Cleanup's 'Previous Windows installations')."
                break
            }
            "DisableHibernation" {
                Complete-GuiTask -Action { Set-HibernationState -Enable $false } `
                    -SuccessMessage "Hibernation disabled — hiberfil.sys removed, disk space freed." `
                    -FailureMessage "Hibernation state could not be changed."
                break
            }
            "EnableHibernation" {
                Complete-GuiTask -Action { Set-HibernationState -Enable $true } `
                    -SuccessMessage "Hibernation enabled." `
                    -FailureMessage "Hibernation state could not be changed."
                break
            }
            "DriveSpaceReport" {
                $Drives = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
                          Where-Object { $null -ne $_.Used -and $null -ne $_.Free -and (($_.Used + $_.Free) -gt 0) }
                $Parts = @()
                foreach ($D in $Drives) {
                    $FreeGB  = [math]::Round($D.Free / 1GB, 1)
                    $TotalGB = [math]::Round(($D.Used + $D.Free) / 1GB, 1)
                    $Line = "{0}: {1} GB free of {2} GB" -f $D.Name, $FreeGB, $TotalGB
                    $Parts += $Line
                    Write-Log "DRIVE $Line"
                }
                if ($Parts.Count -eq 0) { Write-Output "ERROR|No fixed drives could be read." }
                else { Write-Output "SUCCESS|$($Parts -join '   ·   ')" }
                break
            }

            # ============ 4. PRIVACY & SECURITY ============
            "RemoveBloatware" {
                Complete-GuiTask -Action { Remove-Bloatware } `
                    -SuccessMessage "Bloatware sweep complete — pre-loaded Store apps removed." `
                    -FailureMessage "Some bloatware packages could not be removed (policy-protected)."
                break
            }
            "DisableTelemetry" {
                Complete-GuiTask -Action { Disable-Telemetry } `
                    -SuccessMessage "Telemetry services, policies and scheduled diagnostics disabled." `
                    -FailureMessage "Telemetry hardening encountered an issue."
                break
            }
            "DisableAdvertisingID" {
                Complete-GuiTask -Action { Disable-AdvertisingID } `
                    -SuccessMessage "Advertising ID disabled — ad networks lose their per-user identifier." `
                    -FailureMessage "The Advertising ID setting could not be changed."
                break
            }
            "DisableActivityHistory" {
                Complete-GuiTask -Action { Disable-ActivityHistory } `
                    -SuccessMessage "Activity History sync to Microsoft disabled." `
                    -FailureMessage "Activity History settings could not be changed."
                break
            }
            "ApplyAllPrivacy" {
                Complete-GuiTask -Action {
                    Remove-Bloatware
                    Disable-Telemetry
                    Disable-AdvertisingID
                    Disable-ActivityHistory
                } `
                    -SuccessMessage "Full privacy pass applied: bloatware, telemetry, advertising ID and activity history." `
                    -FailureMessage "The privacy pass finished with some failures."
                break
            }

            # ============ 5. INFORMATION & UTILITIES ============
            "SystemInfo" {
                $Info = Get-SystemInfoSnapshot
                $Up = if ($Info.Uptime) { "{0}d {1}h {2}m" -f $Info.Uptime.Days, $Info.Uptime.Hours, $Info.Uptime.Minutes } else { "n/a" }
                $Msg = "$($Info.OSCaption) (Build $($Info.OSBuild)) · $($Info.CPUName) · RAM $($Info.FreeRAMGB)/$($Info.TotalRAMGB) GB free · Uptime $Up · Plan: $($Info.PowerPlan)"
                Write-Log "SYSTEMINFO $Msg"
                foreach ($GPU in @($Info.GPUs)) { Write-Log "SYSTEMINFO GPU: $GPU" }
                Write-Output "SUCCESS|$Msg"
                break
            }
            "DriverBackup" {
                if ($Script:DryRun) {
                    Write-Output "SUCCESS|[DRY-RUN] Would export all third-party driver packages to Desktop\Drivers_Backup_Humam."
                    break
                }
                $BackupPath = "$env:USERPROFILE\Desktop\Drivers_Backup_Humam"
                New-Item -Path $BackupPath -ItemType Directory -Force | Out-Null
                $Exported = Export-WindowsDriver -Online -Destination $BackupPath -ErrorAction Stop
                Write-Output "SUCCESS|$(@($Exported).Count) driver package(s) exported to Desktop\Drivers_Backup_Humam."
                break
            }
            "DriverScan" {
                $UpdateSession  = New-Object -ComObject Microsoft.Update.Session
                $UpdateSearcher = $UpdateSession.CreateUpdateSearcher()
                $Missing        = $UpdateSearcher.Search("IsInstalled=0 and Type='Driver'")
                if ($Missing.Updates.Count -gt 0) {
                    foreach ($U in $Missing.Updates) { Write-Log "MISSING-DRIVER: $($U.Title)" }
                    Write-Output "SUCCESS|Found $($Missing.Updates.Count) missing driver(s) — install them via Settings > Windows Update > Optional updates. Names are in the log."
                } else {
                    Write-Output "SUCCESS|No missing drivers — every device is covered by Windows Update."
                }
                break
            }
            "CreateRestorePoint" {
                if ($Script:DryRun) {
                    New-SystemRestorePoint
                    Write-Output "SUCCESS|[DRY-RUN] Restore point creation simulated."
                    break
                }
                New-SystemRestorePoint
                if ($Script:RestorePointCreated) {
                    Write-Output "SUCCESS|Restore point 'Pre-Humam Setup Blueprint' created."
                } else {
                    Write-Output "ERROR|Restore point could not be created — System Restore may be disabled or throttled on this machine."
                }
                break
            }

            # ============ 6. SAFETY & RECOVERY ============
            "ResetTweaks" {
                Complete-GuiTask -Action { Reset-AllTweaksToDefaults } `
                    -SuccessMessage "All tweaks reverted to your original values (or Windows defaults). A sign-out or restart is recommended." `
                    -FailureMessage "Some tweaks could not be reverted."
                break
            }
            "RestoreServices" {
                $Count = 0
                if (Test-Path $Script:ServicesBackupRegPath) {
                    $Props = Get-ItemProperty -Path $Script:ServicesBackupRegPath -ErrorAction SilentlyContinue
                    if ($Props) {
                        foreach ($Prop in $Props.PSObject.Properties) {
                            if ($Prop.Name -notmatch '^PS(Path|ParentPath|ChildName|Provider)$') { $Count++ }
                        }
                    }
                }
                if ($Count -eq 0) {
                    Write-Output "SUCCESS|No service changes have been recorded by this tool — nothing to restore."
                    break
                }
                Complete-GuiTask -Action { Restore-AllServicesToPreviousState } `
                    -SuccessMessage "$Count service(s) restored to their original startup configuration." `
                    -FailureMessage "Some services could not be restored."
                break
            }
            "RestoreEdge" {
                if (-not $Script:DryRun -and -not (Ensure-Winget)) { Write-Output "ERROR|winget is unavailable, so Edge cannot be reinstalled automatically. Install 'App Installer' from the Microsoft Store first."; break }
                Complete-GuiTask -Action { Install-MicrosoftEdge } `
                    -SuccessMessage "Microsoft Edge reinstated; backed-up settings restored where available." `
                    -FailureMessage "Edge restoration did not complete."
                break
            }

            default {
                Write-Output "ERROR|Unknown task: $TaskName"
            }
        }
    } catch {
        # Safety net: any unanticipated exception still produces a clean
        # contract line - silence is the one failure mode we never allow.
        Write-Log "GUI-TASK EXCEPTION in '$TaskName': $($_.Exception.Message)"
        Write-Output "ERROR|$($_.Exception.Message)"
    }
}
