#Requires -Modules Pester
<#
    16-ContextMenu.ps1 — the snapshot/restore round trip.

    This is the test that makes the Context Menu Manager safe to ship. It
    was the highest-risk item on the roadmap for one reason: it writes to
    a machine-scope registry key that decides whether shell extensions
    load, so "restore" has to mean EXACTLY the state the machine started
    in — not approximately, and not "the last change undone".

    Every test here redirects the module's two registry paths to a
    throwaway HKCU subtree, so nothing touches the real block list.
#>

BeforeAll {
    $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    . (Join-Path $Root 'src/backend/modules/00-Foundation.ps1')
    . (Join-Path $Root 'src/backend/modules/16-ContextMenu.ps1')

    $Script:TestRoot    = 'HKCU:\Software\PulsePesterContextMenu'
    $Script:ShellBlockedKey   = "$Script:TestRoot\Blocked"
    $Script:ContextMenuBackup = "$Script:TestRoot\Backup"

    function Reset-TestKeys {
        if (Test-Path $Script:TestRoot) {
            Remove-Item -Path $Script:TestRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -Path $Script:ShellBlockedKey -Force | Out-Null
    }

    function Get-BlockedNow {
        $item = Get-Item -Path $Script:ShellBlockedKey -ErrorAction SilentlyContinue
        if (-not $item) { return @() }
        return @($item.GetValueNames() | Where-Object { $_ } | Sort-Object)
    }

    function Add-PreExistingBlock {
        param([string]$Clsid)
        New-ItemProperty -Path $Script:ShellBlockedKey -Name $Clsid -Value "" `
            -PropertyType String -Force | Out-Null
    }

    $Script:ClsidA = '{11111111-1111-1111-1111-111111111111}'
    $Script:ClsidB = '{22222222-2222-2222-2222-222222222222}'
    $Script:ClsidC = '{33333333-3333-3333-3333-333333333333}'
}

AfterAll {
    if (Test-Path $Script:TestRoot) {
        Remove-Item -Path $Script:TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Describe 'Snapshot' {
    BeforeEach { Reset-TestKeys }

    It 'captures the block list exactly as it found it' {
        Add-PreExistingBlock $Script:ClsidA
        Add-PreExistingBlock $Script:ClsidB

        Save-ContextMenuSnapshot | Should -BeTrue

        $backup = Get-Item -Path $Script:ContextMenuBackup
        $names = @($backup.GetValueNames() | Where-Object { $_ -ne '_PulseSnapshotTaken' })
        $names | Should -HaveCount 2
        $names | Should -Contain $Script:ClsidA.ToUpperInvariant()
        $names | Should -Contain $Script:ClsidB.ToUpperInvariant()
    }

    It 'is taken ONCE and never overwritten by a later change' {
        # The property that makes "Restore All" mean "back to how it was
        # before Pulse" rather than "undo the most recent toggle".
        Add-PreExistingBlock $Script:ClsidA
        Save-ContextMenuSnapshot | Should -BeTrue

        Add-PreExistingBlock $Script:ClsidB
        Save-ContextMenuSnapshot | Should -BeTrue   # no-op, already exists

        $backup = Get-Item -Path $Script:ContextMenuBackup
        $names = @($backup.GetValueNames() | Where-Object { $_ -ne '_PulseSnapshotTaken' })
        $names | Should -HaveCount 1
        $names | Should -Contain $Script:ClsidA.ToUpperInvariant()
    }

    It 'records an EMPTY block list distinguishably from no snapshot' {
        # A machine that had nothing blocked must still be restorable.
        Save-ContextMenuSnapshot | Should -BeTrue
        Test-Path $Script:ContextMenuBackup | Should -BeTrue
        (Get-Item $Script:ContextMenuBackup).GetValueNames() |
            Should -Contain '_PulseSnapshotTaken'
    }
}

Describe 'Restore' {
    BeforeEach { Reset-TestKeys }

    It 'removes every block Pulse added and keeps the ones it found' {
        Add-PreExistingBlock $Script:ClsidA          # pre-existing
        Save-ContextMenuSnapshot | Should -BeTrue
        Add-PreExistingBlock $Script:ClsidB          # added "by Pulse"
        Add-PreExistingBlock $Script:ClsidC

        (Get-BlockedNow) | Should -HaveCount 3
        Restore-PulseContextMenus | Should -BeTrue

        $after = Get-BlockedNow
        $after | Should -HaveCount 1
        $after | Should -Contain $Script:ClsidA
    }

    It 'restores a block that existed before Pulse and was removed since' {
        Add-PreExistingBlock $Script:ClsidA
        Save-ContextMenuSnapshot | Should -BeTrue
        Remove-ItemProperty -Path $Script:ShellBlockedKey -Name $Script:ClsidA -Force

        (Get-BlockedNow) | Should -HaveCount 0
        Restore-PulseContextMenus | Should -BeTrue
        (Get-BlockedNow) | Should -Contain $Script:ClsidA.ToUpperInvariant()
    }

    It 'returns the block list to a byte-identical state (round trip)' {
        Add-PreExistingBlock $Script:ClsidA
        Add-PreExistingBlock $Script:ClsidB
        $before = Get-BlockedNow

        Save-ContextMenuSnapshot | Should -BeTrue
        Add-PreExistingBlock $Script:ClsidC
        Remove-ItemProperty -Path $Script:ShellBlockedKey -Name $Script:ClsidB -Force
        (Get-BlockedNow) | Should -Not -Be $before

        Restore-PulseContextMenus | Should -BeTrue
        (Get-BlockedNow) | Should -Be $before
    }

    It 'refuses, and says so, when no snapshot exists' {
        $before = $Script:SessionFailCount
        Restore-PulseContextMenus | Should -BeFalse
        $Script:SessionFailCount | Should -BeGreaterThan $before
    }

    It 'restores an empty block list without inventing entries' {
        Save-ContextMenuSnapshot | Should -BeTrue       # nothing blocked
        Add-PreExistingBlock $Script:ClsidA
        Restore-PulseContextMenus | Should -BeTrue
        (Get-BlockedNow) | Should -HaveCount 0
    }
}

Describe 'Toggle guards' {
    BeforeEach { Reset-TestKeys }

    It 'rejects a malformed CLSID rather than writing it' {
        $before = $Script:SessionFailCount
        Set-PulseContextMenuState -Clsid 'not-a-clsid' -Enabled $false | Should -BeFalse
        $Script:SessionFailCount | Should -BeGreaterThan $before
        (Get-BlockedNow) | Should -HaveCount 0
    }

    It 'refuses a CLSID that is not an allowlisted handler' {
        # The GUI decides what to OFFER; this decides what to DO. A task
        # that trusted its caller would be one malformed argument away
        # from blocking an arbitrary shell extension.
        $before = $Script:SessionFailCount
        Set-PulseContextMenuState -Clsid $Script:ClsidA -Enabled $false | Should -BeFalse
        $Script:SessionFailCount | Should -BeGreaterThan $before
        (Get-BlockedNow) | Should -HaveCount 0
    }

    It 'writes nothing at all in -WhatIf mode' {
        # The CLSID has to pass the allowlist guard to reach the dry-run
        # branch at all — the guard runs FIRST, deliberately, so a dry run
        # simulates the same decision the real run would make. Mocking the
        # name resolver is what makes an arbitrary test CLSID look like an
        # allowlisted handler without writing to the real HKCR.
        Mock Get-ShellExtensionName {
            [PSCustomObject]@{ name = '7-Zip Shell Extension'; module = 'C:\7z.dll' }
        }
        $Script:DryRun = $true
        try {
            Set-PulseContextMenuState -Clsid $Script:ClsidA -Enabled $false | Should -BeTrue
            # BOTH halves matter: a dry run that took the snapshot would
            # itself be a mutation, which is the one thing it promises not
            # to be.
            (Get-BlockedNow) | Should -HaveCount 0
            Test-Path $Script:ContextMenuBackup | Should -BeFalse
        } finally {
            $Script:DryRun = $false
        }
    }

    It 'blocks and unblocks an allowlisted handler for real' {
        Mock Get-ShellExtensionName {
            [PSCustomObject]@{ name = '7-Zip Shell Extension'; module = 'C:\7z.dll' }
        }
        Set-PulseContextMenuState -Clsid $Script:ClsidA -Enabled $false | Should -BeTrue
        (Get-BlockedNow) | Should -Contain $Script:ClsidA

        Set-PulseContextMenuState -Clsid $Script:ClsidA -Enabled $true | Should -BeTrue
        (Get-BlockedNow) | Should -HaveCount 0
    }

    It 'snapshots before the FIRST change and not again' {
        Mock Get-ShellExtensionName {
            [PSCustomObject]@{ name = '7-Zip Shell Extension'; module = 'C:\7z.dll' }
        }
        Set-PulseContextMenuState -Clsid $Script:ClsidA -Enabled $false | Should -BeTrue
        Test-Path $Script:ContextMenuBackup | Should -BeTrue
        $taken = (Get-ItemProperty -LiteralPath $Script:ContextMenuBackup)._PulseSnapshotTaken

        Set-PulseContextMenuState -Clsid $Script:ClsidA -Enabled $true | Should -BeTrue
        (Get-ItemProperty -LiteralPath $Script:ContextMenuBackup)._PulseSnapshotTaken |
            Should -Be $taken
    }
}

Describe 'Allowlist' {
    It 'matches on the handler key name' {
        (Resolve-ContextMenuAllowEntry -KeyName '7-Zip' -FriendlyName '') |
            Should -Not -BeNullOrEmpty
    }

    It 'matches on the resolved friendly name' {
        (Resolve-ContextMenuAllowEntry -KeyName 'xyz' -FriendlyName 'TortoiseGit shell') |
            Should -Not -BeNullOrEmpty
    }

    It 'does NOT match an unknown handler' {
        (Resolve-ContextMenuAllowEntry -KeyName 'AcmeAntivirusHook' -FriendlyName 'Acme Scanner') |
            Should -BeNullOrEmpty
    }
}
