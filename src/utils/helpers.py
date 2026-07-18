"""
src/utils/helpers.py

Shared utilities for the Humam Architecture GUI:

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

import subprocess
import sys
import threading
from dataclasses import dataclass

from PySide6.QtCore import (
    QEasingCurve, QObject, QPoint, QPropertyAnimation, QTimer, Qt, Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QLabel, QWidget


# ============================================================
#  TASK RESULT
# ============================================================
@dataclass
class TaskResult:
    success: bool
    message: str


# ============================================================
#  BACKGROUND POWERSHELL WORKER (runs on a QThread)
# ============================================================
class PowerShellTask(QObject):
    """
    Executes `core.ps1 -Task <name>` in a hidden, non-blocking subprocess
    and reports the outcome via signals. Must be moved to a QThread by the
    caller - never call `run()` directly on the GUI thread.
    """

    finished = Signal(TaskResult)   # backend returned a SUCCESS|... or ERROR|... line
    failed = Signal(str)            # timeout, missing powershell.exe, or other exception
    output = Signal(str)            # one line of raw stdout, emitted as core.ps1 prints it

    def __init__(self, ps1_path: str, task_name: str, timeout: int = 120,
                 app_ids: list[str] | None = None, dry_run: bool = False):
        super().__init__()
        self.ps1_path = ps1_path
        self.task_name = task_name
        self.timeout = timeout
        self.app_ids = app_ids or []
        # dry_run=True appends -WhatIf: the backend simulates the task and
        # reports "[WHATIF] ..." lines / a "[DRY-RUN]" result instead of
        # mutating the system. Same SUCCESS|/ERROR| contract either way.
        self.dry_run = dry_run

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
            if self.dry_run:
                cmd += " -WhatIf"

            popen_kwargs = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
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

            # Blocking line reads can't be interrupted by a wall-clock check
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

            lines: list[str] = []
            for raw_line in process.stdout:
                line = raw_line.rstrip("\r\n")
                lines.append(line)
                if line:
                    self.output.emit(line)
            process.wait()
            timeout_timer.cancel()

            if timed_out.is_set():
                self.failed.emit(f"Task timed out after {self.timeout}s.")
                return

            output = "\n".join(lines).strip()

            # Contract with core.ps1: the LAST line of output is either
            #   SUCCESS|Human readable message
            #   ERROR|Human readable message
            last_line = output.splitlines()[-1] if output else ""

            if last_line.startswith("SUCCESS"):
                msg = last_line.split("|", 1)[1].strip() if "|" in last_line else "Task completed."
                self.finished.emit(TaskResult(True, msg))
            elif last_line.startswith("ERROR"):
                msg = last_line.split("|", 1)[1].strip() if "|" in last_line else "Task failed."
                self.finished.emit(TaskResult(False, msg))
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
#  TOAST NOTIFICATION (slide-in, auto-hide)
# ============================================================
class Toast(QWidget):
    """A single glass toast. Positioned by ToastManager; do not use directly."""

    ACCENTS = {"success": "#64ffda", "error": "#ff6b6b", "info": "#00d4ff"}
    ICONS = {"success": "✅", "error": "❌", "info": "ℹ️"}

    def __init__(self, parent: QWidget, kind: str, message: str, duration_ms: int = 5000):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(320)
        accent = self.ACCENTS.get(kind, "#00d4ff")
        icon = self.ICONS.get(kind, "⚡")

        self.setStyleSheet(f"""
            Toast {{
                background-color: rgba(20, 24, 38, 0.92);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-left: 3px solid {accent};
                border-radius: 14px;
            }}
        """)

        label = QLabel(f"{icon}  {message}", self)
        label.setWordWrap(True)
        label.setStyleSheet("color: #e6f1ff; font-size: 13px; font-weight: 500; background: transparent; border: none;")
        label.setContentsMargins(16, 12, 16, 12)
        label.setGeometry(0, 0, 320, 0)
        label.adjustSize()
        label.setFixedWidth(320)

        self.setFixedHeight(max(56, label.sizeHint().height() + 24))
        label.setGeometry(0, 0, 320, self.height())

        # Fade in via opacity effect
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(250)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._slide_anim = QPropertyAnimation(self, b"pos", self)
        self._slide_anim.setDuration(300)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._duration_ms = duration_ms
        self._closing = False

    def slide_in(self, target_pos: QPoint):
        start_pos = QPoint(target_pos.x() + 60, target_pos.y())
        self.move(start_pos)
        self.show()
        self._slide_anim.setStartValue(start_pos)
        self._slide_anim.setEndValue(target_pos)
        self._slide_anim.start()
        self._fade_anim.start()
        QTimer.singleShot(self._duration_ms, self.dismiss)

    def slide_to(self, target_pos: QPoint):
        """Re-position an already-visible toast (e.g. when one above it closes)."""
        self._slide_anim.stop()
        self._slide_anim.setStartValue(self.pos())
        self._slide_anim.setEndValue(target_pos)
        self._slide_anim.start()

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        fade_out = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        fade_out.setDuration(200)
        fade_out.setStartValue(1.0)
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
    """
    Stacks Toasts in the top-right corner of `container`, newest on top,
    matching the VS Code / macOS notification pattern.
    """

    MARGIN = 20
    SPACING = 10

    def __init__(self, container: QWidget):
        super().__init__(container)
        self.container = container
        self._toasts: list[Toast] = []

    def show(self, kind: str, message: str, duration_ms: int = 5000):
        toast = Toast(self.container, kind, message, duration_ms)
        toast.setProperty("manager", self)
        self._toasts.append(toast)
        target = self._position_for_index(len(self._toasts) - 1, toast.height())
        toast.slide_in(target)
        return toast

    def _position_for_index(self, index: int, height: int) -> QPoint:
        x = self.container.width() - 320 - self.MARGIN
        y = self.MARGIN
        for t in self._toasts[:index]:
            y += t.height() + self.SPACING
        return QPoint(x, y)

    def _on_toast_closed(self, closed: Toast):
        if closed in self._toasts:
            self._toasts.remove(closed)
        self.reposition()

    def reposition(self):
        y = self.MARGIN
        x = self.container.width() - 320 - self.MARGIN
        for t in self._toasts:
            t.slide_to(QPoint(x, y))
            y += t.height() + self.SPACING


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

    def __init__(self, widget: QWidget, color: str = "#00d4ff", max_blur: float = 25.0):
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