"""
Job Object kill guarantee (v10.2).

WHAT THIS REPLACES
    Cancelling a task used to be `taskkill /T /F` on powershell.exe's PID.
    /T walks the parent-child tree as it stands at that instant, so it
    misses anything that reparented away first — winget's elevation
    broker, an MSI hand-off to the machine-wide msiexec service. The
    orphan then survives AND keeps the inherited stdout pipe open, so the
    reader never sees EOF and the "stopped" task hangs until its watchdog
    fires.

    test_taskkill_alone_leaks_an_orphan below demonstrates the old failure
    directly rather than describing it: it orphans a process and shows
    taskkill leaving it running.

WHAT MUST NOT REGRESS
    Several tasks END by launching something for the user on purpose —
    CleanCache starts cleanmgr.exe and says "follow its on-screen
    prompts", ClassicContextMenu restarts explorer.exe (the desktop
    shell). A job armed with KILL_ON_JOB_CLOSE kills those the instant the
    handle closes, so the success path disarms first. That is the single
    most dangerous property in this feature and it gets its own test.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from conftest import WINDOWS_ONLY
from utils.helpers import ProcessJob

pytestmark = WINDOWS_ONLY

_CNW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# A parent that spawns a detached grandchild and then EXITS, leaving the
# grandchild orphaned — the shape of the winget/msiexec hand-off.
_ORPHANING_PARENT = (
    "$p = Start-Process powershell "
    "-ArgumentList '-NoProfile','-Command','Start-Sleep 90' "
    "-PassThru -WindowStyle Hidden; Write-Output $p.Id; exit"
)


def _alive(pid: int) -> bool:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) "
         '{"YES"} else {"NO"}'],
        capture_output=True, creationflags=_CNW, timeout=60)
    return result.stdout.decode(errors="replace").strip() == "YES"


def _reap(pid: int):
    subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                   capture_output=True, creationflags=_CNW)


@pytest.fixture
def orphan_maker():
    """Spawns the parent, returns (job, parent_pid, grandchild_pid) once the
    parent has exited and the grandchild is genuinely orphaned. Always
    reaps the grandchild, whatever the test did."""
    spawned: list[int] = []

    def make(use_job: bool = True):
        job = ProcessJob() if use_job else None
        process = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", _ORPHANING_PARENT],
            stdout=subprocess.PIPE, creationflags=_CNW)
        if job is not None:
            job.assign(process)
        grandchild = int(process.stdout.readline().decode().strip())
        spawned.append(grandchild)
        process.wait()               # the parent is now gone
        time.sleep(0.6)
        return job, process.pid, grandchild

    yield make
    for pid in spawned:
        _reap(pid)


class TestJobObject:
    def test_a_job_can_be_created(self):
        job = ProcessJob()
        try:
            assert job.available, (
                "no Job Object could be created — the kill guarantee is "
                "silently inactive and cancellation is back to taskkill only")
        finally:
            job.close()

    def test_close_is_idempotent(self):
        job = ProcessJob()
        job.close()
        job.close()             # must not raise
        assert not job.available

    def test_methods_are_safe_after_close(self):
        """Every method is called from teardown paths that can run in any
        order; none may raise on an already-released handle."""
        job = ProcessJob()
        job.close()
        job.terminate()
        job.detach()
        job.assign(None)

    def test_assign_rejects_a_missing_process(self):
        job = ProcessJob()
        try:
            assert job.assign(None) is False
        finally:
            job.close()


class TestKillGuarantee:
    def test_taskkill_alone_leaks_an_orphan(self, orphan_maker):
        """The defect this feature exists to fix, demonstrated rather than
        asserted from memory. If this ever starts failing, Windows changed
        how /T resolves an orphan and the rationale needs revisiting."""
        _job, parent_pid, grandchild = orphan_maker(use_job=False)
        assert _alive(grandchild), "the fixture failed to orphan a process"

        subprocess.run(["taskkill", "/T", "/F", "/PID", str(parent_pid)],
                       capture_output=True, creationflags=_CNW)
        time.sleep(0.8)
        assert _alive(grandchild), (
            "taskkill /T reached the orphan after all — if this is now "
            "reliable, the Job Object is redundant")

    def test_the_job_kills_an_orphan_taskkill_would_miss(self, orphan_maker):
        job, _parent_pid, grandchild = orphan_maker(use_job=True)
        try:
            assert _alive(grandchild)
            job.terminate()
            time.sleep(0.8)
            assert not _alive(grandchild), (
                "the orphan survived TerminateJobObject — the kill "
                "guarantee is not holding")
        finally:
            job.close()

    def test_closing_an_armed_job_kills_its_members(self, orphan_maker):
        """KILL_ON_JOB_CLOSE is the safety net for the case nothing can
        handle explicitly: the GUI process dying outright. The OS closes
        our handles and the leftover tree must go with them."""
        job, _parent_pid, grandchild = orphan_maker(use_job=True)
        assert _alive(grandchild)
        job.close()              # no terminate() — the close itself must kill
        time.sleep(1.0)
        assert not _alive(grandchild), (
            "closing an armed job left its members running — the "
            "crash/abandon safety net is not armed")


class TestDetachProtectsUserFacingApps:
    """THE dangerous property.

    CleanCache launches cleanmgr.exe and reports success immediately;
    ClassicContextMenu restarts explorer.exe. If the success path closed
    an armed job, Disk Cleanup would vanish as it appeared and the user
    would lose their desktop shell.
    """

    def test_detached_job_leaves_survivors_alone(self, orphan_maker):
        job, _parent_pid, grandchild = orphan_maker(use_job=True)
        assert _alive(grandchild)

        job.detach()             # what the success path does
        job.close()
        time.sleep(1.0)
        assert _alive(grandchild), (
            "a process the task deliberately launched for the user was "
            "killed when the task SUCCEEDED — this is the cleanmgr.exe / "
            "explorer.exe regression; the success path must disarm "
            "kill-on-close before releasing the handle")

    def test_without_detach_the_same_process_dies(self, orphan_maker):
        """Pins that the test above is actually testing detach() and not
        passing because the job was inert."""
        job, _parent_pid, grandchild = orphan_maker(use_job=True)
        assert _alive(grandchild)

        job.close()              # armed
        time.sleep(1.0)
        assert not _alive(grandchild)


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows degradation")
def test_degrades_quietly_off_windows():
    """A dev box without job objects must fall back to taskkill, not fail
    the task the user asked for."""
    job = ProcessJob()
    assert not job.available
    job.assign(None)
    job.terminate()
    job.detach()
    job.close()


class TestWorkerIntegration:
    """The wiring, not just the primitive."""

    def test_worker_starts_with_no_job(self):
        from utils.helpers import PowerShellTask
        worker = PowerShellTask("nonexistent.ps1", "Noop", timeout=5)
        assert worker._job is None

    def test_kill_process_tree_survives_a_missing_job(self):
        """_kill_process_tree runs from the cancel path, the watchdog and
        the finally block — including before a job was ever stored."""
        from utils.helpers import PowerShellTask
        worker = PowerShellTask("nonexistent.ps1", "Noop", timeout=5)
        process = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", "Start-Sleep 30"],
            stdout=subprocess.PIPE, creationflags=_CNW)
        try:
            worker._kill_process_tree(process)   # _job is still None
            time.sleep(0.5)
            assert process.poll() is not None
        finally:
            if process.poll() is None:
                _reap(process.pid)

    def test_a_cancelled_task_reports_cancelled_and_leaves_nothing(self, qapp):
        """End-to-end: a real backend task, stopped mid-flight."""
        import os
        from utils.helpers import PowerShellTask

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ps1 = os.path.join(root, "src/backend/core.ps1")

        worker = PowerShellTask(ps1, "GetTweakState", timeout=90)
        seen: list[str] = []
        worker.cancelled.connect(lambda: seen.append("cancelled"))
        worker.finished.connect(lambda _r: seen.append("finished"))
        worker.failed.connect(lambda _m: seen.append("failed"))

        import threading
        runner = threading.Thread(target=worker.run)
        runner.start()
        # let it get as far as spawning powershell, then stop it
        for _ in range(100):
            with worker._proc_lock:
                started = worker._process is not None
            if started:
                break
            time.sleep(0.02)
        worker.cancel()
        runner.join(timeout=60)
        qapp.processEvents()

        assert not runner.is_alive(), (
            "the reader never unblocked after cancel — a surviving child is "
            "still holding the stdout pipe open")
        assert seen == ["cancelled"], f"expected one cancelled signal, got {seen}"
        assert worker._job is None, "the job handle was not released"
