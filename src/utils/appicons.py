"""
src/utils/appicons.py

APP ICON RESOLUTION for Software Management rows (v1.0).

Every catalog row (AppSelectorDialog, Dev Hub) shows a 28px icon beside
the app's name. Two sources, tried in order:

  1. REAL SHELL ICON — for apps already installed on this machine. The
     registry's Uninstall hives (HKLM 64/32-bit + HKCU) carry a
     DisplayIcon path per installed app; the pixmap comes from Windows'
     own icon extraction via QFileIconProvider. This is the icon the
     Start Menu shows, read the way Windows reads it.

  2. MONOGRAM PLAQUE — for everything not installed (which, on the
     install surface, is most rows). A painted rounded plaque carrying
     the app's initial in a deterministic per-app hue: stable across
     sessions (hash of the name, not random), themed per mode, and in
     the same tinted-well language as every icon plaque in the app.

DELIBERATELY NO NETWORK FETCHING. Official brand graphics would need a
CDN fetch per row — an install surface that phones home to draw itself
is wrong for a privacy-focused tool, wrong offline, and a licensing
question besides. The registry icon IS the official graphic whenever the
app is present; the monogram is honest about absence.

Cost: the registry index is built lazily ONCE per process (~a few ms for
the typical few hundred uninstall keys) and every resolved pixmap is
cached, so a selector dialog pays a one-time scan and then only blits.
"""
from __future__ import annotations

import os
import re
import sys
import zlib

from PySide6.QtCore import QFileInfo, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

# name-normalisation strips everything but letters/digits so catalog names
# ("VLC Media Player") can meet registry names ("VLC media player 3.0.20")
_NORM_RE = re.compile(r"[^a-z0-9]+")

# lazy singletons — see module docstring
_ICON_INDEX: dict[str, str] | None = None
_PIXMAP_CACHE: dict[tuple, QPixmap] = {}
_PROVIDER = None

_UNINSTALL_ROOTS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def _norm(name: str) -> str:
    return _NORM_RE.sub("", name.lower())


def _build_icon_index() -> dict[str, str]:
    """normalised DisplayName -> DisplayIcon path, from every Uninstall
    hive a standard user can read. Every failure is skipped: a single
    unreadable key must never cost the whole index."""
    index: dict[str, str] = {}
    if sys.platform != "win32":
        return index
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for root in _UNINSTALL_ROOTS:
            try:
                key = winreg.OpenKey(hive, root)
            except OSError:
                continue
            try:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                    except OSError:
                        continue
                    try:
                        name = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                        icon = str(winreg.QueryValueEx(sub, "DisplayIcon")[0])
                    except OSError:
                        continue
                    finally:
                        sub.Close()
                    path = _clean_icon_path(icon)
                    if name and path:
                        index.setdefault(_norm(name), path)
            finally:
                key.Close()
    return index


def _clean_icon_path(display_icon: str) -> str | None:
    """`"C:\\...\\app.exe",0` -> a bare, env-expanded file path. The
    trailing `,index` selects an icon WITHIN the file; QFileIconProvider
    always takes the file's primary icon, which for real apps is the
    brand mark — the distinction only matters for icon libraries."""
    path = display_icon.strip().strip('"')
    if "," in path:
        head, _, tail = path.rpartition(",")
        if tail.lstrip("-").isdigit():
            path = head.strip().strip('"')
    path = os.path.expandvars(path)
    return path if path.lower().endswith((".exe", ".ico", ".dll")) else None


def _installed_icon_path(app_name: str) -> str | None:
    """Exact normalised match first, then containment either way (min 5
    chars, so 'Git' can't claim 'GitHub Desktop'-adjacent noise)."""
    global _ICON_INDEX
    if _ICON_INDEX is None:
        try:
            _ICON_INDEX = _build_icon_index()
        except Exception:
            _ICON_INDEX = {}
    needle = _norm(app_name)
    if not needle:
        return None
    hit = _ICON_INDEX.get(needle)
    if hit:
        return hit
    if len(needle) >= 5:
        for key, path in _ICON_INDEX.items():
            if needle in key or (len(key) >= 5 and key in needle):
                return path
    return None


def _shell_pixmap(path: str, px: int) -> QPixmap | None:
    """The file's own shell icon, DPR-doubled so it stays crisp on scaled
    displays (the pixmap carries devicePixelRatio 2 and renders at the
    28px logical size the row asks for)."""
    if not os.path.isfile(path):
        return None
    try:
        if path.lower().endswith(".ico"):
            icon = QIcon(path)
        else:
            global _PROVIDER
            if _PROVIDER is None:
                from PySide6.QtWidgets import QFileIconProvider
                _PROVIDER = QFileIconProvider()
            icon = _PROVIDER.icon(QFileInfo(path))
        if icon.isNull():
            return None
        pm = icon.pixmap(QSize(px * 2, px * 2))
        if pm.isNull():
            return None
        pm.setDevicePixelRatio(2.0)
        return pm
    except Exception:
        return None


def _monogram_pixmap(app_name: str, px: int, dark: bool) -> QPixmap:
    """A painted initial-plaque in a deterministic per-app hue.

    crc32 of the name -> hue, so 'Spotify' is the same green-ish plaque
    every launch on every machine; saturation/lightness are fixed per
    theme so the whole set carries equal visual weight (the same
    peer-ratio rule the module accents follow). The glyph and hairline
    take the solved tone; the well takes a low-alpha tint of it — the
    icon-plaque anatomy everywhere else in the app.
    """
    initial = next((c for c in app_name if c.isalnum()), "?").upper()
    hue = (zlib.crc32(app_name.encode("utf-8")) % 360) / 360.0
    tone = (QColor.fromHslF(hue, 0.52, 0.68) if dark
            else QColor.fromHslF(hue, 0.58, 0.36))

    pm = QPixmap(px * 2, px * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(0.5, 0.5, px - 1.0, px - 1.0)
    fill = QColor(tone)
    fill.setAlphaF(0.14)
    line = QColor(tone)
    line.setAlphaF(0.32)
    p.setPen(line)
    p.setBrush(fill)
    radius = px * 0.29
    p.drawRoundedRect(rect, radius, radius)
    f = QFont("Segoe UI")
    f.setPixelSize(int(px * 0.46))
    f.setWeight(QFont.Weight.Bold)
    p.setFont(f)
    p.setPen(tone)
    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, initial)
    p.end()
    return pm


def app_icon(app_name: str, px: int, t: dict) -> QPixmap:
    """The 28px row icon: real shell icon if the app is installed here,
    monogram plaque otherwise. Cached per (name, size, source/theme)."""
    dark = t.get("name", "dark") == "dark"
    path = _installed_icon_path(app_name)
    key = (app_name, px, "shell" if path else ("mono-d" if dark else "mono-l"))
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    pm = _shell_pixmap(path, px) if path else None
    if pm is None:
        pm = _monogram_pixmap(app_name, px, dark)
    _PIXMAP_CACHE[key] = pm
    return pm
