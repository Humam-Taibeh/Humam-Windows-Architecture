#Requires -Version 5.1
<#
.SYNOPSIS
    08-Privacy.ps1 - debloat, telemetry, advertising ID and activity history.

.DESCRIPTION
    Data-driven: the bloatware list ($Script:BloatApps) and the telemetry
    scheduled-task list ($Script:TelemetryTasks) live in 01-Catalogs.ps1.
    Every registry policy value is snapshotted first so "Reset All Tweaks"
    restores the user's original settings; every service change is
    snapshotted so "Restore Services" can undo it. Fully -WhatIf aware.
#>

# ============================================================
#  BLOATWARE REMOVAL
# ============================================================
function Remove-Bloatware {
    Write-SectionHeader "Bloatware Removal"
    New-SystemRestorePoint
    $RemovedAny = $false

    # Provisioned packages are the copies STAGED for future users / feature
    # updates. Removing only the installed package leaves the provisioned one
    # behind, so a "removed" app silently returns on the next major update or
    # new profile. Deprovisioning as well makes the purge actually stick.
    # Fetched once (needs admin, which RemoveBloatware already requires).
    $Provisioned = @()
    try {
        $Provisioned = @(Get-AppxProvisionedPackage -Online -ErrorAction Stop)
    } catch {
        Write-Log "Remove-Bloatware: could not enumerate provisioned packages - $($_.Exception.Message)"
    }

    foreach ($Pkg in $Script:BloatApps) {
        $Installed = Get-AppxPackage -Name $Pkg -AllUsers -ErrorAction SilentlyContinue
        if ($Installed) {
            if (Test-DryRun "Remove Store app package '$Pkg' (all users)") {
                $RemovedAny = $true
            } else {
                try {
                    $Installed | Remove-AppxPackage -AllUsers -ErrorAction Stop
                    Write-Success "Removed $Pkg"
                    $RemovedAny = $true
                } catch {
                    # A real failure (often policy-protected packages like Xbox/
                    # Widgets on some editions) - Write-ErrorX, not Write-Warn, so
                    # "RemoveBloatware" doesn't report full success when packages
                    # actually remain installed.
                    Write-ErrorX "Could not remove $Pkg (may be protected by policy): $($_.Exception.Message)"
                }
            }
        }

        # Deprovision the staged copy so it does not reinstall. A deprovision
        # failure is a Write-Warn (not Write-ErrorX): the installed-package
        # removal above is the authoritative success signal, and many staged
        # packages simply aren't present to deprovision.
        foreach ($P in @($Provisioned | Where-Object { $_.DisplayName -eq $Pkg })) {
            if (Test-DryRun "Deprovision staged package '$Pkg' so it won't reinstall for new users") {
                $RemovedAny = $true
                continue
            }
            try {
                Remove-AppxProvisionedPackage -Online -PackageName $P.PackageName -ErrorAction Stop | Out-Null
                Write-Success "Deprovisioned $Pkg (won't reinstall for new users)"
                $RemovedAny = $true
            } catch {
                Write-Warn "Could not deprovision $Pkg (may be protected): $($_.Exception.Message)"
            }
        }
    }
    if (-not $RemovedAny) {
        Write-AlreadyOK "No listed bloatware packages found - system is already clean."
    }
    Write-Info "Bloatware sweep complete."
}

# ============================================================
#  TELEMETRY & DIAGNOSTICS
# ============================================================
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
        Set-RegValue -Path $Path -Name "AllowTelemetry" -Value 0 -Type DWord

        Invoke-Mutation -Description "Disable and stop the DiagTrack + dmwappushservice services" -Action {
            Set-Service -Name "DiagTrack" -StartupType Disabled -ErrorAction SilentlyContinue
            Stop-Service -Name "DiagTrack" -Force -ErrorAction SilentlyContinue
            Set-Service -Name "dmwappushservice" -StartupType Disabled -ErrorAction SilentlyContinue
        } | Out-Null

        foreach ($Task in $Script:TelemetryTasks) {
            Invoke-Mutation -Description "Disable scheduled task '$($Task.Path)$($Task.Name)'" -Action {
                Disable-ScheduledTask -TaskPath $Task.Path -TaskName $Task.Name -ErrorAction SilentlyContinue | Out-Null
            } | Out-Null
        }
        Write-Success "Telemetry services and scheduled diagnostics disabled."
    } catch {
        Write-ErrorX "Telemetry hardening encountered an issue: $($_.Exception.Message)"
    }
}

# ============================================================
#  ADVERTISING ID
# ============================================================
function Disable-AdvertisingID {
    New-SystemRestorePoint
    $Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo"
    if ((Get-RegValue -Path $Path -Name "Enabled") -eq 0) {
        Write-AlreadyOK "Advertising ID is already disabled."
        return
    }
    Backup-OriginalRegValue -TweakKey "AdvertisingID" -Path $Path -Name "Enabled"
    try {
        Set-RegValue -Path $Path -Name "Enabled" -Value 0 -Type DWord
        Write-Success "Advertising ID disabled."
    } catch {
        Write-ErrorX "Failed to disable Advertising ID: $($_.Exception.Message)"
    }
}

# ============================================================
#  ACTIVITY HISTORY
# ============================================================
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
        Set-RegValue -Path $Path -Name "EnableActivityFeed" -Value 0 -Type DWord
        Set-RegValue -Path $Path -Name "PublishUserActivities" -Value 0 -Type DWord
        Set-RegValue -Path $Path -Name "UploadUserActivities" -Value 0 -Type DWord
        Write-Success "Activity History sync disabled."
    } catch {
        Write-ErrorX "Failed to disable Activity History: $($_.Exception.Message)"
    }
}
