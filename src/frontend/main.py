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

if sys.platform == "win32":
    import ctypes.wintypes  # MSG / RECT for native window hit-testing

from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPropertyAnimation, Qt, QThread,
    QTimer, Signal,
)
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

# Allow "from utils.helpers import ..." / "from frontend import ..." when
# running as src/frontend/main.py or from a PyInstaller bundle.
_FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_FRONTEND_DIR)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from utils import prefs  # noqa: E402
from utils.helpers import PowerShellTask, TaskResult, ToastManager  # noqa: E402
from frontend import theme as TH  # noqa: E402
from frontend.animations import CascadeAnimator, PageFader  # noqa: E402
from frontend.menu_structure import (  # noqa: E402
    CATEGORIES, DEV_HUB_BUNDLES, DEV_HUB_GROUPS, accent_for_task,
    category_operations, find_action, hub_items, iter_leaf_items,
    requires_admin, search_haystack,
)
from frontend.widgets import (  # noqa: E402
    ActivityDrawer, AmbientGlow, AppSelectorDialog, BreathingIcon,
    CommandPalette, ConfirmDialog, DepthCard, DevHubSelectorDialog,
    ElevatePromptDialog, GlassCard, HubDialog, NavButton, NavPill,
    OfficeWizardDialog, PulseDialog, RecentOperationsPanel,
    ResponsiveGridHost, ShortcutSheetDialog, StartupManagerDialog, TitleBar,
    UpdateCenterDialog, refit_dialog,
)

