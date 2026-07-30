"""
Static contracts between the GUI, the PowerShell backend, and the themes.

These are cheap, headless-safe, and catch the drift class of bug: a task
added to menu_structure.py with no dispatcher case is a card that fails at
click time, and a token present in one theme but not the other is a
KeyError the moment someone toggles.
"""
from __future__ import annotations

import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MENU = os.path.join(_ROOT, "src/frontend/menu_structure.py")
_DISPATCHER = os.path.join(_ROOT, "src/backend/modules/30-GuiDispatcher.ps1")
_PROBE = os.path.join(_ROOT, "src/backend/modules/11-StateProbe.ps1")

# Backend tasks the GUI invokes programmatically rather than from a card:
# state probes, wizard steps and the interactive panels' row actions.
_PROGRAMMATIC = {
    "GetTweakState", "ScanForUpdates", "InstallLocalFile",
    "InstallOfficeODTAuto", "StartupEnableItem", "StartupDisableItem",
    # v10.3: the Automation module's two cards are GUI-LOCAL ("@playbooks",
    # "@health_report") because neither is a backend action in its own
    # right — a playbook replays tasks that already have cases, and the
    # report is opened by a dialog. HealthReport is the one backend case
    # behind them, invoked by widgets.HealthReportDialog rather than by a
    # card, which is exactly what this allow-list is for.
    "HealthReport",
    # Same shape as HealthReport. The Safety & Recovery card is
    # GUI-LOCAL ("@activation") because the report is rendered by a dialog
    # that runs its own PowerShellTask — widgets.ActivationStatusDialog —
    # rather than through main.py's single-task pipeline.
    "ActivationStatus",
}


def _menu_source() -> str:
    return open(_MENU, encoding="utf-8").read()


def _dispatcher_cases() -> set[str]:
    src = open(_DISPATCHER, encoding="utf-8-sig").read()
    body = src[src.index("switch ($TaskName)"):]
    cases: set[str] = set()
    for match in re.finditer(
            r'^\s{8,}((?:"[A-Za-z0-9_]+"\s*,\s*)*"[A-Za-z0-9_]+")\s*\{',
            body, re.M):
        cases.update(re.findall(r'"([A-Za-z0-9_]+)"', match.group(1)))
    return cases


def _gui_tasks() -> set[str]:
    return set(re.findall(r'"task"\s*:\s*"([^"]+)"', _menu_source()))


def test_dispatcher_cases_were_parsed():
    """Guard the regex itself — a silently-empty set would pass every
    'no missing tasks' assertion below for the wrong reason."""
    assert len(_dispatcher_cases()) > 20


def test_every_gui_task_has_a_backend_case():
    """The contract 30-GuiDispatcher.ps1 documents: every `task` in
    menu_structure.py maps 1:1 to one switch case."""
    gui = {t for t in _gui_tasks() if not t.startswith("@")}
    missing = sorted(gui - _dispatcher_cases())
    assert not missing, f"GUI tasks with no dispatcher case: {missing}"


def test_no_unreachable_dispatcher_cases():
    """Every backend case is reachable — either from a card or from a
    known programmatic caller. A new orphan means dead backend code."""
    gui = {t for t in _gui_tasks() if not t.startswith("@")}
    orphans = sorted(_dispatcher_cases() - gui - _PROGRAMMATIC)
    assert not orphans, f"unreachable dispatcher cases: {orphans}"


def test_programmatic_tasks_are_actually_referenced():
    """Keeps the allow-list above honest — if one of these stops being
    called from Python it is dead code, not an exemption."""
    sources = []
    for folder in ("src/frontend", "src/utils"):
        base = os.path.join(_ROOT, folder)
        for name in os.listdir(base):
            if name.endswith(".py") and name != "menu_structure.py":
                sources.append(open(os.path.join(base, name),
                                    encoding="utf-8").read())
    blob = "\n".join(sources)
    unreferenced = sorted(t for t in _PROGRAMMATIC if f'"{t}"' not in blob)
    assert not unreferenced, f"allow-listed but never invoked: {unreferenced}"


