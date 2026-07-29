# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for CurlPyPro — single-file build, works on Windows, macOS and Linux.

Build with:
    pyinstaller curlpypro.spec

Output: dist/CurlPyPro (single self-contained executable, .exe on Windows).
"""
import sys
from pathlib import Path

APP_NAME = "CurlPyPro"

# Optional icon: drop an icon next to this spec and it gets picked up automatically.
#   Windows -> assets/icon.ico   macOS -> assets/icon.icns   Linux -> none needed
icon_file = None
if sys.platform == "win32" and Path("assets/icon.ico").exists():
    icon_file = "assets/icon.ico"
elif sys.platform == "darwin" and Path("assets/icon.icns").exists():
    icon_file = "assets/icon.icns"

runtime_icons = []
for runtime_icon in (
    "assets/icon.ico",
    "assets/icon.icns",
    "assets/icon-master.png",
):
    if Path(runtime_icon).exists():
        runtime_icons.append((runtime_icon, "assets"))

a = Analysis(
    ["curlpypro.py"],
    pathex=[],
    binaries=[],
    datas=runtime_icons,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=True,    # macOS: handle file/url open events
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# On macOS also wrap the executable in a proper .app bundle so it is double-clickable.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name=f"{APP_NAME}.app",
        icon=icon_file,
        bundle_identifier="com.curlpypro.app",
        info_plist={
            "CFBundleName": "CurlPyPro",
            "CFBundleDisplayName": "CurlPyPro",
            "NSHighResolutionCapable": True,
        },
    )
