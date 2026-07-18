#Requires -Version 5.1
<#
.SYNOPSIS
    20-Menus.ps1 - the entire interactive console experience (luxury
    hierarchical menus, intro, session log viewer, restart reminder).

.DESCRIPTION
    PRESENTATION ONLY: every function here orchestrates calls into the
    engine modules (02-10); none of them mutates system state directly.
    None of this code ever executes under the GUI flag - core.ps1 routes
    -Task invocations straight to 30-GuiDispatcher.ps1 instead.
#>

# ============================================================
#  TIME-BASED GREETING & EPIC INTRO
# ============================================================
function Show-TimeGreeting {
    $Hour = (Get-Date).Hour
    if ($Hour -lt 12) { return "Good Morning" }
    elseif ($Hour -lt 18) { return "Good Afternoon" }
    else { return "Good Evening" }
}

function Show-EpicIntro {
    Clear-Host
    $greeting = Show-TimeGreeting
    Write-Host ""
    Write-Host "       ██████╗ ██╗   ██╗██╗     ███████╗███████╗" -ForegroundColor Cyan
    Write-Host "       ██╔══██╗██║   ██║██║     ██╔════╝██╔════╝" -ForegroundColor Cyan
    Write-Host "       ██████╔╝██║   ██║██║     ███████╗█████╗  " -ForegroundColor Cyan
    Write-Host "       ██╔═══╝ ██║   ██║██║     ╚════██║██╔══╝  " -ForegroundColor Cyan
    Write-Host "       ██║     ╚██████╔╝███████╗███████║███████╗" -ForegroundColor Cyan
    Write-Host "       ╚═╝      ╚═════╝ ╚══════╝╚══════╝╚══════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "        $greeting" -ForegroundColor Yellow
    Write-Host "        Pulse · Ultimate Deployment & Optimization Suite — by Humam Taibeh" -ForegroundColor DarkGray
    Write-Host "        v$Script:ScriptVersion  |  $Script:OSCaption (Build $Script:OSBuild)" -ForegroundColor DarkGray
    if ($Script:DryRun) {
        Write-Host "        DRY-RUN MODE (-WhatIf): changes will be simulated, not applied." -ForegroundColor DarkYellow
    }
    Write-Host ""
    Start-Sleep -Seconds 2
}

