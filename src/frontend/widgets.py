"""
src/frontend/widgets.py

COMPONENT LIBRARY — isolated, theme-aware, effect-free custom widgets.

Every widget here:
    - takes its QSS from theme.py factories (never inline color literals),
    - exposes apply_theme(t) for live re-skinning (ThemeManager.changed),
    - paints its hover glow itself via animations.GlowController +
      paint_glow_frame — zero QGraphicsEffect in steady state.

Import graph: theme.py <- animations.py <- widgets.py <- main.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF,
    Qt, QThread, QTimer, QUrl, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QPainter, QPainterPath, QRadialGradient,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
    QWidget,
)

from frontend.animations import (
    GlowController, RippleController, ShimmerBar, paint_bevel_frame,
    paint_glow_frame, paint_nav_indicator, paint_ripple_frame,
)
from frontend import theme as TH
# Update Center / Startup Manager (v6.3) run their own background scans and
# per-item actions independently of main.py's single-task console pipeline
# (both are modal dialogs that fully cover it anyway) - the one deliberate
# exception to this file's "pure component library" rule, since the alter-
# native (threading process ownership through main.py) would either block
# the dialog's own loading UI or duplicate PowerShellTask's cancellation-
# safe process/thread bookkeeping here.
from utils.helpers import PowerShellTask, TaskResult  # noqa: E402


class PulseDialog(QDialog):
    """Base for every frameless Pulse modal.

    Unlike a plain QDialog sized to fit its content, THIS window covers
    the app's full body (everything below the title bar) and paints the
    dense scrim backdrop itself, with the frosted content `panel`
    centered (or top-anchored) inside it. Because the backdrop is part of
    the same top-level window as the panel — not a separate widget
    sitting underneath — it keeps receiving mouse events while the dialog
    is modal: clicking anywhere outside `panel` dismisses the dialog
    exactly like pressing Escape or Cancel, the way a native Fluent/macOS
    sheet behaves. Nested wizards (a PulseDialog opened from another
    PulseDialog) get this for free — each paints its own full-body scrim
    on top of whatever is behind it, so stacked modals just work."""

    def __init__(self, parent: QWidget | None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.panel: "DepthCard | None" = None
        self._scrim_color = QColor(5, 7, 10, 195)
        self._scrim_radius = 22

    def _set_scrim(self, t: dict, radius: int):
        self._scrim_color = QColor(*t["scrim"])
        self._scrim_radius = radius
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        color = self._scrim_color
        if self._scrim_radius:
            # extend the path above the top edge so only the BOTTOM
            # corners round — the top meets the title bar in a flat line
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(0, -self._scrim_radius, 0, 0),
                                self._scrim_radius, self._scrim_radius)
            p.setClipRect(rect)
            p.fillPath(path, color)
        else:
            p.fillRect(rect, color)
        p.end()

    def mousePressEvent(self, e):
        # A click that lands on the scrim itself (outside the panel) is
        # the backdrop-dismiss gesture — everything inside the panel is
        # ordinary child-widget input and reaches its own handlers first,
        # so this only ever fires for genuine outside clicks.
        if self.panel is not None and not self.panel.geometry().contains(e.position().toPoint()):
            self.reject()
            return
        super().mousePressEvent(e)


def _dialog_chrome(dialog: PulseDialog, t: dict, accent: str,
                   width: int, radius: int = 18, anchor: str = "center") -> "DepthCard":
    """One shared construction path for every Pulse dialog: the frosted
    DepthCard panel at exactly `width`, laid out centered (or top-anchored
    for the command palette) inside the dialog's full-body scrim, plus a
    soft elevation shadow. A drop-shadow QGraphicsEffect is allowed here
    as the deliberate exception to the animations.py doctrine: dialogs
    are small, transient surfaces that repaint a handful of times — not
    steady-state 60fps chrome.

    Returns the panel; the caller builds its content layout inside it."""
    panel = DepthCard(radius=radius, parent=dialog)
    panel.setFixedWidth(width)
    panel.setStyleSheet(TH.dialog_panel_qss(t, accent))
    dialog.panel = panel

    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    if anchor == "top":
        outer.addSpacing(34)
    else:
        outer.addStretch(1)
    row = QHBoxLayout()
    row.addStretch(1)
    row.addWidget(panel)
    row.addStretch(1)
    outer.addLayout(row)
    outer.addStretch(1)

    shadow = QGraphicsDropShadowEffect(panel)
    shadow.setBlurRadius(42)
    shadow.setOffset(0, 12)
    shadow.setColor(QColor(0, 0, 0, 150))
    panel.setGraphicsEffect(shadow)
    return panel


def refit_dialog(dialog: PulseDialog):
    """Resize `dialog` to exactly cover its host window's BODY — always
    fully below the title bar, so minimize/maximize/close stay visible
    and reachable no matter what is open — and match its scrim radius to
    the host's maximized state (square when flush, rounded otherwise,
    matching the shell). Called from showEvent and again whenever the
    host resizes while a dialog is open."""
    host = dialog.parentWidget()
    if host is not None:
        host = host.window()
        # nested wizards are parented to another dialog — climb to the app
        while isinstance(host, QDialog) and host.parentWidget() is not None:
            host = host.parentWidget().window()
    if host is not None:
        titlebar_h = getattr(getattr(host, "titlebar", None), "height", lambda: 0)()
        body = QRect(0, titlebar_h, host.width(), host.height() - titlebar_h)
        dialog.setGeometry(QRect(host.mapToGlobal(body.topLeft()), body.size()))
        theme_mgr = getattr(host, "theme", None)
        if theme_mgr is not None:
            dialog._set_scrim(theme_mgr.t, 0 if host.isMaximized() else 22)


def _present_dialog(dialog: PulseDialog, duration_ms: int = 130):
    """Fit + entrance for every dialog, called from showEvent. Entrance is
    a quick compositor-side windowOpacity fade — no QGraphicsEffect
    involved in the animation."""
    refit_dialog(dialog)
    dialog.setWindowOpacity(0.0)
    anim = QPropertyAnimation(dialog, b"windowOpacity", dialog)
    anim.setDuration(duration_ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start()
    dialog._entrance_anim = anim  # keep alive for the run


# ============================================================
#  TITLE BAR — drag, double-click max, Fluent caption buttons
# ============================================================
def _caption_icon_font() -> QFont | None:
    """Native Windows caption glyphs: Segoe Fluent Icons (Win11), falling
    back to Segoe MDL2 Assets (Win10). None on other platforms / missing
    fonts — the title bar then uses plain text glyphs."""
    if sys.platform != "win32":
        return None
    from PySide6.QtGui import QFontDatabase
    for family in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if family in QFontDatabase.families():
            font = QFont(family)
            font.setPixelSize(13)
            return font
    return None


class TitleBar(QWidget):
    """Frameless-window chrome. Left: brand block (glyph · name · version
    · release-channel pill). Right: theme toggle + native-styled caption
    buttons using the OS's own Segoe Fluent icon glyphs.

    Drag guard: dragging while maximized restores the window first and
    re-anchors it under the cursor proportionally — native Windows feel.

    Snap Layouts contract (Windows 11): main.nativeEvent answers
    WM_NCHITTEST with HTMAXBUTTON over `btn_max`, which makes Windows
    show its Snap Layouts flyout on hover — but also means Qt no longer
    receives mouse events for that button. `set_nc_hover()` mirrors the
    hover visual and the click is re-injected from WM_NCLBUTTONUP.
    """

    theme_toggle_requested = Signal()

    # (caption-font glyph, text fallback)
    _ICONS = {
        "min":     ("", "–"),
        "max":     ("", "□"),
        "restore": ("", "❐"),
        "close":   ("", "✕"),
        "sun":     ("", "☀"),
        "moon":    ("", "☾"),
    }

    def __init__(self, window: QMainWindow, t: dict,
                 app_name: str, version: str, channel: str = ""):
        super().__init__(window)
        self._window = window
        self._drag_offset: QPoint | None = None
        self._press_gp: QPoint | None = None
        self._icon_font = _caption_icon_font()
        self.setFixedHeight(50)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 8, 10, 6)
        lay.setSpacing(9)

        self._glyph = QLabel("✦")
        lay.addWidget(self._glyph)
        self._name = QLabel(app_name)
        lay.addWidget(self._name)
        self._version = QLabel(f"v{version}")
        lay.addWidget(self._version)
        self._channel: QLabel | None = None
        if channel:
            self._channel = QLabel(channel.upper())
            lay.addWidget(self._channel)
        lay.addStretch()

        btns = QHBoxLayout()
        btns.setSpacing(2)

        def _mk(icon_key: str, tip: str, slot) -> QPushButton:
            b = QPushButton(self._icon(icon_key))
            b.setFixedSize(40, 30)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if self._icon_font is not None:
                b.setFont(self._icon_font)
            b.clicked.connect(slot)
            btns.addWidget(b)
            return b

        self._btn_theme = _mk("sun", "Switch theme", self.theme_toggle_requested.emit)
        self._btn_min = _mk("min", "Minimize", window.showMinimized)
        self.btn_max = _mk("max", "Maximize", self._toggle_max)
        self._btn_close = _mk("close", "Close", window.close)
        lay.addLayout(btns)

        # keep the max/restore glyph honest however the state changes
        window.installEventFilter(self)
        self.apply_theme(t)

    def _icon(self, key: str) -> str:
        fluent, fallback = self._ICONS[key]
        return fluent if self._icon_font is not None else fallback

    # -- theming ----------------------------------------------
    def apply_theme(self, t: dict):
        self._t = t
        self._glyph.setStyleSheet(
            f"color: {t['accent']}; font-size: 17px; background: transparent; border: none;")
        self._name.setStyleSheet(TH.label_qss(t, "brand"))
        self._version.setStyleSheet(TH.label_qss(t, "version"))
        if self._channel is not None:
            self._channel.setStyleSheet(TH.beta_badge_qss(t))
        for btn in (self._btn_theme, self._btn_min, self.btn_max):
            btn.setStyleSheet(TH.titlebar_button_qss(t, t["titlebar_hover"]))
        self._btn_close.setStyleSheet(TH.titlebar_close_qss(t))
        self._btn_theme.setText(self._icon("sun" if t["name"] == "dark" else "moon"))
        self._btn_theme.setToolTip(
            "Switch to light theme" if t["name"] == "dark" else "Switch to dark theme")

    # -- non-client caption support (driven by main.nativeEvent) --
    # Windows owns the mouse events for all three caption buttons while
    # WM_NCHITTEST maps their (generously expanded) zones to HTMINBUTTON /
    # HTMAXBUTTON / HTCLOSEBUTTON — that's what makes the top-right corner
    # region clickable like a native app instead of demanding a
    # pixel-perfect hit on the 40×30 glyph. Qt therefore never sees
    # Enter/Leave there; hover visuals are mirrored via property flips.
    def caption_buttons(self) -> dict[str, QPushButton]:
        """The NC-hit-tested caption buttons, keyed by role."""
        return {"min": self._btn_min, "max": self.btn_max,
                "close": self._btn_close}

    def theme_button(self) -> QPushButton:
        """The theme toggle — the one title-bar button that stays a plain
        Qt button (HTCLIENT), so the HTCAPTION strip must carve it out."""
        return self._btn_theme

    def set_nc_hover(self, key: str | None):
        """Highlight exactly the caption button under the non-client
        cursor (`None` clears all). Cheap no-op unless a state flips."""
        for name, btn in self.caption_buttons().items():
            on = (name == key)
            if bool(btn.property("nchover")) != on:
                btn.setProperty("nchover", on)
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    def set_max_hover(self, on: bool):
        """Back-compat shim over set_nc_hover."""
        self.set_nc_hover("max" if on else None)

    # -- maximize / restore -----------------------------------
    def _toggle_max(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def _sync_max_glyph(self):
        maxed = self._window.isMaximized()
        self.btn_max.setText(self._icon("restore" if maxed else "max"))
        self.btn_max.setToolTip("Restore" if maxed else "Maximize")

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() == QEvent.Type.WindowStateChange:
            self._sync_max_glyph()
        return False

    # -- drag to move: NATIVE system move first ----------------
    # startSystemMove() hands the drag to Windows itself, which is what
    # makes Aero Snap zones, drag-to-top maximize, shake-to-minimize and
    # restore-from-maximized behave exactly like a native Win11 app.
    # The move starts on the first real drag (4px threshold), never on
    # press, so double-click-to-maximize still gets its events. The old
    # manual path remains as the fallback for platforms without support.
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_gp = e.globalPosition().toPoint()
            self._drag_offset = (e.globalPosition().toPoint()
                                 - self._window.frameGeometry().topLeft())

    def mouseMoveEvent(self, e):
        if self._drag_offset is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        gp = e.globalPosition().toPoint()
        if self._press_gp is not None:
            if (gp - self._press_gp).manhattanLength() < 4:
                return
            self._press_gp = None
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                self._drag_offset = None
                return
        # manual fallback
        if self._window.isMaximized():
            # restore, then re-anchor the (now smaller) window under the
            # cursor at the same horizontal ratio — no visual jump
            ratio = e.position().x() / max(1.0, float(self.width()))
            self._window.showNormal()
            self._drag_offset = QPoint(
                int(self._window.width() * ratio), int(e.position().y()))
        self._window.move(gp - self._drag_offset)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None
        self._press_gp = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()


# ============================================================
#  NAV BUTTON — sidebar category entry with painted glow
# ============================================================
class NavButton(QPushButton):
    def __init__(self, icon: str, title: str, accent: str, t: dict):
        # QPushButton treats a lone "&" as a mnemonic marker (it vanishes
        # and the following character gets an accelerator underline) —
        # category titles like "Maintenance & Repair" need it escaped to
        # "&&" or the button renders "Maintenance _Repair".
        super().__init__(f"{icon}  {title}".replace("&", "&&"))
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", False)
        self._glow = GlowController(self, accent)
        self._ripple = RippleController(self)
        self._accent2 = QColor(t["accent2"])
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.nav_button_qss(t))
        self._accent2 = QColor(t["accent2"])

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._ripple.trigger(e.position())
        super().mousePressEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)  # QSS background/text first
        p = QPainter(self)
        paint_bevel_frame(p, self.rect(), 13)
        paint_ripple_frame(p, self.rect(), 13, self._glow.color,
                           self._ripple.progress, self._ripple.origin)
        paint_glow_frame(p, self.rect(), 13, self._glow.color,
                         self._glow.intensity, self._glow.cursor)
        if self.property("selected"):
            paint_nav_indicator(p, self.rect(), self._glow.color, self._accent2)
        p.end()


# ============================================================
#  GLASS CARD — one operation, painted glow, live re-skin
# ============================================================
class GlassCard(QFrame):
    clicked = Signal()

    _ICON_BASE_PX = 28
    _ICON_GROW_PX = 3  # subtle hover "pop" — see _sync_icon_scale()

    def __init__(self, item: dict, accent: str, t: dict):
        super().__init__()
        self.item = item
        self._accent = accent
        self._danger = bool(item.get("danger"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(132)
        self.setProperty("running", False)

        glow_color = t["err"] if self._danger else accent
        self._glow = GlowController(self, glow_color)
        self._ripple = RippleController(self)

        # "Weighted" press feedback: a painted dark tint that ramps in fast
        # and releases softly. Painted in paintEvent — zero QSS churn, zero
        # QGraphicsEffect, per the animations.py doctrine.
        self._press_tint = 0.0
        self._press_anim = QVariantAnimation(self)
        self._press_anim.setDuration(90)
        self._press_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._press_anim.valueChanged.connect(self._on_press_frame)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        self._icon = QLabel(item["icon"])
        self._icon.setFixedWidth(40)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        # Font is managed as a QFont object, not inline QSS: hover "pop" is
        # a handful of setFont() calls per hover-in (one per distinct
        # integer pixel size), never a per-frame setStyleSheet() rebuild —
        # the exact anti-pattern the animations.py doctrine forbids.
        self._icon_font = QFont()
        self._icon_font.setPixelSize(self._ICON_BASE_PX)
        self._icon.setFont(self._icon_font)
        self._icon_px = self._ICON_BASE_PX
        lay.addWidget(self._icon)

        col = QVBoxLayout()
        col.setSpacing(7)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._title = QLabel(item["title"])
        # Long titles wrap instead of clipping at narrow card widths.
        self._title.setWordWrap(True)
        head.addWidget(self._title, 1)
        self._badge: QLabel | None = None
        if item.get("note"):
            self._badge = QLabel(item["note"])
            head.addWidget(self._badge)
        head.addStretch()
        col.addLayout(head)

        self._desc = QLabel(item["desc"])
        self._desc.setWordWrap(True)
        col.addWidget(self._desc)
        col.addStretch()
        lay.addLayout(col, 1)

        self.apply_theme(t)

    # -- theming ----------------------------------------------
    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.card_qss(t, self._accent, self._danger))
        # No font-size here: the icon's size is a managed QFont (see
        # _sync_icon_scale) so the hover "pop" never needs a QSS rebuild.
        self._icon.setStyleSheet("background: transparent; border: none;")
        self._title.setStyleSheet(TH.label_qss(t, "card"))
        self._desc.setStyleSheet(TH.label_qss(t, "desc"))
        if self._badge is not None:
            self._badge.setStyleSheet(TH.badge_qss(t))
        self._glow.set_accent(t["err"] if self._danger else self._accent)

    # -- state ------------------------------------------------
    def set_running(self, on: bool):
        self.setProperty("running", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def flash(self, kind: str, duration_ms: int = 1400):
        """Transient 'ok' / 'err' verdict tint after a task ends. Same
        dynamic-property mechanic as the running state; the clearing
        timer is bound to this widget as receiver, so a card destroyed
        mid-flash is never touched."""
        self.setProperty("flash", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        QTimer.singleShot(duration_ms, self, self._clear_flash)

    def _clear_flash(self):
        self.setProperty("flash", "")
        self.style().unpolish(self)
        self.style().polish(self)

    # -- interaction / painting --------------------------------
    def _on_press_frame(self, value: float):
        self._press_tint = float(value)
        self.update()

    def _ramp_press(self, target: float):
        self._press_anim.stop()
        self._press_anim.setStartValue(self._press_tint)
        self._press_anim.setEndValue(target)
        self._press_anim.start()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._ramp_press(1.0)
            self._ripple.trigger(e.position())
        super().mousePressEvent(e)

    def leaveEvent(self, e):
        self._ramp_press(0.0)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        self._ramp_press(0.0)
        if (e.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(e.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def _sync_icon_scale(self):
        """Subtle icon 'pop' tied to the existing hover glow intensity —
        no new animation, just reads GlowController's already-running one.
        Guarded so setFont() only fires when the rounded size changes
        (a handful of times per hover ramp, not every frame)."""
        grown = round(self._ICON_BASE_PX + self._ICON_GROW_PX * self._glow.intensity)
        if grown != self._icon_px:
            self._icon_px = grown
            self._icon_font.setPixelSize(grown)
            self._icon.setFont(self._icon_font)

    def paintEvent(self, e):
        super().paintEvent(e)  # QSS glass background/border first
        self._sync_icon_scale()
        p = QPainter(self)
        paint_bevel_frame(p, self.rect(), 16)
        if self._press_tint > 0.01:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(40 * self._press_tint)))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 15, 15)
        paint_ripple_frame(p, self.rect(), 16, self._glow.color,
                           self._ripple.progress, self._ripple.origin)
        paint_glow_frame(p, self.rect(), 16, self._glow.color,
                         self._glow.intensity, self._glow.cursor)
        p.end()


# ============================================================
#  AMBIENT GLOW — static brand-pair light wash behind the shell
# ============================================================
class AmbientGlow(QWidget):
    """Two large, very soft radial-gradient blobs of the brand accent
    pair, painted once behind the sidebar/content frames (lowest widget
    in the shell's z-order, transparent to mouse events). Pure static
    paintEvent — repainted only on resize or theme change, never on a
    timer — this is the 'rich luminescence' cue an otherwise flat
    charcoal/porcelain canvas is missing at wide or maximized window
    sizes. Opacity stays deliberately low (0.06–0.10): theme.py already
    documents why the brand pair reads as neon past that on long
    sessions, so this reuses the same restraint, just spread wide instead
    of concentrated."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._c1 = QColor("#58a6ff")
        self._c2 = QColor("#a78bfa")
        self._radius = 24   # must track shell_qss's floating corner radius

    def apply_theme(self, t: dict):
        self._c1 = QColor(t["accent"])
        self._c2 = QColor(t["accent2"])
        self.update()

    def set_radius(self, radius: int):
        """Match the shell's current corner radius (24px floating, 0
        maximized/flush) — the window behind this widget is translucent,
        so an unclipped rectangle would paint square corners bleeding
        past the shell's rounded edge."""
        if radius != self._radius:
            self._radius = radius
            self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._radius:
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
            p.setClipPath(path)
        w, h = self.width(), self.height()
        span = max(w, h)

        top = QRadialGradient(w * 0.20, h * -0.08, span * 0.55)
        c1 = QColor(self._c1)
        c1.setAlphaF(0.10)
        top.setColorAt(0.0, c1)
        c1_out = QColor(self._c1)
        c1_out.setAlphaF(0.0)
        top.setColorAt(1.0, c1_out)
        p.fillRect(self.rect(), top)

        bottom = QRadialGradient(w * 0.92, h * 0.88, span * 0.50)
        c2 = QColor(self._c2)
        c2.setAlphaF(0.08)
        bottom.setColorAt(0.0, c2)
        c2_out = QColor(self._c2)
        c2_out.setAlphaF(0.0)
        bottom.setColorAt(1.0, c2_out)
        p.fillRect(self.rect(), bottom)
        p.end()


# ============================================================
#  BREATHING ICON — pure-paint pulsing brand glyph (no effects)
# ============================================================
class BreathingIcon(QWidget):
    """The '✦' brand mark with a slow breathing pulse.

    Doctrine-compliant: NO QGraphicsOpacityEffect. One looping
    QVariantAnimation (0→1→0, InOutSine, ~2.6 s) drives painter opacity
    plus a soft radial halo, all inside paintEvent — a repaint costs
    microseconds. The loop suspends automatically while the widget is
    hidden (category pages open), so idle cost off-screen is zero.
    """

    MIN_OPACITY = 0.45   # breath floor — glyph never fully fades
    HALO_ALPHA = 0.20    # halo strength at full breath

    def __init__(self, glyph: str = "✦", size: int = 110,
                 accent: str = "#00d4ff", parent: QWidget | None = None):
        super().__init__(parent)
        self._glyph = glyph
        self._accent = QColor(accent)
        self._breath = 1.0
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._font = QFont("Segoe UI")
        self._font.setPixelSize(int(size * 0.58))
        self._font.setWeight(QFont.Weight.Light)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(2600)
        self._anim.setStartValue(1.0)
        self._anim.setKeyValueAt(0.5, 0.0)   # exhale mid-loop
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_frame)

    # -- theming ----------------------------------------------
    def apply_theme(self, t: dict):
        self._accent = QColor(t["accent"])
        self.update()

    # -- lifecycle: animate only while visible ------------------
    def showEvent(self, e):
        super().showEvent(e)
        self._anim.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._anim.stop()

    # -- painting ----------------------------------------------
    def _on_frame(self, value: float):
        self._breath = float(value)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        level = self.MIN_OPACITY + (1.0 - self.MIN_OPACITY) * self._breath
        center = QPointF(self.width() / 2.0, self.height() / 2.0)

        # soft halo swelling with the breath
        halo = QRadialGradient(center, self.width() / 2.0)
        h0 = QColor(self._accent)
        h0.setAlphaF(self.HALO_ALPHA * level)
        h1 = QColor(self._accent)
        h1.setAlphaF(0.0)
        halo.setColorAt(0.0, h0)
        halo.setColorAt(1.0, h1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(self.rect())

        # the glyph itself
        p.setOpacity(level)
        p.setPen(self._accent)
        p.setFont(self._font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._glyph)
        p.end()


# ============================================================
#  NAV PILL — Back / Home header buttons
# ============================================================
class NavPill(QPushButton):
    def __init__(self, text: str, t: dict, width: int = 92):
        super().__init__(text)
        self.setFixedSize(width, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.nav_pill_qss(t))


# ============================================================
#  DEPTH CARD — non-interactive QFrame with the permanent glass bevel
# ============================================================
class DepthCard(QFrame):
    """A plain QFrame plus the painted glass bevel (see
    animations.paint_bevel_frame) — for surfaces that want the depth cue
    but aren't clickable, so no glow/press/ripple state is needed. Used by
    the Welcome page's system-insight tiles and status dock; QSS selectors
    like `QFrame#insight` still match (Qt resolves by base class + object
    name, and DepthCard IS a QFrame)."""

    def __init__(self, radius: int = 14, parent: QWidget | None = None):
        super().__init__(parent)
        self._radius = radius

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        paint_bevel_frame(p, self.rect(), self._radius)
        p.end()


# ============================================================
#  CONFIRM DIALOG — frameless glass confirmation
# ============================================================
class ConfirmDialog(PulseDialog):
    def __init__(self, parent: QWidget, item: dict, t: dict):
        super().__init__(parent)
        danger = bool(item.get("danger"))
        accent = t["err"] if danger else t["accent"]
        panel = _dialog_chrome(self, t, accent, width=440)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(10)

        head = QLabel(f"{item['icon']}  {item['title']}")
        head.setStyleSheet(TH.label_qss(t, "card").replace("14px", "16px"))
        lay.addWidget(head)

        body = QLabel(item["desc"])
        body.setWordWrap(True)
        body.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(body)

        if danger:
            warn = QLabel("⚠️  This action changes your system and may be hard to undo.")
            warn.setWordWrap(True)
            warn.setStyleSheet(
                f"color: {t['err']}; font-size: 11px; font-weight: 500;"
                "background: transparent; border: none;")
            lay.addWidget(warn)

        lay.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        go = QPushButton("Proceed")
        go.setFixedSize(96, 36)
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.setStyleSheet(TH.dialog_go_qss(t, accent))
        go.clicked.connect(self.accept)
        row.addWidget(go)
        lay.addLayout(row)

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


# ============================================================
#  LIVE CONSOLE — streams raw PowerShell stdout in real time
# ============================================================
class LiveConsole(QPlainTextEdit):
    """Read-only micro-terminal. `put_line()` is the slot for
    PowerShellTask.output: it appends a line, or — when the backend used a
    bare carriage return — rewrites the newest line in place, so winget
    percentages / SFC progress read exactly like a real console."""

    MAX_LINES = 2000  # bound memory on very long-running tasks (SFC/DISM)
    _EMPTY_MESSAGE = "Idle — output streams here in real time while a task runs."

    def __init__(self, t: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFont("Cascadia Mono", 9))
        # No native placeholder text: the empty state is a custom-painted
        # "pulse" waveform motif + message (see paintEvent), not plain text.
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.console_qss(t))
        self._empty_accent = QColor(t["accent"])
        self._empty_text = QColor(t["text_faint"])

    def put_line(self, text: str, replace_last: bool = False):
        """Slot for PowerShellTask.output(text, replace_last)."""
        if replace_last and not self.document().isEmpty():
            self._replace_last_line(text)
        else:
            self.append_line(text)

    def append_line(self, text: str):
        self.appendPlainText(text)
        if self.blockCount() > self.MAX_LINES:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                self.blockCount() - self.MAX_LINES,
            )
            cursor.removeSelectedText()
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _replace_last_line(self, text: str):
        """In-place rewrite of the newest block — carriage-return progress.
        Never grows blockCount(), so the MAX_LINES trim in append_line()
        is unaffected."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock,
                            QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text)
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_console(self):
        self.clear()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.toPlainText():
            return
        # Custom empty state — a small on-brand "pulse" waveform motif in
        # place of the generic gray placeholder text QPlainTextEdit would
        # otherwise render natively.
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.viewport().rect()
        cx, cy = r.center().x(), r.center().y() - 12

        bar_w, gap = 4, 7
        heights = (8, 16, 26, 16, 8)
        total_w = len(heights) * bar_w + (len(heights) - 1) * gap
        x = cx - total_w / 2.0
        accent = QColor(self._empty_accent)
        accent.setAlphaF(0.30)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        for h in heights:
            p.drawRoundedRect(QRectF(x, cy - h / 2.0, bar_w, h), 2, 2)
            x += bar_w + gap

        p.setPen(self._empty_text)
        msg_font = QFont(self.font().family(), 9)
        p.setFont(msg_font)
        msg_rect = r.adjusted(24, int(cy - r.top()) + 22, -24, 0)
        p.drawText(msg_rect,
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                   | Qt.TextFlag.TextWordWrap,
                   self._EMPTY_MESSAGE)
        p.end()


# ============================================================
#  STATE PILL — compact execution-state chip (console header)
# ============================================================
class StatePill(QLabel):
    """IDLE / RUNNING / SUCCESS / ERROR / STOPPED indicator.

    Styled entirely by theme.state_pill_qss through the dynamic `state`
    property — the same repolish mechanic NavButton uses for `selected`,
    so state flips never rebuild QSS."""

    TEXTS = {
        "idle": "IDLE",
        "running": "RUNNING",
        "ok": "SUCCESS",
        "err": "ERROR",
        "stopped": "STOPPED",
    }

    def __init__(self, t: dict, parent: QWidget | None = None):
        super().__init__(self.TEXTS["idle"], parent)
        self.setObjectName("statePill")
        self.setProperty("state", "idle")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(22)
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.state_pill_qss(t))

    def set_state(self, state: str):
        self.setText(self.TEXTS.get(state, state.upper()))
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)


# ============================================================
#  STATUS DOT — the bottom-bar '●', breathes while busy
# ============================================================
class StatusDot(QLabel):
    """The bottom status-bar glyph. Static color swap for ready/ok/err
    (cheap — see set_color); a soft breathing pulse ONLY while busy, using
    BreathingIcon's proven pure-paint technique (no QGraphicsEffect). A
    literal brand moment: Pulse pulses while it's actually working, and
    goes still the instant it's done — a custom 'loading state' graphic
    cue in place of a flat static dot."""

    def __init__(self, glyph: str = "●", parent: QWidget | None = None):
        super().__init__(glyph, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._color = QColor("#3fb950")
        self._breath = 1.0
        self._pulsing = False
        self._font = QFont(self.font())
        self._font.setPixelSize(12)

        # Faster cadence than BreathingIcon's slow 2.6s ambient brand
        # breath — this one signals active work, not idle presence.
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(1000)
        self._anim.setStartValue(1.0)
        self._anim.setKeyValueAt(0.5, 0.35)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_frame)

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def start_pulse(self):
        if not self._pulsing:
            self._pulsing = True
            self._anim.start()

    def stop_pulse(self):
        if self._pulsing:
            self._pulsing = False
            self._anim.stop()
            self._breath = 1.0
            self.update()

    def _on_frame(self, value: float):
        self._breath = float(value)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setOpacity(self._breath if self._pulsing else 1.0)
        p.setPen(self._color)
        p.setFont(self._font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


# ============================================================
#  TOGGLE SWITCH — native-feeling animated on/off control
# ============================================================
class ToggleSwitch(QWidget):
    """A macOS/iOS-style pill switch, pure-paint per the animations.py
    doctrine (no QGraphicsEffect, no per-frame QSS rebuild — one looping
    QVariantAnimation drives the thumb slide + track color cross-fade,
    another drives the busy pulse). Used by the Startup Manager for
    instant enable/disable: clicking flips the thumb immediately and
    emits `toggled`; the caller drives `set_busy(True)` while the backend
    call is in flight and `set_checked_silent()` afterwards to reconcile
    the visual state with the real outcome without re-emitting `toggled`."""

    toggled = Signal(bool)

    WIDTH, HEIGHT, PAD = 42, 24, 3

    def __init__(self, t: dict, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._busy = False
        self._pos = 1.0 if checked else 0.0
        self._on_color = QColor(t["ok"])
        self._off_color = QColor(t["panel_line"])
        self._thumb_color = QColor("#ffffff")

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_frame)

        self._busy_anim = QVariantAnimation(self)
        self._busy_anim.setDuration(900)
        self._busy_anim.setStartValue(0.35)
        self._busy_anim.setKeyValueAt(0.5, 1.0)
        self._busy_anim.setEndValue(0.35)
        self._busy_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._busy_anim.setLoopCount(-1)
        self._busy_anim.valueChanged.connect(lambda _v: self.update())

    # -- theming ------------------------------------------------
    def apply_theme(self, t: dict):
        self._on_color = QColor(t["ok"])
        self._off_color = QColor(t["panel_line"])
        self.update()

    # -- state ----------------------------------------------------
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        self._set_checked(checked, emit=False)

    def set_checked_silent(self, checked: bool):
        """Reconcile the visual state with a backend result without
        re-triggering `toggled` (avoids feedback loops)."""
        self._set_checked(checked, emit=False)

    def set_busy(self, busy: bool):
        if busy == self._busy:
            return
        self._busy = busy
        self.setDisabled(busy)
        if busy:
            self._busy_anim.start()
        else:
            self._busy_anim.stop()
            self.update()

    def _set_checked(self, checked: bool, emit: bool):
        self._checked = checked
        target = 1.0 if checked else 0.0
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(target)
        self._anim.start()
        if emit:
            self.toggled.emit(checked)

    def _on_frame(self, value):
        self._pos = float(value)
        self.update()

    # -- interaction ----------------------------------------------
    def mouseReleaseEvent(self, e):
        if self._busy:
            return
        if (e.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(e.position().toPoint())):
            self._set_checked(not self._checked, emit=True)
        super().mouseReleaseEvent(e)

    # -- painting ---------------------------------------------------
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._busy:
            value = self._busy_anim.currentValue()
            p.setOpacity(float(value) if value is not None else 0.6)

        track, on = self._off_color, self._on_color
        mix = QColor(
            int(track.red()   + (on.red()   - track.red())   * self._pos),
            int(track.green() + (on.green() - track.green()) * self._pos),
            int(track.blue()  + (on.blue()  - track.blue())  * self._pos),
        )
        rect = QRectF(0, 0, self.WIDTH, self.HEIGHT)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix)
        p.drawRoundedRect(rect, self.HEIGHT / 2.0, self.HEIGHT / 2.0)

        d = self.HEIGHT - self.PAD * 2
        x = self.PAD + self._pos * (self.WIDTH - self.PAD * 2 - d)
        p.setBrush(self._thumb_color)
        p.drawEllipse(QRectF(x, self.PAD, d, d))
        p.end()


# ============================================================
#  APP SELECTOR DIALOG — unified with the Dev Hub pattern
# ============================================================
class AppSelectorDialog(PulseDialog):
    """The selector for every `apps` catalog card (Essential Apps, Gaming
    Launchers, Diagnostics, Runtimes, Teams & OneDrive…).

    v6.2: rebuilt on the exact same components and layout grammar as the
    Developer & University Hub — the same DevHubRow (checkbox + per-tool
    '⋯' install-options wizard), the same Select All / Deselect All
    toolbar with a live '<n> selected' counter, and the same
    'Deploy Selected (n)' primary action — so every section of Software
    Management reads as one product, not two generations of UI. Rows here
    arrive pre-checked (the card promised a curated pack); the Dev Hub
    stays manual-first.

    After Accepted, exactly one of these is populated:
      `selected_ids`     ticked AppIds for the bulk winget deploy
      `local_installer`  (app_name, file_path) from a row wizard's Path C,
                          for a single InstallLocalFile run
    """

    def __init__(self, parent: QWidget, item: dict, t: dict):
        super().__init__(parent)
        self._t = t
        self.selected_ids: list[str] = []
        self.local_installer: tuple[str, str] | None = None
        self._rows: dict[str, DevHubRow] = {}
        self._tool_meta: dict[str, tuple[str, str]] = {}  # id -> (name, url)
        accent = t["accent"]

        # Normalize catalog entries: (id, name[, desc[, url]]) → 4-tuple.
        apps: list[tuple[str, str, str, str]] = []
        for entry in item.get("apps", []):
            app_id, app_name = entry[0], entry[1]
            desc = entry[2] if len(entry) > 2 else ""
            url = entry[3] if len(entry) > 3 else ""
            apps.append((app_id, app_name, desc, url))

        panel = _dialog_chrome(self, t, accent, width=560)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QLabel(f"{item['icon']}  {item['title']}")
        head.setStyleSheet(TH.label_qss(t, "card").replace("14px", "16px"))
        lay.addWidget(head)

        sub = QLabel(f"All {len(apps)} apps are pre-selected — untick anything "
                     "you don't want, or use a row's ⋯ for more install options.")
        sub.setWordWrap(True)
        sub.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(sub)

        # -- select-all / select-none + live counter -------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(16)
        all_btn = QPushButton("Select All")
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.setStyleSheet(TH.link_button_qss(t, accent))
        all_btn.clicked.connect(lambda: self._set_all(True))
        toolbar.addWidget(all_btn)

        none_btn = QPushButton("Deselect All")
        none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        none_btn.setStyleSheet(TH.link_button_qss(t, accent))
        none_btn.clicked.connect(lambda: self._set_all(False))
        toolbar.addWidget(none_btn)
        toolbar.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(TH.label_qss(t, "caption"))
        toolbar.addWidget(self._count_label)
        lay.addLayout(toolbar)

        # -- scrollable row list ----------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(TH.scroll_area_qss(t))
        scroll.setMaximumHeight(360)

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 6, 0)
        host_lay.setSpacing(8)
        for app_id, app_name, desc, url in apps:
            row = DevHubRow(app_id, app_name, desc, None, None, t, checked=True)
            row.checkbox.toggled.connect(self._update_count)
            row.options_requested.connect(self._open_tool_wizard)
            self._rows[app_id] = row
            self._tool_meta[app_id] = (app_name, url)
            host_lay.addWidget(row)
        host_lay.addStretch()
        scroll.setWidget(host)
        lay.addWidget(scroll)

        lay.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        self._deploy_btn = QPushButton("Deploy Selected")
        self._deploy_btn.setFixedSize(160, 36)
        self._deploy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._deploy_btn.setStyleSheet(TH.dialog_go_qss(t, accent))
        self._deploy_btn.clicked.connect(self._accept_selection)
        row.addWidget(self._deploy_btn)
        lay.addLayout(row)

        self._update_count()

    # -- selection state ------------------------------------------
    def _set_all(self, checked: bool):
        for row in self._rows.values():
            row.checkbox.setChecked(checked)

    def _update_count(self, _checked: bool = False):
        count = sum(1 for r in self._rows.values() if r.is_checked())
        self._count_label.setText(f"{count} selected")
        self._deploy_btn.setText(
            f"Deploy Selected ({count})" if count else "Deploy Selected")

    def _accept_selection(self):
        self.selected_ids = [aid for aid, row in self._rows.items() if row.is_checked()]
        self.accept()

    # -- per-tool wizard --------------------------------------------
    def _open_tool_wizard(self, app_id: str):
        name, url = self._tool_meta.get(app_id, (app_id, ""))
        desc = self._rows[app_id].checkbox.toolTip()
        wizard = ToolInstallWizardDialog(self, app_id, name, desc, url, self._t)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return
        if wizard.mode == "winget":
            self._set_all(False)
            self._rows[app_id].checkbox.setChecked(True)
            self._accept_selection()
        elif wizard.mode == "local" and wizard.local_path:
            self.local_installer = (name, wizard.local_path)
            self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


# ============================================================
#  COMMAND PALETTE — Ctrl+K fuzzy quick-launcher
# ============================================================
def _fuzzy_score(needle: str, haystack: str) -> int | None:
    """Subsequence fuzzy match: every needle char must appear in haystack
    in order (case handled by the caller); tighter, earlier matches score
    higher. Returns None when needle is not a subsequence of haystack."""
    if not needle:
        return 0
    pos = 0
    score = 0
    streak = 0
    for ch in needle:
        idx = haystack.find(ch, pos)
        if idx == -1:
            return None
        gap = idx - pos
        streak = streak + 1 if gap == 0 else 1
        score += (10 - min(gap, 9)) + streak
        pos = idx + 1
    return score


class CommandPalette(PulseDialog):
    """Ctrl+K quick launcher — fuzzy search over every task defined in
    menu_structure.py. Built fresh on each open (like ConfirmDialog /
    AppSelectorDialog: transient, no live re-theme needed) and driven
    through the same accept()/reject() + `chosen_item` pattern, so the
    caller launches the pick through the app's normal request_task()
    pipeline — confirmations, the app selector, and the concurrency guard
    all apply for free, exactly as if a card had been clicked."""

    MAX_RESULTS = 8

    def __init__(self, parent: QWidget, t: dict, entries: list[tuple[dict, str]]):
        super().__init__(parent)
        self.chosen_item: dict | None = None
        self._entries = entries  # (item dict, category title) pairs

        panel = _dialog_chrome(self, t, t["accent"], width=560, anchor="top")

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 14, 14, 10)
        lay.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Type to search Pulse tasks…")
        self._search.setStyleSheet(TH.command_input_qss(t))
        self._search.setFixedHeight(46)
        self._search.textChanged.connect(self._refilter)
        self._search.installEventFilter(self)
        lay.addWidget(self._search)

        self._list = QListWidget()
        self._list.setStyleSheet(TH.command_list_qss(t))
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setMaximumHeight(320)
        self._list.itemActivated.connect(self._activate)
        lay.addWidget(self._list)

        self._refilter("")

    # -- filtering / selection ----------------------------------
    def _refilter(self, text: str):
        self._list.clear()
        query = text.strip().lower()
        scored = []
        for item, category in self._entries:
            haystack = f"{item['title']} {item.get('desc', '')} {category}".lower()
            score = _fuzzy_score(query, haystack)
            if query and score is None:
                continue
            scored.append((score or 0, item, category))
        scored.sort(key=lambda row: -row[0])
        for _, item, category in scored[: self.MAX_RESULTS]:
            row = QListWidgetItem(f"{item['icon']}  {item['title']}   ·   {category}")
            row.setData(Qt.ItemDataRole.UserRole, item)
            self._list.addItem(row)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _move_selection(self, delta: int):
        n = self._list.count()
        if n == 0:
            return
        row = self._list.currentRow()
        row = (row + delta) % n if row != -1 else (0 if delta > 0 else n - 1)
        self._list.setCurrentRow(row)

    def _activate(self, list_item: QListWidgetItem):
        self.chosen_item = list_item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    # -- keyboard: the QLineEdit owns focus, so Up/Down/Enter/Escape are
    # intercepted here and forwarded to the result list -----------------
    def eventFilter(self, obj, event):
        if obj is self._search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_selection(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                current = self._list.currentItem()
                if current is not None:
                    self._activate(current)
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self, duration_ms=130)
        self._search.setFocus()


# ============================================================
#  OFFICE WIZARD — step-by-step Office Deployment Tool flow
# ============================================================
class OfficeWizardDialog(PulseDialog):
    """Multi-path Office Deployment Tool (ODT) wizard.

    Office ships as one Click-to-Run bundle with no per-app silent
    installer, so unlike every other catalog item this can't be a single
    winget call. Three paths, chosen up front:

      A. Automated Cloud Download — Pulse fetches the Click-to-Run client
         itself and applies a built-in standard configuration. No files to
         find, no folders to browse. Sets `task_override` so the caller
         runs -Task InstallOfficeODTAuto instead of the per-file task.
      B. "I already have my files" — auto-detects Desktop\\Office (and the
         OneDrive-redirected / Public Desktop variants), with a folder
         browser and an individual-file-picker as fallbacks.
      C. Beginner Guide — a plain-language walkthrough for downloading the
         ODT and building a configuration.xml by hand via Microsoft's own
         tools, which then feeds into the same locate flow as B.

    All of this is client-side (file-system checks, QFileDialog, browser
    links — no PowerShell spawned yet). After Accepted, the caller reads
    either `task_override` (path A) or `setup_path`/`config_path` (path
    B/C) and runs it through the normal task pipeline — same live console,
    Stop button and toast machinery as every other task.
    """

    ODT_URL = "https://www.microsoft.com/en-us/download/details.aspx?id=49117"
    OCT_URL = "https://config.office.com/deploymentsettings"

    _SETUP_NAMES = ("setup.exe", "Setup.exe", "setup.exe.exe", "Setup.exe.exe")
    # Preference order: known Office Customization Tool export names first
    # (kept in sync with 10-Office.ps1's Find-OfficeConfigFile) — used both
    # to auto-pick when there's exactly one match and to mark the top pick
    # "(recommended)" when several configs sit in the same folder.
    _CONFIG_NAMES = (
        "configuration.xml", "Configuration.xml",
        "configuration.xml.xml", "Configuration.xml.xml",
        "configuration-Office365-x64.xml", "configuration-Office365-x86.xml",
    )

    _SUBTITLES = {
        "choice": "Choose how you'd like to proceed",
        "auto_confirm": "Automated Cloud Download",
        "guide": "Beginner Guide — get the official tools",
        "locate": "Locate your Office files",
        "confirm": "Confirm & Install",
    }

    def __init__(self, parent: QWidget, t: dict):
        super().__init__(parent)
        self._t = t
        self.setup_path: str | None = None
        self.config_path: str | None = None
        self.task_override: str | None = None
        # Where "Back" from the locate step should return to — "choice" if
        # Path B was picked directly, "guide" if arriving via Path C.
        self._locate_origin = "choice"

        panel = _dialog_chrome(self, t, t["accent"], width=560)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(14)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("📄  Microsoft Office Deployment")
        title.setStyleSheet(TH.label_qss(t, "card").replace("14px", "16px"))
        title_col.addWidget(title)
        self._step_label = QLabel("")
        self._step_label.setStyleSheet(TH.label_qss(t, "caption"))
        title_col.addWidget(self._step_label)
        head.addLayout(title_col)
        head.addStretch()
        lay.addLayout(head)

        self._pages: dict[str, int] = {}
        self._stack = QStackedWidget()
        for name, builder in (
            ("choice", self._build_choice_page),
            ("auto_confirm", self._build_auto_page),
            ("guide", self._build_guide_page),
            ("locate", self._build_locate_page),
            ("confirm", self._build_confirm_page),
        ):
            self._pages[name] = self._stack.count()
            self._stack.addWidget(builder())
        lay.addWidget(self._stack)

        self._goto("choice")

    # -- small shared button factories --------------------------
    def _back_button(self, slot) -> QPushButton:
        b = QPushButton("‹  Back")
        b.setFixedSize(90, 36)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(TH.dialog_cancel_qss(self._t))
        b.clicked.connect(slot)
        return b

    def _primary_button(self, text: str, slot, width: int = 130) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(width, 36)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(TH.dialog_go_qss(self._t, self._t["accent"]))
        b.clicked.connect(slot)
        return b

    def _link_row_button(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(50)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(TH.wizard_link_qss(self._t, self._t["accent"]))
        b.clicked.connect(slot)
        return b

    @staticmethod
    def _clear_layout(lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                OfficeWizardDialog._clear_layout(sub)

    # -- navigation -----------------------------------------------
    def _goto(self, step: str):
        self._step_label.setText(self._SUBTITLES[step])
        self._stack.setCurrentIndex(self._pages[step])
        if step == "locate":
            self._run_autodetect()
        elif step == "confirm":
            self._render_confirm()

    # -- step: choice (3 paths) --------------------------------------
    def _build_choice_page(self) -> QWidget:
        t = self._t
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        intro = QLabel(
            "Office ships as one bundle through Microsoft's official "
            "Deployment Tool (ODT) — there's no per-app silent installer. "
            "Choose how you'd like to proceed.")
        intro.setWordWrap(True)
        intro.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(intro)

        opt_a = GlassCard({
            "icon": "🚀", "title": "Automated Cloud Download",
            "desc": "Pulse downloads the Deployment Tool and applies a standard configuration for you.",
        }, t["accent"], t)
        opt_a.setMinimumHeight(88)
        opt_a.clicked.connect(lambda: self._goto("auto_confirm"))
        lay.addWidget(opt_a)

        opt_b = GlassCard({
            "icon": "📁", "title": "I already have my Office folder ready",
            "desc": "Auto-detect the Office folder on your Desktop, or browse to it.",
        }, t["accent"], t)
        opt_b.setMinimumHeight(88)
        opt_b.clicked.connect(self._enter_locate_from_choice)
        lay.addWidget(opt_b)

        opt_c = GlassCard({
            "icon": "📘", "title": "Step-by-Step Beginner Guide",
            "desc": "New to this? A plain-language walkthrough of the official Microsoft tools.",
        }, t["accent"], t)
        opt_c.setMinimumHeight(88)
        opt_c.clicked.connect(lambda: self._goto("guide"))
        lay.addWidget(opt_c)

        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)
        return page

    def _enter_locate_from_choice(self):
        self._locate_origin = "choice"
        self._goto("locate")

    # -- Path A: automated cloud download ----------------------------
    def _build_auto_page(self) -> QWidget:
        t = self._t
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        info = QLabel(
            "Pulse will download the official Office Click-to-Run client "
            "and write a standard configuration to <b>Desktop\\Office</b> "
            "— Word, Excel, PowerPoint and Outlook in English and Arabic. "
            "No files to find, nothing to configure by hand.")
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(info)

        note = QLabel(
            "ℹ️  This standard configuration targets Volume License "
            "activation (no product key baked in). If your network has a "
            "KMS host it activates automatically; otherwise Office installs "
            "but stays unactivated until a key is added. Prefer a "
            "subscription install with your own settings? Use one of the "
            "other two paths instead.")
        note.setWordWrap(True)
        note.setStyleSheet(TH.label_qss(t, "caption"))
        lay.addWidget(note)

        warn = QLabel(
            "⚠️  IMPORTANT: When the Microsoft Setup window appears, DO NOT "
            "close it or open any other apps until it reaches 100%.")
        warn.setWordWrap(True)
        warn.setStyleSheet(TH.warning_banner_qss(t))
        lay.addWidget(warn)
        lay.addStretch()

        row = QHBoxLayout()
        row.addWidget(self._back_button(lambda: self._goto("choice")))
        row.addStretch()
        row.addWidget(self._primary_button(
            "Download && Install Now", self._accept_auto, width=190))
        lay.addLayout(row)
        return page

    def _accept_auto(self):
        self.task_override = "InstallOfficeODTAuto"
        self.accept()

    # -- Path C: beginner guide ---------------------------------------
    def _build_guide_page(self) -> QWidget:
        t = self._t
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        steps = [
            ("1", "Open the Deployment Tool below, run it, and extract it "
                  "into a folder named <b>Office</b> on your Desktop."),
            ("2", "Open the Customization Tool below, choose your apps, "
                  "languages and channel, then download the resulting "
                  "<b>configuration.xml</b> into that same Office folder."),
            ("3", "Come back here and continue — Pulse will pick up both "
                  "files automatically."),
        ]
        for num, text in steps:
            row = QHBoxLayout()
            row.setSpacing(10)
            badge = QLabel(num)
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(
                f"color: {t['accent']}; background: {TH.alpha(t['accent'], 0.14)};"
                f"border: 1px solid {TH.alpha(t['accent'], 0.40)}; border-radius: 11px;"
                "font-size: 11px; font-weight: 700;")
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setStyleSheet(TH.label_qss(t, "body"))
            row.addWidget(label, 1)
            lay.addLayout(row)

        lay.addWidget(self._link_row_button(
            "🌐  Open Office Deployment Tool   ↗",
            lambda: QDesktopServices.openUrl(QUrl(self.ODT_URL))))
        lay.addWidget(self._link_row_button(
            "⚙️  Open Office Customization Tool   ↗",
            lambda: QDesktopServices.openUrl(QUrl(self.OCT_URL))))

        lay.addStretch()
        row = QHBoxLayout()
        row.addWidget(self._back_button(lambda: self._goto("choice")))
        row.addStretch()
        row.addWidget(self._primary_button(
            "I have the files now  ›", self._enter_locate_from_guide, width=170))
        lay.addLayout(row)
        return page

    def _enter_locate_from_guide(self):
        self._locate_origin = "guide"
        self._goto("locate")

    # -- Path B (direct, or continuing from C): locate files ----------
    def _build_locate_page(self) -> QWidget:
        page = QWidget()
        self._locate_lay = QVBoxLayout(page)
        self._locate_lay.setContentsMargins(0, 0, 0, 0)
        self._locate_lay.setSpacing(10)
        return page

    def _locate_back(self):
        self._goto(self._locate_origin)

    def _run_autodetect(self):
        self._clear_layout(self._locate_lay)
        folder, setup, configs = self._detect_office_folder()
        if setup and configs:
            self._render_locate_found(folder, setup, configs)
        else:
            self._render_locate_missing(folder)

    def _render_locate_found(self, folder: str, setup: Path, configs: list[Path]):
        t = self._t
        lay = self._locate_lay

        ok = QLabel(f"✅  Found in <b>{folder}</b>")
        ok.setTextFormat(Qt.TextFormat.RichText)
        ok.setWordWrap(True)
        ok.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(ok)

        setup_row = QLabel(f"<b>Setup:</b> {setup}")
        setup_row.setTextFormat(Qt.TextFormat.RichText)
        setup_row.setWordWrap(True)
        setup_row.setStyleSheet(TH.label_qss(t, "caption"))
        lay.addWidget(setup_row)

        if len(configs) == 1:
            config_row = QLabel(f"<b>Config:</b> {configs[0]}")
            config_row.setTextFormat(Qt.TextFormat.RichText)
            config_row.setWordWrap(True)
            config_row.setStyleSheet(TH.label_qss(t, "caption"))
            lay.addWidget(config_row)
        else:
            picker_label = QLabel(
                f"Found {len(configs)} configuration files — which one should Pulse use?")
            picker_label.setWordWrap(True)
            picker_label.setStyleSheet(TH.label_qss(t, "body"))
            lay.addWidget(picker_label)
            for i, cfg in enumerate(configs):
                tag = "  (recommended)" if i == 0 else ""
                btn = self._link_row_button(
                    f"📝  {cfg.name}{tag}",
                    lambda checked=False, c=cfg: self._on_files_chosen(str(setup), str(c)))
                lay.addWidget(btn)

        browse = QPushButton("📂  Browse for a different folder…")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.setStyleSheet(TH.link_button_qss(t, t["accent"]))
        browse.clicked.connect(self._browse_folder)
        lay.addWidget(browse)
        lay.addStretch()

        row2 = QHBoxLayout()
        row2.addWidget(self._back_button(self._locate_back))
        row2.addStretch()
        if len(configs) == 1:
            row2.addWidget(self._primary_button(
                "Continue  ›", lambda: self._on_files_chosen(str(setup), str(configs[0]))))
        lay.addLayout(row2)

    def _render_locate_missing(self, folder: str):
        t = self._t
        lay = self._locate_lay

        warn = QLabel(
            f"⚠️  No Office folder with both setup.exe and a configuration "
            f"file was found automatically (checked <b>{folder}</b>).")
        warn.setTextFormat(Qt.TextFormat.RichText)
        warn.setWordWrap(True)
        warn.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(warn)

        lay.addWidget(self._link_row_button(
            "📂  Browse for the Office folder…", self._browse_folder))
        lay.addWidget(self._link_row_button(
            "🗂️  Pick setup.exe and configuration.xml individually…",
            self._pick_files_individually))
        lay.addStretch()

        row = QHBoxLayout()
        row.addWidget(self._back_button(self._locate_back))
        row.addStretch()
        retry = QPushButton("Retry auto-detect")
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.setStyleSheet(TH.link_button_qss(t, t["accent"]))
        retry.clicked.connect(self._run_autodetect)
        row.addWidget(retry)
        lay.addLayout(row)

    def _render_browse_incomplete(self, folder: str, setup: Path | None, configs: list[Path]):
        t = self._t
        lay = self._locate_lay
        missing = []
        if not setup:
            missing.append("setup.exe (or the ODT self-extractor)")
        if not configs:
            missing.append("a configuration .xml file")

        msg = QLabel(f"❌  <b>{folder}</b> is missing: " + ", ".join(missing))
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setWordWrap(True)
        msg.setStyleSheet(
            f"color: {t['err']}; font-size: 12px; font-weight: 500;"
            "background: transparent; border: none;")
        lay.addWidget(msg)

        lay.addWidget(self._link_row_button(
            "🗂️  Pick the files individually…", self._pick_files_individually))
        lay.addStretch()

        row = QHBoxLayout()
        row.addWidget(self._back_button(self._locate_back))
        row.addStretch()
        retry = QPushButton("Browse again")
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.setStyleSheet(TH.link_button_qss(t, t["accent"]))
        retry.clicked.connect(self._browse_folder)
        row.addWidget(retry)
        lay.addLayout(row)

    def _browse_folder(self):
        start = str(Path.home() / "Desktop")
        folder = QFileDialog.getExistingDirectory(
            self, "Select the folder with setup.exe and configuration.xml", start)
        if not folder:
            return
        setup, configs = self._find_office_files(Path(folder))
        self._clear_layout(self._locate_lay)
        if setup and configs:
            self._render_locate_found(folder, setup, configs)
        else:
            self._render_browse_incomplete(folder, setup, configs)

    def _pick_files_individually(self):
        start = str(Path.home() / "Desktop")
        setup, _ = QFileDialog.getOpenFileName(
            self, "Select the Office Deployment Tool (setup.exe)", start,
            "Executable files (*.exe)")
        if not setup:
            return
        config, _ = QFileDialog.getOpenFileName(
            self, "Select configuration.xml", str(Path(setup).parent),
            "XML files (*.xml)")
        if not config:
            return
        self._clear_layout(self._locate_lay)
        self._render_locate_found(str(Path(setup).parent), Path(setup), [Path(config)])

    def _on_files_chosen(self, setup: str, config: str):
        self.setup_path = setup
        self.config_path = config
        self._goto("confirm")

    # -- Path B/C tail: confirm + the "don't close it" warning --------
    def _build_confirm_page(self) -> QWidget:
        page = QWidget()
        self._confirm_lay = QVBoxLayout(page)
        self._confirm_lay.setContentsMargins(0, 0, 0, 0)
        self._confirm_lay.setSpacing(14)
        return page

    def _render_confirm(self):
        self._clear_layout(self._confirm_lay)
        t = self._t
        lay = self._confirm_lay

        summary = QLabel(
            f"<b>Setup:</b> {self.setup_path}<br><b>Config:</b> {self.config_path}")
        summary.setTextFormat(Qt.TextFormat.RichText)
        summary.setWordWrap(True)
        summary.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(summary)

        warn = QLabel(
            "⚠️  IMPORTANT: When the Microsoft Setup window appears, DO NOT "
            "close it or open any other apps until it reaches 100%.")
        warn.setWordWrap(True)
        warn.setStyleSheet(TH.warning_banner_qss(t))
        lay.addWidget(warn)
        lay.addStretch()

        row = QHBoxLayout()
        row.addWidget(self._back_button(lambda: self._goto("locate")))
        row.addStretch()
        row.addWidget(self._primary_button("Install Now", self.accept, width=130))
        lay.addLayout(row)

    # -- file-system detection (client-side, no PowerShell spawned) --
    @classmethod
    def _find_office_files(cls, folder: Path) -> tuple[Path | None, list[Path]]:
        if not folder.is_dir():
            return None, []

        setup: Path | None = None
        for name in cls._SETUP_NAMES:
            cand = folder / name
            if cand.is_file():
                setup = cand
                break
        if setup is None:
            matches = sorted(folder.glob("officedeploymenttool*.exe"))
            if matches:
                setup = matches[0]
        if setup is None:
            exes = sorted(folder.glob("*.exe"))
            if exes:
                setup = exes[0]

        # Every .xml in the folder, known names first (preference order),
        # then whatever else is left over, alphabetically — so a folder
        # with several exports still surfaces a sane "recommended" pick
        # instead of an arbitrary one.
        seen: set[Path] = set()
        configs: list[Path] = []
        for name in cls._CONFIG_NAMES:
            cand = folder / name
            if cand.is_file() and cand not in seen:
                configs.append(cand)
                seen.add(cand)
        for xml in sorted(folder.glob("*.xml")):
            if xml not in seen:
                configs.append(xml)
                seen.add(xml)

        return setup, configs

    def _detect_office_folder(self) -> tuple[str, Path | None, list[Path]]:
        home = Path.home()
        userprofile = os.environ.get("USERPROFILE", str(home))
        public = os.environ.get("PUBLIC", "")
        candidates = [
            home / "Desktop" / "Office",
            Path(userprofile) / "OneDrive" / "Desktop" / "Office",
        ]
        if public:
            candidates.append(Path(public) / "Desktop" / "Office")

        for folder in candidates:
            setup, configs = self._find_office_files(folder)
            if setup and configs:
                return str(folder), setup, configs

        first_existing = next((f for f in candidates if f.is_dir()), candidates[0])
        return str(first_existing), None, []

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


# ============================================================
#  TOOL INSTALL WIZARD — generic 3-path single-tool dialog
# ============================================================
class ToolInstallWizardDialog(PulseDialog):
    """Path A / B / C for exactly one tool. Unlike OfficeWizardDialog (which
    branches because Office genuinely has no per-app winget installer),
    every tool this dialog is used for already has a working winget
    package — Path A here just narrows the caller's normal bulk-deploy
    selection down to this one AppId, reusing 100% of the existing
    Smart-Deploy pipeline. Path B opens the vendor's official page and
    closes (nothing left for Pulse to do). Path C hands back a picked
    installer file for the generic InstallLocalFile task.

    Three flat, terminal choices — no sub-navigation needed, unlike the
    Office wizard's multi-step flow.

    After exec():
      Accepted + mode == "winget" -> caller should deploy just this AppId.
      Accepted + mode == "local"  -> `local_path` holds the picked installer.
      Rejected                    -> nothing to do (Cancel, or Path B was
                                      opened in the browser and that's it).
    """

    def __init__(self, parent: QWidget, app_id: str, app_name: str,
                 desc: str, url: str, t: dict):
        super().__init__(parent)
        self.app_id = app_id
        self.mode: str | None = None
        self.local_path: str | None = None

        panel = _dialog_chrome(self, t, t["accent"], width=470)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QLabel(f"⚙️  {app_name}")
        head.setStyleSheet(TH.label_qss(t, "card").replace("14px", "16px"))
        lay.addWidget(head)

        if desc:
            sub = QLabel(desc)
            sub.setWordWrap(True)
            sub.setStyleSheet(TH.label_qss(t, "body"))
            lay.addWidget(sub)

        path_a = GlassCard({
            "icon": "🚀", "title": "One-Click Automated Install",
            "desc": "Silently installs via winget — the same reliable path Pulse uses everywhere.",
        }, t["accent"], t)
        path_a.setMinimumHeight(84)
        path_a.clicked.connect(self._choose_winget)
        lay.addWidget(path_a)

        path_b = GlassCard({
            "icon": "🌐", "title": "Official Download Link",
            "desc": f"Opens {app_name}'s official website in your browser." if url
                    else "Opens a web search for the official download page.",
        }, t["accent"], t)
        path_b.setMinimumHeight(84)
        path_b.clicked.connect(lambda: self._choose_url(url, app_name))
        lay.addWidget(path_b)

        path_c = GlassCard({
            "icon": "📁", "title": "Local File / Manual Selection",
            "desc": "Already downloaded the installer? Pick the file and Pulse will run it.",
        }, t["accent"], t)
        path_c.setMinimumHeight(84)
        path_c.clicked.connect(self._choose_local)
        lay.addWidget(path_c)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _choose_winget(self):
        self.mode = "winget"
        self.accept()

    def _choose_url(self, url: str, app_name: str):
        target = url or f"https://www.google.com/search?q={app_name} download"
        QDesktopServices.openUrl(QUrl(target))
        self.reject()

    def _choose_local(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the installer", str(Path.home() / "Desktop"),
            "Installers (*.exe *.msi)")
        if not path:
            return
        self.mode = "local"
        self.local_path = path
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


# ============================================================
#  DEV HUB ROW — checkbox + dependency hint + per-tool "..." wizard
# ============================================================
class DevHubRow(QFrame):
    """One tool inside DevHubSelectorDialog. Manual-first: unchecked by
    default. `requires_name`, when given, renders a small "needs X" caption
    — a passive hint, never an auto-check. The "⋯" button opens
    ToolInstallWizardDialog for just this tool, independent of the
    checkbox — picking Path A there short-circuits straight to "select
    only this row and deploy" (see DevHubSelectorDialog._open_tool_wizard),
    Path C hands back a local installer instead."""

    options_requested = Signal(str)  # app_id

    def __init__(self, app_id: str, app_name: str, desc: str,
                 requires_id: str | None, requires_name: str | None, t: dict,
                 checked: bool = False):
        super().__init__()
        self.app_id = app_id
        self.requires_id = requires_id

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.checkbox = QCheckBox(app_name)
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        # Dev Hub is manual-first (False); curated app packs arrive
        # pre-selected (True) — the card already promised "the pack".
        self.checkbox.setChecked(checked)
        if desc:
            self.checkbox.setToolTip(desc)
        row.addWidget(self.checkbox)
        row.addStretch()

        self.options_btn = QPushButton("⋯")
        self.options_btn.setFixedSize(28, 24)
        self.options_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.options_btn.setToolTip("Install options for this tool (winget / official link / local file)")
        self.options_btn.clicked.connect(lambda: self.options_requested.emit(self.app_id))
        row.addWidget(self.options_btn)
        outer.addLayout(row)

        self._hint_label: QLabel | None = None
        if requires_name:
            hint = QLabel(f"↳ needs {requires_name}")
            outer.addWidget(hint)
            self._hint_label = hint

        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.dev_hub_row_qss(t))
        self.checkbox.setStyleSheet(TH.checkbox_qss(t, t["accent"]))
        self.options_btn.setStyleSheet(TH.icon_ghost_button_qss(t, t["accent"]))
        if self._hint_label is not None:
            self._hint_label.setStyleSheet(TH.label_qss(t, "caption"))

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_suggested(self, on: bool):
        """Soft amber nudge: a checked-off tool elsewhere needs this one."""
        self.setProperty("suggested", on)
        self.style().unpolish(self)
        self.style().polish(self)


# ============================================================
#  DEV HUB SELECTOR — sections, bundles, master toggle, dependency hints
# ============================================================
class DevHubSelectorDialog(PulseDialog):
    """The Developer & University Hub's tool picker: section-grouped
    checkboxes (Core Runtimes, IDEs, AI, Databases, Containers), one-click
    quick-select bundles, a master Select All/Deselect All, live dependency
    hints, and a per-row "⋯" that opens ToolInstallWizardDialog for a
    single tool. Manual-first throughout — nothing is pre-checked.

    `groups` / `bundles` are passed in rather than imported, keeping this
    file a pure component library (see the module docstring) — the caller
    (main.py) sources them from menu_structure.DEV_HUB_GROUPS/BUNDLES.

    After Accepted, exactly one of these is populated:
      `selected_ids`     bulk InstallDevHub deploy (checkbox selection, or
                          a single-tool Path A short-circuit from the wizard)
      `local_installer`  (app_name, file_path) for a single InstallLocalFile
                          run, from a per-row wizard's Path C
    """

    def __init__(self, parent: QWidget, t: dict,
                 groups: list[tuple[str, list[tuple]]], bundles: list[dict]):
        super().__init__(parent)
        self._t = t
        self.selected_ids: list[str] = []
        self.local_installer: tuple[str, str] | None = None
        self._rows: dict[str, DevHubRow] = {}
        self._tool_meta: dict[str, tuple[str, str]] = {}  # id -> (name, url)
        self._dependents: dict[str, list[str]] = {}        # requires_id -> [dependent ids]

        panel = _dialog_chrome(self, t, t["accent"], width=560)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(10)

        head = QLabel("🎓  Developer Toolkit")
        head.setStyleSheet(TH.label_qss(t, "card").replace("14px", "16px"))
        lay.addWidget(head)

        sub = QLabel("Nothing is pre-selected — tick exactly what you need, "
                      "or start from a bundle below.")
        sub.setWordWrap(True)
        sub.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(sub)

        # -- quick-select bundles --------------------------------
        bundle_row = QHBoxLayout()
        bundle_row.setSpacing(8)
        for bundle in bundles:
            btn = QPushButton(f"{bundle['icon']}  {bundle['title']}".replace("&", "&&"))
            btn.setFixedHeight(38)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(TH.wizard_link_qss(t, t["accent"]))
            btn.clicked.connect(lambda checked=False, ids=bundle["app_ids"]: self._apply_bundle(ids))
            bundle_row.addWidget(btn)
        lay.addLayout(bundle_row)

        # -- master select all/none -------------------------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(16)
        all_btn = QPushButton("Select All")
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.setStyleSheet(TH.link_button_qss(t, t["accent"]))
        all_btn.clicked.connect(lambda: self._set_all(True))
        toolbar.addWidget(all_btn)

        none_btn = QPushButton("Deselect All")
        none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        none_btn.setStyleSheet(TH.link_button_qss(t, t["accent"]))
        none_btn.clicked.connect(lambda: self._set_all(False))
        toolbar.addWidget(none_btn)
        toolbar.addStretch()

        self._count_label = QLabel("0 selected")
        self._count_label.setStyleSheet(TH.label_qss(t, "caption"))
        toolbar.addWidget(self._count_label)
        lay.addLayout(toolbar)

        # -- scrollable, section-grouped checkbox list -------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(TH.scroll_area_qss(t))
        scroll.setMaximumHeight(380)

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 4, 0)
        host_lay.setSpacing(10)

        for group_title, tools in groups:
            section = QLabel(group_title)
            section.setStyleSheet(TH.label_qss(t, "section"))
            host_lay.addWidget(section)
            for app_id, app_name, desc, url, req_id, req_name in tools:
                row = DevHubRow(app_id, app_name, desc, req_id, req_name, t)
                row.checkbox.toggled.connect(
                    lambda checked, aid=app_id: self._on_row_toggled(aid, checked))
                row.options_requested.connect(self._open_tool_wizard)
                self._rows[app_id] = row
                self._tool_meta[app_id] = (app_name, url)
                if req_id:
                    self._dependents.setdefault(req_id, []).append(app_id)
                host_lay.addWidget(row)
        host_lay.addStretch()
        scroll.setWidget(host)
        lay.addWidget(scroll)

        lay.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        self._deploy_btn = QPushButton("Deploy Selected")
        self._deploy_btn.setFixedSize(156, 36)
        self._deploy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._deploy_btn.setStyleSheet(TH.dialog_go_qss(t, t["accent"]))
        self._deploy_btn.clicked.connect(self._accept_selection)
        row.addWidget(self._deploy_btn)
        lay.addLayout(row)

    # -- selection state ------------------------------------------
    def _set_all(self, checked: bool):
        for row in self._rows.values():
            row.checkbox.setChecked(checked)

    def _apply_bundle(self, app_ids: list[str]):
        for app_id in app_ids:
            row = self._rows.get(app_id)
            if row is not None:
                row.checkbox.setChecked(True)

    def _refresh_runtime_suggestion(self, runtime_id: str):
        """Recompute a runtime row's highlight from scratch: on whenever
        it's unchecked AND at least one of its (possibly several — e.g.
        both NetBeans and IntelliJ need Java) dependents is checked.
        Recomputing fresh rather than reacting to just the row that
        changed is what keeps this correct when more than one dependent
        shares the same runtime."""
        runtime_row = self._rows.get(runtime_id)
        if runtime_row is None:
            return
        dependents = self._dependents.get(runtime_id, [])
        needs_it = (not runtime_row.is_checked()) and any(
            self._rows[d].is_checked() for d in dependents if d in self._rows)
        runtime_row.set_suggested(needs_it)

    def _on_row_toggled(self, app_id: str, checked: bool):
        # Live dependency nudge: checking an IDE softly highlights its
        # still-unchecked runtime; unchecking it (or the runtime getting
        # checked) clears the highlight. Never touches another checkbox.
        row = self._rows.get(app_id)
        if row is not None and row.requires_id:
            self._refresh_runtime_suggestion(row.requires_id)
        if app_id in self._dependents:
            self._refresh_runtime_suggestion(app_id)

        count = sum(1 for r in self._rows.values() if r.is_checked())
        self._count_label.setText(f"{count} selected")
        self._deploy_btn.setText(f"Deploy Selected ({count})" if count else "Deploy Selected")

    def _accept_selection(self):
        self.selected_ids = [aid for aid, row in self._rows.items() if row.is_checked()]
        self.accept()

    # -- per-tool wizard --------------------------------------------
    def _open_tool_wizard(self, app_id: str):
        name, url = self._tool_meta.get(app_id, (app_id, ""))
        desc = self._rows[app_id].checkbox.toolTip()
        wizard = ToolInstallWizardDialog(self, app_id, name, desc, url, self._t)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return
        if wizard.mode == "winget":
            self._set_all(False)
            self._rows[app_id].checkbox.setChecked(True)
            self._accept_selection()
        elif wizard.mode == "local" and wizard.local_path:
            self.local_installer = (name, wizard.local_path)
            self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


# ============================================================
#  UPDATE ROW — one winget upgrade candidate (current -> available)
# ============================================================
class UpdateRow(QFrame):
    """One update candidate: checkbox + name + a current -> available
    version audit. Pre-checked, same 'curated pack' contract AppSelector-
    Dialog uses for its packs — the scan already promised these are real,
    available upgrades; the user unticks whatever they don't want."""

    def __init__(self, app_id: str, name: str, current: str, available: str, t: dict):
        super().__init__()
        self.app_id = app_id

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)

        self.checkbox = QCheckBox()
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.setChecked(True)
        outer.addWidget(self.checkbox)

        col = QVBoxLayout()
        col.setSpacing(2)
        self._title = QLabel(name)
        self._title.setWordWrap(True)
        col.addWidget(self._title)
        self._id_label = QLabel(app_id)
        col.addWidget(self._id_label)
        outer.addLayout(col, 1)

        versions = QHBoxLayout()
        versions.setSpacing(6)
        self._current = QLabel(current or "—")
        versions.addWidget(self._current)
        self._arrow = QLabel("→")
        versions.addWidget(self._arrow)
        self._available = QLabel(available or "—")
        versions.addWidget(self._available)
        outer.addLayout(versions)

        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.update_row_qss(t))
        self.checkbox.setStyleSheet(TH.checkbox_qss(t, t["accent"]))
        self._title.setStyleSheet(TH.label_qss(t, "card"))
        self._id_label.setStyleSheet(TH.label_qss(t, "caption"))
        self._current.setStyleSheet(TH.version_chip_qss(t, accent=False))
        self._available.setStyleSheet(TH.version_chip_qss(t, accent=True))
        self._arrow.setStyleSheet(TH.label_qss(t, "faint"))

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()


