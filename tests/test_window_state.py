"""
Window state machine — including the launch-blocking crash.

Regression origin: _init_geometry() runs during __init__ and calls
restoreGeometry(), which re-applies a saved MAXIMIZED state synchronously.
That fired changeEvent before _build_ui() had created _glow/_shell/_body,
so the handler raised AttributeError and took the process down. Effect:
close Pulse while maximized and it never starts again. It reached a real
user hive. This file is the guard.
"""
from __future__ import annotations

import pytest

from conftest import settle

pytestmark = pytest.mark.native


def test_cold_start_with_no_saved_geometry(fresh_window):
    """First-ever launch: _init_geometry falls through to the centred
    default and calls resize()/move() before the UI exists."""
    from utils import prefs
    from PySide6.QtCore import QSettings
    QSettings(prefs._ORG, prefs._APP).remove("ui/geometry")
    win = fresh_window()
    assert win.isVisible()
    assert win.width() > 0 and win.height() > 0


def test_restoring_a_maximized_geometry_does_not_crash(fresh_window, qapp):
    """THE regression: 'closed while maximized' must still start."""
    from utils import prefs
    first = fresh_window()
    first.showMaximized()
    settle(qapp, 300)
    assert first.isMaximized()
    prefs.set_window_geometry(first.saveGeometry())
    first.hide()

    second = fresh_window(normalize=False)   # must not raise
    assert second.isMaximized(), "the saved maximized state should restore"


def test_restored_maximized_window_looks_flush(fresh_window, qapp):
    """The state change is dropped while the UI is still being built, so
    __init__ must replay it — otherwise a restored-maximized window comes
    up wearing the floating look (margins that don't reach the edges)."""
    from utils import prefs
    first = fresh_window()
    first.showMaximized()
    settle(qapp, 300)
    prefs.set_window_geometry(first.saveGeometry())
    first.hide()

    second = fresh_window(normalize=False)
    settle(qapp, 200)
    assert second._shell.property("flush") is True
    from frontend.main import _FLUSH_MARGINS
    got = second._body.getContentsMargins()
    assert got == _FLUSH_MARGINS


def test_ui_ready_guard_exists(window):
    """changeEvent must stay guarded; without the flag the crash returns."""
    assert window._ui_ready is True


def test_maximize_restore_round_trip(floating, qapp):
    floating.showMaximized()
    settle(qapp, 300)
    assert floating._shell.property("flush") is True
    floating.showNormal()
    settle(qapp, 300)
    assert floating._shell.property("flush") is False


def test_minimize_parks_the_ambient_loop(floating, qapp):
    """hideEvent does not fire on minimize, so the ~28fps repaint would
    otherwise keep running behind an invisible window."""
    floating.showMinimized()
    settle(qapp, 300)
    assert not floating._glow._timer.isActive()
    floating.showNormal()
    settle(qapp, 400)
    assert floating._glow._timer.isActive()


class TestSizeMoveParking:
    """The ambient background is parked for the duration of the OS
    move/size loop — that took mean drag tracking lag from 3.6px to 0.3px."""

    def test_enter_size_move_parks(self, floating):
        floating._in_size_move = True
        floating._glow.suspend()
        try:
            assert not floating._glow._timer.isActive()
        finally:
            floating._in_size_move = False
            floating._glow.resume()

    def test_state_change_mid_drag_does_not_unpark(self, floating):
        """Aero-snapping changes window state INSIDE the move loop; if
        _sync_window_state resumed there it would undo the parking."""
        floating._in_size_move = True
        floating._glow.suspend()
        try:
            floating._sync_window_state()
            assert not floating._glow._timer.isActive()
        finally:
            floating._in_size_move = False
            floating._glow.resume()

    def test_exit_size_move_resumes(self, floating, qapp):
        floating._in_size_move = True
        floating._glow.suspend()
        floating._in_size_move = False
        floating._glow.resume()
        assert floating._glow._timer.isActive()


def test_minimum_size_respects_the_layout_floor(window):
    """Below chrome + one minimum-width card the grid physically cannot
    lay out, so the minimum must never be set under that floor."""
    from frontend.main import CategoryPage
    floor = window._CHROME_W + CategoryPage.MIN_CARD_W
    assert window.minimumWidth() >= floor
