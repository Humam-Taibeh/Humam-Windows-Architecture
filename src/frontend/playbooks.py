"""
src/frontend/playbooks.py

DECLARATIVE AUTOMATION PROFILES (v10.3).

A playbook is an ordered list of tasks the engine already knows how to run
— "new machine -> apply Post-Install Clean -> walk away". It adds no new
capability to the backend and deliberately cannot: a step is just a `task`
name, dispatched through exactly the same contract a card click uses, so a
playbook can never reach anything the GUI could not already reach, and
nothing here needs a matching dispatcher case of its own.

WHY JSON ON DISK RATHER THAN A PYTHON LIST
    menu_structure.py is the catalog of what the app CAN do; that belongs
    in code because each entry carries behaviour flags the GUI branches on.
    A playbook is a user-editable recipe over that catalog — a technician
    should be able to drop `workstation-standard.json` next to the shipped
    three without touching Python or rebuilding the exe.

VALIDATION IS A FEATURE, NOT A FORMALITY
    A playbook naming a task that does not exist would fail at step N,
    halfway through mutating a machine. Every playbook is therefore
    validated against the live catalog at LOAD time, and an invalid one is
    reported rather than silently skipped: a technician who mistyped a task
    name needs to know before they walk away, not after.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, Signal

from frontend.menu_structure import iter_leaf_items, requires_admin
from utils.helpers import DEFAULT_PLAYBOOK_TIMEOUT, PowerShellTask, TaskResult

#: Directory name searched for *.json playbooks.
PLAYBOOK_DIRNAME = "playbooks"


@dataclass(frozen=True)
class PlaybookStep:
    task: str
    note: str = ""
    optional: bool = False

    #: Resolved from the live catalog at load time — the same dict a card
    #: click would pass to request_task, so timeouts and confirm flags come
    #: from one place and cannot drift.
    item: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.item.get("title", self.task)

    @property
    def needs_admin(self) -> bool:
        return requires_admin(self.task)


@dataclass(frozen=True)
class Playbook:
    id: str
    name: str
    description: str
    icon: str
    steps: tuple[PlaybookStep, ...]
    source: str = ""

    @property
    def needs_admin(self) -> bool:
        return any(step.needs_admin for step in self.steps)

    def __len__(self) -> int:
        return len(self.steps)


class PlaybookError(ValueError):
    """A playbook file that cannot be trusted to run. Carries the file name
    so the user can find and fix it."""


def playbook_dirs() -> list[str]:
    """Every directory searched for playbooks, most specific first.

    Mirrors main._locate_ps1's resolution order rather than inventing a
    second one: the PyInstaller extraction dir first when frozen, then the
    repo layout for a source checkout.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.dirname(here)
    repo_root = os.path.dirname(src_dir)

    candidates = [
        os.path.join(repo_root, PLAYBOOK_DIRNAME),
        os.path.join(src_dir, PLAYBOOK_DIRNAME),
    ]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.insert(0, os.path.join(meipass, PLAYBOOK_DIRNAME))
    # Alongside a frozen exe, so a technician can add playbooks to an
    # installed copy without rebuilding it.
    if getattr(sys, "frozen", False):
        candidates.insert(0, os.path.join(
            os.path.dirname(sys.executable), PLAYBOOK_DIRNAME))
    return [path for path in candidates if os.path.isdir(path)]


def _catalog() -> dict[str, dict]:
    """task name -> the catalog item, expanded through hubs."""
    return {item["task"]: item for item, _crumb in iter_leaf_items()
            if item.get("task")}


