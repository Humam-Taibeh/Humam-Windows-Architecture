"""
Static contracts between the GUI, the PowerShell backend, and the themes.

These are cheap, headless-safe, and catch the drift class of bug: a task
added to menu_structure.py with no dispatcher case is a card that fails at
click time, and a token present in one theme but not the other is a
KeyError the moment someone toggles.
"""
from __future__ import annotations

import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MENU = os.path.join(_ROOT, "src/frontend/menu_structure.py")
_DISPATCHER = os.path.join(_ROOT, "src/backend/modules/30-GuiDispatcher.ps1")
_PROBE = os.path.join(_ROOT, "src/backend/modules/11-StateProbe.ps1")

# Backend tasks the GUI invokes programmatically rather than from a card:
# state probes, wizard steps and the interactive panels' row actions.
_PROGRAMMATIC = {
    "GetTweakState", "ScanForUpdates", "InstallLocalFile",
    "InstallOfficeODTAuto", "StartupEnableItem", "StartupDisableItem",
    # v10.3: the Automation module's two cards are GUI-LOCAL ("@playbooks",
    # "@health_report") because neither is a backend action in its own
    # right — a playbook replays tasks that already have cases, and the
    # report is opened by a dialog. HealthReport is the one backend case
    # behind them, invoked by widgets.HealthReportDialog rather than by a
    # card, which is exactly what this allow-list is for.
    "HealthReport",
    # Same shape as HealthReport. The Safety & Recovery card is
    # GUI-LOCAL ("@activation") because the report is rendered by a dialog
    # that runs its own PowerShellTask — widgets.ActivationStatusDialog —
    # rather than through main.py's single-task pipeline.
    "ActivationStatus",
    # v1.0 two-way toggles: reached from the re-apply/revert choice dialog
    # via main._REVERT_TASKS (whose literal strings are what
    # test_programmatic_tasks_are_actually_referenced reads), never from a
    # card's own `task` value.
    "RevertDarkMode", "RevertDisableMouseAccel", "RevertMinimalistTaskbar",
    "RevertClassicContextMenu", "RevertGameMode", "RevertDisableTelemetry",
    "RevertDisableAdvertisingID", "RevertDisableActivityHistory",
    # v1.0+ Phase 1 read-only inspectors. Same shape as HealthReport and
    # ActivationStatus: the CARD is a GUI-local action ("@power_health",
    # "@restore_points") because the report is rendered by a dialog that
    # runs its own PowerShellTask, so these task names are reached from
    # widgets.py rather than from a card's `task`. (StorageScan is NOT
    # here — its card declares the task name directly, exactly like the
    # Startup Manager's StartupReport.)
    "PowerHealth", "RestorePoints",
    # v1.0+ Phase 2 DNS switcher. Same shape as the Startup Manager's
    # per-row actions: the CARD declares the read task (NetworkProfiles),
    # and these two mutations are fired per adapter from inside
    # widgets.DnsSwitcherDialog rather than from a card of their own.
    # Both are admin-gated in $Script:AdminRequiredTasks AND in
    # ADMIN_REQUIRED_TASKS — a revert must need exactly the rights its
    # counterpart needed.
    "SetDnsProfile", "RestoreDns",
    # v1.0+ Phase 2 context-menu manager. The card declares the read task
    # (ContextMenuScan); these two are fired per row from inside
    # widgets.ContextMenuDialog. Both admin-gated in both lists.
    "ContextMenuToggle", "ContextMenuRestore",
}


def _menu_source() -> str:
    return open(_MENU, encoding="utf-8").read()


def _dispatcher_cases() -> set[str]:
    src = open(_DISPATCHER, encoding="utf-8-sig").read()
    body = src[src.index("switch ($TaskName)"):]
    cases: set[str] = set()
    for match in re.finditer(
            r'^\s{8,}((?:"[A-Za-z0-9_]+"\s*,\s*)*"[A-Za-z0-9_]+")\s*\{',
            body, re.M):
        cases.update(re.findall(r'"([A-Za-z0-9_]+)"', match.group(1)))
    return cases


def _gui_tasks() -> set[str]:
    return set(re.findall(r'"task"\s*:\s*"([^"]+)"', _menu_source()))


def test_dispatcher_cases_were_parsed():
    """Guard the regex itself — a silently-empty set would pass every
    'no missing tasks' assertion below for the wrong reason."""
    assert len(_dispatcher_cases()) > 20


def test_every_gui_task_has_a_backend_case():
    """The contract 30-GuiDispatcher.ps1 documents: every `task` in
    menu_structure.py maps 1:1 to one switch case."""
    gui = {t for t in _gui_tasks() if not t.startswith("@")}
    missing = sorted(gui - _dispatcher_cases())
    assert not missing, f"GUI tasks with no dispatcher case: {missing}"


def test_no_unreachable_dispatcher_cases():
    """Every backend case is reachable — either from a card or from a
    known programmatic caller. A new orphan means dead backend code."""
    gui = {t for t in _gui_tasks() if not t.startswith("@")}
    orphans = sorted(_dispatcher_cases() - gui - _PROGRAMMATIC)
    assert not orphans, f"unreachable dispatcher cases: {orphans}"


def test_programmatic_tasks_are_actually_referenced():
    """Keeps the allow-list above honest — if one of these stops being
    called from Python it is dead code, not an exemption."""
    sources = []
    for folder in ("src/frontend", "src/utils"):
        base = os.path.join(_ROOT, folder)
        for name in os.listdir(base):
            if name.endswith(".py") and name != "menu_structure.py":
                sources.append(open(os.path.join(base, name),
                                    encoding="utf-8").read())
    blob = "\n".join(sources)
    unreferenced = sorted(t for t in _PROGRAMMATIC if f'"{t}"' not in blob)
    assert not unreferenced, f"allow-listed but never invoked: {unreferenced}"


