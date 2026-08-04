#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
.SYNOPSIS
    Pester coverage for the v10.3 Update Center scan (04-SoftwareEngine.ps1)
    and the startup optimizer's safety tier (05-Startup.ps1).

.DESCRIPTION
    THE WINGET TABLE PARSER. `winget upgrade` prints TWO tables, not one:
    the main list, then "The following packages have an upgrade available,
    but require explicit targeting for upgrade:" followed by a second table
    with its OWN column widths, because winget re-measures them per table.
    The parser locked onto the first header and sliced every later line at
    those offsets, which had two consequences on a real machine:

      * the packages in the second table were mangled or lost outright, and
      * the introducing SENTENCE was itself sliced into a phantom package.
        That reached the Update Center as a checkable row reading
        "The following packages have an upgrade avail" -> "r upgrade:",
        which the user could tick and try to install.

    Both are reproduced verbatim below from captured winget output.

    THE SAFETY TIER. Until v10.3 the recommendation engine checked its
    DISABLE patterns before its keep patterns. Any disable pattern that
    happened to match an audio helper, an input driver or a security agent
    therefore recommended disabling it, and the keep rule written
    specifically to protect that component was never consulted. The two
    mistakes do not cost the same: over-recommending a game launcher wastes
    nothing, while telling someone to disable their audio stack breaks the
    machine in a way the row's own wording gives no hint of.

.NOTES
    Run:  Invoke-Pester -Path tests\backend
#>

BeforeAll {
    $script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $script:ModuleDir = Join-Path $script:RepoRoot "src\backend\modules"

    . (Join-Path $script:ModuleDir "00-Foundation.ps1")
    . (Join-Path $script:ModuleDir "01-Catalogs.ps1")
    . (Join-Path $script:ModuleDir "04-SoftwareEngine.ps1")
    . (Join-Path $script:ModuleDir "05-Startup.ps1")

    # Captured from `winget upgrade --include-unknown` on a real machine.
    # Column alignment is load-bearing - this is a fixed-width format and
    # re-indenting it would silently change what is under test.
    $script:TwoTableOutput = @(
        'Name                                        Id                           Version   Available Source'
        '---------------------------------------------------------------------------------------------------'
        'AnyDesk                                     AnyDesk.AnyDesk              ad 9.7.12 9.7.13    winget'
        'Epic Online Services                        EpicGames.EpicOnlineServices 4.2.1     4.3.1     winget'
        'Java(TM) SE Development Kit 25.0.1 (64-bit) Oracle.JDK.26                25.0.1.0  26.0.2.0  winget'
        'Node.js                                     OpenJS.NodeJS.LTS            24.18.1   24.19.0   winget'
        '5 upgrades available.'
        ''
        'The following packages have an upgrade available, but require explicit targeting for upgrade:'
        'Name    Id              Version  Available Source'
        '-------------------------------------------------'
        'Discord Discord.Discord 1.0.9249 1.0.9251  winget'
    )
}

Describe "winget table parsing (ConvertFrom-WingetUpgradeTable)" {

    It "reads every package from BOTH tables" {
        $items = @(ConvertFrom-WingetUpgradeTable -Raw $script:TwoTableOutput)
        $ids = @($items | ForEach-Object { $_.Id })

        $ids | Should -Contain 'AnyDesk.AnyDesk'
        $ids | Should -Contain 'EpicGames.EpicOnlineServices'
        $ids | Should -Contain 'Oracle.JDK.26'
        $ids | Should -Contain 'OpenJS.NodeJS.LTS'
        $ids | Should -Contain 'Discord.Discord' -Because `
            "the second table's packages have real upgrades and must not be dropped"
        $items.Count | Should -Be 5
    }

    It "re-reads the column offsets from the SECOND header" {
        # Discord's row is only parsed correctly if the parser switched to
        # the narrower table's offsets. Sliced at the first table's offsets
        # its fields come out shifted or empty.
        $discord = @(ConvertFrom-WingetUpgradeTable -Raw $script:TwoTableOutput) |
            Where-Object { $_.Id -eq 'Discord.Discord' }

        $discord.Name             | Should -Be 'Discord'
        $discord.CurrentVersion   | Should -Be '1.0.9249'
        $discord.AvailableVersion | Should -Be '1.0.9251'
    }

    It "never turns winget's prose into a package" {
        $items = @(ConvertFrom-WingetUpgradeTable -Raw $script:TwoTableOutput)

        foreach ($item in $items) {
            # A real winget id is a single token. The phantom row carried
            # "able, but require explicit ta" as its Id.
            $item.Id | Should -Not -Match '\s' -Because `
                "an id containing whitespace means a prose line was sliced as data"
            $item.Name | Should -Not -Match 'following packages'
        }
    }

    It "keeps a name that legitimately contains spaces" {
        # The reason the parser slices by offset instead of splitting on
        # whitespace in the first place.
        $jdk = @(ConvertFrom-WingetUpgradeTable -Raw $script:TwoTableOutput) |
            Where-Object { $_.Id -eq 'Oracle.JDK.26' }

        $jdk.Name | Should -Be 'Java(TM) SE Development Kit 25.0.1 (64-bit)'
    }

    It "returns nothing for output with no table at all" {
        @(ConvertFrom-WingetUpgradeTable -Raw @(
            'No installed package found matching input criteria.')).Count |
            Should -Be 0
    }

    It "returns nothing for empty or null input" {
        @(ConvertFrom-WingetUpgradeTable -Raw @()).Count   | Should -Be 0
        @(ConvertFrom-WingetUpgradeTable -Raw $null).Count | Should -Be 0
    }
}

