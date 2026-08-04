"""
Regressions found by the v10.3 deep architectural audit.

Four defects, each with the guard that would have caught it and the
generalised guard for its whole class:

1. PAINTED TOKENS THAT QColor CANNOT PARSE. Half the palette is written in
   QSS's rgba() notation. QColor does not understand rgba() — it returns an
   INVALID colour, which Qt paints as opaque black. ToggleSwitch fed
   `panel_line` straight to QColor and drew a hard black pill in place of
   its off-track, in both themes. theme.to_qcolor() exists to parse these;
   nothing enforced its use.

2. A JOB OBJECT LEAKED ON EVERY FAILED SPAWN. PowerShellTask.run() created
   the Windows Job Object before Popen but only published it to self._job
   after, so the `finally` that closes it saw None whenever Popen raised.
   Measured at exactly one leaked kernel handle per failed spawn.

3. THE CONSOLE MATERIALISED ITS WHOLE BUFFER ON EVERY REPAINT.
   LiveConsole.paintEvent asked `if self.toPlainText():` purely to decide
   whether to draw the empty state — 216 KB and 236 us at the 2000-line
   ceiling, on the widget that repaints once per streamed output line.

4. NOTHING PINNED THE "no leaks" CLAIM. Modal opens and module navigation
   are the two loops a user runs hundreds of times per session.
"""
from __future__ import annotations

import ast
import gc
import subprocess
import weakref
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap

from frontend import theme as TH

_SRC = Path(__file__).resolve().parent.parent / "src"


def _both_themes(qapp) -> dict[str, dict]:
    """{'dark': tokens, 'light': tokens} — the manager owns the switch, so
    toggle it rather than reaching for a private per-mode table."""
    manager = TH.ThemeManager()
    out = {}
    for _ in range(2):
        out[manager.t["name"]] = dict(manager.t)
        manager.toggle()
    assert set(out) == {"dark", "light"}, "theme manager stopped round-tripping"
    return out


def _drain(qapp, rounds: int = 4):
    """Settle the way a real event loop does — INCLUDING deferred deletes.

    processEvents() alone does NOT dispatch DeferredDelete events posted
    from the main thread. A leak check built on it reports every correctly
    deleted widget as a leak, which is exactly how this file's first draft
    'found' ten leaks that did not exist.
    """
    for _ in range(rounds):
        qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


# ============================================================
#  1. PAINTED COLOUR TOKENS
# ============================================================
#: Names a token dict travels under at a paint site.
_TOKEN_DICTS = {"t", "tokens"}


def _token_arg(node) -> str | None:
    """`t["key"]` or `t.get("key", ...)` -> "key"; anything else -> None."""
    if isinstance(node, ast.Subscript):
        if (isinstance(node.value, ast.Name) and node.value.id in _TOKEN_DICTS
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            return node.slice.value
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in _TOKEN_DICTS
            and node.args and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)):
        return node.args[0].value
    return None


def _painted_token_sites() -> list[tuple[str, int, str]]:
    """Every `QColor(<token>)` in the frontend, parsed from the AST.

    AST, not a regex over the source text: a regex also matches the token
    named inside a DOCSTRING — including the one in ToggleSwitch._track_off
    that documents this very bug — and a guard that fails on its own
    explanatory prose gets deleted rather than fixed.
    """
    sites = []
    for path in sorted((_SRC / "frontend").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "QColor" and node.args):
                continue
            token = _token_arg(node.args[0])
            if token is not None:
                sites.append((path.name, node.lineno, token))
    return sites


def test_the_painted_token_scan_found_sites():
    """A regex that silently matches nothing would make the guard below a
    no-op that passes forever."""
    assert _painted_token_sites(), (
        "no QColor(t[...]) sites found — the scan pattern has gone stale")