# ============================================================
#  UPDATE CENTER — live winget scan + selective / bulk apply
# ============================================================
class UpdateCenterDialog(PulseDialog):
    """'Check for Updates': runs a live background winget scan (task
    ScanForUpdates), presents a version audit (current vs. available) per
    app with pre-checked rows, and hands back exactly which AppIds to
    update — it never installs anything itself.

    After exec():
      Accepted + selected_ids non-empty -> caller runs task
      'UpdateSelectedApps' with those AppIds through the app's normal
      request_task()/_start_task() pipeline — the same live console, Stop
      button and toast machinery as every other bulk deploy.
      Rejected -> nothing to do.
    """

    def __init__(self, parent: QWidget, ps1_path: str, t: dict):
        super().__init__(parent)
        self._t = t
        self._ps1_path = ps1_path
        self.selected_ids: list[str] = []
        self._rows: dict[str, UpdateRow] = {}
        self._thread: QThread | None = None
        self._worker: PowerShellTask | None = None
        accent = t["accent"]

        panel = _dialog_chrome(self, t, accent, width=640)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("🔄  Update Center")
        title.setStyleSheet(TH.label_qss(t, "card").replace("14px", "16px"))
        title_col.addWidget(title)
        self._subtitle = QLabel("Scanning installed apps against winget…")
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(TH.label_qss(t, "body"))
        title_col.addWidget(self._subtitle)
        head.addLayout(title_col)
        head.addStretch()
        lay.addLayout(head)

        self._stack = QStackedWidget()
        lay.addWidget(self._stack)
        self._loading_page = self._build_loading_page()
        self._stack.addWidget(self._loading_page)
        self._empty_page = self._build_empty_page()
        self._stack.addWidget(self._empty_page)
        self._error_page = self._build_error_page()
        self._stack.addWidget(self._error_page)
        self._results_page = self._build_results_page()
        self._stack.addWidget(self._results_page)
        self._stack.setCurrentWidget(self._loading_page)

        self._start_scan()

    # -- page builders ----------------------------------------------
    def _build_loading_page(self) -> QWidget:
        t = self._t
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 34, 0, 28)
        lay.setSpacing(16)
        lay.addStretch()
        self._shimmer = ShimmerBar(height=6)
        self._shimmer.set_theme(t)
        lay.addWidget(self._shimmer)
        label = QLabel("Checking every installed app against winget's catalog…")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(label)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)
        return page

    def _build_empty_page(self) -> QWidget:
        t = self._t
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 30, 0, 24)
        lay.setSpacing(10)
        lay.addStretch()
        icon = QLabel("✅")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 40px; background: transparent; border: none;")
        lay.addWidget(icon)
        msg = QLabel("You're all caught up — every installed app is at its latest version.")
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        msg.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(msg)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        rescan = QPushButton("Rescan")
        rescan.setFixedSize(96, 36)
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.setStyleSheet(TH.dialog_cancel_qss(t))
        rescan.clicked.connect(self._start_scan)
        row.addWidget(rescan)
        close = QPushButton("Close")
        close.setFixedSize(96, 36)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(TH.dialog_go_qss(t, t["accent"]))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)
        return page

    def _build_error_page(self) -> QWidget:
        t = self._t
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 30, 0, 24)
        lay.setSpacing(10)
        lay.addStretch()
        icon = QLabel("⚠️")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 40px; background: transparent; border: none;")
        lay.addWidget(icon)
        self._error_label = QLabel("")
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(self._error_label)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Close")
        close.setFixedSize(96, 36)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(TH.dialog_cancel_qss(t))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        retry = QPushButton("Retry")
        retry.setFixedSize(96, 36)
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.setStyleSheet(TH.dialog_go_qss(t, t["accent"]))
        retry.clicked.connect(self._start_scan)
        row.addWidget(retry)
        lay.addLayout(row)
        return page

    def _build_results_page(self) -> QWidget:
        t = self._t
        accent = t["accent"]
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(16)
        all_btn = QPushButton("Select All")
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.setStyleSheet(TH.link_button_qss(t, accent))
        all_btn.clicked.connect(lambda: self._set_all(True))
        toolbar.addWidget(all_btn)
        none_btn = QPushButton("Deselect All")
        none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        none_btn.setStyleSheet(TH.link_button_qss(t, accent))
        none_btn.clicked.connect(lambda: self._set_all(False))
        toolbar.addWidget(none_btn)
        toolbar.addStretch()
        self._count_chip = QLabel("")
        self._count_chip.setStyleSheet(TH.stat_chip_qss(t, "accent"))
        toolbar.addWidget(self._count_chip)
        lay.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(TH.scroll_area_qss(t))
        scroll.setMaximumHeight(380)
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 6, 0)
        self._host_lay.setSpacing(8)
        self._host_lay.addStretch()
        scroll.setWidget(self._host)
        lay.addWidget(scroll)

        row = QHBoxLayout()
        row.setSpacing(8)
        rescan = QPushButton("Rescan")
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.setStyleSheet(TH.link_button_qss(t, accent))
        rescan.clicked.connect(self._start_scan)
        row.addWidget(rescan)
        row.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setFixedSize(90, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        self._update_selected_btn = QPushButton("Update Selected")
        self._update_selected_btn.setFixedSize(168, 36)
        self._update_selected_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_selected_btn.setStyleSheet(TH.dialog_secondary_go_qss(t, accent))
        self._update_selected_btn.clicked.connect(self._accept_selected)
        row.addWidget(self._update_selected_btn)

        self._update_all_btn = QPushButton("⚡  Update All")
        self._update_all_btn.setFixedSize(136, 36)
        self._update_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_all_btn.setStyleSheet(TH.dialog_go_qss(t, accent))
        self._update_all_btn.clicked.connect(self._accept_all)
        row.addWidget(self._update_all_btn)
        lay.addLayout(row)
        return page

    # -- scan lifecycle -----------------------------------------------
    def _start_scan(self):
        if self._thread is not None:
            return  # a scan is already in flight
        self._subtitle.setText("Scanning installed apps against winget…")
        self._clear_rows()
        self._stack.setCurrentWidget(self._loading_page)
        self._shimmer.start()

        thread = QThread(self)
        worker = PowerShellTask(self._ps1_path, "ScanForUpdates", timeout=90)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thread_finished(self):
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    def _on_scan_failed(self, message: str):
        self._show_error(message or "The update scan failed to run.")

    def _on_scan_finished(self, result: TaskResult):
        self._shimmer.stop()
        if not result.success:
            self._show_error(result.message)
            return
        updates = result.data if isinstance(result.data, list) else []
        if not updates:
            self._subtitle.setText("Every installed app is up to date.")
            self._stack.setCurrentWidget(self._empty_page)
            return
        self._populate_rows(updates)
        plural = "" if len(updates) == 1 else "s"
        self._subtitle.setText(
            f"{len(updates)} update{plural} available — audited against winget just now.")
        self._stack.setCurrentWidget(self._results_page)

    def _show_error(self, message: str):
        self._shimmer.stop()
        self._error_label.setText(message or "The update scan failed.")
        self._subtitle.setText("Scan failed.")
        self._stack.setCurrentWidget(self._error_page)

    # -- row management -------------------------------------------------
    def _clear_rows(self):
        self._rows.clear()
        while self._host_lay.count() > 1:   # keep the trailing stretch
            item = self._host_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _populate_rows(self, updates: list):
        self._clear_rows()
        for entry in updates:
            if not isinstance(entry, dict):
                continue
            app_id = str(entry.get("Id", "")).strip()
            if not app_id:
                continue
            name = str(entry.get("Name") or app_id)
            current = str(entry.get("CurrentVersion") or "—")
            available = str(entry.get("AvailableVersion") or "—")
            row = UpdateRow(app_id, name, current, available, self._t)
            row.checkbox.toggled.connect(self._update_count)
            self._rows[app_id] = row
            self._host_lay.insertWidget(self._host_lay.count() - 1, row)
        self._update_count()

    def _set_all(self, checked: bool):
        for row in self._rows.values():
            row.checkbox.setChecked(checked)

    def _update_count(self, _checked: bool = False):
        count = sum(1 for r in self._rows.values() if r.is_checked())
        total = len(self._rows)
        self._count_chip.setText(f"{count} of {total} selected")
        self._update_selected_btn.setText(
            f"Update Selected ({count})" if count else "Update Selected")
        self._update_selected_btn.setEnabled(count > 0)

    # -- acceptance -------------------------------------------------
    def _accept_selected(self):
        self.selected_ids = [aid for aid, row in self._rows.items() if row.is_checked()]
        if not self.selected_ids:
            return
        self.accept()

    def _accept_all(self):
        self.selected_ids = list(self._rows.keys())
        if not self.selected_ids:
            return
        self.accept()

    def reject(self):
        if self._worker is not None:
            self._worker.cancel()
        super().reject()

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


# ============================================================
#  STARTUP ROW — one startup entry with a live enable/disable switch
# ============================================================
class StartupRow(QFrame):
    """One startup entry: name, boot-impact badge, recommendation tag and
    the backend's plain-language reason, plus a ToggleSwitch that fires
    the disable/enable task the instant it flips — no separate 'Apply'
    step, per the brief's 'fluid, native toggle switches ... instantly'."""

    _REC_LABELS = {"Disable": "Recommended to Disable", "Keep": "Safe to Keep", "Review": "Worth Reviewing"}

    toggle_requested = Signal(str, bool)   # (encoded_id, want_enabled)

    def __init__(self, item: dict, t: dict):
        super().__init__()
        self.item_id = str(item["Id"])
        self._enabled = bool(item["Enabled"])
        self._impact = str(item.get("Impact") or "Medium")
        self._recommendation = str(item.get("Recommendation") or "Review")

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(4)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self._name = QLabel(str(item.get("Name", "")))
        name_row.addWidget(self._name)
        self._impact_badge = QLabel(f"{self._impact.upper()} IMPACT")
        name_row.addWidget(self._impact_badge)
        self._rec_badge = QLabel(self._REC_LABELS.get(self._recommendation, self._recommendation))
        name_row.addWidget(self._rec_badge)
        name_row.addStretch()
        col.addLayout(name_row)

        type_label = "Registry (Run key)" if item.get("Type") == "Registry" else "Startup folder shortcut"
        reason = str(item.get("Reason") or "")
        self._meta = QLabel(f"{type_label}  ·  {reason}")
        self._meta.setWordWrap(True)
        col.addWidget(self._meta)
        outer.addLayout(col, 1)

        self.switch = ToggleSwitch(t, checked=self._enabled)
        self.switch.toggled.connect(self._on_switch)
        outer.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)

        self.apply_theme(t)
        self._sync_disabled_prop()

    def _on_switch(self, checked: bool):
        self.toggle_requested.emit(self.item_id, checked)

    def set_enabled_state(self, enabled: bool):
        self._enabled = enabled
        self.switch.set_checked_silent(enabled)
        self._sync_disabled_prop()

    def set_busy(self, busy: bool):
        self.switch.set_busy(busy)

    def _sync_disabled_prop(self):
        self.setProperty("disabled_item", not self._enabled)
        self.style().unpolish(self)
        self.style().polish(self)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.startup_row_qss(t))
        self._name.setStyleSheet(TH.label_qss(t, "card"))
        self._impact_badge.setStyleSheet(TH.impact_badge_qss(t, self._impact))
        self._rec_badge.setStyleSheet(TH.recommendation_badge_qss(t, self._recommendation))
        self._meta.setStyleSheet(TH.label_qss(t, "caption"))
        self.switch.apply_theme(t)


