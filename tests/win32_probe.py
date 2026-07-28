"""
tests/win32_probe.py

Thin ctypes helpers for asking Windows what it actually thinks of our
window. The whole point of this suite is that Pulse's window bugs are
INVISIBLE from Python: correct WM_NCHITTEST answers with no sizing border
resize nothing, and a layered window renders glitches without ever raising.
So the assertions talk to Win32 directly rather than to Qt.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

user32 = ctypes.windll.user32

GWL_STYLE, GWL_EXSTYLE = -16, -20

WS_THICKFRAME = 0x00040000
WS_CAPTION = 0x00C00000
WS_EX_LAYERED = 0x00080000

WM_NCHITTEST = 0x0084
WM_GETMINMAXINFO = 0x0024
SC_SIZE = 0xF000
MF_BYCOMMAND = 0x0000

# WM_NCHITTEST verdicts, by name
HT = {
    "NOWHERE": 0, "CLIENT": 1, "CAPTION": 2,
    "MINBUTTON": 8, "MAXBUTTON": 9,
    "LEFT": 10, "RIGHT": 11, "TOP": 12, "TOPLEFT": 13, "TOPRIGHT": 14,
    "BOTTOM": 15, "BOTTOMLEFT": 16, "BOTTOMRIGHT": 17, "CLOSE": 20,
}
HT_NAME = {v: k for k, v in HT.items()}


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wt.POINT), ("ptMaxSize", wt.POINT),
                ("ptMaxPosition", wt.POINT), ("ptMinTrackSize", wt.POINT),
                ("ptMaxTrackSize", wt.POINT)]


def hwnd_of(widget) -> int:
    return int(widget.winId())


def style(hwnd: int) -> int:
    return user32.GetWindowLongW(wt.HWND(hwnd), GWL_STYLE) & 0xFFFFFFFF


def exstyle(hwnd: int) -> int:
    return user32.GetWindowLongW(wt.HWND(hwnd), GWL_EXSTYLE) & 0xFFFFFFFF


def is_layered(hwnd: int) -> bool:
    return bool(exstyle(hwnd) & WS_EX_LAYERED)


def is_zoomed(hwnd: int) -> bool:
    return bool(user32.IsZoomed(wt.HWND(hwnd)))


def window_rect(hwnd: int) -> wt.RECT:
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    return r


def client_size(hwnd: int) -> tuple[int, int]:
    r = wt.RECT()
    user32.GetClientRect(wt.HWND(hwnd), ctypes.byref(r))
    return r.right, r.bottom


def hit_test(hwnd: int, x: int, y: int) -> int:
    """Send a real WM_NCHITTEST at screen point (x, y) and return the code
    the window's own nativeEvent handler produced."""
    lparam = (y & 0xFFFF) << 16 | (x & 0xFFFF)
    result = user32.SendMessageW(wt.HWND(hwnd), WM_NCHITTEST, 0, lparam)
    return ctypes.c_short(result & 0xFFFF).value if result > 0x7FFF else result


def hit_name(hwnd: int, x: int, y: int) -> str:
    code = hit_test(hwnd, x, y)
    return HT_NAME.get(code, f"UNKNOWN({code})")


def is_sizable(hwnd: int) -> bool:
    """Does Windows consider the window resizable? SC_SIZE is greyed out
    in the system menu for a window with no sizing border — the exact
    state that made every correct hit-test answer a no-op."""
    menu = user32.GetSystemMenu(wt.HWND(hwnd), False)
    state = user32.GetMenuState(menu, SC_SIZE, MF_BYCOMMAND)
    return state != -1 and not (state & 0x0003)   # not MF_GRAYED|MF_DISABLED


def min_track_size(hwnd: int) -> tuple[int, int]:
    """The floor the OS resize loop will clamp a drag to, in physical px."""
    mmi = MINMAXINFO()
    user32.SendMessageW(wt.HWND(hwnd), WM_GETMINMAXINFO, 0, ctypes.byref(mmi))
    return mmi.ptMinTrackSize.x, mmi.ptMinTrackSize.y


def edge_points(rect: wt.RECT, inset: int = 2) -> dict[str, tuple[int, int]]:
    """The 8 resize zones plus a caption and a client sample, in screen
    coordinates, for the window described by `rect`."""
    cx = (rect.left + rect.right) // 2
    cy = (rect.top + rect.bottom) // 2
    left, top = rect.left + inset, rect.top + inset
    right, bottom = rect.right - inset - 1, rect.bottom - inset - 1
    return {
        "LEFT": (left, cy), "RIGHT": (right, cy),
        "TOP": (cx, top), "BOTTOM": (cx, bottom),
        "TOPLEFT": (left, top), "TOPRIGHT": (right, top),
        "BOTTOMLEFT": (left, bottom), "BOTTOMRIGHT": (right, bottom),
        "CAPTION": (cx, rect.top + 25),
        "CLIENT": (cx, cy),
    }
