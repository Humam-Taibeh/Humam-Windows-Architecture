"""
src/utils/helpers.py

Shared utilities for the Pulse GUI:

  - PowerShellTask / TaskResult : runs core.ps1 in a background QThread and
    reports back to the GUI thread ONLY through Qt signals. This is the fix
    for the main correctness bug in the previous version, which mutated
    QLabel/QProgressBar widgets directly from a raw Python `threading.Thread`.
    Qt widgets are not thread-safe - touching them from any thread other
    than the GUI thread is undefined behaviour (works "most of the time",
    then crashes or silently corrupts the UI under load). QThread + Signal
    marshals the result back onto the GUI thread automatically.

  - Toast / ToastManager : glass-morphism toast notifications that slide in
    from the top-right corner and auto-dismiss, matching the "VS Code /
    macOS" style requested.

  - HoverGlow : a tiny event filter that adds an animated glow (via
    QGraphicsDropShadowEffect) to a widget on hover, since QSS itself cannot
    animate transitions between pseudo-states.
"""
from __future__ import annotations

import codecs
import json
import subprocess
import sys
import threading
from dataclasses import dataclass

from PySide6.QtCore import (
    QEasingCurve, QElapsedTimer, QObject, QPoint, QPropertyAnimation, QTimer,
    Qt, Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QWidget,
)

from frontend import theme as TH


# ============================================================
#  TASK RESULT
# ============================================================
@dataclass
class TaskResult:
    success: bool
    message: str
    # Structured payload (v6.3): populated when the backend emitted a
    # ##PULSE##DATA|<json> line (Write-GuiData in 00-Foundation.ps1) - the
    # winget update scan and the startup report both use this instead of
    # cramming a table into the human-readable message. None for every
    # older/simpler task that never emits one.
    data: object | None = None


# Verdict sentinel (v6.1): the backend prefixes its final contract line with
# this marker, so stray trailing output from an external tool can never be
# mistaken for the verdict. Bare SUCCESS|/ERROR| lines from pre-6.1 backends
# are still accepted as a fallback.
VERDICT_SENTINEL = "##PULSE##"
# Structured-data line prefix (v6.3) - see TaskResult.data above.
VERDICT_DATA_PREFIX = VERDICT_SENTINEL + "DATA|"


