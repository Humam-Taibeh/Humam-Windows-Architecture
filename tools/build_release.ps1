<#
.SYNOPSIS
    Build PULSE's release artifacts: the onedir bundle, the Setup wizard
    and the checksum file the updater verifies against.

.DESCRIPTION
    One command, three outputs:

        dist\PULSE\                      the installable directory
        dist\PULSE_Setup_v<VERSION>.exe  the Setup wizard
        dist\SHA256SUMS                  digests for both

    SHA256SUMS IS NOT OPTIONAL. src/utils/updater.py refuses to execute a
    downloaded installer whose digest does not appear in it, so a release
    published without this file is a release the updater will decline —
    silently and correctly. It is produced here rather than by CI so that
    a locally built installer can be verified the same way.

    Everything is stamped from the repo's VERSION file. Nothing in this
    script names a version.

.PARAMETER SkipInstaller
    Build only the PyInstaller bundle. For iterating on the app without
    Inno Setup installed.

.PARAMETER KeepBuild
    Leave build\ in place. The default is a clean build, because a stale
    build\ is the usual cause of "the fix isn't in the exe".

.EXAMPLE
    .\tools\build_release.ps1
    .\tools\build_release.ps1 -SkipInstaller
#>
[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$KeepBuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$DistDir = Join-Path $RepoRoot 'dist'
$BuildDir = Join-Path $RepoRoot 'build'
$BundleDir = Join-Path $DistDir 'PULSE'

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Detail([string]$Text) {
    Write-Host "    $Text" -ForegroundColor DarkGray
}

# ============================================================
#  PREFLIGHT
# ============================================================
# Checked up front, together, so a missing tool costs seconds rather than
# surfacing four minutes into a PyInstaller run.
Write-Step 'Preflight'

$VersionFile = Join-Path $RepoRoot 'VERSION'
if (-not (Test-Path -LiteralPath $VersionFile)) {
    throw "VERSION not found at $VersionFile — it is the single source every artifact is stamped from."
}
$Version = (Get-Content -LiteralPath $VersionFile -TotalCount 1).Trim()
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION is '$Version'; releases are tagged v<VERSION> so it must be MAJOR.MINOR.PATCH."
}
Write-Detail "version         $Version"

# The GUI and the engine read VERSION at runtime, so a mismatch here means
# the shipped app would disagree with its own installer. test_contract.py
# pins the same thing; this repeats it because a release must never depend
# on someone having run the suite first.
$MainPy = Get-Content -LiteralPath (Join-Path $RepoRoot 'src\frontend\main.py') -Raw
if ($MainPy -notmatch 'APP_VERSION\s*=\s*version\.VERSION') {
    throw 'src/frontend/main.py no longer reads its version from utils.version — the artifacts would be stamped inconsistently.'
}
$CorePs1 = Get-Content -LiteralPath (Join-Path $RepoRoot 'src\backend\core.ps1') -Raw
if ($CorePs1 -match '\$Script:ScriptVersion\s*=\s*"([^"]+)"') {
    $Fallback = $Matches[1]
    if ($Fallback -ne $Version) {
        throw "core.ps1's fallback version is '$Fallback' but VERSION is '$Version'."
    }
    Write-Detail "core.ps1 fallback matches"
}

$Python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $Python) { throw 'python is not on PATH.' }
Write-Detail "python          $($Python.Source)"

& python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is not installed. Run: pip install -r requirements-dev.txt'
}
Write-Detail 'PyInstaller     present'

$Iscc = $null
if (-not $SkipInstaller) {
    $Candidates = @(
        (Get-Command iscc.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if (-not $Candidates) {
        throw 'Inno Setup 6 (ISCC.exe) was not found. Install it from https://jrsoftware.org/isdl.php, or pass -SkipInstaller.'
    }
    $Iscc = $Candidates[0]
    Write-Detail "iscc            $Iscc"
}

# A dirty tree is not fatal — a technician may be testing an unreleased fix
# — but a release built from uncommitted code that nobody can reproduce is
# worth one line of warning.
$GitStatus = & git -C $RepoRoot status --porcelain 2>$null
if ($LASTEXITCODE -eq 0 -and $GitStatus) {
    Write-Warning "Working tree has uncommitted changes; this build will not be reproducible from a tag."
}

# ============================================================
#  CLEAN
# ============================================================
Write-Step 'Clean'
# A locked artifact is NOT fatal. Anything holding a handle on dist\PULSE —
# the app still running, Explorer sitting in the folder, a shell whose
# working directory is inside it — would otherwise abort a build that
# PyInstaller's --noconfirm is perfectly able to finish by overwriting in
# place. Warn, and let the payload verification below decide whether the
# result is actually sound.
foreach ($Path in @($BundleDir, (Join-Path $DistDir "PULSE_Setup_v$Version.exe"), (Join-Path $DistDir 'SHA256SUMS'))) {
    if (Test-Path -LiteralPath $Path) {
        try {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            Write-Detail "removed $(Split-Path -Leaf $Path)"
        }
        catch {
            Write-Warning "Could not remove $(Split-Path -Leaf $Path) (in use); it will be overwritten in place."
        }
    }
}
if (-not $KeepBuild -and (Test-Path -LiteralPath $BuildDir)) {
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
    Write-Detail 'removed build\'
}

# ============================================================
#  BUNDLE
# ============================================================
Write-Step 'PyInstaller (onedir)'
Push-Location $RepoRoot
try {
    # $ErrorActionPreference is 'Stop' for this script, and in Windows
    # PowerShell that makes ANY line a native executable writes to stderr a
    # terminating error. PyInstaller logs its entire INFO stream there, so
    # a completely successful build would abort on its first line of
    # output. The exit code is the only honest success signal for a native
    # command; relax the preference around the call and test that instead.
    $Previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & python -m PyInstaller --noconfirm main.spec
    }
    finally {
        $ErrorActionPreference = $Previous
    }
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}

$ExePath = Join-Path $BundleDir 'PULSE.exe'
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "Expected $ExePath — the spec's COLLECT name or EXE name changed."
}