def _probe_source() -> str:
    return open(_PROBE, encoding="utf-8-sig").read()


def _probe_keys() -> set[str]:
    """The task names 11-StateProbe.ps1 reports state for, read off the
    `$state["Name"]` assignments that build its return map."""
    return set(re.findall(r'\$state\["([A-Za-z0-9_]+)"\]\s*=', _probe_source()))


class TestStateProbe:
    """The probe's map is keyed by GUI TASK NAME so the frontend can look a
    card up with no translation table (11-StateProbe.ps1's own words). That
    only holds while the keys really are task names — a typo'd or renamed
    key doesn't raise anywhere, it just silently stops badging a card,
    which is invisible in exactly the way a missing badge always is."""

    def test_the_keys_were_actually_parsed(self):
        """Guard the regex: an empty set would make every check below pass
        for the wrong reason."""
        assert len(_probe_keys()) >= 15

    def test_every_probe_key_is_a_real_gui_task(self):
        gui = {t for t in _gui_tasks() if not t.startswith("@")}
        orphans = sorted(_probe_keys() - gui)
        assert not orphans, (
            f"probe reports state for non-existent task(s): {orphans} — "
            "either the task was renamed and the probe key was not, or the "
            "key is a typo that will never match a card")

    def test_every_probe_key_has_a_dispatcher_case(self):
        """A probe key naming a task the backend cannot run is incoherent
        even if a card happens to exist for it."""
        missing = sorted(_probe_keys() - _dispatcher_cases())
        assert not missing, f"probe keys with no dispatcher case: {missing}"

    def test_probe_covers_the_tasks_it_claims(self):
        """Pins the v10.1 coverage so a later refactor cannot quietly drop
        a probe. NetworkOptimization is deliberately NOT here: it flushes
        DNS and resets the Winsock/IP stack, which leaves no durable
        readable marker, so probing it could only ever be a guess."""
        expected = {
            "DarkMode", "DisableMouseAccel", "MinimalistTaskbar",
            "ClassicContextMenu", "GameMode", "DisableAdvertisingID",
            "DisableActivityHistory", "DisableTelemetry",
            "DisableHibernation", "EnableHibernation", "UltimatePowerPlan",
            "RemoveEdge", "RemoveOneDrive", "RemoveWindowsOld",
            "RemoveBloatware", "ApplyAllPrivacy",
        }
        assert expected <= _probe_keys(), (
            f"probe coverage regressed, missing: {sorted(expected - _probe_keys())}")

    def test_network_optimization_is_not_probed(self):
        """Its own guard, because the tempting thing to do is invent one.
        A card that claims 'Applied' for a transient stack reset would be
        actively misleading — worse than no badge at all."""
        assert "NetworkOptimization" not in _probe_keys(), (
            "NetworkOptimization has no durable readable state (ipconfig "
            "/flushdns, netsh winsock reset, netsh int ip reset). Any probe "
            "for it is a guess presented as a fact.")

    def test_state_probe_is_read_only(self):
        """The module's HARD contract: it runs on launch and after every
        task, so a mutating probe would silently re-apply tweaks behind
        the user's back. Static scan for the mutation primitives — cheap,
        and it fails at review time rather than on a user's machine.

        Also referenced from 11-StateProbe.ps1's own comment block as the
        thing pinning its reuse of 06-Tweaks.ps1's presence helpers.
        """
        source = _probe_source()
        # Strip comments: the module DESCRIBES what it must not do.
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#"))
        code = re.sub(r"<#.*?#>", "", code, flags=re.S)

        forbidden = [
            "Set-ItemProperty", "New-ItemProperty", "Remove-ItemProperty",
            "New-Item", "Remove-Item", "Set-Item",
            "Set-Service", "Stop-Service", "Start-Service",
            "Stop-Process", "Remove-AppxPackage",
            "Checkpoint-Computer", "New-SystemRestorePoint",
            "Set-Content", "Out-File", "reg add", "reg delete",
        ]
        found = sorted({c for c in forbidden if c.lower() in code.lower()})
        assert not found, (
            f"11-StateProbe.ps1 contains mutating call(s): {found}. This "
            "module is invoked on launch and after every task — a write "
            "here re-applies tweaks behind the user's back.")

    def test_reused_presence_helpers_still_exist(self):
        """The probe deliberately reuses 06-Tweaks.ps1's helpers instead of
        duplicating detection logic. If either is renamed, the probe's
        try/catch turns the failure into a silent 'unknown' rather than an
        error — so nothing would report the breakage but this."""
        tweaks = open(os.path.join(_ROOT, "src/backend/modules/06-Tweaks.ps1"),
                      encoding="utf-8-sig").read()
        for helper in ("Test-MicrosoftEdgeInstalled", "Test-OneDriveInstalled"):
            if helper in _probe_source():
                assert f"function {helper}" in tweaks, (
                    f"11-StateProbe.ps1 calls {helper}, which no longer "
                    "exists in 06-Tweaks.ps1 — the probe will silently "
                    "report 'unknown' forever")


