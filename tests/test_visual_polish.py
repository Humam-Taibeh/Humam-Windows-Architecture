"""
Visual finish contracts — the v1.1 cosmetic polish pass.

Every assertion here replaced something that was found by MEASURING the
running app rather than by looking at it, which is the same standard the
layout and palette suites hold:

  * the card status badges tinted themselves in their own hue, the exact
    "badge-tint trap" strip_status_qss documents and avoids, and it cost
    them AA — 4.02:1 in light mode on a state-tinted card, on the smallest
    type in the product;
  * the masthead tagline did not elide, it CLIPPED, losing 40px mid-glyph
    at the app's own 980px minimum window width;
  * ElidedCaption measured its size hint off the text it had already
    elided, so the first squeeze was permanent — a ratchet;
  * four input fields had drifted to three different hover weights and the
    most-used one (Ctrl+K) had no hover state at all;
  * console_qss carried a byte-for-byte copy of the shared scrollbar rules.

None of these raises. All of them are invisible until somebody renders the
exact combination that exposes them.
"""
from __future__ import annotations

import re

import pytest

from conftest import settle
from frontend import theme as TH
from frontend.widgets import _CHIP_LANE, ElidedCaption, StatusDot


# ============================================================
#  COLOUR MATHS (shared with the palette suite's approach)
# ============================================================
def _srgb(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lum(rgb) -> float:
    r, g, b = (_srgb(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg, bg) -> float:
    a, b = _lum(fg), _lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _rgb(value: str):
    r, g, b, _a = TH._parse_color(value)
    return (r, g, b)


def _over(rgba, base):
    r, g, b, a = rgba
    return tuple(round(f * a + k * (1 - a)) for f, k in zip((r, g, b), base))


def _chip_background(qss: str) -> str:
    match = re.search(r"background:\s*([^;]+);", qss)
    assert match, f"no background in chip QSS: {qss!r}"
    return match.group(1).strip()


#: WCAG AA for normal-size text. These chips are 9px — unambiguously
#: normal text, so the 3:1 large-text allowance does not apply.
_AA = 4.5


# ============================================================
#  1. STATUS BADGES
# ============================================================
@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("verdict", ["applied", "mixed", "due", "default"])
def test_a_status_badge_clears_aa_on_the_worst_card_it_can_land_on(mode, verdict):
    """The badge's plate is OPAQUE, so the worst surface is the plate
    itself — whatever the card underneath is doing. That is the property
    under test as much as the ratio: before this, the same chip measured
    differently on a resting card and a running one."""
    t = TH.tokens(mode)
    qss = TH.state_chip_qss(t, verdict)
    fill = _rgb(_chip_background(qss))
    fg = _rgb(re.search(r"color:\s*([^;]+);", qss).group(1).strip())
    assert _ratio(fg, fill) >= _AA, (
        f"{verdict} badge in {mode} measures {_ratio(fg, fill):.2f}:1 — the "
        "own-hue tint that failed at 4.02:1 is back")


@pytest.mark.parametrize("mode", ["dark", "light"])
@pytest.mark.parametrize("verdict", ["applied", "mixed", "due", "default"])
def test_a_status_badge_plate_is_opaque(mode, verdict):
    """A translucent plate lets the card's running / flash tint through, so
    the badge's contrast — and its colour — become a function of what the
    CARD is doing. A status badge must report exactly one thing."""
    t = TH.tokens(mode)
    fill = _chip_background(TH.state_chip_qss(t, verdict))
    _r, _g, _b, a = TH._parse_color(fill)
    assert a >= 1.0, (
        f"{verdict} badge in {mode} fills with {fill!r} (alpha {a}) — the "
        "card's state tint will bleed through it")


def test_every_verdict_shares_one_badge_geometry():
    """Three settings of one control, not three kinds of object. The
    neutral DEFAULT chip used to be the odd one out — transparent where
    the toned pair were filled."""
    t = TH.tokens("dark")
    shapes = set()
    for verdict in ("applied", "mixed", "due", "default"):
        qss = TH.state_chip_qss(t, verdict)
        shapes.add((
            re.search(r"border-radius:\s*([^;]+);", qss).group(1).strip(),
            re.search(r"padding:\s*([^;]+);", qss).group(1).strip(),
            re.search(r"font-size:\s*([^;]+);", qss).group(1).strip(),
            re.search(r"font-weight:\s*([^;]+);", qss).group(1).strip(),
            re.search(r"letter-spacing:\s*([^;]+);", qss).group(1).strip(),
        ))
    assert len(shapes) == 1, f"badges render at {len(shapes)} geometries: {shapes}"


def test_the_tone_whisper_stays_under_the_measured_ceiling():
    """CHIP_TONE_WHISPER is the one number that can walk this back toward
    the failure it fixed, so it is pinned with its reason."""
    assert TH.CHIP_TONE_WHISPER <= 0.10, (
        "the badge is being tinted back toward its own hue; re-measure the "
        "contrast table in state_chip_qss before raising this")


# ============================================================
#  2. SCROLLBARS — ONE DEFINITION
# ============================================================
def test_every_scrolling_surface_composes_the_shared_scrollbar():
    """console_qss carried its own copy of all fourteen rules. A copy is a
    thing that drifts, and this one was invisible because a QPlainTextEdit
    scrolls itself and so never picked up the shared sheet."""
    t = TH.tokens("dark")
    shared = TH.scrollbar_qss(t)
    for name in ("scroll_area_qss", "console_qss", "chip_strip_qss",
                 "command_list_qss"):
        built = getattr(TH, name)(t)
        assert "QScrollBar::handle:vertical" in built, (
            f"{name} defines no scrollbar at all")
        # the shared sheet must be present verbatim, not paraphrased
        assert shared.strip() in built, (
            f"{name} does not compose scrollbar_qss — it has its own copy")


def test_the_pill_strip_lane_is_derived_from_the_scrollbar():
    """Two literals that agreed only by luck. When they disagreed the
    handle resolved to zero pixels: a strip that scrolls with no visible
    scrollbar."""
    assert _CHIP_LANE == TH.scrollbar_lane()
    assert f"height: {TH.scrollbar_lane()}px" in TH.chip_strip_qss(
        TH.tokens("dark"))


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_the_scrollbar_acknowledges_a_drag(mode):
    t = TH.tokens(mode)
    qss = TH.scrollbar_qss(t)
    assert "QScrollBar::handle:vertical:pressed" in qss
    assert "QScrollBar::handle:horizontal:pressed" in qss


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_the_scroll_corner_is_never_platform_grey(mode):
    """The square where two bars meet renders as a stock grey tile unless
    it is explicitly cleared."""
    assert "QAbstractScrollArea::corner" in TH.scrollbar_qss(TH.tokens(mode))


# ============================================================
#  3. INPUT FIELDS — ONE HOVER, ONE FOCUS
# ============================================================
#: (builder, needs an accent argument)
_FIELDS = [
    ("sidebar_search_qss", False),
    ("filter_combo_qss", True),
    ("catalog_search_qss", True),
    ("command_input_qss", False),
]


@pytest.mark.parametrize("name,accented", _FIELDS)
@pytest.mark.parametrize("mode", ["dark", "light"])
def test_every_field_answers_both_the_pointer_and_the_keyboard(name, accented, mode):
    t = TH.tokens(mode)
    build = getattr(TH, name)
    qss = build(t, t["accent"]) if accented else build(t)
    assert ":hover" in qss, (
        f"{name} never acknowledges the pointer — the defect the Ctrl+K "
        "palette shipped with")
    if name != "sidebar_search_qss":     # a button, focus is not its state
        assert ":focus" in qss, f"{name} does not mark keyboard focus"


@pytest.mark.parametrize("name,accented", _FIELDS)
def test_every_field_uses_the_shared_state_weights(name, accented):
    """Hover was 0.45 / 0.35 / 0.45 / absent and focus 0.65 / 0.65 / 0.55
    across these four. Nothing chose those numbers."""
    t = TH.tokens("dark")
    build = getattr(TH, name)
    qss = build(t, t["accent"]) if accented else build(t)
    hover = f"{TH.FIELD['hover']:.3f}"
    assert hover in qss, (
        f"{name} does not use TH.FIELD['hover'] ({TH.FIELD['hover']})")


def test_focus_outranks_hover():
    """The scale has to stay ordered, or focus becomes the quieter state."""
    assert TH.FIELD["focus"] > TH.FIELD["hover"]


# ============================================================
#  4. CARD HOVER ELEVATION
# ============================================================
def test_the_hover_lift_is_quantized():
    """paint_drop_shadow and paint_top_sheen cache rasterised strokes keyed
    on alpha, behind a hard 96-entry bound that clears WHOLESALE. A
    continuous lift would mint a fresh full-size stroke every frame of
    every hover and thrash the cache for the entire app."""
    seen = {TH.hover_lift(i / 500.0) for i in range(501)}
    assert len(seen) <= TH.HOVER_LIFT_STEPS + 1, (
        f"{len(seen)} distinct lift values — the stroke cache will thrash")


def test_the_hover_lift_spans_rest_to_full():
    assert TH.hover_lift(0.0) == 0.0
    assert TH.hover_lift(1.0) == 1.0
    assert TH.hover_lift(0.5) == pytest.approx(0.5)


def test_a_hovered_card_rises_rather_than_flattening():
    """Both cues move in the same direction. A lift that deepened the
    shadow while dulling the sheen would read as the card pressing IN."""
    assert TH.HOVER_LIFT_SHADOW > 1.0
    assert TH.HOVER_LIFT_SHEEN > 0.0


@pytest.mark.native
def test_a_hovered_card_paints_its_accent_perimeter(window, qapp):
    """The hairline is what says 'this whole card'; the cursor-tracking
    glow only lights the edge nearest the pointer."""
    from PySide6.QtGui import QPixmap
    window.open_category(1)
    settle(qapp, 400)
    card = window.pages[1].cards[0]

    def render():
        shot = QPixmap(card.size())
        card.render(shot)
        return shot.toImage()

    card._glow._intensity = 0.0
    rest = render()
    card._glow._intensity = 1.0
    hovered = render()
    card._glow._intensity = 0.0

    # sample the left edge, mid-height: away from the corners, and away
    # from the cursor the glow sweep centres on (0,0 by default)
    y = hovered.height() // 2
    changed = sum(1 for x in range(0, 3)
                  if hovered.pixel(x, y) != rest.pixel(x, y))
    assert changed, (
        "the far edge of a hovered card is unchanged — only the cursor "
        "sweep is painting, so the hairline is gone")


# ============================================================
#  5. THE MASTHEAD TAGLINE
# ============================================================
class TestElidedCaption:

    def test_the_size_hint_is_measured_off_the_full_text(self, qapp):
        """The ratchet: QLabel.sizeHint() reports the CURRENT text, which
        is the elided string, so the hint shrinks to the elision and the
        elision never reverses. Fixed once the caption stopped asking the
        base class."""
        cap = ElidedCaption(max_width=400)
        cap.setFullText("Enterprise-Grade Windows Orchestration")
        wide = cap.sizeHint().width()
        # shown, because Qt only delivers the resize event that drives the
        # elision to a widget that is actually realised
        cap.show()
        cap.resize(80, 20)
        qapp.processEvents()
        try:
            assert cap.text() != cap.fullText(), "test needs an actual elision"
            assert cap.sizeHint().width() == wide, (
                "the size hint collapsed to the elided width — the caption "
                "can never grow back when the room returns")
        finally:
            cap.hide()
            cap.deleteLater()
            qapp.processEvents()

    def test_the_width_ceiling_is_per_instance(self, qapp):
        """The card footer's 120px cap must not follow the class onto the
        masthead, where it would truncate a 255px tagline at every size."""
        text = "Enterprise-Grade Windows Orchestration"
        narrow = ElidedCaption()
        narrow.setFullText(text)
        wide = ElidedCaption(max_width=400)
        wide.setFullText(text)
        assert narrow.sizeHint().width() == ElidedCaption.MAX_WIDTH
        assert wide.sizeHint().width() > ElidedCaption.MAX_WIDTH

    def test_it_never_forces_its_parent_wider(self, qapp):
        cap = ElidedCaption(max_width=400)
        cap.setFullText("Enterprise-Grade Windows Orchestration")
        assert cap.minimumSizeHint().width() == 0


@pytest.mark.native
class TestMastheadTagline:

    def test_it_renders_in_full_when_there_is_room(self, window, qapp):
        original = window.size()
        try:
            # The masthead lives on the welcome page, and a page the stack
            # is not showing never receives the resize that re-elides it —
            # the session-shared window fixture may be parked on a module.
            window.go_home()
            window.resize(1400, 900)
            settle(qapp, 250)
            tag = window.welcome._tag
            assert tag.text() == tag.fullText(), (
                "the tagline is truncated on a wide window")
        finally:
            window.resize(original)
            settle(qapp, 150)

    def test_it_elides_rather_than_clipping_at_the_minimum_width(
            self, window, qapp):
        """MEASURED at the app's own minimum. A plain QLabel squeezed below
        its text width does not elide — it clips mid-glyph with nothing to
        say anything was lost. At 980px this lost 40px of
        'Enterprise-Grade Windows Orchestration'."""
        original = window.size()
        try:
            window.go_home()
            window.resize(window.minimumWidth(), 800)
            settle(qapp, 300)
            tag = window.welcome._tag
            from PySide6.QtGui import QFontMetrics
            painted = QFontMetrics(tag.font()).horizontalAdvance(tag.text())
            assert painted <= tag.width(), (
                f"the tagline paints {painted}px into a {tag.width()}px "
                "label — it is being clipped, not elided")
            assert tag.text() != tag.fullText()
            assert tag.text().endswith("…"), (
                "truncated with no ellipsis — nothing tells the user text "
                "was dropped")
        finally:
            window.resize(original)
            settle(qapp, 150)

    def test_it_comes_back_when_the_window_grows(self, window, qapp):
        original = window.size()
        try:
            window.go_home()
            window.resize(window.minimumWidth(), 800)
            settle(qapp, 300)
            assert window.welcome._tag.text() != window.welcome._tag.fullText()
            window.resize(1400, 900)
            settle(qapp, 300)
            tag = window.welcome._tag
            assert tag.text() == tag.fullText(), (
                "the tagline stayed elided after the window grew — the "
                "size-hint ratchet is back")
        finally:
            window.resize(original)
            settle(qapp, 150)


# ============================================================
#  6. THE LIVE STATUS INDICATOR
# ============================================================
class TestStatusDot:

    def test_the_idle_breath_is_slower_and_shallower_than_the_busy_one(self):
        """Amplitude and rate move together. A slow breath at the busy
        depth reads as something wrong; a fast one at the idle depth reads
        as a rendering glitch."""
        busy_ms, busy_floor = StatusDot._BUSY
        idle_ms, idle_floor = StatusDot._IDLE
        assert idle_ms > busy_ms
        assert idle_floor > busy_floor

    def test_the_idle_breath_is_subtle_enough_not_to_pull_the_eye(self):
        _idle_ms, idle_floor = StatusDot._IDLE
        assert idle_floor >= 0.6, (
            "an idle indicator dipping this deep reads as a warning "
            "blinking at the user")

    def test_it_keeps_breathing_when_work_finishes(self, qapp):
        """The whole point of the change: 'System Ready' should look alive,
        not printed. stop_pulse() used to freeze the dot solid."""
        from PySide6.QtCore import QVariantAnimation
        dot = StatusDot()
        dot.show()
        qapp.processEvents()
        try:
            dot.start_pulse()
            assert dot._anim.duration() == StatusDot._BUSY[0]
            dot.stop_pulse()
            assert dot._anim.duration() == StatusDot._IDLE[0]
            assert dot._anim.state() == QVariantAnimation.State.Running, (
                "the status dot went static when the task ended")
        finally:
            dot.hide()
            dot.deleteLater()
            qapp.processEvents()

    def test_it_does_not_animate_while_hidden(self, qapp):
        from PySide6.QtCore import QVariantAnimation
        dot = StatusDot()
        dot.show()
        qapp.processEvents()
        dot.hide()
        qapp.processEvents()
        assert dot._anim.state() != QVariantAnimation.State.Running
        dot.deleteLater()
        qapp.processEvents()


# ============================================================
#  7. THE FROSTED MODAL BACKDROP
# ============================================================
@pytest.mark.native
class TestFrostedBackdrop:

    def _catalog(self, window):
        from frontend import menu_structure as MS
        from frontend import widgets as W
        item = {"icon": "\U0001F4E6", "title": "Demo", "desc": "Demo.",
                "task": "SystemInfo"}
        return W.SoftwareCatalogDialog(window, item, window.theme.t,
                                       MS.SOFTWARE_CATALOG)

    def test_a_modal_frosts_what_it_covers(self, window, qapp):
        dialog = self._catalog(window)
        dialog.resize(window.size())
        dialog.show()
        qapp.processEvents()
        try:
            assert dialog._frost is not None, (
                "the modal fell back to a flat scrim — no glass at all")
            assert not dialog._frost.isNull()
        finally:
            dialog.reject()
            dialog.deleteLater()
            qapp.processEvents()

    def test_the_frost_is_retained_at_blur_resolution(self, window, qapp):
        """Kept small on purpose: the downscale IS the blur, and holding a
        full-size copy would cost a second smooth scale of ~1.8M pixels
        plus the allocation, for pixels carrying no extra information."""
        dialog = self._catalog(window)
        dialog.resize(window.size())
        dialog.show()
        qapp.processEvents()
        try:
            frost = dialog._frost
            assert frost is not None
            assert frost.width() <= dialog.width() // 4, (
                f"frost retained at {frost.width()}px against a "
                f"{dialog.width()}px dialog — it is being kept full-size")
        finally:
            dialog.reject()
            dialog.deleteLater()
            qapp.processEvents()

    def test_a_resize_does_not_recapture_per_step(self, window, qapp):
        """A capture renders the whole host window. A drag emits resize
        events continuously, and paying per step would put the backdrop
        refresh directly in the way of the drag."""
        dialog = self._catalog(window)
        dialog.resize(window.size())
        dialog.show()
        qapp.processEvents()
        try:
            captures = []
            original = dialog._capture_backdrop

            def counted():
                captures.append(1)
                return original()

            dialog._capture_backdrop = counted
            dialog._refrost.timeout.disconnect()
            dialog._refrost.timeout.connect(counted)
            for width in range(900, 1000, 10):
                dialog.resize(width, dialog.height())
                qapp.processEvents()
            assert not captures, "captured mid-drag"
            settle(qapp, 320)
            assert len(captures) == 1, (
                f"{len(captures)} captures for one resize burst")
        finally:
            dialog.reject()
            dialog.deleteLater()
            qapp.processEvents()

    def test_a_parentless_dialog_degrades_to_the_flat_scrim(self, qapp):
        """Failure is silent and total by design — the flat scrim is what
        shipped for every version before this."""
        from frontend.widgets import PulseDialog
        dialog = PulseDialog(None)
        dialog.resize(400, 300)
        dialog._capture_backdrop()
        assert dialog._frost is None
        dialog.deleteLater()
        qapp.processEvents()
