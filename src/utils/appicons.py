"""
src/utils/appicons.py

APP ICON RESOLUTION for Software Management rows.

Every catalog row (AppSelectorDialog, Dev Hub, Update Center) shows a
28px icon beside the app's name. Three sources, tried in order:

  1. A BUNDLED BRAND MARK — assets/appicons/<AppId>.svg, fetched at BUILD
     time by tools/fetch_app_icons.py from Simple Icons and rendered here
     as vector, so it stays crisp at any size or DPI. Painted in the
     brand's official colour, subject to the contrast guard below.

     THIS OUTRANKS THE INSTALLED APP'S OWN ICON, which is not the obvious
     ordering and was arrived at by looking at the result. Windows' icon
     extraction is best-effort: it resolves a DisplayIcon path, and when
     that path is stale, points into a container it cannot read, or names
     a file type with no embedded icon, it hands back a GENERIC document
     glyph rather than failing. Measured on a real machine, Steam and
     iTunes — both installed — came back as blank white pages sitting in a
     row of real logos. A curated mark is guaranteed to be the right
     artwork for the right product, and a list where every row is drawn
     from one source reads as designed rather than as scavenged.

  2. THE INSTALLED APP'S OWN ICON — for software with no bundled mark
     that is already on this machine. Full colour, drawn by the vendor,
     read out of the app's own binary. This is what covers the entries
     Simple Icons cannot supply (see tools/fetch_app_icons.py's map): Edge
     ships with Windows and VS Code is usually installed, so both
     typically land here with their authentic icon.

  3. A NEUTRAL GLYPH — a soft rounded "package" mark in the theme's muted
     tone. This replaced the LETTER MONOGRAM plaques, which put a bare
     "E", "R" or "B" where the Epic, Rockstar and BlueStacks logos
     belonged and read as an unfinished placeholder. A neutral mark that
     is identical for every unknown app says "no logo available"
     honestly; an invented letter tile pretends to be branding.

PULSE NEVER FETCHES ANYTHING. Step 2 reads files committed to the repo;
the network lives entirely in the build-time tool. An elevated
privacy-focused utility must not phone out to draw its own interface, and
this also makes the icons work on an air-gapped machine.

THE CONTRAST GUARD (the reason brand hex alone is not enough): a brand
colour is chosen against the vendor's own backdrop, usually white. Steam,
Notion, Ollama, IntelliJ, PyCharm and 7-Zip are all #000000; Epic Games
is #313131. Painted as-is those are invisible on the obsidian canvas —
the exact "unpolished" failure this module exists to fix, just in a new
form. Each mark is therefore measured against the surface it will sit on
and, when it cannot clear the readability floor, lightened (dark theme)
or darkened (light theme) along its own hue until it does. A monochrome
mark has no hue to preserve, so it simply becomes near-white on obsidian
— which is what those brands' own dark-mode guidelines specify anyway.
"""
from __future__ import annotations

import json
import os
import re
import sys

from PySide6.QtCore import QFileInfo, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from utils import resources

# name-normalisation strips everything but letters/digits so catalog names
# ("VLC Media Player") can meet registry names ("VLC media player 3.0.20")
_NORM_RE = re.compile(r"[^a-z0-9]+")

# lazy singletons — see the per-function notes
_ICON_INDEX: dict[str, str] | None = None
_MANIFEST: dict[str, dict] | None = None
_PIXMAP_CACHE: dict[tuple, QPixmap] = {}
_PROVIDER = None
_UNSET = object()          # "not resolved yet", distinct from "resolved to None"
_GENERIC_KEY: object | bytes | None = _UNSET

_UNINSTALL_ROOTS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)

#: Minimum contrast ratio a brand mark must reach against the surface
#: behind it. Below the 3:1 the theme's own icon floor uses, because a
#: logo is a large solid shape rather than a thin glyph — but far enough
#: above 1:1 that a black mark on obsidian can never ship.
_MIN_CONTRAST = 2.6


def _norm(name: str) -> str:
    return _NORM_RE.sub("", name.lower())


