"""
The release pipeline: spec -> bundle -> installer -> checksums.

None of this runs PyInstaller or Inno Setup — that takes minutes and needs
both installed. What it pins is the set of facts the artifacts depend on,
each of which has a failure that only appears in a shipped build:

    a onefile regression      -> seconds of cold start, engine in %TEMP%
    a changed AppId           -> upgrades install a second copy
    a drifting version        -> the updater compares against the wrong thing
    a renamed installer       -> every release silently has "no installer"
    a dropped SHA256SUMS      -> the updater declines every update
"""
from __future__ import annotations

import os
import re

import pytest

from utils import updater, version

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts: str) -> str:
    with open(os.path.join(_ROOT, *parts), encoding="utf-8-sig") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def spec() -> str:
    return _read("main.spec")


@pytest.fixture(scope="module")
def iss() -> str:
    return _read("installer", "pulse.iss")


@pytest.fixture(scope="module")
def build_script() -> str:
    return _read("tools", "build_release.ps1")


# ============================================================
#  THE SPEC
# ============================================================
def test_the_build_is_onedir_not_onefile(spec):
    """Onefile re-extracts the whole bundle to %TEMP% on EVERY launch, and
    puts the elevated PowerShell engine in a user-writable directory —
    the exact hazard utils/resources.py documents. exclude_binaries=True
    plus COLLECT is what makes it onedir; drop either and it silently
    reverts."""
    assert "COLLECT(" in spec, "the spec no longer collects a onedir bundle"
    assert re.search(r"exclude_binaries\s*=\s*True", spec), (
        "EXE() is embedding the payload again — this is a onefile build")


def test_the_exe_carries_a_version_resource(spec):
    """Without it the Properties tab is blank, AV heuristics have nothing
    to weigh, and the updater has no authoritative installed version."""
    assert "VSVersionInfo(" in spec
    assert re.search(r"version\s*=\s*version_info", spec)


def test_the_version_resource_is_derived_not_typed(spec):
    """Every string is stamped from VERSION. A literal here is a fifth copy
    of the version, and the one nobody thinks to update."""
    assert "open(os.path.join(_here, 'VERSION')" in spec
    assert re.search(r"StringStruct\('FileVersion',\s*APP_VERSION\)", spec)
    assert re.search(r"StringStruct\('ProductVersion',\s*APP_VERSION\)", spec)
    assert not re.search(r"StringStruct\('FileVersion',\s*'[\d.]+'\)", spec), (
        "the version resource carries a hardcoded version")


def test_the_spec_imports_what_it_uses(spec):
    """PyInstaller 6.x does not inject these into the spec namespace; a
    spec that uses them without importing fails with a bare NameError
    several minutes into the build."""
    assert "from PyInstaller.utils.win32.versioninfo import" in spec
    for name in ("VSVersionInfo", "FixedFileInfo", "StringFileInfo",
                 "StringTable", "StringStruct", "VarFileInfo", "VarStruct"):
        assert name in spec, name


def test_upx_stays_disabled(spec):
    """Packed executables are a classic AV false-positive heuristic, and an
    elevated system tool cannot afford that reputation hit."""
    assert not re.search(r"upx\s*=\s*True", spec)


def test_the_app_is_still_not_auto_elevated(spec):
    """uac_admin would make every launch elevated, which makes the whole
    per-task elevation subsystem unreachable and some packages
    un-installable. See the long note in the spec."""
    assert not re.search(r"uac_admin\s*=\s*True", spec)


# ============================================================
#  THE INSTALLER
# ============================================================
def test_the_appid_is_stable_and_present(iss):
    """AppId is how Windows recognises an existing installation. Change it
    and every upgrade installs a second copy with its own Start Menu entry
    and an uninstaller that removes half the product."""
    match = re.search(r"^AppId=\{\{([0-9A-Fa-f-]{36})", iss, re.MULTILINE)
    assert match, "AppId is missing or is not a GUID"
    assert match.group(1).upper() == "7B2F4C91-3E8A-4D6B-9F1C-2A5E8D04B7C3", (
        "the AppId changed — every existing install becomes un-upgradeable")


def test_it_installs_to_program_files_by_default(iss):
    """A user-writable install directory means any process running as the
    user could replace PULSE.exe or the engine beside it and wait for the
    next elevated task. Program Files closes that."""
    assert re.search(r"^PrivilegesRequired=admin", iss, re.MULTILINE)
    assert re.search(r"^DefaultDirName=\{autopf\}", iss, re.MULTILINE)


