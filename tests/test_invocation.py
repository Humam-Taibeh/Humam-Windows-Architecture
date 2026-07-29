"""
How the GUI invokes the backend (v10.3).

THE BUG CLASS THIS FILE EXISTS FOR
    Until v10.3 every task was launched by pasting its values into a
    single-quoted PowerShell literal inside one `-Command` string, escaping
    `'` as `''`. PowerShell's tokenizer accepts FIVE characters as a
    single-quote delimiter — U+0027 plus the typographic quotes U+2018
    U+2019 U+201A U+201B — so a path containing a curly apostrophe closed
    the literal early. `Adobe’s Reader.exe` is what a browser saves, not
    something an attacker has to arrange, and it turned into either a parse
    error the user saw as "Script finished without a recognized status
    line" or, deliberately, appended PowerShell running with Pulse's
    rights.

    The fix is structural: `-File` plus argv, so nothing is ever tokenized
    and there is no escape table to get wrong. These tests pin that
    property rather than any particular escaping, because the lesson of the
    bug is that escaping was the wrong layer to solve it at.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from utils.helpers import (
    SAFE_CWD, PowerShellTask, UnsafeArgument, validate_backend_arg,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE = os.path.join(_ROOT, "src", "backend", "core.ps1")

#: Every character PowerShell's tokenizer will close a single-quoted string
#: on. U+0027 was the only one the old escaper knew about.
PS_QUOTE_CHARS = ["'", "\u2018", "\u2019", "\u201a", "\u201b"]


def _argv_for(**kwargs) -> list[str]:
    return PowerShellTask(_CORE, kwargs.pop("task", "SystemInfo"),
                          **kwargs)._build_argv()


# ============================================================
#  ARGV SHAPE
# ============================================================
class TestArgvConstruction:
    def test_uses_file_not_command(self):
        """-Command hands PowerShell a string it re-parses; -File hands it
        argv. The whole injection class lives in that difference."""
        argv = _argv_for()
        assert "-Command" not in argv
        assert "-File" in argv
        assert argv[argv.index("-File") + 1] == _CORE

    def test_script_path_precedes_its_parameters(self):
        argv = _argv_for()
        assert argv.index("-File") < argv.index("-Task")

    def test_quote_characters_survive_verbatim(self):
        """The property that matters: a value with any PowerShell quote
        character in it reaches argv unchanged — not escaped, not doubled,
        not mangled. Nothing downstream re-parses it."""
        for quote in PS_QUOTE_CHARS:
            path = f"C:\\Users\\Sam\\Downloads\\Adobe{quote}s Reader.exe"
            argv = _argv_for(task="InstallLocalFile", local_installer_path=path)
            assert argv[argv.index("-LocalInstallerPath") + 1] == path

    def test_injection_payload_stays_one_argument(self):
        """The exact shape that used to break out: a curly quote, a
        statement separator, and a second curly quote to re-open the
        literal. As argv it is inert — one string, no separators."""
        payload = "C:\\tmp\\a\u2019; Write-Output PWNED; \u2019b.exe"
        argv = _argv_for(task="InstallLocalFile", local_installer_path=payload)
        assert argv.count(payload) == 1
        assert not any("PWNED" in arg for arg in argv if arg != payload)

    def test_office_paths_ride_their_own_parameters(self):
        setup = "C:\\ODT\\O\u2019Brien\\setup.exe"
        config = "C:\\ODT\\O\u2019Brien\\configuration.xml"
        argv = _argv_for(task="InstallOfficeODT",
                         office_setup=setup, office_config=config)
        assert argv[argv.index("-OfficeSetupPath") + 1] == setup
        assert argv[argv.index("-OfficeConfigPath") + 1] == config

    def test_whatif_is_a_bare_switch(self):
        argv = _argv_for(dry_run=True)
        assert argv[-1] == "-WhatIf"

    def test_no_whatif_when_not_dry_run(self):
        assert "-WhatIf" not in _argv_for()


# ============================================================
#  THE STARTUP-ID CHANNEL  (SEC-3)
# ============================================================
class TestStartupItemChannel:
    """A startup id is `Type|||RegPath|||Name` where Name is whatever an
    installer wrote into a Run key. Carrying it on -AppIds — a
    COMMA-SEPARATED list — meant any name containing a comma was split into
    fragments that resolved to no item, so the entry could not be toggled
    and the GUI blamed a stale list."""

    COMMA_ID = ("Registry|||HKCU:\\Software\\Microsoft\\Windows\\"
                "CurrentVersion\\Run|||Acme, Inc. Updater")

    def test_startup_id_has_its_own_parameter(self):
        argv = _argv_for(task="StartupDisableItem", startup_item_id=self.COMMA_ID)
        assert argv[argv.index("-StartupItemId") + 1] == self.COMMA_ID
        assert "-AppIds" not in argv

    def test_a_comma_in_the_name_is_not_a_separator(self):
        argv = _argv_for(task="StartupDisableItem", startup_item_id=self.COMMA_ID)
        assert self.COMMA_ID in argv

    def test_backend_declares_the_parameter(self):
        source = open(_CORE, encoding="utf-8-sig").read()
        assert "$StartupItemId" in source

    def test_dispatcher_reads_it_instead_of_appids(self):
        path = os.path.join(_ROOT, "src", "backend", "modules",
                            "30-GuiDispatcher.ps1")
        source = open(path, encoding="utf-8-sig").read()
        block = source[source.index('"StartupDisableItem"'):
                       source.index('"ScanForUpdates"')]
        assert "$StartupItemId" in block
        assert "SelectedAppIds" not in block


# ============================================================
#  ARGUMENT VALIDATION
# ============================================================
class TestArgumentValidation:
    """argv removes quoting as a concern; these are the two things argv
    itself cannot express, so they are refused rather than sanitised."""

    def test_leading_dash_is_refused(self):
        with pytest.raises(UnsafeArgument):
            validate_backend_arg("The installer path", "-WhatIf")

    def test_newline_is_refused(self):
        for bad in ("a\nb", "a\rb", "a\x00b"):
            with pytest.raises(UnsafeArgument):
                validate_backend_arg("value", bad)

    def test_ordinary_windows_paths_pass(self):
        for good in (r"C:\Program Files\App\setup.exe",
                     "C:\\Users\\Sam\\O\u2019Brien's file.msi",
                     "Microsoft.VisualStudioCode"):
            assert validate_backend_arg("value", good) == good

    def test_a_refused_value_never_reaches_a_process(self):
        task = PowerShellTask(_CORE, "InstallLocalFile",
                              local_installer_path="-WhatIf")
        with pytest.raises(UnsafeArgument):
            task._build_argv()


# ============================================================
#  BINARY PLANTING  (SEC-2)
# ============================================================
@pytest.mark.skipif(sys.platform != "win32", reason="Win32 search order")
class TestTrustedExecutables:
    """CreateProcess and ShellExecute search the working directory before
    most of PATH. Pulse is routinely launched from Downloads, so a bare
    "powershell" plus an inherited CWD meant a file dropped beside an
    installer won the lookup — with Pulse's rights, elevation included."""

    def test_powershell_is_an_absolute_system_path(self):
        exe = _argv_for()[0]
        assert os.path.isabs(exe), f"{exe!r} would be resolved by search order"
        assert os.path.exists(exe)
        assert exe.lower().startswith(os.environ["SystemRoot"].lower())

    def test_child_cwd_is_pinned_outside_user_space(self):
        assert SAFE_CWD is not None
        assert os.path.isdir(SAFE_CWD)
        assert SAFE_CWD.lower() == os.environ["SystemRoot"].lower()


