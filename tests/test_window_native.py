"""
Native window integration — the contract between Pulse's frameless window
and Windows itself.

Regression origin: every edge and corner answered WM_NCHITTEST correctly
for months, and the window still could not be resized by dragging, because
Qt's FramelessWindowHint builds a bare WS_POPUP with no sizing border and
DefWindowProc simply refuses to run the size loop for one. Hit-test
assertions alone would have stayed green through that entire bug, so
is_sizable() is tested as its own first-class invariant.
"""
from __future__ import annotations

import pytest

from conftest import WINDOWS_ONLY, settle
import win32_probe as w32

pytestmark = [WINDOWS_ONLY, pytest.mark.native]


def test_window_has_a_real_sizing_frame(floating):
    """WS_THICKFRAME is what makes the hit-tests below mean anything."""
    hwnd = w32.hwnd_of(floating)
    assert w32.style(hwnd) & w32.WS_THICKFRAME, (
        "WS_THICKFRAME missing — Windows will ignore every resize hit-test")


def test_windows_reports_the_window_as_sizable(floating):
    """The invariant the old bug violated while all hit-tests passed."""
    assert w32.is_sizable(w32.hwnd_of(floating)), (
        "SC_SIZE greyed out — the OS will not start a resize loop")


def test_sizing_frame_is_never_drawn(floating, qapp):
    """WM_NCCALCSIZE must collapse the non-client area: the frame exists
    for the OS, not for the eye. If it were drawn we'd lose a border-and-
    caption strip out of our own chrome."""
    hwnd = w32.hwnd_of(floating)
    rect = w32.window_rect(hwnd)
    assert w32.client_size(hwnd) == (rect.right - rect.left,
                                     rect.bottom - rect.top)


@pytest.mark.parametrize("zone", [
    "LEFT", "RIGHT", "TOP", "BOTTOM",
    "TOPLEFT", "TOPRIGHT", "BOTTOMLEFT", "BOTTOMRIGHT",
])
def test_all_eight_resize_zones_hit_test(floating, zone):
    """All 4 edges and all 4 corners, so a cursor there gets the native
    resize arrow and starts the OS size loop."""
    hwnd = w32.hwnd_of(floating)
    points = w32.edge_points(w32.window_rect(hwnd))
    x, y = points[zone]
    assert w32.hit_name(hwnd, x, y) == zone


def test_titlebar_is_native_caption(floating):
    """HTCAPTION is what gives OS-driven dragging, Aero Snap and
    double-click maximize — and it keeps working while a modal is open."""
    hwnd = w32.hwnd_of(floating)
    points = w32.edge_points(w32.window_rect(hwnd))
    assert w32.hit_name(hwnd, *points["CAPTION"]) == "CAPTION"


def test_body_is_client_area(floating):
    hwnd = w32.hwnd_of(floating)
    points = w32.edge_points(w32.window_rect(hwnd))
    assert w32.hit_name(hwnd, *points["CLIENT"]) == "CLIENT"


@pytest.mark.parametrize("role,expected", [
    ("min", "MINBUTTON"), ("max", "MAXBUTTON"), ("close", "CLOSE"),
])
def test_caption_buttons_are_non_client(floating, role, expected):
    """Windows owns these three; HTMAXBUTTON is also what summons the
    Windows 11 Snap Layouts flyout."""
    hwnd = w32.hwnd_of(floating)
    rect = w32.window_rect(hwnd)
    btn = floating.titlebar.caption_buttons()[role]
    dpr = floating.devicePixelRatioF()
    centre = btn.mapTo(floating, btn.rect().center())
    x = rect.left + round(centre.x() * dpr)
    y = rect.top + round(centre.y() * dpr)
    assert w32.hit_name(hwnd, x, y) == expected


def test_theme_toggle_stays_a_client_hole(floating):
    """The one title-bar control that must remain an ordinary Qt button —
    if the HTCAPTION strip swallowed it, it would become dead chrome."""
    hwnd = w32.hwnd_of(floating)
    rect = w32.window_rect(hwnd)
    btn = floating.titlebar.theme_button()
    dpr = floating.devicePixelRatioF()
    centre = btn.mapTo(floating, btn.rect().center())
    x = rect.left + round(centre.x() * dpr)
    y = rect.top + round(centre.y() * dpr)
    assert w32.hit_name(hwnd, x, y) == "CLIENT"


def test_resize_loop_clamps_to_the_layout_floor(floating):
    """The OS size loop must honour the minimum the grid actually needs,
    or a drag can squeeze cards past their minimum and clip them."""
    hwnd = w32.hwnd_of(floating)
    dpr = floating.devicePixelRatioF()
    track_w, track_h = w32.min_track_size(hwnd)
    assert abs(track_w - round(floating.minimumWidth() * dpr)) <= 2
    assert abs(track_h - round(floating.minimumHeight() * dpr)) <= 2


class TestMaximized:
    """A maximized window must behave exactly like a native one: no resize
    border, content stopping at the work area, and caption zones reaching
    the literal screen corner."""

    @pytest.fixture(autouse=True)
    def maximized(self, floating, qapp):
        floating.showMaximized()
        settle(qapp, 400)
        yield floating

    def test_client_matches_work_area(self, maximized, qapp):
        """WM_NCCALCSIZE must inset by the frame when zoomed. Using Qt's
        isMaximized() here instead of IsZoomed() bled the window ~9px off
        every edge, because Qt's state lags the transition."""
        from PySide6.QtGui import QGuiApplication
        hwnd = w32.hwnd_of(maximized)
        avail = QGuiApplication.primaryScreen().availableGeometry()
        dpr = maximized.devicePixelRatioF()
        cw, ch = w32.client_size(hwnd)
        assert abs(cw - round(avail.width() * dpr)) <= 4
        assert abs(ch - round(avail.height() * dpr)) <= 4

    def test_no_resize_border_when_maximized(self, maximized):
        hwnd = w32.hwnd_of(maximized)
        rect = w32.window_rect(hwnd)
        assert w32.hit_name(hwnd, rect.left + 2,
                            (rect.top + rect.bottom) // 2) != "LEFT"

    def test_corner_slam_closes(self, maximized):
        """Fitts's law: slamming into the top-right screen corner must hit
        Close, which is only true if the caption zone reaches the edge."""
        hwnd = w32.hwnd_of(maximized)
        rect = w32.window_rect(hwnd)
        assert w32.hit_name(hwnd, rect.right - 2, rect.top + 1) == "CLOSE"


def test_resize_borders_return_after_restore(floating, qapp):
    """Round-trip: maximize then restore must give the borders back."""
    floating.showMaximized()
    settle(qapp, 400)
    floating.showNormal()
    settle(qapp, 500)
    hwnd = w32.hwnd_of(floating)
    assert not w32.is_zoomed(hwnd)
    points = w32.edge_points(w32.window_rect(hwnd))
    assert w32.hit_name(hwnd, *points["LEFT"]) == "LEFT"
    assert w32.hit_name(hwnd, *points["BOTTOMRIGHT"]) == "BOTTOMRIGHT"