def test_a_per_user_install_is_still_offered(iss):
    """For technicians with no admin rights — but never as the default."""
    assert re.search(r"^PrivilegesRequiredOverridesAllowed=dialog", iss,
                     re.MULTILINE)


def test_it_creates_both_shortcuts(iss):
    assert "{group}\\{#MyAppName}" in iss          # Start Menu
    assert "{autodesktop}\\{#MyAppName}" in iss    # Desktop
    assert re.search(r"^Name:\s*\"desktopicon\"", iss, re.MULTILINE)


def test_it_can_replace_a_running_copy(iss):
    """The updater launches this while Pulse is on screen; without these
    Setup hits a locked exe and fails or demands a reboot."""
    assert re.search(r"^CloseApplications=yes", iss, re.MULTILINE)
    assert re.search(r"^RestartApplications=yes", iss, re.MULTILINE)


def test_the_output_name_is_what_the_updater_looks_for(iss):
    """pulse.iss names the artifact and updater._ASSET_RE finds it. If they
    disagree, every release silently has 'no installer' and no user is ever
    offered an update."""
    assert "OutputBaseFilename=PULSE_Setup_v{#MyAppVersion}" in iss
    produced = f"PULSE_Setup_v{version.VERSION}.exe"
    assert updater._ASSET_RE.match(produced), (
        f"the updater would not recognise {produced}")


def test_the_installer_version_comes_from_the_version_file(iss):
    """Hand-compiling pulse.iss must stamp the same version the build
    script would."""
    assert 'FileOpen("..\\VERSION")' in iss
    assert "#define MyAppVersion" in iss


def test_uninstall_asks_before_deleting_preferences(iss):
    """An UPGRADE runs the old uninstaller on some paths; silently wiping a
    user's theme, window placement and task history on a version bump would
    be a bug they could never explain. Hence the UninstallSilent() guard."""
    assert "UninstallSilent()" in iss
    assert "Software\\HumamTaibeh\\Pulse" in iss
    assert "MB_YESNO" in iss


def test_signing_is_wired_but_inert(iss):
    """Unsigned today (SmartScreen will warn); the hook exists so enabling
    it is a one-line change rather than a new feature."""
    assert "SignTool" in iss
    assert re.search(r"^;\s*SignTool=", iss, re.MULTILINE), (
        "SignTool is enabled but no certificate is configured")


# ============================================================
#  THE BUILD SCRIPT
# ============================================================
def test_the_build_script_emits_checksums(build_script):
    """updater.verify() refuses any download whose digest is not published,
    so a release built without SHA256SUMS is one the updater declines."""
    assert "SHA256SUMS" in build_script
    assert "Get-FileHash" in build_script
    assert "SHA256" in build_script


def test_the_checksum_format_is_the_one_the_updater_parses(build_script):
    """'<lowercase sha256>  <filename>' — sha256sum's format."""
    assert 'ToLowerInvariant()' in build_script
    assert '"$Hash  $Name"' in build_script
    sample = f"{'a' * 64}  PULSE_Setup_v{version.VERSION}.exe"
    parsed = updater.parse_sums(sample)
    assert parsed.get(f"PULSE_Setup_v{version.VERSION}.exe") == "a" * 64


def test_the_build_script_verifies_the_bundle_layout(build_script):
    """PyInstaller 6.x puts datas under _internal\\, which IS _MEIPASS —
    so core.ps1's '..\\..\\VERSION' resolves there. Correct, but only while
    the tree keeps that shape."""
    assert "_internal\\VERSION" in build_script
    assert "_internal\\src\\backend\\core.ps1" in build_script
    assert "_internal\\src\\backend\\modules" in build_script


def test_the_build_script_survives_native_stderr(build_script):
    """PyInstaller logs its whole INFO stream to stderr, and Windows
    PowerShell turns that into a terminating error under -ErrorAction Stop
    — so a completely successful build aborted on its first line of
    output. The exit code is the only honest signal for a native command."""
    assert "$ErrorActionPreference = 'Continue'" in build_script
    assert "$LASTEXITCODE" in build_script


def test_the_build_script_refuses_a_ragged_version(build_script):
    """Releases are tagged v<VERSION>, so it must be MAJOR.MINOR.PATCH."""
    assert r"'^\d+\.\d+\.\d+$'" in build_script
