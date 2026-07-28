"""
Window rendering — opacity and reflow.

Regression origin: WA_TranslucentBackground made the top-level window
WS_EX_LAYERED (per-pixel alpha, software-composited). That shipped as a
dark semi-transparent blurred box on launch, invisible UI sections, and
tearing while dragging and resizing — none of which raise an exception.
The guard is therefore pixel-level: scan the rendered window and assert
every sampled pixel is fully opaque, in BOTH themes and at every size.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QImage, QPixmap

from conftest import WINDOWS_ONLY, settle
import win32_probe as w32

pytestmark = pytest.mark.native


def _alpha_census(widget) -> tuple[int, int, int]:
    """(transparent, semi, sampled) over the widget's own rendering."""
    pm = QPixmap(widget.size())
    widget.render(pm)
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    transparent = semi = sampled = 0
    step = 3          # every 3rd pixel: ~125k samples on a 1300x860 window
    for y in range(0, img.height(), step):
        for x in range(0, img.width(), step):
            alpha = (img.pixel(x, y) >> 24) & 0xFF
            sampled += 1
            if alpha == 0:
                transparent += 1
            elif alpha < 255:
                semi += 1
    return transparent, semi, sampled


@WINDOWS_ONLY
def test_window_is_not_layered(floating):
    """The single flag that caused the whole rendering-glitch class."""
    assert not w32.is_layered(w32.hwnd_of(floating)), (
        "WS_EX_LAYERED is back — expect blurred-box artifacts and drag tearing")


def test_translucent_background_attribute_is_off(floating):
    from PySide6.QtCore import Qt
    assert not floating.testAttribute(
        Qt.WidgetAttribute.WA_TranslucentBackground)


def test_window_has_an_opaque_themed_base(floating):
    """Without this the strip Windows reveals during a live resize flashes
    the default palette grey, which reads as tearing on the dragged edge."""
    assert floating.autoFillBackground()


@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_every_pixel_is_opaque_in_both_themes(floating, qapp, theme_name):
    if floating.theme.t["name"] != theme_name:
        floating._toggle_theme_animated()
        settle(qapp, 900)          # the toggle is animated
    assert floating.theme.t["name"] == theme_name
    transparent, semi, sampled = _alpha_census(floating)
    assert sampled > 10_000, "sanity: the scan actually sampled the window"
    assert (transparent, semi) == (0, 0), (
        f"{transparent} transparent + {semi} semi-transparent px "
        f"of {sampled} sampled in {theme_name} theme")


@pytest.mark.parametrize("size", [(1000, 700), (1600, 1000), (1150, 780)])
def test_resize_reflows_fully_painted(floating, qapp, size):
    """Grids, sidebar and cards must reflow to cover the new extent — a
    stale or unpainted region shows up as a non-opaque corner."""
    width, height = size
    floating.resize(width, height)
    settle(qapp, 150)
    pm = QPixmap(floating.size())
    floating.render(pm)
    img = pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    assert (img.width(), img.height()) == (width, height)
    for (px, py) in ((2, 2), (width - 3, 2), (2, height - 3),
                     (width - 3, height - 3)):
        assert ((img.pixel(px, py) >> 24) & 0xFF) == 255, (
            f"corner ({px},{py}) not opaque after resize to {width}x{height}")


def test_minimum_size_still_paints_fully(floating, qapp):
    """The tightest the layout is ever allowed to get."""
    floating.resize(floating.minimumWidth(), floating.minimumHeight())
    settle(qapp, 200)
    transparent, semi, _ = _alpha_census(floating)
    assert (transparent, semi) == (0, 0)


def test_shell_paints_square_corners(floating):
    """The shell must NOT round itself. On an opaque window the wedges
    outside a QSS radius expose the bare QMainWindow palette — the 'dark
    box behind rounded corners' artifact. DWM rounds the real window."""
    from frontend import theme as TH
    qss = TH.shell_qss(floating.theme.t)
    assert "border-radius: 0px" in qss
    assert "RADIUS" not in qss
    assert not hasattr(TH, "apply_blur_behind"), (
        "apply_blur_behind needs a layered window — it must stay removed")
    assert "shell" not in TH.RADIUS, (
        "RADIUS['shell'] is back; the window's corners belong to DWM")
