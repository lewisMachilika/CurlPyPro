# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for CurlPro — single-file build, works on Windows, macOS and Linux.

Build with:
    pyinstaller curlpro.spec

Output: dist/CurlPro (single self-contained executable, .exe on Windows).
"""
import sys
from pathlib import Path

APP_NAME = "CurlPro"

# Optional icon: drop an icon next to this spec and it gets picked up automatically.
#   Windows -> assets/icon.ico   macOS -> assets/icon.icns   Linux -> none needed
icon_file = None
if sys.platform == "win32" and Path("assets/icon.ico").exists():
    icon_file = "assets/icon.ico"
elif sys.platform == "darwin" and Path("assets/icon.icns").exists():
    icon_file = "assets/icon.icns"


a = Analysis(
    ["curlpro.py"],
    pathex=[],
    binaries=[],
    datas=[],
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
        bundle_identifier="com.curlpro.app",
        info_plist={
            "CFBundleName": "CurlPro",
            "CFBundleDisplayName": "CurlPro",
            "NSHighResolutionCapable": True,
        },
    )
