#Requires -Version 5.1
<#
.SYNOPSIS
    PULSE - Modular Enterprise Edition (v6.0)
    Windows Deployment & Optimization Framework - by Humam Taibeh
    -----------------------------------------------------------
    THIS FILE IS A THIN ORCHESTRATOR. All logic lives in the cohesive
    modules under .\modules\, dot-sourced below into ONE shared script
    scope (so every $Script:/$global: variable behaves exactly as it did
    in the pre-4.0 monolith).

    MODULE MAP (load order = numeric prefix):
      00-Foundation.ps1     logging, console UI, prompts, retry, registry
                            read helper, DRY-RUN primitives (-WhatIf engine)
      01-Catalogs.ps1       ALL data: tweaks, app/runtime catalogs, fallback
                            URLs, services, bloatware, dev-tool catalog
      02-Safety.ps1         restore points, tweak/service snapshots,
                            Edge/OneDrive backups, rollback, reset-all
      03-Environment.ps1    winget bootstrap, PATH management,
                            Verify-Environment (dev PATH doctor)
      04-SoftwareEngine.ps1 Smart-Deploy, winget/choco engine, versions,
                            hardware matching, category processor
      05-Startup.ps1        startup program discovery + manager
      06-Tweaks.ps1         data-driven tweak engine + system tweaks,
                            network/power optimization, Edge/OneDrive removal
      07-Maintenance.ps1    SFC/DISM, cache clean, disks, services optimizer
      08-Privacy.ps1        bloatware, telemetry, advertising ID, activity
      09-SystemInfo.ps1     read-only system insight
      10-Office.ps1         Office Deployment Tool suite
      20-Menus.ps1          the entire interactive console experience
      30-GuiDispatcher.ps1  Invoke-GuiTask - the PySide6 frontend contract

    INVOCATION MODES:
      core.ps1                      interactive luxury console menu (elevates)
      core.ps1 -Task <name>         GUI task mode: non-interactive, emits one
                                    final ##PULSE##SUCCESS|... or
                                    ##PULSE##ERROR|... verdict line
      core.ps1 -Task <n> -AppIds a,b   narrows a bulk deploy to ticked apps
      core.ps1 -Task InstallOfficeODT -OfficeSetupPath <p> -OfficeConfigPath <p>
                                     runs the Office Deployment Tool wizard's
                                     resolved setup.exe / configuration.xml
      core.ps1 -Task InstallLocalFile -LocalInstallerPath <p>
                                     runs an installer the Tool Install
                                     Wizard's Path C pointed at
      core.ps1 [...] -WhatIf        DRY-RUN: full simulation, zero mutations

    CHANGELOG v4.0 (Modular Architecture Release):
      - Monolith decomposed into 13 single-responsibility modules; core.ps1
        is now only parameters + elevation + module loader + entry routing.
      - NEW Verify-Environment (task: VerifyEnvironment): audits Git, Python,
        Java, VS Code, GCC, Node and Ollama; auto-repairs missing user-PATH
        entries from known install roots and sets JAVA_HOME when resolvable.
      - NEW -WhatIf dry-run mode across every module: registry writes,
        service changes, deletions, installs and external tools are reported
        as "[WHATIF] ..." lines instead of executing; cache clean measures
        the space it would reclaim. GUI tasks report "[DRY-RUN]" results.
      - All v3.3/v3.4 contracts preserved verbatim: Invoke-GuiTask dispatcher
        (SUCCESS|/ERROR| final line), $Script:NonInteractive safety, tweak &
        service snapshotting, lazy winget bootstrap, data-driven catalogs.
#>

# ============================================================
#  PARAMETERS (MUST BE FIRST)
#  NOTE: $WhatIf is a plain switch by design - no [CmdletBinding()] /
#  SupportsShouldProcess, because the dry-run engine (Test-DryRun in
#  00-Foundation.ps1) must also govern external tools (winget, powercfg,
#  sfc, robocopy...) that ShouldProcess can never reach.
# ============================================================
param(
    [string]$Task,
    [string]$AppIds,
    [string]$OfficeSetupPath,
    [string]$OfficeConfigPath,
    [string]$LocalInstallerPath,
    [switch]$WhatIf
)

# ============================================================
#  ELEVATION (only runs if $Task is empty - i.e., clicked manually)
# ============================================================
if (-not $Task) {
    if ($MyInvocation.InvocationName -ne '.') {
        if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            $ElevArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
            if ($WhatIf) { $ElevArgs += " -WhatIf" }
            Start-Process powershell -ArgumentList $ElevArgs -Verb RunAs
            Exit
        }
    }
}

