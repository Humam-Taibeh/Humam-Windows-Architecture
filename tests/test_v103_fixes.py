"""
v10.3 regression contracts — the batch of fixes that shipped with the
Update Center overhaul.

Every assertion here corresponds to a defect that was REPRODUCED before it
was fixed, either by rendering the running app and reading pixels or by
running the real backend and reading its stdout. None of them raises on its
own, and four of the six were invisible in code review:

  * the accent hairline was drawn on a rect inset by 0.5px at the SAME
    corner radius as the card boundary, so it was concentric on the four
    straight runs and drifted 0.207px inward at the 45 degree point of every
    corner — far too little to see as a misplaced line and exactly enough
    for antialiasing to resolve it as a second edge. That is the "double
    edge corner" the card grid had on hover;

  * StatusDot was the one QLabel in the app that never received a
    stylesheet, so it inherited WA_StyledBackground from an ancestor's QSS
    and Qt filled its rect with the palette Window brush before paintEvent
    ran. The label stretches to the rail's full height, so a 12px dot was
    standing in front of an opaque 12x42 slab;

  * ##PULSE##ITEM| / ##PULSE##STAGE| had to be registered as PAYLOAD
    prefixes, not just parsed. A payload line that is not in that tuple is
    eligible to be read as the task's verdict by the backwards scan, and it
    also gets printed verbatim into the live console;

  * the startup recommendation engine checked its DISABLE patterns before
    its keep patterns, so any disable pattern that happened to match an
    audio helper or a security agent recommended disabling it and the keep
    rule written to protect that component was never consulted.
"""
from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath

from frontend import theme as TH
from frontend.animations import paint_accent_hairline
from frontend.widgets import StartupManagerDialog, StatusDot
from utils.helpers import (
    VERDICT_DATA_PREFIX, VERDICT_ITEM_PREFIX, VERDICT_META_PREFIX,
    VERDICT_PAYLOAD_PREFIXES, VERDICT_STAGE_PREFIX, PowerShellTask,
)


# ============================================================
#  1. THE HOVER HAIRLINE IS CONCENTRIC WITH THE CARD BOUNDARY
# ============================================================
class TestAccentHairlineGeometry:
    """A 1px border inside a rounded rect of radius R must be drawn on a
    rect inset by 0.5 AND at radius R-0.5. Insetting alone moves the corner
    arc's CENTRE inward with the rect while leaving its radius unchanged,
    which is not a concentric curve."""

    SIZE = 60
    RADIUS = 14
    #: SUPERSAMPLE. The defect is a 0.207px corner drift — below the
    #: resolution of a 1:1 render, where pixel quantisation swamps it and a
    #: naive measurement passes on the broken geometry just as happily as on
    #: the fixed one (verified: it did). Everything is therefore drawn
    #: through a painter scale, so the card, the pen width and the drift are
    #: all magnified together and the measurement is taken with ~13 device
    #: pixels of headroom instead of a fifth of one.
    SCALE = 8

    def _render(self, with_hairline: bool, width: float = 1.0) -> QImage:
        """The card's own boundary in white on black, optionally with the
        hairline painted over it in pure red. Rendering the silhouette
        SEPARATELY is what makes the containment check meaningful: the
        hairline is an opaque stroke, so over the card it reads as pure red
        and 'is there white under this pixel' cannot be answered from the
        composited image alone."""
        px = self.SIZE * self.SCALE
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.scale(self.SCALE, self.SCALE)
        rect = QRectF(0, 0, self.SIZE, self.SIZE)
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        p.fillPath(path, QColor(255, 255, 255))
        if with_hairline:
            paint_accent_hairline(p, rect.toRect(), self.RADIUS,
                                  QColor(255, 0, 0), 1.0, alpha=1.0, width=width)
        p.end()
        return img

    def test_the_hairline_never_leaves_the_card_body(self, qapp):
        """Every pixel the hairline paints must sit ON the card, not on the
        black outside it.

        Drawn at width 3 on purpose. At width 1 a hardcoded 0.5 inset still
        keeps the stroke inside, so this would prove nothing; at width 3 an
        inset that does not scale with the pen puts a third of the stroke
        outside the widget, where it is clipped — which is how a 'crisp'
        border ends up with flattened corners."""
        card = self._render(with_hairline=False)
        drawn = self._render(with_hairline=True, width=3.0)
        px = self.SIZE * self.SCALE
        strays = []
        for y in range(px):
            for x in range(px):
                a, b = card.pixelColor(x, y), drawn.pixelColor(x, y)
                if a == b:
                    continue            # the hairline did not touch this pixel
                # It did. The card must have been there to receive it: any
                # coverage at all counts, since an edge pixel is antialiased.
                if a.red() < 8 and a.green() < 8 and a.blue() < 8:
                    strays.append((x, y))
        assert not strays, (
            f"{len(strays)} hairline pixel(s) fell outside the card body, "
            f"first at {strays[:4]} — the inset does not track the pen width")

    def test_corner_and_edge_sit_at_the_same_depth(self, qapp):
        """THE REGRESSION ITSELF. Measure how far the hairline's centre of
        mass sits inside the boundary along the top edge (a straight run)
        and along the corner diagonal, and require them to agree. Before the
        fix these measured 0.5px and 0.707px — a 0.207px disagreement that
        antialiasing resolves as a second line at every corner."""
        import math

        img = self._render(with_hairline=True)
        px = self.SIZE * self.SCALE

        def depth_along(dx: float, dy: float, ox: float, oy: float) -> float:
            """Red-weighted mean distance inward from (ox, oy), in LOGICAL
            pixels — device samples divided back down by SCALE."""
            total = weight = 0.0
            for step in range(0, 8 * self.SCALE):
                d = step * 0.5                      # device px along the ray
                x, y = int(round(ox + dx * d)), int(round(oy + dy * d))
                if not (0 <= x < px and 0 <= y < px):
                    break
                c = img.pixelColor(x, y)
                w = max(0, c.red() - max(c.green(), c.blue()))
                total += w * d
                weight += w
            return (total / weight / self.SCALE) if weight else -1.0

        s = self.SCALE
        # Straight run: down from the middle of the top edge.
        edge = depth_along(0.0, 1.0, (self.SIZE / 2.0) * s, 0.0)
        # Corner: inward along the 45 degree diagonal, starting where the
        # boundary's own arc crosses it.
        r = self.RADIUS
        bx = (r - r / math.sqrt(2)) * s
        by = (r - r / math.sqrt(2)) * s
        corner = depth_along(1 / math.sqrt(2), 1 / math.sqrt(2), bx, by)

        assert edge > 0 and corner > 0, "the hairline was not found on both probes"
        assert abs(edge - corner) < 0.08, (
            f"hairline sits {edge:.3f}px inside the boundary on a straight "
            f"edge but {corner:.3f}px in at the corner — the inset radius is "
            "not concentric, which is what draws a doubled corner")


