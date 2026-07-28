"""
Playbooks — validation and the sequential runner (v10.3).

A playbook runs unattended against a real machine, so the failure modes
that matter are the ones nobody is watching for:

  * a step naming a task that does not exist would fail at step N, having
    already mutated the box. Validation happens at LOAD time and refuses
    the whole playbook.
  * two steps overlapping would race on registry and service state the
    engine assumes it owns. The runner starts step N+1 only from step N's
    thread teardown, and test_steps_never_overlap asserts the observed
    ordering rather than trusting the code shape.
  * a required step failing and the run ploughing on regardless leaves a
    half-configured machine — worse than an obvious stop.
"""
from __future__ import annotations

import json
import os

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from frontend.playbooks import (Playbook, PlaybookError, PlaybookRunner,
                                load_playbooks, parse_playbook, playbook_dirs)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PS1 = os.path.join(_ROOT, "src/backend/core.ps1")


def _doc(**overrides) -> dict:
    doc = {
        "id": "unit-test",
        "name": "Unit Test",
        "description": "",
        "icon": "🧪",
        "steps": [{"task": "DarkMode"}],
    }
    doc.update(overrides)
    return doc


# ============================================================
#  VALIDATION
# ============================================================
class TestValidation:
    def test_a_valid_document_parses(self, qapp):
        playbook = parse_playbook(_doc())
        assert isinstance(playbook, Playbook)
        assert len(playbook) == 1
        assert playbook.steps[0].task == "DarkMode"

    def test_steps_resolve_against_the_live_catalog(self, qapp):
        """The step carries the real catalog item, so its timeout and
        confirm flags come from menu_structure and cannot drift."""
        step = parse_playbook(_doc()).steps[0]
        assert step.item.get("task") == "DarkMode"
        assert step.title == step.item["title"]

    def test_an_unknown_task_is_refused(self, qapp):
        with pytest.raises(PlaybookError, match="unknown task"):
            parse_playbook(_doc(steps=[{"task": "NoSuchTaskExists"}]))

    def test_a_local_action_is_refused(self, qapp):
        """@open_log opens a viewer — queueing it would block an unattended
        run waiting for a human."""
        with pytest.raises(PlaybookError, match="GUI-local"):
            parse_playbook(_doc(steps=[{"task": "@open_log"}]))

    @pytest.mark.parametrize("bad,match", [
        ({"id": ""}, "'id'"),
        ({"name": ""}, "'name'"),
        ({"steps": []}, "non-empty"),
        ({"steps": "nope"}, "non-empty"),
        ({"steps": [{"note": "no task"}]}, "no 'task'"),
        ({"steps": ["not an object"]}, "not an object"),
    ])
    def test_malformed_documents_are_refused(self, qapp, bad, match):
        with pytest.raises(PlaybookError, match=match):
            parse_playbook(_doc(**bad))

    def test_a_non_object_document_is_refused(self, qapp):
        with pytest.raises(PlaybookError):
            parse_playbook(["not", "a", "dict"])

    def test_the_error_names_the_file(self, qapp):
        with pytest.raises(PlaybookError, match="broken.json"):
            parse_playbook(_doc(steps=[{"task": "Nope"}]), source="/x/broken.json")

    def test_admin_requirement_is_derived_not_declared(self, qapp):
        """A playbook author must not be able to under-declare elevation —
        it is computed from the tasks themselves."""
        assert parse_playbook(_doc(steps=[{"task": "DarkMode"}])).needs_admin is False
        assert parse_playbook(_doc(steps=[{"task": "RunSFC"}])).needs_admin is True


# ============================================================
#  THE SHIPPED PLAYBOOKS
# ============================================================
class TestShippedPlaybooks:
    def test_they_all_load_without_errors(self, qapp):
        playbooks, errors = load_playbooks()
        assert not errors, f"shipped playbooks failed validation: {errors}"
        assert playbooks, f"no playbooks found in {playbook_dirs()}"

    def test_the_three_documented_presets_exist(self, qapp):
        playbooks, _ = load_playbooks()
        ids = {p.id for p in playbooks}
        assert {"gamer-rig", "privacy-hardening", "post-install-clean"} <= ids

    def test_every_shipped_playbook_opens_with_a_restore_point(self, qapp):
        """The safety property that makes 'run it and walk away' defensible:
        every mutation after step 1 is undoable."""
        playbooks, _ = load_playbooks()
        for playbook in playbooks:
            assert playbook.steps[0].task == "CreateRestorePoint", (
                f"{playbook.id} does not open with a restore point — a "
                "failed run partway through would not be undoable")

    def test_destructive_steps_are_marked_optional(self, qapp):
        """A step that removes data must not be able to halt the run by
        failing, and must be visibly flagged in the UI."""
        destructive = {"RemoveBloatware", "RemoveWindowsOld", "RemoveEdge",
                       "RemoveOneDrive"}
        playbooks, _ = load_playbooks()
        for playbook in playbooks:
            for step in playbook.steps:
                if step.task in destructive:
                    assert step.optional, (
                        f"{playbook.id}: destructive step {step.task} is "
                        "required — it would halt the whole playbook")


