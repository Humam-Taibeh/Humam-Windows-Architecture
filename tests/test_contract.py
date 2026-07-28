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

# Backend tasks the GUI invokes programmatically rather than from a card:
# state probes, wizard steps and the interactive panels' row actions.
_PROGRAMMATIC = {
    "GetTweakState", "ScanForUpdates", "InstallLocalFile",
    "InstallOfficeODTAuto", "StartupEnableItem", "StartupDisableItem",
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