# ============================================================
#  1. THE INSTALLED APP'S OWN ICON
# ============================================================
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


def _generic_shell_key(px: int) -> bytes | None:
    """The raw bytes of Windows' GENERIC document icon at `px`.

    Windows' icon extraction never fails loudly: hand it a stale path, a
    container it cannot open, or a file type with nothing embedded, and it
    returns the blank-page placeholder as though that were the app's icon.
    Rendered into a row of real logos that reads as a broken image, so the
    placeholder is identified and rejected rather than shown. Comparing
    against the provider's own File icon is exact and needs no heuristics.
    """
    global _GENERIC_KEY
    if _GENERIC_KEY is _UNSET:
        _GENERIC_KEY = None
        try:
            from PySide6.QtWidgets import QFileIconProvider
            provider = _icon_provider()
            pm = provider.icon(QFileIconProvider.IconType.File).pixmap(
                QSize(px * 2, px * 2))
            if not pm.isNull():
                image = pm.toImage()
                _GENERIC_KEY = bytes(image.constBits())
        except Exception:
            _GENERIC_KEY = None
    return _GENERIC_KEY


def _icon_provider():
    global _PROVIDER
    if _PROVIDER is None:
        from PySide6.QtWidgets import QFileIconProvider
        _PROVIDER = QFileIconProvider()
    return _PROVIDER


def _shell_pixmap(path: str, px: int) -> QPixmap | None:
    """The file's own shell icon, DPR-doubled so it stays crisp on scaled
    displays. Returns None when the extractor handed back its generic
    placeholder — see _generic_shell_key."""
    if not os.path.isfile(path):
        return None
    try:
        if path.lower().endswith(".ico"):
            from PySide6.QtGui import QIcon
            icon = QIcon(path)
        else:
            icon = _icon_provider().icon(QFileInfo(path))
        if icon.isNull():
            return None
        pm = icon.pixmap(QSize(px * 2, px * 2))
        if pm.isNull():
            return None
        generic = _generic_shell_key(px)
        if generic is not None:
            try:
                if bytes(pm.toImage().constBits()) == generic:
                    return None       # the blank-page placeholder
            except Exception:
                pass
        pm.setDevicePixelRatio(2.0)
        return pm
    except Exception:
        return None


