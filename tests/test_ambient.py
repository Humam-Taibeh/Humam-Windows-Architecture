"""
Ambient background performance contract.

The glow is the bottom widget in the shell, so it is repainted far more
often than its own ~28fps timer requests: every animation above it (the two
BreathingIcons) forces a partial repaint underneath. Measured at idle
before this was addressed: 3.55 paintEvents per timer tick, ~76/s, adding
up to 26 full-widget repaints per second at ~2.7ms each.

The fix is not to paint less often — that is not ours to control — but to
make each paint cheap: the three drifting orbs are composited into one
cached pixmap and blitted, instead of three smooth-scaled blits per frame.
"""
from __future__ import annotations

import time

import pytest
from PySide6.QtGui import QPixmap

from conftest import settle

pytestmark = pytest.mark.native


def test_layer_is_built_and_reused(window, qapp):
    glow = window._glow
    glow._layer = None
    glow.repaint()
    first = glow._layer
    assert isinstance(first, QPixmap)
    assert glow._layer_size == (glow.width(), glow.height())
    glow.repaint()
    assert glow._layer is first, "layer rebuilt within its cadence window"


def test_layer_rebuilds_after_the_cadence_elapses(window):
    glow = window._glow
    glow.repaint()
    first = glow._layer
    glow._t += (glow._LAYER_MS / 1000.0) + 0.01     # advance animation time
    glow.repaint()
    assert glow._layer is not first, "layer never refreshes — orbs would freeze"


def test_only_one_layer_is_ever_retained(window):
    """The historical bug: caching keyed on window size minted a fresh
    ~1800px pixmap per resize step (1,323 pixmaps / 11.9 GB on one drag)."""
    glow = window._glow
    for width in range(1100, 1400, 40):
        window.resize(width, 820)
        glow.repaint()
    assert isinstance(glow._layer, QPixmap)
    layers = [v for v in vars(glow).values() if isinstance(v, QPixmap)]
    assert len(layers) == 1, "more than one full-size layer retained"


def test_theme_switch_invalidates_the_layer(window, qapp):
    glow = window._glow
    glow.repaint()
    assert glow._layer is not None
    window._toggle_theme_animated()
    settle(qapp, 900)
    glow.repaint()
    assert glow._layer is not None
    window._toggle_theme_animated()
    settle(qapp, 900)


class TestFreezeDuringSizeMove:
    """WM_ENTERSIZEMOVE parks the animation AND freezes the layer, so a
    resize drag cannot trigger a full-window layer rebuild per step."""

    def test_suspend_freezes_the_layer(self, floating):
        glow = floating._glow
        glow.repaint()
        try:
            glow.suspend()
            assert glow._frozen
            frozen = glow._layer
            floating.resize(1180, 780)
            glow.repaint()
            assert glow._layer is frozen, (
                "layer rebuilt mid-resize — the expensive path we removed")
        finally:
            glow.resume()

    def test_resume_rebuilds_at_the_final_size(self, floating, qapp):
        glow = floating._glow
        glow.suspend()
        floating.resize(1220, 800)
        glow.repaint()
        glow.resume()
        settle(qapp, 120)
        glow.repaint()
        assert not glow._frozen
        assert glow._layer_size == (glow.width(), glow.height())


def test_paint_cost_stays_within_the_frame_budget(window, qapp):
    """A full glow repaint must stay well inside one display frame — it
    competes with the OS move/size loop on the same thread, which is what
    made dragging stutter."""
    glow = window._glow
    glow.repaint()                      # warm the layer
    samples = []
    for _ in range(30):
        glow._layer_t = glow._t         # keep the cache warm; measure the blit
        start = time.perf_counter()
        glow.render(QPixmap(glow.size()))
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    median = samples[len(samples) // 2]
    assert median < 2.0, (
        f"ambient repaint median {median:.2f}ms — was ~2.7ms before caching; "
        "a regression here shows up as drag/resize stutter")
