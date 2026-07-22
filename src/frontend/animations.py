"""
src/frontend/animations.py

MOTION SUBSYSTEM — every animation in the app, engineered for 60 fps.

Performance doctrine (this is what fixed the stutter):
    1. NO QGraphicsEffect in steady state. QGraphicsDropShadowEffect and
       friends force the whole widget subtree through a CPU-rasterized
       offscreen pixmap on every repaint — that was the old hover-glow lag.
       Hover glows are now painted directly in paintEvent (GlowController
       + paint_glow_frame): a two-pass gradient stroke, microseconds each.
    2. NO setStyleSheet() inside timers. The old shimmer rebuilt a QSS
       string every 40 ms, forcing a full style re-polish 25×/sec.
       ShimmerBar paints its gradient itself; a repaint costs ~0.05 ms.
    3. QVariantAnimation everywhere. It rides Qt's unified animation
       driver (~60 fps, frame-coalesced) instead of ad-hoc QTimers, and
       gives us clean easing curves for free.
    4. Opacity effects appear ONLY transiently (cascade entrance / page
       fade), are shared with a QParallelAnimationGroup, and are destroyed
       the instant the animation finishes.

Import graph: theme.py <- animations.py <- widgets.py <- main.py
(this module never imports widgets or main).
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint,
    QPointF, QPropertyAnimation, QRectF, QSequentialAnimationGroup,
    QVariantAnimation, Qt,
)
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# ============================================================
#  MOTION CONSTANTS — one place to tune the whole app's feel
# ============================================================
HOVER_MS      = 130    # glow ramp in/out
CASCADE_MS    = 170    # per-card entrance
CASCADE_GAP   = 26     # stagger between cards
CASCADE_RISE  = 18     # px slide-up distance
PAGE_FADE_MS  = 150    # stacked-page cross fade
SHIMMER_MS    = 1200   # one full progress sweep (indeterminate loop, not a
                       # transition — left at its original pace on purpose)

EASE_OUT  = QEasingCurve.Type.OutCubic
EASE_INOUT = QEasingCurve.Type.InOutQuad


# ============================================================
#  HOVER GLOW — effect-free, cursor-tracking border sweep
# ============================================================
class GlowController(QObject):
    """Drives a hover glow WITHOUT QGraphicsEffect.

    Install on any widget whose paintEvent calls paint_glow_frame():

        self._glow = GlowController(self, accent="#00d4ff")
        ...
        def paintEvent(self, e):
            super().paintEvent(e)
            p = QPainter(self)
            paint_glow_frame(p, self.rect(), radius=16,
                             color=self._glow.color,
                             intensity=self._glow.intensity,
                             cursor=self._glow.cursor)

    The controller animates a 0..1 intensity on Enter/Leave (OutCubic,
    HOVER_MS) and tracks the cursor so the radial sweep follows the mouse.
    Repaints are driven by the animation frames + hover moves only.
    """

    def __init__(self, widget: QWidget, accent: str = "#4cc2ff"):
        super().__init__(widget)
        self._widget = widget
        self._intensity = 0.0
        self._cursor = QPointF()
        self.color = QColor(accent)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(HOVER_MS)
        self._anim.setEasingCurve(EASE_OUT)
        self._anim.valueChanged.connect(self._on_frame)

        widget.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        widget.installEventFilter(self)

    # -- public state read by paintEvent ----------------------
    @property
    def intensity(self) -> float:
        return self._intensity

    @property
    def cursor(self) -> QPointF:
        return self._cursor

    def set_accent(self, accent: str):
        """Live theme switch — next repaint uses the new color."""
        self.color = QColor(accent)
        self._widget.update()

    # -- internals --------------------------------------------
    def _on_frame(self, value: float):
        self._intensity = float(value)
        self._widget.update()

    def _ramp_to(self, target: float):
        self._anim.stop()
        self._anim.setStartValue(self._intensity)
        self._anim.setEndValue(target)
        self._anim.start()

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.HoverEnter:
            self._cursor = event.position()
            self._ramp_to(1.0)
        elif et == QEvent.Type.HoverLeave:
            self._ramp_to(0.0)
        elif et == QEvent.Type.HoverMove and self._intensity > 0.0:
            self._cursor = event.position()
            self._widget.update()
        return False


def paint_glow_frame(painter: QPainter, rect, radius: int,
                     color: QColor, intensity: float,
                     cursor: QPointF | None = None):
    """Paint a radial-gradient border glow centered on the cursor.

    Two gradient strokes on a rounded rect — no offscreen buffers, no
    effects, safe to call on every repaint. Cost is negligible even with
    a full grid of cards hovered rapidly.
    """
    if intensity <= 0.01:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    center = cursor if cursor is not None else QPointF(rect.center())
    reach = max(rect.width(), rect.height()) * 0.95
    inner = rect.adjusted(1, 1, -1, -1)

    # pass 1: soft outer halo
    halo = QRadialGradient(center, reach)
    c = QColor(color)
    c.setAlphaF(0.38 * intensity)
    halo.setColorAt(0.0, c)
    c2 = QColor(color)
    c2.setAlphaF(0.0)
    halo.setColorAt(1.0, c2)
    painter.setPen(QPen(QBrush(halo), 5.0))
    painter.drawRoundedRect(inner, radius, radius)

    # pass 2: crisp inner edge
    edge = QRadialGradient(center, reach * 0.8)
    e1 = QColor(color)
    e1.setAlphaF(0.90 * intensity)
    edge.setColorAt(0.0, e1)
    e2 = QColor(color)
    e2.setAlphaF(0.10 * intensity)
    edge.setColorAt(1.0, e2)
    painter.setPen(QPen(QBrush(edge), 1.6))
    painter.drawRoundedRect(inner, radius, radius)
    painter.restore()


def paint_nav_indicator(painter: QPainter, rect, c1: QColor, c2: QColor,
                        inset: int = 8, bar_width: float = 3.0):
    """Left-edge active-item bar for the selected sidebar entry — the same
    affordance Windows 11 Settings uses to mark its selected nav item.
    A short rounded bar with the app's accent->accent2 brand gradient
    running top to bottom; call only while the item is selected (see
    widgets.NavButton.paintEvent). One drawRoundedRect, no offscreen buffer.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    bar = QRectF(rect.left() + 4, rect.top() + inset,
                bar_width, rect.height() - inset * 2)
    grad = QLinearGradient(bar.topLeft(), bar.bottomLeft())
    grad.setColorAt(0.0, c1)
    grad.setColorAt(1.0, c2)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(grad))
    painter.drawRoundedRect(bar, bar_width / 2.0, bar_width / 2.0)
    painter.restore()