# ============================================================
#  APP CONSTANTS
# ============================================================
APP_NAME = "PULSE"
# The app version tracks the UI/design-system generation the
# codebase actually is (v10). It had been pinned at 6.1 while the
# design system moved through v7-v10, so the title bar, the sidebar
# footer and QApplication all reported a version no document,
# changelog entry or bug report matched.
APP_VERSION = "10.0"
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
    QUICK_ACTIONS = [
        (0, "UpdateSelectedApps"),    # Software      — Check for Updates
        (1, "UltimatePowerPlan"),     # Optimization  — Ultimate Power Plan
        (2, "CleanCache"),            # Maintenance   — Aggressive Cache Clean
        (3, "DisableTelemetry"),      # Privacy       — Disable Telemetry
        (4, "SystemInfo"),            # Information   — System Info Snapshot
        (5, "CreateRestorePoint"),    # Safety        — Create Restore Point
    ]

    # Concise, dashboard-tailored one-liners so a Quick Action reads as a
    # crisp control-surface button, not a dense paragraph (the category page
    # keeps each operation's fuller description). Keyed by task name.
    ACTION_BLURBS = {
        "UpdateSelectedApps": "Scan installed apps and update your picks.",
        "UltimatePowerPlan":  "Unlock the hidden high-performance scheme.",
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
        self._chip_meta: list[tuple[QLabel, bool]] = []
        self._tel_icons: list[QLabel] = []
        self._tel_values: list[QLabel] = []
        self._tel_captions: list[QLabel] = []
        self._tel_divs: list[QFrame] = []
        self._action_cards: list[GlassCard] = []
        self._cols = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 18, 30, 20)
        root.setSpacing(15)

        # ============ 1. HERO BANNER — unified identity masthead ==========
        self._hero = DepthCard(radius=22)
        self._hero.setObjectName("heroBanner")
        self._hero.setFixedHeight(116)
        hb = QHBoxLayout(self._hero)
        hb.setContentsMargins(30, 0, 26, 0)
        hb.setSpacing(20)

        self._logo = BreathingIcon("✦", size=64, accent=t["accent"])
        hb.addWidget(self._logo, 0, Qt.AlignmentFlag.AlignVCenter)

        id_col = QVBoxLayout()
        id_col.setSpacing(4)
        id_col.addStretch()
        self._name = QLabel(APP_NAME)
        id_col.addWidget(self._name)
        self._tag = QLabel("Enterprise-Grade Windows Orchestration")
        id_col.addWidget(self._tag)
        id_col.addStretch()
        hb.addLayout(id_col)
        hb.addStretch()

        chip_col = QVBoxLayout()
        chip_col.setSpacing(9)
        chip_col.addStretch()
        for icon, text, ok in (
            ("🧠", "Engine Ready" if engine_ok else "Engine Missing", engine_ok),
            ("🔑", "Administrator" if is_admin else "Not Elevated", is_admin),
        ):
            chip = QLabel(f"{icon}  {text}")
            self._chip_meta.append((chip, ok))
            chip_col.addWidget(chip, 0, Qt.AlignmentFlag.AlignRight)
        chip_col.addStretch()
        hb.addLayout(chip_col)
        root.addWidget(self._hero)

        # ============ 2. SYSTEM TELEMETRY RIBBON ==========================
        # The three OS/CPU/RAM readouts, folded from floating tiles into one
        # cohesive glass strip with hairline dividers — a real system-status
        # bar rather than scattered mini-cards.
        self._telemetry = DepthCard(radius=16)
        self._telemetry.setObjectName("telemetry")
        self._telemetry.setFixedHeight(62)
        tb = QHBoxLayout(self._telemetry)
        tb.setContentsMargins(10, 8, 10, 8)
        tb.setSpacing(0)
        insights = _system_insights()
        for i, (icon, value, caption) in enumerate(insights):
            if i > 0:
                div = QFrame()
                div.setFixedWidth(1)
                div.setFixedHeight(30)
                self._tel_divs.append(div)
                tb.addWidget(div, 0, Qt.AlignmentFlag.AlignVCenter)

            cell = QHBoxLayout()
            cell.setContentsMargins(18, 0, 18, 0)
            cell.setSpacing(12)
            icon_lbl = QLabel(icon)
            self._tel_icons.append(icon_lbl)
            cell.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

            text_col = QVBoxLayout()
            text_col.setSpacing(0)
            # elide long OS strings ("Windows 11 Professional") against a
            # generous per-cell budget so nothing clips mid-glyph
            value_font = QFont("Segoe UI")
            value_font.setPixelSize(15)
            value_font.setWeight(QFont.Weight.DemiBold)
            elided = QFontMetrics(value_font).elidedText(
                value, Qt.TextElideMode.ElideRight, 210)
            value_lbl = QLabel(elided)
            if elided != value:
                value_lbl.setToolTip(value)
            self._tel_values.append(value_lbl)
            text_col.addWidget(value_lbl)
            caption_lbl = QLabel(caption)
            self._tel_captions.append(caption_lbl)
            text_col.addWidget(caption_lbl)
            cell.addLayout(text_col)
            cell.addStretch()
            tb.addLayout(cell, 1)
        root.addWidget(self._telemetry)

        root.addSpacing(2)

        # ============ 3. QUICK ACTIONS ====================================
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
        for cat_index, task in self.QUICK_ACTIONS:
            item, accent = find_action(cat_index, task)
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

        self.apply_theme(t)

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
        for row in range(max(self._grid.rowCount(), n_rows) + 1):
            self._grid.setRowStretch(row, 1 if row < n_rows else 0)
        for i, card in enumerate(self._action_cards):
            self._grid.addWidget(card, i // cols, i % cols)

    # Column counts are driven by ResponsiveGridHost.resized (see the grid
    # construction above), so no resizeEvent/showEvent width guessing here.

    def apply_theme(self, t: dict):
        self._logo.apply_theme(t)
        self._hero.setStyleSheet(TH.hero_banner_qss(t))
        # authoritative masthead wordmark — larger and tighter than the old
        # spread-out splash "hero" role
        self._name.setStyleSheet(
            f"color: {t['text']}; font-size: 34px; font-weight: 800;"
            "letter-spacing: 2px; background: transparent; border: none;")
        self._tag.setStyleSheet(
            TH.label_qss(t, "tagline") + "font-size: 12px; letter-spacing: 1px;")
        for chip, ok in self._chip_meta:
            chip.setStyleSheet(TH.chip_qss(t, ok))

        self._telemetry.setStyleSheet(TH.telemetry_qss(t))
        for lbl in self._tel_icons:
            lbl.setStyleSheet("font-size: 17px; background: transparent; border: none;")
        for lbl in self._tel_values:
            lbl.setStyleSheet(
                f"color: {t['text']}; font-size: 15px; font-weight: 600;"
                "background: transparent; border: none;")
        for lbl in self._tel_captions:
            lbl.setStyleSheet(TH.label_qss(t, "caption"))
        for div in self._tel_divs:
            div.setStyleSheet(f"background: {t['panel_line']}; border: none;")

        self._section.setStyleSheet(TH.label_qss(t, "section"))
        self._rule.setStyleSheet(TH.hub_group_rule_qss(t, t["accent"]))
        self._scroll.setStyleSheet(TH.scroll_area_qss(t))
        for card in self._action_cards:
            card.apply_theme(t)


class CategoryPage(QWidget):
    """One category: header (back · title · home) + scrollable card grid.

    The grid is responsive: column count follows the viewport width so a
    card never drops below MIN_CARD_W and clips its copy. Floating at the
    default size reads as a spacious 2-column layout; maximized widescreen
    gets 3 columns; a small floating window falls back to a single,
    fully-readable column."""

    MAX_COLUMNS = 4
    MIN_CARD_W = 288   # v9.1: tighter cards → more columns, higher density

    home_requested = Signal()
    task_requested = Signal(dict, object)  # (item, GlassCard)

    def __init__(self, category: dict, t: dict):
        super().__init__()
        self.category = category
        self.cards: list[GlassCard] = []
        self._visible: list[GlassCard] = []
        self._t = t
        self._cols = 0

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

        # -- v10 filter rail: the header's right-hand side ---------------
        # The whole right two-thirds of this row was empty. It now carries
        # the two things a module page can usefully say about itself: how
        # to narrow it, and how much is in it. The filter matches titles,
        # descriptions AND a hub's sub-item titles (see
        # menu_structure.search_haystack), so searching "office" surfaces
        # the hub that contains it rather than hiding a real match.
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
        self._filter.setFixedSize(200, 32)
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
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

        for idx, item in enumerate(category["items"]):
            # v7 bento: the first card of a hub landing page (Software
            # Management) is the featured hero — squircle + Aurora lit edge on
            # the top elevation tier. Only hub cards qualify (they never enter
            # the running/flash states the featured card's painted background
            # forgoes), so dense action pages just get the balanced fill grid.
            featured = idx == 0 and bool(item.get("hub"))
            card = GlassCard(item, category["accent"], t, featured=featured)
            card.clicked.connect(
                lambda it=item, c=card: self.task_requested.emit(it, c))
            card.navigate.connect(
                lambda direction, c=card: _focus_neighbour(
                    self._visible, self._cols, c, direction))
            self.cards.append(card)
        # Everything below re-columns over VISIBLE cards only, so filtering
        # reflows the grid instead of leaving holes where hidden cards were.
        self._visible = list(self.cards)
        self._relayout(2)   # safe default; the first resize event corrects it

        # Empty state — a filter that matches nothing must say so; a blank
        # grid is indistinguishable from a broken page.
        self._empty = QLabel("No operations match that filter.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        self._grid.addWidget(self._empty, self.MAX_COLUMNS + 1, 0, 1, self.MAX_COLUMNS)

        self._scroll.setWidget(grid_host)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        lay.addWidget(self._scroll, 1)

        self.apply_theme(t)

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
        unit = max(self.MIN_CARD_W, widest)
        fits = (viewport_w + gap) // (unit + gap)
        return max(1, min(self.MAX_COLUMNS, fits))

    # -- filtering -------------------------------------------------
    def _apply_filter(self, text: str):
        query = text.strip().lower()
        self._visible = [
            card for card in self.cards
            if not query or query in search_haystack(card.item)
        ]
        shown = set(id(c) for c in self._visible)
        for card in self.cards:
            card.setVisible(id(card) in shown)
        self._empty.setVisible(bool(query) and not self._visible)
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
        filtering = bool(self._filter.text().strip())
        if filtering:
            self._count_chip.setText(f"{len(self._visible)} OF {len(self.cards)}")
        else:
            self._count_chip.setText(
                f"{total} OPERATION{'S' if total != 1 else ''}")
        self._count_chip.setStyleSheet(TH.count_chip_qss(
            self._t, TH.resolve_accent(self._t, self.category["accent"]),
            filtered=filtering))

    def _relayout(self, cols: int):
        if cols == self._cols:
            return
        self._cols = cols
        for card in self.cards:
            self._grid.removeWidget(card)
        for col in range(self.MAX_COLUMNS):
            self._grid.setColumnStretch(col, 1 if col < cols else 0)
        # v7 spatial fix: give every OCCUPIED row an equal stretch so the
        # cards share the leftover vertical space and FILL the canvas, instead
        # of the old single trailing spacer row that top-anchored the grid and
        # stranded ~50% of the page as dead middle space (the redesign's #1
        # complaint). Rows past the last occupied one collapse to zero.
        n_rows = (len(self._visible) + cols - 1) // cols
        for row in range(max(self._grid.rowCount(), n_rows) + 1):
            self._grid.setRowStretch(row, 1 if row < n_rows else 0)
        for i, card in enumerate(self._visible):
            self._grid.addWidget(card, i // cols, i % cols)

    # Column counts are driven by ResponsiveGridHost.resized (see the grid
    # construction above): the width that chooses the column count IS the
    # width the cards are laid out in, so the two can never disagree. This
    # replaces the old resizeEvent/showEvent pair, which measured the page
    # and the scroll viewport respectively — two different numbers, one of
    # them lagging a layout pass behind the other.

    def focus_filter(self):
        """Ctrl+L / Ctrl+F target — select-all so typing replaces whatever
        query is already there, matching every browser address bar."""
        self._filter.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._filter.selectAll()

    def apply_theme(self, t: dict):
        self._t = t
        accent = TH.resolve_accent(t, self.category["accent"])
        self._filter.setStyleSheet(TH.filter_input_qss(t, accent))
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
        self._running_item: dict | None = None
        self._running_accent = ""
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
        self._apply_theme(self.theme.t)
        self._refresh_recent()
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
        self._shell.setStyleSheet(TH.shell_qss(t))
        self._glow.apply_theme(t)
        self._sidebar.setStyleSheet(TH.sidebar_qss(t))
        self._content.setStyleSheet(TH.content_qss(t))
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

    def _on_tweak_state(self, result: TaskResult):
        state = result.data if isinstance(result.data, dict) else None
        if not state:
            return
        self._tweak_state = state
        for page in self.pages:
            for card in page.cards:
                card.set_applied(state.get(card.item.get("task")))
        for card in self.welcome.action_cards():
            card.set_applied(state.get(card.item.get("task")))

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

    # ============================================================
    #  KEYBOARD LAYER (v10)
    # ============================================================
    # Before v10 the app had exactly two shortcuts (Escape, Ctrl+K) and the
    # card grid could not be reached from the keyboard at all. The table
    # below is the single source of truth for both the bindings and the
    # help sheet, so a shortcut can never exist without being documented.
    SHORTCUTS = [
        ("Ctrl+K",        "Command palette"),
        ("Ctrl+L",        "Filter this module"),
        ("Ctrl+H",        "Go to the dashboard"),
        ("Ctrl+1 … 6",    "Jump to a module"),
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
        bind("Ctrl+L", self._focus_page_filter)
        bind("Ctrl+F", self._focus_page_filter)   # the other muscle memory
        bind("Ctrl+\\", self.activity.toggle_pinned)
        bind("F1", self._open_shortcut_sheet)
        bind("?", self._open_shortcut_sheet)
        for i in range(len(CATEGORIES)):
            bind(f"Ctrl+{i + 1}", lambda idx=i: self.open_category(idx))

    def _focus_page_filter(self):
        """Ctrl+L on a module page focuses its filter; on the dashboard,
        where there is no filter, it falls back to the command palette so
        the key never does nothing."""
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
        if self._thread is not None and self._thread.isRunning():
            self.toasts.show("info", "A task is already running — please wait.", 3000)
            return
        if not self.ps1_path:
            self.toasts.show("error", f"{PS1_FILENAME} not found — engine unavailable.", 5000)
            return

        app_ids: list[str] | None = None
        office_paths: tuple[str, str] | None = None
        local_installer: tuple[str, str] | None = None
        if item.get("startup_manager"):
            # Fully self-contained: scans, groups by recommendation and
            # flips items live via its own workers. Nothing to hand back —
            # open it and move on, exactly like a plain informational card.
            StartupManagerDialog(self, self.ps1_path, self.theme.t).exec()
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
        elif item.get("devhub"):
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
        # remembered for the Recent Operations trail once this settles
        self._running_item = item
        self._running_accent = accent_for_task(item.get("task"))
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
        # flash=None and is deliberately left out of the trail.
        if flash:
            self._record_recent(flash)
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
        if self._worker is not None:
            self.toasts.show(
                "info", "Wait for the current task to finish before restarting elevated.", 4000)
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
            TH.apply_blur_behind(hwnd)      # real DWM blur behind the shell
            TH.apply_native_rounding(hwnd, rounded=not self.isMaximized())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._glow.setGeometry(self._shell.rect())
        self.toasts.reposition()
        active = QApplication.activeModalWidget()
        if isinstance(active, PulseDialog):
            refit_dialog(active)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            # Pause the living-background loop while minimized (hideEvent does
            # NOT fire on minimize, so the ~28fps timer would otherwise keep
            # running behind an invisible window). Resumes on restore.
            if self.isMinimized():
                self._glow.suspend()
            else:
                self._glow.resume()
            # Maximized = edge-to-edge: the shell drops its floating radius
            # and border (see shell_qss) so corners sit flush with the
            # monitor, exactly like a native maximized Win11 window.
            # (`flush`, not `maximized`: QWidget's built-in read-only
            # `maximized` property would swallow the write.)
            flush = self.isMaximized()
            self._shell.setProperty("flush", flush)
            self._shell.style().unpolish(self._shell)
            self._shell.style().polish(self._shell)
            self._glow.set_radius(0 if flush else 24)
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

    def closeEvent(self, event):
        """Guard against orphaning the backend process tree: if a
        PowerShellTask is still running when the window closes (the X
        button, Alt+F4, or the custom caption's close control below all
        end up here via Qt's normal close path), cancel it and give the
        process-tree kill a moment to land before the QThread gets torn
        down - otherwise winget/DISM/sfc children spawned by core.ps1 are
        left running headless after the GUI disappears."""
        prefs.set_window_geometry(self.saveGeometry())
        prefs.set_drawer_pinned(self.activity.is_pinned())
        if self._thread is not None and self._thread.isRunning():
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
