#Requires -Version 5.1
<#
.SYNOPSIS
    07-Maintenance.ps1 - system repair (SFC/DISM), cache cleanup, disk
    management and the services optimizer.

.DESCRIPTION
    All destructive operations are dry-run aware:
      - Clear-SystemCaches under -WhatIf measures reclaimable space without
        deleting a single file.
      - Remove-WindowsOldFolder reports the folder size it would reclaim.
      - Service changes are snapshotted first (02-Safety.ps1) so "Restore
        All Services" can put them back exactly as they were.
    The optional-services list is data ($Script:OptionalServices).
#>

# ============================================================
#  ADVANCED REPAIR (SFC + DISM)
# ============================================================
function Invoke-SystemRepair {
    New-SystemRestorePoint
    if (Test-DryRun "Run 'sfc /scannow' followed by 'DISM /Online /Cleanup-Image /RestoreHealth'") { return $true }

    Write-SectionHeader "System File Checker (SFC)"
    Write-Info "Running sfc /scannow -- live output below. This can take several minutes."
    $SfcOk = Invoke-WithRetry -OperationName "SFC Scan" -Action {
        # sfc.exe emits UTF-16: read through a redirected pipe, every other
        # byte is a NUL, which breaks both the display and — critically —
        # the "unable to fix" text match below. Strip NULs before use.
        # Stream-while-accumulating (v6): each line is echoed the moment
        # sfc produces it, so "Verification x% complete." rewrites reach
        # the GUI live instead of arriving in one block after the scan.
        $OutputLines = New-Object System.Collections.Generic.List[string]
        & sfc /scannow 2>&1 | ForEach-Object {
            $Clean = ([string]$_ -replace "`0", "")
            [void]$OutputLines.Add($Clean)
            if ($Clean.Trim()) { Write-Host $Clean }
        }
        $SfcExit = $LASTEXITCODE
        $OutputText = $OutputLines -join [Environment]::NewLine

        if ($SfcExit -ne 0) {
            throw "sfc /scannow exited with code $SfcExit."
        }

        # sfc's exit code alone is not trustworthy: on several Windows builds
        # it still returns 0 even when it explicitly says it could not fix
        # everything it found. That text is the real signal, so treat it as
        # a failure regardless of exit code.
        # NOTE: this match is English-only - sfc's message is localized on
        # non-English Windows installs, so this check is a best-effort net,
        # not a guarantee, on those systems.
        if ($OutputText -match "unable to fix some of them") {
            throw "sfc /scannow found corrupt files it could not fully repair. See CBS.log for details."
        }
    }

    Write-SectionHeader "DISM Image Health Restore"
    Write-Info "Running DISM /Online /Cleanup-Image /RestoreHealth -- live output below."
    $DismOk = Invoke-WithRetry -OperationName "DISM RestoreHealth" -Action {
        # Pipe through Write-Host so the progress streams to the caller
        # instead of being captured into Invoke-WithRetry's return value
        # (which silently hid all DISM output and polluted $DismOk).
        & DISM /Online /Cleanup-Image /RestoreHealth | ForEach-Object { Write-Host $_ }
        if ($LASTEXITCODE -ne 0) { throw "DISM exited with code $LASTEXITCODE." }
    }

    return ($SfcOk -and $DismOk)
}

# ============================================================
#  AGGRESSIVE CACHE CLEAN
# ============================================================
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
        if ($Script:DryRun) { Write-Info "[WHATIF] Measuring (not deleting) $Target ..." }
        else                { Write-Info "Cleaning $Target ..." }
        Get-ChildItem -Path $Target -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $Size = if ($_.PSIsContainer) { 0 } else { $_.Length }
            if ($Script:DryRun) {
                # Dry-run: tally what a real pass would reclaim, delete nothing.
                $TotalFreedBytes += $Size
                return
            }
            try {
                Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction Stop
                $TotalFreedBytes += $Size
            } catch {
                $LockedCount++
            }
        }
    }

    if (-not (Test-DryRun "Empty the Recycle Bin")) {
        try {
            Write-Info "Emptying Recycle Bin..."
            Clear-RecycleBin -Force -ErrorAction SilentlyContinue
        } catch {}
    }

    $FreedMB = [math]::Round($TotalFreedBytes / 1MB, 2)
    if ($Script:DryRun) {
        Write-Success "Dry-run complete. A real cleanup pass would reclaim approximately $FreedMB MB."
    } else {
        Write-Success "Cache cleanup complete. Approximately $FreedMB MB reclaimed."
    }
    if ($LockedCount -gt 0) {
        Write-Warn "$LockedCount item(s) were skipped because they were locked/in use (normal for active update/prefetch files)."
    }
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
        if (Test-DryRun "Delete $Path (reclaims ~$SizeGB GB)") { return }
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
    $TargetWord = if ($Enable) { "on" } else { "off" }
    if (Test-DryRun "Run 'powercfg /hibernate $TargetWord'") { return }
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
        if (Test-DryRun "Optimize drive $($Vol.DriveLetter): (TRIM for SSD / defrag for HDD)") { continue }
        try {
            Write-Info "Optimizing $($Vol.DriveLetter): ..."
            Optimize-Volume -DriveLetter $Vol.DriveLetter -ErrorAction Stop
            Write-Success "$($Vol.DriveLetter): optimized (TRIM for SSD / defrag for HDD, auto-detected)."
        } catch {
            # A real per-drive failure (active VSS snapshot, BitLocker,
            # network mount are all plausible) - Write-ErrorX, not Write-Warn,
            # so "OptimizeDrives" doesn't report full success when a drive
            # was actually skipped due to an error.
            Write-ErrorX "Could not optimize $($Vol.DriveLetter): $($_.Exception.Message)"
        }
    }
}

function Invoke-DiskCleanupUtility {
    if (Test-DryRun "Launch the native Disk Cleanup utility (cleanmgr.exe)") { return }
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
#  SERVICES OPTIMIZER
# ============================================================
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
    if (Test-DryRun "Stop and disable service '$Name' ($Label)") { return }
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
    if (Test-DryRun "Re-enable service '$Name' ($Label) with startup type Manual") { return }
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