def paint_bevel_frame(painter: QPainter, rect, radius: int,
                      light_alpha: float = 0.14, dark_alpha: float = 0.20):
    """Permanent glass-edge bevel — depth + a sub-pixel highlight in one
    pass. A single rounded-rect stroke whose pen is a diagonal gradient:
    a bright top-left highlight sweeping through to a soft bottom-right
    shadow. This is the alternative to per-side `border-top-color` /
    `border-bottom-color` QSS rules, which artifact at rounded corners in
    Qt's software rasterizer (see card_qss's comment on the same finding).
    One stroke, every repaint, costs microseconds — no offscreen buffer,
    unlike a real drop shadow.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # inset by half a device pixel so a 1px cosmetic pen lands crisply
    # instead of anti-aliasing across two rows
    inner = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    grad = QLinearGradient(inner.topLeft(), inner.bottomRight())
    grad.setColorAt(0.0, QColor(255, 255, 255, int(255 * light_alpha)))
    grad.setColorAt(1.0, QColor(0, 0, 0, int(255 * dark_alpha)))
    painter.setPen(QPen(QBrush(grad), 1.0))
    painter.drawRoundedRect(inner, radius, radius)
    painter.restore()


# ============================================================
#  RIPPLE — one-shot expanding click feedback, effect-free
# ============================================================
class RippleController(QObject):
    """Drives a click ripple WITHOUT QGraphicsEffect — the same pattern as
    GlowController: a widget owns one controller, reads `.progress` /
    `.origin` in its own paintEvent via paint_ripple_frame(), and calls
    `.trigger(pos)` on mouse press. One QVariantAnimation, no timers."""

    def __init__(self, widget: QWidget, duration_ms: int = 320):
        super().__init__(widget)
        self._widget = widget
        self._progress = 0.0
        self._origin = QPointF()

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(duration_ms)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(EASE_OUT)
        self._anim.valueChanged.connect(self._on_frame)

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def origin(self) -> QPointF:
        return self._origin

    def trigger(self, origin: QPointF):
        self._origin = QPointF(origin)
        self._anim.stop()
        self._anim.start()

    def _on_frame(self, value: float):
        self._progress = float(value)
        self._widget.update()


def paint_ripple_frame(painter: QPainter, rect, radius: int, color: QColor,
                       progress: float, origin: QPointF):
    """Paint an expanding, fading accent-tinted ripple from a click point.

    Clipped to the widget's own rounded rect so it never bleeds onto
    neighboring cards; one radial-gradient fill, no offscreen buffer.
    """
    if progress <= 0.0 or progress >= 1.0:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(rect), radius, radius)
    painter.setClipPath(path)

    max_r = float(rect.width() + rect.height())  # generous — always covers
    r = max(max_r * progress, 1.0)
    grad = QRadialGradient(origin, r)
    c0 = QColor(color)
    c0.setAlphaF(0.16 * (1.0 - progress))
    c1 = QColor(color)
    c1.setAlphaF(0.0)
    grad.setColorAt(0.0, c0)
    grad.setColorAt(1.0, c1)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(grad)
    painter.drawEllipse(origin, r, r)
    painter.restore()


# ============================================================
#  SHIMMER BAR — painted progress sweep (zero stylesheet churn)
# ============================================================
class ShimmerBar(QWidget):
    """Thin indeterminate progress bar: a cyan→purple band sweeping across
    a faint track. All painting, no QSS, driven by one looping
    QVariantAnimation on Qt's 60 fps animation driver."""

    def __init__(self, parent: QWidget | None = None, height: int = 6):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._phase = 0.0
        self._c1 = QColor("#4cc2ff")
        self._c2 = QColor("#8a7dff")
        self._track = QColor(255, 255, 255, 14)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(SHIMMER_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_frame)
        self.hide()

    # -- theme ------------------------------------------------
    def set_theme(self, t: dict):
        self._c1 = QColor(t["accent"])
        self._c2 = QColor(t["accent2"])
        self._track = QColor(*t["shimmer_track"])
        self.update()

    # -- control ----------------------------------------------
    def start(self):
        self.show()
        self._anim.start()

    def stop(self):
        self._anim.stop()
        self.hide()

    # -- internals --------------------------------------------
    def _on_frame(self, value: float):
        self._phase = float(value)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        rad = r.height() / 2.0

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._track)
        p.drawRoundedRect(r, rad, rad)

        # band sweeps from fully off-left to fully off-right
        w = r.width()
        band_w = w * 0.45
        cx = -band_w + self._phase * (w + 2 * band_w)
        grad = QLinearGradient(cx, 0, cx + band_w, 0)
        t0 = QColor(self._c1)
        t0.setAlpha(0)
        grad.setColorAt(0.0, t0)
        grad.setColorAt(0.35, self._c1)
        grad.setColorAt(0.75, self._c2)
        t1 = QColor(self._c2)
        t1.setAlpha(0)
        grad.setColorAt(1.0, t1)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(r, rad, rad)


