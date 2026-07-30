#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }
<#
.SYNOPSIS
    Pester coverage for the v1.0 execution-hardening primitives
    (00-Foundation.ps1): Get-SystemBinary, ConvertTo-WqlLiteral and
    Test-SafeWebUrl.

.DESCRIPTION
    These three functions exist because Pulse RUNS ELEVATED, which turns
    three ordinary-looking conveniences into privilege-escalation paths:

      * a bare executable name is a $env:PATH SEARCH, and PATH is built
        from HKCU — a hive the unelevated user owns. Get-SystemBinary
        replaces the search with an absolute path;
      * an interpolated WQL filter is string concatenation into a query
        language, so a value carrying ' or \ escapes the literal and is
        parsed as query. ConvertTo-WqlLiteral escapes it;
      * Start-Process on an arbitrary string is ShellExecute, which RUNS
        whatever the string resolves to. Test-SafeWebUrl restricts the
        download-page fallback to real http(s) addresses.

    Every failure mode here is silent — each function returns a plausible
    value while doing the wrong thing — which is exactly why they get
    executable tests rather than a static scan alone.

    Nothing here elevates, touches the registry, spawns a process or
    reaches the network: these are pure functions over strings and paths.

.NOTES
    Run:  Invoke-Pester -Path tests\backend
#>

BeforeAll {
    $script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $script:ModuleDir = Join-Path $script:RepoRoot "src\backend\modules"
    . (Join-Path $script:ModuleDir "00-Foundation.ps1")
}

Describe "Absolute system binary paths (Get-SystemBinary)" {

    It "returns a rooted path, never a bare name" {
        foreach ($name in @('powershell', 'explorer', 'taskmgr', 'ie4uinit',
                            'msiexec', 'rundll32', 'cmd', 'sc', 'reg')) {
            $resolved = Get-SystemBinary $name
            [System.IO.Path]::IsPathRooted($resolved) | Should -BeTrue `
                -Because "'$name' must not be left to a PATH search"
        }
    }

    It "anchors System32 tools under the real System directory" {
        $system32 = [System.Environment]::GetFolderPath('System')
        (Get-SystemBinary 'taskmgr') | Should -BeLike "$system32*"
        (Get-SystemBinary 'msiexec') | Should -BeLike "$system32*"
        # powershell.exe sits in a subdirectory OF System32, not beside it.
        (Get-SystemBinary 'powershell') | Should -BeLike "$system32\WindowsPowerShell\v1.0\*"
    }

    It "anchors explorer.exe to the Windows root, not System32" {
        # The one stock tool that is NOT in System32. Getting this wrong
        # yields a path that simply does not exist, and the classic
        # context-menu tweak silently stops restarting the shell.
        $windows = [System.Environment]::GetFolderPath('Windows')
        $explorer = Get-SystemBinary 'explorer'
        $explorer | Should -Be (Join-Path $windows 'explorer.exe')
        Test-Path -LiteralPath $explorer | Should -BeTrue
    }

    It "resolves to files that actually exist on this machine" {
        foreach ($name in @('powershell', 'explorer', 'taskmgr', 'msiexec', 'cmd')) {
            Test-Path -LiteralPath (Get-SystemBinary $name) -PathType Leaf |
                Should -BeTrue -Because "'$name' should be a real stock binary"
        }
    }

    It "throws on an unknown name rather than guessing one" {
        # A typo must fail loudly at the call site. Falling back to the bare
        # name would restore the PATH search this function exists to remove.
        { Get-SystemBinary 'definitely-not-a-windows-tool' } | Should -Throw
    }
}

Describe "WQL literal escaping (ConvertTo-WqlLiteral)" {

    It "leaves an ordinary service name untouched" {
        ConvertTo-WqlLiteral 'DiagTrack' | Should -Be 'DiagTrack'
    }

    It "escapes a single quote so it cannot close the literal" {
        ConvertTo-WqlLiteral "Acme's Service" | Should -Be "Acme\'s Service"
    }

    It "escapes a backslash" {
        ConvertTo-WqlLiteral 'Domain\Svc' | Should -Be 'Domain\\Svc'
    }

    It "escapes the backslash BEFORE the quote" {
        # Order matters and is the subtle bug: escaping the quote first
        # produces \' , and a later backslash pass turns it into \\' —
        # which re-terminates the literal, i.e. the escaping undoes itself.
        ConvertTo-WqlLiteral "a\'b" | Should -Be "a\\\'b"
    }

    It "neutralises a filter-injection attempt" {
        $evil = "x' OR Name!='"
        $filter = "Name='{0}'" -f (ConvertTo-WqlLiteral $evil)
        # The injected quote is escaped, so the whole payload stays one
        # string operand instead of becoming a second WQL clause.
        $filter | Should -Be "Name='x\' OR Name!=\''"
        $filter | Should -Not -Match "OR Name!=''"
    }

    It "accepts an empty string" {
        ConvertTo-WqlLiteral '' | Should -Be ''
    }
}

Describe "Shell-launch URL validation (Test-SafeWebUrl)" {

    It "accepts http and https" {
        Test-SafeWebUrl 'https://www.mozilla.org/firefox/' | Should -BeTrue
        Test-SafeWebUrl 'http://example.com/download' | Should -BeTrue
    }

    It "rejects a local executable path" {
        # Start-Process is ShellExecute: this would RUN, not browse.
        Test-SafeWebUrl 'C:\Windows\System32\calc.exe' | Should -BeFalse
    }

    It "rejects file:// and UNC targets" {
        Test-SafeWebUrl 'file:///C:/Windows/System32/calc.exe' | Should -BeFalse
        Test-SafeWebUrl '\\attacker\share\payload.exe' | Should -BeFalse
    }

    It "rejects other URI schemes the shell would honour" {
        Test-SafeWebUrl 'ms-settings:activation' | Should -BeFalse
        Test-SafeWebUrl 'javascript:alert(1)' | Should -BeFalse
    }

    It "rejects empty and relative strings" {
        Test-SafeWebUrl '' | Should -BeFalse
        Test-SafeWebUrl 'www.example.com' | Should -BeFalse
    }
}