def test_every_painted_token_is_qcolor_parsable(qapp):
    """THE guard for the ToggleSwitch black-pill bug.

    A token handed to QColor must be something QColor can actually read.
    rgba() tokens are legal in QSS and invalid here, and the failure is
    silent: QColor(invalid) is opaque black, so the widget paints a solid
    black shape instead of a subtle tint and nothing raises. Reach for
    TH.to_qcolor() (and TH.blend() if the tint must land on a surface).
    """
    themes = _both_themes(qapp)
    failures = []
    for filename, line, token in _painted_token_sites():
        for mode, tokens in themes.items():
            if token not in tokens:
                continue    # a .get() default for an optional key
            value = tokens[token]
            if not isinstance(value, str):
                continue
            if not QColor(value).isValid():
                failures.append(
                    f"{filename}:{line} paints t[{token!r}] = {value!r} "
                    f"({mode}) — QColor cannot parse it and renders it BLACK; "
                    "use TH.to_qcolor()")
    assert not failures, "unparsable painted tokens:\n  " + "\n  ".join(failures)


def test_the_toggle_off_track_is_neither_black_nor_transparent(qapp):
    """The specific regression: the Startup Manager's switches.

    The off track must be an opaque colour that is visibly NOT the black
    QColor('rgba(...)') used to produce, and must differ from the on track
    so the two states never collapse into one another.
    """
    from frontend.widgets import ToggleSwitch

    for mode, tokens in _both_themes(qapp).items():
        switch = ToggleSwitch(tokens)
        off = switch._off_color
        assert off.isValid(), f"{mode}: off track is an invalid QColor"
        assert off.alpha() == 255, (
            f"{mode}: off track is translucent ({off.alpha()}) — paintEvent "
            "rebuilds the track from RGB and would drop the alpha")
        assert (off.red(), off.green(), off.blue()) != (0, 0, 0), (
            f"{mode}: off track is pure black — the rgba() parse regressed")
        assert off != switch._on_color, f"{mode}: on and off tracks are equal"


def test_the_toggle_track_tracks_the_theme(qapp):
    """apply_theme must re-derive the track, or a live theme switch leaves
    the dark well sitting on the light row."""
    from frontend.widgets import ToggleSwitch

    themes = _both_themes(qapp)
    switch = ToggleSwitch(themes["dark"])
    dark_off = QColor(switch._off_color)
    switch.apply_theme(themes["light"])
    assert switch._off_color != dark_off, (
        "apply_theme did not re-derive the off track for the new theme")


# ============================================================
#  2. JOB OBJECT LIFECYCLE
# ============================================================
def test_a_failed_spawn_still_closes_its_job_object(monkeypatch, tmp_path):
    """The job is created BEFORE Popen so the child can be assigned to it
    the instant it exists. That ordering is correct — but it means a Popen
    that raises leaves a live kernel handle unless the job is published to
    self._job first, which is what the `finally` closes.
    """
    from utils import helpers

    created = []
    real_job = helpers.ProcessJob

    class RecordingJob(real_job):
        def __init__(self):
            super().__init__()
            created.append(self)

        @property
        def closed(self) -> bool:
            return self._handle is None

    def exploding_popen(*args, **kwargs):
        raise FileNotFoundError("simulated: powershell.exe is missing")

    monkeypatch.setattr(helpers, "ProcessJob", RecordingJob)
    monkeypatch.setattr(subprocess, "Popen", exploding_popen)

    ps1 = tmp_path / "core.ps1"
    ps1.write_text("# stub", encoding="utf-8")
    task = helpers.PowerShellTask(str(ps1), "SystemInfo")

    reported = []
    task.failed.connect(reported.append)
    task.run()

    assert reported, "a failed spawn must still report through `failed`"
    assert created, "the task never armed a ProcessJob"
    for job in created:
        if job.available or job._handle is not None:
            assert job.closed, (
                "the Job Object survived a failed spawn — one kernel handle "
                "leaks per attempt for the life of the GUI process")


