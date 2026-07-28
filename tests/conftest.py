"""
tests/conftest.py

Shared fixtures for the Pulse regression suite.

TWO things here are load-bearing and must not be "tidied away":

1. IMPORT ROOTING. src/frontend/main.py imports its siblings absolutely
   (`from frontend.widgets import ...`) and ships as
   `python src\\frontend\\main.py`, so **src/ is the package root**. A test
   that reaches the app via `import src.frontend.main` loads a SECOND,
   independent copy of every module: `src.frontend.widgets.PulseDialog is
   not frontend.widgets.PulseDialog`. Nothing raises — but every
   isinstance() check silently returns False, so working code (e.g.
   PulseApp.resizeEvent's `isinstance(active, PulseDialog)` guard) looks
   broken. test_imports.py guards this invariant explicitly.

2. PREFERENCE ISOLATION. prefs.py writes to the real user hive
   (HKCU\\Software\\HumamTaibeh\\Pulse): theme, window geometry, recent
   operations. Tests maximize windows and save geometry, so without
   isolation a test run would rewrite the developer's actual settings —
   and the "closed while maximized" regression test would leave the app
   in exactly the state that used to prevent it from starting. The whole
   session is redirected to a throwaway app name and deleted afterwards.
"""
from __future__ import annotations

import os
import sys

import pytest

# --- 1. import rooting: src/ IS the package root (see module docstring) ---
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="Win32 window integration is Windows-only")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "native: needs a real (non-offscreen) top-level window")


@pytest.fixture(scope="session", autouse=True)
def _isolate_preferences():
    """Redirect every prefs read/write to a throwaway hive for the run."""
    from utils import prefs
    original = prefs._APP
    prefs._APP = "PulseTestSuite"
    yield
    QSettings(prefs._ORG, prefs._APP).clear()
    prefs._APP = original


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    yield app


@pytest.fixture(scope="session")
def _headless() -> bool:
    return os.environ.get("QT_QPA_PLATFORM", "") == "offscreen"


def _make_window(normalize: bool = True):
    """`normalize=False` shows the window EXACTLY as main() does, without
    forcing it out of a restored state — required by the tests that assert
    a saved maximized geometry comes back maximized. Calling showNormal()
    there would silently undo the very thing under test."""
    from frontend.main import PulseApp
    win = PulseApp()
    if normalize:
        win.showNormal()
        win.resize(1300, 860)
        win.move(140, 110)
    else:
        win.show()
    return win


@pytest.fixture(scope="session")
def window(qapp):
    """One shared window for the whole session — constructing PulseApp is
    expensive (full UI build + theme pass). Tests that mutate window state
    must restore it; `floating` below does that for you."""
    win = _make_window()
    qapp.processEvents()
    yield win
    win.close()
    qapp.processEvents()


@pytest.fixture
def floating(window, qapp):
    """`window`, guaranteed non-maximized before AND after the test — the
    resize-border hit-tests are only valid on a floating window, and a
    test that leaves it maximized would silently break the next one."""
    _restore(window, qapp)
    yield window
    _restore(window, qapp)


def _restore(win, qapp):
    if win.isMaximized() or win.isMinimized():
        win.showNormal()
        settle(qapp, 250)
    if (win.width(), win.height()) != (1300, 860):
        win.resize(1300, 860)
        settle(qapp, 80)


@pytest.fixture
def fresh_window(qapp):
    """A COLD PulseApp construction, for tests that assert on what happens
    during __init__ itself (the restore-geometry crash regression)."""
    made = []

    def build(normalize: bool = True):
        win = _make_window(normalize)
        made.append(win)
        qapp.processEvents()
        return win

    yield build
    for win in made:
        win.close()
    qapp.processEvents()


def settle(qapp, ms: int = 120):
    """Pump the event loop for `ms` — Qt window-state changes and DWM
    transitions are asynchronous, so assertions need a settling window."""
    from PySide6.QtTest import QTest
    qapp.processEvents()
    QTest.qWait(ms)
    qapp.processEvents()