def _probe_source() -> str:
    return open(_PROBE, encoding="utf-8-sig").read()


def _probe_keys() -> set[str]:
    """The task names 11-StateProbe.ps1 reports state for, read off the
    `$state["Name"]` assignments that build its return map."""
    return set(re.findall(r'\$state\["([A-Za-z0-9_]+)"\]\s*=', _probe_source()))


class TestStateProbe:
    """The probe's map is keyed by GUI TASK NAME so the frontend can look a
    card up with no translation table (11-StateProbe.ps1's own words). That
    only holds while the keys really are task names — a typo'd or renamed
    key doesn't raise anywhere, it just silently stops badging a card,
    which is invisible in exactly the way a missing badge always is."""

    def test_the_keys_were_actually_parsed(self):
        """Guard the regex: an empty set would make every check below pass
        for the wrong reason."""
        assert len(_probe_keys()) >= 15

    def test_every_probe_key_is_a_real_gui_task(self):
        gui = {t for t in _gui_tasks() if not t.startswith("@")}
        orphans = sorted(_probe_keys() - gui)
        assert not orphans, (
            f"probe reports state for non-existent task(s): {orphans} — "
            "either the task was renamed and the probe key was not, or the "
            "key is a typo that will never match a card")

    def test_every_probe_key_has_a_dispatcher_case(self):
        """A probe key naming a task the backend cannot run is incoherent
        even if a card happens to exist for it."""
        missing = sorted(_probe_keys() - _dispatcher_cases())
        assert not missing, f"probe keys with no dispatcher case: {missing}"

    def test_probe_covers_the_tasks_it_claims(self):
        """Pins the v10.1 coverage so a later refactor cannot quietly drop
        a probe. NetworkOptimization is deliberately NOT here: it flushes
        DNS and resets the Winsock/IP stack, which leaves no durable
        readable marker, so probing it could only ever be a guess."""
        expected = {
            "DarkMode", "DisableMouseAccel", "MinimalistTaskbar",
            "ClassicContextMenu", "GameMode", "DisableAdvertisingID",
            "DisableActivityHistory", "DisableTelemetry",
            "DisableHibernation", "EnableHibernation", "UltimatePowerPlan",
            "RemoveEdge", "RemoveOneDrive", "RemoveWindowsOld",
            "RemoveBloatware", "ApplyAllPrivacy",
        }
        assert expected <= _probe_keys(), (
            f"probe coverage regressed, missing: {sorted(expected - _probe_keys())}")

    def test_network_optimization_is_not_probed(self):
        """Its own guard, because the tempting thing to do is invent one.
        A card that claims 'Applied' for a transient stack reset would be
        actively misleading — worse than no badge at all."""
        assert "NetworkOptimization" not in _probe_keys(), (
            "NetworkOptimization has no durable readable state (ipconfig "
            "/flushdns, netsh winsock reset, netsh int ip reset). Any probe "
            "for it is a guess presented as a fact.")

    def test_state_probe_is_read_only(self):
        """The module's HARD contract: it runs on launch and after every
        task, so a mutating probe would silently re-apply tweaks behind
        the user's back. Static scan for the mutation primitives — cheap,
        and it fails at review time rather than on a user's machine.

        Also referenced from 11-StateProbe.ps1's own comment block as the
        thing pinning its reuse of 06-Tweaks.ps1's presence helpers.
        """
        source = _probe_source()
        # Strip comments: the module DESCRIBES what it must not do.
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#"))
        code = re.sub(r"<#.*?#>", "", code, flags=re.S)

        forbidden = [
            "Set-ItemProperty", "New-ItemProperty", "Remove-ItemProperty",
            "New-Item", "Remove-Item", "Set-Item",
            "Set-Service", "Stop-Service", "Start-Service",
            "Stop-Process", "Remove-AppxPackage",
            "Checkpoint-Computer", "New-SystemRestorePoint",
            "Set-Content", "Out-File", "reg add", "reg delete",
        ]
        found = sorted({c for c in forbidden if c.lower() in code.lower()})
        assert not found, (
            f"11-StateProbe.ps1 contains mutating call(s): {found}. This "
            "module is invoked on launch and after every task — a write "
            "here re-applies tweaks behind the user's back.")

    def test_reused_presence_helpers_still_exist(self):
        """The probe deliberately reuses 06-Tweaks.ps1's helpers instead of
        duplicating detection logic. If either is renamed, the probe's
        try/catch turns the failure into a silent 'unknown' rather than an
        error — so nothing would report the breakage but this."""
        tweaks = open(os.path.join(_ROOT, "src/backend/modules/06-Tweaks.ps1"),
                      encoding="utf-8-sig").read()
        for helper in ("Test-MicrosoftEdgeInstalled", "Test-OneDriveInstalled"):
            if helper in _probe_source():
                assert f"function {helper}" in tweaks, (
                    f"11-StateProbe.ps1 calls {helper}, which no longer "
                    "exists in 06-Tweaks.ps1 — the probe will silently "
                    "report 'unknown' forever")


