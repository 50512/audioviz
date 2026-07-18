# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_dynamic_libs

# --- winrt (proyeccion de WinRT, usada para la metadata en Windows) ----------
# winrt es un *namespace package* (winrt.__file__ es None) y sus hojas
# winrt.windows.* viven bajo niveles intermedios sin __init__.py, asi que el
# analisis estatico de PyInstaller no las alcanza. collect_submodules atrapa
# winrt._winrt*, winrt.system y winrt.runtime; las hojas winrt.windows.* hay que
# nombrarlas a mano. collect_dynamic_libs trae msvcp140.dll (dependencia de los
# .pyd nativos). Los .pyd entran solos como modulos de extension (hiddenimports).
winrt_hiddenimports = collect_submodules('winrt') + [
    'winrt.windows.foundation',
    'winrt.windows.media.control',
    'winrt.windows.storage.streams',
]
winrt_binaries = collect_dynamic_libs('winrt')

# --- audioviz (imports perezosos) --------------------------------------------
# sources/__init__.py y metadata/__init__.py cargan cada backend con imports por
# string (__getattr__ / create_media_monitor), invisibles al analisis estatico.
# Recolectamos todo el paquete para arrastrar loopback/fb2k/mic/winrt_monitor/etc.
app_hiddenimports = collect_submodules('audioviz')

hiddenimports = winrt_hiddenimports + app_hiddenimports + [
    '_portaudiowpatch',   # extension nativa que carga pyaudiowpatch (WASAPI/loopback)
]

a = Analysis(
    ['run_visualizer.py'],
    pathex=[],
    binaries=winrt_binaries,
    datas=[],
    hiddenimports=hiddenimports,
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
    name='visualizer',
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
)
