"""
Health & Drift Report rendering (v10.3).

frontend.health_report is pure — dict in, string out — so the parts worth
testing are the judgement calls rather than the plumbing:

  * "unknown" must never be silently folded into "not applied". Doing so
    would invent drift on an unelevated session, where several values are
    genuinely unreadable, and send a technician chasing settings that were
    fine all along.
  * the HTML is a client deliverable, so it must be self-contained (no
    external assets to 404 later) and it must escape machine-derived
    strings — a GPU name containing an ampersand should not be able to
    produce a broken file.
  * findings() must stay quiet when there is nothing to say; a report that
    always lists something teaches people to ignore it.
"""
from __future__ import annotations

import json
import re

import pytest

from frontend.health_report import (LOW_DISK_PERCENT, STALE_RESTORE_DAYS,
                                    TWEAK_LABELS, findings, to_html, to_json,
                                    tweak_rows)


def _report(**overrides) -> dict:
    base = {
        "generatedAt": "2026-07-28T22:00:00",
        "hostname": "TEST-PC",
        "elevated": True,
        "system": {"os": "Windows 11 Pro", "build": 26200, "edition": "Professional",
                   "cpu": "Test CPU", "totalRAMGB": 32.0, "freeRAMGB": 20.0,
                   "powerPlan": "Balanced", "uptimeHours": 5.0, "psVersion": "5.1"},
        "drives": [{"name": "C", "totalGB": 900.0, "freeGB": 400.0, "percentFree": 44}],
        "restorePoint": {"available": True, "count": 3,
                         "newestDescription": "PULSE_AutoRestore", "newestAgeDays": 2.0},
        "startup": {"total": 15, "enabled": 6, "recommendedDisable": 0},
        # Baseline is a HEALTHY machine, and internally consistent: the
        # summary is derived from `tweaks` by the backend, so a fixture
        # where they disagree would test a state that cannot occur.
        # `unknown` is 0 here on purpose — an unreadable value is itself a
        # finding, so leaving one in the baseline would mean no test could
        # ever assert the "nothing to report" path.
        "tweaks": {"DarkMode": True, "GameMode": False},
        "tweakSummary": {"applied": 1, "notApplied": 1, "unknown": 0},
    }
    base.update(overrides)
    return base


#: The three-state set, for the tests that are specifically about how
#: unknown is classified.
_TRISTATE = {"DarkMode": True, "GameMode": False, "RemoveEdge": None}


class TestTweakRows:
    def test_the_three_states_are_kept_distinct(self):
        states = {task: state
                  for _label, state, task in tweak_rows(_report(tweaks=_TRISTATE))}
        assert states == {"DarkMode": "applied", "GameMode": "not-applied",
                          "RemoveEdge": "unknown"}

    def test_unknown_is_not_reported_as_drift(self):
        """The judgement call. None means 'could not read', not 'off'."""
        rows = tweak_rows(_report(tweaks={"RemoveEdge": None}))
        assert rows[0][1] == "unknown"
        assert rows[0][1] != "not-applied"

    def test_actionable_rows_sort_first(self):
        """A report is scanned, not read."""
        order = [state for _l, state, _t in tweak_rows(_report(tweaks=_TRISTATE))]
        assert order == ["not-applied", "unknown", "applied"]

    def test_an_unlabelled_task_still_appears(self):
        """A newly probed tweak must not vanish from the report until
        someone remembers to update the label table."""
        rows = tweak_rows(_report(tweaks={"BrandNewTweak": True}))
        assert rows == [("BrandNewTweak", "applied", "BrandNewTweak")]

    def test_missing_tweak_data_yields_no_rows(self):
        assert tweak_rows(_report(tweaks=None)) == []
        assert tweak_rows(_report(tweaks="not a dict")) == []

    def test_every_label_maps_a_real_probe_key(self):
        """Guards the label table against drifting from the probe."""
        import os
        import re as _re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        probe = open(os.path.join(root, "src/backend/modules/11-StateProbe.ps1"),
                     encoding="utf-8-sig").read()
        keys = set(_re.findall(r'\$state\["([A-Za-z0-9_]+)"\]\s*=', probe))
        unknown = sorted(set(TWEAK_LABELS) - keys)
        assert not unknown, f"labels for tasks the probe never reports: {unknown}"


