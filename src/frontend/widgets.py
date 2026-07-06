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
    QEasingCurve, QEvent, QPoint, QPointF, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QRadialGradient, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from frontend.animations import GlowController, paint_glow_frame
from frontend import theme as TH


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

    # -- drag to move (with maximized-drag guard) --------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (e.globalPosition().toPoint()
                                 - self._window.frameGeometry().topLeft())

    def mouseMoveEvent(self, e):
        if self._drag_offset is None or not (e.buttons() & Qt.MouseButton.LeftButton):
            return
        if self._window.isMaximized():
            # restore, then re-anchor the (now smaller) window under the
            # cursor at the same horizontal ratio — no visual jump
            ratio = e.position().x() / max(1.0, float(self.width()))
            self._window.showNormal()
            self._drag_offset = QPoint(
                int(self._window.width() * ratio), int(e.position().y()))
        self._window.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None

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
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.nav_button_qss(t))

    def set_selected(self, on: bool):
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def paintEvent(self, e):
        super().paintEvent(e)  # QSS background/text first
        p = QPainter(self)
        paint_glow_frame(p, self.rect(), 13, self._glow.color,
                         self._glow.intensity, self._glow.cursor)
        p.end()


# ============================================================
#  GLASS CARD — one operation, painted glow, live re-skin
# ============================================================
class GlassCard(QFrame):
    clicked = Signal()

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

        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(14)

        self._icon = QLabel(item["icon"])
        self._icon.setFixedWidth(40)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self._icon)

        col = QVBoxLayout()
        col.setSpacing(4)
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
        self._icon.setStyleSheet("font-size: 28px; background: transparent; border: none;")
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

    # -- interaction / painting --------------------------------
    def mouseReleaseEvent(self, e):
        if (e.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(e.position().toPoint())):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)  # QSS glass background/border first
        p = QPainter(self)
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


# ============================================================
#  LIVE CONSOLE — streams raw PowerShell stdout in real time
# ============================================================
class LiveConsole(QPlainTextEdit):
    """Read-only micro-terminal. `append_line()` is called once per stdout
    line as PowerShellTask.output fires, auto-scrolling to the newest line
    so winget percentages / SFC progress read exactly like a real console."""

    MAX_LINES = 2000  # bound memory on very long-running tasks (SFC/DISM)

    def __init__(self, t: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFont("Cascadia Mono", 9))
        self.setPlaceholderText("Console idle — output streams here while a task runs.")
        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self.setStyleSheet(TH.console_qss(t))

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

    def clear_console(self):
        self.clear()


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
