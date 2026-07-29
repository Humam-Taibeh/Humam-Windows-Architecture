"""
src/frontend/theme.py

DESIGN SYSTEM — Apple-level Glassmorphism, dual theme (Premium Dark / Clean Light).

This module owns every color, every QSS string and the theme switcher.
Nothing here imports widgets or main — it is a pure leaf dependency:

    theme.py  <-  animations.py  <-  widgets.py  <-  main.py

Public surface:
    ThemeManager        live theme state + `changed` signal (no restart needed)
    tokens("dark")      raw token dict for a mode
    alpha("#00d4ff",x)  hex -> rgba() with opacity
    *_qss(t, ...)       QSS factory functions, each takes a token dict
    apply_native_rounding() / enable_native_sizing_frame()
                        DWM corner + Win32 frame integration (Windows, ctypes only)

Rules:
    - QSS is built ONCE per theme switch and applied per widget class.
      Never rebuild stylesheets inside timers/animations (style re-polish
      is the most expensive repeated operation in Qt).
    - Continuous animation colors come from tokens too — animations.py
      reads them, paints them; it never touches QSS.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase

# ============================================================
#  COLOR UTILITIES
# ============================================================
def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def alpha(color: str, opacity: float) -> str:
    """'#00d4ff', 0.25 -> 'rgba(0, 212, 255, 0.25)' — for QSS."""
    r, g, b = _hex_to_rgb(color)
    return f"rgba({r}, {g}, {b}, {opacity:.3f})"


def to_qcolor(value: str) -> QColor:
    """Parse a token string ('#rrggbb' or 'rgba(r, g, b, a)') into a QColor,
    so painted widgets (the featured card's squircle fill, for example) can
    render from the SAME tokens the QSS surfaces use — no second, drifting
    copy of a color hardcoded in a widget."""
    s = value.strip()
    if s.startswith("rgba") or s.startswith("rgb"):
        inner = s[s.index("(") + 1: s.index(")")]
        parts = [p.strip() for p in inner.split(",")]
        r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
        c = QColor(r, g, b)
        if len(parts) > 3:
            c.setAlphaF(float(parts[3]))
        return c
    return QColor(s)


def glass_fill(t: dict, base: str, sheen_stop: float = 0.13) -> str:
    """The one frosted-glass gradient every translucent surface in the app
    shares: a top sheen highlight falling into a flat base tone. Cards, the
    Welcome hero banner and dialog panels all call this with their own base
    color so the whole app reads as one material, not several slightly
    different ad-hoc gradients (which is what card_qss and the old insight
    tiles had before this — 0.12 vs 0.15 sheen stops, purely accidental)."""
    return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {t['card_sheen']}, stop:{sheen_stop} {base}, stop:1 {base})")


def brand_gradient(t: dict, a1: float, a2: float | None = None) -> str:
    """The app's signature two-tone sweep (accent -> accent2). Before this,
    accent2 (the violet half of the brand pair) was painted nowhere but the
    shimmer bar — every other 'primary' surface used a flat single-color
    alpha fill. Reused sparingly here (primary dialog buttons, the selected
    nav item, the running-state pill) so the duotone reads as a deliberate
    system, not a one-off."""
    if a2 is None:
        a2 = a1
    return (f"qlineargradient(x1:0, y1:0, x2:1, y2:1, "
            f"stop:0 {alpha(t['accent'], a1)}, stop:1 {alpha(t['accent2'], a2)})")


def resolve_accent(t: dict, accent: str) -> str:
    """A MODULE KEY ('software') -> that module's accent for the CURRENT
    theme; a literal '#rrggbb' passes straight through.

    v10: the six module colours used to be single hex literals living in
    menu_structure.py, so light mode reused values tuned for a near-black
    canvas — every one of them measured 1.86-2.64:1 against the porcelain
    card, far under the 3:1 floor for an icon, which is why the "Spectrum"
    identity washed out in light mode. They are tokens now (one set per
    mode, solved so each clears 4.5:1 as text on the card and 3:1 as a glyph
    inside its own tinted plaque well), and menu_structure carries only the
    semantic key. Widgets MUST store the key and call this from
    apply_theme() — resolving once at construction would freeze a card on
    whichever theme happened to be active when it was built."""
    if not accent:
        return t["accent"]
    if accent.startswith("#") or accent.startswith("rgb"):
        return accent
    return t["module"].get(accent, t["accent"])


# ============================================================
#  SPACING & RADIUS SCALE (v10)
# ============================================================
# Before v10 the codebase used 13 distinct setSpacing() values (2,4,7,8,9,
# 10,12,13,14,15,16,20) and 17 distinct border-radius values, with margins
# like (15,13,16,13) and (30,18,30,20) that read as accidents rather than
# decisions — the root cause of the app's "almost aligned" feel. Everything
# now comes from these two scales; a new surface picks the nearest step
# instead of inventing another number.
SPACE = {
    "xs":  4,    # icon<->label, tight inline pairs
    "sm":  8,    # inside a row / between sibling controls
    "md":  12,   # between related blocks
    "lg":  16,   # grid gutters, card padding
    "xl":  24,   # section separation, dialog padding
}

# Semantic radii — named by the surface they belong to, so a card and a
# dialog can never drift a pixel apart by accident.
RADIUS = {
    "chip":    8,    # pills, badges, small tags
    "control": 10,   # buttons, inputs
    "plaque":  12,   # icon wells, nav entries, list rows
    "card":    16,   # GlassCard, action surfaces
    "panel":   20,   # sidebar, content frame, dialog panels
    # No "shell" entry: the window's own corners are rounded by DWM
    # (apply_native_rounding), not by QSS. A radius painted here would only
    # carve wedges out of the opaque shell and expose the bare window
    # palette behind them.
}


def bevel_alphas(t: dict) -> tuple[float, float]:
    """(light_alpha, dark_alpha) for animations.paint_bevel_frame, tuned per
    mode. Qt QSS has no box-shadow, so cards can't cast a real drop shadow;
    in LIGHT mode a white card on porcelain therefore leans on a deeper
    bottom-right edge (dark_alpha up) to read as a contact shadow lifting it
    off the page — the painted stand-in for the shadow QSS can't give. Dark
    mode keeps the original balanced glass bevel."""
    if t["name"] == "light":
        # near-white cards make a top-left WHITE highlight moot, so spend the
        # bevel entirely on a firmer bottom-right contact shadow — the 1px
        # stand-in for the drop shadow QSS can't cast, lifting the card off
        # the deeper v8 slate canvas.
        return (0.0, 0.34)
    return (0.14, 0.20)


# ============================================================
#  ICON SYSTEM — monochrome Fluent line-icons (v7)
# ============================================================
# The v7 iconography is a single monochrome line-icon family with ZERO new
# asset pipeline: Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets
# (Windows 10) — the same OS-native icon font the title-bar caption buttons
# already use (see widgets.TitleBar). Every icon is one glyph rendered in
# an accent-tinted plaque; because it's a font, it inherits the theme color
# for free and re-skins live.
#
# GLYPHS maps a semantic name -> (fluent_codepoint, emoji_fallback). The
# codepoint is used whenever the OS font is present; the emoji is used only
# when it is NOT (non-Windows dev, or a stripped Win10 without the font),
# so nothing ever renders blank. Menu items opt in by adding a `glyph` key
# (see menu_structure.py); an item WITHOUT one still renders its plain
# emoji `icon` inside the same plaque, so the system is incrementally
# adoptable and never regresses.
_ICON_FONT_FAMILY: str | None | bool = False   # False = "not resolved yet"


def _resolve_icon_family() -> str | None:
    """The best available OS icon font family, resolved once and cached.
    None on non-Windows / when neither font is installed."""
    global _ICON_FONT_FAMILY
    if _ICON_FONT_FAMILY is not False:
        return _ICON_FONT_FAMILY  # type: ignore[return-value]
    family: str | None = None
    if sys.platform == "win32":
        installed = set(QFontDatabase.families())
        for candidate in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
            if candidate in installed:
                family = candidate
                break
    _ICON_FONT_FAMILY = family
    return family


def icon_font(px: int = 18, weight: QFont.Weight = QFont.Weight.Normal) -> QFont | None:
    """A QFont for the OS icon family at `px` pixels, or None when no icon
    font is available (the caller then renders the emoji fallback in the
    UI's default font). Sized in *pixels* so it stays crisp under fractional
    DPI, exactly like the caption glyphs."""
    family = _resolve_icon_family()
    if family is None:
        return None
    font = QFont(family)
    font.setPixelSize(px)
    font.setWeight(weight)
    return font


def has_icon_font() -> bool:
    return _resolve_icon_family() is not None


# Semantic name -> (Segoe Fluent / MDL2 codepoint, emoji fallback).
# Codepoints are drawn from the long-stable Segoe MDL2 Assets set (all also
# present in Segoe Fluent Icons) — the same well-known PUA glyphs Microsoft
# documents for custom app chrome.
GLYPHS: dict[str, tuple[str, str]] = {
    # --- nav / chrome ---
    'home':          ("", "⌂"),                    # Home
    'chevron':       ("", "›"),                    # ChevronRight
    'back':          ("", "‹"),                    # ChevronLeft
    'lock':          ("", "🔒"),             # Lock — admin-gated affordance
    # --- modules (sidebar) ---
    'package':       ("", "📦"),                    # Software Management
    'bolt':          ("", "⚡"),                    # System Optimization / power
    'repair':        ("", "🔧"),                    # Maintenance / repair / services
    'shield':        ("", "🛡️"),                   # Privacy & Security (padlock)
    'info':          ("", "📊"),                    # Information & Utilities
    'restore':       ("", "🛟"),                    # Safety & Recovery / undo / reset
    # --- software hub cards ---
    'globe':         ("", "🧰"),                    # Browsers & daily apps
    'code':          ("", "🎓"),                    # Developer & University Hub
    'game':          ("", "🎮"),                    # Gaming / Game Mode
    'tools':         ("", "🛠️"),                   # System Tools & Utilities
    # --- optimization ---
    'moon':          ("", "🌙"),                    # Global Dark Mode
    'mouse':         ("", "🖱️"),                   # Mouse acceleration
    'pin':           ("", "📌"),                    # Minimalist Taskbar
    'list':          ("", "📋"),                    # Classic Context Menu
    'network':       ("", "📡"),                    # Network & Ping Optimizer
    # --- maintenance ---
    'broom':         ("", "🧹"),                    # Aggressive Cache Clean
    'disk':          ("", "💾"),                    # Optimize All Drives
    'sleep':         ("", "😴"),                    # Disable Hibernation
    'battery':       ("", "🔋"),                    # Enable Hibernation
    'chart':         ("", "📈"),                    # Drive Space Report (pie)
    # --- privacy / info / safety ---
    'delete':        ("", "🗑️"),                   # Remove Edge / bloatware / Windows.old
    'shieldplain':   ("", "🛡️"),                   # Disable Telemetry (shield)
    'target':        ("", "🎯"),                    # Disable Advertising ID
    'history':       ("", "🕓"),                    # Disable Activity History
    'defender':      ("", "🔒"),                    # Apply ALL Privacy (full shield)
    'chartline':     ("", "📊"),                    # System Info Snapshot
    'save':          ("", "💿"),                    # Driver Backup
    'search':        ("", "🔍"),                    # Missing Driver Scan
    'restorepoint':  ("", "🛟"),                    # Create Restore Point
    'log':           ("", "📜"),                    # View Operation Log
    'folder':        ("", "📁"),                    # OneDrive Backup Folder
    # --- system tools subs ---
    'document':      ("", "📄"),                    # Microsoft Office Suite
    'puzzle':        ("", "🧩"),                    # Core API Runtimes
    'diagnostics':   ("", "🔬"),                    # Hardware Diagnostics
    'terminal':      ("", "🧭"),                    # PATH Doctor
    'boot':          ("", "🚀"),                    # Startup Manager
    'refresh':       ("", "🔄"),                    # Check for Updates
    'sync':          ("", "🔁"),                    # Install / Restore pairs
    'cloud':         ("", "☁️"),                   # OneDrive purge
    # --- console toolbar (v10) ---
    'copy':          ("", "⎘"),   # Copy output to the clipboard
    'clear':         ("", "⌫"),   # Clear the console
    'export':        ("", "⤓"),   # Save output to a file
    'clock':         ("", "◴"),   # Timestamp toggle
}


def glyph(name: str) -> tuple[str, str]:
    """(display_char, is_fluent-safe) — returns the Fluent codepoint when the
    OS font is available, else the emoji fallback. The second tuple element
    tells the caller whether to render it in icon_font() (True) or the
    default UI font (False, for the emoji)."""
    fluent, emoji = GLYPHS.get(name, ("", ""))
    if fluent and has_icon_font():
        return (fluent, True)  # type: ignore[return-value]
    return (emoji, False)      # type: ignore[return-value]


# ============================================================
#  TOKENS — OBSIDIAN DARK (v7 "Aurora")
# ============================================================
# Design intent: a deeper obsidian register than v6.2's charcoal — the
# canvas floor drops toward near-black (#070809) so elevated surfaces read
# as genuinely floating, and a NEW top elevation tier (`card_hi`) lets the
# featured/hero bento card sit a visible step above the standard cards.
# The v7 brand is the signature "Aurora" tri-tone — indigo → violet →
# magenta — used deliberately (painted, saturated) on hero edges, the
# selected nav rail and primary CTAs, while every neutral surface stays
# calm obsidian. The primary interactive `accent` is indigo-forward so
# body UI (borders, focus, hovers) never gets loud.
_DARK = {
    "name":        "dark",
    "font":        "Segoe UI",

    # ---- v9 "Spectrum" surfaces --------------------------------------
    # The v7/v8 obsidian floor (#070809, near-black) was the whole "too
    # dark, lifeless" problem: cards barely cleared the canvas, so the app
    # read as one flat dark sheet with no elevation. v9 lifts the floor into
    # a refined BLUE-GRAPHITE deep-space register — every neutral now carries
    # a little blue chroma (the Linear/Vercel "alive dark" tell, vs. dead
    # gray) — and, crucially, opens a real perceptual GAP between the canvas
    # and the card tier so surfaces genuinely float.
    "bg":          "rgba(16, 18, 27, 0.97)",
    "bg_solid":    "#10121b",
    # shell gradient — a lit deep-space fall: a lifted indigo top settling
    # into a rich near-black-blue floor (not cold near-black).
    "bg_grad_top":    "#1c2131",
    "bg_grad_bottom": "#0a0b11",
    # v10 ELEVATION: the content well is now genuinely RECESSED (0.34 →
    # 0.55) rather than a near-invisible 1.03:1 tint over the canvas. This
    # is the move that finally makes cards float: measured card-vs-well
    # separation goes 1.20:1 → 1.46:1 WITHOUT brightening the card enough
    # to wreck the text ramp sitting on it (the naive "lighten the card"
    # fix forced text_muted and text_faint so high they collapsed into one
    # tone). Depth is bought by digging the hole, not raising the object.
    "overlay":     "rgba(4, 5, 10, 0.55)",
    "panel":       "rgba(255, 255, 255, 0.045)",
    "panel_line":  "rgba(255, 255, 255, 0.078)",
    "card":        "rgba(43, 49, 69, 0.94)",
    # hero/featured tier — a clear further step (1.31:1) above the card.
    "card_hi":     "rgba(59, 66, 96, 0.96)",
    "card_hover":  "rgba(125, 155, 255, 0.10)",
    "card_line":   "rgba(255, 255, 255, 0.10)",
    "card_sheen":  "rgba(255, 255, 255, 0.06)",   # top stop of the glass gradient
    # Dialogs and toasts sit OVER dense text (card grids, the console):
    # fully/near-fully opaque, or the content underneath bleeds through
    # and reads as overlapping text.
    "dialog_bg":   "rgba(20, 23, 33, 1.0)",
    "toast_bg":    "rgba(28, 32, 45, 0.99)",

    # brand — Aurora tri-tone, tuned a touch more electric/vivid for v9:
    # indigo (primary) → violet → magenta.
    "accent":      "#7d9bff",
    "accent2":     "#a184ff",
    "accent3":     "#e784ff",

    # ---- v10 TEXT RAMP -----------------------------------------------
    # Rebuilt from a measurement, not by eye. The two lowest steps carry
    # real 10-13px copy (captions, meta pills, card descriptions), and on
    # the composited card surface the old values measured 3.00:1
    # (text_faint) and 3.98:1 (text_muted) — both under AA for body text.
    # Simply lifting them collapsed the ramp into three indistinguishable
    # tones, so the whole thing is rebuilt EVENLY IN CIE L*: the floor is
    # pinned at 4.55:1 on the card and the remaining three steps are spaced
    # perceptually up to the brightest. Four visibly distinct tones, every
    # one of them legible.
    "text":        "#eef1f6",   # 11.76:1 on card
    "text_soft":   "#d3d6dd",   #  9.14:1
    "text_muted":  "#b4b9c5",   #  6.80:1
    "text_faint":  "#8f97a8",   #  4.55:1  <- the floor

    # ---- v10 MODULE ACCENTS ------------------------------------------
    # The six sidebar/category colours, now real tokens (see
    # resolve_accent). Solved to clear 4.5:1 as text on the card and 3:1
    # as a glyph in their own plaque well, with saturation held as high as
    # those floors allow so the set still reads as a spectrum.
    "module": {
        "software":     "#5e96ff",
        "optimization": "#fba913",
        "maintenance":  "#18dbb3",
        "privacy":      "#ec6f96",
        "information":  "#6598ff",
        "safety":       "#42cd82",
        # v10.3 — Automation (playbooks, health report). Violet is the one
        # hue the original six left unclaimed, so the module reads as new
        # rather than as a relative of an existing one. 6.15:1 here, inside
        # the 5.55-9.02 band the other dark accents occupy.
        "automation":   "#b18cff",
    },

    # status — GitHub-dark grade: unmistakable but never neon
    "ok":          "#3fb950",
    "warn":        "#d29922",
    "err":         "#f85149",
    "danger_line": "rgba(248, 81, 73, 0.30)",

    # chrome
    "scroll":      "rgba(255, 255, 255, 0.13)",
    "scroll_hov":  "rgba(124, 147, 255, 0.50)",
    "shimmer_track": (255, 255, 255, 12),      # QColor args for painted widgets
    "titlebar_hover": "rgba(255, 255, 255, 0.06)",
    "close_hover":    "#c42b1c",               # native Win11 caption red
    # modal backdrop — dense enough that the card grid underneath is
    # fully masked while a dialog is open (QColor args, painted widget)
    "scrim":          (5, 7, 10, 195),
}

# ============================================================
#  TOKENS — PORCELAIN LIGHT (v7 "Aurora")
# ============================================================
# Design intent: comfortable studio-white, not blinding — a warm porcelain
# canvas (nudged ~2% off cool-gray toward paper-white) with soft-white
# raised surfaces. Pure #ffffff appears only on cards (and translucently),
# never as the page itself, so the mode reads like paper under studio
# light instead of a lightbox. The Aurora sweep is restated here in
# deeper, ink-saturated stops so it reads BOLD on paper, not pastel.
_LIGHT = {
    "name":        "light",
    "font":        "Segoe UI",

    # ---- v9.1 "Porcelain Glass" — soothing premium light -------------
    # The prior light mode read as a harsh, flat white void: canvas, content
    # veil and cards all sat within a few % of pure white, so nothing
    # separated and the brightness fatigued the eye. v9.1 rebuilds it as a
    # layered PORCELAIN GLASS stack — a soft, cool off-white canvas with a
    # real top-to-bottom gradient for depth, a distinctly deeper content
    # veil, frosted panels, and clean off-white cards lifted by firm
    # hairlines + a painted contact shadow. Nothing is pure #ffffff except
    # the hero tier, so the whole mode reads as paper under studio light,
    # not a lightbox.
    "bg":          "rgba(222, 228, 239, 0.98)",
    "bg_solid":    "#dee4ef",
    # v10: the canvas floor drops further (#c8d2e3 → #b6c2da). In light
    # mode elevation CANNOT come from brightening the card — a near-white
    # card on a near-white page has nowhere to go (pure #ffffff over the
    # old card measured 1.02:1, i.e. invisible). Depth has to come from
    # darkening everything the card sits on, so the canvas deepens and the
    # card stays the brightest thing on screen by a real margin.
    "bg_grad_top":    "#f2f5fb",
    "bg_grad_bottom": "#b6c2da",
    "overlay":     "rgba(255, 255, 255, 0.24)",
    # frosted panels (sidebar / dock) — a soft white glass, clearly a step
    # above the tinted content veil, clearly below the crisp cards.
    "panel":       "rgba(255, 255, 255, 0.62)",
    "panel_line":  "rgba(28, 38, 56, 0.11)",
    # clean off-white cards — the crisp top layer, separated by a firm
    # hairline + painted contact shadow rather than blinding brightness.
    "card":        "rgba(255, 255, 255, 0.97)",
    # NOTE: the hero tier is pure white and therefore CANNOT out-lighten
    # the card. In light mode it earns its distinction from the painted
    # aurora edge + contact shadow (widgets.GlassCard._paint_featured),
    # not from luminance — chasing a lighter-than-white card is the one
    # elevation move this mode can never make.
    "card_hi":     "rgba(255, 255, 255, 1.0)",
    "card_hover":  "rgba(74, 92, 224, 0.06)",
    # A firm, clean hairline — the "clean borders" the redesign calls for;
    # cards lean on this + the painted contact shadow (bevel_alphas) to
    # separate, so it can't be timid.
    "card_line":   "rgba(28, 38, 56, 0.16)",
    "card_sheen":  "rgba(255, 255, 255, 0.85)",   # top stop of the glass gradient
    # Same opacity rule as dark: overlays never let text bleed through.
    "dialog_bg":   "rgba(248, 250, 253, 1.0)",
    "toast_bg":    "rgba(252, 253, 255, 0.99)",

    # brand — Aurora tri-tone, ink-saturated for paper: indigo → violet → magenta
    "accent":      "#4a5ce0",
    "accent2":     "#7a4fd0",
    "accent3":     "#c24fd0",

    # v10 text ramp — same L*-even construction as dark (see the note in
    # _DARK). text_faint measured 3.94:1 before and carries 10px captions;
    # it is now pinned at 4.55:1 with the three steps above it spaced
    # perceptually down to near-ink.
    "text":        "#15191f",   # 17.51:1 on card
    "text_soft":   "#2b323c",   # 12.85:1
    "text_muted":  "#454f5f",   #  8.21:1
    "text_faint":  "#67778e",   #  4.55:1  <- the floor

    # v10 module accents, ink-saturated for paper. Same solve as dark: 4.5:1
    # as text on the card, 3:1 as a glyph in the plaque well. Amber is the
    # one hue that cannot be both bright and legible on white, so
    # 'optimization' lands as a deep gold rather than a light one.
    "module": {
        "software":     "#1969ff",
        "optimization": "#9a6b17",
        "maintenance":  "#1f826f",
        "privacy":      "#e11c59",
        "information":  "#2069ff",
        "safety":       "#328357",
        # v10.3 — Automation. Solved to 4.27:1, deliberately matching the
        # 4.25-4.29 band the other light accents share: on paper these read
        # as a set only if they carry the same visual weight, so hitting the
        # peer ratio matters more than maximising contrast.
        "automation":   "#7064d8",
    },

    # status — GitHub-light grade
    "ok":          "#1a7f37",
    "warn":        "#9a6700",
    "err":         "#cf222e",
    "danger_line": "rgba(207, 34, 46, 0.35)",

    "scroll":      "rgba(22, 28, 38, 0.16)",
    "scroll_hov":  "rgba(74, 92, 224, 0.55)",
    "shimmer_track": (22, 28, 38, 16),
    "titlebar_hover": "rgba(22, 28, 38, 0.06)",
    "close_hover":    "#c42b1c",               # native Win11 caption red
    # modal backdrop — dark scrims read premium in light mode too
    "scrim":          (18, 24, 33, 130),
}

_MODES = {"dark": _DARK, "light": _LIGHT}


def tokens(mode: str) -> dict:
    return _MODES[mode]


# ============================================================
#  THEME MANAGER — live switching, no restart
# ============================================================
class ThemeManager(QObject):
    """Single app-wide instance. Widgets connect to `changed` and re-apply
    their QSS from the new token dict; painted widgets just repaint."""

    changed = Signal(dict)

    def __init__(self, mode: str = "dark", parent: QObject | None = None):
        super().__init__(parent)
        self._mode = mode if mode in _MODES else "dark"

    # -- state ------------------------------------------------
    @property
    def t(self) -> dict:
        return _MODES[self._mode]

    def set_mode(self, mode: str):
        if mode in _MODES and mode != self._mode:
            self._mode = mode
            self.changed.emit(self.t)

    def toggle(self) -> dict:
        self.set_mode("light" if self._mode == "dark" else "dark")
        return self.t


# ============================================================
#  QSS FACTORIES — one call per theme switch, never per frame
# ============================================================
def shell_qss(t: dict) -> str:
    """Maximized = edge-to-edge: the floating radius/border must vanish so
    the shell meets the monitor edges exactly like a native Win11 app.
    NOTE: the dynamic property is named `flush` (not `maximized`) because
    QWidget already exposes a built-in read-only `maximized` property —
    setProperty() on that name silently fails."""
    grad = (f"qlineargradient(x1:0, y1:0, x2:0.3, y2:1, "
            f"stop:0 {t['bg_grad_top']}, stop:1 {t['bg_grad_bottom']})")
    # The shell is now a FULLY OPAQUE, square canvas that covers every pixel
    # of the window, in both states.
    #
    # It used to carry a 24px radius and a 1px border, which only worked
    # because the window itself was WA_TranslucentBackground: the four
    # corner wedges outside the radius were alpha-0 and simply vanished.
    # On an opaque window those same wedges expose the bare QMainWindow
    # palette instead — the dark square "ears" behind the rounded shell.
    # Windows 11 rounds and borders the window for us at the compositor
    # (DWMWCP_ROUND, see apply_native_rounding), so the shell must NOT
    # round itself; DWM clips the real thing, pixel-perfect and glitch-free.
    return f"""
        #shell {{
            background: {grad};
            border: none;
            border-radius: 0px;
        }}
    """


def sidebar_qss(t: dict) -> str:
    return f"""
        QFrame {{
            background: {t['panel']};
            border-radius: {RADIUS['panel']}px;
            border: 1px solid {t['panel_line']};
        }}
    """


def content_qss(t: dict) -> str:
    return f"""
        QFrame {{
            background: {t['overlay']};
            border-radius: {RADIUS['panel']}px;
            border: 1px solid {t['panel_line']};
        }}
    """


def nav_button_qss(t: dict) -> str:
    """v9 ghost rail: at rest the nav entry is a bare, transparent row —
    only its colored icon plaque and label carry weight — so the sidebar
    reads light, airy and modern (the Linear / VS Code activity-bar feel)
    instead of a stack of heavy filled pills floating over a void. Hover and
    the selected state are where surface and the Aurora brand sweep light
    up, so the pointer always gets a clear, premium answer."""
    return f"""
        QPushButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: {RADIUS['plaque']}px;
            color: {t['text_muted']};
            font-size: 13px; font-weight: 500;
            /* padding clears the painted icon plaque (12px inset + 30px
               plaque + gap) — see widgets.NavButton.paintEvent */
            text-align: left; padding-left: 54px;
        }}
        QPushButton:hover {{
            background-color: {t['card_hover']};
            border: 1px solid {alpha(t['accent'], 0.24)};
            color: {t['text']};
        }}
        QPushButton:pressed {{ background-color: {alpha(t['accent'], 0.18)}; }}
        QPushButton[selected="true"] {{
            background-color: {brand_gradient(t, 0.20, 0.13)};
            border: 1px solid {alpha(t['accent'], 0.52)};
            color: {t['text']};
        }}
    """


def card_qss(t: dict, accent: str, danger: bool = False,
             featured: bool = False) -> str:
    # The featured (hero) card paints its OWN squircle background, Aurora lit
    # edge and hover tint (widgets.GlassCard._paint_featured). QSS must
    # therefore draw NOTHING in every state — a rounded-rect fill would peek
    # out past the squircle's continuous corners. It's only ever a hub card
    # (set in main.CategoryPage), which never enters the running/flash
    # states, so losing those QSS rules here costs nothing.
    if featured:
        return "GlassCard { background: transparent; border: none; }"
    line = t["danger_line"] if danger else t["card_line"]
    hover_line = alpha(t["err"], 0.55) if danger else alpha(accent, 0.55)
    # Frosted-glass base: a subtle top sheen via qlineargradient (QSS-native,
    # cached, radius-safe — per-side highlight borders artifact on rounded
    # corners). State rules AFTER base/hover: QSS is last-match-wins at
    # equal specificity, and a verdict flash must outrank a stale hover.
    return f"""
        GlassCard {{
            background-color: {glass_fill(t, t['card'])};
            border: 1px solid {line};
            border-radius: {RADIUS['card']}px;
        }}
        GlassCard:hover {{
            background-color: {t['card_hover']};
            border: 1px solid {hover_line};
        }}
        GlassCard[running="true"] {{
            background-color: {alpha(t['accent'], 0.10)};
            border: 1px solid {t['accent']};
        }}
        GlassCard[flash="ok"] {{
            background-color: {alpha(t['ok'], 0.10)};
            border: 1px solid {alpha(t['ok'], 0.85)};
        }}
        GlassCard[flash="err"] {{
            background-color: {alpha(t['err'], 0.10)};
            border: 1px solid {alpha(t['err'], 0.85)};
        }}
    """


def icon_plaque_qss(t: dict, accent: str, featured: bool = False) -> str:
    """The v7 card icon container — a rounded, accent-tinted plaque holding
    one monochrome Fluent glyph (or its emoji fallback). This is the single
    biggest 'premium app' cue: instead of a bare emoji floating in the card,
    every icon sits in a consistent, color-coordinated well.

    v8.1 unification: EVERY card in EVERY section now shares the exact same
    plaque finish — identical tint, 1px accent line and monochrome glyph
    color — so the icon grid reads as one system page to page. The featured
    hero card earns its lift from its squircle body + Aurora lit edge, NOT a
    louder icon well, which previously made its glyph look bigger/brighter
    than its siblings and broke cross-category consistency. `featured` is
    still accepted for call-site compatibility but no longer alters the
    plaque.

    v9 "Spectrum": the plaque now carries REAL color at rest. Where v8.1
    deliberately went fully monochrome (a soft text_soft glyph in a whisper
    tint), v9 fills the well with a soft vertical accent gradient, firms its
    hairline, and — the key move — paints the glyph in the module's own
    accent. Every card and every sidebar entry therefore reads in its
    module's color the instant the page loads, not only on hover, which is
    what turns the old flat-gray grid into a vibrant, legible spectrum. The
    tint stays low enough (≤0.24α) that the glyph, not the well, is the
    focus, so the effect is jewel-like, never neon."""
    fill = (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {alpha(accent, 0.24)}, stop:1 {alpha(accent, 0.13)})")
    line = alpha(accent, 0.42)
    glyph_color = accent
    return f"""
        QLabel {{
            background: {fill};
            border: 1px solid {line};
            border-radius: {RADIUS['plaque']}px;
            color: {glyph_color};
        }}
    """


def card_meta_pill_qss(t: dict, accent: str = "") -> str:
    """A small count/hint pill in a card's meta footer row ('14 apps',
    'Office', 'Runtimes'). Neutral card-chrome by default; pass an accent to
    tint it (used for the featured card's lead pill)."""
    if accent:
        return f"""
            color: {accent}; font-size: 10px; font-weight: 700;
            background: {alpha(accent, 0.12)}; border: 1px solid {alpha(accent, 0.32)};
            border-radius: {RADIUS['chip']}px; padding: 2px 9px; letter-spacing: 0.5px;
        """
    return f"""
        color: {t['text_muted']}; font-size: 10px; font-weight: 600;
        background: {t['panel']}; border: 1px solid {t['panel_line']};
        border-radius: {RADIUS['chip']}px; padding: 2px 9px; letter-spacing: 0.5px;
    """


def applied_chip_qss(t: dict) -> str:
    """The 'APPLIED' chip on a card whose tweak the backend probe reports
    as currently in effect (v10). Uses the `ok` token — this is a
    confirmation of system state, not an alert — and stays small and quiet
    so a page of applied tweaks reads as reassuring rather than shouty."""
    return f"""
        color: {t['ok']}; font-size: 9px; font-weight: 700;
        background: {alpha(t['ok'], 0.12)};
        border: 1px solid {alpha(t['ok'], 0.38)};
        border-radius: {RADIUS['chip']}px; padding: 2px 8px; letter-spacing: 1px;
    """


def card_history_pill_qss(t: dict) -> str:
    """The 'Ran 3d ago · ~2m' caption on a card that has been run before
    (v10.1). Quieter than both the meta pill and the APPLIED chip — it is
    background information, and must not compete with the applied-state
    signal sitting beside it. Borderless and untinted for that reason:
    weight comes from text colour alone."""
    return f"""
        color: {t['text_faint']}; font-size: 10px; font-weight: 600;
        background: transparent; border: none;
        padding: 2px 2px; letter-spacing: 0.2px;
    """


def card_chevron_qss(t: dict, accent: str) -> str:
    """The trailing '›' drill-in affordance on a hub/action card. Muted at
    rest; the card's own hover glow does the lighting, so this stays a quiet
    directional cue rather than a second competing accent."""
    return (f"color: {t['text_faint']}; font-size: 18px; font-weight: 400;"
            "background: transparent; border: none;")


def nav_pill_qss(t: dict) -> str:
    """Back / Home / theme-toggle pill buttons."""
    return f"""
        QPushButton {{
            background: {t['card']};
            border: 1px solid {t['card_line']};
            border-radius: {RADIUS['control']}px;
            color: {t['text_muted']};
            font-size: 12px; font-weight: 500;
        }}
        QPushButton:hover {{
            background: {t['card_hover']};
            color: {t['text']};
            border: 1px solid {alpha(t['accent'], 0.40)};
        }}
        QPushButton:pressed {{
            background: {alpha(t['accent'], 0.16)};
            border: 1px solid {alpha(t['accent'], 0.55)};
        }}
    """


def filter_input_qss(t: dict, accent: str) -> str:
    """The category header's inline filter field. Quieter than the Ctrl+K
    palette input (this is a refinement of a page you're already on, not a
    global launcher), so it sits at panel tone until focused, when it takes
    the module's own accent."""
    return f"""
        QLineEdit {{
            background: {t['panel']};
            border: 1px solid {t['panel_line']};
            border-radius: {RADIUS['control']}px;
            color: {t['text']};
            font-size: 12px;
            padding: 0 10px;
            selection-background-color: {alpha(accent, 0.35)};
        }}
        QLineEdit:hover {{ border: 1px solid {alpha(accent, 0.35)}; }}
        QLineEdit:focus {{
            border: 1px solid {alpha(accent, 0.65)};
            background: {t['card']};
        }}
    """


def count_chip_qss(t: dict, accent: str, filtered: bool = False) -> str:
    """'12 operations' / 'showing 3 of 12'. Neutral while the full set is
    shown; accented once a filter is narrowing it, so the chip doubles as
    the indicator that a filter is active."""
    if filtered:
        return f"""
            color: {accent}; font-size: 10px; font-weight: 700;
            background: {alpha(accent, 0.12)};
            border: 1px solid {alpha(accent, 0.38)};
            border-radius: {RADIUS['chip']}px; padding: 3px 10px;
            letter-spacing: 0.5px;
        """
    return f"""
        color: {t['text_faint']}; font-size: 10px; font-weight: 700;
        background: {t['panel']}; border: 1px solid {t['panel_line']};
        border-radius: {RADIUS['chip']}px; padding: 3px 10px;
        letter-spacing: 0.5px;
    """


def keycap_qss(t: dict) -> str:
    """A key rendered as a physical keycap in the shortcut sheet — raised
    surface, firm hairline, monospace-ish tracking. Reads as 'press this'
    rather than as quoted text."""
    return f"""
        color: {t['text']}; font-size: 11px; font-weight: 600;
        background: {t['card']}; border: 1px solid {t['card_line']};
        border-radius: {RADIUS['chip']}px; padding: 5px 8px;
        letter-spacing: 0.5px;
    """


def empty_state_qss(t: dict) -> str:
    """The 'no operations match' message shown when a filter empties the
    grid — an explicit answer beats a blank page, which reads as a bug."""
    return (f"color: {t['text_muted']}; font-size: 13px; font-weight: 500;"
            "background: transparent; border: none;")


def recent_row_qss(t: dict) -> str:
    """One row in the sidebar's Recent Operations panel — the same ghost
    treatment as a nav entry (transparent at rest, surface + accent line on
    hover) so the block reads as part of the rail rather than a foreign
    widget bolted underneath it. Left padding clears the painted module
    glyph, right padding clears the outcome dot (see
    widgets.RecentOperationRow.paintEvent)."""
    return f"""
        QPushButton {{
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: {RADIUS['chip']}px;
            color: {t['text_muted']};
            font-size: 12px; font-weight: 500;
            text-align: left; padding-left: 34px; padding-right: 24px;
        }}
        QPushButton:hover {{
            background-color: {t['card_hover']};
            border: 1px solid {alpha(t['accent'], 0.22)};
            color: {t['text']};
        }}
        QPushButton:pressed {{ background-color: {alpha(t['accent'], 0.16)}; }}
    """


def elevate_button_qss(t: dict) -> str:
    """Sidebar-footer 'Run as Administrator' call-to-action — the relocated,
    far more discoverable home for elevation (was a cramped title-bar badge).
    Amber `warn` tone: a standing 'do this to unlock system actions' prompt,
    not a red failure. Full-width, left-aligned with room for a leading shield
    glyph, sitting in the sidebar's app-control zone right above Exit."""
    return f"""
        QPushButton {{
            background: {alpha(t['warn'], 0.13)};
            border: 1px solid {alpha(t['warn'], 0.42)};
            border-radius: {RADIUS['plaque']}px;
            color: {t['warn']};
            font-size: 12px; font-weight: 600;
            text-align: left; padding-left: 16px;
        }}
        QPushButton:hover {{
            background: {alpha(t['warn'], 0.24)};
            border: 1px solid {alpha(t['warn'], 0.65)};
            color: {t['text']};
        }}
        QPushButton:pressed {{ background: {alpha(t['warn'], 0.36)}; color: {t['text']}; }}
    """


def admin_status_qss(t: dict) -> str:
    """Sidebar-footer counterpart shown when Pulse IS already elevated — a
    quiet, non-interactive green `ok` status chip confirming Administrator
    rights, so the elevation state is always legible in the same spot whether
    or not action is needed."""
    return f"""
        QLabel {{
            background: {alpha(t['ok'], 0.10)};
            border: 1px solid {alpha(t['ok'], 0.32)};
            border-radius: {RADIUS['plaque']}px;
            color: {t['ok']};
            font-size: 12px; font-weight: 600;
            padding: 0 16px;
        }}
    """


def titlebar_button_qss(t: dict, hover: str) -> str:
    """Caption buttons (theme / minimize / maximize). The `nchover`
    dynamic property mirrors :hover for the maximize button, whose mouse
    events are owned by Windows while Snap Layouts is active (the
    WM_NCHITTEST → HTMAXBUTTON path in main.nativeEvent) — Qt never sees
    Enter/Leave there, so the hover look is driven by property flips."""
    return f"""
        QPushButton {{
            background: transparent; border: none; border-radius: {RADIUS['chip']-1}px;
            color: {t['text_muted']}; font-size: 13px;
        }}
        QPushButton:hover, QPushButton[nchover="true"] {{
            background: {hover}; color: {t['text']};
        }}
        QPushButton:pressed {{ background: {alpha(t['accent'], 0.18)}; color: {t['text']}; }}
    """


def titlebar_close_qss(t: dict) -> str:
    """The close button gets the native Win11 treatment: solid caption-red
    fill with a white glyph on hover — the one affordance every Windows
    user's muscle memory expects to look exactly this way. `nchover`
    mirrors :hover while Windows owns the button's mouse events (the
    HTCLOSEBUTTON non-client zone — see main.nativeEvent)."""
    return f"""
        QPushButton {{
            background: transparent; border: none; border-radius: {RADIUS['chip']-1}px;
            color: {t['text_muted']}; font-size: 13px;
        }}
        QPushButton:hover, QPushButton[nchover="true"] {{
            background: {t['close_hover']}; color: #ffffff;
        }}
        QPushButton:pressed {{ background: #b12417; color: #ffffff; }}
    """


def beta_badge_qss(t: dict) -> str:
    """The release-channel pill in the title bar ('BETA') — violet half of
    the brand pair so it reads as identity, not as a warning."""
    return f"""
        color: {t['accent2']}; font-size: 9px; font-weight: 700;
        background: {alpha(t['accent2'], 0.12)};
        border: 1px solid {alpha(t['accent2'], 0.35)};
        border-radius: {RADIUS['chip']}px; padding: 2px 8px; letter-spacing: 1px;
    """


def toast_qss(t: dict, accent: str) -> str:
    """One toast notification card: app-material surface (same frosted
    treatment as dialogs), a slim colored status spine on the left, and
    the theme's own text/border tokens — light mode gets a real light
    toast instead of the old hardcoded dark rectangle."""
    return f"""
        QFrame#toast {{
            background-color: {glass_fill(t, t['toast_bg'], sheen_stop=0.20)};
            border: 1px solid {t['panel_line']};
            border-left: 3px solid {accent};
            border-radius: {RADIUS['plaque']}px;
        }}
    """


def toast_text_qss(t: dict) -> str:
    return (f"color: {t['text']}; font-size: 12px; font-weight: 500;"
            "background: transparent; border: none;")


def toast_icon_qss(t: dict, accent: str) -> str:
    """22px circular status chip inside a toast (✓ / ✕ / i)."""
    return f"""
        color: {accent}; font-size: 11px; font-weight: 700;
        background: {alpha(accent, 0.14)};
        border: 1px solid {alpha(accent, 0.40)};
        border-radius: {RADIUS['control']}px;
    """


def scroll_area_qss(t: dict) -> str:
    return f"""
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
        QScrollBar::handle:vertical {{
            background: {t['scroll']}; border-radius: 3px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {t['scroll_hov']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 2px; }}
        QScrollBar::handle:horizontal {{
            background: {t['scroll']}; border-radius: 3px; min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {t['scroll_hov']}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    """


def chip_qss(t: dict, ok: bool = True) -> str:
    color = t["text_soft"] if ok else t["err"]
    border = t["card_line"] if ok else t["danger_line"]
    return f"""
        color: {color}; font-size: 12px; font-weight: 500;
        background: {t['card']}; border: 1px solid {border};
        border-radius: {RADIUS['plaque']}px; padding: 8px 18px;
    """


def badge_qss(t: dict) -> str:
    return f"""
        color: {t['warn']}; font-size: 9px; font-weight: 600;
        background: {alpha(t['warn'], 0.08)};
        border: 1px solid {alpha(t['warn'], 0.28)};
        border-radius: {RADIUS['chip']-1}px; padding: 2px 7px;
    """


def dialog_panel_qss(t: dict, accent: str) -> str:
    """Same frosted-glass material as GlassCard (glass_fill), so a dialog
    reads as depth-consistent with the surface that opened it instead of a
    flatter, unrelated modal — paired with paint_bevel_frame on the
    DepthCard panel that hosts this (see widgets.ConfirmDialog /
    AppSelectorDialog / CommandPalette)."""
    return f"""
        QFrame {{
            background-color: {glass_fill(t, t['dialog_bg'], sheen_stop=0.18)};
            border: 1px solid {alpha(accent, 0.35)};
            border-radius: {RADIUS['panel']}px;
        }}
    """


def dialog_cancel_qss(t: dict) -> str:
    """Secondary dialog action. font-weight matches dialog_go_qss (600) so
    the Cancel/Close label doesn't render optically lighter than the
    primary button it sits beside; hover also firms the border — the
    fill-only hover left the button reading half-disabled in light mode."""
    return f"""
        QPushButton {{
            background: {t['panel']}; border: 1px solid {t['card_line']};
            border-radius: {RADIUS['control']}px; color: {t['text_soft']};
            font-size: 12px; font-weight: 600;
        }}
        QPushButton:hover {{
            background: {t['card_hover']}; color: {t['text']};
            border: 1px solid {alpha(t['accent'], 0.35)};
        }}
        QPushButton:pressed {{ background: {alpha(t['accent'], 0.14)}; }}
    """


def console_qss(t: dict) -> str:
    """Live PowerShell stdout stream — monospace micro-terminal."""
    return f"""
        QPlainTextEdit {{
            background-color: {t['bg_solid']};
            color: {t['text_soft']};
            border: 1px solid {t['card_line']};
            border-radius: {RADIUS['plaque']}px;
            padding: 8px 10px;
            selection-background-color: {alpha(t['accent'], 0.35)};
        }}
        QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
        QScrollBar::handle:vertical {{
            background: {t['scroll']}; border-radius: 3px; min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {t['scroll_hov']}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 2px; }}
        QScrollBar::handle:horizontal {{
            background: {t['scroll']}; border-radius: 3px; min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {t['scroll_hov']}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    """


def console_header_qss(t: dict) -> str:
    return (f"color: {t['text_faint']}; font-size: 10px; font-weight: 700;"
            "background: transparent; border: none; letter-spacing: 2px;")


def activity_rail_qss(t: dict) -> str:
    """The always-visible header rail of the collapsing Activity drawer
    (widgets.ActivityDrawer). A slim frosted bar carrying the status dot,
    'LIVE OUTPUT' label, the execution-state pill and the expand chevron —
    when the drawer is collapsed this 40px rail is ALL the console footprint
    costs, handing ~140px of vertical canvas back to the card grid."""
    return f"""
        QFrame#activityRail {{
            background: {t['panel']};
            border: 1px solid {t['panel_line']};
            border-radius: {RADIUS['plaque']}px;
        }}
    """


def activity_toggle_qss(t: dict) -> str:
    """The chevron button that expands / pins the Activity drawer."""
    return f"""
        QPushButton {{
            background: transparent; border: none; border-radius: {RADIUS['chip']}px;
            color: {t['text_faint']}; font-size: 13px; font-weight: 700;
        }}
        QPushButton:hover {{
            background: {t['card_hover']}; color: {t['text']};
        }}
        QPushButton:pressed {{ background: {alpha(t['accent'], 0.16)}; }}
        QPushButton:checked {{ color: {t['accent']}; }}
    """


def stop_button_qss(t: dict) -> str:
    """Global kill switch — danger ghost button in the console header row."""
    return f"""
        QPushButton {{
            background: {alpha(t['err'], 0.10)};
            border: 1px solid {alpha(t['err'], 0.45)};
            border-radius: {RADIUS['chip']}px;
            color: {t['err']};
            font-size: 11px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {alpha(t['err'], 0.25)}; color: {t['text']}; }}
        QPushButton:pressed {{ background: {alpha(t['err'], 0.38)}; color: {t['text']}; }}
        QPushButton:disabled {{
            background: {t['panel']};
            border: 1px solid {t['panel_line']};
            color: {t['text_faint']};
        }}
    """


def state_pill_qss(t: dict) -> str:
    """Execution-state chip: IDLE / RUNNING / SUCCESS / ERROR / STOPPED.
    One string per theme switch — states are dynamic-property flips."""
    base = (f"font-size: 9px; font-weight: 700; letter-spacing: 2px;"
            f"border-radius: {RADIUS['control']}px; padding: 3px 12px;")
    return f"""
        QLabel#statePill {{ {base}
            color: {t['text_faint']};
            background: {t['panel']};
            border: 1px solid {t['panel_line']}; }}
        QLabel#statePill[state="running"] {{ {base}
            color: {t['accent']};
            background: {brand_gradient(t, 0.14, 0.10)};
            border: 1px solid {alpha(t['accent'], 0.45)}; }}
        QLabel#statePill[state="ok"] {{ {base}
            color: {t['ok']};
            background: {alpha(t['ok'], 0.10)};
            border: 1px solid {alpha(t['ok'], 0.45)}; }}
        QLabel#statePill[state="err"] {{ {base}
            color: {t['err']};
            background: {alpha(t['err'], 0.10)};
            border: 1px solid {alpha(t['err'], 0.45)}; }}
        QLabel#statePill[state="stopped"] {{ {base}
            color: {t['warn']};
            background: {alpha(t['warn'], 0.10)};
            border: 1px solid {alpha(t['warn'], 0.45)}; }}
    """


def checkbox_qss(t: dict, accent: str) -> str:
    """Selector checkbox. Every state transition answers the pointer:
    unchecked hover pre-tints the well with the accent (a preview of the
    checked fill, not just a border flip), and checked hover brightens the
    ring so an about-to-be-unchecked box visibly acknowledges the cursor."""
    return f"""
        QCheckBox {{
            color: {t['text_soft']}; font-size: 12px; font-weight: 500;
            background: transparent; border: none; spacing: 10px; padding: 4px 2px;
        }}
        QCheckBox::indicator {{
            width: 16px; height: 16px; border-radius: {RADIUS['chip']-2}px;
            border: 1px solid {t['card_line']}; background: {t['card']};
        }}
        QCheckBox::indicator:hover {{
            border: 1px solid {alpha(accent, 0.55)};
            background: {alpha(accent, 0.10)};
        }}
        QCheckBox::indicator:checked {{
            border: 1px solid {accent}; background: {accent};
        }}
        QCheckBox::indicator:checked:hover {{
            border: 1px solid {t['text']};
            background: {accent};
        }}
    """


def wizard_link_qss(t: dict, accent: str) -> str:
    """Full-width clickable link row — the Office wizard's 'open this URL'
    / 'browse for a folder' actions, styled like an inert app_row until
    hovered, when it lights up with the accent (a link that reads as a
    link, not a generic button)."""
    return f"""
        QPushButton {{
            background: {t['card']}; border: 1px solid {t['card_line']};
            border-radius: {RADIUS['plaque']}px; color: {t['text']}; font-size: 13px; font-weight: 600;
            text-align: left; padding: 0 16px;
        }}
        QPushButton:hover {{
            background: {t['card_hover']}; border: 1px solid {alpha(accent, 0.45)};
            color: {accent};
        }}
        QPushButton:pressed {{ background: {alpha(accent, 0.16)}; }}
    """


def warning_banner_qss(t: dict) -> str:
    """Prominent inline warning banner — amber, not danger-red: this is a
    'pay attention' caveat (don't close the Office setup window), not a
    destructive-action confirmation, so it borrows the `warn` token rather
    than `err`."""
    return f"""
        QLabel {{
            background: {alpha(t['warn'], 0.12)};
            border: 1px solid {alpha(t['warn'], 0.45)};
            border-radius: {RADIUS['plaque']}px;
            color: {t['warn']};
            font-size: 12px; font-weight: 600;
            padding: 14px 16px;
        }}
    """


def dev_hub_row_qss(t: dict) -> str:
    """Selector row (Dev Hub AND every Software Management app pack — the
    one unified row style) with a 'suggested' state: a soft amber
    highlight when this tool is a checked-off IDE's unmet runtime
    dependency (see widgets.DevHubRow / DevHubSelectorDialog's
    dependency-hint nudge — 'subtly suggests', never auto-forces a check).
    Hover lifts the fill as well as the border — border-only hover read as
    inert next to GlassCard, whose hover changes both."""
    return f"""
        QFrame {{
            background: {t['card']};
            border: 1px solid {t['card_line']};
            border-radius: {RADIUS['control']}px;
        }}
        QFrame:hover {{
            background: {t['card_hover']};
            border: 1px solid {alpha(t['accent'], 0.35)};
        }}
        QFrame[suggested="true"] {{
            border: 1px solid {alpha(t['warn'], 0.55)};
            background: {alpha(t['warn'], 0.07)};
        }}
    """


def hub_group_header_qss(t: dict, accent: str) -> str:
    """Sub-group title inside a grouped hub's landing screen (System Tools
    & Utilities): the 'section' typographic role, lifted from text_faint
    to a soft accent tint so group boundaries register on first scan —
    the label half of the header row; hub_group_rule_qss is the other."""
    return (f"color: {alpha(accent, 0.90)}; font-size: 10px; font-weight: 700;"
            f"background: transparent; border: none; letter-spacing: 4px;")


def hub_group_rule_qss(t: dict, accent: str) -> str:
    """The hairline that finishes a hub group header: a 1px rule fading
    from the accent at the label's edge to nothing at the panel's right
    side, carrying the eye across the row exactly like the section
    dividers in commercial dashboard UIs. Painted as a QFrame background
    (gradient, not border) so the fade is smooth on any panel width."""
    return (f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"stop:0 {alpha(accent, 0.38)}, stop:1 {alpha(accent, 0.0)});"
            "border: none;")


def icon_ghost_button_qss(t: dict, accent: str) -> str:
    """Small ghost icon-only button — the Dev Hub row's per-tool '⋯'
    install-options trigger."""
    return f"""
        QPushButton {{
            background: transparent; border: 1px solid {t['card_line']};
            border-radius: {RADIUS['chip']-2}px; color: {t['text_muted']}; font-size: 13px; font-weight: 700;
        }}
        QPushButton:hover {{
            background: {alpha(accent, 0.14)}; border: 1px solid {alpha(accent, 0.45)};
            color: {accent};
        }}
        QPushButton:pressed {{ background: {alpha(accent, 0.24)}; }}
    """


def link_button_qss(t: dict, accent: str) -> str:
    return f"""
        QPushButton {{
            background: transparent; border: none;
            color: {accent}; font-size: 11px; font-weight: 600;
        }}
        QPushButton:hover {{ color: {t['text']}; }}
    """


def command_input_qss(t: dict) -> str:
    """Ctrl+K command palette search field."""
    return f"""
        QLineEdit {{
            background: {t['panel']};
            border: 1px solid {t['panel_line']};
            border-radius: {RADIUS['control']}px;
            color: {t['text']};
            font-size: 15px;
            padding: 0 14px;
            selection-background-color: {alpha(t['accent'], 0.35)};
        }}
        QLineEdit:focus {{ border: 1px solid {alpha(t['accent'], 0.55)}; }}
    """


def command_list_qss(t: dict) -> str:
    """Ctrl+K command palette result list."""
    return f"""
        QListWidget {{
            background: transparent;
            border: none;
            outline: none;
            font-size: 13px;
            color: {t['text_soft']};
        }}
        QListWidget::item {{
            padding: 10px 12px;
            border-radius: {RADIUS['chip']}px;
            margin: 1px 2px;
        }}
        QListWidget::item:selected {{
            background: {alpha(t['accent'], 0.16)};
            color: {t['text']};
            border: 1px solid {alpha(t['accent'], 0.40)};
        }}
        QListWidget::item:hover:!selected {{
            background: {t['card_hover']};
        }}
    """


def dialog_secondary_go_qss(t: dict, accent: str) -> str:
    """A quieter CTA than dialog_go_qss's full brand-gradient treatment —
    flat accent-tinted ghost fill, for a dialog's secondary action sitting
    next to the primary one (e.g. 'Update Selected' beside 'Update All')."""
    return f"""
        QPushButton {{
            background: {alpha(accent, 0.08)}; border: 1px solid {alpha(accent, 0.35)};
            border-radius: {RADIUS['control']}px; color: {accent}; font-size: 12px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {alpha(accent, 0.18)}; color: {t['text']}; }}
        QPushButton:pressed {{ background: {alpha(accent, 0.28)}; color: {t['text']}; }}
        QPushButton:disabled {{
            background: {t['panel']}; border: 1px solid {t['panel_line']};
            color: {t['text_faint']};
        }}
    """


def stat_chip_qss(t: dict, tone: str = "neutral") -> str:
    """Small rounded stat pill for a dialog's summary strip ('14 updates
    found', '3 recommended'). `tone` picks the token the chip is built
    from; 'neutral' stays a plain card chip."""
    colors = {"neutral": t["text_soft"], "accent": t["accent"],
              "warn": t["warn"], "ok": t["ok"], "err": t["err"]}
    color = colors.get(tone, t["text_soft"])
    if tone == "neutral":
        bg, border = t["card"], t["card_line"]
    else:
        bg, border = alpha(color, 0.10), alpha(color, 0.35)
    return f"""
        color: {color}; font-size: 12px; font-weight: 600;
        background: {bg}; border: 1px solid {border};
        border-radius: {RADIUS['plaque']}px; padding: 7px 14px;
    """


def version_chip_qss(t: dict, accent: bool = False) -> str:
    """Version number pill in an update row — muted for 'current', lit
    with the accent for 'available' so the eye lands on what's new."""
    if accent:
        return f"""
            color: {t['accent']}; font-size: 11px; font-weight: 700;
            background: {alpha(t['accent'], 0.14)}; border: 1px solid {alpha(t['accent'], 0.40)};
            border-radius: {RADIUS['chip']-1}px; padding: 3px 9px;
        """
    return f"""
        color: {t['text_muted']}; font-size: 11px; font-weight: 600;
        background: {t['panel']}; border: 1px solid {t['panel_line']};
        border-radius: {RADIUS['chip']-1}px; padding: 3px 9px;
    """


def impact_badge_qss(t: dict, level: str) -> str:
    """High/Medium/Low boot-impact badge on a startup row."""
    color = {"High": t["err"], "Medium": t["warn"], "Low": t["ok"]}.get(level, t["text_faint"])
    return f"""
        color: {color}; font-size: 9px; font-weight: 700; letter-spacing: 1px;
        background: {alpha(color, 0.12)}; border: 1px solid {alpha(color, 0.40)};
        border-radius: {RADIUS['chip']}px; padding: 2px 8px;
    """


def recommendation_badge_qss(t: dict, recommendation: str) -> str:
    """Disable/Keep/Review recommendation tag on a startup row."""
    color = {"Disable": t["warn"], "Keep": t["ok"], "Review": t["accent2"]}.get(
        recommendation, t["text_faint"])
    return f"""
        color: {color}; font-size: 10px; font-weight: 700;
        background: {alpha(color, 0.10)}; border: 1px solid {alpha(color, 0.35)};
        border-radius: {RADIUS['chip']}px; padding: 3px 10px;
    """


def startup_row_qss(t: dict) -> str:
    """One item inside the Startup Manager's list — dims (via the
    `disabled_item` dynamic property, deliberately not Qt's own `disabled`
    name, which drives the unrelated :disabled pseudo-state) once its
    toggle is switched off, so the eye reads enabled vs. disabled at a
    glance without hunting for the switch state."""
    return f"""
        QFrame {{
            background: {t['card']}; border: 1px solid {t['card_line']};
            border-radius: {RADIUS['plaque']}px;
        }}
        QFrame:hover {{ border: 1px solid {alpha(t['accent'], 0.30)}; }}
        QFrame[disabled_item="true"] {{
            background: {t['panel']}; border: 1px solid {t['panel_line']};
        }}
    """


def inline_status_qss(t: dict, tone: str = "ok") -> str:
    """The Startup Manager's inline result strip (a dialog-local stand-in
    for the app's ToastManager, whose toasts live behind a modal dialog's
    own top-level window and would never be seen while it's open)."""
    color = {"ok": t["ok"], "err": t["err"], "info": t["accent"]}.get(tone, t["text_soft"])
    return f"""
        color: {color}; font-size: 12px; font-weight: 600;
        background: {alpha(color, 0.10)}; border: 1px solid {alpha(color, 0.32)};
        border-radius: {RADIUS['control']}px; padding: 8px 14px;
    """


def dialog_go_qss(t: dict, accent: str) -> str:
    """Primary dialog action ('Proceed' / 'Deploy'). The two-tone brand
    sweep only applies when `accent` is the theme's normal accent — a
    danger confirmation (accent == t['err']) stays a flat, unambiguous red;
    gradients on a 'this may be hard to undo' button would blur the warning."""
    is_brand = accent == t["accent"]
    fill = (lambda a1, a2: brand_gradient(t, a1, a2)) if is_brand else (lambda a1, a2: alpha(accent, a1))
    return f"""
        QPushButton {{
            background: {fill(0.16, 0.11)}; border: 1px solid {alpha(accent, 0.55)};
            border-radius: {RADIUS['control']}px; color: {accent}; font-size: 12px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {fill(0.30, 0.24)}; color: {t['text']}; }}
        QPushButton:pressed {{ background: {fill(0.42, 0.34)}; color: {t['text']}; }}
    """


# -- label roles ---------------------------------------------
# v7 typographic scale: the v6.2 ramp was flat in the middle — card(14) /
# body(13) / desc(12) sat nearly indistinguishable, so cards had no clear
# focal point. v7 WIDENS that middle: the card TITLE jumps to 16/650 to
# lead unmistakably, while `desc` lifts to 13px on the brighter `text_soft`
# so the title-vs-description gap now reads in BOTH size and tone (hierarchy
# from contrast, per the app's standing philosophy — just tuned harder).
# A new `meta` role carries the card footer's count pills / hints.
_LABEL_ROLES = {
    "title":    ("22px", "680", "text",       ""),
    # v10: a REAL dialog heading role. Every dialog used to build its
    # header as `label_qss(t, "card").replace("14px", "16px")` — but the
    # card role has been 16px since v7, so that replace matched nothing
    # and silently did nothing in all 8 call sites. Dialog titles have
    # been rendering at plain card-title size ever since; they now have
    # their own step above it.
    "dialog":   ("18px", "700", "text",       ""),
    "version":  ("11px", "500", "text_faint", ""),
    "card":     ("16px", "650", "text",       ""),
    "body":     ("13px", "400", "text_muted", ""),
    "desc":     ("13px", "400", "text_soft",  ""),
    "tagline":  ("12px", "400", "text_muted", ""),
    "status":   ("11px", "500", "text_muted", ""),
    "faint":    ("12px", "400", "text_faint", ""),
    "section":  ("10px", "700", "text_faint", "letter-spacing: 4px;"),
    "brand":    ("11px", "600", "text_muted", "letter-spacing: 2px;"),
    "caption":  ("10px", "500", "text_faint", "letter-spacing: 1px;"),
}
# Removed in v10: "hero", "value" and "meta" — all three had zero call
# sites anywhere in the app (the card meta pills use card_meta_pill_qss,
# which carries its own sizing).


def hero_banner_qss(t: dict) -> str:
    """The Welcome dashboard's identity banner (v9.2): the app's most
    important surface, so it wears the full frosted-glass card material
    (same glass_fill every premium surface shares) with a firm hairline —
    an authoritative masthead, not a floating splash mark."""
    return f"""
        QFrame#heroBanner {{
            background: {glass_fill(t, t['card'])};
            border: 1px solid {t['card_line']};
            border-radius: {RADIUS['panel']}px;
        }}
    """


def telemetry_qss(t: dict) -> str:
    """The Welcome dashboard's system-snapshot ribbon (OS · CPU · RAM) —
    one cohesive panel replacing the three floating insight tiles. A
    subordinate panel tone (a step below the hero banner) so the vertical
    hierarchy reads banner → telemetry → module launchpad."""
    return f"""
        QFrame#telemetry {{
            background: {t['panel']};
            border: 1px solid {t['panel_line']};
            border-radius: {RADIUS['card']}px;
        }}
    """


def label_qss(t: dict, role: str) -> str:
    size, weight, color_key, extra = _LABEL_ROLES[role]
    return (f"color: {t[color_key]}; font-size: {size}; font-weight: {weight};"
            f"background: transparent; border: none; {extra}")


# NOTE: apply_blur_behind() (SetWindowCompositionAttribute /
# ACCENT_ENABLE_BLURBEHIND) was removed here. DWM blur-behind is only
# visible through a per-pixel-alpha window, so it required the
# WA_TranslucentBackground / WS_EX_LAYERED composition path that caused
# the window-level rendering glitches (blurred dark box on launch,
# invisible sections, tearing while dragging and resizing). The shell now
# paints an opaque gradient over every pixel and DWM owns the corners.
# If a "glass" backdrop is wanted again, use DWMWA_SYSTEMBACKDROP_TYPE
# (Mica / Acrylic, Windows 11 22H2+) — it is composited by DWM on the GPU
# and needs no layered window.


def enable_native_sizing_frame(hwnd: int) -> bool:
    """Give a frameless window a REAL Win32 sizing frame (WS_THICKFRAME).

    Answering WM_NCHITTEST with HTLEFT/HTBOTTOMRIGHT/... is necessary but
    NOT sufficient to resize a window: DefWindowProc refuses to enter the
    sizing loop, and refuses to swap in the resize cursors, unless the
    window actually owns a sizing border. Qt's FramelessWindowHint builds
    a bare WS_POPUP without one, so every edge and corner hit-test was
    being answered correctly and then ignored by Windows — the window
    simply could not be resized by dragging.

    WS_CAPTION comes along for the ride because it is what makes DWM give
    the window its native drop shadow, snap animations and minimise/
    restore transitions. Neither style draws anything, because
    PulseApp.nativeEvent answers WM_NCCALCSIZE by keeping the client area
    edge-to-edge — the frame exists for the OS, not for the eye.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        GWL_STYLE = -16
        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_STYLE)
        user32.SetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_STYLE,
                              style | WS_THICKFRAME | WS_CAPTION)
        # SWP_FRAMECHANGED forces the WM_NCCALCSIZE that re-reads the style.
        SWP = 0x0001 | 0x0002 | 0x0004 | 0x0020   # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
        user32.SetWindowPos(ctypes.c_void_p(int(hwnd)), None, 0, 0, 0, 0, SWP)
        return True
    except (OSError, AttributeError):
        return False


def resize_border_thickness() -> tuple[int, int]:
    """The (x, y) frame Windows adds around a maximized WS_THICKFRAME
    window, in physical pixels — what WM_NCCALCSIZE must subtract so a
    maximized window's content stops at the work area instead of bleeding
    off every edge of the monitor."""
    if sys.platform != "win32":
        return (0, 0)
    try:
        gsm = ctypes.windll.user32.GetSystemMetrics
        SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
        pad = gsm(SM_CXPADDEDBORDER)
        return (gsm(SM_CXSIZEFRAME) + pad, gsm(SM_CYSIZEFRAME) + pad)
    except (OSError, AttributeError):
        return (0, 0)


def apply_native_rounding(hwnd: int, rounded: bool = True) -> bool:
    """Ask DWM to clip the window to rounded corners (Windows 11+), or to
    explicitly NOT round them (`rounded=False`).

    The False path is the maximized-state fix: a frameless translucent
    window keeps per-pixel hit-testing, so any corner pixel DWM rounds
    away (or QSS leaves unpainted) is alpha-0 and clicks fall STRAIGHT
    THROUGH to whatever window sits behind — the 'I clicked my browser
    through the corner of the maximized app' bug. Maximized native Win11
    windows are square; ours now is too, edge to edge, every pixel opaque
    and click-owning. Harmless no-op on Windows 10."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        pref = ctypes.c_int(2 if rounded else 1)   # DWMWCP_ROUND / DONOTROUND
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(hwnd)), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(pref), ctypes.sizeof(pref))
        return res == 0
    except (OSError, AttributeError):
        return False