class TestFindings:
    def test_a_healthy_machine_produces_none(self):
        """Silence is the useful answer. A report that always lists
        something trains people to skip the section."""
        assert findings(_report()) == []

    def test_a_full_disk_is_reported(self):
        report = _report(drives=[
            {"name": "C", "totalGB": 900.0, "freeGB": 20.0,
             "percentFree": LOW_DISK_PERCENT - 1}])
        assert any("only" in f and "C" in f for f in findings(report))

    def test_unavailable_system_restore_is_reported(self):
        report = _report(restorePoint={"available": False, "count": 0,
                                       "newestDescription": None,
                                       "newestAgeDays": None})
        assert any("System Restore" in f for f in findings(report))

    def test_zero_restore_points_is_reported(self):
        report = _report(restorePoint={"available": True, "count": 0,
                                       "newestDescription": None,
                                       "newestAgeDays": None})
        assert any("No System Restore points" in f for f in findings(report))

    def test_a_stale_restore_point_is_reported(self):
        report = _report(restorePoint={"available": True, "count": 1,
                                       "newestDescription": "old",
                                       "newestAgeDays": STALE_RESTORE_DAYS + 5})
        assert any("days old" in f for f in findings(report))

    def test_startup_bloat_is_reported(self):
        report = _report(startup={"total": 30, "enabled": 20,
                                  "recommendedDisable": 7})
        assert any("startup item" in f for f in findings(report))

    def test_unreadable_values_prompt_for_elevation(self):
        report = _report(tweakSummary={"applied": 1, "notApplied": 0, "unknown": 4})
        assert any("Administrator" in f for f in findings(report))

    def test_missing_sections_do_not_raise(self):
        """An older or partially-failed backend must still render."""
        assert isinstance(findings({}), list)
        assert isinstance(findings({"drives": None, "restorePoint": None,
                                    "startup": None, "tweakSummary": None}), list)


class TestJsonExport:
    def test_it_round_trips(self):
        report = _report()
        assert json.loads(to_json(report)) == report

    def test_it_is_diffable(self):
        """Its purpose is comparing two runs, so key order must be stable
        and the output must not be one long line."""
        text = to_json(_report())
        assert "\n" in text
        assert text.index('"drives"') < text.index('"hostname"')


class TestHtmlExport:
    def test_it_is_a_complete_document(self):
        html = to_html(_report())
        assert html.lstrip().startswith("<!doctype html>")
        assert "</html>" in html

    def test_it_is_self_contained(self):
        """A deliverable that fetches anything is a deliverable that breaks
        offline, or on a client machine, or in three years."""
        html = to_html(_report())
        assert "<script" not in html.lower()
        for pattern in ("src=", "<link", "@import", "http://", "https://"):
            assert pattern not in html.lower(), f"external reference: {pattern}"

    def test_machine_derived_strings_are_escaped(self):
        html = to_html(_report(hostname="PC<&>\"'",
                               system={"os": "Windows <script>alert(1)</script>",
                                       "cpu": "AMD & Intel"}))
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html
        assert "AMD &amp; Intel" in html

    def test_the_hostname_and_timestamp_appear(self):
        html = to_html(_report())
        assert "TEST-PC" in html
        assert "28 Jul 2026" in html

    def test_an_unparseable_timestamp_is_passed_through(self):
        html = to_html(_report(generatedAt="not-a-date"))
        assert "not-a-date" in html

    def test_all_three_drift_states_render(self):
        html = to_html(_report(tweaks=_TRISTATE))
        for label in ("Applied", "Not applied", "Unknown"):
            assert f">{label}<" in html

    def test_a_healthy_machine_says_so(self):
        assert "Nothing needing attention" in to_html(_report())

    def test_findings_are_listed_when_present(self):
        html = to_html(_report(startup={"total": 30, "enabled": 20,
                                        "recommendedDisable": 7}))
        assert "<li>" in html and "startup item" in html

    def test_empty_sections_degrade_to_a_message(self):
        html = to_html(_report(drives=[], tweaks={}))
        assert "No drive data available" in html
        assert "No tweak state available" in html

    def test_it_survives_a_nearly_empty_report(self):
        """The backend wraps every section independently, so a locked-down
        machine can genuinely produce this."""
        html = to_html({"hostname": "X", "generatedAt": "2026-01-01T00:00:00"})
        assert "</html>" in html

    @pytest.mark.parametrize("token", ["prefers-color-scheme", "@media print"])
    def test_it_handles_dark_mode_and_printing(self, token):
        assert token in to_html(_report())

    def test_no_unrendered_placeholders(self):
        """An f-string mistake would ship a literal brace to the client."""
        html = to_html(_report())
        leftovers = re.findall(r"\{[a-z_]+\}", html)
        assert not leftovers, f"unrendered placeholders: {leftovers}"
