#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
.SYNOPSIS
    Pester coverage for the tweak backup / restore framework (02-Safety.ps1).

.DESCRIPTION
    This is the code users depend on when something has ALREADY gone wrong.
    Until now the whole PowerShell engine was guarded only by a parse check,
    PSScriptAnalyzer and the static contract tests in test_contract.py —
    nothing executed a backend code path and asserted on its behaviour.

    The invariants below are the ones whose failure is SILENT and only
    discovered at the worst possible moment, when a user clicks
    "Reset All Tweaks" and gets something other than their original setting:

      * first-write-wins — applying a tweak twice must not overwrite the
        snapshot with the already-tweaked value, or "restore" restores the
        tweak itself and the original is gone forever;
      * __NOTSET__ — a value that did not exist before must be REMOVED on
        restore, not recreated from a hardcoded default, or Pulse invents
        settings the user never had;
      * -WhatIf writes nothing — a dry run that snapshots is itself a
        mutation, which is the one thing dry run promises not to be.

    ISOLATION: every test redirects $Script:TweaksBackupRegPath to a
    throwaway key under HKCU:\Software\PulsePesterTests. The real path
    (HKCU:\Software\Pulse\TweakBackups) holds the user's actual rollback
    data — a test suite that wrote there could destroy the very safety net
    it is meant to be protecting. Nothing here needs elevation, and every
    key created is removed afterwards.

.NOTES
    Run:  Invoke-Pester -Path tests\backend
#>