# ============================================================
#  BACKGROUND POWERSHELL WORKER (runs on a QThread)
# ============================================================
class PowerShellTask(QObject):
    """
    Executes `core.ps1 -Task <name>` in a hidden, non-blocking subprocess,
    streams its stdout live (including in-place carriage-return progress),
    and reports the outcome via signals. Must be moved to a QThread by the
    caller - never call `run()` directly on the GUI thread. `cancel()` is
    the one method that is safe (and intended) to call from the GUI thread.
    """

    finished = Signal(TaskResult)   # backend returned a SUCCESS|... or ERROR|... line
    failed = Signal(str)            # timeout, missing powershell.exe, or other exception
    cancelled = Signal()            # user-initiated hard stop (global kill switch)
    output = Signal(str, bool)      # (text, replace_last): replace_last=True rewrites
                                    # the console's newest line in place — the CR
                                    # progress semantics of sfc / DISM / winget

    def __init__(self, ps1_path: str, task_name: str, timeout: int = 120,
                 app_ids: list[str] | None = None, dry_run: bool = False,
                 office_setup: str | None = None, office_config: str | None = None,
                 local_installer_path: str | None = None):
        super().__init__()
        self.ps1_path = ps1_path
        self.task_name = task_name
        self.timeout = timeout
        self.app_ids = app_ids or []
        # Resolved by the Office ODT wizard (widgets.OfficeWizardDialog)
        # before this worker is ever constructed — both set, or both None.
        self.office_setup = office_setup
        self.office_config = office_config
        # Resolved by the generic Tool Install Wizard's Path C
        # (widgets.ToolInstallWizardDialog) — task InstallLocalFile.
        self.local_installer_path = local_installer_path
        # dry_run=True appends -WhatIf: the backend simulates the task and
        # reports "[WHATIF] ..." lines / a "[DRY-RUN]" result instead of
        # mutating the system. Same SUCCESS|/ERROR| contract either way.
        self.dry_run = dry_run
        # Kill-switch state. cancel() runs on the GUI thread while run()
        # blocks reading stdout on the worker thread, so the Popen handle
        # is shared under a lock and the request travels as an Event.
        self._process: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()
        self._cancel_evt = threading.Event()

    @staticmethod
    def _ps_quote(value: str) -> str:
        """Escape a value for a single-quoted PowerShell string literal."""
        return value.replace("'", "''")

    @staticmethod
    def _kill_process_tree(process: subprocess.Popen):
        """Terminate powershell.exe AND its children (winget, sfc, DISM...).
        A bare process.kill() would orphan the actual worker processes."""
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            if process.poll() is None:
                process.kill()
        except OSError:
            pass

    def cancel(self):
        """Hard-stop the running task: kill the entire process tree now.

        Thread-safe by design — called directly from the GUI thread while
        run() blocks on the pipe in the worker thread. It never emits a
        signal itself: run() owns every terminal emission and reports
        `cancelled` once the pipe drains, so exactly one of finished /
        failed / cancelled fires per task.
        """
        self._cancel_evt.set()
        with self._proc_lock:
            process = self._process
        if process is not None and process.poll() is None:
            self._kill_process_tree(process)

    @staticmethod
    def _split_events(buf: str, replace_next: bool) -> tuple[str, list[tuple[str, bool]], bool]:
        """Cut `buf` into (text, replace_last) line events.

        LF and CRLF close a line normally. A bare CR closes the line AND
        marks the next segment as an in-place rewrite — the console
        semantics sfc / DISM / winget use for progress percentages. A lone
        CR at the end of the buffer is held back: it may be the first half
        of a CRLF pair split across two chunks.
        Returns (unconsumed remainder, events, pending replace flag).
        """
        events: list[tuple[str, bool]] = []
        start = i = 0
        n = len(buf)
        while i < n:
            ch = buf[i]
            if ch == "\n":
                events.append((buf[start:i], replace_next))
                replace_next = False
                i += 1
                start = i
            elif ch == "\r":
                if i + 1 == n:
                    break                    # undecidable until the next chunk
                events.append((buf[start:i], replace_next))
                if buf[i + 1] == "\n":
                    replace_next = False     # CRLF — an ordinary newline
                    i += 2
                else:
                    replace_next = True      # bare CR — the next write overwrites
                    i += 1
                start = i
            else:
                i += 1
        return buf[start:], events, replace_next

    @staticmethod
    def _coalesce(events: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
        """Collapse rewrite bursts inside one chunk.

        A replace event supersedes the event right before it (that is
        literally what a carriage-return rewrite means on a console), so a
        burst of progress frames arriving in a single chunk collapses to
        one UI event — the flood control that keeps the GUI event queue
        bounded on chatty tools.
        """
        out: list[tuple[str, bool]] = []
        for text, replace in events:
            if replace and out:
                out[-1] = (text, out[-1][1])
            else:
                out.append((text, replace))
        return out

    def run(self):
        process = None
        timeout_timer = None
        timed_out = threading.Event()
        try:
            # Force UTF-8 on the pipe: PowerShell 5.1 otherwise emits the OEM
            # code page for redirected stdout, which mangles the backend's
            # unicode glyphs (✓ ✗ — ·) and can abort the read loop entirely.
            cmd = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                   f"& '{self._ps_quote(self.ps1_path)}' -Task '{self._ps_quote(self.task_name)}'")
            if self.app_ids:
                ids_csv = ",".join(self.app_ids)
                cmd += f" -AppIds '{self._ps_quote(ids_csv)}'"
            if self.office_setup and self.office_config:
                cmd += (f" -OfficeSetupPath '{self._ps_quote(self.office_setup)}'"
                        f" -OfficeConfigPath '{self._ps_quote(self.office_config)}'")
            if self.local_installer_path:
                cmd += f" -LocalInstallerPath '{self._ps_quote(self.local_installer_path)}'"
            if self.dry_run:
                cmd += " -WhatIf"

            # Binary pipe + incremental UTF-8 decoder (below): chunk-level
            # reads deliver carriage-return progress the instant it is
            # written, and a multi-byte glyph split across two chunks still
            # decodes intact — same net effect as the old encoding="utf-8",
            # errors="replace" text mode, without line buffering latency.
            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            # STARTUPINFO / CREATE_NO_WINDOW only exist on Windows. Guard so the
            # rest of the app can still be imported/tested on other platforms.
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                popen_kwargs["startupinfo"] = startupinfo
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                )

            process = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                **popen_kwargs,
            )

            with self._proc_lock:
                self._process = process
            # cancel() may have fired between Popen and the store above -
            # honor it now, or the kill would race the first pipe read.
            if self._cancel_evt.is_set():
                self._kill_process_tree(process)

            # Blocking pipe reads can't be interrupted by a wall-clock check
            # between iterations if the child goes silent mid-line, so the
            # timeout is enforced independently by a watchdog that kills the
            # process outright - the read loop below then just unblocks.
            def _on_timeout():
                if process.poll() is not None:
                    return  # finished right at the deadline — not a timeout
                timed_out.set()
                self._kill_process_tree(process)

            timeout_timer = threading.Timer(self.timeout, _on_timeout)
            timeout_timer.start()

            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            lines: list[str] = []       # logical console mirror - the final
                                        # SUCCESS|/ERROR| parse reads this
            buf = ""
            replace_next = False        # a bare CR marks the NEXT segment as
                                        # an in-place rewrite of the last line

            def _apply(text: str, replace: bool):
                if replace and lines:
                    lines[-1] = text
                else:
                    lines.append(text)
                if text and not text.startswith(VERDICT_DATA_PREFIX):
                    # The console shows the human-readable verdict, not the
                    # machine sentinel; `lines` keeps the raw text for parsing.
                    # DATA payload lines are structured JSON for the caller,
                    # not console output - they never reach the live console.
                    shown = (text[len(VERDICT_SENTINEL):]
                             if text.startswith(VERDICT_SENTINEL) else text)
                    self.output.emit(shown, replace)

            while True:
                chunk = process.stdout.read1(4096)   # blocks until data or EOF
                if not chunk:
                    break
                buf += decoder.decode(chunk)
                buf, events, replace_next = self._split_events(buf, replace_next)
                for text, replace in self._coalesce(events):
                    _apply(text, replace)

            tail = buf + decoder.decode(b"", final=True)
            if tail:
                if tail.endswith("\r"):
                    tail = tail[:-1]    # dangling CR at EOF just ends the line
                _apply(tail, replace_next)

            process.wait()
            timeout_timer.cancel()

            # Terminal verdict - exactly one signal per task, in priority
            # order: user cancel > watchdog timeout > backend contract line.
            if self._cancel_evt.is_set():
                self.cancelled.emit()
                return
            if timed_out.is_set():
                self.failed.emit(f"Task timed out after {self.timeout}s.")
                return

            # Contract with core.ps1 (v6.1): the verdict is the newest line
            #   ##PULSE##SUCCESS|Human readable message
            #   ##PULSE##ERROR|Human readable message
            # scanned backwards, so a module that leaks stray stdout after
            # the verdict can no longer shadow it. Pre-6.1 backends without
            # the sentinel are parsed via the strict legacy fallback.
            last_line = next(
                (ln[len(VERDICT_SENTINEL):] for ln in reversed(lines)
                 if ln.startswith(VERDICT_SENTINEL) and not ln.startswith(VERDICT_DATA_PREFIX)),
                None)
            if last_line is None:
                last_line = next(
                    (ln for ln in reversed(lines)
                     if ln.startswith("SUCCESS|") or ln.startswith("ERROR|")),
                    "")

            # Structured payload (v6.3): the most recent ##PULSE##DATA| line,
            # if the task emitted one (Write-GuiData). Malformed JSON never
            # aborts the verdict - it just leaves data as None.
            data = None
            raw_data = next(
                (ln[len(VERDICT_DATA_PREFIX):] for ln in reversed(lines)
                 if ln.startswith(VERDICT_DATA_PREFIX)),
                None)
            if raw_data is not None:
                try:
                    data = json.loads(raw_data)
                except ValueError:
                    data = None

            if last_line.startswith("SUCCESS"):
                msg = last_line.split("|", 1)[1].strip() if "|" in last_line else "Task completed."
                self.finished.emit(TaskResult(True, msg, data))
            elif last_line.startswith("ERROR"):
                msg = last_line.split("|", 1)[1].strip() if "|" in last_line else "Task failed."
                self.finished.emit(TaskResult(False, msg, data))
            else:
                # Backend didn't follow the SUCCESS|/ERROR| contract. Don't dump the
                # raw console output into the "silent executor" UI - just report a
                # short, safe fallback message.
                self.finished.emit(TaskResult(False, "Script finished without a recognized status line."))

        except FileNotFoundError:
            self.failed.emit("powershell.exe was not found on this system.")
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, never swallowed
            self.failed.emit(str(exc))
        finally:
            if timeout_timer is not None:
                timeout_timer.cancel()
            # If an exception aborted the read loop, don't leave a live
            # PowerShell (and its winget/sfc children) running headless.
            if process is not None and process.poll() is None:
                self._kill_process_tree(process)


