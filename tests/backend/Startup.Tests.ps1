#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
.SYNOPSIS
    Pester coverage for startup-item scope preservation (05-Startup.ps1) and
    the health report's startup roll-up (12-HealthReport.ps1).

.DESCRIPTION
    Both invariants here failed SILENTLY before v1.0 — the operation the user
    asked for reported success, and the damage was only visible later, from a
    different account or not at all:

      * SCOPE PRESERVATION — Enable-StartupItem hard-coded the per-user Run
        key and the per-user Startup folder. Disabling an all-users entry
        (HKLM Run, or a shortcut in ProgramData) and re-enabling it therefore
        narrowed it to ONE profile: it still launched for whoever clicked
        re-enable, and stopped launching for every other user on the machine.
        Nothing reported that, because writing to HKCU genuinely succeeded.

      * THE RECOMMENDATION ROLL-UP — Get-StartupRecommendation returns a
        hashtable, and the health report compared it to a string. That is
        always $false, so recommendedDisable was hard-wired to 0 and the
        report's "N startup items recommended for disabling" finding could
        never fire on any machine.

    ISOLATION: nothing here touches a real Run key or a real Startup folder.
    $Script:StartupRunKeyPaths / StartupFolderPaths / StartupDisabledRegPath /
    StartupOriginRegPath / StartupBackupFolder are all redirected into
    HKCU:\Software\PulsePesterTests and a temp directory, so the suite cannot
    disable a program the developer actually relies on at sign-in. The "HKLM"
    stand-in is a second key under the test hive rather than the real HKLM,
    which keeps the whole suite unelevated — what is under test is that the
    RECORDED ORIGIN is honoured, and that logic is hive-agnostic.

.NOTES
    Run:  Invoke-Pester -Path tests\backend
#>

