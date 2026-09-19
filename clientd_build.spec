# -*- mode: python -*-
# PyInstaller spec —— 打包 mj.clientd sidecar 为 mj-clientd.exe (临时,勿引用)
import os

block_cipher = None
repo = os.path.abspath(os.getcwd())

EXCLUDES = ["torch", "torchvision", "torchaudio", "triton"]

from PyInstaller.utils.hooks import collect_all, collect_submodules

ws_datas, ws_binaries, ws_hidden = collect_all("websockets")

a = Analysis(
    ["clientd_build_entry.py"],
    pathex=[repo],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("websockets") + ws_hidden + [
        "websockets.asyncio", "websockets.asyncio.server", "websockets.asyncio.client",
        "websockets.sync", "websockets.sync.server", "websockets.sync.client",
        "websockets.legacy", "websockets.legacy.server", "websockets.legacy.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="mj-clientd",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)