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
import time

from PySide6.QtCore import QByteArray, QSettings

_ORG = "HumamTaibeh"
_APP = "Pulse"

# Recent operations keeps a short, fixed-length trail. Long enough to be
# useful for "run that again", short enough that the sidebar panel stays a
# glance rather than a history log.
RECENT_LIMIT = 3

# Task history is a DIFFERENT thing from the recent trail, and the two are
# deliberately not merged. The trail answers "what did I just do?" — it is
# ordered, capped at three, and de-duplicated by task, so re-running one
# operation refreshes its entry instead of filling the sidebar. History
# answers "when did I last run THIS card, and how long does it take?" for
# every task independently, which needs a per-task record the trail cannot
# carry: it holds no timestamp, no duration, and forgets everything past
# the third entry.
#
# The bound is defensive rather than functional — there are 38 tasks, so
# the map is tiny. It only matters across versions, where a renamed or
# dropped task would otherwise leave a record nothing ever reads again.
HISTORY_LIMIT = 120

# Rolling average window. Short enough that a machine which got faster
# (SSD swap, fewer startup entries) stops being described by its old
# timings; long enough that one anomalous run doesn't redefine the
# estimate.
HISTORY_RUNS_WEIGHT = 5


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


# ============================================================
#  PER-TASK HISTORY  (last run + typical duration)
# ============================================================
def task_history() -> dict[str, dict]:
    """{task: {"last_ts": float, "runs": int, "avg_ms": float,
               "last_ms": float, "outcome": str}}

    Same defensive posture as recent_operations(): any corruption yields
    an empty map rather than an exception, because a card's "last run"
    caption is decoration and must never be able to stop the app starting
    or block a task from running.
    """
    raw = _settings().value("ui/task_history", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    clean: dict[str, dict] = {}
    for task, entry in parsed.items():
        if not isinstance(task, str) or not isinstance(entry, dict):
            continue
        try:
            clean[task] = {
                "last_ts": float(entry.get("last_ts", 0.0)),
                "runs": int(entry.get("runs", 0)),
                "avg_ms": float(entry.get("avg_ms", 0.0)),
                "last_ms": float(entry.get("last_ms", 0.0)),
                "outcome": str(entry.get("outcome", "")),
            }
        except (TypeError, ValueError):
            continue        # one bad record must not discard the rest
    return clean


def record_task_run(task: str, duration_ms: float, outcome: str):
    """Fold one completed run into `task`'s history.

    The average is an exponential moving average rather than a true mean:
    it needs no sample list in storage, and it lets a machine whose real
    timings have changed converge instead of being anchored forever by
    runs from a year ago. HISTORY_RUNS_WEIGHT sets how fast it forgets.

    Only genuine verdicts are recorded — a cancelled run is a partial
    measurement, and averaging it in would drag every estimate downward
    and quietly make the slowest tasks look fast.
    """
    if not task or task.startswith("@"):
        return
    if duration_ms <= 0:
        return

    history = task_history()
    entry = history.get(task)
    if entry and entry.get("runs"):
        weight = 1.0 / min(entry["runs"] + 1, HISTORY_RUNS_WEIGHT)
        avg = entry["avg_ms"] + (duration_ms - entry["avg_ms"]) * weight
        runs = entry["runs"] + 1
    else:
        avg = float(duration_ms)
        runs = 1

    history[task] = {
        "last_ts": time.time(),
        "runs": runs,
        "avg_ms": float(avg),
        "last_ms": float(duration_ms),
        "outcome": str(outcome),
    }

    if len(history) > HISTORY_LIMIT:
        # Evict the least recently run — the ones a rename or a dropped
        # task would have stranded.
        ordered = sorted(history.items(),
                         key=lambda kv: kv[1].get("last_ts", 0.0), reverse=True)
        history = dict(ordered[:HISTORY_LIMIT])

    _settings().setValue("ui/task_history", json.dumps(history))


def clear_task_history():
    _settings().remove("ui/task_history")