BeforeAll {
    $script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $script:ModuleDir = Join-Path $script:RepoRoot "src\backend\modules"

    # Dot-source order mirrors core.ps1's sorted load. 05 needs 00's
    # Resolve-UserRegPath / Test-DryRun / Write-* vocabulary; 12 needs 05's
    # Get-AllStartupItems and Get-StartupRecommendation.
    . (Join-Path $script:ModuleDir "00-Foundation.ps1")
    . (Join-Path $script:ModuleDir "01-Catalogs.ps1")
    . (Join-Path $script:ModuleDir "05-Startup.ps1")
    . (Join-Path $script:ModuleDir "09-SystemInfo.ps1")
    . (Join-Path $script:ModuleDir "11-StateProbe.ps1")
    . (Join-Path $script:ModuleDir "12-HealthReport.ps1")

    # --- isolation ---------------------------------------------------
    $script:TestRoot = "HKCU:\Software\PulsePesterTests"
    $script:UserRunKey = "$script:TestRoot\Run_User"
    $script:MachineRunKey = "$script:TestRoot\Run_Machine"
    $script:TempBase = Join-Path ([System.IO.Path]::GetTempPath()) "PulsePesterStartup"

    $Script:StartupDisabledRegPath = "$script:TestRoot\DisabledStartup"
    $Script:StartupOriginRegPath = "$Script:StartupDisabledRegPath\_Origins"
    $Script:StartupRunKeyPaths = @($script:UserRunKey, $script:MachineRunKey)
    $Script:StartupFolderPaths = @(
        (Join-Path $script:TempBase "Startup_User"),
        (Join-Path $script:TempBase "Startup_Machine")
    )
    $Script:StartupBackupFolder = Join-Path $script:TempBase "Backup"

    $Script:LogPath = Join-Path ([System.IO.Path]::GetTempPath()) "PulsePesterStartup.log"
    $Script:DryRun = $false

    function Reset-TestState {
        if (Test-Path $script:TestRoot) {
            Remove-Item -Path $script:TestRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -Path $script:UserRunKey -Force -ErrorAction SilentlyContinue | Out-Null
        New-Item -Path $script:MachineRunKey -Force -ErrorAction SilentlyContinue | Out-Null

        if (Test-Path $script:TempBase) {
            Remove-Item -Path $script:TempBase -Recurse -Force -ErrorAction SilentlyContinue
        }
        foreach ($dir in (@($Script:StartupFolderPaths) + $Script:StartupBackupFolder)) {
            New-Item -Path $dir -ItemType Directory -Force -ErrorAction SilentlyContinue | Out-Null
        }
    }
}

AfterAll {
    if (Test-Path $script:TestRoot) {
        Remove-Item -Path $script:TestRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path $script:TempBase) {
        Remove-Item -Path $script:TempBase -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($Script:LogPath -and (Test-Path $Script:LogPath)) {
        Remove-Item -Path $Script:LogPath -Force -ErrorAction SilentlyContinue
    }
}

Describe "Startup item scope preservation (05-Startup.ps1)" {

    BeforeEach {
        Reset-TestState
        $Script:DryRun = $false
        $Script:SessionFailCount = 0
        $Script:SessionSuccessCount = 0
    }

    Context "Test isolation" {
        It "never points at a real Run key or Startup folder" {
            # If this fails, STOP: the suite is about to disable programs the
            # developer actually depends on at sign-in.
            $Script:StartupRunKeyPaths[0] | Should -BeLike "*PulsePesterTests*"
            $Script:StartupRunKeyPaths[1] | Should -BeLike "*PulsePesterTests*"
            $Script:StartupDisabledRegPath | Should -Not -Be "HKCU:\Software\Pulse\DisabledStartup"
            foreach ($folder in $Script:StartupFolderPaths) {
                $folder | Should -Not -BeLike "*Start Menu*"
            }
        }
    }

    Context "The origin ledger" {
        It "records the hive an entry was disabled from" {
            Set-ItemProperty -Path $script:MachineRunKey -Name "AcmeUpdater" -Value "C:\acme.exe" -Force
            $item = [PSCustomObject]@{
                Type = "Registry"; Hive = "HKLM"; RegPath = $script:MachineRunKey
                Name = "AcmeUpdater"; Command = "C:\acme.exe"; Enabled = $true
            }
            Disable-StartupItem -Item $item

            Get-StartupOrigin -Type "Registry" -Name "AcmeUpdater" |
                Should -Be $script:MachineRunKey
        }

        It "keeps the ledger out of the disabled-items list" {
            # The origin record lives in a SUB-KEY precisely so it cannot
            # surface as a phantom startup entry in the GUI.
            Set-ItemProperty -Path $script:MachineRunKey -Name "AcmeUpdater" -Value "C:\acme.exe" -Force
            Disable-StartupItem -Item ([PSCustomObject]@{
                Type = "Registry"; Hive = "HKLM"; RegPath = $script:MachineRunKey
                Name = "AcmeUpdater"; Command = "C:\acme.exe"; Enabled = $true })

            $names = @(Get-DisabledStartupItems | Select-Object -ExpandProperty Name)
            $names | Should -Contain "AcmeUpdater"
            $names | Should -Not -Contain "_Origins"
            $names | Should -Not -Contain "Registry|||AcmeUpdater"
        }

        It "returns null for an entry disabled by a pre-1.0 Pulse" {
            # Legacy rows have no record; callers must fall back, not throw.
            New-Item -Path $Script:StartupDisabledRegPath -Force | Out-Null
            Set-ItemProperty -Path $Script:StartupDisabledRegPath -Name "Legacy" -Value "C:\old.exe" -Force

            Get-StartupOrigin -Type "Registry" -Name "Legacy" | Should -BeNullOrEmpty
        }
    }

    Context "Registry entries restore to their original hive" {
        It "returns an all-users entry to the machine key, not the user key" {
            # THE regression. Before v1.0 this landed in the per-user Run key
            # and silently stopped launching for every other account.
            Set-ItemProperty -Path $script:MachineRunKey -Name "AcmeUpdater" -Value "C:\acme.exe" -Force
            $item = [PSCustomObject]@{
                Type = "Registry"; Hive = "HKLM"; RegPath = $script:MachineRunKey
                Name = "AcmeUpdater"; Command = "C:\acme.exe"; Enabled = $true
            }
            Disable-StartupItem -Item $item
            (Get-Item $script:MachineRunKey).Property | Should -Not -Contain "AcmeUpdater"

            $disabled = Get-DisabledStartupItems |
                Where-Object { $_.Name -eq "AcmeUpdater" } | Select-Object -First 1
            Enable-StartupItem -Item $disabled

            (Get-RegValue -Path $script:MachineRunKey -Name "AcmeUpdater") |
                Should -Be "C:\acme.exe" -Because "an all-users entry must come back for all users"
            (Get-Item $script:UserRunKey).Property |
                Should -Not -Contain "AcmeUpdater" -Because "restoring it per-user is the bug under test"
        }

        It "returns a per-user entry to the user key" {
            Set-ItemProperty -Path $script:UserRunKey -Name "MyApp" -Value "C:\mine.exe" -Force
            $item = [PSCustomObject]@{
                Type = "Registry"; Hive = "HKCU"; RegPath = $script:UserRunKey
                Name = "MyApp"; Command = "C:\mine.exe"; Enabled = $true
            }
            Disable-StartupItem -Item $item
            $disabled = Get-DisabledStartupItems |
                Where-Object { $_.Name -eq "MyApp" } | Select-Object -First 1
            Enable-StartupItem -Item $disabled

            (Get-RegValue -Path $script:UserRunKey -Name "MyApp") | Should -Be "C:\mine.exe"
            (Get-Item $script:MachineRunKey).Property | Should -Not -Contain "MyApp"
        }

        It "clears the origin record once the restore succeeded" {
            Set-ItemProperty -Path $script:MachineRunKey -Name "AcmeUpdater" -Value "C:\acme.exe" -Force
            Disable-StartupItem -Item ([PSCustomObject]@{
                Type = "Registry"; Hive = "HKLM"; RegPath = $script:MachineRunKey
                Name = "AcmeUpdater"; Command = "C:\acme.exe"; Enabled = $true })
            $disabled = Get-DisabledStartupItems |
                Where-Object { $_.Name -eq "AcmeUpdater" } | Select-Object -First 1
            Enable-StartupItem -Item $disabled

            Get-StartupOrigin -Type "Registry" -Name "AcmeUpdater" | Should -BeNullOrEmpty
        }

        It "falls back to the user key for a legacy entry with no record" {
            New-Item -Path $Script:StartupDisabledRegPath -Force | Out-Null
            Set-ItemProperty -Path $Script:StartupDisabledRegPath -Name "Legacy" -Value "C:\old.exe" -Force
            $disabled = Get-DisabledStartupItems |
                Where-Object { $_.Name -eq "Legacy" } | Select-Object -First 1

            Enable-StartupItem -Item $disabled

            (Get-RegValue -Path $script:UserRunKey -Name "Legacy") |
                Should -Be "C:\old.exe" -Because "no record means the pre-1.0 per-user default"
        }

        It "refuses an origin outside the known startup locations" {
            # The ledger is in a user-writable hive and its value is fed to
            # Set-ItemProperty, so a tampered record must not redirect a write.
            $hijack = "$script:TestRoot\Hijacked"
            New-Item -Path $hijack -Force | Out-Null
            # PARENT FIRST. New-Item -Force on an existing registry key
            # RECREATES it, so creating _Origins and then its parent silently
            # deleted the record this test depends on — the assertion below
            # then passed for the wrong reason (no record at all, rather than
            # a rejected one), which is exactly the false green a tampered
            # ledger would hide behind.
            New-Item -Path $Script:StartupDisabledRegPath -Force | Out-Null
            New-Item -Path $Script:StartupOriginRegPath -Force | Out-Null
            Set-ItemProperty -Path $Script:StartupDisabledRegPath -Name "Evil" -Value "C:\evil.exe" -Force
            Set-ItemProperty -Path $Script:StartupOriginRegPath -Name "Registry|||Evil" -Value $hijack -Force

            # Guard the guard: prove the tampered record is actually readable,
            # or the fallback assertion proves nothing.
            Get-StartupOrigin -Type "Registry" -Name "Evil" |
                Should -Be $hijack -Because "the test must actually plant a record to reject"

            Resolve-StartupRestoreTarget -Type "Registry" -Name "Evil" |
                Should -Be $script:UserRunKey -Because "an unrecognised origin must fall back, never be honoured"

            $disabled = Get-DisabledStartupItems |
                Where-Object { $_.Name -eq "Evil" } | Select-Object -First 1
            Enable-StartupItem -Item $disabled
            (Get-Item $hijack).Property | Should -Not -Contain "Evil"
        }

        It "writes nothing under -WhatIf" {
            Set-ItemProperty -Path $script:MachineRunKey -Name "AcmeUpdater" -Value "C:\acme.exe" -Force
            $Script:DryRun = $true
            Disable-StartupItem -Item ([PSCustomObject]@{
                Type = "Registry"; Hive = "HKLM"; RegPath = $script:MachineRunKey
                Name = "AcmeUpdater"; Command = "C:\acme.exe"; Enabled = $true })

            (Get-Item $script:MachineRunKey).Property |
                Should -Contain "AcmeUpdater" -Because "a dry run must not disable anything"
            Get-StartupOrigin -Type "Registry" -Name "AcmeUpdater" |
                Should -BeNullOrEmpty -Because "and must not write an origin record either"
        }
    }

    Context "Startup-folder shortcuts restore to their original folder" {
        It "returns an all-users shortcut to the machine folder" {
            $machineFolder = $Script:StartupFolderPaths[1]
            $shortcut = Join-Path $machineFolder "AcmeAll.lnk"
            Set-Content -LiteralPath $shortcut -Value "stub" -Encoding ASCII

            $item = [PSCustomObject]@{
                Type = "Folder"; Hive = ""; RegPath = $machineFolder
                Name = "AcmeAll.lnk"; Command = $shortcut; Enabled = $true
            }
            Disable-StartupItem -Item $item
            Test-Path -LiteralPath $shortcut | Should -BeFalse

            $disabled = Get-DisabledStartupItems |
                Where-Object { $_.Name -eq "AcmeAll.lnk" } | Select-Object -First 1
            Enable-StartupItem -Item $disabled

            Test-Path -LiteralPath $shortcut |
                Should -BeTrue -Because "an all-users shortcut must return to the all-users folder"
            Test-Path -LiteralPath (Join-Path $Script:StartupFolderPaths[0] "AcmeAll.lnk") |
                Should -BeFalse
        }

        It "returns a per-user shortcut to the user folder" {
            $userFolder = $Script:StartupFolderPaths[0]
            $shortcut = Join-Path $userFolder "Mine.lnk"
            Set-Content -LiteralPath $shortcut -Value "stub" -Encoding ASCII

            Disable-StartupItem -Item ([PSCustomObject]@{
                Type = "Folder"; Hive = ""; RegPath = $userFolder
                Name = "Mine.lnk"; Command = $shortcut; Enabled = $true })
            $disabled = Get-DisabledStartupItems |
                Where-Object { $_.Name -eq "Mine.lnk" } | Select-Object -First 1
            Enable-StartupItem -Item $disabled

            Test-Path -LiteralPath $shortcut | Should -BeTrue
            Test-Path -LiteralPath (Join-Path $Script:StartupFolderPaths[1] "Mine.lnk") | Should -BeFalse
        }

        It "handles a bracketed filename literally" {
            # "Game [2].lnk" is an ordinary filename; -Path would read the
            # brackets as a character class and move nothing.
            $userFolder = $Script:StartupFolderPaths[0]
            $shortcut = Join-Path $userFolder "Game [2].lnk"
            Set-Content -LiteralPath $shortcut -Value "stub" -Encoding ASCII

            Disable-StartupItem -Item ([PSCustomObject]@{
                Type = "Folder"; Hive = ""; RegPath = $userFolder
                Name = "Game [2].lnk"; Command = $shortcut; Enabled = $true })

            Test-Path -LiteralPath $shortcut | Should -BeFalse
            Test-Path -LiteralPath (Join-Path $Script:StartupBackupFolder "Game [2].lnk") | Should -BeTrue
        }
    }
}

Describe "Health report startup roll-up (12-HealthReport.ps1)" {

    BeforeEach {
        Reset-TestState
        $Script:DryRun = $false
    }

    It "counts items the recommendation engine flags for disabling" {
        # The bug: Get-StartupRecommendation returns a HASHTABLE, and the
        # report compared it to the string 'Disable'. Always false, so this
        # count was 0 on every machine and the report's corresponding finding
        # could never fire. Steam is a High-impact entry in
        # $Script:StartupDisableRules.
        Set-ItemProperty -Path $script:UserRunKey -Name "Steam" -Value "C:\steam.exe" -Force

        $report = Get-HealthStartupReport

        $report | Should -Not -BeNullOrEmpty
        $report.recommendedDisable |
            Should -BeGreaterThan 0 -Because "a flagged launcher must be counted, not silently dropped"
    }

    It "does not flag an item the keep rules protect" {
        Set-ItemProperty -Path $script:UserRunKey -Name "SecurityHealth" -Value "C:\Windows\System32\SecurityHealthSystray.exe" -Force

        (Get-HealthStartupReport).recommendedDisable | Should -Be 0
    }

    It "reports totals consistent with the discovered items" {
        Set-ItemProperty -Path $script:UserRunKey -Name "Steam" -Value "C:\steam.exe" -Force
        Set-ItemProperty -Path $script:UserRunKey -Name "SecurityHealth" -Value "C:\sh.exe" -Force

        $report = Get-HealthStartupReport

        $report.total | Should -Be 2
        $report.enabled | Should -Be 2
        $report.recommendedDisable | Should -BeLessOrEqual $report.total
    }
}
