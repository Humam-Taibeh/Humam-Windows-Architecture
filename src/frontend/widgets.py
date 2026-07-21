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

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRectF, Qt,
    QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QRadialGradient, QTextCursor,
)
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from frontend.animations import (
    GlowController, RippleController, paint_bevel_frame, paint_glow_frame,
    paint_ripple_frame,
)
from frontend import theme as TH


def _animate_dialog_entrance(dialog: QDialog, duration_ms: int = 130):
    """Premium dialog entrance: a quick window-level fade. windowOpacity is
    compositor-side — no QGraphicsEffect, safe on frameless translucent
    dialogs, and cleaned up implicitly when the dialog closes."""
    dialog.setWindowOpacity(0.0)
    anim = QPropertyAnimation(dialog, b"windowOpacity", dialog)
    anim.setDuration(duration_ms)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start()
    dialog._entrance_anim = anim  # keep alive for the run


# ============================================================
#  TITLE BAR — drag, double-click max, ☀️/🌙, ▢ max/restore
# ============================================================
class TitleBar(QWidget):
    """Frameless-window chrome. Left: brand. Right: theme toggle,
    minimize, maximize/restore (square glyph), close.

    Drag guard: dragging while maximized restores the window first and
    re-anchors it under the cursor proportionally — native Windows feel.
    """

    theme_toggle_requested = Signal()

    def __init__(self, window: QMainWindow, t: dict,
                 app_name: str, version: str):
        super().__init__(window)
        self._window = window
        self._drag_offset: QPoint | None = None
        self._press_gp: QPoint | None = None
        self.setFixedHeight(48)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(22, 10, 14, 0)
        lay.setSpacing(8)

        self._glyph = QLabel("✦")
        lay.addWidget(self._glyph)
        self._brand = QLabel(f"{app_name}  ·  v{version}")
        lay.addWidget(self._brand)
        lay.addStretch()

        def _mk(text: str, tip: str, slot) -> QPushButton:
            b = QPushButton(text)
            b.setFixedSize(34, 28)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b

        self._btn_theme = _mk("☀️", "Switch theme", self.theme_toggle_requested.emit)
        self._btn_min = _mk("—", "Minimize", window.showMinimized)
        self._btn_max = _mk("□", "Maximize", self._toggle_max)
        self._btn_close = _mk("✕", "Close", window.close)

        # keep the max/restore glyph honest however the state changes
        window.installEventFilter(self)
        self.apply_theme(t)

    # -- theming ----------------------------------------------
    def apply_theme(self, t: dict):
        self._t = t
        self._glyph.setStyleSheet(
            f"color: {t['accent']}; font-size: 18px; background: transparent; border: none;")
        self._brand.setStyleSheet(TH.label_qss(t, "brand"))
        for btn in (self._btn_theme, self._btn_min, self._btn_max):
            btn.setStyleSheet(TH.titlebar_button_qss(t, t["titlebar_hover"]))
        self._btn_close.setStyleSheet(TH.titlebar_button_qss(t, t["close_hover"]))
        self._btn_theme.setText("☀️" if t["name"] == "dark" else "🌙")
        self._btn_theme.setToolTip(
            "Switch to light theme" if t["name"] == "dark" else "Switch to dark theme")

    # -- maximize / restore -----------------------------------
    def _toggle_max(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def _sync_max_glyph(self):
        maxed = self._window.isMaximized()
        self._btn_max.setText("❐" if maxed else "□")
        self._btn_max.setToolTip("Restore" if maxed else "Maximize")

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
        super().__init__(f"{icon}  {title}")
        self.setFixedHeight(50)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", False)
        self._glow = GlowController(self, accent)
        self._ripple = RippleController(self)
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.nav_button_qss(t))

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
        self.setMinimumHeight(118)
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
        lay.setContentsMargins(18, 14, 18, 14)
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
        col.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._title = QLabel(item["title"])
        head.addWidget(self._title)
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
class ConfirmDialog(QDialog):
    def __init__(self, parent: QWidget, item: dict, t: dict):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(430)

        danger = bool(item.get("danger"))
        accent = t["err"] if danger else t["accent"]

        panel = QFrame(self)
        panel.setStyleSheet(TH.dialog_panel_qss(t, accent))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(26, 24, 26, 22)
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
        cancel.setFixedSize(96, 34)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        go = QPushButton("Proceed")
        go.setFixedSize(96, 34)
        go.setCursor(Qt.CursorShape.PointingHandCursor)
        go.setStyleSheet(TH.dialog_go_qss(t, accent))
        go.clicked.connect(self.accept)
        row.addWidget(go)
        lay.addLayout(row)

    def showEvent(self, e):
        super().showEvent(e)
        _animate_dialog_entrance(self)


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
#  APP ROW — one checkbox entry inside the multi-selector overlay
# ============================================================
class AppRow(QFrame):
    def __init__(self, app_id: str, app_name: str, t: dict):
        super().__init__()
        self.app_id = app_id
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)

        self.checkbox = QCheckBox(app_name)
        self.checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.checkbox.setChecked(True)
        lay.addWidget(self.checkbox)
        lay.addStretch()

        self._id_label = QLabel(app_id)
        lay.addWidget(self._id_label)

        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.app_row_qss(t))
        self.checkbox.setStyleSheet(TH.checkbox_qss(t, t["accent"]))
        self._id_label.setStyleSheet(TH.label_qss(t, "caption"))

    def is_checked(self) -> bool:
        return self.checkbox.isChecked()


