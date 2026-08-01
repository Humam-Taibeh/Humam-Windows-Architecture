"""
Layout & dialog standards — the v1.0+ Phase 0 guards.

These encode the visual standards that previously existed only as
convention, and each one is here because the thing it forbids ALREADY
SHIPPED once and was found by measuring rather than by looking:

  * a dialog whose content floor exceeded the window it opens in (the
    Software Catalog's 5-tab row forced a 1637px panel against a 1100px
    cap, so the panel was wider than the app at every window size);
  * a sparse row of "matching" tiles that were not the same width, because
    the shared column width was measured off the wrong size hint;
  * a section band whose header outlived its own cards under a filter, so
    the title sat over the next band's content and mislabelled it.

None of these raises. All three are invisible until somebody renders the
exact combination that exposes them, which is what makes them worth
pinning.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QFrame, QLabel

from frontend import theme as TH
from frontend.main import CategoryPage
from frontend.menu_structure import (
    CATEGORIES, category_bands, category_items, category_operations,
)


# ============================================================
#  SECTION BANDS
# ============================================================
class TestSectionBands:
    """A band is rhythm inside a page, never another level of navigation."""

    def test_every_category_declares_items_or_groups(self):
        for category in CATEGORIES:
            assert category.get("items") or category.get("groups"), (
                f"{category['title']} declares neither items nor groups")

    def test_bands_never_lose_a_card(self):
        """category_items must flatten to exactly the cards the bands hold —
        a card that exists in `groups` but not in the flattened view is
        invisible to the counter, the palette and playbook validation."""
        for category in CATEGORIES:
            banded = [item for _title, items in category_bands(category)
                      for item in items]
            assert banded == category_items(category), (
                f"{category['title']}: bands and category_items disagree")

    def test_banded_categories_have_titled_bands(self):
        """An untitled band inside a multi-band category would render as a
        gap with no explanation."""
        for category in CATEGORIES:
            if not category.get("groups"):
                continue
            for title, items in category_bands(category):
                assert title.strip(), (
                    f"{category['title']} has an untitled band")
                assert items, (
                    f"{category['title']} band {title!r} is empty")

    def test_no_band_is_a_wall(self):
        """The defect bands exist to fix. Twelve undifferentiated cards was
        the starting point; a band that grows back to that size has simply
        moved the wall down a level."""
        for category in CATEGORIES:
            for title, items in category_bands(category):
                assert len(items) <= 8, (
                    f"{category['title']} band {title!r} has {len(items)} "
                    "cards — split it rather than letting a band become the "
                    "wall it replaced")

    def test_operation_count_sees_through_bands(self):
        for category in CATEGORIES:
            assert category_operations(category) >= len(category_items(category))


@pytest.mark.parametrize("index", range(len(CATEGORIES)))
def test_band_headers_die_with_their_cards(qapp, index):
    """A header survives only while one of its OWN cards is visible.

    Filtering to a state no card on the page reports must leave NO headers
    behind: a surviving title over the next band's cards actively
    mislabels them, which is worse than the undifferentiated grid.
    """
    page = CategoryPage(CATEGORIES[index], TH.ThemeManager().t)
    page.resize(1200, 800)

    # isHidden(), NOT isVisible(): the page is never shown in a headless
    # run, and isVisible() is False for every child of an unshown parent —
    # which would make the orphan assertion below pass for the wrong
    # reason, on a page where every header actually survived. isHidden()
    # reports the widget's OWN explicit state, which is what is under test.
    def shown(widget) -> bool:
        return not widget.isHidden()

    # every card starts unbadged, so "Action due" matches nothing anywhere
    due_index = [key for _label, key in CategoryPage.FILTERS].index("due")
    page._filter.setCurrentIndex(due_index)
    qapp.processEvents()

    headers = [h for h, _cards in page._bands if h is not None]
    orphans = [h for h in headers if shown(h)]
    assert not orphans, (
        f"{CATEGORIES[index]['title']}: {len(orphans)} band header(s) "
        "survived a filter that hid every card beneath them")
    assert shown(page._empty), "the filtered-empty state was not shown"
    assert not any(shown(c) for c in page.cards), "a card survived the filter"

    page._filter.setCurrentIndex(0)
    qapp.processEvents()
    assert all(shown(h) for h in headers), (
        "clearing the filter did not bring every band header back")
    assert not shown(page._empty), "the empty state outlived the filter"


def test_band_headers_are_themed_in_both_modes(qapp):
    """A band header is built from plain QLabel/QFrame, so an un-styled one
    renders in the platform palette — white text on white in light mode."""
    theme = TH.ThemeManager()
    for _ in range(2):
        page = CategoryPage(CATEGORIES[1], theme.t)   # System & Tweaks: banded
        for header, _cards in page._bands:
            assert header is not None
            title = header.findChild(QLabel, "bandTitle")
            rule = header.findChild(QFrame, "bandRule")
            assert title is not None and title.styleSheet(), (
                f"band title unstyled in {theme.t['name']} mode")
            assert rule is not None and rule.styleSheet(), (
                f"band rule unstyled in {theme.t['name']} mode")
        theme.toggle()


# ============================================================
#  SPARSE MODE
# ============================================================
def test_sparse_mode_is_dormant(qapp):
    """SPARSE_MAX_CARDS dropped to 2 because the only page it still caught
    was one it was never designed for. If a page falls to 2 cards later
    this test is the prompt to look at the centred layout again on
    purpose, rather than discovering it in a screenshot."""
    caught = [c["title"] for c in CATEGORIES
              if len(category_items(c)) <= CategoryPage.SPARSE_MAX_CARDS]
    assert not caught, (
        f"sparse mode now applies to {caught} — confirm the centred, "
        "width-capped composition is really what those pages want")


def test_sparse_columns_would_share_one_width(qapp):
    """The shared unit is measured off sizeHint, not minimumSizeHint: the
    minimum is what a card can be SQUEEZED to (~214px with its description
    wrapped hard), which is not what an unstretched column resolves to.
    Measuring the wrong one shipped a 526px tile beside a 430px one."""
    page = CategoryPage(CATEGORIES[0], TH.ThemeManager().t)
    page.resize(1400, 800)
    qapp.processEvents()
    unit = page._sparse_unit()
    widest = max(c.sizeHint().width() for c in page.cards)
    assert unit >= widest, (
        f"sparse unit {unit} is under the widest card's sizeHint {widest} — "
        "columns would resolve to different widths")
    assert unit >= CategoryPage.SPARSE_CARD_W


# ============================================================
#  DIALOG STANDARDS
# ============================================================
#: Every dialog the app can open, with the arguments its constructor needs.
#: Kept explicit rather than discovered by walking PulseDialog subclasses:
#: a discovered list silently shrinks to nothing if the base class is
#: renamed, and would then pass while testing zero dialogs.
def _dialog_specs(window):
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
            window, item, t, MS.SOFTWARE_CATALOG, MS.CATALOG_BUNDLES,
            MS.CATALOG_BUNDLE_SECTION)),
        ("CommandPalette", lambda: W.CommandPalette(
            window, t, list(MS.iter_leaf_items()))),
        ("ShortcutSheetDialog", lambda: W.ShortcutSheetDialog(
            window, t, [("Ctrl+K", "Search")])),
        ("ElevatePromptDialog", lambda: W.ElevatePromptDialog(window, item, t)),
        ("CloseConfirmDialog", lambda: W.CloseConfirmDialog(window, t, "Demo")),
        ("PowerHealthDialog", lambda: W.PowerHealthDialog(window, "", t)),
        ("RestorePointDialog", lambda: W.RestorePointDialog(window, "", t)),
        ("StorageAnalyzerDialog", lambda: W.StorageAnalyzerDialog(window, "", t)),
    ]


#: The app's own minimum window size. A dialog is opened INSIDE this, so a
#: panel wider than it is a panel hanging off the window.
_MIN_W, _MIN_H = 752, 620

#: Mirrors _dialog_specs' keys. Declared separately because parametrize is
#: evaluated at COLLECTION time, before the `window` fixture that
#: _dialog_specs needs exists.
_DIALOG_NAMES = [
    "ConfirmDialog", "HubDialog", "SoftwareCatalogDialog", "CommandPalette",
    "ShortcutSheetDialog", "ElevatePromptDialog", "CloseConfirmDialog",
    "PowerHealthDialog", "RestorePointDialog", "StorageAnalyzerDialog",
]


def test_the_dialog_roster_is_complete(window):
    """Keeps _DIALOG_NAMES honest against _dialog_specs — a name added to
    one and not the other would silently stop testing a dialog."""
    assert sorted(_DIALOG_NAMES) == sorted(n for n, _b in _dialog_specs(window))


@pytest.mark.parametrize("name", _DIALOG_NAMES)
def test_dialog_panel_fits_the_minimum_window(window, qapp, name):
    """THE guard that would have caught the Software Catalog regression.

    A responsive panel takes its width from a content floor that OVERRIDES
    both its own cap and the host window (see widgets._content_width_floor),
    so a single wide row — five labelled tabs, three bundle buttons — can
    drag the whole dialog wider than the app. Nothing raises; the panel
    simply hangs off the window.
    """
    from frontend.widgets import refit_dialog

    original = window.size()
    window.resize(_MIN_W, _MIN_H)
    qapp.processEvents()
    try:
        spec = dict(_dialog_specs(window))
        dialog = spec[name]()
        dialog.resize(window.size())
        dialog.show()
        qapp.processEvents()
        refit_dialog(dialog)
        qapp.processEvents()

        panel = getattr(dialog, "panel", None)
        assert panel is not None, f"{name} did not build a chrome panel"
        floor = panel.layout().minimumSize().width() if panel.layout() else 0
        assert floor <= _MIN_W, (
            f"{name}'s content floor is {floor}px against a {_MIN_W}px "
            "minimum window — wrap wide button rows in widgets._chip_strip "
            "so the row reports a small minimum and scrolls instead")
        dialog.reject()
        dialog.deleteLater()
        qapp.processEvents()
    finally:
        window.resize(original)
        qapp.processEvents()


def test_every_dialog_uses_the_shared_chrome(window, qapp):
    """`panel` is what _dialog_chrome installs. A dialog without one has
    hand-rolled its own frame and will drift from the rest of the app."""
    missing = []
    for name, build in _dialog_specs(window):
        dialog = build()
        if getattr(dialog, "panel", None) is None:
            missing.append(name)
        dialog.reject()
        dialog.deleteLater()
    qapp.processEvents()
    assert not missing, f"dialogs not built on _dialog_chrome: {missing}"


def test_filtering_dialogs_declare_an_empty_state(window, qapp):
    """A surface that can filter itself to nothing must SAY so. A blank
    bordered box is indistinguishable from a broken dialog — the defect
    the command palette shipped with until Phase 0."""
    for name in ("SoftwareCatalogDialog", "CommandPalette"):
        dialog = dict(_dialog_specs(window))[name]()
        assert getattr(dialog, "_empty", None) is not None, (
            f"{name} can filter to zero results but has no empty state")
        dialog.reject()
        dialog.deleteLater()
    qapp.processEvents()