class TestAppIcons:
    """The Software Management brand-icon contract.

    Icons are fetched at BUILD time by tools/fetch_app_icons.py and read
    from disk at runtime. That split is the whole security argument — an
    elevated, privacy-focused utility must not reach the network to draw
    its own UI — and nothing but this test prevents a future "just fetch
    the missing one" from erasing it.
    """

    @staticmethod
    def _runtime_source() -> str:
        return open(os.path.join(_ROOT, "src/utils/appicons.py"),
                    encoding="utf-8").read()

    def test_runtime_never_reaches_the_network(self):
        code = re.sub(r'""".*?"""', "", self._runtime_source(), flags=re.S)
        forbidden = ["urllib", "requests", "http://", "https://", "socket",
                     "QNetworkAccessManager", "urlopen"]
        found = sorted({name for name in forbidden if name in code})
        assert not found, (
            f"src/utils/appicons.py references {found} — icon resolution is "
            "offline by contract; fetching belongs in tools/fetch_app_icons.py")

    def test_manifest_matches_the_shipped_assets(self):
        """Every manifest entry names a file that exists, so a row can
        never resolve to a missing asset and silently fall through to the
        neutral glyph while claiming a brand mark."""
        manifest_path = os.path.join(_ROOT, "assets/appicons/manifest.json")
        assert os.path.isfile(manifest_path), "brand-icon manifest is missing"
        import json
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        assert manifest, "manifest is empty"
        missing = sorted(
            app_id for app_id, entry in manifest.items()
            if not os.path.isfile(
                os.path.join(_ROOT, "assets/appicons", entry.get("file", ""))))
        assert not missing, f"manifest entries with no asset file: {missing}"

    def test_no_letter_monogram_fallback_survives(self):
        """The defect this system replaced: a bare initial rendered in a
        tile where a logo belonged ("E" for Epic Games). If a monogram
        path ever returns, this fails rather than shipping it again."""
        code = self._runtime_source()
        assert "_monogram_pixmap" not in code
        assert "initial" not in code.lower().split('"""')[-1]

    def test_no_mark_is_fabricated(self):
        """THE provenance rule, and the reason the previous attempt was
        reverted: every bundled mark must be the vendor's real artwork,
        fetched from a curated brand-logo set. Pulse ships no hand-drawn
        stand-ins.

        A purpose-drawn pictogram ("a CPU die means CPU-Z") was tried for
        the seven apps with no open-licensed logo. It made every row a
        crisp vector, and it was still wrong: a mark that DESCRIBES
        software is not that software's logo, and shipping it in the same
        slot as real brand artwork invites the reader to assume it is one.
        A wrong logo is worse than no logo, and an invented one is worse
        than both.
        """
        import json
        manifest = json.load(open(
            os.path.join(_ROOT, "assets/appicons/manifest.json"),
            encoding="utf-8"))
        fabricated = sorted(a for a, e in manifest.items() if e.get("drawn"))
        assert not fabricated, (
            f"manifest still flags hand-drawn marks: {fabricated}")

        tool = open(os.path.join(_ROOT, "tools/fetch_app_icons.py"),
                    encoding="utf-8").read()
        assert "DRAWN_MARKS" not in tool, (
            "fetch_app_icons.py still declares a hand-drawn mark register")

    def test_every_bundled_mark_has_a_traceable_source(self):
        """Each mark names where it came from — a Simple Icons slug via
        ICON_MAP, or an Iconify brand-set id recorded as `source`. A mark
        with neither is an asset nobody can re-derive or verify."""
        import json
        manifest = json.load(open(
            os.path.join(_ROOT, "assets/appicons/manifest.json"),
            encoding="utf-8"))
        tool = open(os.path.join(_ROOT, "tools/fetch_app_icons.py"),
                    encoding="utf-8").read()
        icon_body = tool[tool.index("ICON_MAP"):tool.index("def _get(")]
        mapped = set(re.findall(r'^\s{4}"([^"]+)":', icon_body, re.M))

        untraceable = sorted(
            app_id for app_id, entry in manifest.items()
            if not entry.get("source") and app_id not in mapped)
        assert not untraceable, (
            f"bundled marks with no recorded provenance: {untraceable}")

    def test_colour_and_silhouette_marks_are_classified_correctly(self):
        """`color: true` means "render as drawn"; its absence means
        "recolour through the contrast guard". Getting this backwards is
        silent and ugly in opposite directions — a gradient logo flattened
        to one blob, or a `currentColor` silhouette rendered as pure black
        on obsidian with a rescue plaque bolted behind it.
        """
        import json
        manifest = json.load(open(
            os.path.join(_ROOT, "assets/appicons/manifest.json"),
            encoding="utf-8"))
        wrong = []
        for app_id, entry in manifest.items():
            path = os.path.join(_ROOT, "assets/appicons", entry["file"])
            body = open(path, encoding="utf-8", errors="ignore").read()
            uses_current = "currentColor" in body
            if entry.get("color") and uses_current:
                wrong.append(f"{app_id}: flagged colour but uses currentColor")
            if not entry.get("color") and not uses_current and "source" in entry:
                wrong.append(f"{app_id}: full-colour artwork not flagged colour")
        assert not wrong, "mark classification is wrong:\n  " + "\n  ".join(wrong)

    def test_silhouette_marks_carry_a_brand_hex(self):
        """A recoloured mark needs a colour to be recoloured TO."""
        import json
        manifest = json.load(open(
            os.path.join(_ROOT, "assets/appicons/manifest.json"),
            encoding="utf-8"))
        missing = sorted(a for a, e in manifest.items()
                         if not e.get("color") and not e.get("hex"))
        assert not missing, f"silhouette marks with no brand hex: {missing}"

    def test_uncovered_apps_are_documented_not_forgotten(self):
        """The seven apps with no authentic mark must remain a stated,
        explained gap in the fetch tool rather than a silent one."""
        from frontend.menu_structure import catalog_app_ids
        import json
        manifest = json.load(open(
            os.path.join(_ROOT, "assets/appicons/manifest.json"),
            encoding="utf-8"))
        uncovered = [a for a in catalog_app_ids() if a not in manifest]
        tool = open(os.path.join(_ROOT, "tools/fetch_app_icons.py"),
                    encoding="utf-8").read()
        undocumented = sorted(a for a in uncovered
                              if a.split(".")[0] not in tool)
        assert not undocumented, (
            f"apps with no bundled mark and no explanation: {undocumented}")


    def test_every_mapped_app_is_a_real_catalog_entry(self):
        """tools/fetch_app_icons.py's map is keyed by winget AppId. A key
        that matches no catalog app is a typo that silently downloads an
        asset nothing will ever read."""
        tool = open(os.path.join(_ROOT, "tools/fetch_app_icons.py"),
                    encoding="utf-8").read()
        body = tool[tool.index("ICON_MAP"):tool.index("def _get(")]
        mapped = set(re.findall(r'^\s{4}"([^"]+)":', body, re.M))
        assert len(mapped) > 30, "ICON_MAP did not parse"

        menu = _menu_source()
        dev_hub_ids = set(re.findall(r'^\s+\("([^"]+)",', menu, re.M))
        app_ids = set(re.findall(r'\("([^"]+)",\s*"[^"]*",', menu))
        catalog = dev_hub_ids | app_ids
        orphans = sorted(mapped - catalog)
        assert not orphans, (
            f"ICON_MAP names app id(s) no catalog entry uses: {orphans}")


