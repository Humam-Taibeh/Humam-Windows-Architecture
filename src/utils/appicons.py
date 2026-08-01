"""
src/utils/appicons.py

APP ICON RESOLUTION for Software Management rows.

Every catalog row (SoftwareCatalogDialog, Update Center) shows a 28px
icon beside the app's name. Three sources, tried in order:

  1. A BUNDLED VECTOR MARK — assets/appicons/<AppId>.svg, fetched at BUILD
     time by tools/fetch_app_icons.py and rendered here as vector so it
     stays crisp at any size or DPI. EVERY ONE IS THE VENDOR'S GENUINE
     MARK; Pulse ships no hand-drawn stand-ins. Two kinds live here, and
     the manifest's "color" flag distinguishes them:

       - MONOCHROME (Simple Icons): a single-path silhouette with a brand
         hex, recoloured at paint time subject to the contrast guard below.

       - FULL COLOUR (Iconify's brand-logo sets, principally the CC0 `logos`
         collection): the real artwork including gradients — VS Code's blue
         ribbon, Edge's swirl. Rendered exactly as drawn; legibility is
         handled by a backing plaque rather than by altering the colours,
         because recolouring real vendor artwork is what would make it
         inauthentic.

     SEVEN CATALOG APPS HAVE NO BUNDLED MARK, and that is a finding, not
     an omission: BlueStacks, DirectX, CPU-Z, GPU-Z, HWMonitor,
     CrystalDiskInfo and Open WebUI have no authentic logo in ANY open,
     licensed set. That was measured — the full Simple Icons index (~3300
     marks), the whole `logos` collection (1861), and Iconify's federated
     search across every collection it aggregates. They fall to tier 2,
     where the vendor's own artwork is read out of their own installed
     binary, and to tier 3 when the app is not present. An invented
     pictogram in that gap was tried and REMOVED: a mark that describes
     software is still not that software's logo, and the rule here is that
     a wrong logo is worse than no logo.

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
     read out of the app's own binary. Every CATALOG app now has a bundled
     mark, so this tier no longer fires for them; it still covers the
     Update Center, which lists whatever winget reports as upgradable and
     is therefore not limited to the catalog.

  3. A NEUTRAL GLYPH — a soft rounded "package" mark in the theme's muted
     tone. This replaced the LETTER MONOGRAM plaques, which put a bare
     "E", "R" or "B" where the Epic, Rockstar and BlueStacks logos
     belonged and read as an unfinished placeholder. A neutral mark that
     is identical for every unknown app says "no logo available"
     honestly; an invented letter tile pretends to be branding.

     No catalog row reaches this any more (tests/test_contract.py pins
     that), but it stays as the honest floor for the Update Center's
     off-catalog entries.

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


_RGBA_RE = re.compile(
    r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", re.I)


def _parse_color(value: str, fallback: str) -> QColor:
    """A theme colour token as a QColor, accepting BOTH "#rrggbb" and the
    CSS "rgba(r, g, b, a)" form the palette actually stores.

    Qt does not parse rgba() strings, and — the part that made this a real
    bug rather than a missing feature — QColor("rgba(...)") comes back
    INVALID while still reporting alpha() == 255. The guard here used to be
    `if surface.alpha() == 0`, which therefore never fired: every surface
    handed to the contrast guard was an invalid QColor, which behaves as
    pure black. Both themes were being solved against black, so the light
    theme's readability was decided from the wrong backdrop entirely.

    Parsed locally rather than by importing frontend.theme's to_qcolor:
    utils/ sits BELOW frontend/ in the import graph (theme <- animations <-
    widgets <- main), and reaching upward from here would invert it.
    """
    text = (value or "").strip()
    match = _RGBA_RE.fullmatch(text)
    if match:
        r, g, b, a = match.groups()
        color = QColor(int(r), int(g), int(b))
        if a is not None:
            color.setAlphaF(max(0.0, min(1.0, float(a))))
        return color
    color = QColor(text)
    return color if color.isValid() else QColor(fallback)


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


def _brand_pixmap(app_id: str, px: int, tone: QColor,
                  surface: QColor | None = None) -> QPixmap | None:
    """Render the bundled SVG at 2x.

    TWO KINDS OF MARK live in assets/appicons/, and the manifest's `color`
    flag says which this is:

      MONOCHROME (Simple Icons) — a single-path silhouette carrying no
      colour of its own. Recoloured to `tone` with a flat SourceIn
      composite over the rendered alpha: no per-path editing, identical
      handling for every mark, and it is what lets the contrast guard
      move a #000000 brand off a near-black canvas.

      FULL COLOUR (the `logos` / brand-logo sets) — the vendor's REAL
      artwork, gradients and all: VS Code's blue ribbon, Edge's swirl.
      Rendered exactly as drawn. Pushing one of these through the
      silhouette path would flatten a multi-stop gradient into a single
      blob — authentic artwork, destroyed on paint — so `color` marks skip
      the recolour entirely, and their legibility is solved by the backing
      plaque below instead of by rewriting their colours.
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

        full_colour = bool(entry.get("color"))
        plaque = _backing_plaque(renderer, size, surface) if full_colour else None
        if plaque is not None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(plaque)
            p.drawRoundedRect(QRectF(0, 0, size, size),
                              size * 0.22, size * 0.22)

        # 10% inset: the marks are drawn edge-to-edge in their viewBox,
        # and a logo butting against the row's text needs optical padding
        # to sit as calmly as the shell icons beside it. A mark on a
        # backing plaque insets further so it does not touch the corners.
        pad = size * (0.19 if plaque is not None else 0.10)
        renderer.render(p, QRectF(pad, pad, size - pad * 2, size - pad * 2))
        if not full_colour:
            p.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), tone)
        p.end()
        pm.setDevicePixelRatio(2.0)
        return pm
    except Exception:
        return None


