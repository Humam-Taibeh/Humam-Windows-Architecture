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

import math
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

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


class TestDensity:
    """The field is meant to read as depth, and depth is carried by the
    THREE TIERS more than by the count: same-sized stars at any density
    are a flat texture. Both halves are pinned here because both are the
    kind of number a later "tidy" quietly halves."""

    def test_the_field_is_dense(self, window):
        particles = window._glow._particles
        assert len(particles) >= 100, (
            f"{len(particles)} stars — the field was thinned back out")

    def test_every_depth_tier_is_populated(self, window):
        glow = window._glow
        dims = {round(p["dim"], 3) for p in glow._particles}
        assert len(dims) == len(glow._PARTICLE_TIERS), (
            f"stars carry {len(dims)} distinct tier alphas for "
            f"{len(glow._PARTICLE_TIERS)} tiers — the parallax is gone")
        # far stars must be both dimmer AND slower than near ones, or the
        # tiers read as random variation rather than as distance
        by_dim = sorted(glow._particles, key=lambda p: p["dim"])
        far = [p for p in by_dim if p["dim"] == by_dim[0]["dim"]]
        near = [p for p in by_dim if p["dim"] == by_dim[-1]["dim"]]
        assert max(p["spd"] for p in far) <= min(p["spd"] for p in near)
        # ...and smaller, in BOTH themes' sprite tables. Light carries its
        # own wider spans (see _STAR_SPAN_MUL); a multiplier that inverted
        # the tier ordering would put the far stars in front.
        for key in ("px_dark", "px_light"):
            assert max(p[key] for p in far) <= min(p[key] for p in near), (
                f"{key} does not increase with depth tier")

    def test_star_textures_are_shared_not_per_star(self, window):
        """The density is affordable because stars are quantised onto a
        handful of native-size textures. One texture per star would be
        both the old resize leak and a per-frame scaling cost."""
        glow = window._glow
        glow.repaint()
        assert glow._star_cache, "no star texture was ever built"
        assert len(glow._star_cache) <= 16, (
            f"{len(glow._star_cache)} star textures cached — sizes are no "
            "longer quantised")


# ============================================================
#  STAR VISIBILITY — solved against the worst covering surface
# ============================================================
#: The translucent surfaces a star is actually seen THROUGH, per theme, as
#: (label, rgb, alpha). Taken from the tokens themselves: `overlay` is the
#: content well, `panel` the sidebar. Nobody ever sees a star against the
#: bare canvas — the wash is the bottom widget in the shell.
_VEILS = {
    "dark":  [("content well", (5, 6, 10), 0.45),
              ("sidebar", (18, 20, 26), 0.55)],
    "light": [("content well", (242, 242, 247), 0.55),
              ("sidebar", (255, 255, 255), 0.60)],
}
_CANVAS = {"dark": (11, 13, 17), "light": (242, 242, 247)}


def _srgb_to_lab(rgb):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(v) for v in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b)
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _delta_e(a, b):
    return math.sqrt(sum((x - y) ** 2
                         for x, y in zip(_srgb_to_lab(a), _srgb_to_lab(b))))


def _over(src, alpha, dst):
    return tuple(s * alpha + d * (1 - alpha) for s, d in zip(src, dst))


def _mean_star_delta_e(glow, theme: str, tier_index: int, veil) -> float:
    """Area-weighted mean dE of one star of `tier_index`, composited over
    the canvas and then seen through `veil` — the number the eye actually
    integrates for a 4-16px dot."""
    _, vrgb, valpha = veil
    canvas = _CANVAS[theme]
    base = _over(vrgb, valpha, canvas)
    star = (38, 50, 120) if theme == "light" else (200, 214, 255)
    pmax = glow._STAR_PMAX[theme]

    share, (r_lo, r_hi), _spd, dim = glow._PARTICLE_TIERS[tier_index]
    radius = (r_lo + r_hi) / 2.0
    span = max(4, round(radius * glow._STAR_SPAN_MUL[theme]) * 2)

    def sprite_alpha(t):
        # the _star_pixmap gradient: 1.0 @ 0.0, 0.42 @ 0.30, 0.0 @ 1.0
        if t >= 1.0:
            return 0.0
        if t <= 0.30:
            return 1.0 + (0.42 - 1.0) * (t / 0.30)
        return 0.42 * (1.0 - (t - 0.30) / 0.70)

    total = 0.0
    count = 0
    half = span / 2.0
    for yy in range(span):
        for xx in range(span):
            dx, dy = xx + 0.5 - half, yy + 0.5 - half
            alpha = pmax * dim * sprite_alpha(math.hypot(dx, dy) / half)
            lit = _over(vrgb, valpha, _over(star, alpha, canvas))
            total += _delta_e(lit, base)
            count += 1
    return total / count


