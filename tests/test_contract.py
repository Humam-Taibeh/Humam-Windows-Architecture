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
