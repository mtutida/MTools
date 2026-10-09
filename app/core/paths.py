from pathlib import Path
import os
import sys

# CompriMidia paths are retained only as migration compatibility sources.
LEGACY_APP_FOLDER_NAME = "CompriMidia"
COMPACTME_DATA_PARTS = ("MTools", "CompactMe")

APP_ROOT = Path(__file__).resolve().parents[1]


def _windows_local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def _platform_user_data_root() -> Path:
    if sys.platform == "win32":
        return _windows_local_app_data()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


LEGACY_USER_DATA_DIR = _platform_user_data_root() / LEGACY_APP_FOLDER_NAME
LEGACY_CONFIG_PATH = LEGACY_USER_DATA_DIR / "config.json"
LEGACY_LOG_DIR = LEGACY_USER_DATA_DIR / "logs"
LEGACY_SNAPSHOTS_DIR = LEGACY_USER_DATA_DIR / "snapshots"

USER_DATA_DIR = _platform_user_data_root().joinpath(*COMPACTME_DATA_PARTS)
LOG_DIR = USER_DATA_DIR / "logs"
CONFIG_PATH = USER_DATA_DIR / "config.json"
SNAPSHOTS_DIR = USER_DATA_DIR / "snapshots"

UI_DIR = APP_ROOT / "ui"
ICON_DIR = UI_DIR / "icons"