@pytest.mark.parametrize("tier_index", [0, 1, 2])
def test_light_stars_carry_the_same_weight_as_dark(window, tier_index):
    """A star is never seen against the bare canvas — it is seen through
    the content well (0.55) or the sidebar (0.60), which eat 45-60% of its
    delta before it reaches the eye. Light was tuned against the canvas
    anyway, so it shipped at 60-65% of dark's weight: the far tier, 46% of
    the whole field, measured 0.83 dE through the sidebar, under the ~1.0
    just-noticeable threshold. That is not a subtle effect, it is an
    absent one, and it is why light mode read as having no particles.

    Light is solved TO DARK rather than to a number of its own — the two
    modes have to read as one field in different light, and dark is the
    one that was already right. Measured through each theme's OWN worst
    covering surface, so the comparison is like-for-like.
    """
    glow = window._glow
    dark = min(_mean_star_delta_e(glow, "dark", tier_index, veil)
               for veil in _VEILS["dark"])
    light = min(_mean_star_delta_e(glow, "light", tier_index, veil)
                for veil in _VEILS["light"])
    assert light >= dark * 0.9, (
        f"tier {tier_index}: light stars measure {light:.2f} dE through "
        f"their worst covering surface against dark's {dark:.2f} — light "
        "is back to being solved against the bare canvas")
    assert light <= dark * 1.35, (
        f"tier {tier_index}: light stars measure {light:.2f} dE against "
        f"dark's {dark:.2f} — the field is louder in light than in dark, "
        "which reads as speckle on paper rather than as atmosphere")


def test_every_light_star_clears_the_just_noticeable_threshold(window):
    """The floor, stated directly: a star nobody can see is not ambience.
    ~1.0 dE is the classic JND; the far tier shipped at 0.83."""
    glow = window._glow
    for tier_index in range(len(glow._PARTICLE_TIERS)):
        worst = min(_mean_star_delta_e(glow, "light", tier_index, veil)
                    for veil in _VEILS["light"])
        assert worst >= 1.0, (
            f"light tier {tier_index} measures {worst:.2f} dE through its "
            "worst covering surface — below the just-noticeable threshold")


def test_dark_star_weight_is_untouched(window):
    """Light was the bug; dark was the reference. A change that 'fixes'
    light by also moving dark has re-tuned the mode that was correct."""
    glow = window._glow
    assert glow._STAR_PMAX["dark"] == 0.34
    assert glow._STAR_SPAN_MUL["dark"] == 3.0


def test_the_light_wash_tints_the_paper_without_dyeing_it(window, qapp):
    """v11's rule, measured: light mode's canvas is the neutral system
    grey #F2F2F7, and the multiply wash may shade it but must not turn it
    into a colour. The regression this catches shipped twice — the wash
    dragged the page to a visible lavender (#ECEAF4), which is a light
    mode whose defining colour is no longer 'system grey'.

    Measured off the orb layer composited exactly as paintEvent does it,
    so adding orbs, raising peaks or changing the blend all land here.
    """
    glow = window._glow
    started_light = window.theme.t["name"] == "light"
    if not started_light:
        window._toggle_theme_animated()
        settle(qapp, 900)
    try:
        assert glow._light
        canvas = QPixmap(glow.width(), glow.height())
        canvas.fill(QColor("#F2F2F7"))
        painter = QPainter(canvas)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Multiply)
        glow._layer = None
        painter.drawPixmap(0, 0, glow._ensure_layer())
        painter.end()

        img = canvas.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        tint = samples = 0
        for y in range(0, img.height(), 5):
            for x in range(0, img.width(), 5):
                px = img.pixel(x, y)
                r, g, b = (px >> 16) & 255, (px >> 8) & 255, px & 255
                # the base grey is itself 5 wide (F2/F2/F7); anything the
                # wash adds on top of that is the tint under test
                tint += (max(r, g, b) - min(r, g, b)) - 5
                samples += 1
        mean = tint / samples
        # Calibrated against both ends: the shipping wash measures ~3.9,
        # and the peaks that shipped the lavender canvas measure ~10.4.
        assert mean <= 6.0, (
            f"the light wash adds {mean:.1f} of mean channel spread — the "
            "porcelain is being dyed, not tinted (the v10 regression that "
            "dragged #F2F2F7 to #ECEAF4 measures ~10)")
    finally:
        if not started_light:
            window._toggle_theme_animated()
            settle(qapp, 900)