def test_local_actions_are_marked_with_an_at_sign():
    local = {t for t in _gui_tasks() if t.startswith("@")}
    assert local, "the '@' convention for GUI-local actions has vanished"
    assert not (local & _dispatcher_cases())


def test_every_local_action_is_handled_by_the_gui():
    """The '@' convention's other half. A local action has no dispatcher
    case to catch it, so a card whose task main.py does not handle falls
    through to _run_local_action's path lookup and reports 'Unknown local
    action' at click time — the exact failure the task/case parity check
    above prevents for backend tasks."""
    main = open(os.path.join(_ROOT, "src/frontend/main.py"), encoding="utf-8").read()
    handler = main[main.index("def _run_local_action"):]
    unhandled = sorted(t for t in _gui_tasks()
                       if t.startswith("@") and f'"{t}"' not in handler)
    assert not unhandled, f"local actions with no handler in main.py: {unhandled}"


def test_every_menu_glyph_exists_in_the_icon_map(qapp):
    """A card's `glyph` is looked up with GLYPHS.get(name, ("", "")), which
    means a typo'd or newly-invented name renders a BLANK icon plaque
    rather than raising — invisible in exactly the way a missing icon
    always is."""
    from frontend import theme as TH
    names = set(re.findall(r'"glyph"\s*:\s*"([^"]+)"', _menu_source()))
    assert names, "no glyphs found to check"
    missing = sorted(names - set(TH.GLYPHS))
    assert not missing, f"menu glyphs absent from theme.GLYPHS: {missing}"


_ACTIVATION = os.path.join(_ROOT, "src/backend/modules/13-Activation.ps1")


def test_activation_module_is_read_only():
    """13-Activation.ps1's hard contract, and the whole point of the
    module: it REPORTS licence state and never changes it. The same static
    scan TestStateProbe applies to the tweak probe, plus the licensing
    tools specifically — a module that could activate would make every
    reassurance in its own header false.
    """
    source = open(_ACTIVATION, encoding="utf-8-sig").read()
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    code = re.sub(r"<#.*?#>", "", code, flags=re.S)

    forbidden = [
        # generic mutation primitives
        "Set-ItemProperty", "New-ItemProperty", "Remove-ItemProperty",
        "New-Item", "Remove-Item", "Set-Item", "Set-Service",
        "Set-Content", "Out-File", "reg add", "reg delete",
        # Licensing-specific: the calls that would CHANGE activation state.
        # Named precisely (ActivateProduct, not "Activate") because the
        # module legitimately says "activated", "re-activated" and "Not
        # activated" all over its own status strings — a loose substring
        # here would fail on the report's vocabulary instead of its calls.
        "slmgr", "ospp", "InstallProductKey", "ActivateProduct",
        "SetKeyManagementServiceMachine", "Invoke-CimMethod",
        "Invoke-WebRequest", "Invoke-Expression", "Start-Process",
    ]
    found = sorted({c for c in forbidden if c.lower() in code.lower()})
    assert not found, (
        f"13-Activation.ps1 contains state-changing or remote-code call(s): "
        f"{found}. This module is a read-only report; activation is Windows' "
        "own job, reached through the Settings deep link in the dialog.")


