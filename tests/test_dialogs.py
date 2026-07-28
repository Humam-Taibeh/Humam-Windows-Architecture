"""
Modal dialog scrim geometry and compositing isolation.

PulseDialog is the ONE place translucency is still legitimate: it is a
separate top-level window that must be layered to dim the shell behind it.
These tests pin that it stays contained — the host must never become
layered — and that the scrim is square, since it now covers a square
opaque shell (a rounded scrim leaves lit wedges of shell at the corners).
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication

from conftest import WINDOWS_ONLY, settle
import win32_probe as w32

pytestmark = pytest.mark.native


def _open(dialog, inspect, qapp):
    """exec() the modal and run `inspect` inside its event loop."""
    captured = {}

    def run():
        try:
            captured["result"] = inspect()
        except BaseException as exc:      # surface it after exec() unwinds
            captured["error"] = exc
        finally:
            dialog.reject()

    QTimer.singleShot(250, run)
    dialog.exec()
    qapp.processEvents()
    if "error" in captured:
        raise captured["error"]
    return captured.get("result")


@pytest.fixture
def sheet(floating):
    from frontend import widgets as W
    return W.ShortcutSheetDialog(floating, floating.theme.t, floating.SHORTCUTS)


def test_scrim_is_square_by_default(sheet):
    """The value the FIRST paint uses — refit_dialog re-asserts it, but a
    rounded default flashes two lit wedges of shell on the opening frame."""
    assert sheet._scrim_radius == 0


def test_scrim_covers_exactly_the_host_body(floating, sheet, qapp):
    def check():
        titlebar_h = floating.titlebar.height()
        origin = floating.mapToGlobal(QPoint(0, titlebar_h))
        geo = sheet.geometry()
        assert abs(geo.x() - origin.x()) <= 1
        assert abs(geo.y() - origin.y()) <= 1
        assert geo.width() == floating.width()
        assert geo.height() == floating.height() - titlebar_h

    _open(sheet, check, qapp)


def test_caption_buttons_stay_reachable_behind_a_modal(floating, sheet, qapp):
    """The scrim starts below the title bar so minimize/maximize/close
    remain visible and clickable no matter what is open."""
    def check():
        assert sheet.geometry().y() >= floating.mapToGlobal(
            QPoint(0, floating.titlebar.height())).y()

    _open(sheet, check, qapp)


@WINDOWS_ONLY
def test_host_never_becomes_layered(floating, sheet, qapp):
    """The dialog is layered; that must not leak onto the main window."""
    host = w32.hwnd_of(floating)
    assert not w32.is_layered(host)

    def check():
        assert w32.is_layered(w32.hwnd_of(sheet)), (
            "the scrim needs alpha to dim — it should be layered")
        assert not w32.is_layered(host), "layering leaked onto the host"

    _open(sheet, check, qapp)
    assert not w32.is_layered(host)


@WINDOWS_ONLY
def test_scrim_has_no_sizing_frame(floating, sheet, qapp):
    """Only the main window gets WS_THICKFRAME; a resizable/snappable
    scrim would be nonsense."""
    def check():
        assert not (w32.style(w32.hwnd_of(sheet)) & w32.WS_THICKFRAME)

    _open(sheet, check, qapp)


def test_scrim_refits_when_the_host_resizes(floating, sheet, qapp):
    """PulseApp.resizeEvent -> refit_dialog keeps an open modal glued to
    the body. This depends on isinstance(active, PulseDialog) matching,
    which silently fails if the module tree is imported twice."""
    def check():
        floating.resize(1500, 950)
        settle(qapp, 200)
        geo = sheet.geometry()
        assert geo.width() == floating.width()
        assert geo.height() == floating.height() - floating.titlebar.height()
        assert sheet._scrim_radius == 0

    _open(sheet, check, qapp)


def test_stacked_modals_unwind_cleanly(floating, qapp):
    """Nested wizards each paint their own scrim over whatever is behind."""
    from frontend import widgets as W
    outer = W.ShortcutSheetDialog(floating, floating.theme.t, floating.SHORTCUTS)
    seen = {}

    def open_inner():
        inner = W.ShortcutSheetDialog(floating, floating.theme.t,
                                      floating.SHORTCUTS)

        def check_inner():
            seen["both_visible"] = inner.isVisible() and outer.isVisible()
            seen["both_square"] = (inner._scrim_radius == 0
                                   and outer._scrim_radius == 0)

        _open(inner, check_inner, qapp)
        outer.reject()

    QTimer.singleShot(250, open_inner)
    outer.exec()
    qapp.processEvents()

    assert seen.get("both_visible") is True
    assert seen.get("both_square") is True
    assert QApplication.activeModalWidget() is None


def test_host_still_interactive_after_modals(floating, sheet, qapp):
    _open(sheet, lambda: None, qapp)
    floating.open_category(0)
    qapp.processEvents()
    assert floating.stack.currentIndex() == 1
    floating.go_home()
    qapp.processEvents()