def _mark_luminance(renderer: QSvgRenderer, size: int) -> float | None:
    """Mean WCAG luminance of a rendered mark's OPAQUE pixels, or None.

    Measured rather than declared. The alternative — a per-brand "darkest
    tone" hint in the manifest — is a second set of colour data to keep in
    step with artwork that is fetched automatically, and it would be wrong
    the first time a vendor refreshed their logo. Rendering the real thing
    and reading it back is always current.

    Transparent pixels are excluded: a mark is mostly empty space inside
    its own bounding box, and averaging that in drags every logo toward
    the same middling number and defeats the test.
    """
    try:
        probe = QPixmap(size, size)
        probe.fill(Qt.GlobalColor.transparent)
        painter = QPainter(probe)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        image = probe.toImage()
    except Exception:
        return None

    total = 0.0
    counted = 0
    step = max(1, size // 24)          # ~24x24 samples is plenty for a mean
    for y in range(0, size, step):
        for x in range(0, size, step):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() < 128:
                continue
            total += _luminance(pixel)
            counted += 1
    if counted == 0:
        return None
    return total / counted


def _backing_plaque(renderer: QSvgRenderer, size: int,
                    surface: QColor | None) -> QColor | None:
    """A soft neutral plaque to sit a full-colour logo on, or None when the
    logo already reads against `surface`.

    The trick that rescues a monochrome silhouette cannot be used here:
    walking a brand's hue until it clears the floor is exactly what makes
    a recoloured silhouette legible, and exactly what would make real
    vendor artwork inauthentic. So the mark is left alone and the SURFACE
    moves instead — what macOS and every app store do when they sit an app
    icon on a light tile.

    Applied only when measurement says it is needed, so a vivid mark like
    Edge's swirl keeps the clean plaque-free look it already has on
    obsidian, while VS Code's ribbon — whose dark strokes vanish into the
    canvas — gets its tile.
    """
    if surface is None:
        return None
    luminance = _mark_luminance(renderer, size)
    if luminance is None:
        return None
    surface_luminance = _luminance(surface)
    hi, lo = max(luminance, surface_luminance), min(luminance, surface_luminance)
    if (hi + 0.05) / (lo + 0.05) >= _MIN_CONTRAST:
        return None
    # Near-white rather than pure: a hard #ffffff tile on the light theme's
    # porcelain canvas reads as a hole punched in the row.
    plaque = QColor("#f7f8fa")
    plaque.setAlphaF(0.96)
    return plaque


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
    surface = _parse_color(t.get("dialog_bg", ""),
                           "#16181d" if dark else "#ffffff")
    key = (app_id, app_name, px, "d" if dark else "l")
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached

    pm = None
    entry = _manifest().get(app_id) if app_id else None
    if entry:
        if entry.get("color"):
            # Real vendor artwork: rendered as drawn. `tone` is unused on
            # this path, so the contrast guard is skipped here and the
            # backing plaque inside _brand_pixmap does the legibility work
            # instead — moving the surface, never the brand's own colours.
            pm = _brand_pixmap(app_id, px, QColor("#000000"), surface)
        else:
            brand = QColor(entry.get("hex", "#000000"))
            if not brand.isValid():
                brand = QColor("#888888")
            pm = _brand_pixmap(app_id, px,
                               _readable_brand_color(brand, surface, dark),
                               surface)
    if pm is None:
        path = _installed_icon_path(app_name)
        if path:
            pm = _shell_pixmap(path, px)
    if pm is None:
        pm = _neutral_pixmap(px, QColor(t.get("text_faint", "#858d9d")))

    _PIXMAP_CACHE[key] = pm
    return pm
