#Requires -Version 5.1
<#
.SYNOPSIS
    03-Environment.ps1 - package-manager provisioning and developer
    PATH / environment-variable management.

.DESCRIPTION
    - Invoke-WingetBootstrap / Ensure-Winget: LAZY winget bootstrap (v3.4
      behavior preserved): startup does a fast offline probe only; the full
      network bootstrap runs on-demand the first time a software operation
      actually needs winget. Registry tweaks, repairs and privacy tasks never
      pay for network round-trips they don't use.
    - Add-ToUserPath / Register-DevPath: PATH registration for freshly
      installed developer tools (data: $Script:DevAppPaths).
    - Test-DevDependencySuggestion: post-install companion offers
      (data: $Script:DevDependencyMap).
    - Verify-Environment (ROADMAP v4.0): automatic PATH / environment
      variable doctor for developer tools, driven entirely by
      $Script:DevToolCatalog in 01-Catalogs.ps1. For every tool it either
      confirms it on PATH, repairs the user PATH from a known install
      location, or reports it missing with the winget id to install it.
      Fully -WhatIf aware.
#>

# ============================================================
#  PRE-FLIGHT WINGET BOOTSTRAP (silent, robust)
# ============================================================
function Invoke-WingetBootstrap {
    Write-Host ""
    Write-Host "   [*] Winget not found - launching silent bootstrap from Microsoft CDN..." -ForegroundColor DarkGray
    $tempDir = Join-Path $env:TEMP "WingetBootstrap_Pulse"
    New-Item -ItemType Directory -Path $tempDir -Force -ErrorAction SilentlyContinue | Out-Null

    $deps = @(
        @{ Name = "Microsoft.VCLibs.x64.14.00.Desktop.appx"; Url = "https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx" },
        @{ Name = "Microsoft.UI.Xaml.2.8.x64.appx";         Url = "https://aka.ms/Microsoft.UI.Xaml.2.8.x64.appx" }
    )

    foreach ($dep in $deps) {
        $dest = Join-Path $tempDir $dep.Name
        try {
            Write-Host "   [ ] Downloading $($dep.Name)..." -ForegroundColor DarkGray
            Invoke-WebRequest -Uri $dep.Url -OutFile $dest -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
        } catch {
            Write-Host "   [!] Failed to download $($dep.Name) - bootstrap may fail." -ForegroundColor Yellow
        }
    }

    $latestJson = $null
    try {
        $latestJson = Invoke-RestMethod -Uri "https://api.github.com/repos/microsoft/winget-cli/releases/latest" -TimeoutSec 15 -ErrorAction Stop
    } catch {
        Write-Host "   [X] Cannot reach winget-cli GitHub API. Bootstrap aborted." -ForegroundColor Red
        return $false
    }

    $asset = $latestJson.assets | Where-Object { $_.name -like "Microsoft.DesktopAppInstaller_*_8wekyb3d8bbwe.msixbundle" } | Select-Object -First 1
    if (-not $asset) {
        Write-Host "   [X] MSIX bundle asset not found in latest release." -ForegroundColor Red
        return $false
    }

    $bundleName = $asset.name
    $bundleUrl  = $asset.browser_download_url
    $bundleDest = Join-Path $tempDir $bundleName

    Write-Host "   [ ] Downloading $bundleName ..." -ForegroundColor DarkGray
    try {
        Invoke-WebRequest -Uri $bundleUrl -OutFile $bundleDest -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
    } catch {
        Write-Host "   [X] Download of App Installer bundle failed." -ForegroundColor Red
        return $false
    }

    $allPkgs = Get-ChildItem -Path $tempDir -Filter *.appx | Sort-Object Name
    $allPkgs += Get-ChildItem -Path $tempDir -Filter *.msixbundle
    foreach ($pkg in $allPkgs) {
        try {
            Add-AppxPackage -Path $pkg.FullName -ErrorAction Stop
            Write-Host "   [OK] Installed $($pkg.Name)" -ForegroundColor Green
        } catch {
            Write-Host "   [!] Could not install $($pkg.Name) - may already be present." -ForegroundColor Yellow
        }
    }

    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
    Start-Sleep -Seconds 2

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "   [OK] Winget bootstrapped successfully." -ForegroundColor Green
        return $true
    } else {
        Write-Host "   [X] Winget still unavailable after bootstrap. Manual install required." -ForegroundColor Red
        return $false
    }
}

# LAZY BOOTSTRAP: startup does a fast, offline probe only. The full network
# bootstrap (Invoke-WingetBootstrap - CDN + GitHub downloads) runs on-demand
# via Ensure-Winget, the first time a software operation actually needs it.
$global:WingetAvailable = [bool](Get-Command winget -ErrorAction SilentlyContinue)
$Script:WingetBootstrapTried = $false

