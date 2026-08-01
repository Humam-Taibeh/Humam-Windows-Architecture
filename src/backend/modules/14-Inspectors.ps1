#Requires -Version 5.1
<#
.SYNOPSIS
    14-Inspectors.ps1 - the READ-ONLY hardware/state inspectors (v1.0+ Phase 1).

.DESCRIPTION
    Three reports that answer a question and change nothing:

        Get-PulsePowerHealth    battery wear, cycle count, active power plan
        Get-PulseRestorePoints  every System Restore checkpoint on this PC
        Get-PulseStorageScan    what is actually filling a drive

    HARD CONTRACT, inherited verbatim from 11-StateProbe.ps1,
    12-HealthReport.ps1 and 13-Activation.ps1: THIS FILE IS READ-ONLY. It
    reads WMI, the registry and the filesystem, and formats what it finds.
    Nothing here writes a value, deletes a file, creates or restores a
    checkpoint, or changes a power plan. An "inspector" that could mutate
    would be lying about what it is, and tests/test_contract.py enforces
    that claim rather than trusting this comment.

    THE DELETION QUESTION, settled once: Get-PulseStorageScan finds large
    files and does NOT offer to remove them. Pulse hands the path to
    Explorer instead. A bulk file deleter driven by a size-sorted list is
    how an irreplaceable folder gets destroyed by a mis-click, and Windows
    already ships a file manager with undo, a Recycle Bin and a confirm.
    Finding the 40GB nobody could account for is the whole value here; the
    delete is the easy part and the dangerous part.

    NO ELEVATION REQUIRED, by design. Battery data, restore-point metadata
    and the user's own directories are all readable by a standard user. A
    section that genuinely cannot be read unelevated reports its own
    `available = $false` instead of failing the document, which is the
    same three-state honesty rule 11-StateProbe.ps1 follows: unknown is
    reported as unknown, never as a confident zero.

    EVERY SECTION IS INDEPENDENTLY FALLIBLE (12-HealthReport.ps1's rule).
    A desktop with no battery, a machine with System Restore switched off
    and a locked-down WMI must all still produce a document. Each block
    yields its own "not available" shape rather than throwing.
#>

# ============================================================
#  F1 — BATTERY & POWER HEALTH
# ============================================================
# The battery classes live in root\WMI, not root\cimv2, and are the same
# ones powercfg /batteryreport reads. Going to WMI directly rather than
# shelling out to powercfg is deliberate: /batteryreport WRITES an HTML
# file (a mutation, in a module that promises none), takes seconds, and
# would then have to be parsed back out of HTML. The numbers below are
# the report's own source.
#
# Capacities are in mWh. DesignedCapacity is what the cells shipped with;
# FullChargedCapacity is what they hold today. Wear is the gap, and it is
# the single number a user actually wants: "your battery holds 62% of
# what it did when new" explains a laptop that suddenly lasts two hours.

function Get-BatteryWmiInstance {
    <# One root\WMI class, or $null. Every one of these is optional: OEMs
       ship firmware that omits classes, VMs have none, and desktops have
       no battery at all. A missing class is a fact about the machine, not
       an error. #>
    param([Parameter(Mandatory)][string]$ClassName)
    try {
        $instance = Get-CimInstance -Namespace 'root\WMI' -ClassName $ClassName -ErrorAction Stop |
            Select-Object -First 1
        return $instance
    } catch {
        return $null
    }
}

function Get-PulseBatteryHealth {
    <# Wear/cycle detail, or an `installed = $false` shape on a desktop. #>
    $static    = Get-BatteryWmiInstance -ClassName 'BatteryStaticData'
    $fullCharge = Get-BatteryWmiInstance -ClassName 'BatteryFullChargedCapacity'
    $cycles    = Get-BatteryWmiInstance -ClassName 'BatteryCycleCount'

    $win32 = $null
    try {
        $win32 = Get-CimInstance -ClassName Win32_Battery -ErrorAction Stop | Select-Object -First 1
    } catch {
        $win32 = $null
    }

    if (-not $static -and -not $fullCharge -and -not $win32) {
        return [PSCustomObject]@{
            installed = $false; designedCapacity = $null; fullCapacity = $null
            wearPercent = $null; cycleCount = $null; chargePercent = $null
            onAcPower = $null; chemistry = $null; note =
                "No battery detected - this looks like a desktop PC."
        }
    }

    $designed = if ($static)     { [double]$static.DesignedCapacity }    else { $null }
    $full     = if ($fullCharge) { [double]$fullCharge.FullChargedCapacity } else { $null }

    # Guarded three ways: a missing class, a firmware that reports 0, and a
    # full-charge capacity ABOVE design (some OEMs do this on a new cell).
    # A negative "wear" reads as a bug; clamping at 0 reads as "like new".
    $wear = $null
    if ($designed -gt 0 -and $full -gt 0) {
        $wear = [math]::Round([math]::Max(0, (1 - ($full / $designed)) * 100), 1)
    }

    # PowerOnline is the honest source for AC vs battery. Win32_Battery's
    # BatteryStatus overloads the same field with charging state, so "2"
    # means "on AC" there and something else everywhere else.
    $onAc = $null
    $status = Get-BatteryWmiInstance -ClassName 'BatteryStatus'
    if ($status -and $null -ne $status.PowerOnline) { $onAc = [bool]$status.PowerOnline }

    return [PSCustomObject]@{
        installed        = $true
        designedCapacity = $designed
        fullCapacity     = $full
        wearPercent      = $wear
        cycleCount       = if ($cycles -and $cycles.CycleCount -gt 0) { [int]$cycles.CycleCount } else { $null }
        chargePercent    = if ($win32 -and $null -ne $win32.EstimatedChargeRemaining) { [int]$win32.EstimatedChargeRemaining } else { $null }
        onAcPower        = $onAc
        chemistry        = if ($win32) { [string]$win32.Chemistry } else { $null }
        note             = $null
    }
}

function Get-PulsePowerPlans {
    <# The active power scheme plus the count of available ones. Read-only:
       Pulse's own Ultimate Power Plan card is what CHANGES a plan; this
       only reports which one is live, so the two never disagree about the
       current state. #>
    try {
        # No interpolated value in this filter, so nothing to escape - the
        # WQL-escaping contract (tests/test_contract.py) applies to filters
        # built from variables, and this one is a constant.
        $plans = @(Get-CimInstance -Namespace 'root\cimv2\power' -ClassName Win32_PowerPlan -ErrorAction Stop)
        $active = $plans | Where-Object { $_.IsActive } | Select-Object -First 1
        return [PSCustomObject]@{
            available  = $true
            activeName = if ($active) { [string]$active.ElementName } else { $null }
            planCount  = $plans.Count
        }
    } catch {
        return [PSCustomObject]@{ available = $false; activeName = $null; planCount = 0 }
    }
}

function Get-PulsePowerHealth {
    <# The whole F1 document. #>
    $sleep = $null
    try {
        # Hibernation's own state, read from the registry rather than from
        # powercfg: it is the flag the Enable/Disable Hibernation cards in
        # Maintenance write, so reporting it here lets a user see the
        # result of that card without leaving the inspector.
        $value = Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' `
            -Name 'HibernateEnabled' -ErrorAction Stop
        $sleep = [bool]$value.HibernateEnabled
    } catch {
        $sleep = $null
    }

    return [PSCustomObject]@{
        generatedAt      = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        battery          = Get-PulseBatteryHealth
        power            = Get-PulsePowerPlans
        hibernateEnabled = $sleep
    }
}

function Get-PowerHealthSummaryLine {
    <# The one-line verdict the GUI shows as its toast. #>
    param([Parameter(Mandatory)]$Report)
    $b = $Report.battery
    if (-not $b.installed) {
        $plan = if ($Report.power.activeName) { " Power plan: $($Report.power.activeName)." } else { "" }
        return "No battery detected (desktop PC).$plan"
    }
    if ($null -ne $b.wearPercent) {
        $health = [math]::Round(100 - $b.wearPercent, 1)
        return "Battery health $health% of design capacity ($($b.wearPercent)% wear)."
    }
    return "Battery detected, but this firmware does not report its capacity."
}

# ============================================================
#  F3 — RESTORE POINT BROWSER
# ============================================================
# Pulse creates restore points and calls them the safety net every
# destructive action leans on. Until now it offered no way to check that
# any exist. This is the receipt for that promise.
#
# READ-ONLY, and the rollback deliberately is NOT implemented here: a
# System Restore is a reboot-time operation with its own Microsoft-signed
# wizard (rstrui.exe), and reimplementing that flow inside a third-party
# utility would be both worse and less trustworthy than launching it. The
# GUI hands off; see widgets.RestorePointDialog.

function Get-RestorePointTypeLabel {
    <# The numeric type/event codes System Restore records, translated once
       here so the GUI, the console and the log can never describe the same
       checkpoint differently. #>
    param($RestorePointType, $EventType)
    $type = 0
    if ($null -ne $RestorePointType) { $type = [int]$RestorePointType }
    switch ($type) {
        0  { return "Application install" }
        1  { return "Application uninstall" }
        10 { return "Device driver install" }
        12 { return "Modify settings" }
        13 { return "Cancelled operation" }
        7  { return "Checkpoint" }
        default {
            if ([int]$EventType -eq 100) { return "Manual checkpoint" }
            return "System checkpoint"
        }
    }
}

function Get-SystemRestoreEnabled {
    <# Whether System Restore protection is on for the system drive.
       Returns $null (unknown) rather than $false when the value cannot be
       read - the three-state rule; a confident "off" on an unreadable key
       would tell the user to enable something already enabled. #>
    try {
        $value = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore' `
            -Name 'RPSessionInterval' -ErrorAction Stop
        return ([int]$value.RPSessionInterval -gt 0)
    } catch {
        return $null
    }
}

function Get-PulseRestorePoints {
    <# Every checkpoint, newest first, plus whether protection is even on. #>
    $enabled = Get-SystemRestoreEnabled
    try {
        $raw = @(Get-ComputerRestorePoint -ErrorAction Stop)
    } catch {
        # Disabled, a Home edition with restore off, or access denied.
        return [PSCustomObject]@{
            generatedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            available   = $false
            enabled     = $enabled
            count       = 0
            points      = @()
        }
    }

    $points = @()
    foreach ($point in $raw) {
        $created = $null
        try {
            $created = [System.Management.ManagementDateTimeConverter]::ToDateTime($point.CreationTime)
        } catch {
            $created = $null
        }
        $points += [PSCustomObject]@{
            sequence    = [int]$point.SequenceNumber
            description = [string]$point.Description
            created     = if ($created) { $created.ToString('yyyy-MM-dd HH:mm') } else { $null }
            ageDays     = if ($created) { [math]::Round(((Get-Date) - $created).TotalDays, 1) } else { $null }
            typeLabel   = Get-RestorePointTypeLabel -RestorePointType $point.RestorePointType -EventType $point.EventType
        }
    }
    # Newest first: the checkpoint a user wants is almost always the most
    # recent one, and a list that opens on 2019 reads as stale data.
    $points = @($points | Sort-Object -Property sequence -Descending)

    return [PSCustomObject]@{
        generatedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        available   = $true
        enabled     = $enabled
        count       = $points.Count
        points      = $points
    }
}

function Get-RestorePointSummaryLine {
    param([Parameter(Mandatory)]$Report)
    if (-not $Report.available) {
        return "System Restore reported no checkpoints - protection may be turned off for this PC."
    }
    if ($Report.count -eq 0) {
        return "System Restore is available, but this PC has no checkpoints yet."
    }
    $newest = $Report.points | Select-Object -First 1
    $age = if ($null -ne $newest.ageDays) { "$($newest.ageDays) day(s) old" } else { "date unknown" }
    return "$($Report.count) restore point(s); newest is $age."
}

# ============================================================
#  F2 — STORAGE ANALYZER
# ============================================================
# "Disk full, no idea why" is the complaint this answers. Two views of the
# same scan: the biggest immediate subfolders of the chosen root (where
# the space went) and the biggest individual files (what to act on).
#
# BOUNDED BY DESIGN. A naive recursive size walk over C:\ on a spinning
# disk runs for minutes and looks hung. Three limits keep it honest:
#   * $MaxSeconds  - a wall-clock budget; the scan reports what it has and
#                    flags itself truncated rather than running forever
#   * $TopCount    - only the largest N are ever returned
#   * reparse-point skipping - junctions and symlinks are NOT followed, so
#                    the walk cannot loop or double-count (C:\Users\All
#                    Users -> C:\ProgramData is the classic infinite loop)
#
# Progress is streamed as ordinary host output so the GUI's live console
# shows movement; PowerShellTask.cancel() kills the process outright, so
# cancellation needs no cooperation from the loop.

function Get-StorageScanRoots {
    <# Fixed drives only. Removable and network volumes are excluded: a
       size walk over a mapped share is a network operation with wildly
       different performance, and Pulse is a local machine tool. #>
    try {
        $volumes = @(Get-CimInstance -ClassName Win32_LogicalDisk -Filter 'DriveType = 3' -ErrorAction Stop)
        return @($volumes | ForEach-Object {
            [PSCustomObject]@{
                path       = "$($_.DeviceID)\"
                label      = [string]$_.VolumeName
                totalBytes = [double]$_.Size
                freeBytes  = [double]$_.FreeSpace
            }
        })
    } catch {
        return @()
    }
}

function Measure-DirectorySize {
    <# Recursive byte total for one directory, with the budget and the
       reparse-point guard described above. Errors are swallowed per-item,
       not per-scan: an unreadable AppData subfolder must cost that folder,
       never the whole report. #>
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][datetime]$Deadline,
        [ref]$FileAccumulator,
        [int]$TopFiles = 40
    )
    $total = [double]0
    try {
        $items = Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue
    } catch {
        return $total
    }
    foreach ($item in $items) {
        if ((Get-Date) -gt $Deadline) { break }
        # A reparse point's target is counted under its real parent; following
        # it here would double-count at best and loop at worst.
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        $total += [double]$item.Length
        if ($null -ne $FileAccumulator) {
            $list = $FileAccumulator.Value
            $list.Add([PSCustomObject]@{
                path = $item.FullName; bytes = [double]$item.Length
                modified = $item.LastWriteTime
            }) | Out-Null
        }
    }
    return $total
}

