"""
Perimeter-stroke cache fidelity and bounds.

paint_bevel_frame / paint_top_sheen are static for a given (size, radius,
alpha) but were re-stroked on every repaint. Profiling a full-window render
put the bevel alone at 1.60ms across 14 calls (17% of the frame) — stroking
an antialiased rounded rect with a GRADIENT PEN is a slow path in Qt's
raster engine. They are now rasterised once and blitted.

Caching is only acceptable if it is invisible, so this compares the blitted
result against a live stroke pixel-for-pixel. It also pins the cache bound:
an unbounded size-keyed pixmap cache is what once leaked 11.9GB on a drag.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter,
                           QPen, QPixmap)

from frontend import animations as A


def _blit(width, height, radius, fn, **kw):
    pm = QPixmap(width, height)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    fn(p, QRect(0, 0, width, height), radius, **kw)
    p.end()
    return pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _reference_bevel(width, height, radius, light_alpha, dark_alpha):
    """The pre-cache implementation, stroked live."""
    pm = QPixmap(width, height)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(Qt.BrushStyle.NoBrush)
    inner = QRectF(0, 0, width, height).adjusted(0.5, 0.5, -0.5, -0.5)
    grad = QLinearGradient(inner.topLeft(), inner.bottomRight())
    grad.setColorAt(0.0, QColor(255, 255, 255, int(255 * light_alpha)))
    grad.setColorAt(1.0, QColor(0, 0, 0, int(255 * dark_alpha)))
    p.setPen(QPen(QBrush(grad), 1.0))
    p.drawRoundedRect(inner, radius, radius)
    p.end()
    return pm.toImage().convertToFormat(QImage.Format.Format_ARGB32)


def _max_delta(a: QImage, b: QImage) -> int:
    assert (a.width(), a.height()) == (b.width(), b.height())
    worst = 0
    for y in range(0, a.height(), 2):
        for x in range(0, a.width(), 2):
            pa, pb = a.pixel(x, y), b.pixel(x, y)
            for shift in (24, 16, 8, 0):
                worst = max(worst, abs(((pa >> shift) & 0xFF)
                                       - ((pb >> shift) & 0xFF)))
    return worst


@pytest.mark.parametrize("size,radius", [
    ((321, 152), 16), ((216, 46), 13), ((640, 300), 20),
])
def test_cached_bevel_is_pixel_identical(qapp, size, radius):
    width, height = size
    A._STROKE_CACHE.clear()
    cached = _blit(width, height, radius, A.paint_bevel_frame,
                   light_alpha=0.14, dark_alpha=0.20)
    reference = _reference_bevel(width, height, radius, 0.14, 0.20)
    assert _max_delta(cached, reference) == 0, (
        "cached bevel differs from a live stroke — the cache is visible")


def test_cached_bevel_reuses_the_pixmap(qapp):
    A._STROKE_CACHE.clear()
    _blit(321, 152, 16, A.paint_bevel_frame)
    assert len(A._STROKE_CACHE) == 1
    _blit(321, 152, 16, A.paint_bevel_frame)
    assert len(A._STROKE_CACHE) == 1, "identical stroke rasterised twice"


def test_alpha_variants_are_keyed_separately(qapp):
    """A card and a nav entry share a size but not their bevel alphas —
    if the key ignored them, one would wear the other's depth."""
    A._STROKE_CACHE.clear()
    _blit(300, 120, 16, A.paint_bevel_frame, light_alpha=0.14, dark_alpha=0.20)
    _blit(300, 120, 16, A.paint_bevel_frame, light_alpha=0.30, dark_alpha=0.05)
    assert len(A._STROKE_CACHE) == 2

    light = _blit(300, 120, 16, A.paint_bevel_frame,
                  light_alpha=0.14, dark_alpha=0.20)
    heavy = _blit(300, 120, 16, A.paint_bevel_frame,
                  light_alpha=0.30, dark_alpha=0.05)
    assert _max_delta(light, heavy) > 0, "different alphas produced one image"


def test_sheen_strength_is_keyed(qapp):
    A._STROKE_CACHE.clear()
    _blit(300, 120, 16, A.paint_top_sheen, strength=0.55)
    _blit(300, 120, 16, A.paint_top_sheen, strength=1.0)
    assert len(A._STROKE_CACHE) == 2


def test_zero_strength_sheen_paints_nothing(qapp):
    A._STROKE_CACHE.clear()
    _blit(300, 120, 16, A.paint_top_sheen, strength=0.0)
    assert not A._STROKE_CACHE


def test_cache_is_hard_bounded(qapp):
    """Every distinct size mints an entry; a resize drag sweeps hundreds.
    The bound is what stops that becoming a leak."""
    A._STROKE_CACHE.clear()
    for width in range(200, 200 + A._STROKE_CACHE_MAX * 2):
        _blit(width, 60, 12, A.paint_bevel_frame)
    assert len(A._STROKE_CACHE) <= A._STROKE_CACHE_MAX


def test_degenerate_sizes_do_not_raise(qapp):
    A._STROKE_CACHE.clear()
    for rect in (QRect(0, 0, 0, 0), QRect(0, 0, 10, 0), QRect(0, 0, 0, 10)):
        pm = QPixmap(10, 10)
        p = QPainter(pm)
        A.paint_bevel_frame(p, rect, 8)
        A.paint_top_sheen(p, rect, 8)
        p.end()
    assert not A._STROKE_CACHE