def parse_playbook(raw: dict, source: str = "") -> Playbook:
    """Validate one decoded playbook document against the live catalog.

    Raises PlaybookError with a message aimed at whoever wrote the file.
    """
    where = os.path.basename(source) if source else "playbook"

    if not isinstance(raw, dict):
        raise PlaybookError(f"{where}: expected a JSON object at the top level")

    playbook_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not playbook_id:
        raise PlaybookError(f"{where}: missing required field 'id'")
    if not name:
        raise PlaybookError(f"{where}: missing required field 'name'")

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlaybookError(f"{where}: 'steps' must be a non-empty list")

    catalog = _catalog()
    steps: list[PlaybookStep] = []
    for index, entry in enumerate(raw_steps, start=1):
        if not isinstance(entry, dict):
            raise PlaybookError(f"{where}: step {index} is not an object")
        task = str(entry.get("task") or "").strip()
        if not task:
            raise PlaybookError(f"{where}: step {index} has no 'task'")
        if task.startswith("@"):
            # Local actions open a viewer or a dialog; queueing one would
            # block the run waiting for a human, which is the opposite of
            # what a playbook is for.
            raise PlaybookError(
                f"{where}: step {index} uses the GUI-local action '{task}'. "
                "Playbooks may only contain backend tasks.")
        if task not in catalog:
            raise PlaybookError(
                f"{where}: step {index} names unknown task '{task}'. "
                "It must match a `task` in menu_structure.py.")
        steps.append(PlaybookStep(
            task=task,
            note=str(entry.get("note") or ""),
            optional=bool(entry.get("optional")),
            item=catalog[task],
        ))

    return Playbook(
        id=playbook_id,
        name=name,
        description=str(raw.get("description") or ""),
        icon=str(raw.get("icon") or "📘"),
        steps=tuple(steps),
        source=source,
    )


def load_playbooks() -> tuple[list[Playbook], list[str]]:
    """(playbooks, errors) across every search directory.

    Never raises. A malformed file yields a message in `errors` and does
    not stop the valid ones loading — one bad user-authored file must not
    take the whole feature offline. Duplicate ids resolve to the
    highest-priority directory, matching playbook_dirs' ordering.
    """
    found: dict[str, Playbook] = {}
    errors: list[str] = []

    for directory in playbook_dirs():
        try:
            names = sorted(os.listdir(directory))
        except OSError as exc:
            errors.append(f"{directory}: {exc}")
            continue
        for filename in names:
            if not filename.lower().endswith(".json"):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, encoding="utf-8") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError) as exc:
                errors.append(f"{filename}: {exc}")
                continue
            try:
                playbook = parse_playbook(raw, source=path)
            except PlaybookError as exc:
                errors.append(str(exc))
                continue
            found.setdefault(playbook.id, playbook)

    return sorted(found.values(), key=lambda p: p.name), errors


# ============================================================
#  SEQUENTIAL RUNNER
# ============================================================
@dataclass
class StepResult:
    index: int
    task: str
    title: str
    outcome: str             # "ok" | "error" | "skipped" | "cancelled"
    message: str = ""
    duration_ms: float = 0.0
    meta: dict | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in ("ok", "skipped")


@dataclass
class PlaybookRun:
    playbook: Playbook
    dry_run: bool
    results: list[StepResult] = field(default_factory=list)
    cancelled: bool = False
    halted_on: int | None = None      # index of the required step that failed

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.outcome == "ok")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome == "error")

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.outcome == "skipped")

    @property
    def duration_ms(self) -> float:
        return sum(r.duration_ms for r in self.results)

    @property
    def complete(self) -> bool:
        return (not self.cancelled and self.halted_on is None
                and len(self.results) == len(self.playbook))


