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
    QEasingCurve, QEvent, QPropertyAnimation, Qt, QThread, QTimer, Signal,
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
from frontend.menu_structure import CATEGORIES, total_operations  # noqa: E402
from frontend.widgets import (  # noqa: E402
    AppSelectorDialog, BreathingIcon, CommandPalette, ConfirmDialog, GlassCard,
    LiveConsole, NavButton, NavPill, StatePill, TitleBar,
)

# ============================================================
#  APP CONSTANTS
# ============================================================
APP_NAME = "PULSE"
APP_VERSION = "6.1"
PS1_FILENAME = "core.ps1"
DEFAULT_TIMEOUT = 900

# Body-layout margins: comfortable while floating, collapsed to a slim
# comfort gap when maximized/flush so the (now border-less, radius-less)
# shell doesn't leave a dead-space frame around the sidebar/content.
_FLOAT_MARGINS = (18, 6, 18, 14)
_FLUSH_MARGINS = (8, 4, 8, 8)


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
            frame = QFrame()
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
        self._dock = QFrame()
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
    """One category: header (back · title · home) + scrollable card grid."""

    COLUMNS = 3

    back_requested = Signal()
    home_requested = Signal()
    task_requested = Signal(dict, object)  # (item, GlassCard)

    def __init__(self, category: dict, t: dict):
        super().__init__()
        self.category = category
        self.cards: list[GlassCard] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(14)

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
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(2, 2, 10, 2)
        grid.setSpacing(14)

        for i, item in enumerate(category["items"]):
            card = GlassCard(item, category["accent"], t)
            card.clicked.connect(
                lambda it=item, c=card: self.task_requested.emit(it, c))
            self.cards.append(card)
            grid.addWidget(card, i // self.COLUMNS, i % self.COLUMNS)

        for col in range(self.COLUMNS):
            grid.setColumnStretch(col, 1)
        grid.setRowStretch(grid.rowCount(), 1)

        self._scroll.setWidget(grid_host)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        lay.addWidget(self._scroll, 1)

        self.apply_theme(t)

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
        self.setGeometry(140, 80, 1180, 740)
        self.setMinimumSize(1020, 640)

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

        self.titlebar = TitleBar(self, t, APP_NAME, APP_VERSION)
        self.titlebar.theme_toggle_requested.connect(self._toggle_theme_animated)
        root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(*_FLOAT_MARGINS)
        body.setSpacing(18)
        root.addLayout(body, 1)
        self._body = body  # margins flip to _FLUSH_MARGINS in changeEvent
                           # when maximized (native edge-to-edge fit)

        # -- sidebar ------------------------------------------
        self._sidebar = QFrame()
        self._sidebar.setFixedWidth(240)
        side = QVBoxLayout(self._sidebar)
        side.setContentsMargins(14, 24, 14, 20)
        side.setSpacing(9)

        self._section = QLabel("MODULES")
        self._section.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(self._section)
        side.addSpacing(8)

        for i, cat in enumerate(CATEGORIES):
            btn = NavButton(cat["icon"], cat["title"], cat["accent"], t)
            btn.clicked.connect(lambda checked=False, idx=i: self.open_category(idx))
            self._nav_buttons.append(btn)
            side.addWidget(btn)
        side.addStretch()

        self._exit_btn = QPushButton("✕  Exit")
        self._exit_btn.setFixedHeight(44)
        self._exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._exit_btn.clicked.connect(self.close)
        side.addWidget(self._exit_btn)
        body.addWidget(self._sidebar)

        # -- content ------------------------------------------
        self._content = QFrame()
        content = QVBoxLayout(self._content)
        content.setContentsMargins(18, 16, 18, 12)
        content.setSpacing(10)

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
        self.status_dot = QLabel("●")
        status.addWidget(self.status_dot)
        self.status_text = QLabel("System Ready")
        status.addWidget(self.status_text)
        status.addStretch()
        grip = QSizeGrip(self._content)
        grip.setStyleSheet("background: transparent;")
        status.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        content.addLayout(status)

        body.addWidget(self._content, 1)
        self.toasts = ToastManager(self._shell)

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
        """state: ready | busy | ok | err — colors come from live tokens."""
        self._status_state = state
        t = self.theme.t
        color = {"ready": t["ok"], "busy": t["warn"],
                 "ok": t["ok"], "err": t["err"]}[state]
        self.status_dot.setStyleSheet(
            f"color: {color}; font-size: 12px; background: transparent; border: none;")
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
        # Positioned explicitly (not default-centered): near the top of the
        # window, VS Code / Slack quick-launcher style.
        x = self.x() + (self.width() - palette.width()) // 2
        y = self.y() + 110
        palette.move(x, y)
        if palette.exec() == QDialog.DialogCode.Accepted and palette.chosen_item is not None:
            self.request_task(palette.chosen_item, None)

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
        if item.get("apps"):
            selector = AppSelectorDialog(self, item, self.theme.t)
            if selector.exec() != QDialog.DialogCode.Accepted:
                return
            if not selector.selected_ids:
                self.toasts.show("info", "No apps were selected — nothing to deploy.", 3500)
                return
            app_ids = selector.selected_ids
        elif item.get("confirm"):
            dialog = ConfirmDialog(self, item, self.theme.t)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

        self._start_task(item, card, app_ids)

    def _start_task(self, item: dict, card: GlassCard | None,
                     app_ids: list[str] | None = None):
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
            app_ids=app_ids)
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
            self.toasts.show("success", "Engine loaded successfully.", 3000)
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
            TH.apply_native_rounding(hwnd)  # Win11: clip blur to rounded corners

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toasts.reposition()

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

    # Win32 hit-test codes for the native resize border (WM_NCHITTEST)
    _HT = {"L": 10, "R": 11, "T": 12, "TL": 13, "TR": 14,
           "B": 15, "BL": 16, "BR": 17}

    def nativeEvent(self, eventType, message):
        """Hand the outer 8px of the window back to Windows so edge and
        corner resizing is fully native: real cursors, the OS size loop,
        min-size clamping, and snap-consistent behavior. Everything inside
        stays HTCLIENT, so Qt widgets are untouched. A maximized window has
        no resize border, matching native apps."""
        if (sys.platform == "win32" and eventType == b"windows_generic_MSG"
                and not self.isMaximized()):
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                rect = ctypes.wintypes.RECT()
                if ctypes.windll.user32.GetWindowRect(msg.hWnd, ctypes.byref(rect)):
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
                        return True, code
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
    app = QApplication(sys.argv)
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
