import sys
from functools import lru_cache

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon

from app.ui.assets import icon as asset_icon


_TITLEBAR_ICON_ASSET = "compactme_titlebar.ico"
_DEFAULT_ICON_ASSET = "compactme.ico"


@lru_cache(maxsize=1)
def app_window_icon() -> QIcon:
    """Return the icon used by native window title bars.

    The title-bar icon intentionally uses a dedicated ICO asset.  Windows
    chooses very small entries (usually 16/20 px) for the title bar; using the
    same full-size app icon can make the badge look visually small or slightly
    off-centre after the native resize.  The dedicated asset keeps the same CM
    artwork, but provides title-bar-optimized small entries.
    """
    titlebar_icon = QIcon(asset_icon(_TITLEBAR_ICON_ASSET))
    if not titlebar_icon.isNull():
        return titlebar_icon
    return QIcon(asset_icon(_DEFAULT_ICON_ASSET))


def apply_window_icon(widget) -> None:
    """Apply the app icon and refresh it after the native handle exists.

    The delayed refresh avoids the common Windows/Qt case where the title bar
    keeps the first small-icon raster chosen before the native window is fully
    initialized.  Keeping this in one helper also guarantees the main window and
    dialogs use the same title-bar centering/fill behaviour.
    """
    icon = app_window_icon()
    widget.setWindowIcon(icon)

    if sys.platform.startswith("win"):
        QTimer.singleShot(0, lambda: widget.setWindowIcon(icon))
