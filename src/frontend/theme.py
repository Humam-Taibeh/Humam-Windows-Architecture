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
    apply_blur_behind() real DWM blur behind the window (Windows, ctypes only)

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


# ============================================================
#  TOKENS — PREMIUM DARK
# ============================================================
_DARK = {
    "name":        "dark",
    "font":        "Segoe UI",

    # surfaces — v6.2 enterprise grading: neutral deep charcoal-navy
    # (VS Code / Slack register) instead of saturated navy; frost comes
    # from borders + the sheen gradient, never from lower fill opacity
    "bg":          "rgba(16, 18, 23, 0.96)",
    "bg_solid":    "#101217",
    "overlay":     "rgba(8, 10, 14, 0.55)",    # blur-backing layer behind card grids
    "panel":       "rgba(255, 255, 255, 0.045)",
    "panel_line":  "rgba(255, 255, 255, 0.08)",
    "card":        "rgba(26, 29, 37, 0.66)",
    "card_hover":  "rgba(76, 194, 255, 0.07)",
    "card_line":   "rgba(255, 255, 255, 0.11)",
    "card_sheen":  "rgba(255, 255, 255, 0.05)",   # top stop of the glass gradient
    "dialog_bg":   "rgba(19, 22, 29, 0.98)",

    # brand — calmer, Windows-11-adjacent blue; violet softened to match
    "accent":      "#4cc2ff",
    "accent2":     "#8a7dff",

    # text (contrast ≥ WCAG AA on the surfaces above; slightly desaturated
    # so long sessions read like an editor, not a neon dashboard)
    "text":        "#e8eaed",
    "text_soft":   "#c6ccd8",
    "text_muted":  "#8b93a5",
    "text_faint":  "#5c6472",

    # status — GitHub-dark grade: unmistakable but never neon
    "ok":          "#3fb950",
    "warn":        "#d29922",
    "err":         "#f85149",
    "danger_line": "rgba(248, 81, 73, 0.30)",

    # chrome
    "scroll":      "rgba(255, 255, 255, 0.14)",
    "scroll_hov":  "rgba(76, 194, 255, 0.50)",
    "shimmer_track": (255, 255, 255, 12),      # QColor args for painted widgets
    "titlebar_hover": "rgba(255, 255, 255, 0.08)",
    "close_hover":    "rgba(248, 81, 73, 0.30)",
}

