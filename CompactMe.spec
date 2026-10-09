# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for MTools CompactMe.

Run on Windows from the project root:
    pyinstaller --clean --noconfirm CompactMe.spec

Optional FFmpeg packaging resolves its development source through
tools/ffmpeg_tooling.py, then packages only the pair into the final bin\ folder.
"""

from pathlib import Path
import os
import sys

block_cipher = None
project_root = Path(SPECPATH).resolve()
icon_path = project_root / "app" / "ui" / "icons" / "compactme.ico"
icon_arg = str(icon_path) if icon_path.exists() else None
version_info_path = project_root / "version_info.txt"
version_arg = str(version_info_path) if version_info_path.exists() else None


def _optional_binaries():
    """Package only ffmpeg.exe and ffprobe.exe, never the whole FFmpeg folder.

    Set COMPACTME_EMBED_FFMPEG=0 before running build_windows.bat to keep
    FFmpeg external and reduce dist size. COMPRIMIDIA_EMBED_FFMPEG is accepted
    as a legacy fallback. In external mode, place ffmpeg.exe and ffprobe.exe
    next to the built app in bin\ or keep them available in PATH.
    """
    embed = (os.environ.get("COMPACTME_EMBED_FFMPEG") or os.environ.get("COMPRIMIDIA_EMBED_FFMPEG", "1")).strip().lower()
    if embed in {"0", "false", "no", "off"}:
        return []

    sys.path.insert(0, str(project_root / "tools"))
    from ffmpeg_tooling import resolve_tooling
    resolution = resolve_tooling(project_root)
    if resolution is None:
        raise SystemExit("FFmpeg tooling resolution failed before packaging.")
    return [(str(resolution.ffmpeg), "bin"), (str(resolution.ffprobe), "bin")]


datas = []
config_path = project_root / "config.json"
icons_dir = project_root / "app" / "ui" / "icons"

if config_path.exists():
    datas.append((str(config_path), "."))

if icons_dir.exists():
    datas.append((str(icons_dir), "app/ui/icons"))

hiddenimports = [
    "tools.shared_media_runtime",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    # Optional frame-sampling diagnostics in frozen builds. The installed app
    # defaults to metadata-only analysis, but these imports keep the opt-in
    # diagnostic path complete when COMPACTME_ENABLE_FROZEN_FRAME_SAMPLING=1.
    "PIL",
    "PIL.Image",
    "PIL.ImageChops",
    "PIL.ImageFilter",
    "PIL.ImageStat",
    "PIL.JpegImagePlugin",
    "PIL.PngImagePlugin",
]


a = Analysis(
    [str(project_root / "app" / "main.py")],
    pathex=[str(project_root)],
    binaries=_optional_binaries(),
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Size optimization phase 1: explicitly exclude scientific/plotting stacks
    # that are not imported by CompactMe runtime code. This prevents accidental
    # hook collection of numpy/OpenBLAS/scipy-style payloads while keeping PySide6,
    # Pillow, FFmpeg and FFprobe intact.
    #
    # Size optimization phase 2: exclude unused PySide6 modules. Runtime code only
    # imports QtCore, QtGui, QtWidgets, QtMultimedia, QtMultimediaWidgets,
    # QtNetwork, QtSvg and QtSvgWidgets. Keep this list conservative and validate
    # the compiled app after each change because Qt DLL collection is hook-driven.
    #
    # Size optimization phase 3: keep Pillow available for audio placeholders and
    # optional diagnostics, but exclude the AVIF plugin. CompactMe does not load
    # .avif assets, thumbnails are JPEG-based, and frozen frame sampling is disabled
    # by default. This targets PIL\_avif*.pyd, the largest Pillow codec payload.
    excludes=[
        "numpy",
        "numpy.libs",
        "scipy",
        "pandas",
        "matplotlib",
        "seaborn",
        "sklearn",
        "IPython",
        "jupyter",
        "notebook",
        "PIL._avif",
        "PIL.AvifImagePlugin",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DExtras",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DRender",
        "PySide6.QtBluetooth",
        "PySide6.QtCharts",
        "PySide6.QtConcurrent",
        "PySide6.QtDataVisualization",
        "PySide6.QtDesigner",
        "PySide6.QtGraphs",
        "PySide6.QtHelp",
        "PySide6.QtLocation",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtPositioning",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtQuickWidgets",
        "PySide6.QtRemoteObjects",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtSql",
        "PySide6.QtStateMachine",
        "PySide6.QtTest",
        "PySide6.QtTextToSpeech",
        "PySide6.QtWebChannel",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CompactMe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_arg,
    version=version_arg,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CompactMe",
)