function Ensure-Winget {
    if ($global:WingetAvailable) { return $true }
    if ($Script:DryRun) {
        # Never download/install App Installer during a simulation.
        Write-Host "   [WHATIF] winget is missing - a real run would bootstrap 'App Installer' from the Microsoft CDN." -ForegroundColor DarkYellow
        Write-Log "WHATIF: winget missing - bootstrap skipped in dry-run."
        return $false
    }
    if ($Script:WingetBootstrapTried) { return $false }
    $Script:WingetBootstrapTried = $true
    Write-Log "Winget missing - attempting one-time silent bootstrap."
    $global:WingetAvailable = Invoke-WingetBootstrap
    if (-not $global:WingetAvailable) {
        Write-Warn "Winget could not be provisioned. Install 'App Installer' from the Microsoft Store or https://aka.ms/getwinget"
    }
    return $global:WingetAvailable
}

$global:ChocolateyAvailable = $false
if (Get-Command choco -ErrorAction SilentlyContinue) {
    $global:ChocolateyAvailable = $true
}

# ============================================================
#  USER PATH MANAGEMENT
# ============================================================
function Add-ToUserPath {
    param([string]$Directory)
    if (-not (Test-Path $Directory)) { return $false }
    $Current = [Environment]::GetEnvironmentVariable("Path", "User")
    $Entries = @($Current -split ";" | Where-Object { $_ -ne "" })
    if ($Entries -contains $Directory) { return $true }
    if (Test-DryRun "Add '$Directory' to the user PATH") { return $true }
    $NewPath = (@($Entries) + $Directory) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = "$env:Path;$Directory"
    return $true
}

function Register-DevPath {
    param($AppId, $AppName)
    $Config = $Script:DevAppPaths[$AppId]
    if (-not $Config) { return }

    Write-Info "Resolving install path for $AppName ..."
    $SearchRoots = @(
        "$env:ProgramFiles", "${env:ProgramFiles(x86)}",
        "$env:LOCALAPPDATA\Programs", "C:\msys64"
    ) | Where-Object { $_ -and (Test-Path $_) }

    $Found = $null
    foreach ($Root in $SearchRoots) {
        $Hit = Get-ChildItem -Path $Root -Filter $Config.ExeName -Recurse -Depth 4 -ErrorAction SilentlyContinue -File | Select-Object -First 1
        if ($Hit) { $Found = $Hit.DirectoryName; break }
    }

    if ($Found) {
        if (Add-ToUserPath -Directory $Found) { Write-Success "$AppName added to PATH -> $Found" }
        if ($AppId -eq "MSYS2.MSYS2") { Add-ToUserPath -Directory "C:\msys64\mingw64\bin" | Out-Null }
    } else {
        Write-Warn "$AppName installed, but its executable could not be auto-resolved for PATH registration."
    }
}

# ============================================================
#  DEV DEPENDENCY SUGGESTIONS (post-install helper)
# ============================================================
function Test-DevDependencySuggestion {
    param([string]$AppId)
    if (-not $Script:DevDependencyMap.ContainsKey($AppId)) { return }
    $Dep = $Script:DevDependencyMap[$AppId]

    try {
        if (Get-Command $Dep.CommandName -ErrorAction SilentlyContinue) { return }
    } catch {
        return
    }

    Write-Host ""
    Write-Warn "$($Dep.FriendlyName) was not found on PATH. It's typically required to run or compile projects with the IDE you just installed."
    if (Ask-User "Install $($Dep.FriendlyName)" "Installs $($Dep.FriendlyName) so your new IDE can build and run code right away.") {
        if ($global:WingetAvailable) {
            $DepResult = Smart-Deploy -AppId $Dep.WingetId -AppName $Dep.FriendlyName -Bulk -BulkMethod 'auto'
            if ($DepResult.Status -ne 'Success') {
                Write-Info "Automatic install of $($Dep.FriendlyName) did not complete. Opening the official manual download page..."
                Open-UrlSafe -Url $Dep.Url
            }
        } else {
            Write-Info "Winget is unavailable. Opening the official manual download page for $($Dep.FriendlyName)..."
            Open-UrlSafe -Url $Dep.Url
        }
    } else {
        Write-Info "You can install $($Dep.FriendlyName) later from: $($Dep.Url)"
    }
}

function Open-UrlSafe {
    <# Opens a URL in the default browser - but NEVER during a GUI task
       (silent run) and never during -WhatIf; logs the link instead. #>
    param([Parameter(Mandatory = $true)][string]$Url)
    if ($Script:NonInteractive -or $Script:DryRun) {
        Write-Log "URL (not opened - silent/dry-run mode): $Url"
        return
    }
    try { Start-Process $Url } catch { Write-Warn "Could not open browser automatically. Visit: $Url" }
}

