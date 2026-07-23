"""
src/frontend/main.py

Pulse — GUI orchestrator (PySide6).

MODULAR BLUEPRINT (v6)
======================
    menu_structure.py   data      — categories, cards, task IDs, timeouts
    theme.py            design    — dual-theme tokens, QSS factories, DWM glass
    animations.py       motion    — glow, shimmer, cascade, page fade (60 fps)
    widgets.py          components— TitleBar, NavButton, GlassCard, ConfirmDialog
    utils/helpers.py    threading — PowerShellTask worker, ToastManager
    main.py (this)      orchestration ONLY — pages, navigation, task pipeline

Runtime guarantees:
    - Qt widgets touched only from the GUI thread; PowerShell runs on a
      QThread and reports back through signals.
    - One task at a time; extra clicks get an info toast.
    - No QGraphicsEffect in steady state, no setStyleSheet() in timers —
      see animations.py for the performance doctrine.
    - Theme switches live via ThemeManager.changed -> _apply_theme(t).
"""
from __future__ import annotations

import ctypes
import os
import platform
import sys

if sys.platform == "win32":
    import ctypes.wintypes  # MSG / RECT for native window hit-testing

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, Qt, QThread,
    QTimer, Signal,
)
from PySide6.QtGui import QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QGridLayout,
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea, QSizeGrip,
    QStackedWidget, QVBoxLayout, QWidget,
)

# Allow "from utils.helpers import ..." / "from frontend import ..." when
# running as src/frontend/main.py or from a PyInstaller bundle.
_FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_FRONTEND_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from utils.helpers import PowerShellTask, TaskResult, ToastManager  # noqa: E402
from frontend import theme as TH  # noqa: E402
from frontend.animations import CascadeAnimator, PageFader, ShimmerBar  # noqa: E402
from frontend.menu_structure import (  # noqa: E402
    CATEGORIES, DEV_HUB_BUNDLES, DEV_HUB_GROUPS, total_operations,
)
from frontend.widgets import (  # noqa: E402
    AppSelectorDialog, BreathingIcon, CommandPalette, ConfirmDialog,
    DepthCard, DevHubSelectorDialog, GlassCard, LiveConsole, NavButton,
    NavPill, OfficeWizardDialog, Scrim, StatePill, StatusDot, TitleBar,
)

# ============================================================
#  APP CONSTANTS
# ============================================================
APP_NAME = "PULSE"
APP_VERSION = "6.1"
APP_CHANNEL = "Beta"   # release channel — rendered as a badge, never in prose
PS1_FILENAME = "core.ps1"
DEFAULT_TIMEOUT = 900

# Body-layout margins: comfortable while floating, collapsed to a slim
# comfort gap when maximized/flush so the (now border-less, radius-less)
# shell doesn't leave a dead-space frame around the sidebar/content.
_FLOAT_MARGINS = (20, 8, 20, 16)
_FLUSH_MARGINS = (10, 6, 10, 10)


def _locate_icon() -> str | None:
    """assets/pulse.ico — project root in dev, _MEIPASS in the bundle."""
    candidates = [os.path.join(os.path.dirname(_SRC_DIR), "assets", "pulse.ico")]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, os.path.join(meipass, "assets", "pulse.ico"))
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


# ============================================================
#  SYSTEM INSIGHTS — cheap, dependency-free hardware snapshot
# ============================================================
def _system_insights() -> list[tuple[str, str, str]]:
    """(icon, value, caption) triplets for the Welcome dashboard.
    Registry + kernel32 reads only — resolves in microseconds, so it is
    safe to call on the GUI thread during construction."""
    insights: list[tuple[str, str, str]] = []

    # -- OS -------------------------------------------------
    if sys.platform == "win32":
        build = sys.getwindowsversion().build
        name = "Windows 11" if build >= 22000 else "Windows 10"
        try:
            edition = platform.win32_edition() or ""
        except OSError:
            edition = ""
        insights.append(("🪟", f"{name} {edition}".strip(), f"Build {build}"))
    else:  # dev on non-Windows
        insights.append(("🪟", platform.system(), platform.release()))

    # -- CPU ------------------------------------------------
    cores = os.cpu_count() or 0
    cpu_name = "Logical processors"
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                raw = str(winreg.QueryValueEx(key, "ProcessorNameString")[0])
            cpu_name = " ".join(raw.split())
            if len(cpu_name) > 26:
                cpu_name = cpu_name[:25].rstrip() + "…"
        except OSError:
            pass
    insights.append(("🧠", f"{cores} Cores", cpu_name))

    # -- RAM ------------------------------------------------
    ram_value, ram_caption = "—", "Installed memory"
    if sys.platform == "win32":
        try:
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            status = _MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                ram_value = f"{status.ullTotalPhys / 2**30:.1f} GB"
                ram_caption = f"{status.dwMemoryLoad}% in use"
        except OSError:
            pass
    insights.append(("💾", ram_value, ram_caption))
    return insights