def test_a_successful_run_also_releases_the_job(monkeypatch, tmp_path):
    """The counterpart: the ordinary path must not start leaking either,
    and must still DETACH (kill-on-close off) so anything the task
    deliberately left running survives."""
    from utils import helpers

    created = []
    real_job = helpers.ProcessJob

    class RecordingJob(real_job):
        def __init__(self):
            super().__init__()
            self.detached = False
            created.append(self)

        def detach(self):
            self.detached = True
            super().detach()

    class FakeProcess:
        pid = 4242

        def __init__(self):
            self.stdout = self
            self._chunks = [b"##PULSE##SUCCESS|done\r\n"]

        def read1(self, _n):
            return self._chunks.pop(0) if self._chunks else b""

        def poll(self):
            return 0

        def wait(self):
            return 0

    monkeypatch.setattr(helpers, "ProcessJob", RecordingJob)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: FakeProcess())

    ps1 = tmp_path / "core.ps1"
    ps1.write_text("# stub", encoding="utf-8")
    task = helpers.PowerShellTask(str(ps1), "SystemInfo")
    results = []
    task.finished.connect(results.append)
    task.run()

    assert results and results[0].success, "clean SUCCESS verdict was not parsed"
    assert created, "the task never armed a ProcessJob"
    for job in created:
        assert job._handle is None, "the Job Object handle was not released"
        if job.available or job.detached:
            assert job.detached, (
                "kill-on-close was never disarmed — a task that deliberately "
                "leaves a window open would have it killed")


# ============================================================
#  3. CONSOLE REPAINT COST
# ============================================================
def test_the_console_empty_state_never_materialises_the_buffer(qapp):
    """paintEvent must ask the DOCUMENT whether it is empty, not build the
    whole buffer into a Python str to test its truthiness.

    Pinned by making toPlainText() fail loudly: it is a perfectly good API
    for copy/export (which genuinely need the text) and a 216 KB allocation
    in a paint path that runs once per streamed line.
    """
    from frontend.widgets import LiveConsole

    console = LiveConsole(TH.ThemeManager().t)
    console.resize(600, 200)
    for i in range(200):
        console.append_line(f"[{i:04d}] streaming output line")

    calls = []
    original = type(console).toPlainText

    def spy(self):
        calls.append(1)
        return original(self)

    # render() into a pixmap, NOT repaint(): Qt skips painting entirely for
    # a widget that was never shown, so a repaint()-driven version of this
    # test passes whatever paintEvent does. render() invokes paintEvent
    # directly and needs no visible window.
    target = QPixmap(console.size())
    target.fill(Qt.GlobalColor.transparent)
    try:
        type(console).toPlainText = spy
        painter = QPainter(target)
        console.render(painter, QPoint())
        painter.end()
        qapp.processEvents()
    finally:
        type(console).toPlainText = original

    assert console.blockCount() > 1, "the console under test was left empty"

    assert not calls, (
        f"paintEvent called toPlainText() {len(calls)}x — that materialises "
        "the entire console buffer on every repaint; use "
        "document().isEmpty()")


@pytest.mark.parametrize("state", ["fresh", "filled", "cleared"])
def test_the_empty_state_decision_is_unchanged(qapp, state):
    """document().isEmpty() must agree with the old truthiness test in
    every state the console can be in, or the fix traded cost for a
    missing (or a spurious) empty-state graphic."""
    from frontend.widgets import LiveConsole

    console = LiveConsole(TH.ThemeManager().t)
    if state == "filled":
        console.append_line("something")
    elif state == "cleared":
        console.append_line("something")
        console.clear_console()

    assert console.document().isEmpty() == (not console.toPlainText()), (
        f"{state}: the O(1) emptiness test disagrees with the old one")


# ============================================================
#  4. LEAK REGRESSIONS
# ============================================================
def _dialog_builders(window):
    from frontend import menu_structure as MS
    from frontend import widgets as W

    t = window.theme.t
    item = {"icon": "📦", "title": "Demo", "desc": "Demo card.",
            "task": "SystemInfo"}
    hub = {"icon": "🛠️", "title": "Hub", "desc": "Hub.", "hub": True,
           "items": [item]}
    return [
        ("ConfirmDialog", lambda: W.ConfirmDialog(window, item, t)),
        ("HubDialog", lambda: W.HubDialog(window, hub, t)),
        ("SoftwareCatalogDialog", lambda: W.SoftwareCatalogDialog(
            window, item, t, MS.SOFTWARE_CATALOG)),
        ("CommandPalette", lambda: W.CommandPalette(
            window, t, list(MS.iter_leaf_items()))),
        ("StorageAnalyzerDialog", lambda: W.StorageAnalyzerDialog(window, "", t)),
    ]