Describe "Upgrade detection (Test-RealUpgradeAvailable)" {

    It "treats a blank Available column as 'already current'" {
        Test-RealUpgradeAvailable -Current '1.0' -Available ''    | Should -BeFalse
        Test-RealUpgradeAvailable -Current '1.0' -Available '   ' | Should -BeFalse
    }

    It "does not offer an upgrade to 'Unknown'" {
        # winget prints a literal Unknown when it cannot resolve a version.
        # Rendering that as an available update fills the Update Center with
        # rows that upgrade to nothing.
        Test-RealUpgradeAvailable -Current '1.0' -Available 'Unknown' | Should -BeFalse
    }

    It "does not offer an upgrade to the installed version" {
        Test-RealUpgradeAvailable -Current '9.7.13' -Available '9.7.13' | Should -BeFalse
    }

    It "reports a genuine pending upgrade" {
        Test-RealUpgradeAvailable -Current '9.7.12' -Available '9.7.13' | Should -BeTrue
    }
}

Describe "Deep installed-program inventory (Get-InstalledProgramInventory)" {

    It "reads all four Uninstall hives, 32-bit and 64-bit, machine and user" {
        $paths = @($Script:UninstallKeyPaths | ForEach-Object { $_.Path })

        $paths | Should -Contain 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
        $paths | Should -Contain 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
        $paths | Should -Contain 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
        $paths | Should -Contain 'HKCU:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    }

    It "distinguishes the 32-bit and 64-bit machine views" {
        # A 32-bit PowerShell sees WOW6432Node AS the plain path, so listing
        # only one and trusting the redirector would enumerate the same set
        # twice on one host and miss half of it on the other.
        $machine = @($Script:UninstallKeyPaths | Where-Object { $_.Scope -eq 'Machine' })
        @($machine | ForEach-Object { $_.Arch }) | Should -Contain 'X64'
        @($machine | ForEach-Object { $_.Arch }) | Should -Contain 'X86'
    }

    It "returns real entries carrying the fields the scan joins on" {
        # Read-only against the live machine: every developer box has some
        # software installed, and this asserts the SHAPE, not the contents.
        $inventory = @(Get-InstalledProgramInventory)
        $inventory.Count | Should -BeGreaterThan 0

        $first = $inventory[0]
        $first.Name      | Should -Not -BeNullOrEmpty
        $first.WingetKey | Should -Not -BeNullOrEmpty
        # The join key must be in winget's own ARP\... / MSIX\... fallback-id
        # form, or correlating the inventory against `winget list` silently
        # matches nothing and every program reports "no update source".
        $first.WingetKey | Should -Match '^(ARP\\(Machine|User)\\(X64|X86)\\|MSIX\\)'
    }

    It "hides the entries Windows itself hides" {
        # ARP visibility rules. Without them roughly a third of these keys
        # are MSI patch records and driver payloads, which would bury the
        # user's actual software and inflate every count in the report.
        $names = @(Get-InstalledProgramInventory | ForEach-Object { $_.Name })
        foreach ($name in $names) {
            $name | Should -Not -BeNullOrEmpty
        }

        $raw = @(Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue).Count
        $kept = @(Get-InstalledProgramInventory | Where-Object { $_.Scope -eq 'Machine' -and $_.Arch -eq 'X64' }).Count
        $kept | Should -BeLessOrEqual $raw
    }
}

Describe "Startup safety tier (Get-StartupRecommendation)" {

    Context "protected components are never recommended for disabling" {
        # Name / command pairs modelled on what these actually register as.
        $cases = @(
            @{ Label = 'Realtek audio service'; Name = 'RtkAudUService'; Command = 'C:\Windows\System32\RtkAudUService64.exe' }
            @{ Label = 'Realtek control panel'; Name = 'RTHDVCPL';       Command = 'C:\Program Files\Realtek\Audio\HDA\RtkNGUI64.exe' }
            @{ Label = 'Nahimic audio';         Name = 'NahimicSvc';     Command = 'C:\Program Files\Nahimic\NahimicSvc64.exe' }
            @{ Label = 'Waves MaxxAudio';       Name = 'WavesSvc';       Command = 'C:\Program Files\Waves\MaxxAudio\WavesSvc64.exe' }
            @{ Label = 'Dolby audio';           Name = 'DolbyDAX';       Command = 'C:\Program Files\Dolby\DolbyDAX2\DolbyDAX2API.exe' }
            @{ Label = 'Windows Security';      Name = 'SecurityHealth'; Command = 'C:\Windows\System32\SecurityHealthSystray.exe' }
            @{ Label = 'Windows Defender';      Name = 'WindowsDefender'; Command = 'C:\Program Files\Windows Defender\MSASCui.exe' }
            @{ Label = 'Malwarebytes';          Name = 'Malwarebytes';   Command = 'C:\Program Files\Malwarebytes\mbam.exe' }
            @{ Label = 'IME / text input';      Name = 'ctfmon';         Command = 'C:\Windows\System32\ctfmon.exe' }
            @{ Label = 'Synaptics touchpad';    Name = 'SynTPEnh';       Command = 'C:\Program Files\Synaptics\SynTP\SynTPEnh.exe' }
            @{ Label = 'Wacom tablet';          Name = 'WacomTablet';    Command = 'C:\Program Files\Tablet\Wacom\Wacom_Tablet.exe' }
        )

        It "keeps <Label>" -ForEach $cases {
            $rec = Get-StartupRecommendation -Item ([PSCustomObject]@{
                Name = $Name; Command = $Command })

            $rec.Recommendation | Should -Be 'Keep' -Because `
                "$Label is system-critical and must never be recommended for disabling"
            $rec.Protected | Should -BeTrue
        }
    }

    Context "the protected tier outranks a matching disable rule" {

        It "protects an audio helper whose name also matches a disable pattern" {
            # 'Nahimic' is protected; 'discord' is a disable pattern. A
            # single entry matching both must come out Keep, because the
            # protected tier is checked first and is absolute. Under the old
            # ordering the disable rule won and the audio helper was flagged.
            $rec = Get-StartupRecommendation -Item ([PSCustomObject]@{
                Name    = 'NahimicSvc'
                Command = 'C:\Program Files\Nahimic\discord-overlay-plugin.exe'
            })

            $rec.Recommendation | Should -Be 'Keep'
            $rec.Protected      | Should -BeTrue
        }

        It "still recommends disabling an ordinary heavy launcher" {
            # The safety tier must not have made the engine toothless.
            $rec = Get-StartupRecommendation -Item ([PSCustomObject]@{
                Name = 'Steam'; Command = 'C:\Program Files (x86)\Steam\steam.exe' })

            $rec.Recommendation | Should -Be 'Disable'
            $rec.Protected      | Should -BeFalse
            $rec.Impact         | Should -Be 'High'
        }

        It "still leaves an unrecognised publisher for review" {
            $rec = Get-StartupRecommendation -Item ([PSCustomObject]@{
                Name = 'ZqxWidget'; Command = 'C:\Vendor\zqx.exe' })

            $rec.Recommendation | Should -Be 'Review'
            $rec.Protected      | Should -BeFalse
        }
    }

    Context "NVIDIA Display Container is no longer flagged" {

        It "does not recommend disabling the display container" {
            # It backs the control panel and the driver's own settings, so
            # the old 'nvidia.*(container|telemetry)' pattern was advice that
            # broke display configuration to save a few megabytes.
            $rec = Get-StartupRecommendation -Item ([PSCustomObject]@{
                Name = 'NVDisplay.ContainerLocalSystem'
                Command = 'C:\Program Files\NVIDIA Corporation\Display.NvContainer\NVDisplay.Container.exe'
            })

            $rec.Recommendation | Should -Not -Be 'Disable'
        }

        It "still recommends disabling NVIDIA telemetry" {
            $rec = Get-StartupRecommendation -Item ([PSCustomObject]@{
                Name = 'NvTelemetry'
                Command = 'C:\Program Files\NVIDIA Corporation\NvTelemetry\NvTelemetryContainer.exe'
            })

            $rec.Recommendation | Should -Be 'Disable'
        }
    }
}

Describe "GUI streaming channels (00-Foundation.ps1)" {

    It "emits ITEM and STAGE on their own sentinels" {
        $out = & {
            Write-GuiStage "Reading installed programs"
            Write-GuiItem ([PSCustomObject]@{ Id = 'A.B'; Name = 'Thing' })
        } 6>&1

        # Write-GuiLine goes to [Console]::Out, NOT the pipeline - that is
        # the whole point (a payload emitted from inside a function would
        # otherwise become part of that function's return value). So the
        # pipeline must come back EMPTY here.
        @($out).Count | Should -Be 0 -Because `
            "wire-protocol lines must bypass the PowerShell pipeline"
    }

    It "collapses newlines in a stage message" {
        # A newline would split the line in two and orphan the second half
        # as raw console output the frontend cannot attribute.
        $sb = [System.Text.StringBuilder]::new()
        $writer = [System.IO.StringWriter]::new($sb)
        $old = [Console]::Out
        try {
            [Console]::SetOut($writer)
            Write-GuiStage "first`r`nsecond`tthird"
        } finally {
            [Console]::SetOut($old)
        }

        $written = $sb.ToString().TrimEnd("`r", "`n")
        $written | Should -Be '##PULSE##STAGE|first second third'
        $written.Split("`n").Count | Should -Be 1
    }
}