# ============================================================
#  TOKENS — CLEAN LIGHT
# ============================================================
_LIGHT = {
    "name":        "light",
    "font":        "Segoe UI",

    # v6.2 enterprise grading: soft white on cool gray, Fluent-style accent
    "bg":          "rgba(244, 246, 249, 0.97)",
    "bg_solid":    "#f4f6f9",
    "overlay":     "rgba(255, 255, 255, 0.45)",
    "panel":       "rgba(255, 255, 255, 0.60)",
    "panel_line":  "rgba(27, 32, 40, 0.10)",
    "card":        "rgba(255, 255, 255, 0.85)",
    "card_hover":  "rgba(0, 103, 192, 0.07)",
    "card_line":   "rgba(27, 32, 40, 0.14)",
    "card_sheen":  "rgba(255, 255, 255, 0.95)",   # top stop of the glass gradient
    "dialog_bg":   "rgba(251, 252, 254, 0.99)",

    "accent":      "#0067c0",
    "accent2":     "#6d5ed6",

    "text":        "#1b2028",
    "text_soft":   "#2c3340",
    "text_muted":  "#5a6474",
    "text_faint":  "#8b94a3",

    # status — GitHub-light grade
    "ok":          "#1a7f37",
    "warn":        "#9a6700",
    "err":         "#cf222e",
    "danger_line": "rgba(207, 34, 46, 0.35)",

    "scroll":      "rgba(27, 32, 40, 0.18)",
    "scroll_hov":  "rgba(0, 103, 192, 0.55)",
    "shimmer_track": (27, 32, 40, 16),
    "titlebar_hover": "rgba(27, 32, 40, 0.07)",
    "close_hover":    "rgba(207, 34, 46, 0.22)",
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

    @property
    def is_dark(self) -> bool:
        return self._mode == "dark"

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
    return f"""
        #shell {{
            background-color: {t['bg']};
            border: 1px solid {t['panel_line']};
            border-radius: 24px;
        }}
        #shell[flush="true"] {{
            border-radius: 0px;
            border: none;
        }}
    """


def sidebar_qss(t: dict) -> str:
    return f"""
        QFrame {{
            background: {t['panel']};
            border-radius: 20px;
            border: 1px solid {t['panel_line']};
        }}
    """


def content_qss(t: dict) -> str:
    return f"""
        QFrame {{
            background: {t['overlay']};
            border-radius: 20px;
            border: 1px solid {t['panel_line']};
        }}
    """


def nav_button_qss(t: dict) -> str:
    return f"""
        QPushButton {{
            background-color: {t['card']};
            border: 1px solid {t['panel_line']};
            border-radius: 13px;
            color: {t['text_soft']};
            font-size: 13px; font-weight: 500;
            text-align: left; padding-left: 16px;
        }}
        QPushButton:hover {{
            background-color: {t['card_hover']};
            border: 1px solid {alpha(t['accent'], 0.30)};
            color: {t['text']};
        }}
        QPushButton:pressed {{ background-color: {alpha(t['accent'], 0.18)}; }}
        QPushButton[selected="true"] {{
            background-color: {alpha(t['accent'], 0.13)};
            border: 1px solid {alpha(t['accent'], 0.55)};
            color: {t['text']};
        }}
    """


def card_qss(t: dict, accent: str, danger: bool = False) -> str:
    line = t["danger_line"] if danger else t["card_line"]
    hover_line = alpha(t["err"], 0.55) if danger else alpha(accent, 0.55)
    # Frosted-glass base: a subtle top sheen via qlineargradient (QSS-native,
    # cached, radius-safe — per-side highlight borders artifact on rounded
    # corners). State rules AFTER base/hover: QSS is last-match-wins at
    # equal specificity, and a verdict flash must outrank a stale hover.
    return f"""
        GlassCard {{
            background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {t['card_sheen']}, stop:0.12 {t['card']}, stop:1 {t['card']});
            border: 1px solid {line};
            border-radius: 16px;
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


def nav_pill_qss(t: dict) -> str:
    """Back / Home / theme-toggle pill buttons."""
    return f"""
        QPushButton {{
            background: {t['card']};
            border: 1px solid {t['card_line']};
            border-radius: 10px;
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


def exit_button_qss(t: dict) -> str:
    return f"""
        QPushButton {{
            background-color: {alpha(t['err'], 0.07)};
            border: 1px solid {alpha(t['err'], 0.18)};
            border-radius: 12px;
            color: {t['err']};
            font-size: 13px; font-weight: 500;
        }}
        QPushButton:hover {{ background-color: {alpha(t['err'], 0.22)}; color: {t['text']}; }}
        QPushButton:pressed {{ background-color: {alpha(t['err'], 0.32)}; }}
    """


def titlebar_button_qss(t: dict, hover: str) -> str:
    return f"""
        QPushButton {{
            background: transparent; border: none; border-radius: 8px;
            color: {t['text_muted']}; font-size: 13px;
        }}
        QPushButton:hover {{ background: {hover}; color: {t['text']}; }}
        QPushButton:pressed {{ background: {alpha(t['accent'], 0.18)}; color: {t['text']}; }}
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
    """


def chip_qss(t: dict, ok: bool = True) -> str:
    color = t["text_soft"] if ok else t["err"]
    border = t["card_line"] if ok else t["danger_line"]
    return f"""
        color: {color}; font-size: 12px; font-weight: 500;
        background: {t['card']}; border: 1px solid {border};
        border-radius: 14px; padding: 8px 18px;
    """


def badge_qss(t: dict) -> str:
    return f"""
        color: {t['warn']}; font-size: 9px; font-weight: 600;
        background: {alpha(t['warn'], 0.08)};
        border: 1px solid {alpha(t['warn'], 0.28)};
        border-radius: 7px; padding: 2px 7px;
    """


def dialog_panel_qss(t: dict, accent: str) -> str:
    return f"""
        QFrame {{
            background-color: {t['dialog_bg']};
            border: 1px solid {alpha(accent, 0.35)};
            border-radius: 18px;
        }}
    """


def dialog_cancel_qss(t: dict) -> str:
    return f"""
        QPushButton {{
            background: {t['panel']}; border: 1px solid {t['card_line']};
            border-radius: 10px; color: {t['text_soft']}; font-size: 12px;
        }}
        QPushButton:hover {{ background: {t['card_hover']}; color: {t['text']}; }}
        QPushButton:pressed {{ background: {alpha(t['accent'], 0.14)}; }}
    """


def console_qss(t: dict) -> str:
    """Live PowerShell stdout stream — monospace micro-terminal."""
    return f"""
        QPlainTextEdit {{
            background-color: {t['bg_solid']};
            color: {t['text_soft']};
            border: 1px solid {t['card_line']};
            border-radius: 12px;
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
    """


def console_header_qss(t: dict) -> str:
    return (f"color: {t['text_faint']}; font-size: 10px; font-weight: 700;"
            "background: transparent; border: none; letter-spacing: 2px;")


def stop_button_qss(t: dict) -> str:
    """Global kill switch — danger ghost button in the console header row."""
    return f"""
        QPushButton {{
            background: {alpha(t['err'], 0.10)};
            border: 1px solid {alpha(t['err'], 0.45)};
            border-radius: 8px;
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
    base = ("font-size: 9px; font-weight: 700; letter-spacing: 2px;"
            "border-radius: 10px; padding: 3px 12px;")
    return f"""
        QLabel#statePill {{ {base}
            color: {t['text_faint']};
            background: {t['panel']};
            border: 1px solid {t['panel_line']}; }}
        QLabel#statePill[state="running"] {{ {base}
            color: {t['accent']};
            background: {alpha(t['accent'], 0.10)};
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
    return f"""
        QCheckBox {{
            color: {t['text_soft']}; font-size: 12px; font-weight: 500;
            background: transparent; border: none; spacing: 10px; padding: 4px 2px;
        }}
        QCheckBox::indicator {{
            width: 16px; height: 16px; border-radius: 5px;
            border: 1px solid {t['card_line']}; background: {t['card']};
        }}
        QCheckBox::indicator:hover {{ border: 1px solid {alpha(accent, 0.55)}; }}
        QCheckBox::indicator:checked {{
            border: 1px solid {accent}; background: {accent};
        }}
    """


def app_row_qss(t: dict) -> str:
    return f"""
        QFrame {{
            background: {t['card']};
            border: 1px solid {t['card_line']};
            border-radius: 10px;
        }}
        QFrame:hover {{ border: 1px solid {alpha(t['accent'], 0.35)}; }}
    """


def link_button_qss(t: dict, accent: str) -> str:
    return f"""
        QPushButton {{
            background: transparent; border: none;
            color: {accent}; font-size: 11px; font-weight: 600;
        }}
        QPushButton:hover {{ color: {t['text']}; }}
    """


def dialog_go_qss(t: dict, accent: str) -> str:
    return f"""
        QPushButton {{
            background: {alpha(accent, 0.14)}; border: 1px solid {alpha(accent, 0.55)};
            border-radius: 10px; color: {accent}; font-size: 12px; font-weight: 600;
        }}
        QPushButton:hover {{ background: {alpha(accent, 0.28)}; color: {t['text']}; }}
        QPushButton:pressed {{ background: {alpha(accent, 0.40)}; color: {t['text']}; }}
    """


# -- label roles ---------------------------------------------
_LABEL_ROLES = {
    "hero":     ("30px", "700", "text",       "letter-spacing: 8px;"),
    "title":    ("19px", "650", "text",       ""),
    "card":     ("14px", "600", "text",       ""),
    "body":     ("12px", "400", "text_muted", ""),
    "desc":     ("11px", "400", "text_muted", ""),
    "tagline":  ("11px", "400", "text_muted", ""),
    "status":   ("11px", "500", "text_muted", ""),
    "faint":    ("12px", "400", "text_faint", ""),
    "section":  ("10px", "700", "text_faint", "letter-spacing: 4px;"),
    "brand":    ("11px", "600", "text_muted", "letter-spacing: 2px;"),
    "value":    ("15px", "650", "text",       ""),
    "caption":  ("10px", "500", "text_faint", "letter-spacing: 1px;"),
}


def insight_card_qss(t: dict) -> str:
    """Mini system-metadata preview card on the Welcome page — same
    sheen-gradient glass treatment as GlassCard, for one consistent
    material across the app."""
    return f"""
        QFrame#insight {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {t['card_sheen']}, stop:0.15 {t['card']}, stop:1 {t['card']});
            border: 1px solid {t['card_line']};
            border-radius: 14px;
        }}
        QFrame#insight:hover {{
            border: 1px solid {alpha(t['accent'], 0.35)};
        }}
    """


def dock_qss(t: dict) -> str:
    """Unified glass dock enclosing the Welcome status chips."""
    return f"""
        QFrame#dock {{
            background: {t['panel']};
            border: 1px solid {t['panel_line']};
            border-radius: 22px;
        }}
    """


def label_qss(t: dict, role: str) -> str:
    size, weight, color_key, extra = _LABEL_ROLES[role]
    return (f"color: {t[color_key]}; font-size: {size}; font-weight: {weight};"
            f"background: transparent; border: none; {extra}")


# ============================================================
#  REAL GLASS — DWM blur behind the window (Windows 10/11)
# ============================================================
def apply_blur_behind(hwnd: int, use_acrylic: bool = False) -> bool:
    """Enable native DWM blur behind a top-level window via
    SetWindowCompositionAttribute. Pure ctypes — no dependencies.

    use_acrylic=False (default) uses classic blur-behind, which stays
    smooth while dragging; acrylic looks richer but Windows throttles it
    during window moves (known DWM lag), so it is opt-in.
    Returns False (harmlessly) on any unsupported system.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_uint),
                        ("AccentFlags", ctypes.c_uint),
                        ("GradientColor", ctypes.c_uint),
                        ("AnimationId", ctypes.c_uint)]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int),
                        ("Data", ctypes.POINTER(ACCENT_POLICY)),
                        ("SizeOfData", ctypes.c_size_t)]

        accent = ACCENT_POLICY()
        if use_acrylic:
            accent.AccentState = 4                 # ACCENT_ENABLE_ACRYLICBLURBEHIND
            accent.GradientColor = 0x99000000      # AABBGGRR tint
        else:
            accent.AccentState = 3                 # ACCENT_ENABLE_BLURBEHIND
        data = WINCOMPATTRDATA(19, ctypes.pointer(accent), ctypes.sizeof(accent))
        set_attr = ctypes.windll.user32.SetWindowCompositionAttribute
        return bool(set_attr(ctypes.c_void_p(int(hwnd)), ctypes.byref(data)))
    except (OSError, AttributeError):
        return False


def apply_native_rounding(hwnd: int) -> bool:
    """Ask DWM to clip the window to rounded corners (Windows 11+).
    Keeps the blur-behind region from showing square corners around the
    frameless glass shell. Harmless no-op on Windows 10."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = ctypes.c_int(2)
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            ctypes.c_void_p(int(hwnd)), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(DWMWCP_ROUND), ctypes.sizeof(DWMWCP_ROUND))
        return res == 0
    except (OSError, AttributeError):
        return False