# ============================================================
#  SESSION LOG VIEWER
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
#  SAFETY & RECOVERY MENU
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
#  RUNTIMES MODULE
# ============================================================
function Show-RuntimesModule {
    if (Ensure-Winget) {
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
#  APP DEPLOYMENT HUB
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

# ============================================================
#  1. SOFTWARE MANAGEMENT
# ============================================================
function Show-SoftwareManagementMenu {
    do {
        Write-Banner "📦 SOFTWARE MANAGEMENT"
        Write-ModulePreview -Items @(
            "App Deployment Hub: Essential Apps, Programming & AI Core, Gaming Launchers, Hardware Diagnostics",
            "Core API Runtimes: DirectX, VC++, .NET, Java (bulk or individual)",
            "Startup Program Manager: control which programs launch at boot",
            "Microsoft Office Deployment: auto-install or manual guide",
            "Verify Dev Environment: PATH doctor for Git, Python, Java, GCC..."
        )
        Write-Host "   [1]  App Deployment Hub" -ForegroundColor White
        Write-Host "   [2]  Core API Runtimes" -ForegroundColor White
        Write-Host "   [3]  Startup Program Manager" -ForegroundColor White
        Write-Host "   [4]  Microsoft Office Deployment" -ForegroundColor White
        Write-Host "   [5]  Verify Dev Environment (PATH doctor)" -ForegroundColor White
        Write-Host "   [X]  Back to Main Menu" -ForegroundColor DarkGray
        Write-Divider
        $Choice = Read-Choice -Prompt "   Select option (1-5, X)" -Valid @('1','2','3','4','5','x')
        switch ($Choice) {
            '1' { Show-AppDeploymentHub }
            '2' { Write-Banner "CORE API RUNTIMES"; Write-ModulePreview -Items @("DirectX, VC++, .NET, Java"); Show-RuntimesModule; Read-Host "   Press Enter to continue" }
            '3' { Show-StartupProgramManager }
            '4' { Show-OfficeDeployment }
            '5' { Write-Banner "VERIFY DEV ENVIRONMENT"; Verify-Environment | Out-Null; Read-Host "   Press Enter to continue" }
            'X' { return }
        }
    } while ($true)
}

# ============================================================
#  2. SYSTEM OPTIMIZATION
# ============================================================
function Show-SystemOptimizationMenu {
    do {
        Write-Banner "⚡ SYSTEM OPTIMIZATION"
        Write-ModulePreview -Items @(
            "Smart System Tweaks: Dark mode, mouse, taskbar, OneDrive, Edge, reset defaults",
            "Performance & Gaming: Network optimizer, Pulse Power Plan, Game Mode, Classic context menu"
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
                    "Pulse Power Plan - unlocks hidden High-Performance scheme",
                    "Game Mode & Game Bar - enables Game Mode, disables background recording",
                    "Classic Right-Click Menu - restores the full Windows 10 context menu (Win11 only)"
                )
                New-SystemRestorePoint

                if (Ask-User "Network & Ping Optimizer" "Flushes DNS, resets Winsock and IP stack for lowest latency.") { Invoke-NetworkOptimization }
                if (Ask-User "Activate Pulse Power Plan" "Unlocks the hidden High-Performance scheme as the Pulse Power Plan.") { Enable-UltimatePerformancePowerPlan }
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

# ============================================================
#  3. MAINTENANCE & REPAIR
# ============================================================
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
                if (Ask-User "Run SFC + DISM" "Repairs system files.") { Invoke-SystemRepair | Out-Null }
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

# ============================================================
#  4. PRIVACY & SECURITY
# ============================================================
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

# ============================================================
#  5. INFORMATION & UTILITIES
# ============================================================
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
                    if (-not (Test-DryRun "Export all third-party drivers to Desktop\Pulse_DriverBackup")) {
                        try {
                            $BackupPath = "$env:USERPROFILE\Desktop\Pulse_DriverBackup"
                            New-Item -Path $BackupPath -ItemType Directory -Force | Out-Null
                            Export-WindowsDriver -Online -Destination $BackupPath -ErrorAction Stop | Out-Null
                            Write-Success "Backup saved to Desktop\Pulse_DriverBackup"
                        } catch {
                            Write-Warn "Driver Backup halted."
                        }
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
#  MAIN MENU & RESTART REMINDER
# ============================================================
function Show-MainMenu {
    Write-Banner "PULSE" "Ultimate Windows Optimization  |  v$Script:ScriptVersion  |  by Humam Taibeh"
    Write-Host "   [1] 📦 Software Management" -ForegroundColor White
    Write-Host "   [2] ⚡ System Optimization" -ForegroundColor White
    Write-Host "   [3] 🔧 Maintenance & Repair" -ForegroundColor White
    Write-Host "   [4] 🛡️ Privacy & Security" -ForegroundColor White
    Write-Host "   [5] 📊 Information & Utilities" -ForegroundColor White
    Write-Host "   [6] 🛟 Safety & Recovery" -ForegroundColor White
    Write-Host "   [0] 🚪 Exit" -ForegroundColor DarkGray
    Write-Divider
    if ($Script:DryRun) {
        Write-Host "   [WHATIF] DRY-RUN MODE ACTIVE - every operation is simulated only." -ForegroundColor DarkYellow
        Write-Divider
    }
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
            Invoke-Mutation -Description "Restart the computer" -Action {
                Write-Info "Restarting in 5 seconds... Press Ctrl+C to abort."
                Start-Sleep -Seconds 5
                Restart-Computer -Force
            } | Out-Null
        }
    }
}
