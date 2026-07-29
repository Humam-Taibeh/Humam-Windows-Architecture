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

import math
import os
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QDateTime, QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation,
    QRect, QRectF, Qt, QThread, QTime, QTimer, QUrl, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QTextCursor, QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QPlainTextEdit, QPushButton, QScrollArea, QSizeGrip, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

from frontend.animations import (
    GlowController, RippleController, ShimmerBar, paint_aurora_edge,
    paint_bevel_frame, paint_glow_frame, paint_nav_indicator,
    paint_ripple_frame, paint_top_sheen, squircle_path,
)
from frontend import theme as TH
# Update Center / Startup Manager (v6.3) run their own background scans and
# per-item actions independently of main.py's single-task console pipeline
# (both are modal dialogs that fully cover it anyway) - the one deliberate
# exception to this file's "pure component library" rule, since the alter-
# native (threading process ownership through main.py) would either block
# the dialog's own loading UI or duplicate PowerShellTask's cancellation-
# safe process/thread bookkeeping here.
from utils import resources  # noqa: E402
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
        # Square by default, matching the opaque square shell it covers.
        # This is the value the FIRST paint uses — refit_dialog re-asserts
        # it, but a rounded default would flash two lit wedges of shell at
        # the bottom corners on the frame before that lands.
        self._scrim_radius = 0

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


# Every scrollable "row list" selector (App Selector, Dev Hub, Update
# Center, Startup Manager, and a hub's own landing screen) shares this one
# DYNAMIC sizing rule — global theme consistency means these can never
# quietly drift apart the way UpdateCenterDialog (640px) and
# AppSelectorDialog (560px) once had, and a fixed pixel box can never look
# cramped on a big display or oversized on a small one. Both dimensions
# scale off the HOST WINDOW's *current* size (re-applied live on resize by
# refit_dialog below), landing mid-band of the brief's percentages, with a
# width floor/ceiling so it never goes pocket-sized or absurdly wide on an
# ultrawide monitor. Simple one-off confirmations/wizards (ConfirmDialog,
# CommandPalette, OfficeWizardDialog, ToolInstallWizardDialog) keep their
# own narrower, purpose-built FIXED widths — a two-sentence confirm scaling
# to 1100px on a 4K screen would trade clutter for empty space, not fix it.
_SELECTOR_WIDTH_FRACTION = 0.675   # ~65-70% of host width
_SELECTOR_WIDTH_MIN = 800
_SELECTOR_WIDTH_MAX = 1100
_SELECTOR_HEIGHT_FRACTION = 0.775  # ~75-80% of host height
_SELECTOR_HEIGHT_MIN = 460


def _resolve_host_window(dialog: QDialog) -> QWidget | None:
    """Climb from `dialog` to the real top-level app window — nested
    wizards (a PulseDialog opened from another PulseDialog) are parented
    to the dialog above them, not the app, so this walks up through any
    number of stacked dialogs to the one true QMainWindow."""
    host = dialog.parentWidget()
    if host is None:
        return None
    host = host.window()
    while isinstance(host, QDialog) and host.parentWidget() is not None:
        host = host.parentWidget().window()
    return host


def _selector_panel_size(dialog: QDialog) -> tuple[int, int]:
    """(width, height) for a responsive selector panel, derived from the
    host window's CURRENT size — called once at construction and again on
    every host resize (refit_dialog), so an already-open dialog visibly
    grows/shrinks along with the window instead of freezing at whatever
    size the window happened to be when it was opened."""
    host = _resolve_host_window(dialog)
    if host is None:
        return (_SELECTOR_WIDTH_MIN, _SELECTOR_HEIGHT_MIN)
    width = max(_SELECTOR_WIDTH_MIN,
                min(_SELECTOR_WIDTH_MAX, round(host.width() * _SELECTOR_WIDTH_FRACTION)))
    height = max(_SELECTOR_HEIGHT_MIN, round(host.height() * _SELECTOR_HEIGHT_FRACTION))
    return (width, height)


