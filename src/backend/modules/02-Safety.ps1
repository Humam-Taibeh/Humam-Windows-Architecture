#Requires -Version 5.1
<#
.SYNOPSIS
    02-Safety.ps1 - the bulletproof safety net (snapshots, backups, rollback).

.DESCRIPTION
    Everything that makes the tool reversible lives here:
      - New-SystemRestorePoint: one restore point per session, created before
        the first registry/service/system change in ANY module.
      - Backup/Restore-OriginalRegValue: every reversible tweak snapshots its
        ORIGINAL value to HKCU:\Software\HTCoreArchitecture\TweakBackups so
        "Reset All Tweaks" restores the user's real prior settings, not just
        Microsoft factory defaults.
      - Backup-ServiceState / Restore-AllServicesToPreviousState: every
        service this tool disables is snapshotted (startup type + status).
      - Backup/Restore-EdgeState, Backup-OneDriveFiles: file-level backups
        taken before destructive removals.
      - Invoke-ScriptRollback: whole-system undo via the session restore point.

    Dry-run: snapshot writes are skipped silently under -WhatIf (nothing is
    changed, so there is nothing to snapshot); restores/rollbacks announce
    themselves through the Test-DryRun / guarded primitives.
#>

# ============================================================
#  SYSTEM RESTORE
# ============================================================
function New-SystemRestorePoint {
    if ($Script:RestorePointCreated) { return }
    if (Test-DryRun "Create System Restore point 'Pre-Humam Setup Blueprint' (once per session)") { return }
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
#  TWEAK BACKUP / RESTORE FRAMEWORK
# ============================================================
function Backup-OriginalRegValue {
    param(
        [Parameter(Mandatory = $true)][string]$TweakKey,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    # Dry-run: no value will be changed, so no snapshot is needed (and
    # writing one would itself be a mutation).
    if ($Script:DryRun) { return }
    try {
        if (-not (Test-Path $Script:TweaksBackupRegPath)) {
            New-Item -Path $Script:TweaksBackupRegPath -Force | Out-Null
        }
        $BackupName = ("$TweakKey--$Name") -replace '[\\:\s]', '_'
        $Existing = Get-RegValue -Path $Script:TweaksBackupRegPath -Name $BackupName
        if ($null -ne $Existing) { return }

        $CurrentVal = Get-RegValue -Path $Path -Name $Name
        $Serialized = if ($null -eq $CurrentVal) { "__NOTSET__" } else { "$CurrentVal" }
        Set-ItemProperty -Path $Script:TweaksBackupRegPath -Name $BackupName -Value $Serialized -Type String -Force
    } catch {
        Write-Log "BACKUP-WARN: could not snapshot $Path\$Name for '$TweakKey': $($_.Exception.Message)"
    }
}

function Restore-OriginalRegValue {
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
            Remove-RegValue -Path $Path -Name $Name
            return $true
        }

        $Value = if ($null -ne $Stored) { $Stored } else { $DefaultIfMissing }
        if ($null -eq $Value) { return $false }

        Set-RegValue -Path $Path -Name $Name -Value $Value -Type $Type
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
        Remove-RegKey -Path "HKCU:\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"
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
    if (-not $Script:DryRun) { $Script:PendingRestart = $true }
}

# ============================================================
#  SERVICES SNAPSHOT & RESTORE
# ============================================================
function Backup-ServiceState {
    param([Parameter(Mandatory = $true)][string]$Name)
    # Dry-run: the service will not actually be changed - skip the snapshot.
    if ($Script:DryRun) { return }
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
        if (-not $Script:NonInteractive) { Read-Host "   Press Enter to continue" }
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
            if (Test-DryRun "Restore service '$Name' to startup type '$OrigStartType'") { return }
            Set-Service -Name $Name -StartupType $OrigStartType -ErrorAction Stop
            if ($OrigStartType -notin @("Disabled")) {
                Start-Service -Name $Name -ErrorAction SilentlyContinue
            }
            Write-Success "Service '$Name' restored to original startup type '$OrigStartType'."
        } | Out-Null
    }
    Write-Info "Service restoration pass complete."
    if (-not $Script:NonInteractive) { Read-Host "   Press Enter to continue" }
}

# ============================================================
#  MICROSOFT EDGE BACKUP / RESTORE
# ============================================================
function Backup-EdgeState {
    if (Test-DryRun "Back up Edge version + Preferences/Bookmarks/Favicons to Desktop\HTCore_EdgeBackup") { return }
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
            if (Test-DryRun "Restore backed-up Edge Preferences/Bookmarks/Favicons into the Edge profile") { return }
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
#  ONEDRIVE FILE BACKUP
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
    if (Test-DryRun "Copy local OneDrive folder (~$SizeGB GB) to Desktop\HTCore_OneDriveBackup via robocopy") { return }
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
#  ROLLBACK TO SCRIPT'S OWN RESTORE POINT
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
        Invoke-Mutation -Description "Restore-Computer to restore point #$Script:ScriptRestorePointSeq (whole-system rollback + reboot)" -Action {
            Restore-Computer -RestorePoint $Script:ScriptRestorePointSeq -Confirm:$false -ErrorAction Stop
        } | Out-Null
    } | Out-Null
}