_CATALOGS = os.path.join(_ROOT, "src/backend/modules/01-Catalogs.ps1")


def _backend_admin_tasks() -> set[str]:
    """$Script:AdminRequiredTasks, the backend's own elevation gate."""
    src = open(_CATALOGS, encoding="utf-8-sig").read()
    start = src.index("$Script:AdminRequiredTasks")
    body = src[start:src.index(")", start)]
    return set(re.findall(r'"([A-Za-z0-9_]+)"', body))


class TestRevertToggles:
    """The v1.0 two-way toggle contract. A revert is the inverse of one
    specific apply, and the two must agree about elevation and about
    existing at all — a card offering 'Revert to Default' that dead-ends
    in an access-denied (or in no dispatcher case) is worse than no
    toggle, because the user has been told the undo exists."""

    @staticmethod
    def _revert_map() -> dict[str, str]:
        main = open(os.path.join(_ROOT, "src/frontend/main.py"),
                    encoding="utf-8").read()
        start = main.index("_REVERT_TASKS: dict[str, str] = {")
        body = main[start:main.index("}", start)]
        return dict(re.findall(r'"([A-Za-z0-9_]+)"\s*:\s*"([A-Za-z0-9_]+)"',
                               body))

    def test_the_map_was_parsed(self):
        assert len(self._revert_map()) >= 8

    def test_every_revert_has_a_dispatcher_case(self):
        missing = sorted(set(self._revert_map().values()) - _dispatcher_cases())
        assert not missing, f"revert tasks with no dispatcher case: {missing}"

    def test_every_apply_counterpart_is_a_real_gui_task(self):
        gui = {t for t in _gui_tasks() if not t.startswith("@")}
        orphans = sorted(set(self._revert_map()) - gui)
        assert not orphans, (
            f"_REVERT_TASKS keys that are not GUI tasks: {orphans} — the "
            "toggle would never trigger")

    def test_revert_admin_gating_matches_apply(self):
        """A revert writes the same hives its apply wrote, so it needs the
        same rights. Mismatch in either direction is a bug: under-gating
        reaches HKLM and fails with access-denied instead of prompting to
        elevate; over-gating raises a UAC prompt to undo an HKCU setting
        the session already owns."""
        backend = _backend_admin_tasks()
        wrong = []
        for apply_task, revert_task in sorted(self._revert_map().items()):
            if (apply_task in backend) != (revert_task in backend):
                wrong.append(
                    f"{apply_task}={'admin' if apply_task in backend else 'user'} "
                    f"but {revert_task}="
                    f"{'admin' if revert_task in backend else 'user'}")
        assert not wrong, (
            "revert/apply elevation mismatch in $Script:AdminRequiredTasks "
            "(01-Catalogs.ps1):\n  " + "\n  ".join(wrong))

    def test_reverts_are_not_offered_for_irreversible_tasks(self):
        """The exclusion list is a safety property, not an oversight. A
        removal is undone by REINSTALLING (its own deliberate action, with
        its own download), the hibernation pair are each other's inverse
        already, a power plan switch is a choice rather than a tweak, and
        NetworkOptimization is transient with nothing to restore."""
        forbidden = {
            "RemoveEdge", "RemoveOneDrive", "RemoveWindowsOld",
            "RemoveBloatware", "DisableHibernation", "EnableHibernation",
            "UltimatePowerPlan", "NetworkOptimization", "ApplyAllPrivacy",
        }
        offered = sorted(forbidden & set(self._revert_map()))
        assert not offered, (
            f"click-to-revert offered for irreversible/inapplicable "
            f"task(s): {offered}")


def test_local_actions_are_marked_with_an_at_sign():
    local = {t for t in _gui_tasks() if t.startswith("@")}
    assert local, "the '@' convention for GUI-local actions has vanished"
    assert not (local & _dispatcher_cases())


