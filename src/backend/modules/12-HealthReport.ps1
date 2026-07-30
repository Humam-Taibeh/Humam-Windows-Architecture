#Requires -Version 5.1
<#
.SYNOPSIS
    12-HealthReport.ps1 - read-only system health and configuration-drift
    snapshot, assembled for the GUI's exportable report (v10.3).

.DESCRIPTION
    One task, one JSON document. The GUI renders it and can export it as an
    HTML deliverable a technician hands to a client, so everything here is
    presentation-neutral DATA - no console formatting, no verdict text.

    HARD CONTRACT, inherited from 11-StateProbe.ps1 and for the same
    reasons: this file is READ-ONLY. It runs whenever someone opens the
    report, so a mutating probe would change the very system it claims to
    be describing. Nothing here writes to the registry, touches services,
    creates restore points or deletes anything.

    "DRIFT" here means the gap between what Pulse can apply and what is
    actually in effect right now - a tweak the user applied months ago that
    Windows Update has since reverted shows up as not-applied. That is
    computed from Get-PulseTweakState, so the report and the APPLIED chips
    on the cards can never disagree.

    EVERY SECTION IS INDEPENDENTLY FALLIBLE. A machine with System Restore
    disabled, a locked-down WMI, or no Appx subsystem must still produce a
    report - each block is wrapped so a failure yields $null for that
    section only, and the GUI renders "unavailable" rather than the whole
    feature breaking.
#>

function Get-HealthDriveReport {
    <# Per-volume capacity. Percent free is the number that actually drives
       a recommendation, so it is computed here rather than left to the
       presentation layer to re-derive. #>
    try {
        $drives = Get-PSDrive -PSProvider FileSystem -ErrorAction Stop |
            Where-Object { $null -ne $_.Used -and $null -ne $_.Free }
        $out = @()
        foreach ($drive in $drives) {
            $total = [double]$drive.Used + [double]$drive.Free
            if ($total -le 0) { continue }
            $out += [PSCustomObject]@{
                name        = $drive.Name
                totalGB     = [math]::Round($total / 1GB, 1)
                freeGB      = [math]::Round([double]$drive.Free / 1GB, 1)
                percentFree = [math]::Round(([double]$drive.Free / $total) * 100, 0)
            }
        }
        return $out
    } catch {
        return $null
    }
}

function Get-HealthRestorePointReport {
    <# System Restore is the backstop every destructive Pulse action leans
       on, so "is it even on, and how fresh is the newest checkpoint" is
       one of the most actionable lines in the whole report. #>
    try {
        $points = @(Get-ComputerRestorePoint -ErrorAction Stop)
        if ($points.Count -eq 0) {
            return [PSCustomObject]@{
                available = $true; count = 0; newestDescription = $null
                newestAgeDays = $null
            }
        }
        $newest = $points |
            Sort-Object { [System.Management.ManagementDateTimeConverter]::ToDateTime($_.CreationTime) } -Descending |
            Select-Object -First 1
        $created = [System.Management.ManagementDateTimeConverter]::ToDateTime($newest.CreationTime)
        return [PSCustomObject]@{
            available         = $true
            count             = $points.Count
            newestDescription = [string]$newest.Description
            newestAgeDays     = [math]::Round(((Get-Date) - $created).TotalDays, 1)
        }
    } catch {
        # Disabled, unsupported edition, or access denied unelevated.
        return [PSCustomObject]@{
            available = $false; count = 0; newestDescription = $null
            newestAgeDays = $null
        }
    }
}

function Get-HealthStartupReport {
    try {
        $items = @(Get-AllStartupItems)
        # .Recommendation, not the bare call (v1.0 fix). Get-StartupRecommendation
        # returns a HASHTABLE - @{ Recommendation; Impact; Reason } - so comparing
        # it to a string was always $false and recommendedDisable was hard-wired
        # to 0 on every machine. The only consumer is the report's "N startup
        # item(s) are recommended for disabling" finding
        # (frontend/health_report.py:findings), which therefore never fired: the
        # feature silently reported a clean bill of health for a machine with a
        # dozen flagged launchers.
        $recommended = @($items | Where-Object {
            (Get-StartupRecommendation -Item $_).Recommendation -eq 'Disable' })
        return [PSCustomObject]@{
            total              = $items.Count
            enabled            = @($items | Where-Object { $_.Enabled }).Count
            recommendedDisable = $recommended.Count
        }
    } catch {
        return $null
    }
}

function Get-HealthSystemReport {
    try {
        $info = Get-SystemInfoSnapshot
        $uptimeHours = if ($info.Uptime) { [math]::Round($info.Uptime.TotalHours, 1) } else { $null }
        return [PSCustomObject]@{
            os          = $info.OSCaption
            build       = $info.OSBuild
            edition     = $Script:WindowsEditionID
            cpu         = $info.CPUName
            totalRAMGB  = $info.TotalRAMGB
            freeRAMGB   = $info.FreeRAMGB
            powerPlan   = $info.PowerPlan
            uptimeHours = $uptimeHours
            psVersion   = $info.PSVersion
        }
    } catch {
        return $null
    }
}

function Get-PulseHealthReport {
    <# The whole document. Tweak state is reused verbatim from the probe so
       the report and the cards' APPLIED chips are the same measurement,
       not two implementations that will eventually disagree. #>
    $tweaks = $null
    try { $tweaks = Get-PulseTweakState } catch { $tweaks = $null }

    $applied = 0; $notApplied = 0; $unknown = 0
    if ($tweaks) {
        foreach ($key in $tweaks.Keys) {
            $value = $tweaks[$key]
            if ($null -eq $value) { $unknown++ }
            elseif ($value) { $applied++ }
            else { $notApplied++ }
        }
    }

    return [PSCustomObject]@{
        generatedAt  = (Get-Date).ToString("o")
        hostname     = $env:COMPUTERNAME
        elevated     = [bool]$Script:IsAdminSession
        system       = Get-HealthSystemReport
        drives       = Get-HealthDriveReport
        restorePoint = Get-HealthRestorePointReport
        startup      = Get-HealthStartupReport
        tweaks       = $tweaks
        tweakSummary = [PSCustomObject]@{
            applied    = $applied
            notApplied = $notApplied
            unknown    = $unknown
        }
    }
}
