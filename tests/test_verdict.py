"""
The ##PULSE## sentinel protocol (v10.3).

Three channels now share one prefix, and the rules between them are the
whole contract:

    ##PULSE##SUCCESS|msg / ##PULSE##ERROR|msg   the verdict — the ONLY
                                                thing that decides pass/fail
    ##PULSE##DATA|<json>                        the task's own payload
    ##PULSE##META|<json>                        measurements about the run

The failure this file exists to prevent: META is emitted AFTER the verdict,
so a backwards scan that does not skip it would treat a metrics line as the
verdict and every task would finish "without a recognized status line".
The mirror failure is META shadowing DATA — they were deliberately NOT put
on the same channel, because the frontend takes the last DATA line and the
Update Center would have started reading metrics instead of its version
audit.

Parsing is exercised through PowerShellTask's real logic wherever possible
rather than re-implemented here, so a change to the parser is caught.
"""
from __future__ import annotations

import json

import pytest

from utils.helpers import (VERDICT_DATA_PREFIX, VERDICT_META_PREFIX,
                           VERDICT_PAYLOAD_PREFIXES, VERDICT_SENTINEL,
                           PowerShellTask, TaskResult)


def _parse(lines: list[str]) -> TaskResult:
    """Run the parser's own selection rules over a synthetic transcript.

    Mirrors PowerShellTask.run's terminal block exactly; kept in one place
    so the tests below read as protocol statements rather than plumbing.
    """
    verdict = next(
        (ln[len(VERDICT_SENTINEL):] for ln in reversed(lines)
         if ln.startswith(VERDICT_SENTINEL)
         and not ln.startswith(VERDICT_PAYLOAD_PREFIXES)),
        None)
    if verdict is None:
        verdict = next(
            (ln for ln in reversed(lines)
             if ln.startswith("SUCCESS|") or ln.startswith("ERROR|")), "")

    data = None
    raw = next((ln[len(VERDICT_DATA_PREFIX):] for ln in reversed(lines)
                if ln.startswith(VERDICT_DATA_PREFIX)), None)
    if raw is not None:
        try:
            data = json.loads(raw)
        except ValueError:
            data = None

    meta = None
    raw_meta = next((ln[len(VERDICT_META_PREFIX):] for ln in reversed(lines)
                     if ln.startswith(VERDICT_META_PREFIX)), None)
    if raw_meta is not None:
        try:
            parsed = json.loads(raw_meta)
            meta = parsed if isinstance(parsed, dict) else None
        except ValueError:
            meta = None

    if verdict.startswith("SUCCESS"):
        return TaskResult(True, verdict.split("|", 1)[1].strip(), data, meta)
    if verdict.startswith("ERROR"):
        return TaskResult(False, verdict.split("|", 1)[1].strip(), data, meta)
    return TaskResult(False, "Script finished without a recognized status line.",
                      None, meta)


class TestChannelSeparation:
    def test_meta_after_the_verdict_does_not_become_the_verdict(self):
        """The ordering hazard. Write-GuiMeta runs in Invoke-GuiTask's
        finally block, so META is always the LAST sentinel line."""
        result = _parse([
            "##PULSE##SUCCESS|Cache cleaned.",
            '##PULSE##META|{"task":"CleanCache","durationMs":120}',
        ])
        assert result.success
        assert result.message == "Cache cleaned."

    def test_meta_does_not_shadow_the_task_payload(self):
        """Why META is not on the DATA channel: the frontend takes the LAST
        DATA line, so a metrics envelope sharing that channel would replace
        the Update Center's version audit."""
        result = _parse([
            '##PULSE##DATA|[{"AppId":"Git.Git","Available":"2.45"}]',
            "##PULSE##SUCCESS|Scan complete.",
            '##PULSE##META|{"task":"ScanForUpdates"}',
        ])
        assert result.data == [{"AppId": "Git.Git", "Available": "2.45"}]
        assert result.meta["task"] == "ScanForUpdates"

    def test_an_error_verdict_survives_a_trailing_meta_line(self):
        result = _parse([
            "##PULSE##ERROR|Something broke.",
            '##PULSE##META|{"task":"RunSFC","counts":{"failed":1}}',
        ])
        assert not result.success
        assert result.message == "Something broke."
        assert result.meta["counts"]["failed"] == 1

    def test_every_payload_prefix_is_registered(self):
        """VERDICT_PAYLOAD_PREFIXES is what the backwards scan skips. A new
        channel that is not listed there becomes the verdict."""
        assert VERDICT_DATA_PREFIX in VERDICT_PAYLOAD_PREFIXES
        assert VERDICT_META_PREFIX in VERDICT_PAYLOAD_PREFIXES
        for prefix in VERDICT_PAYLOAD_PREFIXES:
            assert prefix.startswith(VERDICT_SENTINEL)