def test_every_local_action_is_handled_by_the_gui():
    """The '@' convention's other half. A local action has no dispatcher
    case to catch it, so a card whose task main.py does not handle falls
    through to _run_local_action's path lookup and reports 'Unknown local
    action' at click time — the exact failure the task/case parity check
    above prevents for backend tasks."""
    main = open(os.path.join(_ROOT, "src/frontend/main.py"), encoding="utf-8").read()
    handler = main[main.index("def _run_local_action"):]
    unhandled = sorted(t for t in _gui_tasks()
                       if t.startswith("@") and f'"{t}"' not in handler)
    assert not unhandled, f"local actions with no handler in main.py: {unhandled}"


def test_every_menu_glyph_exists_in_the_icon_map(qapp):
    """A card's `glyph` is looked up with GLYPHS.get(name, ("", "")), which
    means a typo'd or newly-invented name renders a BLANK icon plaque
    rather than raising — invisible in exactly the way a missing icon
    always is."""
    from frontend import theme as TH
    names = set(re.findall(r'"glyph"\s*:\s*"([^"]+)"', _menu_source()))
    assert names, "no glyphs found to check"
    missing = sorted(names - set(TH.GLYPHS))
    assert not missing, f"menu glyphs absent from theme.GLYPHS: {missing}"


_ACTIVATION = os.path.join(_ROOT, "src/backend/modules/13-Activation.ps1")


def test_activation_module_is_read_only():
    """13-Activation.ps1's hard contract, and the whole point of the
    module: it REPORTS licence state and never changes it. The same static
    scan TestStateProbe applies to the tweak probe, plus the licensing
    tools specifically — a module that could activate would make every
    reassurance in its own header false.
    """
    source = open(_ACTIVATION, encoding="utf-8-sig").read()
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    code = re.sub(r"<#.*?#>", "", code, flags=re.S)

    forbidden = [
        # generic mutation primitives
        "Set-ItemProperty", "New-ItemProperty", "Remove-ItemProperty",
        "New-Item", "Remove-Item", "Set-Item", "Set-Service",
        "Set-Content", "Out-File", "reg add", "reg delete",
        # Licensing-specific: the calls that would CHANGE activation state.
        # Named precisely (ActivateProduct, not "Activate") because the
        # module legitimately says "activated", "re-activated" and "Not
        # activated" all over its own status strings — a loose substring
        # here would fail on the report's vocabulary instead of its calls.
        "slmgr", "ospp", "InstallProductKey", "ActivateProduct",
        "SetKeyManagementServiceMachine", "Invoke-CimMethod",
        "Invoke-WebRequest", "Invoke-Expression", "Start-Process",
    ]
    found = sorted({c for c in forbidden if c.lower() in code.lower()})
    assert not found, (
        f"13-Activation.ps1 contains state-changing or remote-code call(s): "
        f"{found}. This module is a read-only report; activation is Windows' "
        "own job, reached through the Settings deep link in the dialog.")


_INSPECTORS = os.path.join(_ROOT, "src/backend/modules/14-Inspectors.ps1")


class TestInspectorsAreReadOnly:
    """14-Inspectors.ps1's hard contract (v1.0+ Phase 1).

    Battery health, restore points and the storage scan all promise the
    same thing in their headers: they read and format, and change nothing.
    Two of them are one keyword away from being genuinely destructive — a
    storage analyzer knows exactly where the biggest files are, and a
    restore-point browser sits beside the API that reverts a machine — so
    the promise is asserted rather than trusted.
    """

    @staticmethod
    def _code() -> str:
        source = open(_INSPECTORS, encoding="utf-8-sig").read()
        code = "\n".join(line for line in source.splitlines()
                         if not line.lstrip().startswith("#"))
        return re.sub(r"<#.*?#>", "", code, flags=re.S)

    def test_the_module_was_parsed(self):
        assert "Get-PulseStorageScan" in self._code(), "the scan didn't parse"

    def test_no_mutation_primitives(self):
        forbidden = [
            "Set-ItemProperty", "New-ItemProperty", "Remove-ItemProperty",
            "New-Item", "Remove-Item", "Set-Item", "Set-Service",
            "Set-Content", "Out-File", "Move-Item", "Copy-Item",
            "Clear-Content", "reg add", "reg delete",
            "Invoke-WebRequest", "Invoke-Expression", "Start-Process",
        ]
        code = self._code().lower()
        found = sorted({c for c in forbidden if c.lower() in code})
        assert not found, (
            f"14-Inspectors.ps1 contains state-changing call(s): {found}. "
            "These are REPORTS; the actions they suggest belong to the "
            "Windows surfaces the dialogs hand off to.")

    def test_storage_scan_cannot_delete(self):
        """The decision recorded in the roadmap: Pulse finds the space, it
        does not free it. Explorer owns the delete, with its own undo, its
        own confirm and the Recycle Bin."""
        code = self._code().lower()
        for call in ("remove-item", "[io.file]::delete", "recycle"):
            assert call not in code, (
                f"the storage analyzer references {call!r} — it is strictly "
                "read-only and hands paths to Explorer instead")

    def test_restore_browser_cannot_roll_back(self):
        """Listing checkpoints is a report; performing a System Restore is
        a reboot-time operation owned by Microsoft's signed wizard."""
        code = self._code().lower()
        for call in ("restore-computer", "enable-computerrestore",
                     "disable-computerrestore", "checkpoint-computer"):
            assert call not in code, (
                f"14-Inspectors.ps1 calls {call!r} — the browser reports "
                "checkpoints and launches rstrui.exe; it never restores.")

    def test_power_inspector_does_not_change_plans(self):
        """Ultimate Power Plan (06-Tweaks) is what CHANGES a scheme. If the
        inspector could too, the two would disagree about current state."""
        code = self._code().lower()
        for call in ("powercfg", "/setactive", "set-ciminstance"):
            assert call not in code, (
                f"14-Inspectors.ps1 references {call!r} — it reports the "
                "active plan; changing one belongs to the tweak module.")


