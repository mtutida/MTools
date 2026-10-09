from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget

from app.ui.theme_mode_controller import palette_for_styled_dialog
from app.ui.theme_tokens import build_theme_tokens, color_to_win_bgr


_LOGGER = logging.getLogger(__name__)

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def _is_dark_color(color: QColor) -> bool:
    return QColor(color).lightness() < 128


def _set_dwm_attribute(hwnd: int, attribute: int, value: int) -> None:
    import ctypes
    from ctypes import wintypes

    raw_value = ctypes.c_int(value)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        ctypes.c_int(attribute),
        ctypes.byref(raw_value),
        ctypes.sizeof(raw_value),
    )


def _dialog_caption_colors(widget: QWidget) -> tuple[QColor, QColor]:
    """Return caption/text colors for controlled ancillary dialogs.

    In light mode, use the dialog palette window/text colors so the caption
    matches the native dialog body instead of falling back to the host Windows
    app theme. In dark mode, use ThemeTokens because the dialog body can be
    styled by the app.
    """
    token_palette = palette_for_styled_dialog(widget.palette())
    tokens = build_theme_tokens(token_palette, use_application_palette=False)
    palette_window = QColor(token_palette.window().color())
    if _is_dark_color(palette_window):
        return QColor(tokens.surface_main), QColor(tokens.text_primary)
    return palette_window, QColor(token_palette.windowText().color())

def apply_dialog_caption(widget: QWidget) -> None:
    """Apply a controlled native Windows caption to ancillary dialogs.

    The caption is native Windows chrome, not part of the widget stylesheet.
    Apply explicit DWM colors from the active palette/tokens so forced app
    themes do not inherit an incompatible host Windows caption color.
    """
    if sys.platform != "win32" or widget is None:
        return
    try:
        caption, caption_text = _dialog_caption_colors(widget)
        hwnd = int(widget.winId())
        _set_dwm_attribute(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if _is_dark_color(caption) else 0)
        _set_dwm_attribute(hwnd, _DWMWA_CAPTION_COLOR, color_to_win_bgr(caption))
        _set_dwm_attribute(hwnd, _DWMWA_TEXT_COLOR, color_to_win_bgr(caption_text))
    except Exception:
        _LOGGER.debug("Failed to apply native dialog caption colors.", exc_info=True)


def schedule_dialog_caption(widget: QWidget) -> None:
    """Apply now and after Qt/Windows finishes a show/theme pass."""
    apply_dialog_caption(widget)
    QTimer.singleShot(0, lambda: apply_dialog_caption(widget))
    QTimer.singleShot(50, lambda: apply_dialog_caption(widget))