@pytest.mark.parametrize("name", [
    "ConfirmDialog", "HubDialog", "SoftwareCatalogDialog",
    "CommandPalette", "StorageAnalyzerDialog",
])
def test_repeated_modal_opens_do_not_accumulate(window, qapp, name):
    """Open/close is the loop a user runs hardest. A dialog that outlives
    its close keeps its whole widget tree — and its frost pixmap — parented
    to the window forever.
    """
    from frontend.widgets import PulseDialog

    build = dict(_dialog_builders(window))[name]
    counts = []
    for _ in range(3):
        dialog = build()
        dialog.show()
        _drain(qapp, 2)
        dialog.reject()
        dialog.deleteLater()
        del dialog
        _drain(qapp)
        gc.collect()
        counts.append(len(window.findChildren(PulseDialog)))

    assert counts[0] == counts[-1], (
        f"{name}: live dialog count climbed {counts} across three "
        "open/close cycles")


def test_a_closed_dialog_is_actually_destroyed(window, qapp):
    """The strongest form: nothing — not Qt, not a Python closure — still
    holds the dialog once it has been closed and deleted."""
    from frontend import widgets as W

    t = window.theme.t
    dialog = W.ConfirmDialog(
        window, {"icon": "📦", "title": "Demo", "desc": "d.",
                 "task": "SystemInfo"}, t)
    dialog.show()
    _drain(qapp, 2)
    ref = weakref.ref(dialog)
    dialog.reject()
    dialog.deleteLater()
    del dialog
    _drain(qapp)
    gc.collect()

    assert ref() is None, (
        "the dialog's Python wrapper outlived its deletion — something is "
        "still holding a reference (a lambda captured in a connect(), a "
        "module-level list, or a signal never disconnected)")


def test_module_navigation_does_not_accumulate(window, qapp):
    """Every page is built once, lazily, then reused. Sweeping all modules
    repeatedly must not add objects or timers — the 31ms navigation budget
    depends on pages being reused rather than rebuilt."""
    for index in range(len(window.pages)):     # warm every lazy page first
        window.open_category(index)
        _drain(qapp, 2)
    window.go_home()
    _drain(qapp, 2)

    base_objects = len(window.findChildren(object))
    base_timers = len(window.findChildren(QTimer))

    for _ in range(3):
        for index in range(len(window.pages)):
            window.open_category(index)
            _drain(qapp, 2)
        window.go_home()
        _drain(qapp, 2)

    assert len(window.findChildren(QTimer)) == base_timers, (
        "module navigation is adding timers — a per-visit QTimer that is "
        "never stopped keeps firing for the life of the session")
    assert len(window.findChildren(object)) == base_objects, (
        f"module navigation grew the object tree from {base_objects} to "
        f"{len(window.findChildren(object))} — pages are being rebuilt "
        "rather than reused")


def test_the_ambient_glow_caches_stay_bounded(window, qapp):
    """Both are keyed on values that MUST stay discrete. The orb cache was
    once keyed on window size and reached 11.9 GB on a single resize drag;
    the same mistake here would be silent."""
    glow = window._glow
    for width in range(900, 1400, 25):          # a resize drag
        glow.resize(width, 600)
        glow.repaint()
        qapp.processEvents()

    assert len(glow._orb_cache) <= 32, (
        f"orb cache reached {len(glow._orb_cache)} entries during a resize — "
        "it must be keyed on (colour, peak) only, never on size")
    assert len(glow._star_cache) <= 32, (
        f"star cache reached {len(glow._star_cache)} entries during a resize — "
        "star sizes must stay quantised")