def test_inspector_dialogs_never_delete(qapp):
    """The GUI half of the same contract. A 'Reveal' that quietly became a
    'Delete' would be invisible to the backend scan above."""
    source = open(os.path.join(_ROOT, "src/frontend/widgets.py"),
                  encoding="utf-8").read()
    start = source.index("class StorageAnalyzerDialog")
    end = source.index("class CloseConfirmDialog")
    body = source[start:end]
    for call in ("os.remove", "shutil.rmtree", "os.unlink", "send2trash",
                 "QFile.remove"):
        assert call not in body, (
            f"StorageAnalyzerDialog references {call} — it reveals paths in "
            "Explorer and never removes anything itself")
    # and the reveal must be anchored, not a PATH search
    assert "explorer.exe" in body and "SystemRoot" in body, (
        "the Explorer hand-off must use an absolute System32-anchored path")


def test_activation_dialog_hands_off_to_settings_only():
    """The frontend half of the activation contract (v1.0). The dialog's
    one actionable hand-off is Windows' own Settings page, opened as a URI
    through QDesktopServices — never a spawned process, never a script.
    Pinned because this is the zero-bloat promise most tempting to erode:
    'just run slmgr for the user' is one convenient commit away.
    """
    from frontend.widgets import ActivationStatusDialog

    assert ActivationStatusDialog.SETTINGS_URI == "ms-settings:activation"
    assert ActivationStatusDialog.DOCS_URL.startswith(
        "https://support.microsoft.com/")

    # The dialog's implementation must not grow a process spawn. Scoped to
    # the class body so the rest of widgets.py (which legitimately spawns
    # PowerShell workers) stays out of scope.
    widgets_src = open(os.path.join(_ROOT, "src/frontend/widgets.py"),
                       encoding="utf-8").read()
    start = widgets_src.index("class ActivationStatusDialog")
    end = widgets_src.index("\nclass ", start + 1)
    body = widgets_src[start:end]
    for spawn in ("subprocess.", "os.system", "os.startfile", "Popen("):
        assert spawn not in body, (
            f"ActivationStatusDialog gained a process spawn ({spawn!r}) — "
            "its contract is report + ms-settings hand-off only")


_BACKEND_DIR = os.path.join(_ROOT, "src/backend")

#: Stock tools that must never be invoked by bare name from an elevated
#: process. Each has an anchored path behind Get-SystemBinary
#: (00-Foundation.ps1); winget is the exception and goes through
#: Get-WingetPath (03-Environment.ps1) because it is an app-execution
#: alias rather than a System32 binary.
_ANCHORED_TOOLS = (
    "powershell", "pwsh", "explorer", "taskmgr", "cmd", "winget",
    "msiexec", "ie4uinit", "rundll32", "regsvr32", "sc", "reg", "schtasks",
)


def _backend_files():
    for root, _dirs, names in os.walk(_BACKEND_DIR):
        for name in sorted(names):
            if name.endswith(".ps1"):
                yield os.path.join(root, name)


def _code_lines(path):
    """(line_number, text) for lines that are actually code — comments and
    block comments carry the prose that DESCRIBES these patterns, and a
    scan that matched those would fail on its own documentation."""
    source = open(path, encoding="utf-8-sig").read()
    source = re.sub(r"<#.*?#>", "", source, flags=re.S)
    out = []
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append((number, line.split("#")[0] if " #" in line else line))
    return out


def test_no_bare_executable_invocations():
    """v1.0 PATH-hijack contract.

    Pulse runs elevated, and a bare executable name is a $env:PATH SEARCH
    rather than a path. PATH is assembled from HKCU as well as HKLM, so an
    unelevated user can place a directory ahead of System32 and have their
    binary launched with Pulse's administrator token.

    Every stock tool therefore goes through Get-SystemBinary (or
    Get-WingetPath for the app-execution alias). This scan is the guard
    that keeps a future `Start-Process explorer` from quietly restoring the
    hole, since the resulting behaviour is indistinguishable from correct
    on a machine that is not under attack.
    """
    names = "|".join(_ANCHORED_TOOLS)
    patterns = (
        # Start-Process explorer / Start-Process "taskmgr.exe" / -FilePath "winget"
        re.compile(
            r'Start-Process\s+(?:-FilePath\s+)?["\']?(?:%s)(?:\.exe)?["\']?[\s,]' % names,
            re.I),
        # & winget ... / & "explorer" ...
        re.compile(r'&\s+["\']?(?:%s)(?:\.exe)?["\']?\s' % names, re.I),
    )

    offenders = []
    for path in _backend_files():
        relative = os.path.relpath(path, _ROOT).replace(os.sep, "/")
        for number, line in _code_lines(path):
            for pattern in patterns:
                if pattern.search(line):
                    offenders.append(f"{relative}:{number}: {line.strip()}")

    assert not offenders, (
        "bare executable invocation(s) found — these resolve through "
        "$env:PATH, which the unelevated user controls, and Pulse runs "
        "elevated. Route them through Get-SystemBinary (00-Foundation.ps1) "
        "or Get-WingetPath (03-Environment.ps1):\n  " + "\n  ".join(offenders))


