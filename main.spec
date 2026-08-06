# -*- mode: python ; coding: utf-8 -*-
#
#  PULSE — PyInstaller build recipe.
#
#  ONEDIR, NOT ONEFILE (v10.3). This used to pass a.binaries/a.datas
#  straight into EXE(), which produces a single self-extracting executable.
#  For an INSTALLED application that is the wrong shape, for two reasons
#  that only appear once it ships:
#
#    1. A onefile build re-extracts the ENTIRE bundle — PySide6, Qt's
#       plugins, the whole PowerShell engine — into %TEMP%\_MEIxxxxxx on
#       every single launch, then deletes it on exit. That is seconds of
#       cold start the user pays each time, for nothing, on a tool whose
#       whole promise is "one launcher".
#
#    2. It puts the engine in a user-writable directory. utils/resources.py
#       documents this exact hazard: with _MEIPASS under %TEMP%, the
#       "bundled" root ladder resolved to %TEMP%, so %TEMP%\src\backend\
#       core.ps1 became a candidate location for the script Pulse runs
#       ELEVATED. Any process running as the user could write it.
#
#  Onedir puts _MEIPASS inside the install directory — which the Inno Setup
#  script installs to Program Files, i.e. somewhere an unelevated process
#  cannot write. Launch is a plain exec with no extraction at all.
#
#  Build:  pyinstaller main.spec      ->  dist/PULSE/PULSE.exe
#  The installer (installer/pulse.iss) packages that directory wholesale.

import os
import re

# PyInstaller 6.x does NOT inject these into the spec namespace the way it
# does Analysis/EXE/COLLECT — a spec that uses them without this import
# fails with a bare NameError several minutes into the build.
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo,
)

# The version resource is stamped from the SAME `VERSION` file the GUI and
# the engine read (see src/utils/version.py). Windows wants a 4-tuple of
# integers, so the three-component release version gains a trailing 0.
_here = os.path.abspath(os.getcwd())
with open(os.path.join(_here, 'VERSION'), encoding='utf-8-sig') as _fh:
    APP_VERSION = _fh.read().strip()
if not re.fullmatch(r'\d+\.\d+\.\d+', APP_VERSION):
    raise SystemExit(f'VERSION is {APP_VERSION!r}; expected MAJOR.MINOR.PATCH')
_v = tuple(int(p) for p in APP_VERSION.split('.')) + (0,)

a = Analysis(
    ['src/frontend/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src/backend/core.ps1', 'src/backend'),
        # core.ps1 is only a thin orchestrator: it dot-sources every module
        # in src/backend/modules/ at startup. Without this entry the bundled
        # exe ships an engine that fails to load on every task.
        ('src/backend/modules', 'src/backend/modules'),
        # window/taskbar icon, loaded at runtime via _locate_icon()
        ('assets/pulse.ico', 'assets'),
        # Shipped playbooks (v10.3). Resolved at runtime by
        # frontend.playbooks.playbook_dirs(), which checks _MEIPASS first;
        # without this the Automation module loads an empty list in the
        # frozen build and the feature silently looks broken. A technician
        # can still drop extra .json files next to the exe — that
        # directory is searched ahead of this one.
        ('playbooks', 'playbooks'),
        # The single version source (utils/version.py reads it, and so does
        # core.ps1 via ..\..\VERSION). It has to land at the BUNDLE ROOT:
        # that is what makes the engine's one relative path resolve in both
        # the checkout and the bundle. Without this entry both fall back to
        # their hardcoded literal and the app silently misreports itself
        # the first time VERSION changes.
        ('VERSION', '.'),
    ],
    hiddenimports=[
        'utils.helpers',
        'utils.version',
        'frontend.theme',
        'frontend.animations',
        'frontend.menu_structure',
        'frontend.widgets',
        'frontend.playbooks',
        'frontend.health_report',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ============================================================
#  WINDOWS VERSION RESOURCE
# ============================================================
# The exe shipped with NO version resource at all, so its Properties tab
# was blank, SmartScreen and AV heuristics had nothing to weigh, and the
# updater had no authoritative version to compare an installed build
# against. Every string here is derived from `VERSION`; none is a literal.
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_v, prodvers=_v,
        mask=0x3F, flags=0x0,
        OS=0x40004,        # VOS_NT_WINDOWS32
        fileType=0x1,      # VFT_APP
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable('040904B0', [      # US English, Unicode
                StringStruct('CompanyName', 'Humam Taibeh'),
                StringStruct('FileDescription',
                             'PULSE — Windows configuration and repair'),
                StringStruct('FileVersion', APP_VERSION),
                StringStruct('InternalName', 'PULSE'),
                StringStruct('LegalCopyright',
                             'Copyright (c) Humam Taibeh. MIT License.'),
                StringStruct('OriginalFilename', 'PULSE.exe'),
                StringStruct('ProductName', 'PULSE'),
                StringStruct('ProductVersion', APP_VERSION),
            ]),
        ]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)

exe = EXE(
    pyz,
    a.scripts,
    # ONEDIR: the binaries and datas are collected alongside the exe by
    # COLLECT below rather than embedded in it. exclude_binaries=True is
    # what makes that split; without it this silently reverts to onefile.
    exclude_binaries=True,
    name='PULSE',
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX intentionally disabled (v6.1): packed executables are a classic
    # antivirus false-positive heuristic, and an elevated system tool cannot
    # afford that reputation hit for a few MB of size.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='assets/pulse.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # uac_admin is deliberately NOT set (v1.0). It used to be True, which put
    # `requestedExecutionLevel requireAdministrator` in the manifest and made
    # EVERY launch of the packaged app elevated — with two consequences that
    # only showed up in the shipped binary, never when running from source:
    #
    #   1. A large, fully-tested subsystem became unreachable. The per-task
    #      elevation gate (menu_structure.requires_admin), the inline
    #      ElevatePromptDialog, the sidebar "Run as Administrator" CTA, the
    #      locked-card affordance and the "Not Elevated" hero chip can only
    #      ever be exercised by a non-elevated session. There wasn't one.
    #
    #   2. It made a documented failure permanent. Some installers set
    #      `elevationProhibited` and hard-refuse under an Administrator token
    #      (see $Script:KnownElevationProhibitedAppIds in 01-Catalogs.ps1);
    #      the backend's own error text tells the user to "use Pulse's GUI
    #      without elevating", which was impossible advice in the release
    #      build. Those packages were simply un-installable.
    #
    # Launching asInvoked restores the intended model: Pulse starts with the
    # rights the user has, and the ~24 tasks that genuinely need HKLM /
    # services / machine state ask for elevation at the moment they are
    # clicked. That is also what keeps HKCU tweaks landing in the hive of the
    # user who asked for them — see Initialize-UserHiveTargeting in
    # src/backend/modules/00-Foundation.ps1 for what goes wrong when an
    # elevated session belongs to a different account than the desktop.
)

# ============================================================
#  COLLECT — the installable directory
# ============================================================
# Produces dist/PULSE/ containing PULSE.exe plus _internal/ (Qt, PySide6,
# the Python runtime) and the data trees declared above. installer/pulse.iss
# packages this whole directory; nothing else is needed at runtime.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,          # same reasoning as EXE: see the note there
    upx_exclude=[],
    name='PULSE',
)
