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
import sys

import pytest

from utils import resources


@pytest.fixture
def frozen_onefile(monkeypatch, tmp_path):
    """Pose as a PyInstaller ONEFILE bundle, with the real geometry.

    The extraction directory is a `_MEIxxxxxx` folder inside a user-writable
    parent (production uses %TEMP%; tmp_path stands in so the test never
    touches the real one) and the executable lives elsewhere.

    REPO_ROOT / SRC_DIR ARE PATCHED TOO, and that is the load-bearing part.
    They are module-level constants derived from resources.__file__ at import
    time, so leaving them pointing at the developer's checkout would make
    these tests pass for a reason that does not exist in a shipped build —
    the planted files would simply be somewhere the resolver was never going
    to look. In a real onefile bundle __file__ lives under _MEIPASS, which is
    exactly what makes the extraction parent (%TEMP%) become REPO_ROOT and is
    the whole hazard under test. So they are set to the values the bundle
    would actually produce:

        _UTILS_DIR = <_MEIPASS>/utils   (the synthetic __file__'s directory)
        SRC_DIR    = <_MEIPASS>
        REPO_ROOT  = <_MEIPASS>/..      = the user-writable parent
    """
    meipass = tmp_path / "_MEI123456"
    meipass.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable",
                        str(tmp_path / "install" / "Pulse.exe"), raising=False)
    monkeypatch.setattr(resources, "SRC_DIR", str(meipass))
    monkeypatch.setattr(resources, "REPO_ROOT", str(tmp_path))
    return tmp_path, meipass


class TestFrozenRootsExcludeTheExtractionParent:
    """The v1.0 security fix, pinned.

    REPO_ROOT/SRC_DIR are derived from resources.__file__. In a onefile
    bundle that made the extraction directory's PARENT — %TEMP%, which any
    process running as the user can write to — a "bundled" root, and
    therefore a fallback location for the elevated engine and a live source
    of playbooks. If these tests fail, that hole is open again.
    """

    def test_frozen_bundled_roots_are_only_the_bundle(self, frozen_onefile):
        temp_parent, meipass = frozen_onefile
        roots = resources.bundled_roots()
        assert roots == [str(meipass)]
        assert str(temp_parent) not in roots

    def test_frozen_bundled_roots_exclude_the_extraction_parent(
            self, frozen_onefile):
        """REPO_ROOT is the dangerous one, and the only one worth asserting.

        SRC_DIR deliberately is NOT checked: in a real bundle it resolves to
        _MEIPASS itself, so "SRC_DIR must be absent" would be a contradiction
        rather than a guarantee. REPO_ROOT is its parent — the user-writable
        directory that must never be searched.
        """
        roots = resources.bundled_roots()
        assert resources.REPO_ROOT not in roots
        assert resources.SRC_DIR == getattr(sys, "_MEIPASS")

    def test_frozen_user_roots_add_only_the_executable_directory(
            self, frozen_onefile):
        temp_parent, meipass = frozen_onefile
        roots = resources.user_roots()
        assert roots == [os.path.dirname(sys.executable), str(meipass)]
        # The whole point: the extraction parent is not searched for
        # playbooks, which resource_dirs() would otherwise MERGE rather than
        # skip once the bundle's own copy was found.
        assert str(temp_parent) not in roots

    def test_frozen_engine_lookup_cannot_land_outside_the_bundle(
            self, frozen_onefile):
        """A planted engine beside the extraction dir must not resolve."""
        temp_parent, meipass = frozen_onefile
        planted = temp_parent / "src" / "backend"
        planted.mkdir(parents=True)
        (planted / "core.ps1").write_text("# planted", encoding="utf-8")

        found = resources.find_resource("src/backend/core.ps1")
        assert found is None, (
            "an engine written next to the extraction directory resolved")

    def test_frozen_playbook_dirs_cannot_include_the_extraction_parent(
            self, frozen_onefile):
        temp_parent, meipass = frozen_onefile
        (temp_parent / "playbooks").mkdir()
        dirs = resources.resource_dirs("playbooks",
                                       roots=resources.user_roots())
        assert str(temp_parent / "playbooks") not in dirs

    def test_frozen_without_meipass_falls_back_to_the_executable_dir(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable",
                            str(tmp_path / "app" / "Pulse.exe"), raising=False)
        assert resources.bundled_roots() == [str(tmp_path / "app")]


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
