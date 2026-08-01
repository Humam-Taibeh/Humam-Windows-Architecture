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
import subprocess
import sys
import time

if sys.platform == "win32":
    import ctypes.wintypes  # MSG / RECT for native window hit-testing

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPropertyAnimation, Qt, QThread,
    QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QPalette, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QGraphicsOpacityEffect,
    QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

# Allow "from utils.helpers import ..." / "from frontend import ..." when
# running as src/frontend/main.py or from a PyInstaller bundle.
_FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_FRONTEND_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from utils import prefs, resources  # noqa: E402
from utils.helpers import (  # noqa: E402
    PowerShellTask, SystemPulseSampler, TaskResult, ToastManager, has_battery,
)
from frontend import theme as TH  # noqa: E402
from frontend.animations import CascadeAnimator, PageFader  # noqa: E402
from frontend.menu_structure import (  # noqa: E402
    CATALOG_BUNDLE_SECTION, CATALOG_BUNDLES, CATEGORIES, SOFTWARE_CATALOG,
    accent_for_task, category_bands, category_operations, find_action_anywhere,
    hub_items, iter_leaf_items, recurring_days, requires_admin,
)
from frontend.widgets import (  # noqa: E402
    ActivationStatusDialog, ActivityDrawer, AmbientGlow,
    BreathingIcon,
    CloseConfirmDialog, CommandPalette, ConfirmDialog, DepthCard,
    ElevatePromptDialog, GlassCard, HealthReportDialog, HubDialog, MeterBar,
    NavButton,
    NavPill, OfficeWizardDialog, PlaybookDialog, PowerHealthDialog,
    PulseDialog, RecentOperationsPanel, RestorePointDialog, RevertChoiceDialog,
    ResponsiveGridHost, ShortcutSheetDialog, SoftwareCatalogDialog,
    StartupManagerDialog, StorageAnalyzerDialog, TitleBar, UpdateCenterDialog,
    refit_dialog,
)
from frontend.playbooks import PlaybookRunner, load_playbooks  # noqa: E402

# ============================================================
#  APP CONSTANTS
# ============================================================
APP_NAME = "PULSE"
# The app version tracks the UI/design-system generation the codebase
# actually is. It had been pinned at 6.1 while the design system moved
# through v7-v10, then at 10.0 through the 10.1/10.2/10.3 releases — so
# the title bar, the sidebar footer and QApplication all reported a
# version no document, changelog entry or bug report matched.
# KEEP IN LOCKSTEP with $Script:ScriptVersion in src/backend/core.ps1;
# tests/test_contract.py fails the build if the two drift again.
APP_VERSION = "10.3"
APP_CHANNEL = "Beta"   # release channel — rendered as a badge, never in prose
PS1_FILENAME = "core.ps1"
DEFAULT_TIMEOUT = 900

# Body-layout margins: comfortable while floating, collapsed to a slim
# comfort gap when maximized/flush so the (now border-less, radius-less)
# shell doesn't leave a dead-space frame around the sidebar/content.
_FLOAT_MARGINS = (20, 8, 20, 16)
_FLUSH_MARGINS = (10, 6, 10, 10)

# ============================================================
#  TWO-WAY TOGGLES (v1.0) — GUI task -> its dispatcher revert case
# ============================================================
# The safely invertible set, and ONLY it. Every entry restores backed-up
# original values (02-Safety.ps1's Restore-* functions, the same code the
# bulk Reset All Tweaks composes). Deliberately absent: the Hibernation
# pair (each card is already the other's revert), UltimatePowerPlan
# (switching schemes is a choice, not a revert), the Remove* tasks
# (reinstalling software is its own explicit action, not a toggle), and
# NetworkOptimization (transient — nothing to revert to).
#
# Literal strings on purpose: tests/test_contract.py's _PROGRAMMATIC
# reachability check reads the Revert* names out of this file, which is
# what keeps a dispatcher case from going quietly dead.
_REVERT_TASKS: dict[str, str] = {
    "DarkMode": "RevertDarkMode",
    "DisableMouseAccel": "RevertDisableMouseAccel",
    "MinimalistTaskbar": "RevertMinimalistTaskbar",
    "ClassicContextMenu": "RevertClassicContextMenu",
    "GameMode": "RevertGameMode",
    "DisableTelemetry": "RevertDisableTelemetry",
    "DisableAdvertisingID": "RevertDisableAdvertisingID",
    "DisableActivityHistory": "RevertDisableActivityHistory",
}


def _locate_icon() -> str | None:
    """assets/pulse.ico — project root in dev, _MEIPASS in the bundle."""
    return resources.find_resource("assets/pulse.ico")


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


def _system_spec_line() -> str:
    """"Windows 11 Pro · Build 26200 · 16 cores · 32.0 GB" — the machine's
    static identity as one caption.

    v1.0: this is what remains of the removed system status strip. The
    strip rendered the same three facts as a 66px band of plaques directly
    above a System Pulse card measuring the same hardware live, which is
    the duplication the redundancy pass removed. As a subtitle to the
    live meters the facts still land — a reader sees "16 cores" right
    above the processor meter — without a second surface claiming them.
    """
    parts = []
    for _icon, value, caption in _system_insights():
        value = (value or "").strip()
        caption = (caption or "").strip()
        if not value or value == "—":
            continue
        # the OS cell carries its build in the caption; the others carry a
        # descriptor ("Logical processors", "62% in use") that the spec
        # line does not want — keep only the build.
        if caption.lower().startswith("build"):
            parts.append(f"{value} · {caption}")
        else:
            parts.append(value)
    return "  ·  ".join(parts) if parts else "System details unavailable"


def due_routines(history: dict) -> list[tuple[str, str]]:
    """[(title, caption)] for every ROUTINE task that is overdue or has
    never been run, worst-overdue first.

    Powers the dashboard's Maintenance & Attention card and mirrors the
    per-card ACTION DUE badge exactly — both read `recurring` from
    menu_structure and the same stored history, so a card badged due and
    this panel can never disagree.
    """
    now = time.time()
    rows: list[tuple[float, str, str]] = []
    for item, _breadcrumb in iter_leaf_items():
        interval = recurring_days(item)
        if interval is None:
            continue
        entry = history.get(item.get("task"))
        last = float(entry.get("last_ts", 0.0)) if entry else 0.0
        if not last:
            rows.append((float("inf"), item.get("title", ""), "never run"))
            continue
        days = (now - last) / 86400.0
        if days >= interval:
            rows.append((days, item.get("title", ""),
                         f"last run {_humanize_days(days)}"))
    rows.sort(key=lambda row: -row[0])
    return [(title, caption) for _overdue, title, caption in rows]


def _humanize_days(days: float) -> str:
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 60:
        return f"{int(days)} days ago"
    return f"{int(days // 30)} months ago"


def _focus_neighbour(cards: list, cols: int, current, direction: str) -> bool:
    """Move keyboard focus to `current`'s neighbour in a `cols`-wide grid.

    Shared by every card grid in the app so arrow traversal behaves
    identically on the dashboard and on a module page. Operates on the
    VISIBLE card list, which is what makes traversal stay correct while a
    filter is narrowing the grid — stepping right from the last match must
    not land on a hidden card.

    Left/right wrap within a row's bounds by clamping (not wrapping to the
    next row), matching how Windows list grids behave; up/down move a whole
    row. Returns False when there is nowhere to go, so the caller can let
    the key fall through to normal tab handling."""
    if current not in cards or cols <= 0:
        return False
    index = cards.index(current)
    row, col = divmod(index, cols)
    if direction == "left":
        target = index - 1 if col > 0 else index
    elif direction == "right":
        target = index + 1 if col < cols - 1 else index
    elif direction == "up":
        target = index - cols if row > 0 else index
    else:  # down
        target = index + cols
        if target >= len(cards):
            # a short final row: land on its last card rather than nothing
            target = len(cards) - 1 if row < (len(cards) - 1) // cols else index
    if target == index or not (0 <= target < len(cards)):
        return False
    cards[target].setFocus(Qt.FocusReason.OtherFocusReason)
    return True