# ============================================================
#  2. THE BUNDLED BRAND MARK
# ============================================================
def _manifest() -> dict[str, dict]:
    """assets/appicons/manifest.json, written by tools/fetch_app_icons.py.
    A missing or unreadable manifest degrades to "no bundled marks" rather
    than raising — icons are decoration, and decoration must never be able
    to stop the installer UI from opening."""
    global _MANIFEST
    if _MANIFEST is None:
        _MANIFEST = {}
        path = resources.find_resource("assets/appicons/manifest.json")
        if path:
            try:
                with open(path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    _MANIFEST = loaded
            except (OSError, ValueError):
                _MANIFEST = {}
    return _MANIFEST


def _luminance(color: QColor) -> float:
    """WCAG relative luminance."""
    channels = []
    for raw in (color.redF(), color.greenF(), color.blueF()):
        channels.append(raw / 12.92 if raw <= 0.03928
                        else ((raw + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _readable_brand_color(brand: QColor, surface: QColor, dark: bool) -> QColor:
    """`brand`, walked along its own hue until it clears _MIN_CONTRAST
    against `surface` — see the module docstring's contrast-guard note.

    Lightness is stepped rather than solved analytically because HSL
    lightness and WCAG luminance are not the same curve; twenty small
    steps land within a hundredth of the floor and cost microseconds once
    per (app, theme) thanks to the pixmap cache.
    """
    if _contrast(brand, surface) >= _MIN_CONTRAST:
        return brand
    h, s, lightness, a = brand.getHslF()
    if h < 0:
        h = 0.0            # achromatic: getHslF reports hue -1
    for step in range(1, 21):
        moved = lightness + (0.05 * step if dark else -0.05 * step)
        candidate = QColor.fromHslF(h, s, max(0.0, min(1.0, moved)), a)
        if _contrast(candidate, surface) >= _MIN_CONTRAST:
            return candidate
    return QColor("#f2f4f8") if dark else QColor("#12151b")


def _brand_pixmap(app_id: str, px: int, tone: QColor) -> QPixmap | None:
    """Render the bundled SVG at 2x, recoloured to `tone`.

    Simple Icons marks are single-path monochrome silhouettes, so the
    recolour is a flat SourceIn composite over the rendered alpha — no
    per-path editing, and it works for every mark identically.
    """
    entry = _manifest().get(app_id)
    if not entry:
        return None
    path = resources.find_resource(f"assets/appicons/{entry.get('file', '')}")
    if not path or not os.path.isfile(path):
        return None
    try:
        renderer = QSvgRenderer(path)
        if not renderer.isValid():
            return None
        # DEVICE pixels while painting; the 2x device-pixel-ratio is
        # attached only AFTER the last stroke. Setting it first would halve
        # the painter's logical coordinate space, so every rect below —
        # sized in device pixels — would overflow it and the mark would be
        # clipped to its top-left quadrant.
        size = px * 2
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 10% inset: the marks are drawn edge-to-edge in their viewBox,
        # and a logo butting against the row's text needs optical padding
        # to sit as calmly as the shell icons beside it.
        pad = size * 0.10
        renderer.render(p, QRectF(pad, pad, size - pad * 2, size - pad * 2))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pm.rect(), tone)
        p.end()
        pm.setDevicePixelRatio(2.0)
        return pm
    except Exception:
        return None


# ============================================================
#  3. THE NEUTRAL GLYPH
# ============================================================
def _neutral_pixmap(px: int, tone: QColor) -> QPixmap:
    """A soft rounded package mark — the honest "no logo available"
    state. Identical for every app that reaches it, deliberately: a mark
    that varies per app (the old letter monogram) reads as branding and
    invites the question "why is Epic Games a letter E?"."""
    # device pixels first, DPR attached last — see _brand_pixmap's note
    size = px * 2
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    body = QColor(tone)
    body.setAlphaF(0.55)
    pen_w = max(2.0, size * 0.055)
    from PySide6.QtGui import QPen
    p.setPen(QPen(body, pen_w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    inset = size * 0.20
    box = QRectF(inset, inset, size - inset * 2, size - inset * 2)
    p.drawRoundedRect(box, size * 0.10, size * 0.10)
    # a single horizontal seam — reads as a parcel, not as a blank frame
    p.drawLine(int(box.left()), int(box.center().y()),
               int(box.right()), int(box.center().y()))
    p.end()
    pm.setDevicePixelRatio(2.0)
    return pm


# ============================================================
#  PUBLIC ENTRY POINT
# ============================================================
def app_icon(app_name: str, px: int, t: dict, app_id: str = "") -> QPixmap:
    """The row icon for `app_name` (and `app_id`, when the caller has it).

    Cached per (id, name, size, theme). The theme is part of the key
    because both the brand recolour and the neutral glyph are solved
    against the current surface — see the contrast-guard note above.
    """
    dark = t.get("name", "dark") == "dark"
    surface = QColor(t.get("dialog_bg", "#16181d"))
    if surface.alpha() == 0:
        surface = QColor("#16181d" if dark else "#ffffff")
    key = (app_id, app_name, px, "d" if dark else "l")
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached

    pm = None
    entry = _manifest().get(app_id) if app_id else None
    if entry:
        brand = QColor(entry.get("hex", "#000000"))
        if not brand.isValid():
            brand = QColor("#888888")
        pm = _brand_pixmap(app_id, px,
                           _readable_brand_color(brand, surface, dark))
    if pm is None:
        path = _installed_icon_path(app_name)
        if path:
            pm = _shell_pixmap(path, px)
    if pm is None:
        pm = _neutral_pixmap(px, QColor(t.get("text_faint", "#858d9d")))

    _PIXMAP_CACHE[key] = pm
    return pm