def _dialog_chrome(dialog: PulseDialog, t: dict, accent: str,
                   width: int = 0, radius: int = 18, anchor: str = "center",
                   responsive: bool = False) -> "DepthCard":
    """One shared construction path for every Pulse dialog: the frosted
    DepthCard panel, laid out centered (or top-anchored for the command
    palette) inside the dialog's full-body scrim, plus a soft elevation
    shadow. A drop-shadow QGraphicsEffect is allowed here as the
    deliberate exception to the animations.py doctrine: dialogs are small,
    transient surfaces that repaint a handful of times — not steady-state
    60fps chrome.

    `responsive=True` sizes the panel dynamically off the host window (see
    _selector_panel_size) and keeps it that way as the window resizes;
    `width` (a fixed pixel value) is used only when `responsive=False`.

    Returns the panel; the caller builds its content layout inside it."""
    panel = DepthCard(radius=radius, parent=dialog)
    dialog._responsive_panel = responsive
    if responsive:
        panel.setFixedSize(*_selector_panel_size(dialog))
    else:
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
    and reachable no matter what is open — with a square scrim, matching
    the now square-and-opaque shell. Called from showEvent and again whenever the
    host resizes while a dialog is open — which is also what keeps a
    responsive selector panel (see _dialog_chrome) sized to the window
    live, instead of freezing at its opening-time dimensions."""
    host = _resolve_host_window(dialog)
    if host is not None:
        titlebar_h = getattr(getattr(host, "titlebar", None), "height", lambda: 0)()
        body = QRect(0, titlebar_h, host.width(), host.height() - titlebar_h)
        dialog.setGeometry(QRect(host.mapToGlobal(body.topLeft()), body.size()))
        theme_mgr = getattr(host, "theme", None)
        if theme_mgr is not None:
            # Square in both states: the shell it covers is now square in
            # both states too (DWM owns the window's rounding), so a
            # rounded scrim would leave four lit wedges of shell showing.
            dialog._set_scrim(theme_mgr.t, 0)
        if getattr(dialog, "_responsive_panel", False) and dialog.panel is not None:
            dialog.panel.setFixedSize(*_selector_panel_size(dialog))


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
                 app_name: str, version: str, channel: str = "",
                 is_admin: bool = True):
        super().__init__(window)
        self._window = window
        self._drag_offset: QPoint | None = None
        self._press_gp: QPoint | None = None
        self._icon_font = _caption_icon_font()
        self.setFixedHeight(50)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 8, 10, 6)
        lay.setSpacing(9)

        # Same breathing-pulse component the Welcome page's hero mark uses
        # (BreathingIcon) — the brand glyph reads identically everywhere
        # it appears instead of animating on the home screen and sitting
        # inert in the title bar.
        self._glyph = BreathingIcon("✦", size=26, accent=t["accent"])
        lay.addWidget(self._glyph)
        self._name = QLabel(app_name)
        lay.addWidget(self._name)
        self._version = QLabel(f"v{version}")
        lay.addWidget(self._version)
        self._channel: QLabel | None = None
        if channel:
            self._channel = QLabel(channel.upper())
            lay.addWidget(self._channel)
        # v8: elevation state/action lives in the sidebar footer
        # (main.PulseApp._build_ui), not the title bar — the left cluster stays
        # a clean brand-only block. (v9.4 removed the dead admin-badge no-op
        # scaffolding that used to sit here.)
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
        self._glyph.apply_theme(t)
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
    """Sidebar module entry — v7: a painted, accent-tinted icon PLAQUE
    holding one monochrome Fluent glyph, the module title, a left Aurora
    active-rail when selected, and the effect-free painted glow/ripple. The
    glyph comes from theme.GLYPHS via a semantic key, so the whole sidebar
    reads as one coherent line-icon system instead of mismatched emoji."""

    _PLAQUE = 30       # plaque edge (px)
    _PLAQUE_X = 12     # left inset — must stay in sync with nav_button_qss padding

    def __init__(self, glyph_key: str, title: str, accent_key: str, t: dict):
        # QPushButton treats a lone "&" as a mnemonic marker (it vanishes
        # and the following character gets an accelerator underline) —
        # category titles like "Maintenance & Repair" need it escaped to
        # "&&" or the button renders "Maintenance _Repair". The icon is now
        # PAINTED (a plaque), so only the title is button text.
        super().__init__(title.replace("&", "&&"))
        self._glyph_key = glyph_key
        # v10: the module's accent KEY, not a frozen hex — re-resolved on
        # every theme switch inside apply_theme (see theme.resolve_accent).
        self._accent_key = accent_key
        self.setFixedHeight(46)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", False)
        self._glow = GlowController(self, TH.resolve_accent(t, accent_key))
        self._ripple = RippleController(self)
        self._accent = QColor(TH.resolve_accent(t, accent_key))
        self._accent2 = QColor(t["accent2"])
        self._icon_font: QFont | None = None
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.nav_button_qss(t))
        # the module's OWN colour for this theme — the sidebar rail reads as
        # a spectrum, and the glow/plaque follow it (previously the plaque
        # used the module colour while the glow used the generic app accent,
        # so a hovered nav entry lit up in the wrong colour).
        self._accent = QColor(TH.resolve_accent(t, self._accent_key))
        self._glow.set_accent(TH.resolve_accent(t, self._accent_key))
        self._accent2 = QColor(t["accent2"])
        self._glyph_char, self._glyph_fluent = TH.glyph(self._glyph_key)
        self._icon_font = TH.icon_font(16) if self._glyph_fluent else None
        self._plaque_fill = QColor(self._accent)
        self._plaque_fill.setAlphaF(0.12)
        self._plaque_line = QColor(self._accent)
        self._plaque_line.setAlphaF(0.30)
        # v9 "Spectrum": the idle glyph carries its own module accent (was a
        # monochrome text_soft), so all six modules read as a colored rail at
        # rest — matching the newly-colored GlassCard plaques (icon_plaque_qss).
        self._glyph_color_idle = QColor(self._accent)

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._ripple.trigger(e.position())
        super().mousePressEvent(e)

    def _paint_plaque(self, p: QPainter):
        selected = bool(self.property("selected"))
        y = (self.height() - self._PLAQUE) / 2.0
        box = QRectF(self._PLAQUE_X, y, self._PLAQUE, self._PLAQUE)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # brighter fill/glyph when selected — the plaque lights with the module
        fill = QColor(self._accent)
        fill.setAlphaF(0.20 if selected else 0.12)
        line = QColor(self._accent)
        line.setAlphaF(0.45 if selected else 0.30)
        p.setPen(QPen(line, 1.0))
        p.setBrush(fill)
        p.drawRoundedRect(box, 9, 9)
        # glyph
        p.setPen(self._accent if selected else self._glyph_color_idle)
        if self._icon_font is not None:
            p.setFont(self._icon_font)
        else:
            f = QFont(self.font())
            f.setPixelSize(15)
            p.setFont(f)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, self._glyph_char)

    def paintEvent(self, e):
        super().paintEvent(e)  # QSS background/text first
        p = QPainter(self)
        self._paint_plaque(p)
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
def format_relative_age(timestamp: float, now: float | None = None) -> str:
    """A short, honest "how long ago" for a card caption.

    Deliberately COARSE and rounded down: "3 days ago" is what someone
    wants to know, and a precise "2 days 21 hours ago" reads as noise on a
    card. Rounding down also keeps the label from ever overstating how
    recent a run was, which is the direction that would mislead.
    """
    if not timestamp:
        return ""
    now = time.time() if now is None else now
    seconds = now - timestamp
    if seconds < 0:
        # Clock moved backwards (DST, NTP correction, a restored profile).
        # "Just now" is the only claim still defensible.
        return "just now"
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d ago"
    weeks = days / 7
    if weeks < 5:
        return f"{int(weeks)}w ago"
    months = days / 30
    if months < 12:
        return f"{int(months)}mo ago"
    return f"{int(days / 365)}y ago"


def format_duration(milliseconds: float) -> str:
    """Compact duration for the "typically ~Ns" hint."""
    if milliseconds <= 0:
        return ""
    seconds = milliseconds / 1000.0
    if seconds < 60:
        return f"{max(1, int(round(seconds)))}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(round(minutes))}m"
    hours = minutes / 60
    if hours < 10:
        # One decimal only where it carries information (1.5h, not 1.0h).
        text = f"{hours:.1f}".rstrip("0").rstrip(".")
        return f"{text}h"
    return f"{int(round(hours))}h"


def format_history_caption(entry: dict | None) -> tuple[str, str]:
    """(pill text, tooltip) for a task's run history — ("", "") when there
    is nothing truthful to say.

    The duration half is withheld until a task has run more than once: a
    single sample is not a "typical" duration, and presenting it as one
    would be a confident-sounding guess drawn from one data point.
    """
    if not entry:
        return "", ""
    age = format_relative_age(entry.get("last_ts", 0.0))
    if not age:
        return "", ""

    runs = int(entry.get("runs", 0))
    duration = format_duration(entry.get("avg_ms", 0.0)) if runs > 1 else ""
    # Terse by design. "Ran 3 days ago" reads better in isolation but this
    # sits in a card footer beside the APPLIED chip, and every character
    # here is width the responsive grid has to find (see ElidedCaption).
    # The full sentence lives in the tooltip.
    text = age + (f" · ~{duration}" if duration else "")

    detail = [f"Last run {age}"]
    last_ms = entry.get("last_ms", 0.0)
    if last_ms:
        detail.append(f"took {format_duration(last_ms)}")
    if runs > 1:
        detail.append(f"{runs} runs recorded, averaging {duration}")
    if entry.get("outcome") == "err":
        detail.append("the last run reported an error")
    return text, " · ".join(detail)


def _derive_card_meta(item: dict) -> list[str]:
    """The count/hint pills a card shows in its v7 meta footer — derived
    from the item's own shape so the footer stays truthful without any
    hand-authored metadata. A hub reports how many options it holds; a
    selector reports its app count; the specialised launchers name their
    action. Plain one-shot actions return [] (no footer, no chevron)."""
    # Explicit override — the Welcome dashboard's module launchpad cards
    # pass their own 'N operations' label so they read as enterable modules
    # (pill + drill-in chevron) without being a hub/selector themselves.
    if item.get("meta_label"):
        return [item["meta_label"]]
    if item.get("hub"):
        subs = item.get("items")
        if not subs and item.get("groups"):
            subs = [s for g in item["groups"] for s in g.get("items", [])]
        n = len(subs or [])
        return [f"{n} options" if n != 1 else "1 option"]
    if item.get("apps"):
        n = len(item["apps"])
        return [f"{n} apps"]
    if item.get("devhub"):
        return ["Pick & deploy"]
    if item.get("update_center"):
        return ["Live scan"]
    if item.get("startup_manager"):
        return ["Audit & toggle"]
    if item.get("wizard"):
        return ["Guided setup"]
    return []


class ResponsiveGridHost(QWidget):
    """The widget a responsive card grid lives inside, which reports its
    own width changes.

    v10: column counts used to be derived from the PAGE's width minus a
    hand-tallied chrome constant, while the cards were actually laid out
    inside this host — whose width is the scroll VIEWPORT's and settles a
    layout pass later. Whenever the two disagreed (every frame of a live
    drag-resize, and on any page not yet shown) the grid was given a
    column count that did not fit the container, and cards were positioned
    past its right edge: measured at 974px window width, a 1719px-wide
    grid inside a 590px host.

    Driving the relayout from the host's OWN resizeEvent removes the
    disagreement by construction — the width used to choose the column
    count is, by definition, the width the cards are laid out in."""

    resized = Signal(int)   # new available content width

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        layout = self.layout()
        margins = layout.contentsMargins() if layout is not None else None
        chrome = (margins.left() + margins.right()) if margins else 0
        self.resized.emit(self.width() - chrome)


class ElidedCaption(QLabel):
    """A single-line caption that NEVER widens its parent.

    ClampedLabel solves the vertical version of this problem; this is the
    horizontal one, and it exists because of a regression measured while
    adding the v10.1 run-history pill: dropping a plain QLabel into the
    card footer took GlassCard.minimumSizeHint() from 184px to 337px once
    both it and the APPLIED chip were visible.

    That is the v9.1 density bug returning by another door. The footer was
    deliberately built so a card's minimum is the MAX of its rows and not
    their SUM — a plain QLabel breaks that, because QHBoxLayout adds every
    child's minimum width together, and the widest caption ("1y ago ·
    ~1.5h") therefore becomes a floor the responsive grid must honour on
    every card forever.

    Two mechanisms keep it honest:
      * a MINIMUM width of zero, so the layout is never obliged to find
        room for the text and the card's floor is unaffected, while the
        size policy stays Preferred so the caption is still granted its
        natural width whenever the row has room, and
      * elision to whatever width it actually receives, so a squeezed
        caption degrades to "1y ago…" rather than being clipped mid-glyph.

    The size policy is deliberately NOT Ignored. Ignored discards the
    sizeHint outright, which — next to the footer's trailing stretch —
    collapsed the caption to zero width and painted nothing at all, while
    still reporting isVisible() as True. That shipped past unit tests
    asserting on visibility and text; only a screenshot showed the cards
    were blank. test_history_pill_is_actually_painted now pins the width.

    The untruncated text always remains in the tooltip.
    """

    #: Never demand more than this, however long the caption gets.
    MAX_WIDTH = 120

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)

    def setFullText(self, text: str):
        self._full = text
        self._apply_elision()

    def fullText(self) -> str:
        return self._full

    def sizeHint(self):            # noqa: N802 - Qt casing
        hint = super().sizeHint()
        hint.setWidth(min(hint.width(), self.MAX_WIDTH))
        return hint

    def minimumSizeHint(self):     # noqa: N802 - Qt casing
        """The whole point: a caption is decoration and may collapse to
        nothing rather than force a card wider."""
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def resizeEvent(self, event):  # noqa: N802 - Qt casing
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self):
        if not self._full:
            super().setText("")
            return
        available = self.width()
        if available <= 0:
            super().setText(self._full)
            return
        super().setText(
            self.fontMetrics().elidedText(
                self._full, Qt.TextElideMode.ElideRight, available))


class ClampedLabel(QLabel):
    """A word-wrapped label with a HARD line budget.

    Why this exists: a plain wordWrap QLabel grows without limit, but
    GlassCard caps its own height (setMaximumHeight). The two disagreed
    silently — measured across the real catalog at a 3-column width, 14
    cards had their description cut off mid-sentence and 5 also lost part
    of their title; the worst (PATH Doctor) lost 88px, more than half its
    copy. Nothing warned; the text was simply painted outside the card's
    clip and vanished.

    This label instead lays the text out itself (QTextLayout, the same
    engine QLabel uses), keeps at most `max_lines` of it, elides the last
    kept line with an ellipsis, and puts the FULL text in the tooltip so
    nothing is ever unreachable. Its height is pinned to exactly
    max_lines * lineSpacing, so a card's height is now a deterministic
    function of its line budget rather than of how long someone's
    description happened to be.
    """

    def __init__(self, text: str = "", max_lines: int = 2,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWordWrap(True)
        self._max_lines = max(1, max_lines)
        self._full = text
        self._elided = False
        self._reflowing = False
        super().setText(text)

    # -- public API -------------------------------------------
    def setFullText(self, text: str):
        self._full = text
        self._reflow()

    def fullText(self) -> str:
        return self._full

    # -- layout -----------------------------------------------
    def _pin_height(self, lines: int | None = None):
        """Height = exactly the number of lines actually used, capped at
        the budget.

        The first cut of this reserved max_lines unconditionally, which
        made every short one-line blurb claim three lines of vertical
        space — enough to overflow the Welcome page's shorter Quick Action
        cards. The cap is a CEILING, not a quota: a 1-line description
        should occupy 1 line and let the card breathe."""
        used = self._max_lines if lines is None else max(1, min(lines, self._max_lines))
        target = QFontMetrics(self.font()).lineSpacing() * used
        if self.height() != target or self.minimumHeight() != target:
            self.setFixedHeight(target)

    def changeEvent(self, e):
        # Every label in this app takes its font-size from QSS, and QSS is
        # applied during POLISH — long after setStyleSheet() returns. Pinning
        # the height inside setStyleSheet therefore measured the widget's
        # pre-QSS default font and produced a budget for the wrong type size.
        # FontChange is the event Qt emits once the effective font (QSS
        # included) has actually resolved, so that is where the budget is
        # computed. StyleChange covers a live theme re-skin.
        super().changeEvent(e)
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._pin_height()
            self._reflow()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._reflow()

    def _reflow(self):
        width = self.width() - self.margin() * 2
        if width <= 0 or not self._full:
            return
        if self._reflowing:      # setFixedHeight below re-enters via resizeEvent
            return
        self._reflowing = True
        try:
            self._reflow_impl(width)
        finally:
            self._reflowing = False

    def _reflow_impl(self, width: int):
        layout = QTextLayout(self._full, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        starts: list[int] = []
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            starts.append(line.textStart())
        layout.endLayout()

        # height tracks the lines actually used, capped at the budget
        self._pin_height(len(starts))

        if len(starts) <= self._max_lines:
            if super().text() != self._full:
                super().setText(self._full)
            self._elided = False
            self.setToolTip("")
            return

        # Keep the whole prefix verbatim (so Qt re-wraps it identically),
        # then elide only what would have spilled past the budget.
        cut = starts[self._max_lines - 1]
        fm = QFontMetrics(self.font())
        tail = fm.elidedText(self._full[cut:], Qt.TextElideMode.ElideRight, width)
        super().setText(self._full[:cut] + tail)
        self._elided = True
        self.setToolTip(self._full)


class GlassCard(QFrame):
    clicked = Signal()
    # Arrow-key traversal request: "left" | "right" | "up" | "down". The
    # card knows a key was pressed but not where its neighbours are — the
    # page that owns the grid resolves that (see main._focus_neighbour).
    navigate = Signal(str)

    _ICON_BASE_PX = 21
    _ICON_GROW_PX = 2   # subtle hover "pop" — see _sync_icon_scale()
    _PLAQUE = 42        # icon plaque footprint (v9.1: tighter, denser card)

    # v10 height envelope, DERIVED from the card's anatomy rather than
    # guessed. With the header-row layout the arithmetic is:
    #   padding 12+12  +  plaque row 42  +  gap 8  +  desc 3x15  = 119
    #   ... plus the optional meta footer (gap 8 + pill 20)       = 147
    # Because ClampedLabel caps each block at an exact line count, 152 is a
    # ceiling the content genuinely cannot exceed — which is what makes a
    # maximum safe at all. (Pre-v10 the cap was 146 and content simply
    # overflowed it invisibly; the minimum was 112, itself below the 119 a
    # three-line description needs, so the minimum could clip too.)
    CARD_MIN_H = 120
    CARD_MAX_H = 152

    def __init__(self, item: dict, accent: str, t: dict,
                 featured: bool = False, locked: bool = False):
        super().__init__()
        self.item = item
        # v10: `accent` is a module KEY ("software") for category/dashboard
        # cards, or a literal hex when a dialog passes t["accent"] directly.
        # Both are stored unresolved and turned into a real colour inside
        # apply_theme() via theme.resolve_accent, so a card built under one
        # theme repaints correctly under the other.
        self._accent_key = accent
        self._accent = TH.resolve_accent(t, accent)
        self._danger = bool(item.get("danger"))
        self._featured = featured
        # v9.4: `locked` marks an admin-gated action shown on a non-elevated
        # Pulse — a small lock glyph in the head signals "needs Administrator"
        # up front (the click then opens the inline elevate prompt).
        self._locked = locked
        self._applied: bool | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # v10 ACCESSIBILITY: cards were QFrames with mouse handlers only —
        # no focus policy, no key handling — so the entire operation grid,
        # the app's primary surface, was unreachable by keyboard. Tab
        # stopped at the sidebar. StrongFocus puts every card in the tab
        # order; keyPressEvent below adds Enter/Space activation and arrow
        # traversal, and paintEvent draws a real focus ring.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(item.get("title", ""))
        self.setAccessibleDescription(item.get("desc", ""))
        # v8 proportion fix: a min AND a max so cards never balloon. The
        # equal-row-stretch grid (main.CategoryPage._relayout) still fills the
        # canvas, but a capped card can't grow into a tall, empty slab — it
        # settles at a natural height and the leftover space becomes balanced
        # inter-row breathing room, so a 4- or 5-card page reads evenly
        # distributed instead of either top-anchored-with-a-void or stretched.
        #
        # v8.1: featured and standard cards now share the SAME height bounds.
        # Giving the hero card a taller envelope made its grid row outgrow the
        # rows below it (and the rows of a hub-less category like System
        # Optimization), so transitioning between modules felt subtly off. The
        # featured card keeps its distinction through its squircle body +
        # Aurora edge, not extra size — every card in every section now shares
        # one height envelope, so rows lock to a single rhythm everywhere.
        # v9.1 density pass: a tighter height envelope (was 140/178) so cards
        # stop ballooning into empty slabs — content sits closer together and
        # reads denser, and the equal-row-stretch grid distributes the saved
        # space as clean breathing room between rows.
        # Only a MAXIMUM is set explicitly. An explicit setMinimumHeight
        # OVERRIDES minimumSizeHint(), so a fixed floor below what a
        # particular card's content needs (e.g. 120 on a footer card that
        # needs 147) silently let the layout squeeze it and clip the
        # content again. The floor is applied in minimumSizeHint() instead,
        # where it can be combined with — never override — the layout's own
        # requirement.
        self.setMaximumHeight(self.CARD_MAX_H)
        self.setProperty("running", False)

        glow_color = t["err"] if self._danger else self._accent
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

        # v10 CARD ANATOMY — a header row (plaque + title) above a
        # FULL-WIDTH description, replacing the old two-column "plaque to
        # the left of everything" arrangement.
        #
        # This is a measurement-driven change, not a restyle. In the old
        # layout the description shared a column with the title, so at the
        # 3-column grid width it was only ~256px wide — narrow enough that a
        # 48-character sentence ("Force the dark theme across Windows and
        # all apps.") already needed FOUR lines and got truncated. Dropping
        # the description onto its own row gives it the card's full inner
        # width (~312px at the same grid width, +22%), which is what finally
        # lets ordinary copy render complete.
        lay = QVBoxLayout(self)
        # symmetric, on-scale padding (was 15/13/16/13 — asymmetric by
        # accident, which is what made cards read subtly misaligned in a grid)
        lay.setContentsMargins(TH.SPACE["lg"], TH.SPACE["md"],
                               TH.SPACE["lg"], TH.SPACE["md"])
        lay.setSpacing(TH.SPACE["sm"])

        # -- icon plaque (v7) — a Fluent glyph in an accent-tinted well ----
        char, self._glyph_fluent = (
            TH.glyph(item["glyph"]) if item.get("glyph")
            else (item.get("icon", "•"), False))
        self._icon = QLabel(char)
        self._icon.setFixedSize(self._PLAQUE, self._PLAQUE)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Font is managed as a QFont object, not inline QSS: hover "pop" is
        # a handful of setFont() calls per hover-in (one per distinct integer
        # pixel size), never a per-frame setStyleSheet() rebuild — the exact
        # anti-pattern the animations.py doctrine forbids.
        base_font = TH.icon_font(self._ICON_BASE_PX) if self._glyph_fluent else QFont()
        self._icon_font = base_font if base_font is not None else QFont()
        self._icon_font.setPixelSize(self._ICON_BASE_PX)
        self._icon.setFont(self._icon_font)
        self._icon_px = self._ICON_BASE_PX

        # -- header row: plaque + title (+ chevron / lock) -----------------
        head = QHBoxLayout()
        head.setSpacing(TH.SPACE["md"])
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        # v10 line budget: title 2 lines, description 3. Both are
        # ClampedLabels, so a long string elides with its full text in the
        # tooltip instead of being painted outside the card.
        self._title = ClampedLabel(item["title"], max_lines=2)
        head.addWidget(self._title, 1)
        # v9.1 density fix: the note badge ('Windows 11 only') used to sit on
        # the TITLE's row, so a card's minimum width became plaque + title +
        # badge (~416px) — which forced the responsive grid to overflow once
        # cards were narrowed for a denser 3-column layout. Moving the badge
        # into the footer row means the card minimum is the MAX of its rows,
        # not their SUM, so dense columns fit cleanly — and a small pill in
        # the bottom-right reads more premium than a chip crowding the title.
        self._badge: QLabel | None = None
        if item.get("note"):
            self._badge = QLabel(item["note"])
        # Drill-in chevron — shown only for cards that open a further screen
        # (hubs / selectors), i.e. exactly the cards that have a meta footer.
        self._meta_texts = _derive_card_meta(item)
        self._chevron: QLabel | None = None
        if self._meta_texts:
            self._chevron = QLabel(TH.glyph("chevron")[0])
            cf = TH.icon_font(15) if TH.glyph("chevron")[1] else QFont()
            if cf is not None:
                cf.setPixelSize(15)
                self._chevron.setFont(cf)
            head.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        # admin-gated lock indicator (v9.4): a quiet warn-tinted lock glyph
        # pinned to the head's right edge when this card needs elevation the
        # current session doesn't have.
        self._lock: QLabel | None = None
        if self._locked:
            lock_char, lock_fluent = TH.glyph("lock")
            self._lock = QLabel(lock_char)
            lf = TH.icon_font(13) if lock_fluent else QFont()
            if lf is not None:
                lf.setPixelSize(13)
                self._lock.setFont(lf)
            self._lock.setToolTip(
                "Needs Administrator — clicking will offer to relaunch Pulse elevated.")
            head.addWidget(self._lock, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        # -- description: its own FULL-WIDTH row (the v10 change) ----------
        # A uniform 3-line budget. Combined with the full card width this
        # lets the tightened catalog copy render complete on every card at
        # every column count, with elision left as a pure safety net.
        self._desc = ClampedLabel(item["desc"], max_lines=3)
        lay.addWidget(self._desc)
        lay.addStretch()

        # -- meta footer (v7) — count/hint pills fill the card with signal,
        #    plus the relocated note badge pinned bottom-right (v9.1) --------
        # v10: the footer now ALWAYS exists, because the applied-state chip
        # can appear on any card once the backend probe reports on it. It
        # stays zero-height until something needs it (the chip and badge
        # both start hidden), so a plain action card is unchanged visually.
        self._meta_pills: list[QLabel] = []
        self._applied_chip = QLabel("APPLIED")
        self._applied_chip.hide()
        # v10.1 run history ("Ran 3d ago · ~2m"). Starts hidden and stays
        # hidden until this task has actually been run, so a fresh install
        # looks exactly as it did before rather than showing a row of
        # empty placeholders.
        self._history_pill = ElidedCaption()
        self._history_pill.hide()
        foot = QHBoxLayout()
        foot.setSpacing(TH.SPACE["sm"])
        for text in self._meta_texts:
            pill = QLabel(text)
            self._meta_pills.append(pill)
            foot.addWidget(pill)
        foot.addWidget(self._applied_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        foot.addWidget(self._history_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        foot.addStretch()
        if self._badge is not None:
            foot.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(foot)

        self.apply_theme(t)

    # -- theming ----------------------------------------------
    def apply_theme(self, t: dict):
        # re-resolve first: the module palette differs per theme (v10)
        self._accent = TH.resolve_accent(t, self._accent_key)
        self.setStyleSheet(TH.card_qss(t, self._accent, self._danger, self._featured))
        plaque_accent = t["err"] if self._danger else self._accent
        self._icon.setStyleSheet(TH.icon_plaque_qss(t, plaque_accent, self._featured))
        self._title.setStyleSheet(TH.label_qss(t, "card"))
        self._desc.setStyleSheet(TH.label_qss(t, "desc"))
        if self._badge is not None:
            self._badge.setStyleSheet(TH.badge_qss(t))
        if self._chevron is not None:
            self._chevron.setStyleSheet(TH.card_chevron_qss(t, self._accent))
        if self._lock is not None:
            self._lock.setStyleSheet(
                f"color: {t['warn']}; background: transparent; border: none;")
        for i, pill in enumerate(self._meta_pills):
            # the lead pill on the featured card carries the accent tint
            tint = plaque_accent if (self._featured and i == 0) else ""
            pill.setStyleSheet(TH.card_meta_pill_qss(t, tint))
        self._applied_chip.setStyleSheet(TH.applied_chip_qss(t))
        self._history_pill.setStyleSheet(TH.card_history_pill_qss(t))
        self._glow.set_accent(plaque_accent)
        # painted-material state, read in paintEvent
        self._bevel = TH.bevel_alphas(t)
        self._feat_base = TH.to_qcolor(t["card_hi"])
        self._feat_sheen = TH.to_qcolor(t["card_sheen"])
        self._aur1 = QColor(t["accent"])
        self._aur2 = QColor(t["accent2"])
        self._aur3 = QColor(t["accent3"])

    def set_applied(self, applied: bool | None):
        """Reflect the backend's read-only applied-state probe.

        Three states, deliberately: True shows the chip, False hides it,
        and None (unknown — unreadable key, unsupported Windows build, or
        a task the probe doesn't cover) ALSO hides it. Never guess: a card
        with no chip means "we're not claiming anything", which is honest,
        whereas a wrong 'Applied' badge would actively mislead someone into
        skipping a tweak they still need."""
        self._applied = applied
        show = applied is True
        self._applied_chip.setVisible(show)
        self._applied_chip.setToolTip(
            "This setting is currently active on your system." if show else "")

    def set_history(self, entry: dict | None):
        """Show this task's last-run caption, or nothing at all.

        Same honesty rule as set_applied(): with no record, the card says
        nothing rather than guessing or showing a "never run" placeholder
        — a card the user ran outside Pulse, or before this feature
        existed, genuinely has no history for us to report.
        """
        text, tooltip = format_history_caption(entry)
        self._history_pill.setFullText(text)
        self._history_pill.setToolTip(tooltip)
        self._history_pill.setVisible(bool(text))

    def minimumSizeHint(self):     # noqa: N802 - Qt casing
        """The card's real floor: whatever its content needs, but never
        squatter than CARD_MIN_H. Combining here (rather than via
        setMinimumHeight) means the aesthetic floor can never win over a
        content requirement — it can only raise a short card, never crush
        a tall one."""
        hint = super().minimumSizeHint()
        hint.setHeight(max(hint.height(), self.CARD_MIN_H))
        return hint

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

    _NAV_KEYS = {
        Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
        Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
    }

    def keyPressEvent(self, e):
        key = e.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            # Same feedback a click gets, so activating from the keyboard
            # feels like the same action rather than a silent shortcut.
            self._ramp_press(1.0)
            self._ripple.trigger(QPointF(self.rect().center()))
            QTimer.singleShot(90, lambda: self._ramp_press(0.0))
            self.clicked.emit()
            return
        direction = self._NAV_KEYS.get(key)
        if direction is not None:
            self.navigate.emit(direction)
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        # Keyboard focus lights the same glow the pointer does, so the two
        # input methods produce one consistent "this is active" state.
        self._glow._ramp_to(1.0)
        self.update()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if not self.underMouse():
            self._glow._ramp_to(0.0)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
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

    def _paint_featured(self, p: QPainter):
        """The hero card's fully-painted material: a squircle (continuous-
        corner) glass surface on the top elevation tier, a hover-reactive
        accent wash, and the signature Aurora lit edge. Only ever a hub card,
        so no running/flash QSS state is lost by owning the background."""
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = squircle_path(self.rect().adjusted(1, 1, -1, -1), 20)
        # frosted-glass fill: top sheen falling into the card_hi base
        grad = QLinearGradient(self.rect().topLeft(), self.rect().bottomLeft())
        grad.setColorAt(0.0, self._feat_sheen)
        grad.setColorAt(0.16, self._feat_base)
        grad.setColorAt(1.0, self._feat_base)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(path)
        # hover wash — reuses the already-running glow intensity, no new anim
        if self._glow.intensity > 0.01:
            wash = QColor(self._glow.color)
            wash.setAlphaF(0.07 * self._glow.intensity)
            p.setBrush(wash)
            p.drawPath(path)
        paint_aurora_edge(p, path, self._aur1, self._aur2, self._aur3,
                          width=1.6, intensity=0.95)

    def paintEvent(self, e):
        super().paintEvent(e)  # QSS glass background/border first (transparent if featured)
        self._sync_icon_scale()
        p = QPainter(self)
        if self._featured:
            self._paint_featured(p)
        else:
            paint_bevel_frame(p, self.rect(), 16, *self._bevel)
            paint_top_sheen(p, self.rect(), 16, strength=0.55)
        if self._press_tint > 0.01:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(40 * self._press_tint)))
            p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 15, 15)
        paint_ripple_frame(p, self.rect(), 16, self._glow.color,
                           self._ripple.progress, self._ripple.origin)
        paint_glow_frame(p, self.rect(), 16, self._glow.color,
                         self._glow.intensity, self._glow.cursor)
        # Keyboard focus ring — painted LAST so it sits above the hover
        # glow and stays unambiguous even on a card the pointer is also
        # over. A solid 2px accent ring rather than Qt's dotted default,
        # which is invisible against this material.
        if self.hasFocus():
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setBrush(Qt.BrushStyle.NoBrush)
            ring = QColor(self._glow.color)
            ring.setAlphaF(0.95)
            p.setPen(QPen(ring, 2.0))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5),
                              15, 15)
        p.end()


# ============================================================
#  AMBIENT GLOW — static brand-pair light wash behind the shell
# ============================================================
class AmbientGlow(QWidget):
    """A LIVING canvas behind the sidebar/content frames (lowest widget in
    the shell's z-order, transparent to mouse events).

    Two motion layers, both engineered to stay cheap:

    1. Aurora orbs — three large, soft brand-tinted radial blobs (indigo /
       violet / magenta) that slowly DRIFT on independent sine paths and
       BREATHE (a gentle opacity pulse). Each orb is a radial-gradient
       PIXMAP rendered once and cached, then blitted at its drifting
       position every frame — a GPU-friendly blit, not a per-frame gradient
       rasterization, so the animation costs microseconds even full-screen.
    2. Particle field — a scatter of tiny soft 'stars' drifting slowly
       upward and twinkling, wrapping around the top. ~40 small ellipses a
       frame, negligible.

    Driven by one QTimer at ~28 fps (slow drift stays perfectly smooth at
    that rate) that suspends whenever the widget is hidden, so a minimized
    or backgrounded window pays nothing. Opacities stay low (theme.py
    documents why the brand pair reads neon past ~0.16) — this is ambient
    luminescence, never a light show."""

    _INTERVAL_MS = 36          # ~28 fps — slow motion reads smooth, CPU low
    _N_PARTICLES = 42

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._c1 = QColor("#7d9bff")
        self._c2 = QColor("#a184ff")
        self._c3 = QColor("#e784ff")
        self._light = False
        self._radius = 24   # must track shell_qss's floating corner radius
        self._t = 0.0
        # v9.5: paused while the window is minimized — hideEvent doesn't fire on
        # minimize (Qt keeps children "visible"), so the loop would otherwise
        # keep ticking at ~28fps behind a minimized window. Driven by
        # suspend()/resume() from PulseApp.changeEvent.
        self._suspended = False
        self._orb_cache: dict = {}
        # Composited orb layer — see _ensure_layer. Exactly ONE pixmap is
        # ever retained (never a dict keyed on size), which is what keeps
        # this from repeating the old resize memory leak.
        self._layer: QPixmap | None = None
        self._layer_t = -1e9
        self._layer_size = (0, 0)
        self._frozen = False
        self._particles: list[dict] = []
        self._build_particles()

        # Independent drift/breathe parameters per orb: (base_x_frac,
        # base_y_frac, drift_speed, drift_phase, breathe_speed, breathe_phase)
        self._orb_motion = [
            (0.16, -0.06, 0.055, 0.0, 0.42, 0.0),
            (1.02,  0.28, 0.041, 2.1, 0.37, 1.3),
            (0.70,  1.06, 0.048, 4.0, 0.31, 3.4),
        ]

        self._timer = QTimer(self)
        self._timer.setInterval(self._INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    # -- particle field ---------------------------------------
    def _build_particles(self):
        rng = random.Random(7)   # fixed seed → stable, reproducible scatter
        self._particles = []
        for _ in range(self._N_PARTICLES):
            self._particles.append({
                "x": rng.random(),
                "y": rng.random(),
                "r": rng.uniform(0.7, 2.0),
                "spd": rng.uniform(0.008, 0.028),   # frac of height / second, upward
                "tw": rng.random() * math.tau,       # twinkle phase
                "tws": rng.uniform(0.6, 1.5),        # twinkle speed
            })

    # -- theming ----------------------------------------------
    def apply_theme(self, t: dict):
        self._c1 = QColor(t["accent"])
        self._c2 = QColor(t["accent2"])
        self._c3 = QColor(t["accent3"])
        self._light = t["name"] == "light"
        self._orb_cache.clear()   # colors changed — cached orb pixmaps stale
        self._layer = None        # ...and so is the composited orb layer
        self.update()

    def set_radius(self, radius: int):
        """Match the shell's corner radius. Now always 0: the shell is an
        opaque square canvas and DWM rounds the window itself, so there is
        no rounded edge for this wash to bleed past. (Kept as a setter
        rather than deleted — it still guards against painting into a
        rounded corner if the shell ever regains one.)"""
        if radius != self._radius:
            self._radius = radius
            self.update()

    # -- lifecycle: animate only while visible AND not minimized --------
    def showEvent(self, e):
        super().showEvent(e)
        if not self._suspended:
            self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def suspend(self):
        """Pause the animation while the window is minimized, or for the
        duration of an OS move/resize loop (PulseApp.changeEvent and the
        WM_ENTERSIZEMOVE handler both call this).

        Also FREEZES the composited orb layer: during a resize the widget
        is a different size on every step, which would otherwise invalidate
        the cache and rebuild a full-window layer per step — the most
        expensive thing possible in the middle of a drag. While frozen the
        existing layer is simply stretched to fit (see paintEvent); it is a
        soft gradient, so scaling it is visually free, and the correct
        layer is rebuilt once on resume."""
        self._suspended = True
        self._frozen = True
        self._timer.stop()

    def resume(self):
        """Resume after restore. No-ops while the widget is hidden (the next
        showEvent will start it) so we never animate an off-screen surface."""
        self._suspended = False
        if self._frozen:
            self._frozen = False
            self._layer = None      # rebuild once, at the final size
            self.update()
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        dt = self._INTERVAL_MS / 1000.0
        self._t += dt
        for pt in self._particles:
            pt["y"] -= pt["spd"] * dt
            if pt["y"] < -0.03:
                pt["y"] += 1.06   # wrap back to just below the bottom edge
        self.update()

    # -- orb pixmap cache -------------------------------------
    # v10: orbs are rendered ONCE at a fixed texture size and SCALED on
    # blit, instead of being re-rasterised at the window's current size.
    #
    # The old cache was keyed on `diameter = max(w, h) * 1.25`, i.e. on the
    # window size — so every single pixel of a drag-resize minted three
    # fresh ~1800x1800 pixmaps and kept them forever. Measured on a plain
    # 1000->1440px drag: 1,323 cached pixmaps totalling 11.9 GB, at 34.9 ms
    # per resize step. That was both the resize stutter and an unbounded
    # memory leak keyed on how much the user dragged.
    #
    # A radial-gradient blob is smooth by construction, so scaling one up
    # is visually identical to rasterising it at full size (the painter
    # already runs with SmoothPixmapTransform). The cache is now keyed only
    # on (colour, peak): at most a handful of entries, a few MB, forever.
    _ORB_TEX = 512

    def _orb_pixmap(self, color: QColor, peak: float) -> QPixmap:
        key = (color.rgb(), round(peak * 1000))
        pm = self._orb_cache.get(key)
        if pm is not None:
            return pm
        diameter = self._ORB_TEX
        pm = QPixmap(diameter, diameter)
        pm.fill(Qt.GlobalColor.transparent)
        pp = QPainter(pm)
        pp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QRadialGradient(diameter / 2.0, diameter / 2.0, diameter / 2.0)
        c = QColor(color)
        c.setAlphaF(peak)
        grad.setColorAt(0.0, c)
        # a soft, wide falloff — most of the gradient is the tail, so orbs
        # blend seamlessly into the canvas with no visible hard rim
        mid = QColor(color)
        mid.setAlphaF(peak * 0.35)
        grad.setColorAt(0.45, mid)
        c_out = QColor(color)
        c_out.setAlphaF(0.0)
        grad.setColorAt(1.0, c_out)
        pp.setPen(Qt.PenStyle.NoPen)
        pp.setBrush(grad)
        pp.drawEllipse(0, 0, diameter, diameter)
        pp.end()
        self._orb_cache[key] = pm
        return pm

    # -- composited orb layer ---------------------------------
    # The three orbs are drawn ONCE into a widget-sized layer and then
    # blitted as a single pixmap, instead of three smooth-scaled blits per
    # frame. This matters because the glow is repainted far more often than
    # its own 28fps timer asks: it is the bottom widget in the shell, so
    # every animation above it (the two BreathingIcons, ~60fps each) forces
    # a partial repaint underneath. Measured at idle: 3.55 paintEvents per
    # timer tick, ~76/s, totalling 26 full-widget repaints per second.
    #
    # Cost per paint drops ~9x (2.70ms -> 0.29ms dark, 4.29 -> 0.61 light).
    # The layer is rebuilt at _LAYER_MS, not per frame; the orbs drift so
    # slowly that the largest possible step between rebuilds is ~3px on a
    # blob with a ~500px falloff. Verified against the old direct path:
    # maximum channel difference 2/255.
    _LAYER_MS = 100

    def _ensure_layer(self) -> QPixmap | None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        if self._frozen and self._layer is not None:
            return self._layer          # mid-drag: stretch, never rebuild
        stale = (self._layer is None
                 or self._layer_size != (w, h)
                 or (self._t - self._layer_t) * 1000.0 >= self._LAYER_MS)
        if stale:
            self._layer = self._build_layer(w, h)
            self._layer_t = self._t
            self._layer_size = (w, h)
        return self._layer

    def _build_layer(self, w: int, h: int) -> QPixmap:
        """Composite the three drifting/breathing orbs into one transparent
        pixmap. Orb blending among themselves stays SourceOver exactly as
        before; the light-mode Multiply is applied when the finished layer
        is blitted onto the canvas (see paintEvent)."""
        diameter = int(max(w, h) * 1.25)
        layer = QPixmap(w, h)
        layer.fill(Qt.GlobalColor.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        # v10: pulled well back in light mode (was 0.30/0.27/0.24). Those
        # peaks were tuned against the older, lighter porcelain canvas; once
        # the elevation pass deepened the canvas gradient so cards could
        # actually float, the same multiply strength turned the whole page a
        # hazy lavender. The wash should tint the paper, not dye it.
        peaks = (0.16, 0.14, 0.12) if self._light else (0.17, 0.12, 0.11)
        colors = (self._c1, self._c3, self._c2)   # indigo, magenta, violet
        amp_x, amp_y = w * 0.06, h * 0.06
        for i, (bx, by, dspd, dph, bspd, bph) in enumerate(self._orb_motion):
            dx = math.sin(self._t * dspd * math.tau + dph) * amp_x
            dy = math.cos(self._t * dspd * math.tau * 0.8 + dph) * amp_y
            cx = bx * w + dx - diameter / 2.0
            cy = by * h + dy - diameter / 2.0
            breathe = 1.0 + 0.16 * math.sin(self._t * bspd * math.tau + bph)
            p.setOpacity(max(0.0, min(1.0, breathe)))
            p.drawPixmap(QRect(int(cx), int(cy), diameter, diameter),
                         self._orb_pixmap(colors[i], peaks[i]))
        p.end()
        return layer

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._radius:
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
            p.setClipPath(path)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            p.end()
            return

        # --- aurora orbs: one cached composite ---------------------------
        # Per-theme visibility is the whole game here. On the DEEP-SPACE dark
        # canvas a light-colored orb ADDS light (normal SourceOver) and reads
        # instantly. On the PORCELAIN light canvas that same additive light
        # orb is invisible — lightening near-white does nothing — so light
        # mode switches to a MULTIPLY blend: the saturated brand orbs now
        # DARKEN the porcelain into soft, clearly-visible drifting colored
        # clouds (dusty indigo / rose / violet). Same motion, opposite blend,
        # visible in both worlds.
        layer = self._ensure_layer()
        if layer is not None:
            if self._light:
                p.setCompositionMode(
                    QPainter.CompositionMode.CompositionMode_Multiply)
            if self._layer_size == (w, h):
                p.drawPixmap(0, 0, layer)
            else:
                # frozen mid-resize — stretch the last good layer to fit
                p.drawPixmap(QRect(0, 0, w, h), layer)
            p.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setOpacity(1.0)

        # --- particle field: slow upward drift + twinkle -----------------
        if self._light:
            base = QColor(38, 50, 120)     # deep indigo motes, clearly readable
            pmax = 0.34                     # on porcelain
        else:
            base = QColor(200, 214, 255)   # cool starlight on deep space
            pmax = 0.34
        p.setPen(Qt.PenStyle.NoPen)
        # Most repaints here are small regions dirtied by the animations
        # sitting above this widget, so skip the motes outside them — Qt
        # would clip the drawing anyway, but not the per-particle QColor
        # construction and trig that precede it.
        dirty = e.rect().adjusted(-3, -3, 3, 3)
        for pt in self._particles:
            x, y = pt["x"] * w, pt["y"] * h
            if not dirty.contains(int(x), int(y)):
                continue
            tw = 0.5 + 0.5 * math.sin(self._t * pt["tws"] * math.tau + pt["tw"])
            col = QColor(base)
            col.setAlphaF(pmax * (0.25 + 0.75 * tw))
            p.setBrush(col)
            r = pt["r"] * (0.7 + 0.5 * tw)
            p.drawEllipse(QPointF(x, y), r, r)
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
        head.setStyleSheet(TH.label_qss(t, "dialog"))
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


class PlaybookStepRow(QFrame):
    """One step inside PlaybookDialog, with a live status lamp.

    The lamp is text, not colour alone: "colour = state" fails for the
    ~8% of men with a red/green deficiency, and this list is the only
    place the user learns which step of an unattended run went wrong.
    """

    LAMPS = {
        "pending":   ("○", "text_faint"),
        "running":   ("◐", "accent"),
        "ok":        ("✓", "ok"),
        "error":     ("✕", "err"),
        "skipped":   ("–", "text_faint"),
        "cancelled": ("■", "warn"),
    }

    def __init__(self, index: int, step, t: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._t = t
        self._state = "pending"
        self.setObjectName("playbookStep")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)

        self._lamp = QLabel()
        self._lamp.setFixedWidth(16)
        self._lamp.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._lamp, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self._title = QLabel(f"{index + 1}.  {step.title}")
        text_col.addWidget(self._title)
        self._detail = QLabel(step.note or step.task)
        self._detail.setWordWrap(True)
        text_col.addWidget(self._detail)
        lay.addLayout(text_col, 1)

        self._tag = QLabel("optional" if step.optional else "")
        self._tag.setVisible(bool(step.optional))
        lay.addWidget(self._tag, 0, Qt.AlignmentFlag.AlignTop)

        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self._t = t
        self.setStyleSheet(
            f"#playbookStep {{ background: transparent; border: none; "
            f"border-bottom: 1px solid {t['panel_line']}; }}")
        self._title.setStyleSheet(TH.label_qss(t, "card"))
        self._detail.setStyleSheet(TH.label_qss(t, "caption"))
        self._tag.setStyleSheet(TH.card_meta_pill_qss(t))
        self.set_state(self._state, self._detail.text())

    def set_state(self, state: str, detail: str | None = None):
        self._state = state
        glyph, token = self.LAMPS.get(state, self.LAMPS["pending"])
        self._lamp.setText(glyph)
        self._lamp.setStyleSheet(
            f"color: {self._t[token]}; font-size: 13px; font-weight: 700;"
            "background: transparent; border: none;")
        if detail is not None:
            self._detail.setText(detail)


class PlaybookDialog(PulseDialog):
    """Browse, preview and run declarative playbooks (v10.3).

    ONE dialog with two modes rather than a chooser plus a progress
    window. A playbook run is not a fire-and-forget action — the user
    wants to watch which step is executing and read what happened
    afterwards — so the step list they picked from becomes the step list
    they watch, in place. Nothing moves under the cursor at the moment the
    run starts, which is exactly when it would be most disorienting.

    PREVIEW IS THE DEFAULT-ADJACENT ACTION. Every step runs through the
    engine's -WhatIf path, so "Preview" answers "what would this do to my
    machine" with zero mutations. It is offered first and styled as the
    safe button; Run carries the accent.
    """

    #: Emitted when the user presses Stop during a live run.
    stop_requested = Signal()

    def __init__(self, parent: QWidget, playbooks: list, errors: list[str],
                 t: dict, is_admin: bool = True):
        super().__init__(parent)
        self._t = t
        self._playbooks = playbooks
        self._is_admin = is_admin
        self._rows: list[PlaybookStepRow] = []
        self._current = playbooks[0] if playbooks else None

        #: Set when the user asks to run; read by the caller.
        self.chosen: object | None = None
        self.dry_run = False
        #: True between enter_run_mode and enter_done_mode. While set, the
        #: dialog refuses to be dismissed — see reject().
        self._run_locked = False

        accent = TH.resolve_accent(t, "automation")
        self._accent = accent
        panel = _dialog_chrome(self, t, accent, responsive=True)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QLabel("📘  Playbooks")
        head.setStyleSheet(TH.label_qss(t, "dialog"))
        lay.addWidget(head)

        blurb = QLabel(
            "Ordered task sequences that run through the normal engine. "
            "Preview simulates every step with -WhatIf and changes nothing.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(blurb)

        if errors:
            # A malformed playbook is reported, never silently dropped —
            # a technician who mistyped a task name must find out now.
            warn = QLabel("⚠️  " + "  ·  ".join(errors[:3]))
            warn.setWordWrap(True)
            warn.setStyleSheet(
                f"color: {t['warn']}; font-size: 11px; background: transparent;"
                "border: none;")
            lay.addWidget(warn)

        if not playbooks:
            empty = QLabel(
                "No playbooks found. Drop a .json file into the "
                "'playbooks' folder next to Pulse to add one.")
            empty.setWordWrap(True)
            empty.setStyleSheet(TH.label_qss(t, "body"))
            lay.addWidget(empty)
            lay.addStretch()
            self._build_buttons(lay, t, runnable=False)
            return

        # -- playbook picker: a row of pills ---------------------------
        picker = QHBoxLayout()
        picker.setSpacing(8)
        self._pills: list[QPushButton] = []
        for playbook in playbooks:
            pill = QPushButton(f"{playbook.icon}  {playbook.name}")
            pill.setCursor(Qt.CursorShape.PointingHandCursor)
            pill.setCheckable(True)
            pill.clicked.connect(
                lambda _checked, p=playbook: self._select(p))
            self._pills.append(pill)
            picker.addWidget(pill)
        picker.addStretch()
        lay.addLayout(picker)

        self._summary = QLabel()
        self._summary.setWordWrap(True)
        lay.addWidget(self._summary)

        # -- step list -------------------------------------------------
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(TH.scroll_area_qss(t))
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 6, 0)
        self._host_lay.setSpacing(0)
        self._host_lay.addStretch()
        self._scroll.setWidget(self._host)
        lay.addWidget(self._scroll, 1)

        self._status = QLabel()
        self._status.setWordWrap(True)
        lay.addWidget(self._status)
        self.set_status("")

        self._build_buttons(lay, t, runnable=True)
        self._select(self._current)

    # -- construction helpers ---------------------------------
    def _build_buttons(self, lay: QVBoxLayout, t: dict, runnable: bool):
        row = QHBoxLayout()
        row.addStretch()

        self._close_btn = QPushButton("Close")
        self._close_btn.setFixedSize(96, 36)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(TH.dialog_cancel_qss(t))
        self._close_btn.clicked.connect(self.reject)
        row.addWidget(self._close_btn)

        self._preview_btn = QPushButton("Preview")
        self._preview_btn.setFixedSize(112, 36)
        self._preview_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._preview_btn.setStyleSheet(TH.dialog_cancel_qss(t))
        self._preview_btn.setToolTip(
            "Run every step with -WhatIf: reports what would happen and "
            "changes nothing.")
        self._preview_btn.clicked.connect(lambda: self._launch(dry_run=True))
        self._preview_btn.setVisible(runnable)
        row.addWidget(self._preview_btn)

        self._run_btn = QPushButton("Run Playbook")
        self._run_btn.setFixedSize(132, 36)
        self._run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._run_btn.setStyleSheet(TH.dialog_go_qss(t, self._accent))
        self._run_btn.clicked.connect(lambda: self._launch(dry_run=False))
        self._run_btn.setVisible(runnable)
        row.addWidget(self._run_btn)

        lay.addLayout(row)

    def _select(self, playbook):
        self._current = playbook
        t = self._t
        for pill, candidate in zip(self._pills, self._playbooks):
            active = candidate is playbook
            pill.setChecked(active)
            pill.setStyleSheet(
                TH.dialog_go_qss(t, self._accent) if active
                else TH.dialog_cancel_qss(t))

        admin_note = ""
        if playbook.needs_admin and not self._is_admin:
            admin_note = ("  ·  ⚠️ needs Administrator — some steps will be "
                          "refused in this session")
        self._summary.setText(
            f"{playbook.description}\n{len(playbook)} steps{admin_note}")
        self._summary.setStyleSheet(TH.label_qss(t, "body"))

        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        for index, step in enumerate(playbook.steps):
            row = PlaybookStepRow(index, step, t)
            self._rows.append(row)
            self._host_lay.insertWidget(self._host_lay.count() - 1, row)
        self._status.setText("")

    def _launch(self, dry_run: bool):
        self.chosen = self._current
        self.dry_run = dry_run
        self.accept()

    # -- live run API (driven by PlaybookRunner) --------------
    def enter_run_mode(self, dry_run: bool):
        """Switch the browse UI into a progress view in place."""
        self._run_locked = True
        for pill in self._pills:
            pill.setEnabled(False)
        self._preview_btn.setEnabled(False)
        self._run_btn.setText("Stop")
        self._run_btn.setStyleSheet(TH.dialog_go_qss(self._t, self._t["err"]))
        try:
            self._run_btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._run_btn.clicked.connect(self.stop_requested.emit)
        self._close_btn.setEnabled(False)
        prefix = "Previewing" if dry_run else "Running"
        self.set_status(f"{prefix} {self._current.name}…")

    def mark_step(self, index: int, state: str, detail: str | None = None):
        if 0 <= index < len(self._rows):
            self._rows[index].set_state(state, detail)

    def set_status(self, text: str, kind: str = "info"):
        colour = {"ok": self._t["ok"], "error": self._t["err"],
                  "warn": self._t["warn"]}.get(kind, self._t["text_muted"])
        self._status.setText(text)
        self._status.setStyleSheet(
            f"color: {colour}; font-size: 11px; font-weight: 600;"
            "background: transparent; border: none;")
        # Hidden while empty: the dialog's panel QSS gives a bare QLabel a
        # frame, so an empty status line painted as a stray input box
        # sitting above the buttons.
        self._status.setVisible(bool(text))

    def reject(self):
        """Refuse dismissal while a run is live.

        Disabling the Close BUTTON was never enough: PulseDialog also
        dismisses on Escape (QDialog's default) and on a click anywhere on
        the scrim, and the app's native caption-close path rejects every
        open dialog before closing the window. Any of those detached the
        dialog from a PlaybookRunner that kept going — so a sequence of
        machine-wide changes carried on with its progress view gone and no
        way to reach the Stop button.

        The run is stoppable, not un-abandonable: Stop is right there, and
        the window's own close guard now sees the playbook too.
        """
        if self._run_locked:
            self.set_status(
                "This playbook is still running — press Stop to end it, "
                "or let it finish.", "warn")
            return
        super().reject()

    def force_close(self):
        """Dismiss regardless of the run lock.

        The one legitimate override: the app itself is shutting down and
        has already cancelled the runner, so this dialog's exec() loop has
        to unwind or it would outlive the window it is parented to.
        """
        self._run_locked = False
        super().reject()

    def enter_done_mode(self):
        self._run_locked = False
        self._close_btn.setEnabled(True)
        self._run_btn.setText("Close")
        self._run_btn.setStyleSheet(TH.dialog_go_qss(self._t, self._accent))
        try:
            self._run_btn.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._run_btn.clicked.connect(self.reject)

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


class HealthReportDialog(PulseDialog):
    """The Health & Drift Report (v10.3): run the probe, read it, export it.

    Runs its own PowerShellTask rather than borrowing the main window's,
    for the same reason StartupManagerDialog and UpdateCenterDialog do —
    it is a self-contained panel that never hands anything back, so
    entangling it with the shell's single-task pipeline would let opening
    a read-only report block a real operation.

    Export writes a self-contained HTML file (client deliverable) or the
    raw JSON (diffable between two runs). Both come from
    frontend.health_report, which is pure and tested separately.
    """

    def __init__(self, parent: QWidget, ps1_path: str, t: dict):
        super().__init__(parent)
        self._t = t
        self._ps1 = ps1_path
        self._report: dict | None = None
        self._thread: QThread | None = None
        self._worker: PowerShellTask | None = None

        accent = TH.resolve_accent(t, "automation")
        self._accent = accent
        panel = _dialog_chrome(self, t, accent, responsive=True)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QLabel("🩺  Health & Drift Report")
        head.setStyleSheet(TH.label_qss(t, "dialog"))
        lay.addWidget(head)

        self._status = QLabel("Reading system state…")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(self._status)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(TH.scroll_area_qss(t))
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 6, 0)
        self._host_lay.setSpacing(6)
        self._host_lay.addStretch()
        self._scroll.setWidget(self._host)
        lay.addWidget(self._scroll, 1)

        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Close")
        close.setFixedSize(96, 36)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(TH.dialog_cancel_qss(t))
        close.clicked.connect(self.reject)
        row.addWidget(close)

        self._json_btn = QPushButton("Export JSON")
        self._json_btn.setFixedSize(122, 36)
        self._json_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._json_btn.setStyleSheet(TH.dialog_cancel_qss(t))
        self._json_btn.setEnabled(False)
        self._json_btn.clicked.connect(lambda: self._export("json"))
        row.addWidget(self._json_btn)

        self._html_btn = QPushButton("Export HTML")
        self._html_btn.setFixedSize(128, 36)
        self._html_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._html_btn.setStyleSheet(TH.dialog_go_qss(t, accent))
        self._html_btn.setEnabled(False)
        self._html_btn.clicked.connect(lambda: self._export("html"))
        row.addWidget(self._html_btn)
        lay.addLayout(row)

        QTimer.singleShot(0, self._start)

    # -- data -------------------------------------------------
    def _start(self):
        if not self._ps1:
            self._status.setText("Engine unavailable — core.ps1 was not found.")
            return
        thread = QThread(self)
        worker = PowerShellTask(self._ps1, "HealthReport", timeout=180)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_report)
        worker.failed.connect(self._on_failed)
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(thread.quit)
        thread.finished.connect(self._cleanup)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_report(self, result: TaskResult):
        if not result.success or not isinstance(result.data, dict):
            self._on_failed(result.message or "The report could not be read.")
            return
        self._report = result.data
        self._render(result.data)
        self._json_btn.setEnabled(True)
        self._html_btn.setEnabled(True)

    def _on_failed(self, message: str):
        self._status.setText(f"Could not generate the report: {message}")

    def _cleanup(self):
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    # -- rendering --------------------------------------------
    def _row(self, label: str, value: str, tone: str = "") -> QWidget:
        t = self._t
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 2, 0, 2)
        line.setSpacing(10)
        left = QLabel(label)
        left.setStyleSheet(TH.label_qss(t, "caption"))
        left.setMinimumWidth(210)
        line.addWidget(left, 0)
        right = QLabel(value)
        right.setWordWrap(True)
        colour = {"ok": t["ok"], "err": t["err"], "warn": t["warn"]}.get(
            tone, t["text"])
        right.setStyleSheet(
            f"color: {colour}; font-size: 12px; font-weight: 600;"
            "background: transparent; border: none;")
        line.addWidget(right, 1)
        return holder

    def _note(self, text: str, tone: str = "") -> QLabel:
        """A FULL-WIDTH line. Findings are sentences, not label/value pairs
        — running them through _row left every one of them indented past a
        210px empty column."""
        t = self._t
        label = QLabel(text)
        label.setWordWrap(True)
        colour = {"ok": t["ok"], "err": t["err"], "warn": t["warn"]}.get(
            tone, t["text"])
        label.setStyleSheet(
            f"color: {colour}; font-size: 12px; font-weight: 600;"
            "background: transparent; border: none;")
        return label

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text.upper())
        label.setStyleSheet(
            f"color: {self._accent}; font-size: 10px; font-weight: 800;"
            "letter-spacing: 1.2px; background: transparent; border: none;"
            "margin-top: 8px;")
        return label

    def _render(self, report: dict):
        from frontend.health_report import TWEAK_LABELS, findings, tweak_rows

        summary = report.get("tweakSummary") or {}
        self._status.setText(
            f"{report.get('hostname', 'this machine')} · "
            f"{summary.get('applied', 0)} applied · "
            f"{summary.get('notApplied', 0)} not applied · "
            f"{summary.get('unknown', 0)} unknown")

        found = findings(report)
        self._add(self._heading("Findings"))
        if found:
            for line in found:
                self._add(self._note(f"•  {line}", "warn"))
        else:
            self._add(self._note("•  Nothing needing attention.", "ok"))

        system = report.get("system") or {}
        if system:
            self._add(self._heading("System"))
            self._add(self._row("Operating system",
                                f"{system.get('os')} (build {system.get('build')})"))
            self._add(self._row("Processor", str(system.get("cpu"))))
            self._add(self._row(
                "Memory",
                f"{system.get('freeRAMGB')} GB free of {system.get('totalRAMGB')} GB"))
            self._add(self._row("Power plan", str(system.get("powerPlan"))))

        drives = report.get("drives") or []
        if drives:
            self._add(self._heading("Storage"))
            for drive in drives:
                percent = drive.get("percentFree", 100)
                tone = "err" if isinstance(percent, (int, float)) and percent < 10 else ""
                self._add(self._row(
                    f"Drive {drive.get('name')}",
                    f"{drive.get('freeGB')} GB free of {drive.get('totalGB')} GB "
                    f"({percent}%)", tone))

        self._add(self._heading("Configuration drift"))
        for label, state, _task in tweak_rows(report):
            tone = {"applied": "ok", "not-applied": "err"}.get(state, "")
            shown = {"applied": "Applied", "not-applied": "Not applied",
                     "unknown": "Unknown"}[state]
            self._add(self._row(label, shown, tone))

    def _add(self, widget: QWidget):
        self._host_lay.insertWidget(self._host_lay.count() - 1, widget)

    # -- export -----------------------------------------------
    def _export(self, kind: str):
        from frontend.health_report import to_html, to_json

        if not self._report:
            return
        stamp = time.strftime("%Y%m%d_%H%M")
        default = os.path.join(resources.desktop_dir(),
                               f"Pulse_HealthReport_{stamp}.{kind}")
        filters = {"html": "HTML report (*.html)", "json": "JSON data (*.json)"}
        path, _chosen = QFileDialog.getSaveFileName(
            self, f"Export {kind.upper()} report", default, filters[kind])
        if not path:
            return
        try:
            payload = to_html(self._report) if kind == "html" else to_json(self._report)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(payload)
        except OSError as exc:
            self._status.setText(f"Could not write the file: {exc}")
            return
        self._status.setText(f"Exported to {path}")

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)

    def reject(self):
        if self._worker is not None:
            self._worker.cancel()
        super().reject()


class CloseConfirmDialog(PulseDialog):
    """Shown when the window is closed while a task is still running (v10.2).

    Closing used to cancel the task silently, which is the wrong default
    for this app: the running operation may be halfway through an MSI
    install, a driver export or an Edge purge, and "stopped halfway" is a
    materially worse state than either finished or never started. The
    close is now a question rather than an assumption.

    Deliberately NOT a generic ConfirmDialog: the buttons here are not
    Cancel/Proceed. Both options do something irreversible-ish, so each is
    named for its OUTCOME ("Keep Running" / "Stop & Close") — a user
    hitting Alt+F4 by accident must be able to tell the two apart without
    parsing the sentence above them. The safe choice is the default and
    the destructive one carries the error accent.
    """

    def __init__(self, parent: QWidget, t: dict, task_title: str = ""):
        super().__init__(parent)
        accent = t["warn"]
        panel = _dialog_chrome(self, t, accent, width=460)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(10)

        head = QLabel("⚠️  A task is still running")
        head.setStyleSheet(TH.label_qss(t, "dialog"))
        lay.addWidget(head)

        running = task_title.strip() or "An operation"
        body = QLabel(
            f"<b>{running}</b> hasn't finished yet. Closing Pulse now stops "
            "it partway through — the change it was making may be left half "
            "applied, and you'll need to run it again.")
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(body)

        lay.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch()

        # The safe option is the default: Enter and Escape both keep the
        # task alive, so no reflexive keypress can end a long install.
        keep = QPushButton("Keep Running")
        keep.setFixedSize(128, 36)
        keep.setCursor(Qt.CursorShape.PointingHandCursor)
        keep.setStyleSheet(TH.dialog_cancel_qss(t))
        keep.setDefault(True)
        keep.setAutoDefault(True)
        keep.clicked.connect(self.reject)
        row.addWidget(keep)

        # "&&" is not a typo: Qt reads a single & in button text as a
        # mnemonic marker, so "Stop & Close" renders as "Stop _Close" with
        # the C underlined, which looks like a broken label. The doubled
        # ampersand is the escape that paints a literal "&".
        stop = QPushButton("Stop && Close")
        stop.setFixedSize(128, 36)
        stop.setCursor(Qt.CursorShape.PointingHandCursor)
        stop.setStyleSheet(TH.dialog_go_qss(t, t["err"]))
        stop.setAutoDefault(False)
        stop.clicked.connect(self.accept)
        row.addWidget(stop)
        lay.addLayout(row)

        self._keep_btn = keep
        self._stop_btn = stop

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)
        self._keep_btn.setFocus()


# ============================================================
#  ELEVATE PROMPT — inline "this needs Administrator" gate
# ============================================================
class ElevatePromptDialog(PulseDialog):
    """Shown when a NON-elevated Pulse is asked to run an admin-gated action
    (see menu_structure.requires_admin). Instead of spawning PowerShell only
    to bounce back an access-denied verdict, this offers a one-click UAC
    relaunch up front. Accepted => the caller runs PulseApp._relaunch_as_admin;
    rejected => nothing happens and no task is started. Amber `warn` accent to
    match the sidebar's 'Run as Administrator' CTA — a standing requirement,
    not a red failure."""

    def __init__(self, parent: QWidget, item: dict, t: dict):
        super().__init__(parent)
        accent = t["warn"]
        panel = _dialog_chrome(self, t, accent, width=470)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(10)

        head = QLabel("🛡  Administrator required")
        head.setStyleSheet(TH.label_qss(t, "card"))
        lay.addWidget(head)

        body = QLabel(
            f"“{item.get('title', 'This action')}” makes system-level changes "
            "that need Administrator rights. Relaunch Pulse elevated to "
            "continue — Windows will show a UAC consent prompt.")
        body.setWordWrap(True)
        body.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(body)

        lay.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch()

        cancel = QPushButton("Not now")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        go = QPushButton("Relaunch as Administrator")
        go.setFixedSize(214, 36)
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.setStyleSheet(TH.dialog_go_qss(t, accent))
        go.clicked.connect(self.accept)
        row.addWidget(go)
        lay.addLayout(row)

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


# ============================================================
#  SHORTCUT SHEET — F1 / ? keyboard reference
# ============================================================
class ShortcutSheetDialog(PulseDialog):
    """The keyboard reference (F1 or ?).

    v10 added a real keyboard layer; a shortcut nobody can discover is a
    shortcut that doesn't exist, and Ctrl+K had been undiscoverable for
    exactly that reason. Rows are rendered from PulseApp.SHORTCUTS, so the
    sheet cannot drift out of sync with the bindings actually installed."""

    def __init__(self, parent: QWidget, t: dict, shortcuts: list[tuple[str, str]]):
        super().__init__(parent)
        accent = t["accent"]
        panel = _dialog_chrome(self, t, accent, width=440)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(TH.SPACE["xl"], TH.SPACE["xl"],
                               TH.SPACE["xl"], TH.SPACE["lg"])
        lay.setSpacing(TH.SPACE["md"])

        head = QLabel("Keyboard shortcuts")
        head.setStyleSheet(TH.label_qss(t, "dialog"))
        lay.addWidget(head)

        for keys, description in shortcuts:
            row = QHBoxLayout()
            row.setSpacing(TH.SPACE["md"])
            key_label = QLabel(keys)
            key_label.setStyleSheet(TH.keycap_qss(t))
            key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            key_label.setFixedWidth(120)
            row.addWidget(key_label, 0, Qt.AlignmentFlag.AlignVCenter)
            desc = QLabel(description)
            desc.setStyleSheet(TH.label_qss(t, "body"))
            row.addWidget(desc, 1)
            lay.addLayout(row)

        lay.addSpacing(TH.SPACE["sm"])
        foot = QHBoxLayout()
        foot.addStretch()
        close = QPushButton("Close")
        close.setFixedSize(96, 36)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(TH.dialog_cancel_qss(t))
        close.clicked.connect(self.reject)
        foot.addWidget(close)
        lay.addLayout(foot)

    def showEvent(self, e):
        super().showEvent(e)
        _present_dialog(self)


# ============================================================
#  HUB DIALOG — a hub card's landing screen (drill-down navigation)
# ============================================================
class HubDialog(PulseDialog):
    """A primary hub card's landing screen: its sub-actions rendered as
    the exact same GlassCard a category page uses — zero new card design,
    100% visual parity with the page this modal is standing in for. This
    is what lets Software Management collapse to four spacious primary
    cards (Browsers & Daily Apps / Developer & University Hub / Gaming &
    Launchers / System Tools & Utilities) without deleting a single
    existing action: each hub is just a focused, one-level-deeper page.

    Picking a sub-card closes this dialog and hands it back via
    `chosen_item`; the caller runs it through the normal request_task()
    pipeline exactly as if the card lived directly on a category page."""

    def __init__(self, parent: QWidget, hub: dict, t: dict):
        super().__init__(parent)
        self.chosen_item: dict | None = None
        accent = t["accent"]
        panel = _dialog_chrome(self, t, accent, responsive=True)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(14)

        head = QLabel(f"{hub['icon']}  {hub['title']}")
        head.setStyleSheet(TH.label_qss(t, "dialog"))
        lay.addWidget(head)

        sub = QLabel(hub.get("desc", ""))
        sub.setWordWrap(True)
        sub.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(TH.scroll_area_qss(t))
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 6, 0)
        host_lay.setSpacing(14)
        groups = hub.get("groups")
        if groups:
            # Grouped hub (System Tools & Utilities): each group opens with
            # a header ROW — an accent-tinted section title plus a 1px rule
            # fading out to the right (hub_group_header_qss /
            # hub_group_rule_qss) — then its cards at natural height, the
            # whole list top-anchored with a trailing stretch. Rhythm is
            # proximity-correct: a header sits tight over its own cards and
            # a full extra step away from the previous group's last card,
            # so the three clusters read at a glance. With this many
            # sub-actions the point is a tidy, scannable list that scrolls
            # - NOT the equal-stretch "fill the screen" treatment used for
            # the sparse flat hubs below, which would balloon each card and
            # swallow the headers.
            for gi, group in enumerate(groups):
                if gi > 0:
                    host_lay.addSpacing(10)
                head_row = QHBoxLayout()
                head_row.setSpacing(12)
                header = QLabel(group["title"])
                header.setStyleSheet(TH.hub_group_header_qss(t, accent))
                head_row.addWidget(header)
                rule = QFrame()
                rule.setFixedHeight(1)
                rule.setStyleSheet(TH.hub_group_rule_qss(t, accent))
                head_row.addWidget(rule, 1)
                host_lay.addLayout(head_row)
                for item in group["items"]:
                    card = GlassCard(item, accent, t)
                    card.setMinimumHeight(96)
                    card.clicked.connect(lambda it=item: self._choose(it))
                    host_lay.addWidget(card)
            host_lay.addStretch(1)
        else:
            # Every card gets an EQUAL stretch factor and no trailing spacer -
            # with only a handful of sub-actions per hub, top-anchoring them
            # with dead space below (the old behavior) read as an empty,
            # unfinished sub-menu on the new, much taller responsive panel.
            # Stretching each card to share the leftover height instead makes
            # 2-4 sub-actions fill the screen generously, exactly like the
            # premium, fully-populated feel of a normal category page; once
            # there are enough items to exceed the natural minimum heights,
            # the scroll area takes over automatically.
            for item in hub.get("items", []):
                card = GlassCard(item, accent, t)
                card.setMinimumHeight(110)
                card.clicked.connect(lambda it=item: self._choose(it))
                host_lay.addWidget(card, 1)
        scroll.setWidget(host)
        # Stretch factor, not a maximumHeight cap: the panel itself is now
        # a fixed size derived from the host window (see _dialog_chrome's
        # `responsive=True`), so the scroll area should claim every pixel
        # left over after the header/footer instead of stopping short.
        lay.addWidget(scroll, 1)

        lay.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("Close")
        close.setFixedSize(96, 36)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(TH.dialog_cancel_qss(t))
        close.clicked.connect(self.reject)
        row.addWidget(close)
        lay.addLayout(row)

    def _choose(self, item: dict):
        self.chosen_item = item
        self.accept()

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

    def __init__(self, t: dict, parent: QWidget | None = None,
                 timestamps: bool = True):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFont("Cascadia Mono", 9))
        self._timestamps = timestamps
        # No native placeholder text: the empty state is a custom-painted
        # "pulse" waveform motif + message (see paintEvent), not plain text.
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.console_qss(t))
        self._empty_accent = QColor(t["accent"])
        self._empty_text = QColor(t["text_faint"])

    def set_timestamps(self, on: bool):
        """Toggle the HH:MM:SS gutter. Only affects lines written AFTER the
        change — retro-stamping existing output would invent times we never
        observed, and un-stamping would have to parse them back out of text
        that may legitimately contain a similar prefix."""
        self._timestamps = bool(on)

    def _stamp(self, text: str) -> str:
        if not self._timestamps:
            return text
        return f"{QTime.currentTime().toString('HH:mm:ss')}  {text}"

    def put_line(self, text: str, replace_last: bool = False):
        """Slot for PowerShellTask.output(text, replace_last)."""
        if replace_last and not self.document().isEmpty():
            self._replace_last_line(text)
        else:
            self.append_line(text)

    def append_line(self, text: str):
        self.appendPlainText(self._stamp(text))
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
        # re-stamped, not stamp-preserved: a carriage-return progress line is
        # rewritten continuously, so the useful timestamp is the moment of
        # the LATEST update, not of the first one
        cursor.insertText(self._stamp(text))
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    # -- v10 output actions ------------------------------------
    def copy_all(self) -> int:
        """Whole buffer to the clipboard. Returns the line count so the
        caller can confirm what was taken — a silent copy leaves the user
        unsure it worked."""
        text = self.toPlainText()
        QApplication.clipboard().setText(text)
        return len(text.splitlines()) if text else 0

    def export_to(self, path: str) -> int:
        """Write the buffer to `path`, returning the line count. Raises
        OSError on failure — the caller reports it; this must not swallow
        a failed write and imply the log was saved."""
        text = self.toPlainText()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return len(text.splitlines()) if text else 0

    def line_count(self) -> int:
        text = self.toPlainText()
        return len(text.splitlines()) if text else 0

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
#  ACTIVITY DRAWER — auto-collapsing live-output console (v7)
# ============================================================
class ActivityDrawer(QWidget):
    """The v7 replacement for the always-open 170px console block — the
    single biggest spatial win of the redesign.

    A slim 44px 'rail' (status dot · LIVE OUTPUT · state pill · Stop · a
    pin/expand chevron) is always visible; the heavy console + shimmer live
    in a BODY that is collapsed to zero height while idle and animates open
    the instant a task runs (set_running(True)), then animates shut again
    when it finishes — handing ~140px of vertical canvas back to the card
    grid whenever nothing is executing. The chevron lets the user PIN it
    open across tasks.

    Doctrine-compliant: the open/close motion is a QPropertyAnimation on the
    body's maximumHeight — no QGraphicsEffect, no per-frame QSS. main.py
    reaches the console / state pill / stop button / shimmer / status dot as
    plain attributes, so the existing task pipeline wires to them unchanged."""

    BODY_H = 186   # console (172) + spacing (8) + shimmer (6)
    ANIM_MS = 200

    # Emitted on every frame of the open/close animation so anything
    # anchored to the drawer's top edge (the toast stack) tracks it live
    # rather than snapping once the animation has finished.
    height_changed = Signal()

    def __init__(self, t: dict, on_stop=None, parent: QWidget | None = None,
                 pinned: bool = False):
        super().__init__(parent)
        self._pinned = pinned
        self._active = False   # a task is currently running

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # -- always-visible rail ------------------------------
        self._rail = QFrame()
        self._rail.setObjectName("activityRail")
        self._rail.setFixedHeight(44)
        rail = QHBoxLayout(self._rail)
        rail.setContentsMargins(14, 0, 8, 0)
        rail.setSpacing(10)

        self.status_dot = StatusDot("●")
        self.status_dot.setFixedWidth(12)
        rail.addWidget(self.status_dot)
        self.status_text = QLabel("System Ready")
        rail.addWidget(self.status_text)
        rail.addStretch()

        self._console_label = QLabel("LIVE OUTPUT")
        rail.addWidget(self._console_label)
        self.state_pill = StatePill(t)
        rail.addWidget(self.state_pill)

        self.stop_btn = QPushButton("■  Stop Task")
        self.stop_btn.setFixedSize(112, 26)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setToolTip(
            "Hard-stop the running task (kills the whole process tree)")
        if on_stop is not None:
            self.stop_btn.clicked.connect(on_stop)
        self.stop_btn.hide()
        rail.addWidget(self.stop_btn)

        # -- v10 output actions -------------------------------
        # Live output was previously a dead end: you could watch it scroll
        # past and nothing else. These four turn it into something you can
        # actually take away — copy it into a bug report, save it beside a
        # failed run, clear it before a fresh attempt, or drop the
        # timestamp gutter when pasting somewhere narrow. Icon-only ghost
        # buttons so the rail stays quiet.
        self._tools: list[QPushButton] = []

        def tool(glyph_key: str, tip: str, slot, checkable: bool = False):
            char, fluent = TH.glyph(glyph_key)
            btn = QPushButton(char)
            btn.setFixedSize(26, 26)
            btn.setCheckable(checkable)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setToolTip(tip)
            font = TH.icon_font(12) if fluent else None
            if font is not None:
                btn.setFont(font)
            btn.clicked.connect(slot)
            rail.addWidget(btn)
            self._tools.append(btn)
            return btn

        self._btn_stamp = tool("clock", "Show timestamps in the output",
                               self._toggle_timestamps, checkable=True)
        self._btn_stamp.setChecked(True)
        tool("copy", "Copy all output to the clipboard", self._copy_output)
        tool("export", "Save the output to a file…", self._export_output)
        tool("clear", "Clear the output", self._clear_output)

        self._toggle = QPushButton(TH.glyph("chevron")[0])
        self._toggle.setCheckable(True)
        self._toggle.setFixedSize(28, 28)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.setToolTip("Pin the live output open")
        tf = TH.icon_font(13) if TH.glyph("chevron")[1] else None
        if tf is not None:
            self._toggle.setFont(tf)
        self._toggle.toggled.connect(self._on_toggle)
        rail.addWidget(self._toggle)

        self._grip = QSizeGrip(self._rail)
        self._grip.setFixedSize(14, 14)
        rail.addWidget(self._grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        outer.addWidget(self._rail)

        # -- collapsible body ---------------------------------
        self._body = QWidget()
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        self.console = LiveConsole(t)
        self.console.setFixedHeight(172)
        body.addWidget(self.console)
        self.shimmer = ShimmerBar()
        body.addWidget(self.shimmer)
        outer.addWidget(self._body)

        # Start collapsed (idle) — the whole point of the drawer — unless
        # the user pinned it open in a previous session (v10 persistence).
        self._body.setMaximumHeight(self.BODY_H if pinned else 0)
        self._body.setVisible(pinned)
        if pinned:
            self._toggle.setChecked(True)

        self._anim = QPropertyAnimation(self._body, b"maximumHeight", self)
        self._anim.setDuration(self.ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.finished.connect(self._on_anim_done)
        self._anim.valueChanged.connect(lambda _v: self.height_changed.emit())

        self.apply_theme(t)

    # -- output actions ---------------------------------------
    # `notify` is supplied by main.py so these report through the app's own
    # ToastManager; the drawer has no business owning notification UI.
    def set_notifier(self, notify):
        self._notify = notify

    def _tell(self, kind: str, message: str):
        notify = getattr(self, "_notify", None)
        if notify is not None:
            notify(kind, message)

    def _toggle_timestamps(self, checked: bool):
        self.console.set_timestamps(checked)
        self._btn_stamp.setToolTip(
            "Hide timestamps in the output" if checked
            else "Show timestamps in the output")

    def _copy_output(self):
        lines = self.console.copy_all()
        if lines:
            self._tell("success", f"Copied {lines} line(s) to the clipboard.")
        else:
            self._tell("info", "There is no output to copy yet.")

    def _clear_output(self):
        if not self.console.line_count():
            self._tell("info", "The output is already empty.")
            return
        self.console.clear_console()
        self._tell("info", "Output cleared.")

    def _export_output(self):
        if not self.console.line_count():
            self._tell("info", "There is no output to save yet.")
            return
        default = os.path.join(
            resources.desktop_dir(),
            f"Pulse_Output_{QDateTime.currentDateTime().toString('yyyyMMdd_HHmmss')}.txt")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save live output", default, "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            lines = self.console.export_to(path)
        except OSError as exc:
            # never claim a save that didn't happen
            self._tell("error", f"Could not save the output: {exc}")
            return
        self._tell("success", f"Saved {lines} line(s) to {os.path.basename(path)}")

    # -- theming ----------------------------------------------
    def apply_theme(self, t: dict):
        self._rail.setStyleSheet(TH.activity_rail_qss(t))
        self._console_label.setStyleSheet(TH.console_header_qss(t))
        for btn in self._tools:
            btn.setStyleSheet(TH.activity_toggle_qss(t))
        self.status_text.setStyleSheet(TH.label_qss(t, "status"))
        self.state_pill.apply_theme(t)
        self.stop_btn.setStyleSheet(TH.stop_button_qss(t))
        self._toggle.setStyleSheet(TH.activity_toggle_qss(t))
        self.console.apply_theme(t)
        self.shimmer.set_theme(t)

    # -- open / close animation -------------------------------
    def _animate_to(self, target: int):
        self._anim.stop()
        if target > 0:
            self._body.setVisible(True)
        self._anim.setStartValue(self._body.maximumHeight())
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim_done(self):
        # fully hide the body once closed so the console stops painting
        if self._body.maximumHeight() == 0:
            self._body.setVisible(False)

    def _open(self):
        self._animate_to(self.BODY_H)

    def _close(self):
        self._animate_to(0)

    def _on_toggle(self, checked: bool):
        self._pinned = checked
        self._toggle.setToolTip(
            "Unpin the live output" if checked else "Pin the live output open")
        if checked:
            self._open()
        elif not self._active:
            self._close()

    # -- public API (called by main.py's task pipeline) --------
    HOLD_MS = 1500   # keep the final verdict visible before auto-collapsing

    def set_running(self, running: bool):
        """A task started (True) → expand immediately; finished (False) →
        collapse after a brief hold so the final verdict/output stays
        readable, unless the user has pinned the drawer open."""
        self._active = running
        if running:
            self._open()
        else:
            QTimer.singleShot(self.HOLD_MS, self._collapse_if_idle)

    def _collapse_if_idle(self):
        # a new task may have started (or the user pinned it) during the hold
        if not self._active and not self._pinned:
            self._close()

    def is_pinned(self) -> bool:
        """Persisted across sessions — see utils.prefs.drawer_pinned."""
        return self._pinned

    def toggle_pinned(self):
        """Ctrl+\\ — flip the pin through the toggle button so the chevron's
        checked state, the tooltip and the drawer stay in one truth."""
        self._toggle.setChecked(not self._toggle.isChecked())


# ============================================================
#  TOGGLE SWITCH — native-feeling animated on/off control
# ============================================================
class RecentOperationRow(QPushButton):
    """One entry in the sidebar's Recent Operations panel: the operation's
    own module-accented glyph, its title, and a small outcome dot (green /
    red) recording how the last run ended. Clicking re-runs it through the
    app's normal request_task pipeline, so confirmations, selectors and the
    admin gate all still apply."""

    _DOT = 6

    def __init__(self, entry: dict, t: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.entry = entry
        self._accent_key = entry.get("accent", "")
        self._glyph_key = entry.get("glyph", "")
        self._outcome = entry.get("outcome", "ok")
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(str(entry.get("title", "")).replace("&", "&&"))
        self.setToolTip(f"Run “{entry.get('title', '')}” again")
        self._glow = GlowController(self, TH.resolve_accent(t, self._accent_key))
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self._accent = QColor(TH.resolve_accent(t, self._accent_key))
        self._glow.set_accent(TH.resolve_accent(t, self._accent_key))
        self._dot_color = QColor(t["ok"] if self._outcome == "ok" else t["err"])
        self.setStyleSheet(TH.recent_row_qss(t))
        self._glyph_char, fluent = TH.glyph(self._glyph_key)
        self._icon_font = TH.icon_font(13) if fluent else None

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # leading module glyph
        p.setPen(self._accent)
        if self._icon_font is not None:
            p.setFont(self._icon_font)
        else:
            f = QFont(self.font())
            f.setPixelSize(12)
            p.setFont(f)
        p.drawText(QRectF(10, 0, 18, self.height()),
                   Qt.AlignmentFlag.AlignCenter, self._glyph_char)
        # trailing outcome dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._dot_color)
        cy = self.height() / 2.0
        p.drawEllipse(QPointF(self.width() - 14, cy), self._DOT / 2, self._DOT / 2)
        paint_glow_frame(p, self.rect(), TH.RADIUS["chip"], self._glow.color,
                         self._glow.intensity, self._glow.cursor)
        p.end()


class RecentOperationsPanel(QWidget):
    """The sidebar's 'Recent' block (v10).

    The nav rail ended after six module buttons and then ran into ~360px of
    dead space before the elevation CTA — the single largest unused area in
    the app. This fills it with the one thing a rail like this can offer
    that navigation cannot: the operations you actually ran, one click from
    running again.

    Hidden entirely when the trail is empty (a first launch shows no
    stub/placeholder — an empty panel would be worse than the void it
    replaced), so it costs nothing until it has something to say."""

    rerun_requested = Signal(str)   # task name

    def __init__(self, t: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: list[RecentOperationRow] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(TH.SPACE["xs"])

        header = QHBoxLayout()
        header.setSpacing(TH.SPACE["md"])
        self._title = QLabel("RECENT")
        self._title.setIndent(10)
        header.addWidget(self._title)
        self._rule = QFrame()
        self._rule.setFixedHeight(1)
        header.addWidget(self._rule, 1)
        lay.addLayout(header)
        lay.addSpacing(TH.SPACE["xs"])

        self._rows_box = QVBoxLayout()
        self._rows_box.setContentsMargins(0, 0, 0, 0)
        self._rows_box.setSpacing(TH.SPACE["xs"])
        lay.addLayout(self._rows_box)
        self._t = t
        self.apply_theme(t)
        self.setVisible(False)

    def set_entries(self, entries: list[dict]):
        for row in self._rows:
            self._rows_box.removeWidget(row)
            row.deleteLater()
        self._rows.clear()
        for entry in entries:
            row = RecentOperationRow(entry, self._t)
            row.clicked.connect(
                lambda checked=False, task=entry.get("task", ""):
                self.rerun_requested.emit(task))
            self._rows_box.addWidget(row)
            self._rows.append(row)
        self.setVisible(bool(entries))

    def apply_theme(self, t: dict):
        self._t = t
        self._title.setStyleSheet(TH.label_qss(t, "section"))
        self._rule.setStyleSheet(TH.hub_group_rule_qss(t, t["accent"]))
        for row in self._rows:
            row.apply_theme(t)


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
    Launchers, Diagnostics, Core API Runtimes…).

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

        panel = _dialog_chrome(self, t, accent, responsive=True)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QLabel(f"{item['icon']}  {item['title']}")
        head.setStyleSheet(TH.label_qss(t, "dialog"))
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
        # Stretch factor, not a maximumHeight cap — see HubDialog's note;
        # the panel is now a fixed size derived from the host window.
        lay.addWidget(scroll, 1)

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
        title.setStyleSheet(TH.label_qss(t, "dialog"))
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
        head.setStyleSheet(TH.label_qss(t, "dialog"))
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

        panel = _dialog_chrome(self, t, t["accent"], responsive=True)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(10)

        head = QLabel("🎓  Developer Toolkit")
        head.setStyleSheet(TH.label_qss(t, "dialog"))
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
        lay.addWidget(scroll, 1)

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
    """One update candidate, built on the EXACT same structure as
    DevHubRow (checkbox carries its own label, a '⋯' wizard button sits at
    the row's right edge, a muted caption line underneath) — so an Update
    Center row and an Essential Apps / Dev Hub row read as one family, not
    two different products with different padding and chrome. Pre-checked,
    same 'curated pack' contract every other selector uses — the scan
    already promised these are real, available upgrades.

    The whole row is clickable (not just the checkbox) — ticking a box or
    tapping anywhere on the row does the same thing, matching how a native
    settings list behaves. The '⋯' opens the identical
    ToolInstallWizardDialog every other app row uses (Path A silent winget
    / Path B official link / Path C local file); Path A there just narrows
    the caller's selection down to this one AppId."""

    options_requested = Signal(str)  # app_id

    def __init__(self, app_id: str, name: str, current: str, available: str, t: dict):
        super().__init__()
        self.app_id = app_id
        self.app_name = name
        self.current_version = current
        self.available_version = available
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.checkbox = QCheckBox(name)
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.setChecked(True)
        row.addWidget(self.checkbox)
        row.addStretch()

        self._current = QLabel(current or "—")
        row.addWidget(self._current)
        self._arrow = QLabel("→")
        row.addWidget(self._arrow)
        self._available = QLabel(available or "—")
        row.addWidget(self._available)

        self.options_btn = QPushButton("⋯")
        self.options_btn.setFixedSize(28, 24)
        self.options_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.options_btn.setToolTip(
            "Install options for this app (winget / official link / local file)")
        self.options_btn.clicked.connect(lambda: self.options_requested.emit(self.app_id))
        row.addWidget(self.options_btn)
        outer.addLayout(row)

        self._id_label = QLabel(app_id)
        outer.addWidget(self._id_label)

        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.dev_hub_row_qss(t))
        self.checkbox.setStyleSheet(TH.checkbox_qss(t, t["accent"]))
        self._current.setStyleSheet(TH.version_chip_qss(t, accent=False))
        self._available.setStyleSheet(TH.version_chip_qss(t, accent=True))
        self._arrow.setStyleSheet(TH.label_qss(t, "faint"))
        self.options_btn.setStyleSheet(TH.icon_ghost_button_qss(t, t["accent"]))
        self._id_label.setStyleSheet(TH.label_qss(t, "caption"))

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def mouseReleaseEvent(self, e):
        # Click-anywhere-toggles, except on controls that already own
        # their own click (the checkbox itself, the '⋯' wizard button).
        if e.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(e.position().toPoint())
            if child not in (self.checkbox, self.options_btn):
                self.checkbox.setChecked(not self.checkbox.isChecked())
        super().mouseReleaseEvent(e)


# ============================================================
#  UPDATE CENTER — live winget scan + selective / bulk apply
# ============================================================
class UpdateCenterDialog(PulseDialog):
    """'Check for Updates': runs a live background winget scan (task
    ScanForUpdates), then presents a version audit (current vs. available)
    per app in the exact same panel geometry, row styling and action-row
    layout as AppSelectorDialog — same width, same padding, same single
    primary CTA — so the two feel like the same screen with different
    data, not two different dialogs. It never installs anything itself.

    After exec():
      Accepted + selected_ids non-empty -> caller runs task
      'UpdateSelectedApps' with those AppIds through the app's normal
      request_task()/_start_task() pipeline — the same live console, Stop
      button and toast machinery as every other bulk deploy.
      Accepted + local_installer set -> caller runs task InstallLocalFile,
      exactly like AppSelectorDialog/DevHubSelectorDialog's row wizards.
      Rejected -> nothing to do.
    """

    def __init__(self, parent: QWidget, ps1_path: str, t: dict):
        super().__init__(parent)
        self._t = t
        self._ps1_path = ps1_path
        self.selected_ids: list[str] = []
        self.local_installer: tuple[str, str] | None = None
        self._rows: dict[str, UpdateRow] = {}
        self._thread: QThread | None = None
        self._worker: PowerShellTask | None = None
        accent = t["accent"]

        panel = _dialog_chrome(self, t, accent, responsive=True)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QLabel("🔄  Update Center")
        head.setStyleSheet(TH.label_qss(t, "dialog"))
        lay.addWidget(head)

        self._subtitle = QLabel("Scanning installed apps against winget…")
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(self._subtitle)

        self._stack = QStackedWidget()
        lay.addWidget(self._stack, 1)
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
        """Deliberately mirrors AppSelectorDialog's results layout line for
        line: Select All / Deselect All / stretch / count on the left
        toolbar (Rescan joins the left cluster so the right edge stays
        exactly the count label, like every other selector), the same
        360px scroll cap, and a Cancel + single primary-CTA bottom row —
        same sizes, same QSS factories."""
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
        rescan_btn = QPushButton("Rescan")
        rescan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rescan_btn.setStyleSheet(TH.link_button_qss(t, accent))
        rescan_btn.clicked.connect(self._start_scan)
        toolbar.addWidget(rescan_btn)
        toolbar.addStretch()
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(TH.label_qss(t, "caption"))
        toolbar.addWidget(self._count_label)
        lay.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(TH.scroll_area_qss(t))
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 6, 0)
        self._host_lay.setSpacing(8)
        self._host_lay.addStretch()
        scroll.setWidget(self._host)
        lay.addWidget(scroll, 1)

        lay.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 36)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        self._deploy_btn = QPushButton("Update Selected")
        self._deploy_btn.setFixedSize(160, 36)
        self._deploy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._deploy_btn.setStyleSheet(TH.dialog_go_qss(t, accent))
        self._deploy_btn.clicked.connect(self._accept_selection)
        row.addWidget(self._deploy_btn)
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
            row.options_requested.connect(self._open_tool_wizard)
            self._rows[app_id] = row
            self._host_lay.insertWidget(self._host_lay.count() - 1, row)
        # Same sentence shape AppSelectorDialog uses for its curated packs —
        # one consistent voice across every selector in the app.
        self._subtitle.setText(
            f"All {len(self._rows)} updates are pre-selected — untick anything you don't "
            "want, or use a row's ⋯ for more install options.")
        self._update_count()

    def _set_all(self, checked: bool):
        for row in self._rows.values():
            row.checkbox.setChecked(checked)

    def _update_count(self, _checked: bool = False):
        count = sum(1 for r in self._rows.values() if r.is_checked())
        self._count_label.setText(f"{count} selected")
        total = len(self._rows)
        if count and count == total:
            self._deploy_btn.setText(f"Update All ({count})")
        else:
            self._deploy_btn.setText(f"Update Selected ({count})" if count else "Update Selected")
        self._deploy_btn.setEnabled(count > 0)

    # -- acceptance -------------------------------------------------
    def _accept_selection(self):
        self.selected_ids = [aid for aid, row in self._rows.items() if row.is_checked()]
        if not self.selected_ids:
            return
        self.accept()

    # -- per-app wizard ("⋯") --------------------------------------------
    def _open_tool_wizard(self, app_id: str):
        row = self._rows.get(app_id)
        if row is None:
            return
        desc = f"Update available: {row.current_version} → {row.available_version}"
        wizard = ToolInstallWizardDialog(self, app_id, row.app_name, desc, "", self._t)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return
        if wizard.mode == "winget":
            self._set_all(False)
            row.checkbox.setChecked(True)
            self._accept_selection()
        elif wizard.mode == "local" and wizard.local_path:
            self.local_installer = (row.app_name, wizard.local_path)
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
        panel = _dialog_chrome(self, t, accent, responsive=True)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(12)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("🚀  Startup Manager")
        title.setStyleSheet(TH.label_qss(t, "dialog"))
        title_col.addWidget(title)
        self._subtitle = QLabel("Auditing Run keys and Startup folders…")
        self._subtitle.setWordWrap(True)
        self._subtitle.setStyleSheet(TH.label_qss(t, "body"))
        title_col.addWidget(self._subtitle)
        head.addLayout(title_col)
        head.addStretch()
        lay.addLayout(head)

        self._stack = QStackedWidget()
        lay.addWidget(self._stack, 1)
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
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._host_lay = QVBoxLayout(self._host)
        self._host_lay.setContentsMargins(0, 0, 6, 0)
        self._host_lay.setSpacing(8)
        self._host_lay.addStretch()
        scroll.setWidget(self._host)
        lay.addWidget(scroll, 1)

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
        # 90s, not 60s: the scan itself is fast (pure registry reads + in-
        # memory regex scoring — see 05-Startup.ps1), but cold PowerShell
        # process start-up (module dot-sourcing, AV real-time scanning) is
        # environment-dependent and deserves real margin, not a hair-trigger
        # timeout — same generous window ScanForUpdates already uses.
        worker = PowerShellTask(self._ps1_path, "StartupReport", timeout=90)
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
        worker = PowerShellTask(self._ps1_path, task_name, timeout=60,
                                startup_item_id=item_id)
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