# The version resource is what Windows shows in Properties and what the
# updater compares an installed build against. Its absence was invisible
# before precisely because nothing ever looked.
$Stamped = (Get-Item -LiteralPath $ExePath).VersionInfo.FileVersion
if (-not $Stamped) {
    throw 'PULSE.exe carries no version resource — the spec is not passing version_info to EXE().'
}
Write-Detail "PULSE.exe       FileVersion $Stamped"
if ($Stamped.Split('.')[0..2] -join '.' -ne $Version) {
    throw "PULSE.exe reports '$Stamped' but VERSION is '$Version'."
}

# Onedir means the payload is real files rather than an embedded archive.
# PyInstaller 6.x puts all of it under _internal\, which IS _MEIPASS — so
# resources.bundled_roots() resolves there, and core.ps1's "..\..\VERSION"
# (from _internal\src\backend\) lands on _internal\VERSION. Both correct,
# but only because the tree keeps this exact shape: verify it rather than
# discover a broken engine after shipping.
foreach ($Rel in @('_internal',
                   '_internal\VERSION',
                   '_internal\src\backend\core.ps1',
                   '_internal\src\backend\modules',
                   '_internal\playbooks',
                   '_internal\assets\pulse.ico')) {
    if (-not (Test-Path -LiteralPath (Join-Path $BundleDir $Rel))) {
        throw "The bundle is missing '$Rel' — check the spec's datas."
    }
}
# The engine's one relative path, resolved exactly as core.ps1 resolves it.
$EngineVersionPath = Join-Path $BundleDir '_internal\src\backend\..\..\VERSION'
if (-not (Test-Path -LiteralPath $EngineVersionPath)) {
    throw 'core.ps1 would not find VERSION in this bundle layout.'
}
$BundledVersion = (Get-Content -LiteralPath $EngineVersionPath -TotalCount 1).Trim()
if ($BundledVersion -ne $Version) {
    throw "The bundled VERSION says '$BundledVersion' but this build is '$Version'."
}
Write-Detail 'bundle payload  complete'

$BundleSize = (Get-ChildItem -LiteralPath $BundleDir -Recurse -File |
    Measure-Object -Property Length -Sum).Sum / 1MB
Write-Detail ("bundle size     {0:N1} MB" -f $BundleSize)

# ============================================================
#  INSTALLER
# ============================================================
$SetupPath = Join-Path $DistDir "PULSE_Setup_v$Version.exe"
if (-not $SkipInstaller) {
    Write-Step 'Inno Setup'
    $Previous = $ErrorActionPreference     # see the note on the PyInstaller call
    $ErrorActionPreference = 'Continue'
    try {
        & $Iscc "/DMyAppVersion=$Version" (Join-Path $RepoRoot 'installer\pulse.iss')
    }
    finally {
        $ErrorActionPreference = $Previous
    }
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE." }
    if (-not (Test-Path -LiteralPath $SetupPath)) {
        throw "Expected $SetupPath — OutputBaseFilename in pulse.iss no longer matches."
    }
    Write-Detail ("setup size      {0:N1} MB" -f ((Get-Item -LiteralPath $SetupPath).Length / 1MB))
}

# ============================================================
#  CHECKSUMS
# ============================================================
# Format: "<lowercase sha256>  <filename>", i.e. sha256sum's. The updater
# parses exactly this, and publishing it as a release asset is what lets a
# download be verified before it is executed.
Write-Step 'SHA256SUMS'
$SumsPath = Join-Path $DistDir 'SHA256SUMS'
$Lines = @()
foreach ($Target in @($SetupPath)) {
    if (Test-Path -LiteralPath $Target) {
        $Hash = (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant()
        $Name = Split-Path -Leaf $Target
        $Lines += "$Hash  $Name"
        Write-Detail "$Hash  $Name"
    }
}
if ($Lines.Count -eq 0) {
    Write-Warning 'Nothing to checksum (installer was skipped); SHA256SUMS not written.'
}
else {
    # UTF8 without BOM: the updater and sha256sum both read this as ASCII,
    # and a BOM would corrupt the first digest.
    [System.IO.File]::WriteAllLines($SumsPath, $Lines, (New-Object System.Text.UTF8Encoding($false)))
}

# ============================================================
#  DONE
# ============================================================
Write-Step 'Done'
Write-Host "    PULSE $Version" -ForegroundColor Green
Write-Host "    bundle    $BundleDir"
if (-not $SkipInstaller) {
    Write-Host "    installer $SetupPath"
    Write-Host "    checksums $SumsPath"
    Write-Host ''
    Write-Host '    Next:' -ForegroundColor Yellow
    Write-Host "      git tag -a v$Version -m 'PULSE v$Version'  &&  git push origin v$Version"
    Write-Host '      Attach the installer AND SHA256SUMS to the GitHub release —'
    Write-Host '      the updater declines any download it cannot verify.'
}