function Get-PulseStorageScan {
    <# The F2 document for one root. #>
    param(
        [string]$ScanPath = "",
        [int]$MaxSeconds = 90,
        [int]$TopCount = 15
    )

    $roots = Get-StorageScanRoots
    if ([string]::IsNullOrWhiteSpace($ScanPath)) {
        $ScanPath = $env:SystemDrive + "\"
    }
    if (-not (Test-Path -LiteralPath $ScanPath)) {
        return [PSCustomObject]@{
            generatedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
            scanPath = $ScanPath; available = $false; truncated = $false
            roots = $roots; folders = @(); files = @()
            totalBytes = 0
        }
    }

    $deadline = (Get-Date).AddSeconds($MaxSeconds)
    $files = New-Object System.Collections.ArrayList
    $folders = @()
    $total = [double]0

    $children = @()
    try {
        $children = @(Get-ChildItem -LiteralPath $ScanPath -Force -Directory -ErrorAction SilentlyContinue)
    } catch {
        $children = @()
    }

    foreach ($dir in $children) {
        if ((Get-Date) -gt $deadline) { break }
        if ($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        Write-Host ("   scanning " + $dir.Name + " ...")
        $size = Measure-DirectorySize -Path $dir.FullName -Deadline $deadline -FileAccumulator ([ref]$files)
        $total += $size
        $folders += [PSCustomObject]@{ path = $dir.FullName; name = $dir.Name; bytes = $size }
    }

    # Loose files sitting directly in the root count too - a 12GB pagefile
    # or an old ISO on C:\ is exactly the kind of thing this is for.
    try {
        foreach ($file in @(Get-ChildItem -LiteralPath $ScanPath -Force -File -ErrorAction SilentlyContinue)) {
            if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            $total += [double]$file.Length
            $files.Add([PSCustomObject]@{
                path = $file.FullName; bytes = [double]$file.Length
                modified = $file.LastWriteTime
            }) | Out-Null
        }
    } catch { }

    $truncated = ((Get-Date) -gt $deadline)

    $topFolders = @($folders | Sort-Object -Property bytes -Descending | Select-Object -First $TopCount)
    $topFiles = @($files | Sort-Object -Property bytes -Descending | Select-Object -First $TopCount |
        ForEach-Object {
            [PSCustomObject]@{
                path  = $_.path
                name  = Split-Path -Path $_.path -Leaf
                bytes = $_.bytes
                modified = if ($_.modified) { ([datetime]$_.modified).ToString('yyyy-MM-dd') } else { $null }
            }
        })

    return [PSCustomObject]@{
        generatedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        scanPath    = $ScanPath
        available   = $true
        truncated   = $truncated
        roots       = $roots
        folders     = $topFolders
        files       = $topFiles
        totalBytes  = $total
    }
}

function Get-StorageScanSummaryLine {
    param([Parameter(Mandatory)]$Report)
    if (-not $Report.available) {
        return "Could not read $($Report.scanPath) - the path does not exist or is not readable."
    }
    $gb = [math]::Round($Report.totalBytes / 1GB, 1)
    $note = if ($Report.truncated) { " (partial - the scan hit its time budget)" } else { "" }
    return "Scanned $($Report.scanPath): $gb GB across $($Report.folders.Count) top folder(s)$note."
}