# ============================================================
#  PAGES
# ============================================================
class WelcomePage(QWidget):
    """Landing view: breathing brand mark, system insight dashboard and
    the status chips grouped inside a unified glass dock."""

    INSIGHT_W, INSIGHT_H = 200, 86   # mini-card footprint
    INSIGHT_GAP = 14
    DOCK_H = 66

    def __init__(self, t: dict, engine_ok: bool, is_admin: bool):
        super().__init__()
        self._chip_meta: list[tuple[QLabel, bool]] = []
        self._insight_frames: list[QFrame] = []
        self._insight_values: list[QLabel] = []
        self._insight_captions: list[QLabel] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 16, 40, 16)
        lay.setSpacing(0)
        lay.addStretch(3)

        # -- brand ------------------------------------------
        self._logo = BreathingIcon("✦", size=110, accent=t["accent"])
        logo_row = QHBoxLayout()
        logo_row.addStretch()
        logo_row.addWidget(self._logo)
        logo_row.addStretch()
        lay.addLayout(logo_row)
        lay.addSpacing(2)

        self._name = QLabel(APP_NAME)
        self._name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._name)
        lay.addSpacing(6)

        self._tag = QLabel("Enterprise-Grade Windows Orchestration")
        self._tag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._tag)
        lay.addSpacing(32)

        # -- system insight dashboard ------------------------
        insights_row = QHBoxLayout()
        insights_row.setSpacing(self.INSIGHT_GAP)
        insights_row.addStretch()
        for icon, value, caption in _system_insights():
            frame = DepthCard(radius=14)
            frame.setObjectName("insight")
            frame.setFixedSize(self.INSIGHT_W, self.INSIGHT_H)
            card = QVBoxLayout(frame)
            card.setContentsMargins(16, 12, 16, 12)
            card.setSpacing(3)

            top = QHBoxLayout()
            top.setSpacing(8)
            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet("font-size: 16px; background: transparent; border: none;")
            top.addWidget(icon_lbl)
            value_lbl = QLabel(value)
            top.addWidget(value_lbl)
            top.addStretch()
            card.addLayout(top)

            caption_lbl = QLabel(caption)
            card.addWidget(caption_lbl)
            card.addStretch()

            self._insight_frames.append(frame)
            self._insight_values.append(value_lbl)
            self._insight_captions.append(caption_lbl)
            insights_row.addWidget(frame)
        insights_row.addStretch()
        lay.addLayout(insights_row)
        lay.addSpacing(26)

        # -- unified glass dock (status chips) ----------------
        self._dock = DepthCard(radius=22)
        self._dock.setObjectName("dock")
        self._dock.setFixedHeight(self.DOCK_H)
        dock_lay = QHBoxLayout(self._dock)
        dock_lay.setContentsMargins(18, 12, 18, 12)
        dock_lay.setSpacing(12)
        for icon, text, ok in (
            ("🗂️", f"{len(CATEGORIES)} Modules", True),
            ("⚙️", f"{total_operations()} Operations", True),
            ("🧠", "Engine Ready" if engine_ok else "Engine Missing", engine_ok),
            ("🔑", "Administrator" if is_admin else "Not Elevated", is_admin),
        ):
            chip = QLabel(f"{icon}  {text}")
            self._chip_meta.append((chip, ok))
            dock_lay.addWidget(chip)

        dock_row = QHBoxLayout()
        dock_row.addStretch()
        dock_row.addWidget(self._dock)
        dock_row.addStretch()
        lay.addLayout(dock_row)
        lay.addSpacing(24)

        self._hint = QLabel("Select a module from the left panel to begin")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._hint)
        lay.addStretch(4)

        self.apply_theme(t)

    def apply_theme(self, t: dict):
        self._logo.apply_theme(t)
        self._name.setStyleSheet(TH.label_qss(t, "hero"))
        self._tag.setStyleSheet(
            TH.label_qss(t, "body") + "font-size: 13px; letter-spacing: 1px;")
        self._hint.setStyleSheet(TH.label_qss(t, "faint"))
        for frame in self._insight_frames:
            frame.setStyleSheet(TH.insight_card_qss(t))
        for lbl in self._insight_values:
            lbl.setStyleSheet(TH.label_qss(t, "value"))
        for lbl in self._insight_captions:
            lbl.setStyleSheet(TH.label_qss(t, "caption"))
        self._dock.setStyleSheet(TH.dock_qss(t))
        for chip, ok in self._chip_meta:
            chip.setStyleSheet(TH.chip_qss(t, ok))


