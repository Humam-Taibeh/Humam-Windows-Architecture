"""
One answer to "where does this file live" (v10.3, Phase 4).

Three functions used to re-derive the same layout ladder from their own
__file__ — main._locate_ps1, main._locate_icon, playbooks.playbook_dirs —
and the user's Desktop was recomputed inline in three more places. None of
them was wrong; all of them were free to drift the next time the layout
changed.

THE LOAD-BEARING PART is not the deduplication, it is the SPLIT. Playbooks
search the executable's own directory because adding one to an installed
copy is a supported workflow. core.ps1 deliberately does not: a file
droppable beside the exe that becomes the script Pulse runs elevated on
every task is exactly the hijack the v10.3 security pass closed elsewhere.
A future "tidy-up" that merges the two root sets would reopen it silently,
so the boundary is asserted here rather than left to the docstring.
"""
from __future__ import annotations

import os

from utils import resources


class TestRootSets:
    def test_bundled_roots_exist_and_are_ordered(self):
        roots = resources.bundled_roots()
        assert roots
        assert all(os.path.isabs(r) for r in roots)
        assert resources.REPO_ROOT in roots

    def test_user_roots_extend_bundled_roots(self):
        bundled = resources.bundled_roots()
        user = resources.user_roots()
        assert set(bundled).issubset(set(user))
        # order preserved: everything bundled keeps its relative sequence
        assert [r for r in user if r in bundled] == bundled

    def test_roots_are_deduplicated(self):
        for roots in (resources.bundled_roots(), resources.user_roots()):
            keys = [os.path.normcase(os.path.abspath(r)) for r in roots]
            assert len(keys) == len(set(keys)), f"duplicate root in {roots}"


class TestEngineIsNotUserOverridable:
    """The security boundary, asserted."""

    def test_engine_search_excludes_the_executable_directory(self, window):
        """_locate_ps1 must resolve through bundled roots only.

        If this fails because someone switched it to user_roots(), that is
        not a style regression — it means a core.ps1 written next to an
        installed Pulse would be executed, elevated, on every task.
        """
        import inspect

        from frontend.main import PulseApp
        source = inspect.getsource(PulseApp._locate_ps1)
        assert "user_roots" not in source, (
            "the engine is being searched for in user-writable roots")

    def test_engine_resolves_in_this_checkout(self, window):
        assert window.ps1_path is not None
        assert window.ps1_path.endswith("core.ps1")
        assert os.path.isfile(window.ps1_path)


class TestFindResource:
    def test_finds_a_real_bundled_file(self):
        found = resources.find_resource("src/backend/core.ps1")
        assert found and os.path.isfile(found)

    def test_missing_resource_returns_none_rather_than_raising(self):
        assert resources.find_resource("nope/does-not-exist.txt") is None

    def test_first_matching_candidate_wins(self):
        """Callers pass legacy layouts as later candidates; the current
        one has to win when both are present."""
        found = resources.find_resource(
            "src/backend/core.ps1", "core.ps1")
        assert found.replace("\\", "/").endswith("src/backend/core.ps1")

    def test_directories_are_not_returned_as_files(self):
        assert resources.find_resource("src") is None


class TestResourceDirs:
    def test_playbooks_directory_is_found(self):
        dirs = resources.resource_dirs("playbooks",
                                       roots=resources.user_roots())
        assert dirs, "the shipped playbooks/ directory was not found"
        assert all(os.path.isdir(d) for d in dirs)

    def test_only_existing_directories_are_returned(self):
        assert resources.resource_dirs("definitely-not-here") == []


class TestUserLocations:
    def test_desktop_is_under_the_user_profile(self):
        desktop = resources.desktop_dir()
        assert desktop.endswith("Desktop")
        assert desktop.startswith(os.path.expanduser("~"))

    def test_local_appdata_resolves(self):
        assert os.path.isabs(resources.local_appdata())


def test_playbooks_still_load_through_the_shared_resolver():
    """The behavioural end of the refactor: the shipped playbooks must
    still be found and parse cleanly."""
    from frontend.playbooks import load_playbooks, playbook_dirs

    assert playbook_dirs(), "no playbook directory resolved"
    books, errors = load_playbooks()
    assert not errors, f"shipped playbooks failed to validate: {errors}"
    assert len(books) >= 3, "the three shipped playbooks did not all load"
