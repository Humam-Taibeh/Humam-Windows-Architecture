# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['src/frontend/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[('src/backend/core.ps1', 'src/backend')],
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
    name='HumamArchitecture',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