class PlaybookRunner(QObject):
    """Runs a playbook's steps one at a time through the ordinary engine.

    ONE STEP IN FLIGHT, ALWAYS. Each step gets its own QThread +
    PowerShellTask and the next is only started from the previous one's
    terminal signal. Running them concurrently would be faster and wrong:
    the tasks mutate overlapping registry and service state, and two
    engines racing on a restore point is exactly the situation the restore
    point exists to protect against.

    FAILURE POLICY. A required step that fails HALTS the run — a playbook
    is a baseline, and continuing to tweak a machine whose restore point
    could not be created, or whose telemetry pass errored, produces a
    half-configured box that is worse than an obvious stop. A step marked
    `optional` records its failure and the run continues; that flag is
    what a playbook author uses to say "nice to have".
    """

    step_started = Signal(int)              # index into playbook.steps
    step_output = Signal(str, bool)         # forwarded live console
    step_finished = Signal(int, object)     # index, StepResult
    finished = Signal(object)               # PlaybookRun

    def __init__(self, ps1_path: str, playbook: Playbook,
                 dry_run: bool = False, parent: QObject | None = None):
        super().__init__(parent)
        self.ps1_path = ps1_path
        self.playbook = playbook
        self.run = PlaybookRun(playbook=playbook, dry_run=dry_run)
        self._index = -1
        self._thread: QThread | None = None
        self._worker: PowerShellTask | None = None
        self._started_at = 0.0
        self._cancelled = False
        # Set when a step's outcome says "keep going"; consumed by
        # _on_thread_finished so the next step starts only after the
        # current thread is fully torn down.
        self._pending_advance = False
        # finished is a terminal signal — several paths can reach the end
        # (last step, halt, cancel) and exactly one emission must escape.
        self._emitted = False

    # -- lifecycle --------------------------------------------
    def start(self):
        self._advance()

    def cancel(self):
        """Stop after killing the step in flight. The steps already applied
        are NOT rolled back — that is what the restore point every shipped
        playbook opens with is for, and silently reversing completed work
        would be a bigger surprise than leaving it."""
        self._cancelled = True
        self.run.cancelled = True
        if self._worker is not None:
            self._worker.cancel()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    # -- the queue --------------------------------------------
    def _advance(self):
        if self._cancelled:
            self._emit_finished()
            return
        self._index += 1
        if self._index >= len(self.playbook.steps):
            self._emit_finished()
            return

        step = self.playbook.steps[self._index]
        self.step_started.emit(self._index)

        thread = QThread(self)
        worker = PowerShellTask(
            self.ps1_path, step.task,
            timeout=step.item.get("timeout", DEFAULT_PLAYBOOK_TIMEOUT),
            dry_run=self.run.dry_run)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.output.connect(self.step_output)
        worker.finished.connect(self._on_step_finished)
        worker.failed.connect(self._on_step_failed)
        worker.cancelled.connect(self._on_step_cancelled)
        for signal in (worker.finished, worker.failed, worker.cancelled):
            signal.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._worker = worker
        self._started_at = time.monotonic()
        thread.start()

    def _elapsed_ms(self) -> float:
        return (time.monotonic() - self._started_at) * 1000.0

    def _record(self, outcome: str, message: str, meta: dict | None = None):
        step = self.playbook.steps[self._index]
        result = StepResult(
            index=self._index, task=step.task, title=step.title,
            outcome=outcome, message=message,
            duration_ms=self._elapsed_ms(), meta=meta)
        self.run.results.append(result)
        self.step_finished.emit(self._index, result)
        return result

    def _on_step_finished(self, result: TaskResult):
        if result.success:
            self._record("ok", result.message, result.meta)
            self._continue()
            return

        self._record("error", result.message, result.meta)
        step = self.playbook.steps[self._index]
        if step.optional:
            self._continue()
        else:
            self.run.halted_on = self._index
            self._finish_after_thread()

    def _on_step_failed(self, message: str):
        # Timeout / missing powershell.exe — always treated as a hard stop
        # unless the step opted out, same rule as an ERROR verdict.
        self._record("error", message)
        if self.playbook.steps[self._index].optional:
            self._continue()
        else:
            self.run.halted_on = self._index
            self._finish_after_thread()

    def _on_step_cancelled(self):
        self._record("cancelled", "Stopped before this step completed.")
        self._cancelled = True
        self.run.cancelled = True
        self._finish_after_thread()

    def _continue(self):
        # Queue the next step only once the current thread has fully torn
        # down, so two engines can never overlap.
        self._pending_advance = True

    def _finish_after_thread(self):
        self._pending_advance = False

    def _on_thread_finished(self):
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None
        if self._pending_advance:
            self._pending_advance = False
            self._advance()
        else:
            self._emit_finished()

    def _emit_finished(self):
        if self._emitted:
            return
        self._emitted = True
        self.finished.emit(self.run)
