"""
src/utils/version.py

THE VERSION, AND THERE IS EXACTLY ONE OF IT (v10.3).

Pulse's version used to be a literal, copied. `APP_VERSION` sat in
main.py, `$Script:ScriptVersion` in core.ps1, and each carried a comment
asking the next person to keep them in lockstep. They drifted anyway —
both were pinned at 10.0 through the 10.1, 10.2 and 10.3 releases, so the
title bar, the sidebar footer and QApplication all reported a version no
changelog entry matched. tests/test_contract.py has caught that pair since,
but a test that compares two literals only proves they are equal; it does
not stop a third from being added.

Three more were about to be: the Inno Setup script names the output
`PULSE_Setup_vX.Y.Z.exe`, the PyInstaller spec stamps a Windows version
resource onto the exe, and the updater compares the running build against
a GitHub tag. Five copies of one fact, four of which the app cannot read
at runtime.

So the fact moved OUT of the code entirely, into `VERSION` at the repo
root — a file the GUI imports, PowerShell reads, Inno Setup preprocesses
and CI tags from. Nothing computes it; everything quotes it.

WHY A FALLBACK EXISTS
    `VERSION` ships inside the bundle (see main.spec's datas). If it is
    ever missing — a hand-assembled build, a partial copy — the honest
    options are to crash or to be approximately right about a decoration.
    Every other getter in utils/ takes the second (see prefs.py's "any
    corruption yields an empty map"), because a version string is used in
    a badge, a footer and an update check, and none of those is worth
    refusing to start over. The fallback is deliberately the same literal
    the file holds, and test_contract.py pins them together so it cannot
    quietly rot into a lie.

THE THREE-COMPONENT SCHEME
    Tags are `vMAJOR.MINOR.PATCH`. That is not cosmetic: the updater
    compares versions as integer tuples, and the repo's own history shows
    why the components have to be normalised before they are compared —
    the existing tags are `v1.0.0` and `v6.1.0` (three) while the app
    reported `10.3` (two). String comparison gets `"10.3" < "6.1.0"`
    exactly backwards and would have offered users a downgrade.
"""
from __future__ import annotations

import os
import re

from utils import resources

#: Read if `VERSION` cannot be found. Kept equal to the file's contents by
#: tests/test_contract.py — see "WHY A FALLBACK EXISTS" above.
_FALLBACK = "10.3.0"

#: Release channel — rendered as a badge, never in prose. Also decides
#: whether the updater considers GitHub prereleases (a stable build must
#: never be offered one).
CHANNEL = "Beta"


def _read() -> str:
    path = resources.find_resource("VERSION")
    if path:
        try:
            with open(path, encoding="utf-8-sig") as handle:
                text = handle.read().strip()
            if text:
                return text
        except OSError:
            pass        # unreadable file — same answer as a missing one
    return _FALLBACK


#: "10.3.0". The single string every surface quotes.
VERSION: str = _read()


def parse(text: str) -> tuple[int, int, int]:
    """`"v10.3"` / `"10.3.0"` / `"10.3.0-beta.1"` -> `(10, 3, 0)`.

    NORMALISED TO THREE COMPONENTS, which is the entire point. The
    comparison this feeds decides whether a user is offered an update, and
    the two mistakes available here both ship a downgrade:

        "10.3" < "6.1.0"        as strings  -> offers v6.1.0 over 10.3.0
        (10, 3) > (10, 3, 0)    ragged tuples -> never offers 10.3.1

    Missing components are zero-filled; extra ones are dropped; anything
    non-numeric ends the parse rather than raising, so a tag like
    `v11.0.0-rc1` compares as `(11, 0, 0)` instead of throwing inside a
    background update check. An unparseable string is `(0, 0, 0)` — the
    version that can never win a comparison, so a malformed tag is
    silently ignored rather than acted on.
    """
    parts: list[int] = []
    for chunk in re.split(r"[.\-+]", str(text).strip().lstrip("vV")):
        match = re.match(r"^(\d+)", chunk)
        if not match:
            break
        parts.append(int(match.group(1)))
        if len(parts) == 3:
            break
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


#: (10, 3, 0) — the running build, ready to compare.
VERSION_TUPLE: tuple[int, int, int] = parse(VERSION)


def is_newer(candidate: str, current: str = VERSION) -> bool:
    """Is `candidate` a version worth offering over `current`?

    Strictly greater, never equal — re-offering the running build is the
    most common updater bug there is, and it is indistinguishable from a
    broken update loop to the person clicking through it.
    """
    return parse(candidate) > parse(current)


def version_file_path() -> str | None:
    """Where `VERSION` was actually read from, or None if the fallback was
    used. Only the build tooling needs this; the app quotes VERSION."""
    return resources.find_resource("VERSION")


def repo_root_version_file() -> str:
    """The checkout's `VERSION`, whether or not it exists — the path the
    build script writes and reads. Distinct from version_file_path(),
    which answers "what did the RUNNING build load"."""
    return os.path.join(resources.REPO_ROOT, "VERSION")