_BACKEND_DIR = os.path.join(_ROOT, "src/backend")

#: Stock tools that must never be invoked by bare name from an elevated
#: process. Each has an anchored path behind Get-SystemBinary
#: (00-Foundation.ps1); winget is the exception and goes through
#: Get-WingetPath (03-Environment.ps1) because it is an app-execution
#: alias rather than a System32 binary.
_ANCHORED_TOOLS = (
    "powershell", "pwsh", "explorer", "taskmgr", "cmd", "winget",
    "msiexec", "ie4uinit", "rundll32", "regsvr32", "sc", "reg", "schtasks",
)


def _backend_files():
    for root, _dirs, names in os.walk(_BACKEND_DIR):
        for name in sorted(names):
            if name.endswith(".ps1"):
                yield os.path.join(root, name)


def _code_lines(path):
    """(line_number, text) for lines that are actually code — comments and
    block comments carry the prose that DESCRIBES these patterns, and a
    scan that matched those would fail on its own documentation."""
    source = open(path, encoding="utf-8-sig").read()
    source = re.sub(r"<#.*?#>", "", source, flags=re.S)
    out = []
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append((number, line.split("#")[0] if " #" in line else line))
    return out


def test_no_bare_executable_invocations():
    """v1.0 PATH-hijack contract.

    Pulse runs elevated, and a bare executable name is a $env:PATH SEARCH
    rather than a path. PATH is assembled from HKCU as well as HKLM, so an
    unelevated user can place a directory ahead of System32 and have their
    binary launched with Pulse's administrator token.

    Every stock tool therefore goes through Get-SystemBinary (or
    Get-WingetPath for the app-execution alias). This scan is the guard
    that keeps a future `Start-Process explorer` from quietly restoring the
    hole, since the resulting behaviour is indistinguishable from correct
    on a machine that is not under attack.
    """
    names = "|".join(_ANCHORED_TOOLS)
    patterns = (
        # Start-Process explorer / Start-Process "taskmgr.exe" / -FilePath "winget"
        re.compile(
            r'Start-Process\s+(?:-FilePath\s+)?["\']?(?:%s)(?:\.exe)?["\']?[\s,]' % names,
            re.I),
        # & winget ... / & "explorer" ...
        re.compile(r'&\s+["\']?(?:%s)(?:\.exe)?["\']?\s' % names, re.I),
    )

    offenders = []
    for path in _backend_files():
        relative = os.path.relpath(path, _ROOT).replace(os.sep, "/")
        for number, line in _code_lines(path):
            for pattern in patterns:
                if pattern.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()}")

    assert not offenders, (
        "bare executable invocation(s) found — these resolve through "
        "$env:PATH, which the unelevated user controls, and Pulse runs "
        "elevated. Route them through Get-SystemBinary (00-Foundation.ps1) "
        "or Get-WingetPath (03-Environment.ps1):\n  " + "\n  ".join(offenders))


