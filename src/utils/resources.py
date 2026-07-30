"""
src/utils/resources.py

WHERE PULSE'S FILES LIVE (v10.3).

Pulse runs from three different layouts and has to find the same files in
all of them:

    a source checkout      repo/src/backend/core.ps1
    a PyInstaller bundle   <_MEIPASS>/src/backend/core.ps1
    an installed exe       <exe dir>/playbooks/*.json

Before this module, three separate functions each re-derived that ladder
from their own `__file__` — main._locate_ps1, main._locate_icon and
playbooks.playbook_dirs — and a fourth pattern (the user's Desktop) was
recomputed inline in three places. Four answers to two questions, none of
them wrong yet, all of them free to drift apart the next time the layout
changed.

TWO ROOT SETS, AND THE DIFFERENCE MATTERS
    `bundled_roots()` is what SHIPS with Pulse: the bundle's extraction
    directory and the source tree. `user_roots()` is that plus the
    directory the executable sits in, which a technician can write to.

    Those are deliberately not the same list, and the split is a security
    boundary rather than a tidiness one. Playbooks are meant to be
    user-extensible — dropping `workstation-standard.json` next to the exe
    is a supported workflow — so they search `user_roots()`. The ENGINE
    (core.ps1) does not: letting a file beside the executable win the
    lookup would mean anyone who can write to the install directory can
    replace the script Pulse runs, elevated, on every task. That is the
    same class of hijack the v10.3 pass closed for `powershell.exe` and
    the working directory, and it would be an odd thing to re-open here.
"""
from __future__ import annotations

import os
import sys

#: src/utils/resources.py -> src/utils -> src -> repo root
_UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(_UTILS_DIR)
REPO_ROOT = os.path.dirname(SRC_DIR)


def _dedupe(paths: list[str]) -> list[str]:
    """Order-preserving dedupe. The layouts overlap — running from source
    with a stale _MEIPASS set, for instance — and searching the same tree
    twice would only make the resolution order harder to reason about."""
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def bundled_roots() -> list[str]:
    """Trees that ship WITH Pulse, most specific first.

    Used for anything whose contents are part of the application: the
    PowerShell engine and the app icon.

    FROZEN BUILDS RETURN THE BUNDLE AND NOTHING ELSE (v1.0), and that is a
    security fix rather than a tidy-up. REPO_ROOT and SRC_DIR are derived
    from this module's own __file__, which inside a PyInstaller bundle is a
    synthetic path under the extraction directory — so in a ONEFILE build,
    where _MEIPASS is `%TEMP%\\_MEIxxxxxx`, the ladder resolved to:

        SRC_DIR   = %TEMP%\\_MEIxxxxxx
        REPO_ROOT = %TEMP%              <-- user-writable, and a "bundled" root

    That silently handed back the exact boundary this module's docstring
    promises to hold: `%TEMP%\\src\\backend\\core.ps1` became a fallback
    location for the elevated engine, and because resource_dirs() merges
    every match rather than stopping at the first, `%TEMP%\\playbooks\\*.json`
    joined the playbook list outright. Any process running as the user could
    write both.

    The source-checkout ladder is only meaningful when there IS a checkout,
    so it is now scoped to exactly that case.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        # No _MEIPASS on a frozen build is not a layout we ship, but the exe's
        # own directory is the only defensible answer if it ever happens —
        # never a path derived from __file__.
        return _dedupe([meipass or os.path.dirname(sys.executable)])
    return _dedupe([REPO_ROOT, SRC_DIR])


def user_roots() -> list[str]:
    """`bundled_roots()` plus the executable's own directory.

    ONLY for resources a user is invited to add to an installed copy —
    today that is playbooks, and nothing else should join it without the
    same argument being made explicitly. See the module docstring.
    """
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(sys.executable))
    return _dedupe(roots + bundled_roots())


def find_resource(*relative: str, roots: list[str] | None = None) -> str | None:
    """First existing file matching any `relative` path under any root.

    Roots are the outer loop, so a bundle's own copy always wins over a
    source tree that happens to be present too. Returns None rather than
    raising: every caller has a real answer for "not found" (a disabled
    engine chip, a default window icon) that is better than a traceback.
    """
    for root in (roots if roots is not None else bundled_roots()):
        for rel in relative:
            candidate = os.path.join(root, *rel.split("/"))
            if os.path.isfile(candidate):
                return candidate
    return None


def resource_dirs(name: str, roots: list[str] | None = None) -> list[str]:
    """EVERY existing directory called `name` across the roots, in order.

    Unlike find_resource this does not stop at the first hit: playbooks
    are merged across locations (with the earliest winning on an id
    clash), so the caller needs the whole ordered list.
    """
    found = [os.path.join(root, name)
             for root in (roots if roots is not None else bundled_roots())]
    return [path for path in found if os.path.isdir(path)]


# ============================================================
#  USER LOCATIONS
# ============================================================
def desktop_dir() -> str:
    """The user's Desktop — where Pulse's exports and backups default to.

    Not resolved through the shell's known-folder API on purpose: the
    backend writes its own backups to the literal `$env:USERPROFILE\\
    Desktop` (see 02-Safety.ps1), so the GUI has to agree with it. A
    redirected Desktop would move both or neither.
    """
    return os.path.join(os.path.expanduser("~"), "Desktop")


def local_appdata() -> str:
    """%LOCALAPPDATA%, with the same fallback the backend assumes."""
    return os.environ.get(
        "LOCALAPPDATA",
        os.path.join(os.path.expanduser("~"), "AppData", "Local"))