# ============================================================
#  ROADMAP v4.0: VERIFY-ENVIRONMENT (developer PATH doctor)
#  NOTE: 'Verify' is not an approved PowerShell verb; the name is kept
#  because it is the roadmap's contract name (task: VerifyEnvironment).
# ============================================================
function Verify-Environment {
    Write-SectionHeader "Developer Environment Verification (PATH doctor)"
    $Ok       = 0
    $Repaired = 0
    $Missing  = New-Object System.Collections.ArrayList

    foreach ($Tool in $Script:DevToolCatalog) {
        $OnPath      = $null -ne (Get-Command $Tool.Command -ErrorAction SilentlyContinue)
        $ResolvedDir = $null

        if (-not $OnPath) {
            # Probe the tool's well-known install locations. Wildcards pick
            # the newest matching directory (e.g. jdk-21 over jdk-17).
            foreach ($Probe in $Tool.Probes) {
                if ($ResolvedDir) { break }
                if ([string]::IsNullOrWhiteSpace($Probe)) { continue }
                $Candidates = @(Get-Item -Path $Probe -ErrorAction SilentlyContinue |
                                Where-Object { $_.PSIsContainer } |
                                Sort-Object FullName -Descending)
                foreach ($Dir in $Candidates) {
                    foreach ($Ext in @("exe", "cmd", "bat")) {
                        if (Test-Path (Join-Path $Dir.FullName "$($Tool.Command).$Ext")) {
                            $ResolvedDir = $Dir.FullName
                            break
                        }
                    }
                    if ($ResolvedDir) { break }
                }
            }
        }

        if ($OnPath) {
            Write-AlreadyOK "$($Tool.Name): '$($Tool.Command)' is available on PATH."
            $Ok++
        } elseif ($ResolvedDir) {
            Write-Warn "$($Tool.Name) is installed at '$ResolvedDir' but missing from PATH - repairing."
            if (Add-ToUserPath -Directory $ResolvedDir) {
                Write-Success "$($Tool.Name) PATH entry registered -> $ResolvedDir"
                $Repaired++
            } else {
                Write-ErrorX "Could not add '$ResolvedDir' to the user PATH for $($Tool.Name)."
            }
        } else {
            Write-Warn "$($Tool.Name) not found ('$($Tool.Command)'). Install via winget id '$($Tool.WingetId)'."
            Write-Log "VERIFY-ENV MISSING: $($Tool.Name) - winget install --id $($Tool.WingetId)"
            [void]$Missing.Add($Tool.Name)
        }

        # Companion environment variable (e.g. JAVA_HOME for the JDK).
        if ($Tool.EnvVarName) {
            $ExistingVar = [Environment]::GetEnvironmentVariable($Tool.EnvVarName, "User")
            if (-not $ExistingVar) { $ExistingVar = [Environment]::GetEnvironmentVariable($Tool.EnvVarName, "Machine") }
            if ($ExistingVar) {
                Write-AlreadyOK "$($Tool.EnvVarName) is already set."
            } else {
                # Home dir = parent of the bin directory the command lives in.
                $HomeDir = $null
                if ($ResolvedDir) {
                    $HomeDir = Split-Path $ResolvedDir -Parent
                } elseif ($OnPath) {
                    $Cmd = Get-Command $Tool.Command -ErrorAction SilentlyContinue
                    if ($Cmd -and $Cmd.Source) { $HomeDir = Split-Path (Split-Path $Cmd.Source -Parent) -Parent }
                }
                # Sanity gate: a valid home must itself contain bin\<command>.exe.
                # This rejects PATH shims (e.g. Oracle's javapath symlink dir),
                # whose grandparent is NOT a usable JAVA_HOME.
                if ($HomeDir -and -not (Test-Path (Join-Path $HomeDir "bin\$($Tool.Command).exe"))) {
                    Write-Log "VERIFY-ENV: skipped $($Tool.EnvVarName) - '$HomeDir' is not a valid home (no bin\$($Tool.Command).exe; PATH entry is likely a shim)."
                    $HomeDir = $null
                }
                if ($HomeDir -and (Test-Path $HomeDir)) {
                    if (Test-DryRun "Set user environment variable $($Tool.EnvVarName) = '$HomeDir'") {
                        $Repaired++
                    } else {
                        try {
                            [Environment]::SetEnvironmentVariable($Tool.EnvVarName, $HomeDir, "User")
                            Set-Item -Path "env:$($Tool.EnvVarName)" -Value $HomeDir
                            Write-Success "$($Tool.EnvVarName) set to '$HomeDir' (user scope)."
                            $Repaired++
                        } catch {
                            Write-ErrorX "Could not set $($Tool.EnvVarName): $($_.Exception.Message)"
                        }
                    }
                }
            }
        }
    }

    Write-Info "Environment verification complete: $Ok tool(s) OK, $Repaired PATH/env repair(s), $($Missing.Count) missing."
    if ($Repaired -gt 0 -and -not $Script:DryRun) {
        Write-Info "New PATH entries apply to NEW terminals/apps; already-open windows keep their old PATH."
    }

    return [PSCustomObject]@{
        OkCount       = $Ok
        RepairedCount = $Repaired
        MissingCount  = $Missing.Count
        MissingNames  = @($Missing)
    }
}
