"""
Version parsing and comparison (v10.3).

This module is small and it decides whether a user is offered a download,
so it gets pinned properly. Every case below is one the repository can
actually produce: its own tags are `v1.0.0` and `v6.1.0` while the app
reported `10.3`, so the updater's very first comparison in the wild is a
ragged, v-prefixed, two-against-three-component one.
"""
from __future__ import annotations

import pytest

from utils import version as V


# ============================================================
#  PARSING
# ============================================================
@pytest.mark.parametrize("text, expected", [
    ("10.3.0",          (10, 3, 0)),
    ("v10.3.0",         (10, 3, 0)),
    ("V10.3.0",         (10, 3, 0)),
    ("  v10.3.0  ",     (10, 3, 0)),
    # Two components zero-fill rather than staying ragged. A (10, 3) tuple
    # compares GREATER than (10, 3, 0), so a two-component running version
    # would never be offered its own patch release.
    ("10.3",            (10, 3, 0)),
    ("v6.1",            (6, 1, 0)),
    ("10",              (10, 0, 0)),
    # Extra components are dropped, not folded in.
    ("10.3.0.4",        (10, 3, 0)),
    # Prerelease and build metadata end the parse instead of raising —
    # this runs on a background thread during a silent update check.
    ("v11.0.0-rc1",     (11, 0, 0)),
    ("10.4.0-beta.2",   (10, 4, 0)),
    ("10.3.0+build17",  (10, 3, 0)),
])
def test_parse_normalises_to_three_components(text, expected):
    assert V.parse(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "v", "latest", "nightly", "..",
                                  "vNext", None])
def test_unparseable_versions_are_the_lowest_possible(text):
    """(0, 0, 0) can never win a comparison, so a malformed or moving tag
    is ignored rather than acted on. The alternative — raising — would put
    a traceback in a thread whose entire contract is to fail silently."""
    assert V.parse(text) == (0, 0, 0)
    assert not V.is_newer(text)


# ============================================================
#  COMPARISON — the two bugs that both ship a downgrade
# ============================================================
def test_string_ordering_would_have_offered_a_downgrade():
    """The regression this replaces, stated as a test: compared as strings,
    the repo's newest tag beats the running build and the updater walks
    every user from 10.3.0 back to 6.1.0."""
    assert "10.3.0" < "v6.1.0"                    # the naive comparison
    assert not V.is_newer("v6.1.0", "10.3.0")     # the real one


def test_a_patch_release_is_offered_to_a_two_component_build():
    """The ragged-tuple bug: (10, 3) > (10, 3, 0), so an app reporting
    "10.3" would decline its own patch."""
    assert V.is_newer("10.3.1", "10.3")


@pytest.mark.parametrize("candidate, current", [
    ("10.4.0",  "10.3.0"),
    ("11.0.0",  "10.9.9"),
    ("10.3.1",  "10.3.0"),
    ("10.10.0", "10.9.0"),   # decimal ordering, not lexicographic
])
def test_newer_versions_are_offered(candidate, current):
    assert V.is_newer(candidate, current)


@pytest.mark.parametrize("candidate, current", [
    ("10.3.0",  "10.3.0"),   # equal — never re-offer the running build
    ("10.2.9",  "10.3.0"),
    ("9.9.9",   "10.0.0"),
    ("10.9.0",  "10.10.0"),
])
def test_older_or_equal_versions_are_not_offered(candidate, current):
    assert not V.is_newer(candidate, current)


def test_equality_is_not_newer_even_across_spellings():
    """`v10.3` and `10.3.0` are the same build wearing two spellings. An
    updater that reads them as different is an update loop the user cannot
    escape by updating."""
    assert not V.is_newer("v10.3", "10.3.0")
    assert not V.is_newer("10.3.0", "v10.3")


# ============================================================
#  THE RUNNING BUILD
# ============================================================
def test_the_module_resolved_a_real_version_file():
    """If this fails, resources.find_resource stopped locating VERSION and
    every surface is quoting the fallback literal."""
    assert V.version_file_path() is not None
    assert V.VERSION_TUPLE == V.parse(V.VERSION)
    assert V.VERSION_TUPLE != (0, 0, 0)