class CategoryPage(QWidget):
    """One category: header (back · title · home) + scrollable card grid.

    The grid is responsive: column count follows the viewport width so a
    card never drops below MIN_CARD_W and clips its copy. Floating at the
    default size reads as a spacious 2-column layout; maximized widescreen
    gets 3 columns; a small floating window falls back to a single,
    fully-readable column."""

    MAX_COLUMNS = 3
    MIN_CARD_W = 340   # narrower than this and descriptions start clipping

    back_requested = Signal()
    home_requested = Signal()
    task_requested = Signal(dict, object)  # (item, GlassCard)

    def __init__(self, category: dict, t: dict):
        super().__init__()
        self.category = category
        self.cards: list[GlassCard] = []
        self._cols = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(16)

        # -- header -------------------------------------------
        head = QHBoxLayout()
        head.setSpacing(14)

        self._back = NavPill("‹  Back", t)
        self._back.clicked.connect(self.back_requested)
        head.addWidget(self._back)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._title = QLabel(f"{category['icon']}  {category['title']}")
        title_col.addWidget(self._title)
        self._tagline = QLabel(category["tagline"])
        title_col.addWidget(self._tagline)
        head.addLayout(title_col)
        head.addStretch()

        self._home = NavPill("⌂  Home", t)
        self._home.clicked.connect(self.home_requested)
        head.addWidget(self._home)
        lay.addLayout(head)

        # -- card grid ----------------------------------------
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        grid_host = QWidget()
        grid_host.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(2, 4, 12, 4)
        self._grid.setSpacing(18)

        for item in category["items"]:
            card = GlassCard(item, category["accent"], t)
            card.clicked.connect(
                lambda it=item, c=card: self.task_requested.emit(it, c))
            self.cards.append(card)
        self._relayout(2)   # safe default; the first resize event corrects it

        self._scroll.setWidget(grid_host)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        lay.addWidget(self._scroll, 1)

        self.apply_theme(t)

    # -- responsive grid ------------------------------------------
    def _columns_for(self, viewport_w: int) -> int:
        return max(1, min(self.MAX_COLUMNS, viewport_w // self.MIN_CARD_W))

    def _relayout(self, cols: int):
        if cols == self._cols:
            return
        self._cols = cols
        for card in self.cards:
            self._grid.removeWidget(card)
        for col in range(self.MAX_COLUMNS):
            self._grid.setColumnStretch(col, 1 if col < cols else 0)
        for row in range(self._grid.rowCount()):
            self._grid.setRowStretch(row, 0)
        for i, card in enumerate(self.cards):
            self._grid.addWidget(card, i // cols, i % cols)
        self._grid.setRowStretch((len(self.cards) + cols - 1) // cols, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout(self._columns_for(self._scroll.viewport().width()))

    def apply_theme(self, t: dict):
        self._back.apply_theme(t)
        self._home.apply_theme(t)
        self._title.setStyleSheet(TH.label_qss(t, "title"))
        self._tagline.setStyleSheet(TH.label_qss(t, "tagline"))
        self._scroll.setStyleSheet(TH.scroll_area_qss(t))
        for card in self.cards:
            card.apply_theme(t)


# ============================================================
#  MAIN WINDOW
# ============================================================
class PulseApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pulse")
        # Min/Max hints keep the frameless window a first-class citizen to
        # the OS: taskbar minimize animation and Win+Up/Down work natively.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        icon_path = _locate_icon()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))
        self._init_geometry()

        # Strong references — Qt/Python will GC these mid-flight otherwise.
        self._thread: QThread | None = None
        self._worker: PowerShellTask | None = None
        self._running_card: GlassCard | None = None
        self._nav_buttons: list[NavButton] = []
        self._status_state = "ready"
        self._glass_applied = False

        self.theme = TH.ThemeManager("dark", self)
        self.theme.changed.connect(self._apply_theme)

        self.cascade = CascadeAnimator(self)
        self.fader = PageFader(self)

        self.ps1_path = self._locate_ps1()
        self.is_admin = self._check_admin()

        self._build_ui()
        self._apply_theme(self.theme.t)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.go_home)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._open_command_palette)
        QTimer.singleShot(300, self._startup_toasts)

    def _init_geometry(self):
        """Screen-aware first launch: size to the monitor instead of a
        hardcoded 1180×740 (which overflowed 1366×768 laptops and small
        high-DPI displays), centered in the available work area. The
        minimum size is likewise clamped so the window can never be
        forced larger than the screen it lives on."""
        desired_w, desired_h = 1180, 760
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(desired_w, desired_h)
            self.setMinimumSize(980, 620)
            return
        avail = screen.availableGeometry()
        w = min(desired_w, avail.width() - 48)
        h = min(desired_h, avail.height() - 48)
        self.setMinimumSize(min(980, avail.width() - 48),
                            min(620, avail.height() - 48))
        self.resize(w, h)
        self.move(avail.center().x() - w // 2, avail.center().y() - h // 2)

    # ============================================================
    #  UI ASSEMBLY
    # ============================================================
    def _build_ui(self):
        t = self.theme.t

        self._shell = QFrame()
        self._shell.setObjectName("shell")
        self.setCentralWidget(self._shell)

        root = QVBoxLayout(self._shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.titlebar = TitleBar(self, t, APP_NAME, APP_VERSION, APP_CHANNEL)
        self.titlebar.theme_toggle_requested.connect(self._toggle_theme_animated)
        root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(*_FLOAT_MARGINS)
        body.setSpacing(20)
        root.addLayout(body, 1)
        self._body = body  # margins flip to _FLUSH_MARGINS in changeEvent
                           # when maximized (native edge-to-edge fit)

        # -- sidebar ------------------------------------------
        self._sidebar = QFrame()
        self._sidebar.setFixedWidth(250)
        side = QVBoxLayout(self._sidebar)
        side.setContentsMargins(16, 24, 16, 18)
        side.setSpacing(8)

        self._section = QLabel("MODULES")
        self._section.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._section.setIndent(10)   # editor-style left-aligned section label
        side.addWidget(self._section)
        side.addSpacing(8)

        for i, cat in enumerate(CATEGORIES):
            btn = NavButton(cat["icon"], cat["title"], cat["accent"], t)
            btn.clicked.connect(lambda checked=False, idx=i: self.open_category(idx))
            self._nav_buttons.append(btn)
            side.addWidget(btn)
        side.addStretch()

        self._exit_btn = QPushButton("✕  Exit")
        self._exit_btn.setFixedHeight(40)
        self._exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._exit_btn.clicked.connect(self.close)
        side.addWidget(self._exit_btn)
        body.addWidget(self._sidebar)

        # -- content ------------------------------------------
        self._content = QFrame()
        content = QVBoxLayout(self._content)
        content.setContentsMargins(24, 18, 24, 16)
        content.setSpacing(12)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; border: none; }")
        self.welcome = WelcomePage(t, bool(self.ps1_path), self.is_admin)
        self.stack.addWidget(self.welcome)
        self.pages: list[CategoryPage] = []
        for cat in CATEGORIES:
            page = CategoryPage(cat, t)
            page.back_requested.connect(self.go_home)
            page.home_requested.connect(self.go_home)
            page.task_requested.connect(self.request_task)
            self.pages.append(page)
            self.stack.addWidget(page)
        content.addWidget(self.stack, 1)

        # -- console header: label · state pill · kill switch --
        console_head = QHBoxLayout()
        console_head.setSpacing(10)
        self._console_label = QLabel("LIVE OUTPUT")
        console_head.addWidget(self._console_label)
        self.state_pill = StatePill(t)
        console_head.addWidget(self.state_pill)
        console_head.addStretch()
        self.stop_btn = QPushButton("■  Stop Task")
        self.stop_btn.setFixedSize(112, 26)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setToolTip(
            "Hard-stop the running task (kills the whole process tree)")
        self.stop_btn.clicked.connect(self._cancel_running_task)
        self.stop_btn.hide()   # visible only while a task runs
        console_head.addWidget(self.stop_btn)
        content.addLayout(console_head)

        self.console = LiveConsole(t)
        self.console.setFixedHeight(170)
        content.addWidget(self.console)

        self.shimmer = ShimmerBar()
        content.addWidget(self.shimmer)

        status = QHBoxLayout()
        status.setSpacing(8)
        self.status_dot = StatusDot("●")
        status.addWidget(self.status_dot)
        self.status_text = QLabel("System Ready")
        status.addWidget(self.status_text)
        status.addStretch()
        grip = QSizeGrip(self._content)
        grip.setStyleSheet("background: transparent;")
        status.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        content.addLayout(status)

        body.addWidget(self._content, 1)
        # Scrim before ToastManager: toasts re-raise themselves on show,
        # so live notifications still surface above an active backdrop.
        self._scrim = Scrim(self._shell)
        self.toasts = ToastManager(self._shell, t)

    # ============================================================
    #  LIVE THEME PIPELINE
    # ============================================================
    def _apply_theme(self, t: dict):
        self._shell.setStyleSheet(TH.shell_qss(t))
        self._sidebar.setStyleSheet(TH.sidebar_qss(t))
        self._content.setStyleSheet(TH.content_qss(t))
        self._section.setStyleSheet(TH.label_qss(t, "section"))
        self._exit_btn.setStyleSheet(TH.exit_button_qss(t))
        self.titlebar.apply_theme(t)
        self.welcome.apply_theme(t)
        for btn in self._nav_buttons:
            btn.apply_theme(t)
        for page in self.pages:
            page.apply_theme(t)
        self.shimmer.set_theme(t)
        self._console_label.setStyleSheet(TH.console_header_qss(t))
        self.state_pill.apply_theme(t)
        self.stop_btn.setStyleSheet(TH.stop_button_qss(t))
        self.console.apply_theme(t)
        self.status_text.setStyleSheet(TH.label_qss(t, "status"))
        self.toasts.apply_theme(t)
        self._scrim.set_theme(t)
        self._set_status(self._status_state, self.status_text.text())

    def _toggle_theme_animated(self):
        """Theme switch with a 220ms cross-fade: a snapshot of the old look
        sits on top and dissolves into the freshly re-skinned UI. One
        transient overlay + opacity effect — steady state stays effect-free
        per the animations.py doctrine."""
        snap = self._shell.grab()
        overlay = QLabel(self._shell)
        overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        overlay.setPixmap(snap)
        overlay.setGeometry(self._shell.rect())
        overlay.show()
        overlay.raise_()

        self.theme.toggle()  # re-skins everything underneath, synchronously

        effect = QGraphicsOpacityEffect(overlay)
        overlay.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", overlay)
        anim.setDuration(160)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        anim.finished.connect(overlay.deleteLater)
        anim.start()

    def _set_status(self, state: str, text: str | None = None):
        """state: ready | busy | ok | err — colors come from live tokens.
        The dot itself breathes only while busy — see widgets.StatusDot."""
        self._status_state = state
        t = self.theme.t
        color = {"ready": t["ok"], "busy": t["warn"],
                 "ok": t["ok"], "err": t["err"]}[state]
        self.status_dot.set_color(color)
        if state == "busy":
            self.status_dot.start_pulse()
        else:
            self.status_dot.stop_pulse()
        if text is not None:
            self.status_text.setText(text)

    # ============================================================
    #  NAVIGATION (cascade on category open, fade on home)
    # ============================================================
    def go_home(self):
        self._select_nav(None)
        if self.stack.currentIndex() != 0:
            self.cascade.stop()
            self.stack.setCurrentIndex(0)
            self.fader.fade_in(self.welcome, rise_px=10)

    def open_category(self, index: int):
        self._select_nav(index)
        page = self.pages[index]
        if self.stack.currentWidget() is page:
            return
        self.cascade.stop()
        self.stack.setCurrentIndex(index + 1)
        # let the layout place the cards, then run the staggered entrance
        QTimer.singleShot(0, lambda p=page: self.cascade.play(p.cards))

    def _select_nav(self, index: int | None):
        for i, btn in enumerate(self._nav_buttons):
            btn.set_selected(i == index)

    # ============================================================
    #  COMMAND PALETTE (Ctrl+K)
    # ============================================================
    def _open_command_palette(self):
        entries = [(item, cat["title"]) for cat in CATEGORIES for item in cat["items"]]
        palette = CommandPalette(self, self.theme.t, entries)
        # Top-anchored VS Code / Slack quick-launcher placement comes from
        # _present_dialog(anchor="top") in the palette's own showEvent.
        if (self._exec_dialog(palette) == QDialog.DialogCode.Accepted
                and palette.chosen_item is not None):
            self.request_task(palette.chosen_item, None)

    # ============================================================
    #  MODAL PRESENTATION — scrim under every dialog
    # ============================================================
    def _body_rect(self) -> QRect:
        """The shell area below the title bar — what a scrim may cover.
        The title bar itself is never covered: the window controls stay
        visible and (via the non-client path) interactive during modals."""
        tb_h = self.titlebar.height()
        return QRect(0, tb_h, self._shell.width(), self._shell.height() - tb_h)

    def _exec_dialog(self, dialog) -> int:
        """exec() any Pulse dialog over a dense backdrop that fully masks
        the card grid / console underneath — no more content bleeding
        through around an open modal."""
        self._scrim.show_over(self._body_rect(), self.isMaximized())
        try:
            return dialog.exec()
        finally:
            self._scrim.dismiss()

    # ============================================================
    #  TASK PIPELINE
    # ============================================================
    def request_task(self, item: dict, card: GlassCard):
        task = item["task"]

        if task.startswith("@"):
            self._run_local_action(task)
            return
        if self._thread is not None and self._thread.isRunning():
            self.toasts.show("info", "A task is already running — please wait.", 3000)
            return
        if not self.ps1_path:
            self.toasts.show("error", f"{PS1_FILENAME} not found — engine unavailable.", 5000)
            return

        app_ids: list[str] | None = None
        office_paths: tuple[str, str] | None = None
        local_installer: tuple[str, str] | None = None
        if item.get("devhub"):
            dialog = DevHubSelectorDialog(self, self.theme.t, DEV_HUB_GROUPS, DEV_HUB_BUNDLES)
            if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
                return
            if dialog.local_installer:
                # A per-tool "⋯" wizard resolved to Path C (a local file) —
                # run the generic single-installer task instead of the bulk
                # InstallDevHub deploy.
                local_installer = dialog.local_installer
                item = {**item, "task": "InstallLocalFile"}
            elif dialog.selected_ids:
                app_ids = dialog.selected_ids
            else:
                self.toasts.show(
                    "info", "No tools were selected — nothing to deploy.", 3500)
                return
        elif item.get("wizard") == "office":
            wizard = OfficeWizardDialog(self, self.theme.t)
            if self._exec_dialog(wizard) != QDialog.DialogCode.Accepted:
                return
            if wizard.task_override:
                # Path A (Automated Cloud Download): the backend resolves
                # its own setup.exe/configuration.xml after downloading, so
                # there are no paths to pass — just a different task name.
                item = {**item, "task": wizard.task_override}
            elif wizard.setup_path and wizard.config_path:
                office_paths = (wizard.setup_path, wizard.config_path)
            else:
                self.toasts.show(
                    "info", "Office installation cancelled — no files were selected.", 3500)
                return
        elif item.get("apps"):
            selector = AppSelectorDialog(self, item, self.theme.t)
            if self._exec_dialog(selector) != QDialog.DialogCode.Accepted:
                return
            if selector.local_installer:
                # A per-app "⋯" wizard resolved to Path C (a local file) —
                # run the generic single-installer task instead of the
                # bulk winget deploy. Same contract as the Dev Hub branch.
                local_installer = selector.local_installer
                item = {**item, "task": "InstallLocalFile"}
            elif selector.selected_ids:
                app_ids = selector.selected_ids
            else:
                self.toasts.show("info", "No apps were selected — nothing to deploy.", 3500)
                return
        elif item.get("confirm"):
            dialog = ConfirmDialog(self, item, self.theme.t)
            if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
                return

        self._start_task(item, card, app_ids, office_paths, local_installer)

    def _start_task(self, item: dict, card: GlassCard | None,
                     app_ids: list[str] | None = None,
                     office_paths: tuple[str, str] | None = None,
                     local_installer: tuple[str, str] | None = None):
        self._running_card = card
        if card is not None:
            card.set_running(True)
        self._set_status("busy", f"Executing: {item['title']} …")
        self.state_pill.set_state("running")
        self.stop_btn.setText("■  Stop Task")
        self.stop_btn.setEnabled(True)
        self.stop_btn.show()
        self.shimmer.start()
        self.console.clear_console()
        self.toasts.show("info", f"Starting: {item['title']}", 2500)

        thread = QThread(self)
        worker = PowerShellTask(
            self.ps1_path, item["task"], timeout=item.get("timeout", DEFAULT_TIMEOUT),
            app_ids=app_ids,
            office_setup=office_paths[0] if office_paths else None,
            office_config=office_paths[1] if office_paths else None,
            local_installer_path=local_installer[1] if local_installer else None)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.output.connect(self.console.put_line)
        worker.finished.connect(self._on_task_finished)
        worker.failed.connect(self._on_task_failed)
        worker.cancelled.connect(self._on_task_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_task_finished(self, result: TaskResult):
        if result.success:
            self.toasts.show("success", result.message, 5000)
            self._set_status("ok", "System Ready")
            self.state_pill.set_state("ok")
        else:
            message = result.message
            if message.lower().startswith("unknown task"):
                message = ("This module needs the updated core.ps1 backend. "
                           "Update src/backend/core.ps1 to enable it.")
            self.toasts.show("error", message, 6000)
            self._set_status("err", "System Ready")
            self.state_pill.set_state("err")
        self._finish_common("ok" if result.success else "err")

    def _on_task_failed(self, message: str):
        self.toasts.show("error", message, 6000)
        self._set_status("err", "System Ready")
        self.state_pill.set_state("err")
        self._finish_common("err")

    def _cancel_running_task(self):
        """Global kill switch. Disabling the button makes it one-shot; the
        worker's cancel() only sets an Event and taskkills by PID, so the
        direct cross-thread call is safe (see helpers.PowerShellTask)."""
        if self._worker is None:
            return
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stopping…")
        self._set_status("busy", "Stopping task…")
        self._worker.cancel()

    def _on_task_cancelled(self):
        self.toasts.show(
            "info", "Task stopped. Re-run it later to complete the operation.", 5000)
        self._set_status("ready", "System Ready")
        self.state_pill.set_state("stopped")
        self._finish_common()

    def _finish_common(self, flash: str | None = None):
        if self._running_card is not None:
            self._running_card.set_running(False)
            if flash:
                self._running_card.flash(flash)
            self._running_card = None
        self.shimmer.stop()
        self.stop_btn.hide()

    def _on_thread_finished(self):
        # Deferred cleanup so Qt never destroys a worker while one of its
        # queued signals is still in flight.
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

    # ============================================================
    #  LOCAL ACTIONS (no PowerShell process)
    # ============================================================
    def _run_local_action(self, task: str):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        localappdata = os.environ.get(
            "LOCALAPPDATA",
            os.path.join(os.path.expanduser("~"), "AppData", "Local"))
        # Newest home first, then the pre-6.1 Desktop locations (including
        # the pre-rebrand v5.x names) — upgraded machines keep working.
        targets = {
            "@open_log": (
                os.path.join(localappdata, "Pulse", "logs", "Pulse_Log.txt"),
                os.path.join(desktop, "Pulse_Log.txt"),
                os.path.join(desktop, "HTCoreArchitecture_Log.txt"),
            ),
            "@open_onedrive_backup": (
                os.path.join(desktop, "Pulse_OneDriveBackup"),
                os.path.join(desktop, "HTCore_OneDriveBackup"),
            ),
        }
        candidates = targets.get(task)
        if candidates is None:
            self.toasts.show("error", f"Unknown local action: {task}", 4000)
            return
        path = next((p for p in candidates if os.path.exists(p)), None)
        if path is None:
            self.toasts.show("info", "Nothing there yet — run an operation first.", 4000)
            return
        try:
            os.startfile(path)  # noqa: S606 - opening a local file/folder for the user
            self.toasts.show("success", f"Opened {os.path.basename(path)}", 3000)
        except OSError as exc:
            self.toasts.show("error", f"Could not open: {exc}", 5000)

    # ============================================================
    #  ENGINE / ENVIRONMENT
    # ============================================================
    def _locate_ps1(self) -> str | None:
        candidates = [
            os.path.join(_SRC_DIR, "backend", PS1_FILENAME),
            os.path.join(_FRONTEND_DIR, PS1_FILENAME),
            os.path.join(os.path.dirname(_SRC_DIR), PS1_FILENAME),
        ]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:  # PyInstaller onefile extraction dir
            candidates.insert(0, os.path.join(meipass, "src", "backend", PS1_FILENAME))
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    @staticmethod
    def _check_admin() -> bool:
        if sys.platform != "win32":
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except OSError:
            return False

    def _startup_toasts(self):
        if not self.ps1_path:
            self.toasts.show("error", f"{PS1_FILENAME} not found next to the app.", 8000)
        else:
            self.toasts.show("success", "Engine ready — all modules loaded.", 2500)
        if not self.is_admin:
            self.toasts.show(
                "info",
                "Not running as Administrator — system tasks may fail. "
                "Right-click → Run as administrator.", 8000)

    # ============================================================
    #  WINDOW EVENTS — native glass, native resize, native corners
    # ============================================================
    def showEvent(self, event):
        super().showEvent(event)
        if not self._glass_applied:
            self._glass_applied = True
            hwnd = int(self.winId())
            TH.apply_blur_behind(hwnd)      # real DWM blur behind the shell
            TH.apply_native_rounding(hwnd, rounded=not self.isMaximized())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toasts.reposition()
        if self._scrim.isVisible():
            self._scrim.setGeometry(self._body_rect())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            # Maximized = edge-to-edge: the shell drops its floating radius
            # and border (see shell_qss) so corners sit flush with the
            # monitor, exactly like a native maximized Win11 window.
            # (`flush`, not `maximized`: QWidget's built-in read-only
            # `maximized` property would swallow the write.)
            flush = self.isMaximized()
            self._shell.setProperty("flush", flush)
            self._shell.style().unpolish(self._shell)
            self._shell.style().polish(self._shell)
            # Removing the border/radius alone just relocates the dead
            # space to the body margins instead of the shell edge — they
            # must collapse too, or "flush" still looks like a floating
            # window with a big empty frame around it.
            self._body.setContentsMargins(*(_FLUSH_MARGINS if flush else _FLOAT_MARGINS))
            # DWM must stop rounding too: on a per-pixel-alpha window the
            # corner pixels DWM shaves off become CLICK-THROUGH holes into
            # whatever sits behind the app — square corners while
            # maximized make every edge pixel opaque and click-owning,
            # exactly like a native maximized window.
            if self._glass_applied:
                TH.apply_native_rounding(int(self.winId()), rounded=not flush)

    # Win32 hit-test codes for the native resize border (WM_NCHITTEST)
    _HT = {"L": 10, "R": 11, "T": 12, "TL": 13, "TR": 14,
           "B": 15, "BL": 16, "BR": 17}
    # Non-client caption-button verdicts. HTMAXBUTTON also summons the
    # Windows 11 Snap Layouts flyout.
    _HT_CAPTION = {"min": 8, "max": 9, "close": 20}
    _HTMAXBUTTON = 9
    _WM_NCHITTEST = 0x0084
    _WM_NCLBUTTONDOWN = 0x00A1
    _WM_NCLBUTTONUP = 0x00A2
    _WM_NCMOUSELEAVE = 0x02A2

    def _caption_hit(self, rect, gx: int, gy: int) -> str | None:
        """Which caption button owns the (physical-pixel, screen-space)
        point — with Fitts-friendly expanded zones, not the bare 40×30
        glyph rects: the strip from the top of the window down to the
        bottom of the buttons, from the minimize button's left edge all
        the way to the window's right edge, split at the midpoints of the
        gaps. Slamming the cursor into the top-right corner region and
        clicking now behaves exactly like a native Windows app.
        Physical-pixel math is window-relative so mixed-DPI multi-monitor
        setups can't skew the mapping."""
        titlebar = self.titlebar
        if not titlebar.isVisible():
            return None
        buttons = titlebar.caption_buttons()
        dpr = self.devicePixelRatioF()

        def phys(btn):
            top_left = btn.mapTo(self, QPoint(0, 0))
            left = rect.left + round(top_left.x() * dpr)
            top = rect.top + round(top_left.y() * dpr)
            return (left, top, left + round(btn.width() * dpr),
                    top + round(btn.height() * dpr))

        min_l, _, min_r, min_b = phys(buttons["min"])
        max_l, _, max_r, max_b = phys(buttons["max"])
        close_l, _, _, close_b = phys(buttons["close"])

        zone_bottom = max(min_b, max_b, close_b) + round(4 * dpr)
        if not (rect.top <= gy < zone_bottom):
            return None
        if gx >= (max_r + close_l) // 2:
            return "close" if gx < rect.right else None
        if gx >= (min_r + max_l) // 2:
            return "max"
        if gx >= min_l - round(2 * dpr):
            return "min"
        return None

    def _over_theme_button(self, rect, gx: int, gy: int) -> bool:
        """The theme toggle stays an ordinary Qt button — the HTCAPTION
        strip must leave a client hole over it or it becomes undraggable
        dead chrome instead of a clickable control."""
        btn = self.titlebar.theme_button()
        if not btn.isVisible():
            return False
        dpr = self.devicePixelRatioF()
        top_left = btn.mapTo(self, QPoint(0, 0))
        left = rect.left + round(top_left.x() * dpr)
        top = rect.top + round(top_left.y() * dpr)
        return (left <= gx < left + round(btn.width() * dpr)
                and top <= gy < top + round(btn.height() * dpr))

    def nativeEvent(self, eventType, message):
        """Native window integration, in two parts:

        1. Native resize borders: the outer 8px goes back to Windows so
           edge/corner resizing uses real cursors, the OS size loop,
           min-size clamping and snap behavior. Everything inside stays
           HTCLIENT. A maximized window has no resize border, matching
           native apps — which also means the caption zones then reach
           the literal top-right screen corner (Fitts corner-slam close).
        2. Non-client caption buttons: WM_NCHITTEST maps generously
           expanded zones over minimize/maximize/close to HTMINBUTTON /
           HTMAXBUTTON / HTCLOSEBUTTON, so a click anywhere in the
           top-right corner region lands — no pixel-perfect aiming.
           HTMAXBUTTON additionally summons the Windows 11 Snap Layouts
           flyout. Windows owns those buttons' mouse events from then on:
           hover is mirrored via titlebar.set_nc_hover() and clicks are
           re-injected from WM_NCLBUTTONUP (the sequence Microsoft's own
           custom-titlebar guidance prescribes).
        """
        if sys.platform == "win32" and eventType == b"windows_generic_MSG":
            # Native messages can arrive while the window is still being
            # constructed — before the title bar exists, fall through to Qt.
            titlebar = getattr(self, "titlebar", None)
            if titlebar is None:
                return super().nativeEvent(eventType, message)
            msg = ctypes.wintypes.MSG.from_address(int(message))

            if msg.message == self._WM_NCHITTEST:
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                rect = ctypes.wintypes.RECT()
                if not ctypes.windll.user32.GetWindowRect(msg.hWnd, ctypes.byref(rect)):
                    return super().nativeEvent(eventType, message)

                # resize borders first (floating only) — same priority
                # order as native windows
                if not self.isMaximized():
                    border = max(4, int(8 * self.devicePixelRatioF()))
                    left = x < rect.left + border
                    right = x >= rect.right - border
                    top = y < rect.top + border
                    bottom = y >= rect.bottom - border
                    code = 0
                    if top and left:
                        code = self._HT["TL"]
                    elif top and right:
                        code = self._HT["TR"]
                    elif bottom and left:
                        code = self._HT["BL"]
                    elif bottom and right:
                        code = self._HT["BR"]
                    elif left:
                        code = self._HT["L"]
                    elif right:
                        code = self._HT["R"]
                    elif top:
                        code = self._HT["T"]
                    elif bottom:
                        code = self._HT["B"]
                    if code:
                        titlebar.set_nc_hover(None)
                        return True, code

                # expanded caption-button zones
                hit = self._caption_hit(rect, x, y)
                titlebar.set_nc_hover(hit)
                if hit is not None:
                    return True, self._HT_CAPTION[hit]

                # the rest of the title-bar strip = native HTCAPTION:
                # OS-driven drag with Aero Snap, double-click maximize,
                # right-click system menu — and, because it bypasses Qt's
                # input routing, it stays LIVE while a modal dialog is
                # open. Only the theme toggle keeps an HTCLIENT hole.
                dpr = self.devicePixelRatioF()
                tb_bottom = rect.top + round(titlebar.height() * dpr)
                if y < tb_bottom and not self._over_theme_button(rect, x, y):
                    return True, 2   # HTCAPTION

            elif (msg.message == self._WM_NCLBUTTONDOWN
                    and msg.wParam in self._HT_CAPTION.values()):
                return True, 0   # consume — no default non-client flicker

            elif msg.message == self._WM_NCLBUTTONUP:
                if msg.wParam == self._HT_CAPTION["min"]:
                    titlebar.set_nc_hover(None)
                    self.showMinimized()
                    return True, 0
                if msg.wParam == self._HT_CAPTION["max"]:
                    titlebar._toggle_max()
                    return True, 0
                if msg.wParam == self._HT_CAPTION["close"]:
                    # The close control works even while a modal dialog is
                    # open (this path bypasses Qt's modal input blocking) —
                    # settle any open dialogs first so their exec() loops
                    # unwind instead of orphaning a floating panel.
                    for widget in QApplication.topLevelWidgets():
                        if isinstance(widget, QDialog) and widget.isVisible():
                            widget.reject()
                    self.close()
                    return True, 0

            elif msg.message == self._WM_NCMOUSELEAVE:
                titlebar.set_nc_hover(None)

        return super().nativeEvent(eventType, message)


# ============================================================
#  ENTRY POINT
# ============================================================
def main() -> int:
    if sys.platform == "win32":
        # Explicit AppUserModelID: without it, running from source groups
        # Pulse under python.exe on the taskbar with Python's icon.
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "HumamTaibeh.Pulse")
        except (OSError, AttributeError):
            pass
    # Fractional per-monitor DPI (125% / 150% / 175% laptops): pass the
    # exact scale factor through instead of rounding to whole integers,
    # so the UI is pixel-crisp and identically proportioned on every
    # display. Must be set before the QApplication exists.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Pulse")
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    icon_path = _locate_icon()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))
    window = PulseApp()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
