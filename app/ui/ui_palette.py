from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from app.ui.theme_tokens import build_theme_tokens


class UIPalette:
    """Legacy palette facade backed by semantic theme tokens."""

    DARK = False

    CARD_BG = None
    CARD_BORDER = None
    TEXT_PRIMARY = None
    TEXT_SECONDARY = None

    FOOTER_BG = None
    FOOTER_BORDER = None

    BUTTON_BG = None
    BUTTON_BG_HOVER = None
    BUTTON_BG_ACTIVE = None

    ACCENT = None
    ACCENT_HOVER = None
    ACCENT_ACTIVE = None
    ACCENT_TEXT = None

    BUTTON_TEXT = None
    BUTTON_TEXT_DISABLED = None

    @classmethod
    def reload(cls):
        app = QApplication.instance()
        if not app:
            return

        palette: QPalette = app.palette()
        tokens = build_theme_tokens(palette)

        cls.DARK = palette.window().color().lightness() < 128

        cls.CARD_BG = tokens.surface_card
        cls.CARD_BORDER = tokens.border_card

        cls.TEXT_PRIMARY = tokens.text_primary
        cls.TEXT_SECONDARY = tokens.text_secondary

        cls.FOOTER_BG = tokens.surface_panel
        cls.FOOTER_BORDER = tokens.border_panel

        cls.BUTTON_BG = tokens.button_bg
        cls.BUTTON_BG_HOVER = tokens.button_hover_bg
        cls.BUTTON_BG_ACTIVE = tokens.button_pressed_bg

        cls.ACCENT = tokens.progress_fill
        cls.ACCENT_HOVER = tokens.button_hover_border
        cls.ACCENT_ACTIVE = tokens.button_pressed_border
        cls.ACCENT_TEXT = tokens.text_on_dark_surface

        cls.BUTTON_TEXT = tokens.button_fg
        cls.BUTTON_TEXT_DISABLED = tokens.button_disabled_fg


UIPalette.reload()