def test_layer_rebuild_stays_inside_its_cadence(window, qapp):
    """The orb layer is rebuilt 10x a second on the UI thread, so its cost
    is a permanent background tax — measured at ~3.6ms for five orbs. The
    ceiling is a tenth of the cadence it runs at; past that, adding orbs
    is spending frame budget rather than depth."""
    glow = window._glow
    samples = []
    for _ in range(12):
        start = time.perf_counter()
        glow._build_layer(glow.width(), glow.height())
        samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    median = samples[len(samples) // 2]
    assert median < glow._LAYER_MS / 10.0, (
        f"orb layer rebuild median {median:.2f}ms against a "
        f"{glow._LAYER_MS}ms cadence — the ambient field is now a "
        "measurable share of the app's idle CPU")


#: Worst-case (whole-widget) repaint ceiling, per theme. Light is allowed
#: more because its layer is composited with CompositionMode_Multiply,
#: which is measurably dearer per pixel than dark's SourceOver — that is
#: the price of the wash being visible on porcelain at all. Measured on
#: the reference machine at 1360x900: 1.7ms dark, 2.2ms light, against a
#: 36ms frame at 28fps.
_PAINT_CEILING_MS = {"dark": 2.4, "light": 2.9}


@pytest.mark.parametrize("theme_name", ["dark", "light"])
def test_paint_cost_stays_within_the_frame_budget(window, qapp, theme_name):
    """A full glow repaint must stay well inside one display frame — it
    competes with the OS move/size loop on the same thread, which is what
    made dragging stutter.

    BOTH themes, explicitly. This used to measure whichever theme the
    session happened to be in — always dark, since the toggle test puts it
    back — so the mode with the dearer blend was never measured at all.
    """
    glow = window._glow
    started = window.theme.t["name"]
    if started != theme_name:
        window._toggle_theme_animated()
        settle(qapp, 900)
    try:
        glow.repaint()                  # warm the layer
        samples = []
        for _ in range(30):
            glow._layer_t = glow._t     # keep the cache warm; measure the blit
            start = time.perf_counter()
            glow.render(QPixmap(glow.size()))
            samples.append((time.perf_counter() - start) * 1000)
        samples.sort()
        median = samples[len(samples) // 2]
        assert median < _PAINT_CEILING_MS[theme_name], (
            f"ambient repaint median {median:.2f}ms in {theme_name} — was "
            "~2.7ms before the layer cache; a regression here shows up as "
            "drag/resize stutter")
    finally:
        if window.theme.t["name"] != started:
            window._toggle_theme_animated()
            settle(qapp, 900)


# ============================================================
#  CONTINUITY ACROSS NAVIGATION  (v10.3 final pass)
# ============================================================
# Reported from real-world testing on low-spec hardware: "the ambient
# circles freeze during a tab switch, then reset and restart their path".
#
# Nothing ever reset them — _build_particles runs once, in __init__. What
# happened is that _tick RETURNED BEFORE INTEGRATING while deferred, so a
# page switch cost the field the entire deferral: 150 ms warm, 360 ms the
# first time a module is opened (PAGE_FADE_MS, then CASCADE_BUDGET_MS +
# CASCADE_MS). Motion resumed from where it stopped instead of from where
# continuous motion would have put it, which from the outside is
# indistinguishable from the field snapping back.
#
# Measured over a three-lap sweep of all four modules before the fix: the
# field advanced 661 ms of a 2695 ms sweep — 75% frozen, with dead stalls
# of 1061 ms. The deferral itself is NOT the bug and must stay: the wash's
# cost is the full-window repaint it forces through every translucent
# surface above it (18.5 ms), and landing one mid-transition is the hitch
# it was introduced to remove. Skipping the PAINT is the whole saving;
# skipping the arithmetic with it was free to do and expensive to have done.

def _deferred_tick(glow, elapsed_s: float):
    """Drive one tick that lands `elapsed_s` after arming, while deferred."""
    glow.defer(5000)
    glow._armed_at = time.perf_counter() - elapsed_s
    glow._tick()


def test_a_deferred_tick_still_advances_the_field(window):
    """The whole fix in one assertion: a frame we choose not to PAINT is
    still a frame the field MOVED through."""
    glow = window._glow
    try:
        before_t = glow._t
        before_y = [p["y"] for p in glow._particles]
        _deferred_tick(glow, 0.10)

        assert glow._t > before_t, (
            "a deferred tick did not advance _t — the field is losing wall "
            "time on every page transition and will resume from a stale "
            "position, which is the reported 'freeze and snap back'")
        moved = sum(1 for p, y0 in zip(glow._particles, before_y)
                    if abs(p["y"] - y0) > 1e-9)
        assert moved == len(glow._particles), (
            f"only {moved}/{len(glow._particles)} particles drifted during a "
            "deferred frame")
    finally:
        glow._defer_until = 0.0


def test_a_deferred_tick_does_not_repaint(window):
    """...and the saving the deferral exists for is still banked. If this
    ever starts painting, navigation gets its 18.5 ms hitch back."""
    glow = window._glow
    calls = []
    original = glow.update
    glow.update = lambda *a, **k: calls.append(1)
    try:
        _deferred_tick(glow, 0.10)
        assert not calls, (
            "a deferred tick repainted — the deferral buys nothing and the "
            "page transition pays the full-window repaint it was meant to "
            "step out of the way of")
    finally:
        glow.update = original
        glow._defer_until = 0.0


def test_the_governor_ignores_deferred_ticks(window):
    """Lateness means 'adding a repaint here would hurt'. A deferred tick
    is not adding one, so it must not ratchet the field toward the ceiling
    — that is what left the wash crawling at 4.5fps for ~1s AFTER a switch
    had already finished."""
    glow = window._glow
    try:
        glow._interval = float(glow._INTERVAL_MS)
        # a tick arriving far beyond _LATE_MS, but deferred
        _deferred_tick(glow, (glow._LATE_MS + 200.0) / 1000.0)
        assert glow._interval == float(glow._INTERVAL_MS), (
            f"the governor backed off to {glow._interval:.0f}ms on a frame "
            "that never painted")
    finally:
        glow._defer_until = 0.0
        glow._interval = float(glow._INTERVAL_MS)


def test_navigation_does_not_freeze_the_field(window, qapp):
    """End-to-end: sweep every module and assert the field keeps up with
    the wall clock. Pre-fix this ran at 17-25% and is the user-visible
    symptom; the threshold is deliberately loose so it fails on a
    regression rather than on scheduler noise."""
    from frontend.menu_structure import CATEGORIES

    window._revealed.clear()          # force the 360 ms first-visit defer
    window.go_home()
    settle(qapp, 500)

    t_start = glow_t0 = window._glow._t
    wall_start = time.perf_counter()
    for _ in range(2):
        for index in range(len(CATEGORIES)):
            window.open_category(index)
            settle(qapp, 180)
    wall = time.perf_counter() - wall_start
    advanced = window._glow._t - t_start

    assert advanced > wall * 0.70, (
        f"the ambient field advanced {advanced * 1000:.0f} ms across a "
        f"{wall * 1000:.0f} ms navigation sweep "
        f"({advanced / wall * 100:.0f}% — pre-fix this was 17-25%). The wash "
        "is freezing through page transitions instead of only skipping the "
        "repaint.")
    assert glow_t0 == t_start          # guard the measurement itself
