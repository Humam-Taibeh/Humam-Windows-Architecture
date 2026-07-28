"""
Whole-window render budget.

Everything else in this suite guards the render pipeline STRUCTURALLY —
test_paint_cache asserts the bevel pixmap is reused, test_ambient asserts
the glow layer is rebuilt once per cadence rather than once per paint.
Those are the right guards for the specific optimizations they cover, but
none of them measures the thing the user actually feels: how long the
whole shell takes to put a frame together.

That number was driven from ~14ms to 8.22ms and then to 7.41ms across two
optimization passes, and until now nothing held the line. A future change
that quietly reintroduces an uncached gradient, a per-paint QPainterPath
rebuild, or a full-tree stylesheet repolish would sail through every
existing test while doubling frame cost — visible immediately as drag and
resize stutter on a high-refresh display, which is exactly the regression
class the last two sessions were spent eliminating.

WHY A MEDIAN, AND WHY A LOOSE CEILING
    Wall-clock assertions are the flakiest thing you can put in CI: a
    shared vCPU, a noisy neighbour or a GC pause can spike any single
    sample. So this measures many frames, discards the warm-up, and takes
    the MEDIAN — which is stable even when the max is not. The ceiling is
    then set well above the real figure (12ms against a measured ~7.4ms)
    so it catches a doubling, not a fluctuation. It is a regression alarm,
    not a benchmark; the number to watch trend over time is the one it
    prints on failure.

    PULSE_RENDER_BUDGET_MS overrides the ceiling for unusually slow or
    contended machines without editing the test.
"""
from __future__ import annotations

import os
import statistics
import time

import pytest
from PySide6.QtGui import QPixmap

from frontend import animations as A

# 12ms leaves headroom over the measured ~7.4ms while still being inside a
# single 60Hz frame (16.7ms) — past this the shell can no longer keep up
# with its own compositor on the thread that also runs the OS resize loop.
DEFAULT_BUDGET_MS = 12.0

SAMPLES = 40
WARMUP = 5


def _budget_ms() -> float:
    raw = os.environ.get("PULSE_RENDER_BUDGET_MS", "")
    try:
        return float(raw) if raw else DEFAULT_BUDGET_MS
    except ValueError:
        return DEFAULT_BUDGET_MS


def _render_median_ms(widget, qapp) -> tuple[float, float]:
    """(median, p95) milliseconds for a full render of `widget`.

    render() into a pre-allocated pixmap rather than grab(), so the
    measurement is the paint pass itself and not the pixmap allocation —
    allocation cost scales with window size and would otherwise drown the
    signal this test exists to watch.
    """
    pixmap = QPixmap(widget.size())
    for _ in range(WARMUP):          # warm caches: bevel pixmaps, glow layer
        widget.render(pixmap)
    qapp.processEvents()

    samples = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        widget.render(pixmap)
        samples.append((time.perf_counter() - start) * 1000)

    samples.sort()
    return statistics.median(samples), samples[int(len(samples) * 0.95) - 1]


def test_dashboard_render_stays_within_the_frame_budget(window, qapp):
    """The default view — the heaviest one, since the welcome dashboard
    carries the module launchpad, the recent-operations panel and the
    ambient glow all at once."""
    window.go_home()
    qapp.processEvents()

    median, p95 = _render_median_ms(window, qapp)
    budget = _budget_ms()
    assert median < budget, (
        f"dashboard render median {median:.2f}ms (p95 {p95:.2f}ms) exceeds the "
        f"{budget:.1f}ms budget — was 7.41ms when this guard was written. "
        "Look for a newly uncached gradient/stroke or a per-paint stylesheet "
        "repolish; this shows up to users as drag and resize stutter.")


def test_category_page_render_stays_within_the_frame_budget(window, qapp):
    """A populated card grid. Cheaper than the dashboard (measured
    ~5.3ms), so it gets the same ceiling with more headroom — a
    regression that hits the card paint path trips this one first."""
    window.open_category(1)             # System Optimization — 7 cards
    qapp.processEvents()
    try:
        median, p95 = _render_median_ms(window, qapp)
    finally:
        window.go_home()
        qapp.processEvents()

    budget = _budget_ms()
    assert median < budget, (
        f"category page render median {median:.2f}ms (p95 {p95:.2f}ms) exceeds "
        f"the {budget:.1f}ms budget — was 5.27ms when this guard was written.")


