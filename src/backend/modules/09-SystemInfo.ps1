#Requires -Version 5.1
<#
.SYNOPSIS
    09-SystemInfo.ps1 - read-only system insight: hardware snapshot and the
    interactive System Info Dashboard.

.DESCRIPTION
    Get-SystemInfoSnapshot returns a structured object (never prints), so
    the console dashboard and the GUI dispatcher's "SystemInfo" task share
    one source of truth. Everything here is read-only - no dry-run guards
    are needed by design.
#>

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