def test_wql_filters_escape_interpolated_values():
    """A WQL -Filter that interpolates a variable directly is injectable:
    WQL quotes with ' and escapes with \\, so a value carrying either ends
    the literal early and the rest is parsed as query. Interpolated values
    must go through ConvertTo-WqlLiteral (00-Foundation.ps1).

    A filter built only from literals (13-Activation.ps1's two constant
    application-ID GUIDs) has nothing to escape and is not matched here.
    """
    # -Filter "...'$Something'..." — a bare $var inside a quoted literal.
    raw = re.compile(r'-Filter\s+"[^"]*\'\$(?!\()[A-Za-z_]\w*[^"]*\'')

    offenders = []
    for path in _backend_files():
        relative = os.path.relpath(path, _ROOT).replace(os.sep, "/")
        for number, line in _code_lines(path):
            if raw.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")

    assert not offenders, (
        "WQL filter(s) interpolate a value without escaping it. Build the "
        "filter with ConvertTo-WqlLiteral, e.g.\n"
        '  $Filter = "Name=\'{0}\'" -f (ConvertTo-WqlLiteral $Name)\n  '
        + "\n  ".join(offenders))


class TestThemes:
    @staticmethod
    def _themes(qapp):
        from frontend import theme as TH
        return {name: TH.ThemeManager(name, None).t for name in ("dark", "light")}

    def test_both_themes_expose_identical_token_sets(self, qapp):
        themes = self._themes(qapp)
        dark, light = set(themes["dark"]), set(themes["light"])
        assert dark == light, (
            f"only in dark: {sorted(dark - light)}; "
            f"only in light: {sorted(light - dark)}")

    def test_module_accents_resolve_in_both_themes(self, qapp):
        from frontend import theme as TH
        accents = set(re.findall(r'"accent"\s*:\s*"([^"]+)"', _menu_source()))
        assert accents, "no module accents found to check"
        for name, tokens in self._themes(qapp).items():
            for accent in accents:
                value = TH.resolve_accent(tokens, accent)
                assert isinstance(value, str) and value.startswith("#"), (
                    f"accent {accent!r} did not resolve in {name}: {value!r}")

    def test_opaque_canvas_tokens_are_solid_hex(self, qapp):
        """The shell gradient must stay fully opaque — an rgba() here
        would punch translucency straight back through the window."""
        for name, tokens in self._themes(qapp).items():
            for key in ("bg_grad_top", "bg_grad_bottom"):
                value = tokens[key]
                assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), (
                    f"{name}.{key} = {value!r} is not an opaque hex colour")

    #: (text token, surface token, floor). Body copy is held to WCAG AA
    #: (4.5:1); the muted and faint tiers are captions and secondary
    #: labels at a larger effective weight, so they are held to the 3:1
    #: large-text floor. Below those numbers the light theme washes out —
    #: which is exactly how the v10 palette shipped, at 1.86-2.64:1.
    _CONTRAST_PAIRS = [
        ("text",       "dialog_bg", 4.5),
        ("text",       "card",      4.5),
        ("text",       "panel",     4.5),
        ("text_muted", "panel",     3.0),
        ("text_muted", "card",      3.0),
        ("text_muted", "dialog_bg", 3.0),
        ("text_faint", "card",      3.0),
        ("text_faint", "dialog_bg", 3.0),
    ]

    @staticmethod
    def _relative_luminance(color) -> float:
        channels = []
        for raw in (color.redF(), color.greenF(), color.blueF()):
            channels.append(raw / 12.92 if raw <= 0.03928
                            else ((raw + 0.055) / 1.055) ** 2.4)
        r, g, b = channels
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def _contrast(self, fg, bg) -> float:
        lf, lb = self._relative_luminance(fg), self._relative_luminance(bg)
        hi, lo = max(lf, lb), min(lf, lb)
        return (hi + 0.05) / (lo + 0.05)

    def test_text_clears_its_contrast_floor_in_both_themes(self, qapp):
        """Contrast regression guard (v1.0+ Phase 0).

        A palette edit that looks fine on the author's monitor in their
        preferred theme is the single easiest way to make the OTHER theme
        unreadable, and nothing about it raises. This measures the pairs
        the app actually paints, in both modes, against the floors the
        v11 palette was solved for.
        """
        from frontend import theme as TH

        failures = []
        checked = 0
        for name, tokens in self._themes(qapp).items():
            for fg_key, bg_key, floor in self._CONTRAST_PAIRS:
                # A missing token is a renamed token, not an exemption —
                # skipping quietly is how this whole test becomes a no-op.
                assert fg_key in tokens and bg_key in tokens, (
                    f"{name}: contrast pair ({fg_key}, {bg_key}) names a "
                    "token that no longer exists — update _CONTRAST_PAIRS")
                checked += 1
                # Surfaces may be rgba() over the canvas; composite them
                # onto the solid shell colour first, or a translucent card
                # measures against nothing and reports a fictional number.
                bg = TH.to_qcolor(TH.blend(tokens["bg_solid"], tokens[bg_key]))
                fg = TH.to_qcolor(tokens[fg_key])
                ratio = self._contrast(fg, bg)
                if ratio < floor:
                    failures.append(
                        f"{name}: {fg_key} on {bg_key} = {ratio:.2f}:1 "
                        f"(floor {floor}:1)")
        assert checked == 2 * len(self._CONTRAST_PAIRS), "not every pair ran"
        assert not failures, "contrast floor breached:\n  " + "\n  ".join(failures)


def test_every_bound_shortcut_is_documented(window):
    """SHORTCUTS is meant to be the single source of truth for both the
    bindings and the help sheet — Ctrl+F was bound but undocumented."""
    from PySide6.QtGui import QShortcut
    documented = " ".join(seq for seq, _ in window.SHORTCUTS)
    bound = {s.key().toString() for s in window.findChildren(QShortcut)}
    undocumented = sorted(
        key for key in bound
        if key.startswith("Ctrl+") and not key[-1].isdigit()
        and key not in documented)
    assert not undocumented, f"bound but not in SHORTCUTS: {undocumented}"


