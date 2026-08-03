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

# Task history answers "when did I last run THIS card, and how long does
# it take?" for every task independently — the per-task record behind each
# card's "Ran 3d ago · ~2m" caption and its ACTION DUE badge.
#
# A second store used to sit beside it: an ordered, three-deep "recent
# operations" trail feeding the sidebar's RECENT panel. The panel was cut
# in the v1.0 RC layout pass (see main.PulseApp._build_ui) and the trail
# went with it — nothing read it, and a store nothing reads is a schema
# the next version still has to migrate.
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
#  PER-TASK HISTORY  (last run + typical duration)
# ============================================================
def task_history() -> dict[str, dict]:
    """{task: {"last_ts": float, "runs": int, "avg_ms": float,
               "last_ms": float, "outcome": str}}

    Same defensive posture as every getter above: any corruption yields
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
