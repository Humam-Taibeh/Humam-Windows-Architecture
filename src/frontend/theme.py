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

    # surfaces (opacities raised vs v4 — readability override)
    "bg":          "rgba(12, 15, 25, 0.96)",
    "bg_solid":    "#0c0f19",
    "overlay":     "rgba(5, 8, 16, 0.55)",     # blur-backing layer behind card grids
    "panel":       "rgba(255, 255, 255, 0.035)",
    "panel_line":  "rgba(255, 255, 255, 0.06)",
    "card":        "rgba(22, 27, 42, 0.72)",
    "card_hover":  "rgba(0, 212, 255, 0.08)",
    "card_line":   "rgba(255, 255, 255, 0.09)",
    "dialog_bg":   "rgba(16, 20, 32, 0.98)",

    # brand
    "accent":      "#00d4ff",
    "accent2":     "#7b61ff",

    # text (contrast ≥ WCAG AA on the surfaces above)
    "text":        "#f2f7ff",
    "text_soft":   "#ccd6f6",
    "text_muted":  "#93a1c0",
    "text_faint":  "#5b6884",

    # status
    "ok":          "#00ff88",
    "warn":        "#ffd700",
    "err":         "#ff6b6b",
    "danger_line": "rgba(255, 107, 107, 0.30)",

    # chrome
    "scroll":      "rgba(255, 255, 255, 0.16)",
    "scroll_hov":  "rgba(0, 212, 255, 0.50)",
    "shimmer_track": (255, 255, 255, 14),      # QColor args for painted widgets
    "titlebar_hover": "rgba(255, 255, 255, 0.10)",
    "close_hover":    "rgba(255, 70, 70, 0.30)",
}

# ============================================================
#  TOKENS — CLEAN LIGHT
# ============================================================
_LIGHT = {
    "name":        "light",
    "font":        "Segoe UI",

    "bg":          "rgba(242, 246, 252, 0.97)",
    "bg_solid":    "#f2f6fc",
    "overlay":     "rgba(255, 255, 255, 0.45)",
    "panel":       "rgba(255, 255, 255, 0.55)",
    "panel_line":  "rgba(15, 23, 42, 0.09)",
    "card":        "rgba(255, 255, 255, 0.78)",
    "card_hover":  "rgba(0, 140, 190, 0.09)",
    "card_line":   "rgba(15, 23, 42, 0.12)",
    "dialog_bg":   "rgba(250, 252, 255, 0.99)",

    "accent":      "#0090c8",
    "accent2":     "#6a5cff",

    "text":        "#0f172a",
    "text_soft":   "#243247",
    "text_muted":  "#54627e",
    "text_faint":  "#8b98ae",

    "ok":          "#0f9d58",
    "warn":        "#a87b00",
    "err":         "#d84a4a",
    "danger_line": "rgba(216, 74, 74, 0.35)",

    "scroll":      "rgba(15, 23, 42, 0.18)",
    "scroll_hov":  "rgba(0, 144, 200, 0.55)",
    "shimmer_track": (15, 23, 42, 18),
    "titlebar_hover": "rgba(15, 23, 42, 0.08)",
    "close_hover":    "rgba(216, 74, 74, 0.25)",
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
    return f"""
        #shell {{
            background-color: {t['bg']};
            border: 1px solid {t['panel_line']};
            border-radius: 24px;
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
    return f"""
        GlassCard {{
            background-color: {t['card']};
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
    """


def titlebar_button_qss(t: dict, hover: str) -> str:
    return f"""
        QPushButton {{
            background: transparent; border: none; border-radius: 8px;
            color: {t['text_muted']}; font-size: 13px;
        }}
        QPushButton:hover {{ background: {hover}; color: {t['text']}; }}
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
    """Mini system-metadata preview card on the Welcome page."""
    return f"""
        QFrame#insight {{
            background: {t['card']};
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