class TestSoftwareCatalogMirror:
    """The unified catalog's frontend/backend mirror.

    SOFTWARE_CATALOG (GUI) and $Apps_CatalogAll (backend) are two spellings
    of one list: the GUI decides what the user can tick, the backend
    decides what each tick installs, and -AppIds is the only thing joining
    them. A drift is silent and one-directional — an id the GUI offers but
    the backend's list omits is simply filtered out of the deploy queue
    (see Invoke-GuiBulkDeploy's $SelectedIds contract), so the user ticks
    an app, gets a clean SUCCESS, and never receives it. The old per-card
    lists carried the same contract in a comment and nothing enforced it.
    """

    @staticmethod
    def _backend_ids() -> list[str]:
        """$Apps_CatalogAll, resolved to a flat id list. Parsed from the
        SOURCE rather than by running PowerShell so the test stays fast and
        works anywhere; the array is a plain concatenation of the per-group
        arrays, so each one is expanded in the order the union names them.

        Comments are stripped FIRST. 01-Catalogs.ps1 documents PowerShell's
        single-element-array flattening pitfall with a literal
        `@("id","name")` example, and an array body that runs up to the
        next top-level `$` swallows the comment block sitting above it —
        which silently added a phantom "id" entry to the expected list."""
        raw = open(_CATALOGS, encoding="utf-8-sig").read()
        src = "\n".join(re.sub(r"#.*$", "", line)
                        for line in raw.splitlines())

        def array_ids(name: str) -> list[str]:
            start = src.index(f"${name} = ")
            body = src[start:src.index("\n$", start + 1)]
            return re.findall(r'@\("([^"]+)",', body)

        union_line = re.search(r'\$Apps_CatalogAll\s*=\s*(.+)', src).group(1)
        members = re.findall(r'\$(Apps_\w+|Runtimes)', union_line)
        ids: list[str] = []
        for member in members:
            expanded = (["Apps_DevRuntimes", "Apps_DevIDEs", "Apps_DevAI",
                         "Apps_DevData", "Apps_DevContainers"]
                        if member == "Apps_DevHubAll" else [member])
            for name in expanded:
                ids.extend(array_ids(name))
        return ids

    def test_the_backend_list_was_parsed(self):
        """Guard the parser — a silently-empty list would make the equality
        assertion below pass for the wrong reason."""
        assert len(self._backend_ids()) > 30

    def test_gui_catalog_mirrors_the_backend_exactly(self):
        from frontend.menu_structure import catalog_app_ids
        gui = catalog_app_ids()
        backend = self._backend_ids()
        assert gui == backend, (
            "SOFTWARE_CATALOG and $Apps_CatalogAll disagree.\n"
            f"  only in the GUI:     {sorted(set(gui) - set(backend))}\n"
            f"  only in the backend: {sorted(set(backend) - set(gui))}\n"
            "  (order matters too — the deploy log follows catalog order)")

    def test_no_app_appears_twice(self):
        from frontend.menu_structure import catalog_app_ids
        ids = catalog_app_ids()
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        assert not dupes, f"app listed in more than one catalog section: {dupes}"

    def test_every_bundle_names_a_real_catalog_app(self):
        """A bundle button that ticks nothing is a dead control."""
        from frontend.menu_structure import CATALOG_BUNDLES, catalog_app_ids
        known = set(catalog_app_ids())
        for bundle in CATALOG_BUNDLES:
            unknown = sorted(set(bundle["app_ids"]) - known)
            assert not unknown, (
                f"bundle {bundle['key']!r} names non-catalog app(s): {unknown}")

    def test_the_bundle_section_exists(self):
        from frontend.menu_structure import (
            CATALOG_BUNDLE_SECTION, SOFTWARE_CATALOG)
        keys = {s["key"] for s in SOFTWARE_CATALOG}
        assert CATALOG_BUNDLE_SECTION in keys, (
            f"bundles are pinned to section {CATALOG_BUNDLE_SECTION!r}, "
            f"which is not one of {sorted(keys)} — the row would never show")

    def test_dependency_hints_point_inside_the_catalog(self):
        """A 'needs Java JDK' caption whose target is not in the catalog
        sends the user looking for a row that does not exist."""
        from frontend.menu_structure import catalog_app_ids, catalog_tools
        known = set(catalog_app_ids())
        for tool in catalog_tools():
            requires_id = tool[4]
            assert requires_id is None or requires_id in known, (
                f"{tool[0]} declares a dependency on {requires_id}, "
                "which is not in the catalog")


def test_command_palette_entries_are_runnable(qapp):
    from frontend.menu_structure import iter_leaf_items
    entries = list(iter_leaf_items())
    assert entries
    assert all(item.get("task") for item, _ in entries)
    assert all(crumb for _, crumb in entries)


def test_frontend_and_backend_report_the_same_version():
    """Both constants carry a "keep in lockstep" comment and both drifted
    anyway — main.py and core.ps1 sat at 10.0 through the 10.1, 10.2 and
    10.3 releases, so the title bar, the sidebar footer and QApplication
    all reported a version no changelog entry matched. A comment is not a
    constraint; this is."""
    from frontend.main import APP_VERSION

    core = open(os.path.join(_ROOT, "src/backend/core.ps1"),
                encoding="utf-8-sig").read()
    match = re.search(r'\$Script:ScriptVersion\s*=\s*"([^"]+)"', core)
    assert match, "ScriptVersion was renamed — update this test with it"
    assert match.group(1) == APP_VERSION, (
        f"core.ps1 says {match.group(1)}, main.py says {APP_VERSION}")