def test_render_cost_does_not_scale_with_repeat_paints(window, qapp):
    """A caching guard that does NOT depend on wall-clock thresholds.

    If a cache is keyed wrongly — or silently evicted every frame — the
    first paint after a change is cheap but sustained painting is not.
    Comparing an early window of frames against a late one catches that
    shape of regression on any machine, however slow, because it is a
    RATIO rather than an absolute. Generous 3x bound: this is looking for
    unbounded growth, not measuring jitter.
    """
    window.go_home()
    qapp.processEvents()
    pixmap = QPixmap(window.size())
    for _ in range(WARMUP):
        window.render(pixmap)

    def window_median(count: int) -> float:
        got = []
        for _ in range(count):
            start = time.perf_counter()
            window.render(pixmap)
            got.append((time.perf_counter() - start) * 1000)
        return statistics.median(got)

    early = window_median(15)
    late = window_median(15)
    assert late < early * 3 + 1.0, (
        f"sustained render cost grew from {early:.2f}ms to {late:.2f}ms — "
        "a paint cache is being evicted or re-keyed on every frame")


def test_full_window_render_reuses_every_cached_stroke(window, qapp):
    """The cheap, machine-independent guard that the wall-clock ones miss.

    Found while mutation-testing this file: making _cached_stroke clear the
    cache on every call — i.e. re-rasterising every bevel and sheen on
    every frame, the exact regression the stroke cache exists to prevent —
    costs 1.43x (6.33ms -> 9.03ms). That slips under the 12ms budget, and
    it ALSO slips past test_paint_cache's `len(_STROKE_CACHE) == 1`,
    because clearing and then re-adding one entry still leaves one entry.

    Identity is what actually distinguishes the two: a genuine cache hands
    back the SAME pixmap, while a thrashing one hands back an
    equal-looking new one. Identity must be taken with Qt's cacheKey() and
    NOT Python's id(): the replaced pixmap is garbage-collected the moment
    the cache drops it, so CPython frequently hands the identical address
    straight back to its replacement. An id()-based version of this test
    passed against the very mutation it was written to catch.
    """
    window.go_home()
    qapp.processEvents()
    pixmap = QPixmap(window.size())
    for _ in range(WARMUP):              # populate the cache
        window.render(pixmap)

    before = {key: value.cacheKey() for key, value in A._STROKE_CACHE.items()}
    assert before, (
        "a full window render populated no stroke cache entries at all — "
        "the cache is bypassed, or paint_bevel_frame is no longer reached")

    window.render(pixmap)
    after = {key: value.cacheKey() for key, value in A._STROKE_CACHE.items()}

    rerasterised = [key for key, ident in before.items()
                    if key in after and after[key] != ident]
    evicted = [key for key in before if key not in after]
    assert not rerasterised and not evicted, (
        f"{len(rerasterised)} stroke(s) re-rasterised and {len(evicted)} "
        "evicted during a steady-state repaint — the cache is thrashing, so "
        "every frame pays the full antialiased gradient-pen stroke cost")


@pytest.mark.parametrize("size", [(1100, 720), (1600, 1000)])
def test_budget_holds_across_window_sizes(window, qapp, size):
    """A cache keyed on size must not turn a resize into a rebuild storm.
    The budget is scaled by area against the 1300x860 baseline, so a
    larger window is allowed to cost proportionally more — but no more."""
    original = (window.width(), window.height())
    window.go_home()
    window.resize(*size)
    qapp.processEvents()
    try:
        median, _p95 = _render_median_ms(window, qapp)
    finally:
        window.resize(*original)
        qapp.processEvents()

    area_ratio = (size[0] * size[1]) / (1300 * 860)
    budget = _budget_ms() * max(1.0, area_ratio)
    assert median < budget, (
        f"render median {median:.2f}ms at {size[0]}x{size[1]} exceeds the "
        f"area-scaled budget {budget:.1f}ms")