# ============================================================
#  CASCADE — staggered slide-up + fade-in card entrance
# ============================================================
class CascadeAnimator(QObject):
    """Cinematic entrance for a grid of cards.

    Each widget gets pause(i·GAP) → parallel(fade 0→1, rise +26px→0),
    all inside ONE QParallelAnimationGroup so Qt schedules every frame
    together. Opacity effects exist only for the duration of the run and
    are removed in _cleanup — steady-state rendering stays effect-free.
    """

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._group: QParallelAnimationGroup | None = None
        self._staged: list[tuple[QWidget, QGraphicsOpacityEffect, QPoint]] = []

    def play(self, widgets: list[QWidget],
             stagger_ms: int = CASCADE_GAP,
             duration_ms: int = CASCADE_MS,
             rise_px: int = CASCADE_RISE):
        self.stop()  # settle any previous run instantly

        group = QParallelAnimationGroup(self)
        for i, w in enumerate(widgets):
            target = w.pos()  # layout has already placed it
            effect = QGraphicsOpacityEffect(w)
            effect.setOpacity(0.0)
            w.setGraphicsEffect(effect)
            w.move(target + QPoint(0, rise_px))
            self._staged.append((w, effect, target))

            fade = QPropertyAnimation(effect, b"opacity")
            fade.setDuration(duration_ms)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(EASE_OUT)

            rise = QPropertyAnimation(w, b"pos")
            rise.setDuration(duration_ms)
            rise.setStartValue(target + QPoint(0, rise_px))
            rise.setEndValue(target)
            rise.setEasingCurve(EASE_OUT)

            both = QParallelAnimationGroup()
            both.addAnimation(fade)
            both.addAnimation(rise)

            seq = QSequentialAnimationGroup()
            seq.addPause(i * stagger_ms)
            seq.addAnimation(both)
            group.addAnimation(seq)

        group.finished.connect(self._cleanup)
        self._group = group
        group.start()

    def stop(self):
        """Cancel a running cascade and snap widgets to their final state."""
        if self._group is not None:
            self._group.stop()
        self._cleanup()

    def _cleanup(self):
        for w, _effect, target in self._staged:
            try:
                w.setGraphicsEffect(None)   # deletes the effect
                w.move(target)
            except RuntimeError:
                pass  # widget was destroyed mid-flight (page closed)
        self._staged.clear()
        if self._group is not None:
            self._group.deleteLater()
            self._group = None