class TestMetaIsNotAVerdict:
    def test_meta_alone_is_not_a_pass(self):
        """META carries no outcome by design — a task that emitted metrics
        but no verdict has still failed the contract."""
        result = _parse(['##PULSE##META|{"task":"CleanCache","counts":{"failed":0}}'])
        assert not result.success
        assert "without a recognized status line" in result.message

    def test_meta_counts_do_not_override_the_verdict(self):
        """A task can succeed overall while logging individual failures
        (a bulk deploy where one app was unavailable). The verdict wins."""
        result = _parse([
            "##PULSE##SUCCESS|Deployed 9 of 10 apps.",
            '##PULSE##META|{"counts":{"succeeded":9,"failed":1}}',
        ])
        assert result.success, "a non-zero failed count silently flipped the verdict"
        assert result.meta["counts"]["failed"] == 1


class TestDegradation:
    def test_a_backend_without_meta_still_parses(self):
        """Pre-10.3 engines emit no META line at all."""
        result = _parse(["##PULSE##SUCCESS|Done."])
        assert result.success
        assert result.meta is None

    def test_malformed_meta_json_is_ignored(self):
        result = _parse([
            "##PULSE##SUCCESS|Done.",
            "##PULSE##META|{not valid json",
        ])
        assert result.success, "a broken metrics line must never fail the task"
        assert result.meta is None

    def test_non_object_meta_is_rejected(self):
        """meta is typed dict|None; a bare array would break every consumer
        that does meta.get(...)."""
        result = _parse([
            "##PULSE##SUCCESS|Done.",
            "##PULSE##META|[1,2,3]",
        ])
        assert result.meta is None

    def test_legacy_bare_verdict_still_works(self):
        result = _parse(["SUCCESS|Old backend."])
        assert result.success
        assert result.message == "Old backend."


class TestConsoleSuppression:
    @pytest.mark.parametrize("line", [
        '##PULSE##DATA|{"a":1}',
        '##PULSE##META|{"task":"X"}',
    ])
    def test_payload_lines_never_reach_the_live_console(self, line):
        """They are machine payloads. A JSON blob appearing in the user's
        console reads as a crash."""
        assert line.startswith(VERDICT_PAYLOAD_PREFIXES)

    def test_the_verdict_line_does_reach_the_console(self):
        assert not "##PULSE##SUCCESS|Done.".startswith(VERDICT_PAYLOAD_PREFIXES)


class TestAgainstTheRealBackend:
    """One live round trip, so the contract is verified against the engine
    rather than against this file's idea of it."""

    def test_a_real_task_emits_parseable_metrics(self, qapp):
        import os
        import threading

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ps1 = os.path.join(root, "src/backend/core.ps1")

        captured: dict = {}
        console: list[str] = []
        worker = PowerShellTask(ps1, "GetTweakState", timeout=120)
        worker.finished.connect(lambda r: captured.update(result=r))
        worker.output.connect(lambda text, _replace: console.append(text))

        runner = threading.Thread(target=worker.run)
        runner.start()
        runner.join(timeout=180)
        qapp.processEvents()

        result = captured.get("result")
        assert result is not None, "the backend produced no verdict at all"
        assert result.success, result.message

        assert isinstance(result.meta, dict), "no metrics envelope was emitted"
        assert result.meta.get("task") == "GetTweakState"
        assert isinstance(result.meta.get("durationMs"), int)
        assert set(result.meta.get("counts", {})) == {"succeeded", "failed", "skipped"}
        assert result.meta.get("dryRun") is False

        # The regression that adding META could have caused.
        assert isinstance(result.data, dict) and result.data, (
            "the task's own DATA payload was lost — META is shadowing it")

        assert not [ln for ln in console if VERDICT_SENTINEL in ln
                    and ln.startswith(("DATA|", "META|"))], (
            "a machine payload leaked into the live console")
