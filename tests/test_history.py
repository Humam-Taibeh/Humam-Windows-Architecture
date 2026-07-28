"""
Per-task run history — storage, formatting, and the card footer.

Three separable concerns, tested separately:

  * prefs.record_task_run / task_history — durable storage that must
    degrade to "no history" rather than ever raising, because a decorative
    caption must never be able to stop the app starting or block a run.
  * the format_* helpers — pure functions, so the interesting cases
    (clock skew, a single sample, boundary units) are cheap to pin.
  * GlassCard's footer — where a REGRESSION was found and fixed during
    implementation: a plain QLabel caption took the card's minimum width
    from 184px to 337px, reviving the v9.1 density bug. That guard is
    test_history_pill_never_widens_the_card below, and it is the most
    load-bearing test in this file.
"""
from __future__ import annotations

import time

import pytest

from utils import prefs
from frontend.widgets import (ElidedCaption, format_duration,
                              format_history_caption, format_relative_age)


@pytest.fixture(autouse=True)
def _clean_history():
    """Each test starts from an empty history. The session-wide preference
    isolation in conftest already redirects the hive; this just keeps the
    tests independent of each other's writes."""
    prefs.clear_task_history()
    yield
    prefs.clear_task_history()


# ============================================================
#  STORAGE
# ============================================================
class TestStorage:
    def test_a_run_is_recorded_and_read_back(self):
        prefs.record_task_run("DarkMode", 4200.0, "ok")
        entry = prefs.task_history()["DarkMode"]
        assert entry["runs"] == 1
        assert entry["avg_ms"] == pytest.approx(4200.0)
        assert entry["last_ms"] == pytest.approx(4200.0)
        assert entry["outcome"] == "ok"
        assert entry["last_ts"] > 0

    def test_average_converges_toward_recent_runs(self):
        """The EMA must move toward new evidence without being captured by
        any single sample."""
        prefs.record_task_run("RunSFC", 100_000.0, "ok")
        for _ in range(8):
            prefs.record_task_run("RunSFC", 20_000.0, "ok")
        entry = prefs.task_history()["RunSFC"]
        assert entry["runs"] == 9
        assert 20_000 <= entry["avg_ms"] < 40_000, (
            f"average {entry['avg_ms']:.0f}ms is still anchored by the first "
            "outlier — the window is not forgetting")

    def test_second_run_moves_the_average_off_the_first(self):
        prefs.record_task_run("CleanCache", 1000.0, "ok")
        prefs.record_task_run("CleanCache", 3000.0, "ok")
        avg = prefs.task_history()["CleanCache"]["avg_ms"]
        assert 1000.0 < avg < 3000.0

    def test_tasks_are_tracked_independently(self):
        prefs.record_task_run("DarkMode", 1000.0, "ok")
        prefs.record_task_run("GameMode", 2000.0, "ok")
        history = prefs.task_history()
        assert history["DarkMode"]["avg_ms"] == pytest.approx(1000.0)
        assert history["GameMode"]["avg_ms"] == pytest.approx(2000.0)

    def test_local_actions_are_not_recorded(self):
        """`@open_log` opens a file viewer — it is not an operation and has
        no duration worth reporting."""
        prefs.record_task_run("@open_log", 500.0, "ok")
        assert "@open_log" not in prefs.task_history()

    def test_zero_and_negative_durations_are_rejected(self):
        """A non-positive elapsed time means the measurement was wrong, not
        that the task was instant. Recording it would poison the average."""
        prefs.record_task_run("DarkMode", 0.0, "ok")
        prefs.record_task_run("DarkMode", -5.0, "ok")
        assert prefs.task_history() == {}

    def test_corrupt_storage_yields_an_empty_history(self):
        prefs._settings().setValue("ui/task_history", "{not json at all")
        assert prefs.task_history() == {}

    def test_a_non_dict_payload_yields_an_empty_history(self):
        prefs._settings().setValue("ui/task_history", "[1, 2, 3]")
        assert prefs.task_history() == {}

    def test_one_corrupt_record_does_not_discard_the_others(self):
        prefs.record_task_run("DarkMode", 1000.0, "ok")
        import json
        raw = json.loads(prefs._settings().value("ui/task_history"))
        raw["Broken"] = {"last_ts": "not-a-number"}
        raw["AlsoBroken"] = "not-a-dict"
        prefs._settings().setValue("ui/task_history", json.dumps(raw))

        history = prefs.task_history()
        assert "DarkMode" in history, "a good record was lost to a bad neighbour"
        assert "AlsoBroken" not in history

    def test_history_is_bounded(self):
        for i in range(prefs.HISTORY_LIMIT + 25):
            prefs.record_task_run(f"Task{i}", 100.0 + i, "ok")
        assert len(prefs.task_history()) <= prefs.HISTORY_LIMIT

    def test_eviction_keeps_the_most_recent(self):
        for i in range(prefs.HISTORY_LIMIT + 10):
            prefs.record_task_run(f"Task{i}", 100.0, "ok")
        history = prefs.task_history()
        newest = f"Task{prefs.HISTORY_LIMIT + 9}"
        assert newest in history, "eviction dropped the newest record"