def test_wql_filters_escape_interpolated_values():
    """A WQL -Filter that interpolates a variable directly is injectable:
    WQL quotes with ' and escapes with \\, so a value carrying either ends
    the literal early and the rest is parsed as query. Interpolated values
    must go through ConvertTo-WqlLiteral (00-Foundation.ps1).

    A filter built only from literals (13-Activation.ps1's two constant
    application-ID GUIDs) has nothing to escape and is not matched here.
    """
    # -Filter "...'$Something'..." — a bare $var inside a quoted literal.
    raw = re.compile(r'-Filter\s+"[^"]*\'\$(?!\()[A-Za-z_]\w*[^"]*\'')

    offenders = []
    for path in _backend_files():
        relative = os.path.relpath(path, _ROOT).replace(os.sep, "/")
        for number, line in _code_lines(path):
            if raw.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")

    assert not offenders, (
        "WQL filter(s) interpolate a value without escaping it. Build the "
        "filter with ConvertTo-WqlLiteral, e.g.\n"
        '  $Filter = "Name=\'{0}\'" -f (ConvertTo-WqlLiteral $Name)\n  '
        + "\n  ".join(offenders))


class TestThemes:
    @staticmethod
    def _themes(qapp):
        from frontend import theme as TH
        return {name: TH.ThemeManager(name, None).t for name in ("dark", "light")}

    def test_both_themes_expose_identical_token_sets(self, qapp):
        themes = self._themes(qapp)
        dark, light = set(themes["dark"]), set(themes["light"])
        assert dark == light, (
            f"only in dark: {sorted(dark - light)}; "
            f"only in light: {sorted(light - dark)}")

    def test_module_accents_resolve_in_both_themes(self, qapp):
        from frontend import theme as TH
        accents = set(re.findall(r'"accent"\s*:\s*"([^"]+)"', _menu_source()))
        assert accents, "no module accents found to check"
        for name, tokens in self._themes(qapp).items():
            for accent in accents:
                value = TH.resolve_accent(tokens, accent)
                assert isinstance(value, str) and value.startswith("#"), (
                    f"accent {accent!r} did not resolve in {name}: {value!r}")

    def test_opaque_canvas_tokens_are_solid_hex(self, qapp):
        """The shell gradient must stay fully opaque — an rgba() here
        would punch translucency straight back through the window."""
        for name, tokens in self._themes(qapp).items():
            for key in ("bg_grad_top", "bg_grad_bottom"):
                value = tokens[key]
                assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), (
                    f"{name}.{key} = {value!r} is not an opaque hex colour")


def test_every_bound_shortcut_is_documented(window):
    """SHORTCUTS is meant to be the single source of truth for both the
    bindings and the help sheet — Ctrl+F was bound but undocumented."""
    from PySide6.QtGui import QShortcut
    documented = " ".join(seq for seq, _ in window.SHORTCUTS)
    bound = {s.key().toString() for s in window.findChildren(QShortcut)}
    undocumented = sorted(
        key for key in bound
        if key.startswith("Ctrl+") and not key[-1].isdigit()
        and key not in documented)
    assert not undocumented, f"bound but not in SHORTCUTS: {undocumented}"


def test_command_palette_entries_are_runnable(qapp):
    from frontend.menu_structure import iter_leaf_items
    entries = list(iter_leaf_items())
    assert entries
    assert all(item.get("task") for item, _ in entries)
    assert all(crumb for _, crumb in entries)


def test_frontend_and_backend_report_the_same_version():
    """Both constants carry a "keep in lockstep" comment and both drifted
    anyway — main.py and core.ps1 sat at 10.0 through the 10.1, 10.2 and
    10.3 releases, so the title bar, the sidebar footer and QApplication
    all reported a version no changelog entry matched. A comment is not a
    constraint; this is."""
    from frontend.main import APP_VERSION

    core = open(os.path.join(_ROOT, "src/backend/core.ps1"),
                encoding="utf-8-sig").read()
    match = re.search(r'\$Script:ScriptVersion\s*=\s*"([^"]+)"', core)
    assert match, "ScriptVersion was renamed — update this test with it"
    assert match.group(1) == APP_VERSION, (
        f"core.ps1 says {match.group(1)}, main.py says {APP_VERSION}")
