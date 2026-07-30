# -*- mode: python ; coding: utf-8 -*-

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
    ],
    hiddenimports=[
        'utils.helpers',
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Pulse',
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