# ============================================================
#  FORMATTING (pure functions)
# ============================================================
class TestFormatting:
    @pytest.mark.parametrize("seconds,expected", [
        (5, "just now"),
        (89, "just now"),
        (60 * 5, "5m ago"),
        (60 * 90, "1h ago"),
        (3600 * 30, "1d ago"),
        (86400 * 3, "3d ago"),
        (86400 * 10, "1w ago"),
        (86400 * 60, "2mo ago"),
        (86400 * 400, "1y ago"),
    ])
    def test_relative_age(self, seconds, expected):
        now = 1_000_000.0
        assert format_relative_age(now - seconds, now=now) == expected

    def test_no_timestamp_produces_no_caption(self):
        assert format_relative_age(0.0) == ""

    def test_a_future_timestamp_does_not_produce_nonsense(self):
        """Clock skew (NTP correction, DST, a restored profile) must not
        render a negative age."""
        now = 1_000_000.0
        assert format_relative_age(now + 5000, now=now) == "just now"

    @pytest.mark.parametrize("ms,expected", [
        (1500, "2s"),
        (45_000, "45s"),
        (90_000, "2m"),
        (600_000, "10m"),
        (5_400_000, "1.5h"),
        (7_200_000, "2h"),
    ])
    def test_duration(self, ms, expected):
        assert format_duration(ms) == expected

    def test_zero_duration_is_blank(self):
        assert format_duration(0) == ""

    def test_caption_withholds_duration_after_a_single_run(self):
        """One sample is not a 'typical' duration; presenting it as one
        would be a guess wearing the clothes of a statistic."""
        entry = {"last_ts": time.time() - 3600, "runs": 1,
                 "avg_ms": 90_000, "last_ms": 90_000, "outcome": "ok"}
        text, tooltip = format_history_caption(entry)
        assert "~" not in text, f"single run advertised a typical duration: {text}"
        assert "1h ago" in text
        assert tooltip

    def test_caption_includes_duration_once_there_are_several_runs(self):
        entry = {"last_ts": time.time() - 3600, "runs": 4,
                 "avg_ms": 90_000, "last_ms": 90_000, "outcome": "ok"}
        text, _ = format_history_caption(entry)
        assert "~2m" in text

    def test_no_entry_produces_no_caption(self):
        assert format_history_caption(None) == ("", "")
        assert format_history_caption({}) == ("", "")

    def test_a_failed_last_run_is_surfaced_in_the_tooltip(self):
        entry = {"last_ts": time.time() - 60, "runs": 3,
                 "avg_ms": 1000, "last_ms": 1000, "outcome": "err"}
        _, tooltip = format_history_caption(entry)
        assert "error" in tooltip.lower()


# ============================================================
#  THE CARD FOOTER
# ============================================================
def _card_for(window, task: str):
    for page in window.pages:
        for card in page.cards:
            if card.item.get("task") == task:
                return card
    raise AssertionError(f"no card found for task {task!r}")


