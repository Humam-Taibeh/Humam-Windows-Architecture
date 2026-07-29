"""
"Is the engine busy?" has exactly one answer (v10.3).

THE BUG THIS FILE PINS
    Four places asked whether something was running — the close guard, the
    elevation relaunch, request_task, and the playbook launcher — and each
    of them inspected `self._thread`. A PlaybookRunner owns its OWN
    QThread, so all four answered False during the longest and most
    destructive operation Pulse performs: a playbook applying a machine
    baseline step by step.

    Consequences, all reachable from the UI:
      - closing the window mid-playbook skipped CloseConfirmDialog
        entirely and killed the run without asking;
      - "Run as Administrator" offered to quit and relaunch mid-run;
      - Escape or a click on the scrim dismissed the run dialog while the
        runner carried on mutating the machine with its progress view gone.

    These tests assert the predicate, not the four call sites' wording, so
    a fifth caller that forgets the playbook is a failing test rather than
    a new instance of the same bug.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog


class _FakeRunner:
    """A PlaybookRunner as the window sees it: something with a playbook
    and a cancel(). A real one would spawn PowerShell per step."""

    def __init__(self, name="Post-Install Clean"):
        self.playbook = type("_P", (), {"name": name})()
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _FakeDialog:
    def __init__(self):
        self.force_closed = False

    def force_close(self):
        self.force_closed = True


@pytest.fixture
def mid_playbook(window):
    """`window` with a playbook in flight and no single task running."""
    original = (window._playbook_runner, window._playbook_dialog, window._thread)
    runner, dialog = _FakeRunner(), _FakeDialog()
    window._playbook_runner = runner
    window._playbook_dialog = dialog
    window._thread = None
    yield window, runner, dialog
    (window._playbook_runner, window._playbook_dialog,
     window._thread) = original


class TestPredicate:
    def test_idle_window_is_not_busy(self, window):
        assert not window._busy()

    def test_a_running_playbook_counts_as_busy(self, mid_playbook):
        window, _runner, _dialog = mid_playbook
        assert window._playbook_is_running()
        assert window._busy(), (
            "the playbook was invisible to the busy check — this is the "
            "exact v10.2 gap")

    def test_task_and_playbook_are_asked_the_same_way(self, mid_playbook):
        """_task_is_running stays narrow (it guards the one-at-a-time task
        slot); _busy is the one every user-facing decision uses."""
        window, _runner, _dialog = mid_playbook
        assert not window._task_is_running()
        assert window._busy()


class TestCloseGuard:
    def test_closing_mid_playbook_asks_first(self, mid_playbook, monkeypatch):
        window, _runner, _dialog = mid_playbook
        asked = []
        monkeypatch.setattr(window, "_exec_dialog",
                            lambda d: asked.append(d) or QDialog.DialogCode.Rejected)
        event = QCloseEvent()
        window.closeEvent(event)
        assert asked, "the window closed mid-playbook without confirming"
        assert not event.isAccepted()

    def test_declining_leaves_the_playbook_running(self, mid_playbook, monkeypatch):
        window, runner, dialog = mid_playbook
        monkeypatch.setattr(window, "_exec_dialog",
                            lambda _d: QDialog.DialogCode.Rejected)
        window.closeEvent(QCloseEvent())
        assert not runner.cancelled, "'Keep Running' cancelled the playbook anyway"
        assert not dialog.force_closed

    def test_accepting_cancels_the_run_and_releases_the_dialog(
            self, mid_playbook, monkeypatch):
        window, runner, dialog = mid_playbook
        monkeypatch.setattr(window, "_exec_dialog",
                            lambda _d: QDialog.DialogCode.Accepted)
        event = QCloseEvent()
        window.closeEvent(event)
        assert runner.cancelled, "the playbook kept running after the app closed"
        assert dialog.force_closed, (
            "the run dialog's exec() loop was left up, outliving the window "
            "it is parented to")
        assert event.isAccepted()

    def test_the_prompt_names_the_playbook(self, mid_playbook, monkeypatch):
        """A confirmation that says nothing about what it will interrupt
        cannot be answered well."""
        window, runner, _dialog = mid_playbook
        seen = {}

        def _capture(dialog):
            seen["text"] = _dialog_text(dialog)
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(window, "_exec_dialog", _capture)
        window.closeEvent(QCloseEvent())
        assert runner.playbook.name in seen.get("text", ""), (
            "the close prompt did not mention the playbook it would stop")


def _dialog_text(dialog) -> str:
    from PySide6.QtWidgets import QLabel
    return " ".join(label.text() for label in dialog.findChildren(QLabel))


class TestElevationGuard:
    def test_relaunch_is_refused_mid_playbook(self, mid_playbook, monkeypatch):
        """Relaunching quits this process, which kills the run. The guard
        has to fire BEFORE the UAC prompt, not after."""
        window, _runner, _dialog = mid_playbook
        toasts: list[tuple] = []
        monkeypatch.setattr(window.toasts, "show", lambda *a, **k: toasts.append(a))
        # Anything past the guard would schedule this instance's exit.
        monkeypatch.setattr(
            "PySide6.QtCore.QTimer.singleShot",
            lambda *a, **k: pytest.fail("elevation proceeded mid-playbook"))
        window._relaunch_as_admin()
        assert toasts, "the refusal was silent — the button appeared to do nothing"


class TestRunDialogLock:
    """Disabling the Close BUTTON was never enough: PulseDialog dismisses
    on Escape (QDialog's default) and on a scrim click, and the native
    caption-close path rejects every open dialog. Any of those detached
    the dialog from a runner that kept going."""

    @pytest.fixture
    def dialog(self, window, qapp):
        from frontend.playbooks import Playbook, PlaybookStep
        from frontend.widgets import PlaybookDialog
        book = Playbook(id="t", name="Test Book", description="d", icon="📘",
                        steps=(PlaybookStep(task="SystemInfo",
                                            item={"title": "System Info"}),))
        d = PlaybookDialog(window, [book], [], window.theme.t, True)
        yield d
        d.deleteLater()

    @staticmethod
    def _dismissals(dialog) -> list:
        """Spy on the `rejected` signal.

        NOT dialog.result(): QDialog's result defaults to 0, which IS
        DialogCode.Rejected, so asserting on it would pass for a dialog
        that was never dismissed at all — the exact false-negative this
        test exists to avoid.
        """
        seen: list[bool] = []
        dialog.rejected.connect(lambda: seen.append(True))
        return seen

    def test_reject_is_refused_while_running(self, dialog):
        seen = self._dismissals(dialog)
        dialog.enter_run_mode(dry_run=False)
        dialog.reject()
        assert not seen, "Escape / scrim click dismissed a live playbook run"

    def test_the_refusal_explains_itself(self, dialog):
        dialog.enter_run_mode(dry_run=False)
        dialog.reject()
        assert "still running" in _dialog_text(dialog).lower(), (
            "the dialog silently ignored Escape, which reads as a freeze")

    def test_reject_works_again_once_done(self, dialog):
        seen = self._dismissals(dialog)
        dialog.enter_run_mode(dry_run=False)
        dialog.enter_done_mode()
        dialog.reject()
        assert seen, "the dialog stayed locked after the run finished"

    def test_force_close_overrides_the_lock(self, dialog):
        """The one sanctioned override — app shutdown, runner already
        cancelled."""
        seen = self._dismissals(dialog)
        dialog.enter_run_mode(dry_run=False)
        dialog.force_close()
        assert seen, "force_close did not release the run lock"


class TestTaskGuard:
    def test_request_task_refuses_mid_playbook(self, mid_playbook, monkeypatch):
        """Two engines racing on overlapping registry and service state is
        precisely what the one-at-a-time rule exists to prevent."""
        window, _runner, _dialog = mid_playbook
        started = []
        monkeypatch.setattr(window, "_start_task",
                            lambda *a, **k: started.append(a))
        monkeypatch.setattr(window.toasts, "show", lambda *a, **k: None)
        window.request_task({"task": "SystemInfo", "title": "System Info"}, None)
        assert not started, "a task started on top of a running playbook"