# ============================================================
#  PAGES
# ============================================================
class WelcomePage(QWidget):
    """Landing view — a majestic command-center DASHBOARD, not a splash:

        ┌──────────────────────────────────────────────────────────┐
        │ ✦  PULSE                              Engine Ready         │  hero banner
        │    Enterprise-Grade Windows Orchestration   Administrator  │
        ├──────────────────────────────────────────────────────────┤
        │ 🪟 Windows 11  │  🧠 16 Cores  │  💾 32 GB                  │  telemetry ribbon
        ├──────────────────────────────────────────────────────────┤
        │ EXPLORE MODULES ────────────────────────────────────────  │
        │ ┌────────┐ ┌────────┐ ┌────────┐                          │
        │ │ module │ │ module │ │ module │   … all 6, clickable      │  module launchpad
        │ └────────┘ └────────┘ └────────┘                          │
        └──────────────────────────────────────────────────────────┘

    The QUICK ACTIONS band is the centerpiece and — critically — is NOT a
    repeat of the sidebar. The left rail already navigates the six modules;
    duplicating them here as a grid was redundant. Instead the dashboard
    surfaces the highest-value single OPERATIONS (one per module, full
    accent spectrum) as live cards that RUN on click (action_requested),
    giving the home screen a distinct control-center purpose the nav can't:
    do the most common things instantly, without drilling into a module."""

    ACTION_MIN_W = 250   # responsive column threshold for the quick-action grid
    ACTION_MAX_COLS = 3

    # (category index, task) for each Quick Action — one per module, so the
    # band reads as a full-spectrum control surface. Resolved via
    # menu_structure.find_action (skips any the backend no longer defines).
    # v1.0: task names only, resolved through find_action_anywhere. The old
    # (category_index, task) form silently lost two actions when the module
    # count went from seven to four — see find_action_anywhere's docstring.
    QUICK_ACTIONS = [
        "UpdateSelectedApps",    # Software              — Check for Updates
        "UltimatePowerPlan",     # System & Tweaks       — Ultimate Power Plan
        "CleanCache",            # Maintenance & Security— Aggressive Cache Clean
        "DisableTelemetry",      # System & Tweaks       — Disable Telemetry
        "SystemInfo",            # Utilities & Tools     — System Info Snapshot
        "CreateRestorePoint",    # Maintenance & Security— Create Restore Point
    ]

    # Concise, dashboard-tailored one-liners so a Quick Action reads as a
    # crisp control-surface button, not a dense paragraph (the category page
    # keeps each operation's fuller description). Keyed by task name.
    ACTION_BLURBS = {
        "UpdateSelectedApps": "Scan installed apps and update your picks.",
        # The desktop-only caveat outranks the feature description here: a
        # laptop owner needs to know this one isn't for them BEFORE they
        # read what it does. The category card carries the full wording.
        "UltimatePowerPlan":  "Desktop PCs only — not for laptops/mobile.",
        "CleanCache":         "Wipe temp, Update and system caches.",
        "DisableTelemetry":   "Stop diagnostic data collection.",
        "SystemInfo":         "Hardware, uptime and disk snapshot.",
        "CreateRestorePoint": "A safety checkpoint before big changes.",
    }

    # (item, card) -> PulseApp.request_task — the card rides along so a
    # dashboard action gets the same running-glow + ok/err flash a category
    # card gets (v9.4); object (not GlassCard) keeps this module import-light.
    action_requested = Signal(dict, object)

    def __init__(self, t: dict, engine_ok: bool, is_admin: bool):
        super().__init__()
        self._action_cards: list[GlassCard] = []
        self._cols = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 16, 28, 18)
        root.setSpacing(TH.SPACE["md"])

        # ============ 1. HERO BANNER — identity masthead ==================
        # v1.0: a clean identity band. The Engine/Admin status chips that
        # used to crowd its right edge moved down into the status strip, so
        # every system fact now lives in one place and the masthead reads as
        # a calm wordmark rather than a banner competing with its own
        # metadata. Shorter, too (116 → 96), reclaiming vertical canvas.
        # radius from the scale, not a literal: hero_banner_qss rounds this
        # same surface from RADIUS["panel"], and the two drifted (22 vs 20)
        # for as long as the number was written out here by hand.
        self._hero = DepthCard(radius=TH.RADIUS["panel"], t=t)
        self._hero.setObjectName("heroBanner")
        self._hero.setFixedHeight(96)
        hb = QHBoxLayout(self._hero)
        hb.setContentsMargins(28, 0, 28, 0)
        hb.setSpacing(TH.SPACE["lg"])

        self._logo = BreathingIcon("✦", size=58, accent=t["accent"])
        hb.addWidget(self._logo, 0, Qt.AlignmentFlag.AlignVCenter)

        id_col = QVBoxLayout()
        id_col.setSpacing(3)
        id_col.addStretch()
        self._name = QLabel(APP_NAME)
        id_col.addWidget(self._name)
        self._tag = QLabel("Enterprise-Grade Windows Orchestration")
        id_col.addWidget(self._tag)
        id_col.addStretch()
        hb.addLayout(id_col)
        hb.addStretch()

        # Engine / admin state pills, right-anchored in the masthead.
        # v1.0 REDUNDANCY PASS: these lived in a separate 66px "system
        # status strip" that ALSO carried OS/CPU/RAM — the same machine
        # facts the System Pulse card below reports live. One set of
        # system stats, in one place: the strip is gone, its two session
        # pills come back here (where they sat before v1.0), and every
        # machine metric now belongs to System Pulse.
        self._status_pills: list[tuple[QLabel, bool]] = []
        for text, ok in (
            ("Engine Ready" if engine_ok else "Engine Missing", engine_ok),
            ("Administrator" if is_admin else "Not Elevated", is_admin),
        ):
            pill = QLabel(text)
            pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._status_pills.append((pill, ok))
            hb.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(self._hero)

        # ============ 2. QUICK ACTIONS ====================================
        head = QHBoxLayout()
        head.setSpacing(14)
        self._section = QLabel("QUICK ACTIONS")
        head.addWidget(self._section)
        self._rule = QFrame()
        self._rule.setFixedHeight(1)
        head.addWidget(self._rule, 1)
        root.addLayout(head)

        grid_host = ResponsiveGridHost()
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(0, 2, 0, 0)
        self._grid.setSpacing(TH.SPACE["lg"])
        # the grid re-columns off its OWN width — see ResponsiveGridHost
        grid_host.resized.connect(
            lambda w: self._relayout_actions(self._columns_for(w)))
        for task in self.QUICK_ACTIONS:
            item, accent = find_action_anywhere(task)
            if item is None:
                continue   # backend no longer defines it — skip gracefully
            # DISPLAY copy: a concise blurb and no meta-producing keys, so all
            # six cards read as uniform, crisp action buttons (no stray pill /
            # chevron on the one update_center action). The CLICK still emits
            # the ORIGINAL item, so request_task keeps full behaviour — e.g.
            # 'Check for Updates' still opens the UpdateCenter dialog.
            card_item = {**item, "desc": self.ACTION_BLURBS.get(task, item["desc"])}
            for meta_key in ("update_center", "note", "apps", "devhub"):
                card_item.pop(meta_key, None)
            locked = requires_admin(task) and not is_admin
            card = GlassCard(card_item, accent, t, locked=locked)
            # v10: Quick Actions share the STANDARD card envelope. They used
            # to be capped tighter (104/132) to read as compact buttons, but
            # that cap sits below the 119px the v10 card anatomy needs once a
            # blurb wraps to three lines, so at narrow widths the text was
            # forced outside the card. Their blurbs are short, so they still
            # settle near the minimum and read compact — now by content
            # rather than by a cap that could clip them.
            card.clicked.connect(
                lambda it=item, c=card: self.action_requested.emit(it, c))
            card.navigate.connect(
                lambda direction, c=card: _focus_neighbour(
                    self._action_cards, self._cols, c, direction))
            self._action_cards.append(card)
        self._relayout_actions(3)

        # v10: the Quick Action grid lives in a scroll area, exactly like a
        # CategoryPage's card grid. Without one, a short window had nowhere
        # to put the overflow — Qt resolved the impossible constraint by
        # violating the cards' own minimum heights, crushing them to as
        # little as 17px with their content spilling out. Scrolling is the
        # correct answer to "not enough room"; crushing never is.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(grid_host)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        root.addWidget(self._scroll, 1)

        # ============ 4. SYSTEM HEALTH & ACTIVITY =========================
        # v1.0's answer to the void below the Quick Actions grid: a fixed-
        # height band of two live cards. FIXED height, deliberately — the
        # scroll area above keeps the stretch, so a short window shrinks the
        # (scrollable) action grid rather than crushing the meters, per the
        # v10 "scrolling is the correct answer to not enough room" rule.
        head2 = QHBoxLayout()
        head2.setSpacing(14)
        self._section2 = QLabel("SYSTEM HEALTH & ACTIVITY")
        head2.addWidget(self._section2)
        self._rule2 = QFrame()
        self._rule2.setFixedHeight(1)
        head2.addWidget(self._rule2, 1)
        root.addLayout(head2)

        band = QHBoxLayout()
        band.setSpacing(TH.SPACE["lg"])

        # -- left: SYSTEM PULSE — live utilisation meters ---------------
        # Sampling is kernel32 reads on a 2 s timer that runs ONLY while
        # this page is visible (showEvent/hideEvent below) — the AmbientGlow
        # suspend discipline applied to data instead of paint.
        self._pulse_card = DepthCard(radius=TH.RADIUS["card"], t=t)
        # same card-glass material as the status strip — telemetry_qss is
        # scoped to QFrame#telemetry, so the band cards take that name
        self._pulse_card.setObjectName("telemetry")
        self._pulse_card.setFixedHeight(158)
        pl = QVBoxLayout(self._pulse_card)
        pl.setContentsMargins(TH.SPACE["lg"], TH.SPACE["md"],
                              TH.SPACE["lg"], TH.SPACE["md"])
        pl.setSpacing(2)
        self._pulse_title = QLabel("SYSTEM PULSE")
        pl.addWidget(self._pulse_title)
        # The machine's identity, in the ONE place machine facts live now
        # (v1.0 redundancy pass — see the hero's pill comment). The removed
        # status strip's OS / core-count / RAM facts are folded in here as
        # this card's subtitle, so the live meters below have the static
        # spec they are measured against sitting directly above them.
        self._pulse_spec = QLabel(_system_spec_line())
        self._pulse_spec.setWordWrap(True)
        pl.addWidget(self._pulse_spec)
        pl.addSpacing(2)
        self._meters: dict[str, MeterBar] = {}
        for key, label in (("cpu", "PROCESSOR"), ("mem", "MEMORY"),
                           ("disk", "SYSTEM DRIVE")):
            meter = MeterBar(label, t)
            self._meters[key] = meter
            pl.addWidget(meter)
        pl.addStretch()
        band.addWidget(self._pulse_card, 1)

        # -- right: MAINTENANCE & ATTENTION -----------------------------
        # v1.0 REDUNDANCY PASS: this slot used to be a second "Recent
        # Activity" list — the same prefs.recent_operations() trail the
        # sidebar panel already shows on every page. Two renderings of one
        # list is not a dashboard, it is a duplicate, so the trail now
        # lives ONLY in the sidebar and this card answers a question
        # nothing else in the app did: which ROUTINE tasks are overdue?
        #
        # That also completes the recurring-task story (menu_structure's
        # `recurring` key): the card badge tells you a single task is due,
        # and this panel tells you at a glance whether anything is.
        self._maint_card = DepthCard(radius=TH.RADIUS["card"], t=t)
        self._maint_card.setObjectName("telemetry")
        self._maint_card.setFixedHeight(158)
        al = QVBoxLayout(self._maint_card)
        al.setContentsMargins(TH.SPACE["lg"], TH.SPACE["md"],
                              TH.SPACE["lg"], TH.SPACE["md"])
        al.setSpacing(2)
        self._maint_title = QLabel("MAINTENANCE & ATTENTION")
        al.addWidget(self._maint_title)
        self._maint_empty = QLabel("")
        self._maint_empty.setWordWrap(True)
        al.addWidget(self._maint_empty)
        self._rows_lay = QVBoxLayout()
        self._rows_lay.setContentsMargins(0, 2, 0, 0)
        self._rows_lay.setSpacing(2)
        al.addLayout(self._rows_lay)
        al.addStretch()
        band.addWidget(self._maint_card, 1)
        root.addLayout(band)

        self._maint_rows: list[QLabel] = []
        self._t = t
        self.refresh_maintenance()

        self._pulse_sampler = SystemPulseSampler()
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(2000)
        self._pulse_timer.timeout.connect(self._tick_pulse)

        self.apply_theme(t)

    # -- system pulse lifecycle: sample only while the page is shown ----
    def showEvent(self, e):
        super().showEvent(e)
        self._tick_pulse()          # prime immediately (CPU fills on tick 2)
        self._pulse_timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._pulse_timer.stop()

    def _tick_pulse(self):
        s = self._pulse_sampler.sample()
        cpu = s["cpu"]
        self._meters["cpu"].set_value(
            cpu, f"{round(cpu * 100)}%" if cpu is not None else "—")
        self._meters["mem"].set_value(s["mem"], s["mem_text"])
        self._meters["disk"].set_value(s["disk"], s["disk_text"])

    # -- maintenance & attention ----------------------------------------
    #: Rows the card can show before it would overflow its fixed height.
    MAINT_ROWS = 3

    def refresh_maintenance(self):
        """Rebuild the overdue-routine list from stored run history.

        Read straight from prefs rather than cached: the same honesty rule
        the tweak probe follows — a task's last-run time can change from
        under us (another Pulse window, a cleared history), so the answer
        is always recomputed from storage."""
        history = prefs.task_history()
        due = due_routines(history)
        for row in self._maint_rows:
            self._rows_lay.removeWidget(row)
            row.deleteLater()
        self._maint_rows = []

        if not due:
            self._maint_empty.setText(
                "All routine maintenance is up to date." if history
                else "No maintenance run yet — Cache Clean and a Restore "
                     "Point are good first steps.")
            self._maint_empty.setVisible(True)
        else:
            self._maint_empty.setVisible(False)
            for title, caption in due[: self.MAINT_ROWS]:
                row = QLabel(f"●  {title}  —  {caption}")
                row.setWordWrap(False)
                self._maint_rows.append(row)
                self._rows_lay.addWidget(row)
            extra = len(due) - self.MAINT_ROWS
            if extra > 0:
                more = QLabel(f"    +{extra} more due")
                self._maint_rows.append(more)
                self._rows_lay.addWidget(more)
        self._style_maintenance()

    def _style_maintenance(self):
        t = self._t
        self._maint_empty.setStyleSheet(TH.label_qss(t, "caption"))
        for row in self._maint_rows:
            row.setStyleSheet(
                f"color: {t['warn']}; font-size: 11px; font-weight: 600;"
                "background: transparent; border: none;")

    def action_cards(self) -> list[GlassCard]:
        """The dashboard's Quick Action cards — the applied-state probe
        badges these too, so a tweak shown on both the dashboard and its
        category page reports identically in both places."""
        return list(self._action_cards)

    # -- responsive quick-action grid ---------------------------------
    def _columns_for(self, width: int) -> int:
        """v10: content-aware, matching CategoryPage._columns_for. The old
        version divided by a flat ACTION_MIN_W (250) with no regard for what
        the cards actually need, so once a card's real content minimum
        exceeded that constant the grid confidently laid out a column count
        that squeezed cards below their minimum width and clipped them."""
        gap = self._grid.spacing()
        widest = max((c.minimumSizeHint().width() for c in self._action_cards),
                     default=self.ACTION_MIN_W)
        unit = max(self.ACTION_MIN_W, widest)
        return max(1, min(self.ACTION_MAX_COLS, (width + gap) // (unit + gap)))

    def _relayout_actions(self, cols: int):
        if cols == self._cols:
            return
        self._cols = cols
        for card in self._action_cards:
            self._grid.removeWidget(card)
        for col in range(self.ACTION_MAX_COLS):
            self._grid.setColumnStretch(col, 1 if col < cols else 0)
        n_rows = (len(self._action_cards) + cols - 1) // cols
        # v1.0: content rows take NO stretch, and one trailing row takes it
        # all. The old version stretched every content row equally, so on a
        # tall window two rows of cards were flung to opposite ends with a
        # canyon of empty space between them — the "excessive empty space"
        # the redesign called out. Anchoring the cards to the top with even
        # gutters and pushing all slack below reads as a filled, deliberate
        # grid instead.
        for row in range(max(self._grid.rowCount(), n_rows) + 1):
            self._grid.setRowStretch(row, 1 if row == n_rows else 0)
        for i, card in enumerate(self._action_cards):
            self._grid.addWidget(card, i // cols, i % cols)

    # Column counts are driven by ResponsiveGridHost.resized (see the grid
    # construction above), so no resizeEvent/showEvent width guessing here.

    def apply_theme(self, t: dict):
        self._logo.apply_theme(t)
        self._hero.setStyleSheet(TH.hero_banner_qss(t))
        self._hero.set_theme(t)
        # authoritative masthead wordmark — larger and tighter than the old
        # spread-out splash "hero" role
        self._name.setStyleSheet(
            f"color: {t['text']}; font-size: 34px; font-weight: 800;"
            "letter-spacing: 2px; background: transparent; border: none;")
        self._tag.setStyleSheet(
            TH.label_qss(t, "tagline") + "font-size: 12px; letter-spacing: 1px;")
        for pill, ok in self._status_pills:
            pill.setStyleSheet(TH.strip_status_qss(t, ok))

        self._section.setStyleSheet(TH.label_qss(t, "section"))
        self._rule.setStyleSheet(TH.hub_group_rule_qss(t, t["accent"]))
        self._scroll.setStyleSheet(TH.scroll_area_qss(t))
        for card in self._action_cards:
            card.apply_theme(t)

        # -- system health & maintenance band -----------------------------
        self._t = t
        self._section2.setStyleSheet(TH.label_qss(t, "section"))
        self._rule2.setStyleSheet(TH.hub_group_rule_qss(t, t["accent2"]))
        for card_frame in (self._pulse_card, self._maint_card):
            card_frame.setStyleSheet(TH.telemetry_qss(t))
            card_frame.set_theme(t)
        for title in (self._pulse_title, self._maint_title):
            title.setStyleSheet(TH.label_qss(t, "section"))
        self._pulse_spec.setStyleSheet(TH.label_qss(t, "caption"))
        for meter in self._meters.values():
            meter.set_theme(t)
        self._style_maintenance()


class CategoryPage(QWidget):
    """One category: header (back · title · home) + scrollable card grid.

    The grid is responsive: column count follows the viewport width so a
    card never drops below MIN_CARD_W and clips its copy. Floating at the
    default size reads as a spacious 2-column layout; maximized widescreen
    gets 3 columns; a small floating window falls back to a single,
    fully-readable column."""

    MAX_COLUMNS = 4
    MIN_CARD_W = 288   # v9.1: tighter cards → more columns, higher density

    # SPARSE MODE — pages with this many cards or fewer trade the
    # fill-the-canvas grid for a centered, width-capped row. The
    # equal-stretch grid is the right answer for 3+ cards; for two it
    # produced two ~700px slabs floating mid-canvas with a void on every
    # side (see the v1.0 audit renders). Centered at a readable width and
    # top-anchored, the same two cards read as a deliberate composition.
    #
    # v1.0+ : 3 -> 2. The threshold was tuned for the 2-card Automation
    # page, which no longer exists (it merged into Utilities & Tools). At
    # 3 the only page it still caught was Software Management — a page it
    # was never designed for, and one whose hero + 2 cards read better in
    # the normal balanced grid. No page has 2 cards today, so this is now
    # a dormant guard for a future short page rather than live styling.
    SPARSE_MAX_CARDS = 2
    SPARSE_CARD_W = 430

    #: Grid row the filtered-empty label is parked on — past any plausible
    #: number of band-header + card rows a category page can produce.
    _EMPTY_ROW = 900

    #: (label, badge-state key) for the header's status filter. "" is the
    #: unfiltered default; every other key is a state GlassCard can badge
    #: (see GlassCard._STATE_BADGES), so no option can be a dead end.
    FILTERS = [
        ("All operations", ""),
        ("Applied", "applied"),
        ("Not applied", "default"),
        ("Modified", "mixed"),
        ("Action due", "due"),
    ]

    home_requested = Signal()
    task_requested = Signal(dict, object)  # (item, GlassCard)

    def __init__(self, category: dict, t: dict):
        super().__init__()
        self.category = category
        self.cards: list[GlassCard] = []
        self._visible: list[GlassCard] = []
        #: (header_widget | None, cards) per section band, render order.
        self._bands: list[tuple[QWidget | None, list[GlassCard]]] = []
        self._t = t
        self._cols = 0
        self._applied_unit = 0     # see _relayout / _sparse_unit

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(16)

        # -- header: breadcrumb trail -------------------------
        # v8 navigation doctrine: a single, depth-aware breadcrumb path —
        # `⌂ Home  ›  Module` — replaces the old redundant Back+Home pill
        # pair (both did the same thing on a two-level app, so "Back" on a
        # top-level page pointed nowhere the sidebar didn't already reach).
        # Only the HOME crumb is interactive; the trailing crumb is the
        # current location, led by the module's own accent rail — the exact
        # Finder / VS Code path-bar pattern, which scales cleanly if the app
        # ever nests deeper (each new level just appends another crumb).
        head = QHBoxLayout()
        head.setSpacing(10)

        self._home = NavPill("⌂  Home", t, width=88)
        self._home.setToolTip("Back to the welcome screen")
        self._home.clicked.connect(self.home_requested)
        head.addWidget(self._home)

        self._crumb_sep = QLabel("›")
        self._crumb_sep.setFixedWidth(10)
        self._crumb_sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._crumb_sep)
        head.addSpacing(4)

        # the current-location crumb: a short vertical rail in the module's
        # own accent leads the title — the same 'you are here, and this is
        # its color' cue the sidebar's active-rail uses.
        self._accent_rail = QFrame()
        self._accent_rail.setFixedWidth(3)
        self._accent_rail.setFixedHeight(34)
        head.addWidget(self._accent_rail)
        head.addSpacing(2)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._title = QLabel(category["title"])
        title_col.addWidget(self._title)
        self._tagline = QLabel(category["tagline"])
        title_col.addWidget(self._tagline)
        head.addLayout(title_col)
        head.addStretch()

        # -- v1.0 STATUS filter: the header's right-hand side ------------
        # This was a free-text "Filter…" box, which sat on screen at the
        # same time as the sidebar's "Search everything…" doorway and left
        # two inputs competing to answer the same question. Text search is
        # now unambiguously GLOBAL (one implementation, the Ctrl+K palette,
        # which already searches every app, tweak and tool); this control
        # does the thing the palette cannot — narrow the page you are on by
        # the STATE its cards are in.
        #
        # The options are exactly the badge states the app can actually
        # produce, so a filter can never present a category that renders
        # empty for a state nothing ever reports.
        self._filter = QComboBox()
        self._filter.setFixedSize(190, 32)
        self._filter.setCursor(Qt.CursorShape.PointingHandCursor)
        for label, key in self.FILTERS:
            self._filter.addItem(label, key)
        self._filter.currentIndexChanged.connect(lambda _i: self.refresh_filter())
        head.addWidget(self._filter, 0, Qt.AlignmentFlag.AlignVCenter)

        self._count_chip = QLabel()
        self._count_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._count_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(head)

        # -- card grid ----------------------------------------
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        grid_host = ResponsiveGridHost()
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(2, 4, 12, 4)
        self._grid.setSpacing(TH.SPACE["lg"])
        # the grid re-columns off its OWN width — see ResponsiveGridHost
        grid_host.resized.connect(lambda w: self._relayout(self._columns_for(w)))

        # SECTION BANDS (v1.0+): one band per titled group, or a single
        # untitled band for a flat category — see menu_structure.
        # category_bands. Cards stay in ONE flat self.cards list in render
        # order, so filtering, badge refresh and arrow-key navigation are
        # completely unaware that bands exist; only _relayout draws them.
        idx = 0
        for band_title, band_items in category_bands(category):
            band_cards: list[GlassCard] = []
            for item in band_items:
                # v7 bento: the first card of a landing page (Software
                # Management) is the featured hero — squircle + Aurora lit
                # edge on the top elevation tier. Reserved for the two card
                # kinds that OPEN SOMETHING rather than acting immediately —
                # a hub container or the software catalog — so dense action
                # pages still get the balanced fill grid and no destructive
                # one-click tweak is ever dressed as the page's centrepiece.
                featured = idx == 0 and bool(item.get("hub") or item.get("catalog"))
                card = GlassCard(item, category["accent"], t, featured=featured)
                card.clicked.connect(
                    lambda it=item, c=card: self.task_requested.emit(it, c))
                card.navigate.connect(
                    lambda direction, c=card: _focus_neighbour(
                        self._visible, self._cols, c, direction))
                self.cards.append(card)
                band_cards.append(card)
                idx += 1
            header = self._band_header(band_title, t) if band_title else None
            self._bands.append((header, band_cards))
        # Everything below re-columns over VISIBLE cards only, so filtering
        # reflows the grid instead of leaving holes where hidden cards were.
        self._visible = list(self.cards)
        # Page-level, not filter-level: filtering a dense page down to two
        # matches must NOT recentre it mid-keystroke — sparse is a property
        # of what the page is, not of what a query left showing.
        self._sparse = len(self.cards) <= self.SPARSE_MAX_CARDS
        self._relayout(2)   # safe default; the first resize event corrects it

        # Empty state — a filter that matches nothing must say so; a blank
        # grid is indistinguishable from a broken page.
        self._empty = QLabel("No operations match that filter.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        # Parked on a row far below any real content. It used to sit at
        # MAX_COLUMNS+1 (row 5), which was safely past the end only while a
        # page was a single unbanded block; a banded page interleaves header
        # rows with card rows and reaches row 5 easily, which would have
        # dropped the empty-state label into the middle of the grid.
        self._grid.addWidget(self._empty, self._EMPTY_ROW, 0, 1, self.MAX_COLUMNS)

        self._scroll.setWidget(grid_host)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        lay.addWidget(self._scroll, 1)

        self.apply_theme(t)

    def _band_header(self, title: str, t: dict) -> QWidget:
        """A section band's header: an accent-tinted title plus a 1px rule
        fading out to the right.

        Byte-for-byte the same construction a grouped HubDialog uses
        (hub_group_header_qss / hub_group_rule_qss) — a band on a page and
        a group inside a hub are the same idea at two scales, and giving
        them two different looks would say they were different things.

        Returned as ONE container widget so the grid can add, remove and
        hide the title and its rule as a single unit; hiding them
        separately is how a filtered-empty band leaves a stray rule
        floating over the cards of the band below it.
        """
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        label = QLabel(title)
        label.setObjectName("bandTitle")
        row.addWidget(label)
        rule = QFrame()
        rule.setObjectName("bandRule")
        rule.setFixedHeight(1)
        row.addWidget(rule, 1)
        return host

    # -- responsive grid ------------------------------------------
    def _columns_for(self, viewport_w: int) -> int:
        """Column count that ACTUALLY fits. Two guards beyond the naive
        `viewport // MIN_CARD_W`: (1) it is spacing-aware — N columns need
        N·MIN_CARD_W plus (N-1) gaps — and (2) it never returns more columns
        than the widest card's real content minimum allows, so a card can
        never be squeezed below its minimum and pushed off the right edge
        (the v9.1 density pass exposed this: note-badge cards had a wide
        minimum that overflowed a 3-up grid). The result is dense where the
        content permits and gracefully drops a column where it doesn't."""
        gap = self._grid.spacing()
        widest = max((c.minimumSizeHint().width() for c in self.cards),
                     default=self.MIN_CARD_W)
        # sparse pages column against their fixed display width, so the
        # 2-card row drops to a single column exactly when two capped
        # cards genuinely no longer fit
        floor = self.SPARSE_CARD_W if self._sparse else self.MIN_CARD_W
        unit = max(floor, widest)
        fits = (viewport_w + gap) // (unit + gap)
        return max(1, min(self.MAX_COLUMNS, fits))

    # -- filtering -------------------------------------------------
    def refresh_filter(self):
        """Re-apply the current status filter.

        Called both when the user changes the dropdown AND whenever card
        badges are re-decided (main._refresh_card_badges): the filter
        selects on badge state, so a probe result arriving after the user
        picked "Action due" has to reflow the grid or the page would keep
        showing a stale selection."""
        state = self._filter.currentData() or ""
        self._visible = [
            card for card in self.cards
            if not state or card.state() == state
        ]
        shown = set(id(c) for c in self._visible)
        for card in self.cards:
            card.setVisible(id(card) in shown)
        self._empty.setText(
            "No operations in this module are "
            f"{self._filter.currentText().lower()}.")
        self._empty.setVisible(bool(state) and not self._visible)
        # force a rebuild: the column count may not change, but WHICH cards
        # occupy which cells certainly has
        self._cols = 0
        self._relayout(self._columns_for(self._grid_available_width()))
        self._sync_count_chip()

    def _grid_available_width(self) -> int:
        host = self._grid.parentWidget()
        margins = self._grid.contentsMargins()
        return host.width() - margins.left() - margins.right() if host else 0

    def _sync_count_chip(self):
        total = category_operations(self.category)
        filtering = bool(self._filter.currentData())
        if filtering:
            self._count_chip.setText(f"{len(self._visible)} OF {len(self.cards)}")
        else:
            self._count_chip.setText(
                f"{total} OPERATION{'S' if total != 1 else ''}")
        self._count_chip.setStyleSheet(TH.count_chip_qss(
            self._t, TH.resolve_accent(self._t, self.category["accent"]),
            filtered=filtering))

    def _relayout(self, cols: int):
        # A sparse page also rebuilds when its shared column WIDTH changes,
        # not only its column COUNT. Card minimums are resolved lazily by
        # Qt, so the first pass after construction reads a smaller minimum
        # than the cards finally want; with a count-only guard that stale
        # width was latched forever and the row shipped mismatched tiles.
        unit = self._sparse_unit() if self._sparse else 0
        if cols == self._cols and unit == self._applied_unit:
            return
        self._cols = cols
        self._applied_unit = unit
        for card in self.cards:
            self._grid.removeWidget(card)
        for header, _cards in self._bands:
            if header is not None:
                self._grid.removeWidget(header)
        if self._sparse:
            self._relayout_sparse(cols, unit)
            return
        for col in range(self.MAX_COLUMNS + 2):
            # +2 clears sparse-mode leftovers if a page ever flips modes —
            # gutter stretches and minimum widths are sparse-only state
            self._grid.setColumnStretch(col, 0)
            self._grid.setColumnMinimumWidth(col, 0)
        for col in range(self.MAX_COLUMNS):
            self._grid.setColumnStretch(col, 1 if col < cols else 0)
        # v1.0: content rows take NO stretch and one trailing row takes it
        # all — the dashboard's rule (WelcomePage._relayout_actions), now
        # shared so every grid in the app anchors the same way.
        #
        # This replaces v7's equal-stretch-per-occupied-row, which existed
        # to stop a short grid top-anchoring above a void. It solved that
        # for a FULL page and created the mirror-image problem for a short
        # one: cards are height-capped (CARD_MAX_H), so a stretched row
        # cannot grow — it just centres its cards inside the slack. With
        # the v1.0 status filter a page can now show three cards out of
        # eleven at any time, and those three floated in the middle of the
        # canvas with dead space above AND below. Anchoring to the top and
        # pushing all slack below reads as a deliberate result set.
        shown = {id(c) for c in self._visible}
        row = 0
        for header, band_cards in self._bands:
            visible_here = [c for c in band_cards if id(c) in shown]
            # A band header lives only as long as one of its OWN cards
            # does. Filtering to "Action due" can empty three of four
            # bands, and a surviving title over the next band's cards
            # mislabels them — worse than the wall the bands replaced.
            if header is not None:
                header.setVisible(bool(visible_here))
                if visible_here:
                    self._grid.addWidget(header, row, 0, 1, self.MAX_COLUMNS)
                    row += 1
            for i, card in enumerate(visible_here):
                self._grid.addWidget(card, row + i // cols, i % cols)
            if visible_here:
                row += (len(visible_here) + cols - 1) // cols
        # Content rows take NO stretch and one trailing row takes it all —
        # the dashboard's rule (WelcomePage._relayout_actions), shared so
        # every grid in the app anchors the same way.
        #
        # This replaces v7's equal-stretch-per-occupied-row, which existed
        # to stop a short grid top-anchoring above a void. It solved that
        # for a FULL page and created the mirror-image problem for a short
        # one: cards are height-capped (CARD_MAX_H), so a stretched row
        # cannot grow — it just centres its cards inside the slack. With
        # the status filter a page can show three cards out of eleven at
        # any time, and those three floated in the middle of the canvas
        # with dead space above AND below. Anchoring to the top and pushing
        # all slack below reads as a deliberate result set.
        for r in range(max(self._grid.rowCount(), row) + 1):
            self._grid.setRowStretch(r, 1 if r == row else 0)

    def _sparse_unit(self) -> int:
        """The ONE column width every sparse card shares.

        A column minimum is a floor, not a size: an unstretched column
        still grows to the widest sizeHint it contains. The Software
        Catalog hero carries a longer description than the cards beside
        it, so only ITS column grew and a row meant to read as a set of
        matching tiles shipped at 526px next to 430px.

        Measured off sizeHint, NOT minimumSizeHint — the minimum is what a
        card can be squeezed to (~214px, with its description wrapped
        hard), which is not what the column actually resolves to and left
        the mismatch in place."""
        widest = max((c.sizeHint().width() for c in self.cards),
                     default=self.SPARSE_CARD_W)
        return max(self.SPARSE_CARD_W, widest)

    def _relayout_sparse(self, cols: int, unit: int):
        """Centered, equal-width composition for a page of ≤3 cards: the
        cards sit in equal fixed-width columns between two stretch
        gutters, top-anchored with the slack below (the dashboard's v1.0
        row rule) — never two slabs stretched across the full canvas,
        never a row floating in the vertical middle."""
        n = max(1, min(cols, len(self._visible) or 1))
        for col in range(self.MAX_COLUMNS + 2):
            self._grid.setColumnStretch(col, 0)
            self._grid.setColumnMinimumWidth(col, 0)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(n + 1, 1)
        for col in range(1, n + 1):
            self._grid.setColumnMinimumWidth(col, unit)
        n_rows = (len(self._visible) + n - 1) // n
        for row in range(max(self._grid.rowCount(), n_rows) + 1):
            self._grid.setRowStretch(row, 1 if row == n_rows else 0)
        for i, card in enumerate(self._visible):
            self._grid.addWidget(card, i // n, 1 + i % n)

    # Column counts are driven by ResponsiveGridHost.resized (see the grid
    # construction above): the width that chooses the column count IS the
    # width the cards are laid out in, so the two can never disagree. This
    # replaces the old resizeEvent/showEvent pair, which measured the page
    # and the scroll viewport respectively — two different numbers, one of
    # them lagging a layout pass behind the other.

    def focus_filter(self):
        """Ctrl+Shift+F target — open the status dropdown.

        Plain Ctrl+F no longer lands here: with page-level text search
        folded into the global palette (v1.0), the muscle-memory "find"
        keys belong to the one search the app has. This shortcut reaches
        the status filter, which is a different question."""
        self._filter.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._filter.showPopup()

    def apply_theme(self, t: dict):
        self._t = t
        accent = TH.resolve_accent(t, self.category["accent"])
        self._filter.setStyleSheet(TH.filter_combo_qss(t, accent))
        self._empty.setStyleSheet(TH.empty_state_qss(t))
        self._sync_count_chip()
        self._home.apply_theme(t)
        self._crumb_sep.setStyleSheet(
            f"color: {t['text_faint']}; font-size: 17px; font-weight: 400;"
            "background: transparent; border: none;")
        self._accent_rail.setStyleSheet(
            f"background: {TH.resolve_accent(t, self.category['accent'])};"
            "border: none; border-radius: 2px;")
        self._title.setStyleSheet(TH.label_qss(t, "title"))
        self._tagline.setStyleSheet(TH.label_qss(t, "tagline"))
        self._scroll.setStyleSheet(TH.scroll_area_qss(t))
        for header, _cards in self._bands:
            if header is None:
                continue
            title = header.findChild(QLabel, "bandTitle")
            if title is not None:
                title.setStyleSheet(TH.hub_group_header_qss(t, accent))
            rule = header.findChild(QFrame, "bandRule")
            if rule is not None:
                rule.setStyleSheet(TH.hub_group_rule_qss(t, accent))
        for card in self.cards:
            card.apply_theme(t)


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    """WM_NCCALCSIZE's lParam payload. rgrc[0] is the proposed new client
    rect (in, then out) — writing it back unchanged is what collapses the
    non-client frame to nothing. See PulseApp.nativeEvent."""
    _fields_ = [("rgrc", ctypes.wintypes.RECT * 3),
                ("lppos", ctypes.c_void_p)]


# ============================================================
#  MAIN WINDOW
# ============================================================
class PulseApp(QMainWindow):
    def __init__(self):
        super().__init__()
        # Must be the very first assignment: Qt can deliver events (notably
        # WindowStateChange, from restoreGeometry below) while __init__ is
        # still running, and the handlers guard on this flag.
        self._ui_ready = False
        # True only between WM_ENTERSIZEMOVE/WM_EXITSIZEMOVE — see nativeEvent.
        self._in_size_move = False
        self.setWindowTitle("Pulse")
        # Min/Max hints keep the frameless window a first-class citizen to
        # the OS: taskbar minimize animation and Win+Up/Down work natively.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint)
        # NO WA_TranslucentBackground. It made the top-level window
        # WS_EX_LAYERED (per-pixel alpha, software-composited), which is
        # what produced the launch-time "dark semi-transparent blurred
        # box", the invisible sections, and the tearing/ghosting during
        # drag and resize — a layered window has to re-upload its whole
        # alpha surface on every move and repaint. The shell paints an
        # opaque gradient over every pixel anyway (theme.shell_qss), so
        # the alpha channel bought nothing but glitches. Rounded corners
        # and the frame border now come from DWM itself
        # (theme.apply_native_rounding), which is what Windows 11 apps do.
        # The opaque base colour is set per-theme in _apply_theme.
        icon_path = _locate_icon()
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))
        self._init_geometry()

        # Strong references — Qt/Python will GC these mid-flight otherwise.
        self._thread: QThread | None = None
        self._worker: PowerShellTask | None = None
        self._running_card: GlassCard | None = None
        self._running_item: dict | None = None
        self._running_accent = ""
        self._run_started_at: float | None = None
        # Playbook orchestration (v10.3). Held so a second playbook — or a
        # single task — cannot start on top of a run already in flight.
        self._playbook_runner = None
        self._playbook_dialog = None
        self._probe_thread: QThread | None = None
        self._probe_worker: PowerShellTask | None = None
        self._tweak_state: dict = {}
        self._nav_buttons: list[NavButton] = []
        self._status_state = "ready"
        self._glass_applied = False

        # v10: the chosen theme survives a restart (was hardcoded "dark",
        # so switching to light had to be redone on every launch).
        self.theme = TH.ThemeManager(prefs.theme_mode("dark"), self)
        self.theme.changed.connect(self._apply_theme)
        self.theme.changed.connect(
            lambda t: prefs.set_theme_mode(t["name"]))

        self.cascade = CascadeAnimator(self)
        self.fader = PageFader(self)

        self.ps1_path = self._locate_ps1()
        self.is_admin = self._check_admin()

        self._build_ui()
        self._ui_ready = True
        # Catch up on any window state restored before the widgets existed
        # (a geometry saved while maximized comes back maximized here).
        self._sync_window_state()
        self._apply_theme(self.theme.t)
        self._refresh_recent()
        self._refresh_task_history()
        self._install_shortcuts()
        QTimer.singleShot(300, self._startup_toasts)
        # first applied-state read, after the window has settled
        QTimer.singleShot(600, self._refresh_tweak_state)

    # The width the shell's chrome consumes before a single card can be
    # drawn: sidebar + body margins + body spacing + content padding +
    # grid margins + scrollbar gutter. Below (this + one MIN_CARD_W) the
    # grid physically cannot lay out, so it is the app's true floor.
    _CHROME_W = 250 + 40 + 20 + 48 + 14 + 8
    # title bar + one card row + the Activity rail + vertical padding
    _CHROME_H = 50 + 152 + 44 + 60

    def _init_geometry(self):
        """Screen-aware first launch, centered in the available work area.

        v10: the minimum size is now DERIVED from what the layout actually
        needs (_CHROME_W + one minimum-width card) rather than being a
        hardcoded 980x620 that was then clamped down by the screen size.
        The old `min(980, avail.width() - 48)` could hand back a minimum
        BELOW the layout's real floor on a small display, which let the
        user drag the window down to a size where cards were squeezed past
        their minimum and clipped off the right edge — the layout looked
        broken but nothing was actually wrong except the constraint."""
        desired_w, desired_h = 1180, 760
        floor_w = self._CHROME_W + CategoryPage.MIN_CARD_W
        floor_h = self._CHROME_H
        # the comfortable minimum, never below the hard layout floor
        min_w, min_h = max(floor_w, 980), max(floor_h, 620)

        screen = QApplication.primaryScreen()
        if screen is None:
            self.setMinimumSize(min_w, min_h)
            self.resize(desired_w, desired_h)
            return
        avail = screen.availableGeometry()
        # On a display too small for the comfortable minimum, shrink toward
        # the hard floor rather than below it — a window that cannot lay
        # itself out is worse than one that slightly overhangs the work area.
        min_w = max(floor_w, min(min_w, avail.width() - 48))
        min_h = max(floor_h, min(min_h, avail.height() - 48))
        self.setMinimumSize(min_w, min_h)

        # A remembered geometry wins, but only if Qt can still honour it —
        # restoreGeometry() returns False when the saved screen is gone, in
        # which case we fall through to the centred default rather than
        # placing the window off-screen.
        saved = prefs.window_geometry()
        if saved is not None and self.restoreGeometry(saved):
            return

        w = max(min_w, min(desired_w, avail.width() - 48))
        h = max(min_h, min(desired_h, avail.height() - 48))
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

        # Created first so later siblings (titlebar/sidebar/content, added
        # below) stack above it — the ambient wash sits behind everything.
        self._glow = AmbientGlow(self._shell)
        self._glow.apply_theme(t)

        root = QVBoxLayout(self._shell)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.titlebar = TitleBar(self, t, APP_NAME, APP_VERSION, APP_CHANNEL,
                                  is_admin=self.is_admin)
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

        # -- global search doorway (v1.0) ----------------------
        # The Linear/Raycast sidebar pattern: a quiet input-shaped button
        # at the top of the rail that opens the Ctrl+K palette. One search
        # implementation, two entry points — the button exists for
        # discoverability (a keyboard-only affordance is invisible to
        # anyone who hasn't read the shortcut sheet).
        self._search_btn = QPushButton("🔍  Search everything…")
        self._search_btn.setFixedHeight(36)
        self._search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_btn.setToolTip(
            "Search every app, tweak and tool  (Ctrl+K)")
        self._search_btn.clicked.connect(self._open_command_palette)
        side.addWidget(self._search_btn)
        side.addSpacing(10)

        self._section = QLabel("MODULES")
        self._section.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._section.setIndent(10)   # editor-style left-aligned section label
        side.addWidget(self._section)
        side.addSpacing(8)

        for i, cat in enumerate(CATEGORIES):
            btn = NavButton(cat["glyph"], cat["title"], cat["accent"], t)
            btn.clicked.connect(lambda checked=False, idx=i: self.open_category(idx))
            self._nav_buttons.append(btn)
            side.addWidget(btn)

        # -- Recent Operations (v10) ---------------------------
        # Fills what used to be ~360px of empty rail below the nav with
        # one-click re-runs of what the user actually did last. Sits
        # directly under the modules (still in the "navigate/act" zone),
        # with the stretch AFTER it so the elevation CTA stays anchored to
        # the bottom. Hides itself entirely when there's no history.
        side.addSpacing(TH.SPACE["xl"])
        self._recent = RecentOperationsPanel(t)
        self._recent.rerun_requested.connect(self._rerun_recent)
        side.addWidget(self._recent)
        side.addStretch()

        # -- sidebar footer: elevation · identity (v8.1) --------
        # The sidebar footer is the app's "system controls" zone. Elevation
        # lives here (relocated from the title bar): a prominent, always-
        # visible amber CTA when unelevated, or a quiet green confirmation
        # chip when already Administrator — far more discoverable than the
        # old title-bar badge, and it drops the fragile native hit-test
        # carve-out that badge required.
        #
        # v8.1: the redundant red "Exit" button was removed. Quitting is the
        # title bar's native close 'X' — every Windows user's muscle memory —
        # so a second, louder (red) exit affordance in the sidebar was pure
        # duplication. Dropping it leaves the elevation chip as the clean,
        # single focus of this zone, with the quiet identity line closing the
        # rail beneath it.
        self._elevate_btn: QPushButton | None = None
        self._admin_chip: QLabel | None = None
        if self.is_admin:
            self._admin_chip = QLabel("🛡  Administrator")
            self._admin_chip.setFixedHeight(42)
            self._admin_chip.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            side.addWidget(self._admin_chip)
        else:
            self._elevate_btn = QPushButton("🛡  Run as Administrator")
            self._elevate_btn.setFixedHeight(42)
            self._elevate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._elevate_btn.setToolTip(
                "Some system-level actions need Administrator rights. "
                "Relaunch Pulse elevated (you'll get a UAC prompt).")
            self._elevate_btn.clicked.connect(self._relaunch_as_admin)
            side.addWidget(self._elevate_btn)
        side.addSpacing(14)

        # Anchors the nav column so it no longer floats above a void — a
        # quiet identity line the way VS Code / Linear close their rails.
        self._side_footer = QLabel(f"PULSE  v{APP_VERSION}  ·  {APP_CHANNEL.upper()}")
        self._side_footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(self._side_footer)
        body.addWidget(self._sidebar)

        # -- content ------------------------------------------
        self._content = QFrame()
        content = QVBoxLayout(self._content)
        content.setContentsMargins(24, 18, 24, 16)
        content.setSpacing(12)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; border: none; }")
        self.welcome = WelcomePage(t, bool(self.ps1_path), self.is_admin)
        self.welcome.action_requested.connect(self.request_task)
        self.stack.addWidget(self.welcome)
        self.pages: list[CategoryPage] = []
        for cat in CATEGORIES:
            page = CategoryPage(cat, t)
            page.home_requested.connect(self.go_home)
            page.task_requested.connect(self.request_task)
            self.pages.append(page)
            self.stack.addWidget(page)
        content.addWidget(self.stack, 1)

        # -- Activity drawer (v7): auto-collapsing live output ----
        # Replaces the always-open 170px console + separate status row. The
        # drawer keeps a slim 44px rail visible (status dot, state pill, Stop,
        # pin chevron) and only expands its console body while a task runs —
        # reclaiming ~140px of canvas whenever the app is idle. The rest of
        # the task pipeline still reaches console/state_pill/stop_btn/shimmer/
        # status_dot/status_text as attributes, via the aliases below.
        self.activity = ActivityDrawer(t, on_stop=self._cancel_running_task,
                                       pinned=prefs.drawer_pinned())
        content.addWidget(self.activity)
        self.console = self.activity.console
        self.state_pill = self.activity.state_pill
        self.stop_btn = self.activity.stop_btn
        self.shimmer = self.activity.shimmer
        self.status_dot = self.activity.status_dot
        self.status_text = self.activity.status_text

        body.addWidget(self._content, 1)
        self.toasts = ToastManager(self._shell, t)
        # The Activity drawer owns the bottom-right corner and grows ~186px
        # when a task starts; registering it keeps the toast stack riding
        # above the live console instead of landing on top of it (v10).
        self.toasts.set_bottom_obstacle(self.activity)
        # the drawer's copy/export/clear actions report through the
        # app's own toast stack rather than owning notification UI
        self.activity.set_notifier(
            lambda kind, message: self.toasts.show(kind, message, 3500))
        self.activity.height_changed.connect(self.toasts.reposition)

    # ============================================================
    #  LIVE THEME PIPELINE
    # ============================================================
    def _apply_theme(self, t: dict):
        # Paint the window's own background in the theme's canvas colour.
        # During a live resize Windows exposes the newly-revealed strip
        # before Qt has repainted the shell into it; without an opaque
        # themed base that strip flashes the default palette grey, which
        # reads as tearing along the edge being dragged.
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(t["bg_grad_bottom"]))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self._shell.setStyleSheet(TH.shell_qss(t))
        self._glow.apply_theme(t)
        self._sidebar.setStyleSheet(TH.sidebar_qss(t))
        self._content.setStyleSheet(TH.content_qss(t))
        self._search_btn.setStyleSheet(TH.sidebar_search_qss(t))
        self._section.setStyleSheet(TH.label_qss(t, "section"))
        self._side_footer.setStyleSheet(TH.label_qss(t, "caption"))
        if self._elevate_btn is not None:
            self._elevate_btn.setStyleSheet(TH.elevate_button_qss(t))
        if self._admin_chip is not None:
            self._admin_chip.setStyleSheet(TH.admin_status_qss(t))
        self.titlebar.apply_theme(t)
        self.welcome.apply_theme(t)
        for btn in self._nav_buttons:
            btn.apply_theme(t)
        for page in self.pages:
            page.apply_theme(t)
        self.activity.apply_theme(t)
        self._recent.apply_theme(t)
        self.toasts.apply_theme(t)
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
    #  APPLIED-STATE PROBE (read-only, background)
    # ============================================================
    def _refresh_tweak_state(self):
        """Ask the backend which readable tweaks are currently in effect and
        badge the matching cards.

        Runs on its OWN thread, entirely outside the single-task pipeline:
        it is read-only (see backend 11-StateProbe.ps1), so it must never
        occupy the "one task at a time" slot, block a real operation, or
        show up in the live console. Deliberately NOT cached to disk — the
        user can change any of these settings outside Pulse, so the honest
        answer is always the one the system gives right now.
        """
        if not self.ps1_path or self._probe_thread is not None:
            return
        thread = QThread(self)
        worker = PowerShellTask(self.ps1_path, "GetTweakState", timeout=90)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_tweak_state)
        # A probe failure is genuinely unimportant: cards simply stay
        # un-badged. It must never toast or change the status line.
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(thread.quit)
        thread.finished.connect(self._on_probe_thread_finished)
        self._probe_thread = thread
        self._probe_worker = worker
        thread.start()

    @staticmethod
    def _badge_verdict(task: str | None, verdict) -> str | None:
        """Badge policy for one ONE-SHOT card. "applied" and "mixed" always
        show; "default" shows ONLY on the two-way toggle cards
        (_REVERT_TASKS), where "at Windows defaults" answers a question the
        card genuinely poses — on a removal card ("Remove Edge") a DEFAULT
        badge would just be noise restating that Edge exists. Legacy
        booleans from an older backend normalise so a version-skewed probe
        stays honest."""
        if verdict is True:
            verdict = "applied"
        elif verdict is False:
            verdict = "default"
        if verdict == "default" and task not in _REVERT_TASKS:
            return None
        return verdict if verdict in ("applied", "mixed", "default") else None

    def _card_badge(self, item: dict, history: dict) -> str | None:
        """THE badge decision for any card — one function so the two inputs
        (the state probe and the run history) can never fight over the same
        chip, which is what would happen if _on_tweak_state and
        _refresh_task_history each wrote it.

        A ROUTINE task takes the history branch and never the probe: a
        cache clean has no durable state to read, so "APPLIED" was a
        category error — it was run, and then time passed. It badges ACTION
        DUE once its interval has elapsed (or if it has never run), and
        otherwise nothing, leaving its "Ran 3d ago" caption to say when.
        """
        interval = recurring_days(item)
        task = item.get("task")
        if interval is not None:
            entry = history.get(task)
            last = float(entry.get("last_ts", 0.0)) if entry else 0.0
            if not last:
                return "due"
            return "due" if (time.time() - last) / 86400.0 >= interval else None
        return self._badge_verdict(task, self._tweak_state.get(task))

    def _refresh_card_badges(self):
        """Re-decide every card's badge from the current probe state and
        run history. Called by both producers, so whichever lands last
        renders the same answer."""
        history = prefs.task_history()
        for page in self.pages:
            for card in page.cards:
                card.set_applied(self._card_badge(card.item, history))
        for card in self.welcome.action_cards():
            card.set_applied(self._card_badge(card.item, history))
        for page in self.pages:
            page.refresh_filter()

    def _on_tweak_state(self, result: TaskResult):
        state = result.data if isinstance(result.data, dict) else None
        if not state:
            return
        self._tweak_state = state
        self._refresh_card_badges()

    def _on_probe_thread_finished(self):
        if self._probe_worker is not None:
            self._probe_worker.deleteLater()
            self._probe_worker = None
        if self._probe_thread is not None:
            self._probe_thread.deleteLater()
            self._probe_thread = None

    # ============================================================
    #  RECENT OPERATIONS (sidebar panel, persisted across sessions)
    # ============================================================
    def _refresh_recent(self):
        # The operations trail has exactly ONE rendering (v1.0 redundancy
        # pass): this sidebar panel, visible on every page. The dashboard
        # showed a second copy of the same list until v1.0; its slot now
        # carries overdue maintenance instead — see WelcomePage.
        self._recent.set_entries(prefs.recent_operations())

    def _rerun_recent(self, task: str):
        """Re-run a remembered operation. Resolved back to its LIVE catalog
        item rather than replayed from the stored copy, so a re-run always
        picks up the current definition (timeout, confirm flag, selector) —
        and an operation the catalog no longer defines fails loudly here
        instead of being dispatched to a task the backend has dropped."""
        for item, _breadcrumb in iter_leaf_items():
            if item.get("task") == task:
                self.request_task(item, None)
                return
        self.toasts.show(
            "info", "That operation is no longer available in this version.", 4000)

    # ============================================================
    #  PER-TASK RUN HISTORY (v10.1) — "Ran 3d ago · ~2m" on the card
    # ============================================================
    def _refresh_task_history(self):
        """Push the stored history onto every card. Mirrors the shape of
        _on_tweak_state deliberately: both answer "what do we know about
        this card?" and both must reach the dashboard's action cards as
        well as the category pages."""
        history = prefs.task_history()
        for page in self.pages:
            for card in page.cards:
                card.set_history(history.get(card.item.get("task")))
        for card in self.welcome.action_cards():
            card.set_history(history.get(card.item.get("task")))
        # A routine task's badge IS a function of its history, so the two
        # must be pushed together — running a cache clean has to clear its
        # ACTION DUE chip in the same pass that updates its caption.
        self._refresh_card_badges()
        self.welcome.refresh_maintenance()

    def _record_task_history(self, outcome: str):
        """Fold the run that just settled into its task's history.

        Timed from _start_task rather than from the worker, so the figure
        is the WALL CLOCK the user actually waited — including the module
        load and process spawn that a backend-side timer would miss. That
        is the number a "typically ~2m" hint has to describe to be useful.
        """
        item = self._running_item
        if item is None or self._run_started_at is None:
            return
        elapsed_ms = (time.monotonic() - self._run_started_at) * 1000.0
        self._run_started_at = None
        prefs.record_task_run(item.get("task", ""), elapsed_ms, outcome)
        self._refresh_task_history()

    def _record_recent(self, outcome: str):
        """Called once a task settles. The card's own module accent and
        glyph ride along so the sidebar row is colour-coded to the module
        the operation came from."""
        item = self._running_item
        if item is None:
            return
        prefs.push_recent_operation(
            task=item.get("task", ""),
            title=item.get("title", ""),
            glyph=item.get("glyph", ""),
            accent=self._running_accent,
            outcome=outcome)
        self._refresh_recent()

    def _open_health_report(self):
        """Read-only, so it deliberately does NOT take the shell's task
        slot — a report can be pulled while nothing else is happening or
        alongside an idle window, and it never mutates anything."""
        if not self.ps1_path:
            self.toasts.show("error", f"{PS1_FILENAME} not found — engine unavailable.", 5000)
            return
        self._exec_dialog(HealthReportDialog(self, self.ps1_path, self.theme.t))

    def _open_activation_status(self):
        """Read-only licence report — same reasoning as the health report:
        it never mutates anything, so it does not take the shell's task
        slot, and it needs no elevation because every property the backend
        probe reads is available to a standard user."""
        if not self.ps1_path:
            self.toasts.show("error", f"{PS1_FILENAME} not found — engine unavailable.", 5000)
            return
        self._exec_dialog(ActivationStatusDialog(self, self.ps1_path, self.theme.t))

    # ============================================================
    #  PLAYBOOKS (v10.3)
    # ============================================================
    def _open_playbooks(self):
        """Browse -> preview/run -> watch, all in one dialog.

        The runner drives the SAME dialog that was used to pick the
        playbook (enter_run_mode), so nothing re-layouts under the cursor
        at the moment the run begins.
        """
        if not self.ps1_path:
            self.toasts.show("error", f"{PS1_FILENAME} not found — engine unavailable.", 5000)
            return
        if self._busy():
            self.toasts.show("info", "Something is already running — please wait.", 3000)
            return

        playbooks, errors = load_playbooks()
        dialog = PlaybookDialog(self, playbooks, errors, self.theme.t, self.is_admin)
        if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
            return
        if dialog.chosen is None:
            return
        self._start_playbook(dialog.chosen, dialog.dry_run)

    def _start_playbook(self, playbook, dry_run: bool):
        """Run `playbook` in a fresh dialog left in run mode.

        A real (non-preview) run of an admin-gated playbook is gated up
        front rather than allowed to fail on step 1: every shipped
        playbook opens with Create Restore Point, so an unelevated session
        would halt immediately having done nothing. Preview is exempt —
        simulating what WOULD need elevation is exactly the question
        preview answers.
        """
        if not dry_run and playbook.needs_admin and not self.is_admin:
            item = {"icon": playbook.icon, "title": playbook.name,
                    "desc": f"This playbook changes machine-wide settings, so "
                            f"Pulse needs to run elevated. Preview still works "
                            f"without elevation."}
            if self._exec_dialog(
                    ElevatePromptDialog(self, item, self.theme.t)) == QDialog.DialogCode.Accepted:
                self._relaunch_as_admin()
            return

        dialog = PlaybookDialog(self, [playbook], [], self.theme.t, self.is_admin)
        runner = PlaybookRunner(self.ps1_path, playbook, dry_run=dry_run, parent=self)
        self._playbook_runner = runner
        self._playbook_dialog = dialog

        dialog.enter_run_mode(dry_run)
        dialog.stop_requested.connect(runner.cancel)
        runner.step_started.connect(
            lambda i: dialog.mark_step(i, "running", "running…"))
        runner.step_output.connect(self.console.put_line)
        runner.step_finished.connect(
            lambda i, res: dialog.mark_step(i, res.outcome, res.message))
        runner.finished.connect(
            lambda run: self._on_playbook_finished(run, dialog))

        self.activity.set_running(True)
        self.console.clear_console()
        self.state_pill.set_state("running")
        self._set_status("busy", f"Playbook: {playbook.name} …")
        QTimer.singleShot(0, runner.start)
        dialog.exec()

    def _on_playbook_finished(self, run, dialog):
        self._playbook_runner = None
        self._playbook_dialog = None

        # The run can settle AFTER the window began closing (closeEvent
        # cancels the runner, and the cancellation arrives here a moment
        # later), by which point Qt may already have destroyed the dialog
        # and the shell's widgets. Reporting into a torn-down UI is not
        # worth an exception on the way out.
        try:
            self._report_playbook_result(run, dialog)
        except RuntimeError:
            pass

    def _report_playbook_result(self, run, dialog):
        prefix = "[DRY-RUN] " if run.dry_run else ""
        seconds = run.duration_ms / 1000.0
        if run.cancelled:
            summary = f"{prefix}Stopped after {run.succeeded} of {len(run.playbook)} steps."
            kind, flash = "warn", None
        elif run.halted_on is not None:
            step = run.playbook.steps[run.halted_on]
            summary = (f"{prefix}Halted at step {run.halted_on + 1} "
                       f"({step.title}). {run.succeeded} step(s) completed.")
            kind, flash = "error", "err"
        else:
            summary = (f"{prefix}{run.succeeded} of {len(run.playbook)} steps "
                       f"completed in {seconds:.0f}s.")
            if run.failed:
                summary += f" {run.failed} optional step(s) failed."
            kind, flash = ("warn" if run.failed else "ok"), "ok"

        dialog.set_status(summary, kind)
        dialog.enter_done_mode()
        self.toasts.show(
            {"ok": "success", "warn": "warn", "error": "error"}[kind], summary, 7000)
        self._set_status("ok" if kind != "error" else "err", "System Ready")
        self.state_pill.set_state("ok" if kind != "error" else "err")
        self.activity.set_running(False)
        # A playbook changes several probed settings at once.
        QTimer.singleShot(400, self._refresh_tweak_state)
        self._refresh_task_history()
        if flash:
            self._refresh_recent()

    # ============================================================
    #  KEYBOARD LAYER (v10)
    # ============================================================
    # Before v10 the app had exactly two shortcuts (Escape, Ctrl+K) and the
    # card grid could not be reached from the keyboard at all. The table
    # below is the single source of truth for both the bindings and the
    # help sheet, so a shortcut can never exist without being documented.
    SHORTCUTS = [
        ("Ctrl+K  or  Ctrl+F", "Search everything"),
        ("Ctrl+Shift+F",  "Filter this module by status"),
        ("Ctrl+H",        "Go to the dashboard"),
        ("Ctrl+1 … 4",    "Jump to a module"),
        ("Ctrl+\\",       "Show / hide live output"),
        ("↑ ↓ ← →",       "Move between cards"),
        ("Enter / Space", "Run the focused card"),
        ("Esc",           "Back to the dashboard"),
        ("F1  or  ?",     "This shortcut sheet"),
    ]

    def _install_shortcuts(self):
        def bind(sequence, slot):
            QShortcut(QKeySequence(sequence), self, activated=slot)

        bind(Qt.Key.Key_Escape, self.go_home)
        bind("Ctrl+K", self._open_command_palette)
        bind("Ctrl+H", self.go_home)
        # v1.0: the "find" keys now open the ONE search the app has. The
        # page-level control they used to focus is a status filter, not a
        # search, so it moves to its own binding rather than quietly
        # answering a keypress the user meant for text search.
        bind("Ctrl+F", self._open_command_palette)
        bind("Ctrl+Shift+F", self._focus_page_filter)
        bind("Ctrl+\\", self.activity.toggle_pinned)
        bind("F1", self._open_shortcut_sheet)
        bind("?", self._open_shortcut_sheet)
        for i in range(len(CATEGORIES)):
            bind(f"Ctrl+{i + 1}", lambda idx=i: self.open_category(idx))

    def _focus_page_filter(self):
        """Ctrl+Shift+F on a module page opens its status filter; on the
        dashboard, which has no filter, it falls back to the command
        palette so the key never does nothing."""
        page = self.stack.currentWidget()
        if isinstance(page, CategoryPage):
            page.focus_filter()
        else:
            self._open_command_palette()

    def _open_shortcut_sheet(self):
        self._exec_dialog(ShortcutSheetDialog(self, self.theme.t, self.SHORTCUTS))

    # ============================================================
    #  COMMAND PALETTE (Ctrl+K)
    # ============================================================
    def _open_command_palette(self):
        # iter_leaf_items() expands hub containers so a sub-action (e.g.
        # "Microsoft Office Suite", tucked inside the Browsers & Daily Apps
        # hub) stays searchable even though its category page now shows
        # only the hub card.
        entries = list(iter_leaf_items())
        palette = CommandPalette(self, self.theme.t, entries)
        # Top-anchored VS Code / Slack quick-launcher placement comes from
        # _present_dialog(anchor="top") in the palette's own showEvent.
        if (self._exec_dialog(palette) == QDialog.DialogCode.Accepted
                and palette.chosen_item is not None):
            self.request_task(palette.chosen_item, None)

    # ============================================================
    #  MODAL PRESENTATION
    # ============================================================
    def _exec_dialog(self, dialog) -> int:
        """exec() any Pulse dialog. The dialog itself (PulseDialog) sizes
        to the shell body and paints its own scrim backdrop in showEvent —
        see widgets._present_dialog — so the card grid / console underneath
        is fully masked and a click on the backdrop dismisses the dialog,
        with no separate scrim widget to coordinate here."""
        return dialog.exec()

    # ============================================================
    #  HUB NAVIGATION — a primary card's drill-down landing screen
    # ============================================================
    def _open_hub(self, hub: dict):
        """A hub with exactly one real action skips the landing screen
        entirely (nothing to choose between) and runs it directly — the
        Developer & University Hub and Gaming & Launchers cards behave
        exactly as they did before Software Management collapsed to 4
        primary cards. A hub with several sub-actions opens HubDialog."""
        sub_items = hub_items(hub)
        if len(sub_items) == 1:
            self.request_task(sub_items[0], None)
            return
        dialog = HubDialog(self, hub, self.theme.t)
        if (self._exec_dialog(dialog) == QDialog.DialogCode.Accepted
                and dialog.chosen_item is not None):
            self.request_task(dialog.chosen_item, None)

    # ============================================================
    #  TASK PIPELINE
    # ============================================================
    def request_task(self, item: dict, card: GlassCard | None = None):
        if item.get("hub"):
            self._open_hub(item)
            return
        task = item["task"]

        if task.startswith("@"):
            self._run_local_action(task)
            return
        # Elevation pre-check (v9.4): an admin-gated task on a non-elevated
        # Pulse gets an inline one-click "relaunch elevated" prompt BEFORE we
        # spawn PowerShell — cleaner than a spawn-then-access-denied round trip,
        # and it covers category cards, dashboard Quick Actions and Ctrl+K in
        # one place. The backend still enforces the same gate as a backstop.
        if requires_admin(task) and not self.is_admin:
            dialog = ElevatePromptDialog(self, item, self.theme.t)
            if self._exec_dialog(dialog) == QDialog.DialogCode.Accepted:
                self._relaunch_as_admin()
            return
        if self._busy():
            self.toasts.show("info", "Something is already running — please wait.", 3000)
            return
        if not self.ps1_path:
            self.toasts.show("error", f"{PS1_FILENAME} not found — engine unavailable.", 5000)
            return

        # -- v1.0 chassis guard: the Ultimate Power Plan on battery hardware
        # The card copy already says "Desktop PCs only"; a machine that
        # REPORTS A BATTERY gets an explicit danger-styled confirm on top,
        # because never-sleep AC timeouts on a laptop are a flat (and, in a
        # bag, hot) battery. Only a definite True escalates — unknown stays
        # silent rather than warning every desktop with a quirky driver.
        if task == "UltimatePowerPlan" and has_battery() is True:
            item = {**item, "danger": True, "confirm": True,
                    "desc": ("A battery was detected — this machine looks "
                             "like a laptop or mobile device. This plan "
                             "disables display and sleep timeouts on AC "
                             "power and is designed for desktop PCs only. "
                             "Proceed only if this is genuinely a desktop.")}

        # -- v1.0 two-way toggle: applied tweak -> re-apply / revert choice
        # Only when the probe DEFINITELY reports the tweak applied (or
        # modified): unknown keeps the plain apply flow, because offering a
        # revert for a state we cannot read would promise an undo we cannot
        # scope. The choice dialog replaces the item's own confirm step —
        # one click, one question.
        if (task in _REVERT_TASKS
                and self._tweak_state.get(task) in ("applied", "mixed", True)):
            choice = RevertChoiceDialog(self, item, self.theme.t,
                                        "mixed" if self._tweak_state.get(task) == "mixed"
                                        else "applied")
            if self._exec_dialog(choice) != QDialog.DialogCode.Accepted:
                return
            if choice.choice == "revert":
                revert_item = {
                    "icon": item.get("icon", "↩"),
                    "glyph": item.get("glyph", ""),
                    "title": f"Revert: {item['title']}",
                    "desc": "Restore this tweak to your original values.",
                    "task": _REVERT_TASKS[task],
                    "timeout": item.get("timeout", 300),
                }
                self._start_task(revert_item, card)
                return
            item = {**item}
            item.pop("confirm", None)   # the choice dialog WAS the confirm

        app_ids: list[str] | None = None
        office_paths: tuple[str, str] | None = None
        local_installer: tuple[str, str] | None = None
        if item.get("startup_manager"):
            # Fully self-contained: scans, groups by recommendation and
            # flips items live via its own workers. Nothing to hand back —
            # open it and move on, exactly like a plain informational card.
            StartupManagerDialog(self, self.ps1_path, self.theme.t).exec()
            return
        if item.get("storage_analyzer"):
            # Same shape: a read-only scan that hands nothing back. It owns
            # its own drive picker and re-scans in place, so there is no
            # selection for the task pipeline to carry.
            self._exec_dialog(StorageAnalyzerDialog(self, self.ps1_path, self.theme.t))
            return
        if item.get("update_center"):
            dialog = UpdateCenterDialog(self, self.ps1_path, self.theme.t)
            if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
                return
            if dialog.local_installer:
                # A row's "⋯" wizard resolved to Path C (a local file) —
                # same contract as every other selector's row wizard.
                local_installer = dialog.local_installer
                item = {**item, "task": "InstallLocalFile"}
            elif dialog.selected_ids:
                app_ids = dialog.selected_ids
            else:
                self.toasts.show("info", "No updates were selected — nothing to update.", 3500)
                return
        elif item.get("catalog"):
            # THE unified software hub — every installable app behind one
            # card, tab-filtered by sub-category. Hands back exactly what
            # the old per-pack selectors did, so everything downstream
            # (concurrency guard, live console, toasts) is unchanged.
            dialog = SoftwareCatalogDialog(
                self, item, self.theme.t,
                SOFTWARE_CATALOG, CATALOG_BUNDLES, CATALOG_BUNDLE_SECTION)
            if self._exec_dialog(dialog) != QDialog.DialogCode.Accepted:
                return
            if dialog.local_installer:
                # A per-app "⋯" wizard resolved to Path C (a local file) —
                # run the generic single-installer task instead of the bulk
                # InstallCatalogApps deploy.
                local_installer = dialog.local_installer
                item = {**item, "task": "InstallLocalFile"}
            elif dialog.selected_ids:
                app_ids = dialog.selected_ids
            else:
                self.toasts.show(
                    "info", "No apps were selected — nothing to deploy.", 3500)
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
        # remembered for the Recent Operations trail once this settles
        self._running_item = item
        self._running_accent = accent_for_task(item.get("task"))
        # monotonic, not time.time(): this measures an ELAPSED interval, and
        # a wall clock can jump backwards mid-run (NTP correction, DST) and
        # bank a negative or wildly inflated duration into the average.
        self._run_started_at = time.monotonic()
        if card is not None:
            card.set_running(True)
        self.activity.set_running(True)   # expand the drawer for live output
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
            if "needs administrator rights" in message.lower():
                # A clean amber warning, not a flat red error. This is a
                # backstop: the frontend's own pre-check (request_task +
                # requires_admin) normally shows the inline elevate prompt
                # before a task ever spawns, so reaching here means the backend
                # gate fired — confirmation, not a surprise failure.
                self.toasts.show("warn", message, 7000)
                self._set_status("err", "Administrator rights required")
            else:
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
        # Only a real verdict is worth remembering — a cancelled run passes
        # flash=None and is deliberately left out of the trail, and out of
        # the duration history: a stopped task is a partial measurement
        # that would drag every "typically ~Ns" estimate downward.
        if flash:
            self._record_recent(flash)
            self._record_task_history(flash)
        self._run_started_at = None
        self._running_item = None
        # a task may have just changed one of the probed settings
        QTimer.singleShot(400, self._refresh_tweak_state)
        if self._running_card is not None:
            self._running_card.set_running(False)
            if flash:
                self._running_card.flash(flash)
            self._running_card = None
        self.shimmer.stop()
        self.stop_btn.hide()
        # Collapse the drawer after a brief hold so the final verdict stays
        # readable; a pinned drawer (or one still running) stays open.
        self.activity.set_running(False)

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
        # Handled-in-app actions come first: these open a Pulse surface
        # rather than a file, so they never reach the path resolution below.
        if task == "@playbooks":
            self._open_playbooks()
            return
        if task == "@health_report":
            self._open_health_report()
            return
        if task == "@activation":
            self._open_activation_status()
            return
        # Read-only inspectors (Phase 1). Each runs its own PowerShellTask
        # inside its dialog, exactly like the activation report, so opening
        # one never occupies the shell's single-task pipeline.
        if task == "@power_health":
            self._exec_dialog(PowerHealthDialog(self, self.ps1_path, self.theme.t))
            return
        if task == "@restore_points":
            self._exec_dialog(RestorePointDialog(self, self.ps1_path, self.theme.t))
            return

        desktop = resources.desktop_dir()
        localappdata = resources.local_appdata()
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
    @staticmethod
    def _locate_ps1() -> str | None:
        """The PowerShell engine.

        Searched across BUNDLED roots only — deliberately not the
        directory the exe sits in, unlike playbooks. See
        utils/resources.py: a core.ps1 that could be dropped beside an
        installed Pulse would be a script anyone with write access to the
        install folder could swap, and it runs elevated on every task.
        """
        return resources.find_resource(
            f"src/backend/{PS1_FILENAME}",
            f"src/frontend/{PS1_FILENAME}",
            f"backend/{PS1_FILENAME}",
            PS1_FILENAME,
        )

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
                "Not running as Administrator — system tasks will prompt to "
                "relaunch elevated. Or click 'Run as Administrator' in the sidebar.",
                8000)

    def _relaunch_as_admin(self):
        """One-click UAC relaunch, triggered by the sidebar footer's 'Run as
        Administrator' button. Spawns a second, elevated Pulse via the 'runas'
        verb (which shows Windows' own UAC consent prompt) and quits this
        instance once it's confirmed launched — never before, so declining
        the prompt (or the launch failing outright) leaves the user with
        the still-running unelevated app instead of no app at all."""
        if sys.stdout is not None:
            print("[Pulse] _relaunch_as_admin: elevation requested.")
        if self._busy():
            # Relaunching quits this process, which kills whatever the
            # engine is doing — including, before v10.3, a playbook this
            # check could not see.
            self.toasts.show(
                "info", "Wait for the current operation to finish before "
                        "restarting elevated.", 4000)
            return
        if sys.platform != "win32":
            return

        frozen = getattr(sys, "frozen", False)
        # lpFile itself is a single path, never tokenized by ShellExecute -
        # quoting IT would make Windows search for a file literally named
        # with quote characters and fail. Only lpParameters is a command
        # line the target process re-parses, so that's the piece that
        # needs Win32 quoting - list2cmdline wraps any path containing
        # spaces in quotes exactly the way CommandLineToArgvW expects.
        # sys.argv[1:] rides along so a relaunch preserves whatever flags
        # the current run was started with, not just the bare script.
        exe = sys.executable
        extra_args = sys.argv[1:]
        if frozen:
            arg_list = extra_args
            workdir = os.path.dirname(exe)
        else:
            arg_list = [os.path.abspath(__file__), *extra_args]
            workdir = _FRONTEND_DIR
        params = subprocess.list2cmdline(arg_list)

        try:
            # SW_SHOWNORMAL=1. Return value is an HINSTANCE per the Win32
            # contract - values > 32 mean success, <= 32 is a specific
            # SE_ERR_* failure code (declining the UAC prompt included).
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, params, workdir, 1)
        except OSError:
            ret = 0
        if sys.stdout is not None:
            print(f"[Pulse] ShellExecuteW(runas) -> {ret} "
                  f"(exe={exe!r} params={params!r})")
        if ret <= 32:
            self.toasts.show("info", "Elevation was cancelled.", 4000)
            return
        self.toasts.show("success", "Relaunching elevated…", 1500)
        QTimer.singleShot(400, QApplication.instance().quit)

    # ============================================================
    #  WINDOW EVENTS — native glass, native resize, native corners
    # ============================================================
    def showEvent(self, event):
        super().showEvent(event)
        if not self._glass_applied:
            self._glass_applied = True
            hwnd = int(self.winId())
            # apply_blur_behind() is deliberately NOT called any more: DWM
            # blur-behind only shows through a per-pixel-alpha window, so
            # it required the WA_TranslucentBackground that was causing the
            # rendering glitches. An opaque shell has nothing to see
            # through, and the call would only re-introduce the layered
            # composition path.
            # A real sizing frame, so the edge/corner hit-tests answered in
            # nativeEvent are actually acted on by Windows (see
            # theme.enable_native_sizing_frame). WM_NCCALCSIZE below keeps
            # the client area edge-to-edge, so nothing is drawn for it.
            TH.enable_native_sizing_frame(hwnd)
            TH.apply_native_rounding(hwnd, rounded=not self.isMaximized())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._glow.setGeometry(self._shell.rect())
        self.toasts.reposition()
        active = QApplication.activeModalWidget()
        if isinstance(active, PulseDialog):
            refit_dialog(active)

    def _sync_window_state(self):
        """Bring every state-dependent visual in line with the window's
        CURRENT normal/maximized/minimized state.

        Split out of changeEvent because it must also run once after the
        UI exists: `_init_geometry()` restores a saved geometry during
        __init__, and if that geometry was saved while maximized, Qt
        emits WindowStateChange *before* `_build_ui()` has created
        `_glow`/`_shell`/`_body`. That event is dropped (see changeEvent's
        guard), so the restored maximized window would otherwise come up
        wearing the floating look — rounded shell, floating margins,
        DWM-rounded corners."""
        # Pause the living-background loop while minimized (hideEvent does
        # NOT fire on minimize, so the ~28fps timer would otherwise keep
        # running behind an invisible window), and while a drag is in
        # flight — Aero-snapping with the mouse changes the window state
        # *inside* the move/size loop, and resuming there would undo the
        # drag suspension. WM_EXITSIZEMOVE owns the resume in that case.
        if self.isMinimized() or self._in_size_move:
            self._glow.suspend()
        else:
            self._glow.resume()
        # Maximized = edge-to-edge: the shell drops its floating radius
        # and border (see shell_qss) so corners sit flush with the
        # monitor, exactly like a native maximized Win11 window.
        # (`flush`, not `maximized`: QWidget's built-in read-only
        # `maximized` property would swallow the write.)
        flush = self.isMaximized()
        # Kept as a state record for widgets that ask (and for the body
        # margins below); the shell itself no longer restyles on it, since
        # it is square and border-less in both states now.
        self._shell.setProperty("flush", flush)
        # Always 0: the ambient wash fills a square, opaque shell. Its
        # rounded clip only ever existed to stop it painting into the
        # translucent window's rounded corner cut-out, and a rounded clip
        # on an opaque window just carves visible notches at the corners.
        self._glow.set_radius(0)
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

    def changeEvent(self, event):
        super().changeEvent(event)
        # State changes can land before the UI exists — restoreGeometry()
        # inside _init_geometry() re-applies a saved maximized state while
        # __init__ is still running. Touching _glow/_shell/_body here used
        # to raise AttributeError and take the whole app down on launch
        # (i.e. "closed while maximized" = never starts again). __init__
        # calls _sync_window_state() once the widgets exist.
        if event.type() == QEvent.Type.WindowStateChange and self._ui_ready:
            self._sync_window_state()

    def _task_is_running(self) -> bool:
        """A single PowerShellTask is in flight (the one-at-a-time slot)."""
        return self._thread is not None and self._thread.isRunning()

    def _playbook_is_running(self) -> bool:
        return self._playbook_runner is not None

    def _busy(self) -> bool:
        """Is the engine mutating this machine right now, by ANY route?

        v10.3: this exists because "is something running" was previously
        asked four different ways, and every one of them inspected only
        `self._thread`. A PlaybookRunner owns its OWN QThread, so all four
        answered False for the longest and most destructive operation the
        app can perform — a playbook halfway through a machine baseline.
        The close guard skipped its confirmation, the elevation relaunch
        offered to quit mid-run, and request_task would have started a
        second engine on top of the first.

        Every one of those questions is the same question, so it now has
        exactly one answer.
        """
        return self._task_is_running() or self._playbook_is_running()

    def closeEvent(self, event):
        """Guard against orphaning the backend process tree: if a
        PowerShellTask is still running when the window closes (the X
        button, Alt+F4, or the custom caption's close control below all
        end up here via Qt's normal close path), cancel it and give the
        process-tree kill a moment to land before the QThread gets torn
        down - otherwise winget/DISM/sfc children spawned by core.ps1 are
        left running headless after the GUI disappears.

        v10.2: that cancellation is no longer silent. A half-applied MSI
        install or a half-finished Edge purge is a worse state than either
        outcome the user was choosing between, so closing mid-task asks
        first (widgets.CloseConfirmDialog). Declining ignores the event and
        the window stays open with the task untouched.

        Geometry is saved only once the close is going ahead — writing it
        before the prompt would persist the geometry of a window the user
        then chose NOT to close, which is harmless today but wrong the
        moment anything else keys off that write.
        """
        if self._busy():
            # Name what is actually in flight. A playbook is the case that
            # matters most here — it is the longest operation the app runs
            # and the one whose half-finished state is hardest to reason
            # about — and it used to slip past this guard entirely.
            if self._playbook_is_running():
                title = f"the playbook “{self._playbook_runner.playbook.name}”"
            else:
                title = (self._running_item or {}).get("title", "")
            if self._exec_dialog(
                    CloseConfirmDialog(self, self.theme.t, title)) != QDialog.DialogCode.Accepted:
                event.ignore()
                return

        prefs.set_window_geometry(self.saveGeometry())
        prefs.set_drawer_pinned(self.activity.is_pinned())
        if self._playbook_is_running():
            # Stops the step in flight and prevents the next one starting.
            # The steps already applied are deliberately left in place —
            # same policy as the Stop button (see PlaybookRunner.cancel).
            self._playbook_runner.cancel()
            # ...then let the run dialog's exec() loop unwind. It is
            # parented to this window, so leaving it up would outlive its
            # own parent. force_close is the sanctioned override of the
            # run lock that reject() otherwise enforces.
            if self._playbook_dialog is not None:
                self._playbook_dialog.force_close()
        if self._task_is_running():
            if self._worker is not None:
                self._worker.cancel()
            self._thread.wait(3000)
        super().closeEvent(event)

    # Win32 hit-test codes for the native resize border (WM_NCHITTEST)
    _HT = {"L": 10, "R": 11, "T": 12, "TL": 13, "TR": 14,
           "B": 15, "BL": 16, "BR": 17}
    # Non-client caption-button verdicts. HTMAXBUTTON also summons the
    # Windows 11 Snap Layouts flyout.
    _HT_CAPTION = {"min": 8, "max": 9, "close": 20}
    _HTMAXBUTTON = 9
    _WM_NCHITTEST = 0x0084
    _WM_NCCALCSIZE = 0x0083
    _WM_NCLBUTTONDOWN = 0x00A1
    _WM_NCLBUTTONUP = 0x00A2
    _WM_NCMOUSELEAVE = 0x02A2
    # Windows brackets every OS-driven move/resize (title-bar drag, edge
    # drag, Aero Snap) with this pair.
    _WM_ENTERSIZEMOVE = 0x0231
    _WM_EXITSIZEMOVE = 0x0232

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

            if msg.message == self._WM_NCCALCSIZE and msg.wParam:
                # The window owns a real WS_THICKFRAME/WS_CAPTION frame so
                # Windows will run the resize loop for it — but that frame
                # must never be DRAWN or it would eat a border-and-caption
                # strip out of our own chrome. Returning the proposed
                # window rect unchanged makes the client area cover the
                # entire window, which is the whole custom-frame trick.
                params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
                rect = params.rgrc[0]
                # IsZoomed(), not Qt's isMaximized(): this message is part
                # of the maximize transition itself, so Qt's window state
                # has not been updated yet and would report the OLD state
                # — leaving the maximized window oversized by the frame on
                # every edge. The OS always knows.
                if ctypes.windll.user32.IsZoomed(msg.hWnd):
                    # A maximized WS_THICKFRAME window is deliberately
                    # oversized by the frame on every side; without this
                    # inset the content would hang off all four edges of
                    # the monitor (and over the taskbar).
                    bx, by = TH.resize_border_thickness()
                    rect.left += bx
                    rect.top += by
                    rect.right -= bx
                    rect.bottom -= by
                return True, 0

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
                # open. Only the theme toggle and the (optional) admin
                # badge keep an HTCLIENT hole.
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

            elif msg.message == self._WM_ENTERSIZEMOVE:
                # The ambient background is a ~28fps full-window repaint.
                # While Windows runs its modal move/size loop that repaint
                # competes with the loop on the SAME thread, so the window
                # visibly lags the cursor: measured p95 latency per move
                # step 20.45ms against a 5.56ms display frame. The drift is
                # decorative and nobody can perceive it mid-drag — parking
                # it for the duration is free smoothness. Not consumed:
                # DefWindowProc still has to run the loop.
                self._in_size_move = True
                if self._ui_ready:
                    self._glow.suspend()

            elif msg.message == self._WM_EXITSIZEMOVE:
                self._in_size_move = False
                if self._ui_ready and not self.isMinimized():
                    self._glow.resume()

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