class TestCardFooter:
    def test_a_card_with_no_history_says_nothing(self, window, qapp):
        card = _card_for(window, "RunSFC")
        card.set_history(None)
        qapp.processEvents()
        assert card._history_pill.fullText() == ""
        assert not card._history_pill.isVisibleTo(card)

    def test_a_card_with_history_shows_its_caption(self, window, qapp):
        card = _card_for(window, "DarkMode")
        card.set_history({"last_ts": time.time() - 86400 * 3, "runs": 4,
                          "avg_ms": 120_000, "last_ms": 118_000,
                          "outcome": "ok"})
        qapp.processEvents()
        try:
            assert card._history_pill.isVisibleTo(card)
            assert "3d ago" in card._history_pill.fullText()
            assert card._history_pill.toolTip()
        finally:
            card.set_history(None)

    def test_history_pill_is_actually_painted(self, window, qapp):
        """isVisible() and a non-empty text are NOT enough.

        The first implementation gave the caption a size policy of
        Ignored to stop it inflating the card. Ignored discards the
        sizeHint entirely, so next to the footer's trailing stretch the
        layout handed it ZERO width: nothing was painted on any card, yet
        isVisibleTo() returned True and fullText() was correct, so every
        assertion in this file still passed. A screenshot was the only
        thing that caught it. Assert the geometry the user actually sees.
        """
        card = _card_for(window, "DarkMode")
        window.open_category(1)
        qapp.processEvents()
        card.set_history({"last_ts": time.time() - 86400 * 3, "runs": 4,
                          "avg_ms": 120_000, "last_ms": 120_000,
                          "outcome": "ok"})
        qapp.processEvents()
        try:
            pill = card._history_pill
            assert pill.width() > 20, (
                f"the caption was laid out {pill.width()}px wide — it is "
                "collapsed and paints nothing, however 'visible' it claims "
                "to be")
            assert pill.height() > 0
            assert pill.text(), "no text survived elision at the granted width"
        finally:
            card.set_history(None)
            window.go_home()
            qapp.processEvents()

    def test_history_pill_never_widens_the_card(self, window, qapp):
        """THE regression guard.

        A plain QLabel here measured 184px -> 337px on the card's minimum
        width once the APPLIED chip was also visible, because QHBoxLayout
        sums its children's minimums. That is precisely the v9.1 density
        bug the footer was restructured to eliminate, and it would have
        shipped as cards refusing to fit the dense 3-column grid — with
        nothing raising anywhere.
        """
        card = _card_for(window, "DarkMode")
        card.set_applied(None)
        card.set_history(None)
        qapp.processEvents()
        baseline = card.minimumSizeHint().width()

        try:
            card.set_applied(True)
            # The longest caption the formatter can possibly produce.
            card.set_history({"last_ts": time.time() - 86400 * 400, "runs": 9,
                              "avg_ms": 5_400_000, "last_ms": 5_400_000,
                              "outcome": "err"})
            qapp.processEvents()
            widest = card.minimumSizeHint().width()
        finally:
            card.set_applied(None)
            card.set_history(None)
            qapp.processEvents()

        assert widest == baseline, (
            f"card minimum width grew {baseline}px -> {widest}px with the "
            "history caption and APPLIED chip both shown — the footer is "
            "summing widths again, which breaks the responsive card grid")

    def test_caption_elides_rather_than_overflowing(self, qapp):
        """Exercised inside a real parent layout, which is the only way the
        caption is ever used — a never-shown top-level widget does not get
        a resize event delivered, so testing it bare would assert on a
        code path production never takes."""
        from PySide6.QtWidgets import QHBoxLayout, QWidget

        host = QWidget()
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        pill = ElidedCaption()
        pill.setFullText("1y ago · ~1.5h")
        # setFixedWidth is the deterministic way to starve it: a plain
        # resize() is undone by the parent layout on the next pass, and a
        # top-level host cannot be driven below the window system's own
        # minimum width.
        pill.setFixedWidth(30)
        layout.addWidget(pill)
        host.show()
        qapp.processEvents()
        try:
            assert pill.width() < pill.fontMetrics().horizontalAdvance(
                pill.fullText()), "the pill was not actually squeezed"
            assert pill.fullText() == "1y ago · ~1.5h", "the full text was lost"
            assert pill.text() != pill.fullText(), (
                "a squeezed caption did not elide — it will paint outside "
                "its box or be clipped mid-glyph")
            assert pill.text().endswith("…")
        finally:
            host.close()

    def test_caption_reports_a_zero_minimum_width(self, qapp):
        pill = ElidedCaption()
        pill.setFullText("1y ago · ~1.5h")
        assert pill.minimumSizeHint().width() == 0


def test_a_completed_run_reaches_the_card(window, qapp):
    """The end-to-end path: _start_task stamps a monotonic start,
    _finish_common folds the elapsed time in, and every card is refreshed
    from storage. Driven through the window's own methods rather than by
    writing prefs directly, so a break in the wiring is caught."""
    import time as _time
    card = _card_for(window, "DarkMode")
    window._running_item = {"task": "DarkMode", "title": "Global Dark Mode",
                            "glyph": "moon"}
    window._running_accent = "optimization"
    window._run_started_at = _time.monotonic() - 3.0
    try:
        window._record_task_history("ok")
        qapp.processEvents()

        stored = prefs.task_history().get("DarkMode")
        assert stored is not None, "the run never reached storage"
        assert stored["last_ms"] == pytest.approx(3000, rel=0.3)
        assert card._history_pill.fullText().startswith("just now")
    finally:
        window._running_item = None
        window._run_started_at = None
        card.set_history(None)


def test_a_cancelled_run_is_not_recorded(window, qapp):
    """_finish_common passes flash=None for a cancellation. A stopped task
    is a partial measurement and would drag every estimate down."""
    import time as _time
    window._running_item = {"task": "OptimizeDrives", "title": "Optimize",
                            "glyph": "disk"}
    window._running_accent = "maintenance"
    window._run_started_at = _time.monotonic() - 2.0
    try:
        window._finish_common(None)          # the cancellation path
        qapp.processEvents()
        assert "OptimizeDrives" not in prefs.task_history()
    finally:
        window._running_item = None
        window._run_started_at = None