# ============================================================
#  TOAST NOTIFICATION (bottom-right, theme-aware, dismissible)
# ============================================================
class Toast(QFrame):
    """A single notification card. Positioned by ToastManager; never used
    directly.

    v6.2 redesign, fixing every complaint about the old top-right toast:
      - theme-aware: styled from the live token dict (the old toast was a
        hardcoded dark rectangle that looked broken in light mode),
      - anchored bottom-right (VS Code register) so it never covers the
        title bar or fights the caption buttons,
      - click anywhere on it to dismiss instantly,
      - hovering pauses the auto-hide countdown (read at your own pace),
      - status is a quiet ✓ / ✕ / i chip + colored spine, not an emoji.
    """

    WIDTH = 340
    GLYPHS = {"success": "✓", "error": "✕", "info": "i", "warn": "⚠"}

    def __init__(self, parent: QWidget, kind: str, message: str,
                 t: dict, duration_ms: int = 5000):
        super().__init__(parent)
        self.kind = kind
        self.message = message
        self.setObjectName("toast")
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to dismiss")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 11, 16, 11)
        lay.setSpacing(10)

        self._chip = QLabel(self.GLYPHS.get(kind, "i"))
        self._chip.setFixedSize(22, 22)
        self._chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._chip, 0, Qt.AlignmentFlag.AlignTop)

        self._label = QLabel(message)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lay.addWidget(self._label, 1)

        self.apply_theme(t)

        # Height from the real wrapped-text metrics at the final width.
        text_w = self.WIDTH - 14 - 16 - 22 - 10
        text_h = self._label.heightForWidth(text_w)
        self.setFixedHeight(max(46, text_h + 22))

        # Fade in via a transient opacity effect (child widgets have no
        # windowOpacity; the effect is unavoidable here but short-lived
        # and only ever on a 340px card).
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(160)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._slide_anim = QPropertyAnimation(self, b"pos", self)
        self._slide_anim.setDuration(180)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Pausable auto-hide: a single-shot timer plus an elapsed clock so
        # hover can freeze the countdown and resume with the remainder.
        self._life = QTimer(self)
        self._life.setSingleShot(True)
        self._life.timeout.connect(self.dismiss)
        self._clock = QElapsedTimer()
        self._remaining_ms = duration_ms
        self._closing = False

    # -- theming ----------------------------------------------
    def apply_theme(self, t: dict):
        accent = {"success": t["ok"], "error": t["err"], "warn": t["warn"]}.get(
            self.kind, t["accent"])
        self.setStyleSheet(TH.toast_qss(t, accent))
        self._chip.setStyleSheet(TH.toast_icon_qss(t, accent))
        self._label.setStyleSheet(TH.toast_text_qss(t))

    # -- lifecycle --------------------------------------------
    def slide_in(self, target_pos: QPoint):
        start_pos = QPoint(target_pos.x() + 48, target_pos.y())
        self.move(start_pos)
        self.show()
        self.raise_()
        self._slide_anim.setStartValue(start_pos)
        self._slide_anim.setEndValue(target_pos)
        self._slide_anim.start()
        self._fade_anim.start()
        self._clock.start()
        self._life.start(self._remaining_ms)

    def slide_to(self, target_pos: QPoint):
        """Re-position an already-visible toast (stack shuffle)."""
        if self.pos() == target_pos:
            return
        self._slide_anim.stop()
        self._slide_anim.setStartValue(self.pos())
        self._slide_anim.setEndValue(target_pos)
        self._slide_anim.start()

    def extend(self, duration_ms: int):
        """A duplicate message arrived — restart this toast's countdown
        instead of stacking an identical card underneath it."""
        if self._closing:
            return
        self._remaining_ms = duration_ms
        self._clock.restart()
        self._life.start(duration_ms)

    # -- interaction ------------------------------------------
    def enterEvent(self, e):
        if self._life.isActive():
            spent = self._clock.elapsed()
            self._remaining_ms = max(0, self._remaining_ms - spent)
            self._life.stop()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if not self._closing:
            self._clock.restart()
            self._life.start(max(900, self._remaining_ms))
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.dismiss()
        super().mouseReleaseEvent(e)

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        self._life.stop()
        fade_out = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        fade_out.setDuration(140)
        fade_out.setStartValue(self._opacity_effect.opacity())
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        fade_out.finished.connect(self._remove)
        fade_out.start()
        self._fade_out_anim = fade_out  # keep alive

    def _remove(self):
        manager: "ToastManager" = self.property("manager")
        if manager is not None:
            manager._on_toast_closed(self)
        self.deleteLater()