# UTF-8 output, unconditionally: Windows consoles default to the OEM code
# page (437 on US-English installs, others elsewhere), which renders every
# box-drawing character and glyph this file prints (=,|,check,cross - see
# 00-Foundation.ps1's $Script:Box*/Check/Cross) as mangled question marks
# and garbage - not a cosmetic quirk, a genuinely broken-looking console.
# The GUI's spawned subprocess already sets this (helpers.PowerShellTask
# prepends the same line); this covers the interactive console, which
# never got it and was the real "chaotic UI" culprit, not color choices.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Console styling only makes sense when a human is looking at the console.
# In GUI task mode stdout is a pipe and the console is hidden - skip it.
if (-not $Task) {
    $Host.UI.RawUI.BackgroundColor = "Black"
    $Host.UI.RawUI.ForegroundColor = "Gray"
    Clear-Host
}
$ErrorActionPreference = "Stop"

$Script:ScriptVersion = "6.1"

# When invoked with -Task (i.e. from the GUI), there is no console attached
# for Read-Host to block on. Ask-User, Invoke-WithRetry, Smart-Deploy and
# Open-FallbackUrl all check this flag so they never wait on input that can
# never arrive and never pop windows (browser/Store) mid-silent-run.
# Set BEFORE the modules load so even module top-level code is governed.
$Script:NonInteractive = [bool]$Task

# -WhatIf dry-run flag, honored by every mutation primitive in the modules.
$Script:DryRun = [bool]$WhatIf

# ============================================================
#  MODULE LOADER
#  Dot-sourcing (not Import-Module) is deliberate: every module executes
#  in THIS script scope, preserving the monolith's $Script: semantics.
#  Numeric prefixes define a deterministic load order; functions resolve
#  at call time, so only data/top-level statements depend on it.
# ============================================================
$Script:ModuleRoot = Join-Path $PSScriptRoot "modules"
$LoadingModule = "(none)"
try {
    $ModuleFiles = @(Get-ChildItem -Path $Script:ModuleRoot -Filter "*.ps1" -File -ErrorAction Stop | Sort-Object Name)
    if ($ModuleFiles.Count -eq 0) { throw "No backend modules found in '$Script:ModuleRoot'." }
    foreach ($ModuleFile in $ModuleFiles) {
        $LoadingModule = $ModuleFile.Name
        . $ModuleFile.FullName
    }
} catch {
    # A broken module must never produce silence: honor the GUI contract
    # even when the backend itself cannot come up.
    $LoadError = "Backend module '$LoadingModule' failed to load: $($_.Exception.Message)"
    if ($Task) {
        Write-Output "##PULSE##ERROR|$LoadError"
    } else {
        Write-Host ""
        Write-Host "   FATAL: $LoadError" -ForegroundColor Red
        Write-Host "   Verify that src\backend\modules\ is complete and intact." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
    Exit 1
}

# Global last-resort trap - installed after the modules so it can use the
# foundation's log path and glyphs.
trap {
    Write-Host ""
    Write-Host "   $Script:Cross  UNEXPECTED ERROR: $($_.Exception.Message)" -ForegroundColor Red
    try {
        Add-Content -Path $Script:LogPath -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] UNCAUGHT: $($_.Exception.Message)" -ErrorAction SilentlyContinue
    } catch {}
    Start-Sleep -Seconds 2
    continue
}

# ============================================================
#  TASK ENGINE (GUI mode: executed only if -Task was provided)
# ============================================================
if ($Task) {
    # Belt-and-braces: the flag is already set above, but nothing below may
    # ever block on a console that does not exist.
    $Script:NonInteractive = $true

    if ($Script:DryRun) {
        Write-Host "   [WHATIF] Dry-run mode: simulating '$Task' - no system changes will be made." -ForegroundColor DarkYellow
        Write-Log "WHATIF: GUI task '$Task' started in dry-run mode."
    }

    Invoke-GuiTask -TaskName $Task
    Exit
}

# ============================================================
#  TERMINAL MENU (only if run directly, no params)
# ============================================================
if ($MyInvocation.InvocationName -ne '.') {
    Show-EpicIntro
    do {
        Show-MainMenu
        $Selection = Read-Choice -Prompt "   Select Module [0-6]" -Valid @('0','1','2','3','4','5','6')
        switch ($Selection) {
            "1" { Show-SoftwareManagementMenu }
            "2" { Show-SystemOptimizationMenu }
            "3" { Show-MaintenanceRepairMenu }
            "4" { Show-PrivacySecurityMenu }
            "5" { Show-InformationUtilitiesMenu }
            "6" { Show-SafetyRecoveryMenu }
            "0" {
                if (Ask-User "Exit Pulse" "Closes the tool. Any pending restart will still be offered first.") {
                    Write-Host ""
                    Write-Host "   📊 Session Summary: $($Script:SessionSuccessCount) successes, $($Script:SessionFailCount) failures." -ForegroundColor Cyan
                    Show-RestartReminder
                    Write-Host "   Thank you for using PULSE!" -ForegroundColor Yellow
                    Write-Host "   Exiting in 3 seconds..." -ForegroundColor DarkGray
                    Start-Sleep -Seconds 3
                    Exit
                }
            }
            default {
                Write-Warn "Invalid selection."
                Start-Sleep -Seconds 1
            }
        }
    } while ($true)
}