# ============================================================
#  APP SELECTOR DIALOG — checkbox multi-selector overlay
# ============================================================
class AppSelectorDialog(QDialog):
    """Frameless glass overlay listing every app in a Software Management
    category as a checkbox, so the user picks exactly which winget IDs get
    deployed instead of the whole category running blind. `selected_ids`
    holds the AppId list after an Accepted exec()."""

    def __init__(self, parent: QWidget, item: dict, t: dict):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(460)

        self.selected_ids: list[str] = []
        self._rows: list[AppRow] = []
        apps: list[tuple[str, str]] = item.get("apps", [])
        accent = t["accent"]

        panel = QFrame(self)
        panel.setStyleSheet(TH.dialog_panel_qss(t, accent))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)

        head = QLabel(f"{item['icon']}  {item['title']}")
        head.setStyleSheet(TH.label_qss(t, "card").replace("14px", "16px"))
        lay.addWidget(head)

        sub = QLabel(f"Choose exactly which of these {len(apps)} apps to deploy.")
        sub.setStyleSheet(TH.label_qss(t, "body"))
        lay.addWidget(sub)

        # -- select-all / select-none shortcuts -----------------
        toolbar = QHBoxLayout()
        toolbar.setSpacing(16)
        all_btn = QPushButton("Select All")
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.setStyleSheet(TH.link_button_qss(t, accent))
        all_btn.clicked.connect(lambda: self._set_all(True))
        toolbar.addWidget(all_btn)

        none_btn = QPushButton("Select None")
        none_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        none_btn.setStyleSheet(TH.link_button_qss(t, accent))
        none_btn.clicked.connect(lambda: self._set_all(False))
        toolbar.addWidget(none_btn)
        toolbar.addStretch()
        lay.addLayout(toolbar)

        # -- scrollable checkbox list -----------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(TH.scroll_area_qss(t))
        scroll.setMaximumHeight(320)

        host = QWidget()
        host.setStyleSheet("background: transparent;")
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 4, 0)
        host_lay.setSpacing(8)
        for app_id, app_name in apps:
            row = AppRow(app_id, app_name, t)
            self._rows.append(row)
            host_lay.addWidget(row)
        host_lay.addStretch()
        scroll.setWidget(host)
        lay.addWidget(scroll)

        lay.addSpacing(4)
        row = QHBoxLayout()
        row.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setFixedSize(96, 34)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(TH.dialog_cancel_qss(t))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        deploy = QPushButton("Deploy")
        deploy.setFixedSize(110, 34)
        deploy.setCursor(Qt.CursorShape.PointingHandCursor)
        deploy.setStyleSheet(TH.dialog_go_qss(t, accent))
        deploy.clicked.connect(self._accept_selection)
        row.addWidget(deploy)
        lay.addLayout(row)

    def _set_all(self, checked: bool):
        for row in self._rows:
            row.checkbox.setChecked(checked)

    def _accept_selection(self):
        self.selected_ids = [row.app_id for row in self._rows if row.is_checked()]
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        _animate_dialog_entrance(self)


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


class CommandPalette(QDialog):
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
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setFixedWidth(560)

        self.chosen_item: dict | None = None
        self._entries = entries  # (item dict, category title) pairs

        panel = QFrame(self)
        panel.setStyleSheet(TH.dialog_panel_qss(t, t["accent"]))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

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
        _animate_dialog_entrance(self, duration_ms=130)
        self._search.setFocus()