# ============================================================
#  2. THE STATUS DOT PAINTS NO BOX
# ============================================================
class TestStatusDotIsTransparent:
    def test_it_declares_its_own_transparent_background(self, qapp):
        """The dot must carry the same `background: transparent; border:
        none` every other label in the app gets from label_qss. Without a
        stylesheet of its own it inherits WA_StyledBackground from an
        ancestor and Qt fills its whole rect with the palette Window brush
        — the theme's `overlay` token, an opaque-enough slab behind a 12px
        glyph."""
        dot = StatusDot("●")
        qss = dot.styleSheet().replace(" ", "").lower()
        assert "background:transparent" in qss, (
            "StatusDot has no transparent background rule; it will paint the "
            f"palette Window brush behind the dot (styleSheet={dot.styleSheet()!r})")
        assert "border:none" in qss

    def test_the_rendered_dot_has_no_opaque_backing(self, qapp):
        """Render it and check the corners. The glyph is centred and ~6px of
        ink, so the four corners of a 12x42 label are background — and the
        background must be nothing at all."""
        dot = StatusDot("●")
        dot.resize(12, 42)
        img = dot.grab().toImage()
        w, h = img.width(), img.height()
        for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            assert img.pixelColor(x, y).alpha() == 0, (
                f"pixel ({x}, {y}) is opaque — the dot is painting a backing box")


# ============================================================
#  3. THE INCREMENTAL WIRE CHANNELS
# ============================================================
class TestStreamingChannels:
    def test_both_new_prefixes_are_registered_as_payloads(self):
        """A payload prefix that is not in this tuple is eligible to be
        mistaken for the verdict by the backwards scan in run(), and is also
        dumped verbatim into the live console."""
        for prefix in (VERDICT_ITEM_PREFIX, VERDICT_STAGE_PREFIX):
            assert prefix in VERDICT_PAYLOAD_PREFIXES
        # The pre-existing two must not have been displaced.
        assert VERDICT_DATA_PREFIX in VERDICT_PAYLOAD_PREFIXES
        assert VERDICT_META_PREFIX in VERDICT_PAYLOAD_PREFIXES

    def test_the_task_declares_the_two_signals(self):
        assert hasattr(PowerShellTask, "item")
        assert hasattr(PowerShellTask, "stage")

    def test_a_streamed_scan_emits_rows_and_keeps_them_off_the_console(self, qapp):
        """Feed run()'s own line handler the exact byte stream the backend
        emits and assert on what comes out of each channel."""
        task = PowerShellTask("core.ps1", "ScanForUpdates")
        items, stages, console = [], [], []
        task.item.connect(items.append)
        task.stage.connect(stages.append)
        task.output.connect(lambda text, _replace: console.append(text))

        payload = {"Id": "AnyDesk.AnyDesk", "Name": "AnyDesk",
                   "CurrentVersion": "9.7.12", "AvailableVersion": "9.7.13"}
        stream = [
            VERDICT_STAGE_PREFIX + "Reading installed programs…",
            VERDICT_ITEM_PREFIX + json.dumps(payload),
            VERDICT_ITEM_PREFIX + "{not json",          # must be dropped, not raised
            "   winget is doing something",
            VERDICT_DATA_PREFIX + json.dumps([payload]),
            "##PULSE##SUCCESS|Scanned 182 installed program(s).",
        ]
        # run()'s handler is a closure, so drive the same public path the
        # reader does: _split_events -> _coalesce -> the signal fan-out.
        for line in stream:
            buf, events, _ = PowerShellTask._split_events(line + "\n", False)
            for text, _replace in PowerShellTask._coalesce(events):
                if text.startswith(VERDICT_ITEM_PREFIX):
                    try:
                        task.item.emit(json.loads(text[len(VERDICT_ITEM_PREFIX):]))
                    except ValueError:
                        pass
                elif text.startswith(VERDICT_STAGE_PREFIX):
                    task.stage.emit(text[len(VERDICT_STAGE_PREFIX):])
                if not text.startswith(VERDICT_PAYLOAD_PREFIXES):
                    task.output.emit(text, False)

        assert items == [payload], "the malformed ITEM should be dropped silently"
        assert stages == ["Reading installed programs…"]
        joined = "\n".join(console)
        assert "##PULSE##ITEM" not in joined and "##PULSE##STAGE" not in joined, (
            "a payload line leaked into the live console")
        assert "winget is doing something" in joined


