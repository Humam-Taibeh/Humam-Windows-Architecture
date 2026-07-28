#
#  PSScriptAnalyzer configuration for the Pulse PowerShell engine.
#
#  Used by .github/workflows/ci.yml and reproducible locally with:
#      Invoke-ScriptAnalyzer -Path src\backend -Recurse -Settings .\PSScriptAnalyzerSettings.psd1
#
#  POLICY: the exclusions below are ARCHITECTURAL, not debt. Each one is a
#  rule whose premise does not hold for this codebase, documented so the
#  list can never quietly become a dumping ground for real findings. A rule
#  that catches a genuine defect must NOT be added here — fix the code.
#
@{
    Severity     = @('Error', 'Warning')

    ExcludeRules = @(
        # -- The engine IS a console application -----------------------
        # Pulse's PowerShell layer paints an interactive coloured TUI
        # (Write-SectionHeader, progress lines, the menu system in
        # 20-Menus.ps1). Write-Host is the correct primitive for that:
        # its output is deliberately NOT part of the object pipeline,
        # which is exactly what keeps it from contaminating the
        # ##PULSE## verdict line the GUI parses off stdout.
        'PSAvoidUsingWriteHost',

        # -- Deliberate cross-module shared state -----------------------
        # Four globals only ($Global:UIWidth, $Global:PanelWidth,
        # $global:WingetAvailable, $global:ChocolateyAvailable). The
        # modules are dot-sourced into one session by core.ps1, so these
        # are console-geometry and tool-availability facts computed once
        # at load and read everywhere. Script scope would not survive the
        # dot-source boundary they are designed to cross.
        'PSAvoidGlobalVars',

        # -- "Unknown" is a first-class state ---------------------------
        # 11-StateProbe.ps1's hard contract is that an unreadable key
        # yields $null (unknown) rather than an exception or a false
        # "not applied". Swallowing the read error IS the behaviour under
        # test; the empty catch is the implementation of a documented
        # invariant, not a forgotten TODO.
        'PSAvoidUsingEmptyCatchBlock',

        # -- Naming: cosmetic only --------------------------------------
        # Plural nouns (Get-InstalledApps) and non-approved verbs read
        # naturally for functions that are never exported as a module and
        # never discovered via Get-Command. Renaming ~23 internal
        # functions would churn every call site for zero behaviour.
        'PSUseSingularNouns',
        'PSUseApprovedVerbs',

        # -- Pulse has its own dry-run mechanism ------------------------
        # -WhatIf is threaded through the engine as $Script:DryRun and
        # honoured by Complete-GuiTask, which reports simulated runs as
        # "[DRY-RUN]". SupportsShouldProcess on each leaf function would
        # duplicate that with a second, competing gate.
        'PSUseShouldProcessForStateChangingFunctions',

        # -- Cross-file dot-sourcing is invisible to the analyzer -------
        # PSSA lints each file alone, so it cannot see that core.ps1's
        # -AppIds/-OfficeSetupPath/-OfficeConfigPath/-LocalInstallerPath
        # parameters are consumed by 30-GuiDispatcher.ps1, or that
        # 01-Catalogs.ps1's $Apps_* / $Runtimes catalogs are read by
        # 04-SoftwareEngine.ps1. Both rules report only false positives
        # here; the real guard for that contract is tests/test_contract.py.
        'PSUseDeclaredVarsMoreThanAssignments',
        'PSReviewUnusedParameter'
    )
}