# ============================================================
#  STARTUP MANAGER — intelligent optimization hub
# ============================================================
class StartupManagerDialog(PulseDialog):
    """Startup Report, overhauled into an optimization hub: scans Run keys
    + Startup folders (task StartupReport, JSON payload), groups every
    entry under the backend's recommendation, and lets the user flip each
    one live via ToggleSwitch — every click round-trips through its own
    worker immediately. Nothing is handed back to the caller: this dialog
    is fully self-contained (unlike AppSelectorDialog/UpdateCenterDialog,
    which only decide what a *later* task should run), so main.py just
    opens it and moves on when it closes."""

    def __init__(self, parent: QWidget, ps1_path: str, t: dict):
        super().__init__(parent)
        self._t = t
        self._ps1_path = ps1_path
        self._rows: dict[str, StartupRow] = {}
        self._items: dict[str, dict] = {}

        self._scan_thread: QThread | None = None
        self._scan_worker: PowerShellTask | None = None
        self._toggle_thread: QThread | None = None
        self._toggle_worker: PowerShellTask | None = None
        self._toggle_queue: list[tuple[str, bool]] = []
        self._active_toggle_id: str | None = None
        self._active_want_enabled: bool = False

        accent = t["accent"]
        panel = _dialog_chrome(self, t, accent, width=640)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("🚀  Startup Manager")
        title.setStyleSheet(TH.label_qss(t, "card").replace("14px", "16px"))
        title_col.addWidget(title)
        self._subtitle = QLabel("Auditing Run keys and Startup folders…")
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(TH.label_qss(t, "body"))
        title_col.addWidget(self._subtitle)
        head.addLayout(title_col)
        head.addStretch()
        lay.addLayout(head)

        self._stack = QStackedWidget()
        lay.addWidget(self._stack)
        self._loading_page = self._build_loading_page()
        self._stack.addWidget(self._loading_page)
        self._error_page = self._build_error_page()
        self._stack.addWidget(self._error_page)
        self._results_page = self._build_results_page()
        self._stack.addWidget(self._results_page)
        self._stack.setCurrentWidget(self._loading_page)

        self._status_strip = QLabel("")
        self._status_strip.setWordWrap(True)
        self._status_strip.hide()
        lay.addWidget(self._status_strip)
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._status_strip.hide)

        self._start_scan()

    # -- page builders ----------------------------------------------
    def _build_loading_page(self) -> QWidget:
        t = self._t
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 34, 0, 28)
        lay.setSpacing(16)
        lay.addStretch()
        self._shimmer = ShimmerBar(height=6)
        self._shimmer.set_theme(t)
        lay.addWidget(self._shimmer)
        label = QLabel("Reading Run keys, the Startup folders, and scoring boot impact…")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(label)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        lay.addLayout(row)
        return page

    def _build_error_page(self) -> QWidget:
        t = self._t
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 30, 0, 24)
        lay.setSpacing(10)
        lay.addStretch()
        icon = QLabel("⚠️")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 40px; background: transparent; border: none;")
        lay.addWidget(icon)
        self._error_label = QLabel("")
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(self._error_label)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Close")
        close.setFixedSize(96, 36)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(TH.dialog_cancel_qss(t))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        retry = QPushButton("Retry")
        retry.setFixedSize(96, 36)
        retry.setCursor(Qt.CursorShape.PointingHandCursor)
        retry.setStyleSheet(TH.dialog_go_qss(t, t["accent"]))
        retry.clicked.connect(self._start_scan)
        row.addWidget(retry)
        lay.addLayout(row)
        return page

    def _build_results_page(self) -> QWidget:
        t = self._t
        accent = t["accent"]
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        summary = QHBoxLayout()
        summary.setSpacing(8)
        self._chip_enabled = QLabel("")
        self._chip_enabled.setStyleSheet(TH.stat_chip_qss(t, "neutral"))
        summary.addWidget(self._chip_enabled)
        self._chip_disabled = QLabel("")
        self._chip_disabled.setStyleSheet(TH.stat_chip_qss(t, "neutral"))
        summary.addWidget(self._chip_disabled)
        self._chip_recommended = QLabel("")
        self._chip_recommended.setStyleSheet(TH.stat_chip_qss(t, "warn"))
        summary.addWidget(self._chip_recommended)
        summary.addStretch()
        lay.addLayout(summary)

        self._optimize_btn = QPushButton("⚡  Optimize Startup")
        self._optimize_btn.setFixedHeight(38)
        self._optimize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._optimize_btn.setStyleSheet(TH.dialog_go_qss(t, accent))
        self._optimize_btn.setToolTip(
            "Disables every currently-enabled item the audit recommends disabling, one by one.")
        self._optimize_btn.clicked.connect(self._start_optimize)
        lay.addWidget(self._optimize_btn)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(TH.scroll_area_qss(t))
        scroll.setMaximumHeight(360)
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 6, 0)
        self._host_lay.setSpacing(8)
        self._host_lay.addStretch()
        scroll.setWidget(self._host)
        lay.addWidget(scroll)

        row = QHBoxLayout()
        rescan = QPushButton("Rescan")
        rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan.setStyleSheet(TH.link_button_qss(t, accent))
        rescan.clicked.connect(self._start_scan)
        row.addWidget(rescan)
        row.addStretch()
        close = QPushButton("Close")
        close.setFixedSize(96, 36)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(TH.dialog_secondary_go_qss(t, accent))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        return page

    # -- scan lifecycle -----------------------------------------------
    def _start_scan(self):
        if self._scan_thread is not None:
            return
        self._subtitle.setText("Auditing Run keys and Startup folders…")
        self._clear_rows()
        self._stack.setCurrentWidget(self._loading_page)
        self._shimmer.start()

        thread = QThread(self)
        worker = PowerShellTask(self._ps1_path, "StartupReport", timeout=60)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_scan_thread_finished)
        self._scan_thread = thread
        self._scan_worker = worker
        thread.start()

    def _on_scan_thread_finished(self):
        if self._scan_worker is not None:
            self._scan_worker.deleteLater()
            self._scan_worker = None
        if self._scan_thread is not None:
            self._scan_thread.deleteLater()
            self._scan_thread = None

    def _on_scan_failed(self, message: str):
        self._show_error(message or "The startup audit failed to run.")

    def _on_scan_finished(self, result: TaskResult):
        self._shimmer.stop()
        if not result.success:
            self._show_error(result.message)
            return
        items = result.data if isinstance(result.data, list) else []
        items = [it for it in items if isinstance(it, dict) and it.get("Id")]
        if not items:
            self._show_error("No startup items were found to audit.")
            return
        self._populate_rows(items)
        self._subtitle.setText("Toggle any item to change it instantly — changes are reversible.")
        self._stack.setCurrentWidget(self._results_page)

    def _show_error(self, message: str):
        self._shimmer.stop()
        self._error_label.setText(message or "The startup audit failed.")
        self._subtitle.setText("Audit failed.")
        self._stack.setCurrentWidget(self._error_page)

    # -- row management -------------------------------------------------
    def _clear_rows(self):
        self._rows.clear()
        while self._host_lay.count() > 1:   # keep the trailing stretch
            item = self._host_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _populate_rows(self, items: list[dict]):
        self._clear_rows()
        self._items = {str(it["Id"]): it for it in items}

        buckets: dict[str, list[dict]] = {"Disable": [], "Review": [], "Keep": [], "_off": []}
        for it in items:
            if not it.get("Enabled"):
                buckets["_off"].append(it)
            else:
                buckets.setdefault(it.get("Recommendation", "Review"), []).append(it)

        sections = [
            ("⚠️  Recommended to Disable", buckets["Disable"]),
            ("🔎  Worth Reviewing", buckets["Review"]),
            ("✅  Safe to Keep", buckets["Keep"]),
            ("⏸️  Currently Disabled", buckets["_off"]),
        ]
        for label, rows in sections:
            if not rows:
                continue
            header = QLabel(f"{label}   ·   {len(rows)}")
            header.setStyleSheet(TH.label_qss(self._t, "section"))
            self._host_lay.insertWidget(self._host_lay.count() - 1, header)
            for it in rows:
                row = StartupRow(it, self._t)
                row.toggle_requested.connect(self._on_toggle_requested)
                self._rows[str(it["Id"])] = row
                self._host_lay.insertWidget(self._host_lay.count() - 1, row)
        self._update_summary()

    def _update_summary(self):
        items = list(self._items.values())
        enabled = sum(1 for it in items if it.get("Enabled"))
        disabled = len(items) - enabled
        recommended = sum(
            1 for it in items if it.get("Enabled") and it.get("Recommendation") == "Disable")
        self._chip_enabled.setText(f"{enabled} enabled")
        self._chip_disabled.setText(f"{disabled} disabled")
        self._chip_recommended.setText(f"{recommended} recommended to disable")
        self._optimize_btn.setEnabled(recommended > 0)
        self._optimize_btn.setText(
            f"⚡  Optimize Startup ({recommended})" if recommended else "⚡  Optimize Startup — all clear")

    # -- toggle queue (sequential — one PowerShell process at a time) --
    def _on_toggle_requested(self, item_id: str, want_enabled: bool):
        self._toggle_queue.append((item_id, want_enabled))
        self._pump_toggle_queue()

    def _start_optimize(self):
        recommended_ids = [
            it["Id"] for it in self._items.values()
            if it.get("Enabled") and it.get("Recommendation") == "Disable"
        ]
        if not recommended_ids:
            return
        self._show_status("info", f"Disabling {len(recommended_ids)} recommended item(s)…")
        for item_id in recommended_ids:
            row = self._rows.get(item_id)
            if row is not None:
                row.set_busy(True)
            self._toggle_queue.append((item_id, False))
        self._pump_toggle_queue()

    def _pump_toggle_queue(self):
        if self._toggle_worker is not None or not self._toggle_queue:
            return
        item_id, want_enabled = self._toggle_queue.pop(0)
        row = self._rows.get(item_id)
        if row is not None:
            row.set_busy(True)
        self._active_toggle_id = item_id
        self._active_want_enabled = want_enabled

        task_name = "StartupEnableItem" if want_enabled else "StartupDisableItem"
        thread = QThread(self)
        worker = PowerShellTask(self._ps1_path, task_name, timeout=60, app_ids=[item_id])
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_toggle_finished)
        worker.failed.connect(self._on_toggle_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(self._on_toggle_thread_finished)
        self._toggle_thread = thread
        self._toggle_worker = worker
        thread.start()

    def _on_toggle_thread_finished(self):
        if self._toggle_worker is not None:
            self._toggle_worker.deleteLater()
            self._toggle_worker = None
        if self._toggle_thread is not None:
            self._toggle_thread.deleteLater()
            self._toggle_thread = None
        QTimer.singleShot(0, self._pump_toggle_queue)

    def _on_toggle_finished(self, result: TaskResult):
        item_id, want_enabled = self._active_toggle_id, self._active_want_enabled
        row = self._rows.get(item_id)
        if row is not None:
            row.set_busy(False)
        if result.success:
            if item_id in self._items:
                self._items[item_id]["Enabled"] = want_enabled
            if row is not None:
                row.set_enabled_state(want_enabled)
            self._show_status("ok", f"✓  {result.message}")
        else:
            if row is not None:
                row.set_enabled_state(not want_enabled)   # snap back
            self._show_status("err", f"✕  {result.message}")
        self._update_summary()

    def _on_toggle_failed(self, message: str):
        item_id = self._active_toggle_id
        row = self._rows.get(item_id)
        if row is not None:
            row.set_busy(False)
            row.set_enabled_state(not self._active_want_enabled)
        self._show_status("err", f"✕  {message}")
        self._update_summary()

    def _show_status(self, tone: str, message: str):
        self._status_strip.setText(message)
        self._status_strip.setStyleSheet(TH.inline_status_qss(self._t, tone))
        self._status_strip.show()
        self._status_timer.start(4000)

    # -- lifecycle --------------------------------------------------
    def _cancel_workers(self):
        if self._scan_worker is not None:
            self._scan_worker.cancel()
        if self._toggle_worker is not None:
            self._toggle_worker.cancel()
        self._toggle_queue.clear()

    def reject(self):
        self._cancel_workers()
        super().reject()

    def accept(self):
        self._cancel_workers()
        super().accept()

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)