class ToastManager(QObject):
    """Stacks Toasts in the bottom-right corner of `container`, newest
    nearest the corner, older cards pushed upward — the VS Code
    notification pattern. Lives inside the app's own layout hierarchy
    (a child of the shell), so it can never cover the title bar, block
    the caption buttons, or outstack the window."""

    MARGIN_X = 18
    MARGIN_BOTTOM = 52   # clears the status bar + resize grip
    SPACING = 10
    MAX_VISIBLE = 4

    def __init__(self, container: QWidget, t: dict | None = None):
        super().__init__(container)
        self.container = container
        self._t = t or TH.tokens("dark")
        self._toasts: list[Toast] = []

    def apply_theme(self, t: dict):
        self._t = t
        for toast in self._toasts:
            toast.apply_theme(t)

    def show(self, kind: str, message: str, duration_ms: int = 5000):
        # Dedupe: an identical live toast just restarts its countdown.
        for toast in self._toasts:
            if (toast.kind == kind and toast.message == message
                    and not toast._closing):
                toast.extend(duration_ms)
                return toast

        toast = Toast(self.container, kind, message, self._t, duration_ms)
        toast.setProperty("manager", self)
        self._toasts.append(toast)

        # Bound the stack — the oldest card yields its slot.
        overflow = len(self._toasts) - self.MAX_VISIBLE
        if overflow > 0:
            for old in self._toasts[:overflow]:
                old.dismiss()

        positions = self._target_positions()
        for older, pos in zip(self._toasts[:-1], positions[:-1]):
            older.slide_to(pos)
        toast.slide_in(positions[-1])
        return toast

    def _target_positions(self) -> list[QPoint]:
        """Bottom-up stack: the newest toast hugs the corner, each older
        one sits SPACING above the card below it."""
        x = self.container.width() - Toast.WIDTH - self.MARGIN_X
        positions: list[QPoint] = []
        y = self.container.height() - self.MARGIN_BOTTOM
        for toast in reversed(self._toasts):
            y -= toast.height()
            positions.append(QPoint(x, y))
            y -= self.SPACING
        positions.reverse()
        return positions

    def _on_toast_closed(self, closed: Toast):
        if closed in self._toasts:
            self._toasts.remove(closed)
        self.reposition()

    def reposition(self):
        for toast, pos in zip(self._toasts, self._target_positions()):
            toast.slide_to(pos)