# ============================================================
#  PAGE FADE — transient cross-fade for QStackedWidget pages
# ============================================================
class PageFader(QObject):
    """Fade-in (with an optional subtle rise) for the page a QStackedWidget
    just switched to. The opacity effect lives only for PAGE_FADE_MS, then
    is removed; a transient position offset is always restored."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._anim: QParallelAnimationGroup | None = None
        self._page: QWidget | None = None
        self._target: QPoint | None = None

    def fade_in(self, page: QWidget, duration_ms: int = PAGE_FADE_MS,
                rise_px: int = 0):
        self._finish()  # settle any in-flight fade first

        effect = QGraphicsOpacityEffect(page)
        effect.setOpacity(0.0)
        page.setGraphicsEffect(effect)

        group = QParallelAnimationGroup(self)

        fade = QPropertyAnimation(effect, b"opacity")
        fade.setDuration(duration_ms)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(EASE_OUT)
        group.addAnimation(fade)

        self._target = None
        if rise_px:
            # weighted entrance: the page settles upward into place
            self._target = QPoint(page.pos())
            page.move(self._target + QPoint(0, rise_px))
            rise = QPropertyAnimation(page, b"pos")
            rise.setDuration(duration_ms)
            rise.setStartValue(self._target + QPoint(0, rise_px))
            rise.setEndValue(self._target)
            rise.setEasingCurve(EASE_OUT)
            group.addAnimation(rise)

        group.finished.connect(self._finish)
        self._page = page
        self._anim = group
        group.start()

    def _finish(self):
        if self._anim is not None:
            self._anim.stop()
            self._anim.deleteLater()
            self._anim = None
        if self._page is not None:
            try:
                self._page.setGraphicsEffect(None)
                if self._target is not None:
                    self._page.move(self._target)
            except RuntimeError:
                pass
            self._page = None
        self._target = None
