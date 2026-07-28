"""
src/utils/prefs.py

USER PREFERENCES — the app's small, durable memory (v10).

Before this, Pulse remembered nothing between launches: it always opened
dark, always at the default size in the middle of the primary monitor, and
always with the Activity drawer unpinned, no matter what the user had
chosen last time. Every session started by undoing the previous one.

Backed by QSettings, so storage is the platform-native location
(HKCU\\Software\\HumamTaibeh\\Pulse on Windows) with no file handling,
no serialisation format to maintain, and no risk of a corrupt file
breaking startup — every getter degrades to its default.

Deliberately NOT stored here: anything the backend owns (applied tweak
state is read live from the system by GetTweakState, never cached, so the
GUI can't disagree with reality after a change made outside Pulse).
"""
from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QSettings

_ORG = "HumamTaibeh"
_APP = "Pulse"

# Recent operations keeps a short, fixed-length trail. Long enough to be
# useful for "run that again", short enough that the sidebar panel stays a
# glance rather than a history log.
RECENT_LIMIT = 3


def _settings() -> QSettings:
    return QSettings(_ORG, _APP)


# ============================================================
#  THEME
# ============================================================
def theme_mode(default: str = "dark") -> str:
    mode = str(_settings().value("ui/theme", default))
    return mode if mode in ("dark", "light") else default


def set_theme_mode(mode: str):
    _settings().setValue("ui/theme", mode)


# ============================================================
#  WINDOW GEOMETRY
# ============================================================
def window_geometry() -> QByteArray | None:
    """Qt's own opaque geometry blob (saveGeometry/restoreGeometry). Using
    Qt's format rather than storing x/y/w/h ourselves means multi-monitor
    placement, DPI changes and the maximised flag are all handled by Qt —
    including the case where the monitor the window was last on no longer
    exists."""
    value = _settings().value("ui/geometry")
    return value if isinstance(value, QByteArray) and not value.isEmpty() else None


def set_window_geometry(blob: QByteArray):
    _settings().setValue("ui/geometry", blob)


# ============================================================
#  ACTIVITY DRAWER
# ============================================================
def drawer_pinned(default: bool = False) -> bool:
    value = _settings().value("ui/drawer_pinned", default)
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes")


def set_drawer_pinned(pinned: bool):
    _settings().setValue("ui/drawer_pinned", bool(pinned))


# ============================================================
#  RECENT OPERATIONS
# ============================================================
def recent_operations() -> list[dict]:
    """Most-recent-first list of {task, title, glyph, accent, outcome}.

    Stored as a JSON string rather than a QSettings list because QSettings
    flattens nested containers inconsistently across backends. Any parse
    failure yields an empty trail — a broken history must never be able to
    stop the app from starting."""
    raw = _settings().value("ui/recent", "")
    if not raw:
        return []
    try:
        parsed = json.loads(str(raw))
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, dict) and entry.get("task")][
        :RECENT_LIMIT]


def push_recent_operation(task: str, title: str, glyph: str,
                          accent: str, outcome: str):
    """Record a completed run, newest first, de-duplicated by task so
    re-running the same thing refreshes its entry instead of filling the
    whole trail with one repeated card."""
    if not task or task.startswith("@"):
        return          # local viewer actions aren't "operations"
    trail = [e for e in recent_operations() if e.get("task") != task]
    trail.insert(0, {"task": task, "title": title, "glyph": glyph,
                     "accent": accent, "outcome": outcome})
    _settings().setValue("ui/recent", json.dumps(trail[:RECENT_LIMIT]))


def clear_recent_operations():
    _settings().remove("ui/recent")