BeforeAll {
    $script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $script:ModuleDir = Join-Path $script:RepoRoot "src\backend\modules"

    # The engine is a set of dot-sourced modules sharing one scope, so the
    # dependency order here mirrors core.ps1's own sorted load.
    #
    # 07-Maintenance is loaded because Backup-ServiceState (in 02-Safety)
    # calls Get-ServiceState, which is defined there. That is a genuine
    # backwards dependency across the numbering, and it is fine in
    # production only because PowerShell resolves function calls at
    # invocation time rather than at load time — by the time any service is
    # backed up, core.ps1 has dot-sourced every module. Loading 02 alone
    # here would fail for a reason that never occurs in the real engine.
    . (Join-Path $script:ModuleDir "00-Foundation.ps1")
    . (Join-Path $script:ModuleDir "02-Safety.ps1")
    . (Join-Path $script:ModuleDir "07-Maintenance.ps1")

    # --- isolation ---------------------------------------------------
    $script:TestRoot = "HKCU:\Software\PulsePesterTests"
    $Script:TweaksBackupRegPath = "$script:TestRoot\TweakBackups"
    $script:TargetKey = "$script:TestRoot\Target"

    # Keep the real Pulse log out of it too.
    $Script:LogPath = Join-Path ([System.IO.Path]::GetTempPath()) "PulsePesterTests.log"
    $Script:DryRun = $false

    function Reset-TestHive {
        if (Test-Path $script:TestRoot) {
            Remove-Item -Path $script:TestRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -Path $script:TargetKey -Force -ErrorAction SilentlyContinue | Out-Null
    }
}

AfterAll {
    if (Test-Path $script:TestRoot) {
        Remove-Item -Path $script:TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($Script:LogPath -and (Test-Path $Script:LogPath)) {
        Remove-Item -Path $Script:LogPath -Force -ErrorAction SilentlyContinue
    }
}

Describe "Tweak backup and restore framework (02-Safety.ps1)" {

    # Pester 6 rejects a BeforeEach at the container root, so the whole
    # suite lives under one Describe and the per-test reset sits here.
    BeforeEach {
        Reset-TestHive
        $Script:DryRun = $false
        $Script:SessionFailCount = 0
    }

Context "Test isolation" {
    It "never points at the user's real backup hive" {
        # If this ever fails, STOP: the suite is about to overwrite the
        # rollback data the whole feature exists to protect.
        $Script:TweaksBackupRegPath | Should -Not -Be "HKCU:\Software\Pulse\TweakBackups"
        $Script:TweaksBackupRegPath | Should -BeLike "*PulsePesterTests*"
    }

    It "leaves the real backup hive untouched" {
        $real = "HKCU:\Software\Pulse\TweakBackups"
        $before = if (Test-Path $real) { (Get-Item $real).Property.Count } else { -1 }
        Backup-OriginalRegValue -TweakKey "Probe" -Path $script:TargetKey -Name "Anything"
        $after = if (Test-Path $real) { (Get-Item $real).Property.Count } else { -1 }
        $after | Should -Be $before
    }
}

Context "Backup-OriginalRegValue" {
    It "snapshots an existing value" {
        Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 7 -Force
        Backup-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting"

        $stored = Get-RegValue -Path $Script:TweaksBackupRegPath -Name "MyTweak--Setting"
        $stored | Should -Be "7"
    }

    It "records a missing value as the __NOTSET__ sentinel" {
        # The distinction that stops Pulse inventing settings on restore.
        Backup-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Absent"

        $stored = Get-RegValue -Path $Script:TweaksBackupRegPath -Name "MyTweak--Absent"
        $stored | Should -Be "__NOTSET__"
    }

    It "is first-write-wins and never overwrites an existing snapshot" {
        # THE critical one. Applying a tweak twice must not capture the
        # tweaked value as though it were the user's original.
        Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 1 -Force
        Backup-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting"

        Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 999 -Force
        Backup-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting"

        $stored = Get-RegValue -Path $Script:TweaksBackupRegPath -Name "MyTweak--Setting"
        $stored | Should -Be "1" -Because "the second snapshot must not clobber the user's real original"
    }

    It "writes nothing under -WhatIf" {
        $Script:DryRun = $true
        Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 5 -Force
        Backup-OriginalRegValue -TweakKey "DryTweak" -Path $script:TargetKey -Name "Setting"

        Get-RegValue -Path $Script:TweaksBackupRegPath -Name "DryTweak--Setting" |
            Should -BeNullOrEmpty -Because "a dry run that writes a snapshot is itself a mutation"
    }

    It "sanitises separators out of the backup value name" {
        Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 3 -Force
        Backup-OriginalRegValue -TweakKey "Has Space:And\Slash" -Path $script:TargetKey -Name "Setting"

        $names = (Get-Item $Script:TweaksBackupRegPath).Property
        $names | Should -Contain "Has_Space_And_Slash--Setting"
    }

    It "keeps separate snapshots for separate tweak keys" {
        Set-ItemProperty -Path $script:TargetKey -Name "Shared" -Value 10 -Force
        Backup-OriginalRegValue -TweakKey "TweakA" -Path $script:TargetKey -Name "Shared"
        Set-ItemProperty -Path $script:TargetKey -Name "Shared" -Value 20 -Force
        Backup-OriginalRegValue -TweakKey "TweakB" -Path $script:TargetKey -Name "Shared"

        Get-RegValue -Path $Script:TweaksBackupRegPath -Name "TweakA--Shared" | Should -Be "10"
        Get-RegValue -Path $Script:TweaksBackupRegPath -Name "TweakB--Shared" | Should -Be "20"
    }
}

Context "Restore-OriginalRegValue" {
    It "completes a full backup -> tweak -> restore round trip" {
        Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 1 -Force
        Backup-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting"
        Set-RegValue -Path $script:TargetKey -Name "Setting" -Value 0 -Type DWord

        $ok = Restore-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting"

        $ok | Should -BeTrue
        (Get-RegValue -Path $script:TargetKey -Name "Setting") | Should -Be 1
    }

    It "REMOVES a value that did not exist before the tweak" {
        # __NOTSET__ round trip: Pulse must not leave behind a setting the
        # user never had, even a 'default-looking' one.
        Backup-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Invented"
        Set-RegValue -Path $script:TargetKey -Name "Invented" -Value 1 -Type DWord
        (Get-RegValue -Path $script:TargetKey -Name "Invented") | Should -Be 1

        $ok = Restore-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey `
                -Name "Invented" -DefaultIfMissing "1"

        $ok | Should -BeTrue
        (Get-RegValue -Path $script:TargetKey -Name "Invented") |
            Should -BeNullOrEmpty -Because "__NOTSET__ means remove, never write the default"
    }

    It "falls back to DefaultIfMissing when no snapshot exists" {
        Set-RegValue -Path $script:TargetKey -Name "Orphan" -Value 0 -Type DWord

        $ok = Restore-OriginalRegValue -TweakKey "NeverBackedUp" -Path $script:TargetKey `
                -Name "Orphan" -DefaultIfMissing "1"

        $ok | Should -BeTrue
        (Get-RegValue -Path $script:TargetKey -Name "Orphan") | Should -Be 1
    }

    It "reports failure when there is neither a snapshot nor a default" {
        # Must be $false, not a silent $true — Reset-AllTweaksToDefaults
        # gates its green 'reverted' line on this exact return value.
        $ok = Restore-OriginalRegValue -TweakKey "NeverBackedUp" -Path $script:TargetKey -Name "Nothing"
        $ok | Should -BeFalse
    }

    It "does not invent a value when it reports failure" {
        Restore-OriginalRegValue -TweakKey "NeverBackedUp" -Path $script:TargetKey -Name "Nothing" | Out-Null
        (Get-RegValue -Path $script:TargetKey -Name "Nothing") | Should -BeNullOrEmpty
    }

    It "restores string-typed values without coercing them" {
        # MouseAccel's thresholds are REG_SZ; restoring them as DWord would
        # silently change their type and break the control panel UI.
        Set-ItemProperty -Path $script:TargetKey -Name "MouseSpeed" -Value "1" -Type String -Force
        Backup-OriginalRegValue -TweakKey "MouseAccel" -Path $script:TargetKey -Name "MouseSpeed"
        Set-RegValue -Path $script:TargetKey -Name "MouseSpeed" -Value "0" -Type String

        $ok = Restore-OriginalRegValue -TweakKey "MouseAccel" -Path $script:TargetKey `
                -Name "MouseSpeed" -DefaultIfMissing "1" -Type String

        $ok | Should -BeTrue
        (Get-RegValue -Path $script:TargetKey -Name "MouseSpeed") | Should -Be "1"
        (Get-Item $script:TargetKey).GetValueKind("MouseSpeed") | Should -Be "String"
    }

    It "is idempotent — restoring twice lands on the same value" {
        Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 1 -Force
        Backup-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting"
        Set-RegValue -Path $script:TargetKey -Name "Setting" -Value 0 -Type DWord

        Restore-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting" | Out-Null
        Restore-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting" | Out-Null

        (Get-RegValue -Path $script:TargetKey -Name "Setting") | Should -Be 1
    }

    It "survives a target key that no longer exists" {
        Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 1 -Force
        Backup-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting"
        Remove-Item -Path $script:TargetKey -Recurse -Force

        { Restore-OriginalRegValue -TweakKey "MyTweak" -Path $script:TargetKey -Name "Setting" } |
            Should -Not -Throw
    }
}

Context "Snapshot failure is surfaced, not swallowed" {
    It "reports a failed snapshot instead of failing silently" {
        # A silent snapshot failure means Reset All Tweaks later has nothing
        # to restore, with no warning at either point — the exact defect the
        # Write-ErrorX in Backup-OriginalRegValue's catch block was added to
        # fix. Forced deterministically by pointing the backup hive at a
        # provider drive that does not exist, so New-Item cannot succeed on
        # any machine, elevated or not.
        $goodPath = $Script:TweaksBackupRegPath
        $Script:TweaksBackupRegPath = "PulseNoSuchDrive:\Backups"
        $Script:SessionFailCount = 0
        try {
            Set-ItemProperty -Path $script:TargetKey -Name "Setting" -Value 1 -Force
            # 2>$null: the provider's own non-terminating "drive not found"
            # write is expected noise here, not a test signal.
            Backup-OriginalRegValue -TweakKey "Bad" -Path $script:TargetKey -Name "Setting" 2>$null
        } finally {
            $Script:TweaksBackupRegPath = $goodPath
        }

        $Script:SessionFailCount | Should -BeGreaterThan 0 -Because "losing rollback capability must be reported when it happens, not discovered at reset time"
    }
}

Context "Backup-ServiceState" {
    It "records a service's start type" {
        $svc = Get-Service | Where-Object { $_.Status -eq 'Running' } | Select-Object -First 1
        $svc | Should -Not -BeNullOrEmpty -Because "the test host must have some running service"

        $Script:ServicesBackupRegPath = "$script:TestRoot\ServiceBackups"
        Backup-ServiceState -Name $svc.Name

        $stored = Get-RegValue -Path $Script:ServicesBackupRegPath -Name $svc.Name
        $stored | Should -Not -BeNullOrEmpty
    }

    It "is first-write-wins for services too" {
        $svc = Get-Service | Where-Object { $_.Status -eq 'Running' } | Select-Object -First 1
        $Script:ServicesBackupRegPath = "$script:TestRoot\ServiceBackups"

        Backup-ServiceState -Name $svc.Name
        $first = Get-RegValue -Path $Script:ServicesBackupRegPath -Name $svc.Name

        Set-ItemProperty -Path $Script:ServicesBackupRegPath -Name $svc.Name -Value "Tampered" -Force
        Backup-ServiceState -Name $svc.Name
        $second = Get-RegValue -Path $Script:ServicesBackupRegPath -Name $svc.Name

        $second | Should -Be "Tampered" -Because "an existing service snapshot must not be re-captured"
        $first | Should -Not -BeNullOrEmpty
    }
}
}
