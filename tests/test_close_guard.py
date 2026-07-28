"""
Confirm-on-close while a task is running (v10.2).

Closing mid-task used to cancel silently. For this app that is the wrong
default: the running operation may be halfway through an MSI install, a
driver export or an Edge purge, and "stopped halfway" is a materially
worse state than either finished or never started.

The interesting cases are the NEGATIVE ones — declining must leave the
window open AND the task untouched — because a half-honoured "no" is
worse than no prompt at all: the user would keep a window whose task had
already been killed.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog

from frontend.widgets import CloseConfirmDialog


class _FakeThread:
    """Stands in for the QThread a live task owns. Constructing a real one
    would mean spawning real PowerShell for a UI-decision test."""

    def __init__(self, running: bool):
        self._running = running
        self.waited = False

    def isRunning(self):        # noqa: N802 - Qt casing
        return self._running

    def wait(self, _ms=0):
        self.waited = True
        return True


class _FakeWorker:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def armed(window, monkeypatch):
    """`window` with a fake running task installed, restored afterwards."""
    original = (window._thread, window._worker, window._running_item)
    thread, worker = _FakeThread(True), _FakeWorker()
    window._thread = thread
    window._worker = worker
    window._running_item = {"title": "Install Essential Apps", "task": "InstallEssentialApps"}
    yield window, thread, worker
    window._thread, window._worker, window._running_item = original


def _close(window) -> QCloseEvent:
    event = QCloseEvent()
    window.closeEvent(event)
    return event


class TestPromptAppears:
    def test_declining_keeps_the_window_open(self, armed, monkeypatch):
        window, thread, worker = armed
        monkeypatch.setattr(window, "_exec_dialog",
                            lambda _d: QDialog.DialogCode.Rejected)
        event = _close(window)
        assert not event.isAccepted(), "the window closed despite 'Keep Running'"

    def test_declining_does_not_touch_the_task(self, armed, monkeypatch):
        """The half-honoured 'no' — window stays, task dies anyway."""
        window, thread, worker = armed
        monkeypatch.setattr(window, "_exec_dialog",
                            lambda _d: QDialog.DialogCode.Rejected)
        _close(window)
        assert not worker.cancelled, (
            "the running task was cancelled even though the user chose to "
            "keep it running")
        assert not thread.waited

    def test_accepting_cancels_the_task(self, armed, monkeypatch):
        window, thread, worker = armed
        monkeypatch.setattr(window, "_exec_dialog",
                            lambda _d: QDialog.DialogCode.Accepted)
        _close(window)
        assert worker.cancelled, "'Stop & Close' did not stop the task"
        assert thread.waited, (
            "the window did not wait for the process-tree kill to land — "
            "children can outlive the GUI")

    def test_the_prompt_names_the_running_task(self, armed, monkeypatch):
        """A bare 'a task is running' is not actionable; the user needs to
        know WHICH one before deciding to kill it."""
        window, _thread, _worker = armed
        seen = {}

        def capture(dialog):
            seen["dialog"] = dialog
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(window, "_exec_dialog", capture)
        _close(window)

        from PySide6.QtWidgets import QLabel

        dialog = seen.get("dialog")
        assert isinstance(dialog, CloseConfirmDialog)
        labels = " ".join(lbl.text() for lbl in dialog.findChildren(QLabel))
        assert "Install Essential Apps" in labels, (
            f"the prompt did not name the running task; labels were: {labels!r}")

    def test_declining_does_not_persist_geometry(self, armed, monkeypatch):
        """Geometry belongs to a window the user actually closed."""
        window, _thread, _worker = armed
        monkeypatch.setattr(window, "_exec_dialog",
                            lambda _d: QDialog.DialogCode.Rejected)
        writes = []
        from utils import prefs
        monkeypatch.setattr(prefs, "set_window_geometry",
                            lambda blob: writes.append(blob))
        _close(window)
        assert not writes, "geometry was saved for a close that never happened"


class TestNoPromptWhenIdle:
    def test_closing_while_idle_does_not_prompt(self, window, monkeypatch):
        original = (window._thread, window._worker)
        window._thread, window._worker = _FakeThread(False), _FakeWorker()
        prompted = []
        monkeypatch.setattr(window, "_exec_dialog",
                            lambda _d: prompted.append(_d) or QDialog.DialogCode.Rejected)
        try:
            event = _close(window)
            assert not prompted, (
                "closing an idle Pulse asked for confirmation — the prompt "
                "must only appear when something is actually running")
            assert event.isAccepted()
        finally:
            window._thread, window._worker = original

    def test_idle_close_still_persists_geometry(self, window, monkeypatch):
        original = (window._thread, window._worker)
        window._thread, window._worker = _FakeThread(False), _FakeWorker()
        writes = []
        from utils import prefs
        monkeypatch.setattr(prefs, "set_window_geometry",
                            lambda blob: writes.append(blob))
        try:
            _close(window)
            assert writes, "an ordinary close stopped saving window geometry"
        finally:
            window._thread, window._worker = original


class TestDialogItself:
    def test_the_safe_choice_is_the_default(self, window, qapp):
        """Enter on a reflexive Alt+F4 must not end a long install."""
        dialog = CloseConfirmDialog(window, window.theme.t, "Install Runtimes")
        try:
            assert dialog._keep_btn.isDefault(), (
                "'Stop & Close' is the default button — a stray Enter would "
                "kill the running task")
            assert not dialog._stop_btn.isDefault()
        finally:
            dialog.deleteLater()

    def test_the_stop_button_label_is_not_eaten_by_a_mnemonic(self, window, qapp):
        """A single '&' in Qt button text is a mnemonic marker: "Stop &
        Close" paints as "Stop _Close" with the C underlined, which reads
        as a broken label. Caught by screenshot, pinned here."""
        dialog = CloseConfirmDialog(window, window.theme.t, "Install Runtimes")
        try:
            raw = dialog._stop_btn.text()
            assert "&&" in raw or "&" not in raw, (
                f"button text {raw!r} contains an unescaped '&' — Qt will "
                "swallow it as a mnemonic and underline the next letter")
        finally:
            dialog.deleteLater()

    def test_escape_keeps_the_task_running(self, window, qapp):
        """PulseDialog rejects on Escape; reject must mean 'keep running'."""
        dialog = CloseConfirmDialog(window, window.theme.t, "Install Runtimes")
        try:
            dialog.reject()
            assert dialog.result() == QDialog.DialogCode.Rejected
        finally:
            dialog.deleteLater()

    def test_it_survives_an_unnamed_task(self, window, qapp):
        """_running_item can legitimately be None by the time the close
        lands (the task settled between the check and the prompt)."""
        dialog = CloseConfirmDialog(window, window.theme.t, "")
        try:
            from PySide6.QtWidgets import QLabel
            labels = " ".join(lbl.text() for lbl in dialog.findChildren(QLabel))
            assert "An operation" in labels
        finally:
            dialog.deleteLater()
