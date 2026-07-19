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
    ],
    hiddenimports=[
        'utils.helpers',
        'frontend.theme',
        'frontend.animations',
        'frontend.menu_structure',
        'frontend.widgets',
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
    uac_admin=True,
)