# ============================================================
#  LOADER RESILIENCE
# ============================================================
class TestLoaderResilience:
    def test_a_broken_file_does_not_take_the_others_down(self, qapp, tmp_path,
                                                         monkeypatch):
        good = tmp_path / "good.json"
        good.write_text(json.dumps(_doc(id="good", name="Good")), encoding="utf-8")
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "invalid.json").write_text(
            json.dumps(_doc(id="bad", name="Bad", steps=[{"task": "Nope"}])),
            encoding="utf-8")

        monkeypatch.setattr("frontend.playbooks.playbook_dirs",
                            lambda: [str(tmp_path)])
        playbooks, errors = load_playbooks()

        assert [p.id for p in playbooks] == ["good"]
        assert len(errors) == 2, errors

    def test_a_missing_directory_is_not_an_error(self, qapp, monkeypatch):
        monkeypatch.setattr("frontend.playbooks.playbook_dirs", lambda: [])
        playbooks, errors = load_playbooks()
        assert playbooks == [] and errors == []

    def test_non_json_files_are_ignored(self, qapp, tmp_path, monkeypatch):
        (tmp_path / "README.md").write_text("not a playbook", encoding="utf-8")
        (tmp_path / "ok.json").write_text(
            json.dumps(_doc(id="ok", name="Ok")), encoding="utf-8")
        monkeypatch.setattr("frontend.playbooks.playbook_dirs",
                            lambda: [str(tmp_path)])
        playbooks, errors = load_playbooks()
        assert [p.id for p in playbooks] == ["ok"] and not errors


# ============================================================
#  THE RUNNER  (drives the real engine, in dry-run)
# ============================================================
def _run(runner: PlaybookRunner, timeout_ms: int = 600_000):
    """Drive `runner` to completion on a nested event loop."""
    loop = QEventLoop()
    finished: dict = {}
    runner.finished.connect(lambda run: (finished.update(run=run), loop.quit()))
    QTimer.singleShot(0, runner.start)
    guard = QTimer()
    guard.setSingleShot(True)
    guard.timeout.connect(loop.quit)
    guard.start(timeout_ms)
    loop.exec()
    return finished.get("run")


@pytest.mark.native
class TestRunner:
    """Dry-run only: every step goes through -WhatIf, so the suite never
    mutates the machine it is running on."""

    def test_a_multi_step_playbook_completes(self, qapp):
        playbook = parse_playbook(_doc(steps=[
            {"task": "DarkMode"}, {"task": "DisableMouseAccel"},
        ]))
        run = _run(PlaybookRunner(_PS1, playbook, dry_run=True))
        assert run is not None, "the runner never emitted finished"
        assert run.complete, [r.outcome for r in run.results]
        assert run.succeeded == 2 and run.failed == 0

    def test_steps_never_overlap(self, qapp):
        """The serialisation guarantee. Two engines racing on registry and
        service state is exactly what a restore point cannot save you
        from, because both would be mid-write."""
        playbook = parse_playbook(_doc(steps=[
            {"task": "DarkMode"}, {"task": "DisableMouseAccel"},
            {"task": "DriveSpaceReport"},
        ]))
        runner = PlaybookRunner(_PS1, playbook, dry_run=True)
        events: list[tuple[str, int]] = []
        runner.step_started.connect(lambda i: events.append(("start", i)))
        runner.step_finished.connect(lambda i, _r: events.append(("done", i)))
        _run(runner)

        assert events == [("start", 0), ("done", 0),
                          ("start", 1), ("done", 1),
                          ("start", 2), ("done", 2)], events

    def test_dry_run_is_reported_by_every_step(self, qapp):
        """The -WhatIf flag must reach the engine, not just the UI label —
        verified through the v10.3 metrics envelope."""
        playbook = parse_playbook(_doc(steps=[{"task": "DarkMode"}]))
        run = _run(PlaybookRunner(_PS1, playbook, dry_run=True))
        assert run.dry_run
        assert run.results[0].meta is not None, "no metrics envelope came back"
        assert run.results[0].meta.get("dryRun") is True, (
            "the engine did not run this step as a dry run — a preview "
            "would have mutated the machine")

    def test_a_required_failure_halts_the_run(self, qapp):
        """CreateRestorePoint needs elevation; unelevated it returns an
        ERROR verdict, which must stop everything after it."""
        if os.environ.get("PULSE_TESTS_ELEVATED"):
            pytest.skip("needs an unelevated session to force the failure")
        playbook = parse_playbook(_doc(steps=[
            {"task": "CreateRestorePoint"}, {"task": "DarkMode"},
        ]))
        run = _run(PlaybookRunner(_PS1, playbook, dry_run=True))
        assert run.halted_on == 0, [r.outcome for r in run.results]
        assert len(run.results) == 1, (
            "the run continued past a failed required step — the machine "
            "would be left half configured")
        assert not run.complete

    def test_an_optional_failure_does_not_halt_the_run(self, qapp):
        if os.environ.get("PULSE_TESTS_ELEVATED"):
            pytest.skip("needs an unelevated session to force the failure")
        playbook = parse_playbook(_doc(steps=[
            {"task": "CreateRestorePoint", "optional": True},
            {"task": "DarkMode"},
        ]))
        run = _run(PlaybookRunner(_PS1, playbook, dry_run=True))
        assert run.halted_on is None
        assert len(run.results) == 2
        assert run.results[0].outcome == "error"
        assert run.results[1].outcome == "ok"

    def test_finished_is_emitted_exactly_once(self, qapp):
        """Several paths reach the end (last step, halt, cancel); a double
        emission would run the caller's summary twice."""
        playbook = parse_playbook(_doc(steps=[{"task": "DarkMode"}]))
        runner = PlaybookRunner(_PS1, playbook, dry_run=True)
        seen: list[object] = []
        runner.finished.connect(seen.append)
        _run(runner)
        assert len(seen) == 1, f"finished fired {len(seen)} times"