# ============================================================
#  END-TO-END: the real engine, with a hostile path, dry-run only
# ============================================================
@pytest.mark.skipif(sys.platform != "win32", reason="needs powershell.exe")
class TestLiveInvocation:
    """The static tests above prove the argv shape; this proves PowerShell
    actually binds it. -WhatIf throughout — nothing here may touch the
    machine."""

    def _run(self, task: PowerShellTask) -> str:
        result = subprocess.run(
            task._build_argv(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=SAFE_CWD, timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return result.stdout + result.stderr

    def test_plain_task_reaches_a_verdict(self):
        out = self._run(PowerShellTask(_CORE, "SystemInfo", dry_run=True))
        assert "##PULSE##SUCCESS|" in out

    def test_curly_quote_path_binds_without_a_parse_error(self):
        """The regression proper. Under the old -Command builder this exact
        input produced "The string is missing the terminator" and no
        verdict line at all."""
        out = self._run(PowerShellTask(
            _CORE, "InstallLocalFile", dry_run=True,
            local_installer_path="C:\\tmp\\Adobe\u2019s Reader.exe"))
        assert "missing the terminator" not in out
        assert "##PULSE##" in out

    def test_injection_payload_does_not_execute(self):
        """The payload must be DISPLAYED, never RUN.

        Note the assertion is per-line, not a substring search: the backend
        correctly echoes the path it was given ("Running installer: C:\\tmp\\
        a’; Write-Output PWNED; ’b.exe"), so the marker legitimately appears
        inside that one quoted line. What must never appear is a line that
        is ONLY the marker — that is what `Write-Output PWNED` produces when
        the tokenizer is tricked into treating it as a statement.
        """
        marker = "PWNED"
        out = self._run(PowerShellTask(
            _CORE, "InstallLocalFile", dry_run=True,
            local_installer_path=f"C:\\tmp\\a\u2019; Write-Output {marker}; \u2019b.exe"))
        executed = [ln for ln in out.splitlines() if ln.strip() == marker]
        assert not executed, f"the payload was executed: {executed}"
        # ...and it did reach the backend intact, as data.
        assert marker in out

    def test_appids_shape_binds(self):
        out = self._run(PowerShellTask(
            _CORE, "InstallEssentialApps", dry_run=True,
            app_ids=["Mozilla.Firefox", "7zip.7zip"]))
        assert "##PULSE##" in out

    def test_startup_id_task_reaches_a_verdict(self):
        """Deliberately weak, and labelled as such.

        StartupDisableItem is admin-gated ($Script:AdminRequiredTasks), so
        in an unelevated session the dispatcher returns before it ever
        reads $StartupItemId. That makes any assertion about the id's
        VALUE here vacuous. Binding is proved elevation-free by
        TestParameterBinding below; all this checks is that the argv is
        well-formed enough for the engine to run and answer at all.
        """
        out = self._run(PowerShellTask(
            _CORE, "StartupDisableItem", dry_run=True,
            startup_item_id="Registry|||HKCU:\\Nope|||Acme, Inc. Updater"))
        assert "##PULSE##" in out
        assert "missing the terminator" not in out


# ============================================================
#  PARAMETER BINDING, WITHOUT NEEDING ELEVATION
# ============================================================
@pytest.mark.skipif(sys.platform != "win32", reason="needs powershell.exe")
class TestParameterBinding:
    """Prove every shape binds to the parameter it is meant to.

    The live tests above cannot do this for the admin-gated tasks (Office,
    Startup) because the dispatcher's elevation check fires first, so an
    unelevated run would pass whether the value bound or not.

    So: take core.ps1's OWN param() block — read from the file, never
    retyped — drop it into a stub that echoes what PowerShell bound, and
    run the real argv against it. If someone renames a parameter on either
    side, this fails.
    """

    @staticmethod
    def _stub(tmp_path) -> str:
        source = open(_CORE, encoding="utf-8-sig").read()
        start = source.index("param(")
        end = source.index(")", source.index("[switch]$WhatIf")) + 1
        stub = tmp_path / "bind_stub.ps1"
        stub.write_text(
            source[start:end] + "\n"
            # REQUIRED, not boilerplate. PowerShell 5.1 encodes redirected
            # stdout in the console code page, and CP437 has no U+2019 — so
            # WideCharToMultiByte "best-fits" it to a plain apostrophe on the
            # way OUT. Without this line the stub reports ’ as ', and the
            # assertions below fail against a value that bound perfectly.
            # core.ps1 sets exactly this for the same reason (it is why the
            # frontend no longer needs to prepend it), so the stub has to
            # match its host to measure anything real.
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            "foreach ($kv in $PSBoundParameters.GetEnumerator()) {\n"
            "  Write-Output (\"BOUND|\" + $kv.Key + \"|\" + $kv.Value)\n"
            "}\n", encoding="utf-8")
        return str(stub)

    def _bound(self, tmp_path, task: PowerShellTask) -> dict[str, str]:
        argv = task._build_argv()
        argv[argv.index("-File") + 1] = self._stub(tmp_path)
        result = subprocess.run(argv, capture_output=True, text=True,
                                encoding="utf-8", cwd=SAFE_CWD, timeout=90,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stdout + result.stderr
        pairs = {}
        for line in result.stdout.splitlines():
            if line.startswith("BOUND|"):
                _, key, value = line.split("|", 2)
                pairs[key] = value
        return pairs

    def test_startup_id_binds_verbatim_including_its_commas(self, tmp_path):
        item_id = ("Registry|||HKCU:\\Software\\Microsoft\\Windows\\"
                   "CurrentVersion\\Run|||Acme, Inc. Updater")
        bound = self._bound(tmp_path, PowerShellTask(
            _CORE, "StartupDisableItem", startup_item_id=item_id))
        assert bound["StartupItemId"] == item_id
        assert "AppIds" not in bound

    def test_office_paths_bind_to_their_own_parameters(self, tmp_path):
        setup = "C:\\ODT\\O\u2019Brien\\setup.exe"
        config = "C:\\ODT\\O\u2019Brien\\configuration.xml"
        bound = self._bound(tmp_path, PowerShellTask(
            _CORE, "InstallOfficeODT",
            office_setup=setup, office_config=config))
        assert bound["OfficeSetupPath"] == setup
        assert bound["OfficeConfigPath"] == config

    def test_installer_path_with_a_curly_quote_binds_verbatim(self, tmp_path):
        path = "C:\\tmp\\Adobe\u2019s Reader.exe"
        bound = self._bound(tmp_path, PowerShellTask(
            _CORE, "InstallLocalFile", local_installer_path=path))
        assert bound["LocalInstallerPath"] == path

    def test_whatif_binds_as_a_switch(self, tmp_path):
        bound = self._bound(tmp_path, PowerShellTask(
            _CORE, "SystemInfo", dry_run=True))
        assert bound["WhatIf"] == "True"
        assert bound["Task"] == "SystemInfo"

    def test_appids_stays_one_string_for_the_backend_to_split(self, tmp_path):
        bound = self._bound(tmp_path, PowerShellTask(
            _CORE, "InstallEssentialApps",
            app_ids=["Mozilla.Firefox", "7zip.7zip"]))
        assert bound["AppIds"] == "Mozilla.Firefox,7zip.7zip"