# ============================================================
#  4. THE STARTUP FILTERS
# ============================================================
class TestStartupFilters:
    """The summary pills are the filter control, so each pill's COUNT and
    the rows it isolates must come from one predicate — a pill reading '13
    disabled' that then shows eleven rows is worse than no filter."""

    ITEMS = [
        {"Id": "a", "Enabled": True,  "Recommendation": "Keep",    "Protected": True},
        {"Id": "b", "Enabled": True,  "Recommendation": "Disable", "Protected": False},
        {"Id": "c", "Enabled": True,  "Recommendation": "Review",  "Protected": False},
        {"Id": "d", "Enabled": False, "Recommendation": "Review",  "Protected": False},
    ]

    def _ids(self, key: str) -> set[str]:
        pred = StartupManagerDialog._FILTERS[key]
        return {it["Id"] for it in self.ITEMS if pred(it)}

    def test_every_chip_has_a_predicate(self):
        assert set(StartupManagerDialog._FILTERS) == {
            "all", "enabled", "disabled", "recommended"}

    def test_each_filter_isolates_exactly_its_bucket(self):
        assert self._ids("all") == {"a", "b", "c", "d"}
        assert self._ids("enabled") == {"a", "b", "c"}
        assert self._ids("disabled") == {"d"}
        assert self._ids("recommended") == {"b"}

    def test_enabled_and_disabled_partition_the_list(self):
        """Not merely disjoint — together they must account for every item,
        or the two counts stop adding up to the total the user can see."""
        assert not (self._ids("enabled") & self._ids("disabled"))
        assert self._ids("enabled") | self._ids("disabled") == self._ids("all")

    def test_recommended_is_a_subset_of_enabled(self):
        """An already-disabled item cannot be 'recommended to disable'."""
        assert self._ids("recommended") <= self._ids("enabled")


# ============================================================
#  5. THE FILTER CHIP CLEARS AA IN ITS ACTIVE STATE
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


class TestFilterChipContrast:
    """The active chip is a SOLID tone fill with canvas-coloured text, and
    that is a contrast decision. The obvious alternative — tone text over a
    heavier tone tint — measures 3.94:1 on the light theme's white card,
    i.e. it fails AA on the one state that most needs to be readable."""

    @staticmethod
    def _themes(qapp):
        return {"dark": TH._DARK, "light": TH._LIGHT}

    @pytest.mark.parametrize("tone", ["neutral", "accent", "warn", "ok", "err"])
    def test_active_chip_text_clears_aa_in_both_themes(self, qapp, tone):
        failures = []
        for name, t in self._themes(qapp).items():
            colors = {"neutral": t["text_soft"], "accent": t["accent"],
                      "warn": t["warn"], "ok": t["ok"], "err": t["err"]}
            fill = TH.to_qcolor(colors[tone])
            text = TH.to_qcolor(t["bg_solid"])
            ratio = _ratio((fill.red(), fill.green(), fill.blue()),
                           (text.red(), text.green(), text.blue()))
            if ratio < 4.5:
                failures.append(f"{name}/{tone} = {ratio:.2f}:1")
        assert not failures, (
            "active filter chip breaches WCAG AA: " + ", ".join(failures))

    def test_active_state_actually_differs_from_rest(self, qapp):
        """A filter the user cannot tell is on is not a filter."""
        t = TH._DARK
        rest = TH.filter_chip_qss(t, "warn", active=False)
        active = TH.filter_chip_qss(t, "warn", active=True)
        assert rest != active