# ============================================================
#  HOVER GLOW (animated drop-shadow on hover, since QSS can't animate)
# ============================================================
class HoverGlow(QObject):
    """
    Install on a QWidget to give it a soft animated glow on hover:
        btn.installEventFilter(HoverGlow(btn, color="#00d4ff"))
    Keep a reference to the filter alive (e.g. store it on the widget/list)
    for the lifetime of the widget.
    """

    def __init__(self, widget: QWidget, color: str = "#4cc2ff", max_blur: float = 25.0):
        super().__init__(widget)
        self.widget = widget
        self.effect = QGraphicsDropShadowEffect(widget)
        self.effect.setColor(QColor(color))
        self.effect.setOffset(0, 0)
        self.effect.setBlurRadius(0)
        widget.setGraphicsEffect(self.effect)

        self.anim = QPropertyAnimation(self.effect, b"blurRadius", self)
        self.anim.setDuration(200)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.max_blur = max_blur

    def eventFilter(self, obj, event):
        if obj is self.widget:
            if event.type() == event.Type.Enter:
                self._animate_to(self.max_blur)
            elif event.type() == event.Type.Leave:
                self._animate_to(0.0)
        return False

    def _animate_to(self, value: float):
        self.anim.stop()
        self.anim.setStartValue(self.effect.blurRadius())
        self.anim.setEndValue(value)
        self.anim.start()


# ============================================================
#  PULSE ANIMATION (for the big status icon while a task runs)
# ============================================================
class PulseAnimation(QObject):
    """Loops a QLabel's opacity between 1.0 and 0.35 to signal 'working'."""

    def __init__(self, label: QLabel):
        super().__init__(label)
        self.label = label
        self.effect = QGraphicsOpacityEffect(label)
        self.effect.setOpacity(1.0)
        label.setGraphicsEffect(self.effect)

        self.anim = QPropertyAnimation(self.effect, b"opacity", self)
        self.anim.setDuration(900)
        self.anim.setStartValue(1.0)
        self.anim.setKeyValueAt(0.5, 0.35)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.anim.setLoopCount(-1)  # infinite

    def start(self):
        self.anim.start()

    def stop(self):
        self.anim.stop()
        self.effect.setOpacity(1.0)