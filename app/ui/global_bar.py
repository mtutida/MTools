import logging
import os
import hashlib
import tempfile
import threading
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QPoint, QSettings, QSize, Qt, QRect, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QMenu,
    QProxyStyle,
    QPushButton,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from app.ancillary.configuration import ConfigurationService
from app.ancillary.logging_service import LoggingService
from app.engine.encode_estimator import estimate_size_crf
from app.engine.ffprobe_probe import probe
from app.engine.thumbnail_generator import generate_thumbnail
from app.interaction_model.event_bridge import event_bridge
from app.interaction_model.job_state import ensure_job_state, get_job_attr, patch_job
from app.ui.assisted_import_dialog import AssistedImportDialog
from app.core.compression_profiles import build_automatic_profile_from_media_info, default_audio_profile, default_quick_profile, derive_job_profile_from_payload, estimate_output_bitrate_for_profile, estimate_output_is_actionable, estimate_size_for_profile
from app.ui.theme_mode_controller import apply_theme_mode, normalized_theme_mode
from app.ui.theme_tokens import build_theme_tokens, build_button_stylesheet, build_small_button_stylesheet
from app.ui.ui_utils import load_icon

_LOGGER = logging.getLogger(__name__)



class _NormalStateButtonContentNudgeStyle(QProxyStyle):
    """Nudges QPushButton contents only in the non-hover state.

    The embedded top bar uses flat/tab-like buttons. On Windows, the normal
    state and hover state can resolve a slightly different content rectangle
    after stylesheet rendering, which makes the label look 1px too high only
    before hover. Keeping this as a tiny style shim avoids changing the card
    layout or the hover metrics already validated visually.
    """

    def __init__(self, base_style=None, normal_dy: int = 1):
        super().__init__(base_style)
        self._normal_dy = normal_dy

    def subElementRect(self, element, option, widget=None):
        rect = super().subElementRect(element, option, widget)
        if element == QStyle.SubElement.SE_PushButtonContents and option is not None:
            is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
            is_pressed = bool(option.state & QStyle.StateFlag.State_Sunken)
            if not is_hover and not is_pressed:
                rect.translate(0, self._normal_dy)
        return rect


def _install_normal_state_text_nudge(button: QPushButton, dy: int = 1):
    style = _NormalStateButtonContentNudgeStyle(None, dy)
    button.setStyle(style)
    button._normal_state_text_nudge_style = style

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".3gp")
AUDIO_EXT = (".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus", ".alac")


class _CompactMenuActionWidget(QWidget):
    triggered = Signal()
    hovered = Signal()
    left = Signal()

    def __init__(self, icon: QIcon, text: str, *, has_submenu: bool = False, icon_kind: str | None = None, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._icon_kind = icon_kind
        self._text = text
        self._has_submenu = has_submenu
        self._hovered = False
        self._tokens = None
        self.setMouseTracking(True)
        self.setFixedHeight(28)
        self.setMinimumWidth(130)

    def set_tokens(self, tokens):
        self._tokens = tokens
        self.update()

    def set_icon(self, icon: QIcon):
        self._icon = icon
        self.update()

    def set_icon_kind(self, icon_kind: str | None):
        self._icon_kind = icon_kind
        self.update()

    def _draw_menu_icon(self, painter: QPainter, rect: QRect, color: QColor) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        x = rect.left()
        y = rect.top()
        kind = self._icon_kind

        if kind == "prefs":
            for row_y, knob_x in ((4, 5), (7, 10), (10, 7)):
                yy = y + row_y
                painter.drawLine(x + 2, yy, x + 12, yy)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QRectF(x + knob_x - 1.35, yy - 1.35, 2.7, 2.7))
                painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "appearance":
            painter.drawEllipse(QRectF(x + 2.4, y + 2.4, 9.2, 9.2))
            painter.setBrush(QBrush(color))
            painter.drawPie(QRectF(x + 2.4, y + 2.4, 9.2, 9.2), 90 * 16, 180 * 16)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "help":
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "?")
        elif kind == "about":
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(rect.adjusted(0, -1, 0, -1), Qt.AlignmentFlag.AlignCenter, "i")
        elif kind == "exit":
            painter.drawLine(x + 3, y + 2, x + 9, y + 2)
            painter.drawLine(x + 3, y + 12, x + 9, y + 12)
            painter.drawLine(x + 3, y + 2, x + 3, y + 12)
            painter.drawLine(x + 6, y + 7, x + 12, y + 7)
            painter.drawLine(x + 9, y + 5, x + 12, y + 7)
            painter.drawLine(x + 9, y + 9, x + 12, y + 7)

        painter.restore()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        self.hovered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        self.left.emit()
        super().leaveEvent(event)

    def event(self, event):
        if event.type() in (QEvent.Type.Leave, QEvent.Type.WindowDeactivate):
            if self._hovered:
                self._hovered = False
                self.update()
                self.left.emit()
        return super().event(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._hovered = False
            self.update()
            self.triggered.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if tokens is not None:
            fg = QColor(tokens.text_primary)
            hover_bg = QColor(tokens.button_hover_bg)
            menu_bg = QColor(tokens.surface_panel)
            if menu_bg.lightness() >= 128:
                link = QColor(tokens.text_linkish)
                hover_bg = QColor(
                    round(link.red() * 0.18 + hover_bg.red() * 0.82),
                    round(link.green() * 0.18 + hover_bg.green() * 0.82),
                    round(link.blue() * 0.18 + hover_bg.blue() * 0.82),
                    round(link.alpha() * 0.18 + hover_bg.alpha() * 0.82),
                )
        else:
            fg = self.palette().text().color()
            hover_bg = self.palette().highlight().color()

        if self._hovered:
            painter.fillRect(self.rect(), hover_bg)

        icon_size = 14
        icon_x = 7
        icon_y = (self.height() - icon_size) // 2
        icon_rect = QRect(icon_x, icon_y, icon_size, icon_size)
        if self._icon_kind:
            self._draw_menu_icon(painter, icon_rect, fg)
        elif not self._icon.isNull():
            self._icon.paint(painter, icon_rect)

        painter.setPen(fg)
        painter.setFont(self.font())
        text_x = 28
        right_pad = 22 if self._has_submenu else 8
        text_rect = QRect(text_x, 0, max(1, self.width() - text_x - right_pad), self.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)

        if self._has_submenu:
            pen = QPen(fg, 1.25, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            cx = self.width() - 12
            cy = self.height() // 2
            painter.drawLine(cx - 2, cy - 4, cx + 2, cy)
            painter.drawLine(cx + 2, cy, cx - 2, cy + 4)

        painter.end()


class _CompactThemeActionWidget(QWidget):
    triggered = Signal(str)

    def __init__(self, text: str, mode: str, parent=None):
        super().__init__(parent)
        self._text = text
        self._mode = mode
        self._checked = False
        self._hovered = False
        self._tokens = None
        self.setMouseTracking(True)
        self.setFixedHeight(28)
        self.setMinimumWidth(96)

    def set_checked(self, checked: bool):
        self._checked = bool(checked)
        self.update()

    def set_tokens(self, tokens):
        self._tokens = tokens
        self.update()

    def set_hovered(self, hovered: bool):
        hovered = bool(hovered)
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self._hovered = False
            self.update()
            self.triggered.emit(self._mode)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        tokens = self._tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if tokens is not None:
            fg = QColor(tokens.text_primary)
            hover_bg = QColor(tokens.button_hover_bg)
            menu_bg = QColor(tokens.surface_panel)
            if menu_bg.lightness() >= 128:
                link = QColor(tokens.text_linkish)
                hover_bg = QColor(
                    round(link.red() * 0.18 + hover_bg.red() * 0.82),
                    round(link.green() * 0.18 + hover_bg.green() * 0.82),
                    round(link.blue() * 0.18 + hover_bg.blue() * 0.82),
                    round(link.alpha() * 0.18 + hover_bg.alpha() * 0.82),
                )
        else:
            fg = self.palette().text().color()
            hover_bg = self.palette().highlight().color()

        if self._hovered:
            painter.fillRect(self.rect(), hover_bg)

        if self._checked:
            pen = QPen(fg, 1.55, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            cy = self.height() // 2
            painter.drawLine(8, cy, 10, cy + 3)
            painter.drawLine(10, cy + 3, 15, cy - 4)

        painter.setPen(fg)
        painter.setFont(self.font())
        text_rect = QRect(26, 0, max(1, self.width() - 32), self.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)

        painter.end()


class _CompactThemeWidgetAction(QWidgetAction):
    def __init__(self, text: str, mode: str, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._widget = _CompactThemeActionWidget(text, mode)
        self.setDefaultWidget(self._widget)

    def data(self):
        return self._mode

    def set_checked(self, checked: bool):
        self._widget.set_checked(checked)

    def set_tokens(self, tokens):
        self._widget.set_tokens(tokens)

    def set_icon(self, icon: QIcon):
        self._widget.set_icon(icon)

    def widget(self):
        return self._widget



class _CompactWidgetAction(QWidgetAction):
    def __init__(self, icon: QIcon, text: str, *, has_submenu: bool = False, close_menu_on_trigger: bool = True, icon_kind: str | None = None, parent=None):
        super().__init__(parent)
        self._widget = _CompactMenuActionWidget(icon, text, has_submenu=has_submenu, icon_kind=icon_kind)
        if close_menu_on_trigger:
            self._widget.triggered.connect(self.trigger)
        self.setDefaultWidget(self._widget)

    def set_tokens(self, tokens):
        self._widget.set_tokens(tokens)

    def set_icon_kind(self, icon_kind: str | None):
        if hasattr(self._widget, "set_icon_kind"):
            self._widget.set_icon_kind(icon_kind)

    def widget(self):
        return self._widget



class GlobalBarWidget(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("GlobalBarWidget")

        self.settings = QSettings("MTools", "CompactMe")
        self._logger = LoggingService()
        saved_import_dir = self.settings.value("last_import_dir", "")
        saved_import_dir = os.path.normpath(str(saved_import_dir or ""))
        public_desktop = os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")
        # Windows exposes the personal and public desktop folders as one shell
        # namespace. QFileDialog accepts this namespace and shows both sets of
        # items while preserving the native Windows layout.
        unified_desktop = "shell:Desktop"
        fallback_import_dir = (
            public_desktop if os.path.isdir(public_desktop) else os.path.expanduser("~")
        )
        user_desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        saved_is_user_desktop = os.path.normcase(saved_import_dir) == os.path.normcase(
            os.path.normpath(user_desktop)
        )
        self._last_import_dir = (
            unified_desktop
            if saved_is_user_desktop and os.path.isdir(public_desktop)
            else saved_import_dir if os.path.isdir(saved_import_dir) else fallback_import_dir
        )

        self._theme_submenu_hover_timer = QTimer(self)
        self._theme_submenu_hover_timer.setInterval(16)
        self._theme_submenu_hover_timer.timeout.connect(self._hide_theme_menu_if_focus_lost)

        layout = QHBoxLayout(self)
        self._applying_styles = False
        self._embedded_in_title_bar = False
        self._apply_styles()

        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        self.btn_add = QPushButton("Adicionar")
        self.btn_add.setToolTip("Adiciona arquivos em modo assistido")
        self.btn_add.setToolTipDuration(5000)
        self.btn_add.setMouseTracking(True)
        self.btn_add_quick = QPushButton("Adicionar Rápido")
        self.btn_add_quick.setToolTip("Adiciona arquivos em modo simples")
        self.btn_import_folder = QPushButton("Importar Pasta")

        for b in [
            self.btn_add,
            self.btn_add_quick,
            self.btn_import_folder,
        ]:
            b.setFixedHeight(28)
            b.setMaximumHeight(28)
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            _install_normal_state_text_nudge(b, 1)

        self._update_embedded_button_widths()

        layout.addWidget(self.btn_add)
        layout.addWidget(self.btn_add_quick)
        layout.addWidget(self.btn_import_folder)

        self.menu_button = QToolButton()
        self.menu_button.setObjectName("GlobalBarMenuButton")
        self.menu_button.setCheckable(False)
        self.menu_button.setText("")
        icon_path = os.path.join(os.path.dirname(__file__), "icons", "vertical_dots.svg")
        if os.path.exists(icon_path):
            menu_icon = load_icon("vertical_dots.svg")
            if not menu_icon.isNull():
                self.menu_button.setIcon(menu_icon)
                self.menu_button.setIconSize(QSize(18, 18))
            else:
                self.menu_button.setText("⋮")
        else:
            self.menu_button.setText("⋮")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.menu_button.setFixedSize(36, 30)
        from PySide6.QtGui import QFont

        font = self.menu_button.font()
        font.setPointSize(20)
        font.setWeight(QFont.Weight.DemiBold)
        self.menu_button.setFont(font)
        self._build_menu()

        self._menu_button_slot = QWidget()
        self._menu_button_slot.setFixedSize(36, 31)
        self._menu_button_slot.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        menu_button_slot_layout = QVBoxLayout(self._menu_button_slot)
        menu_button_slot_layout.setContentsMargins(0, 2, 0, 0)
        menu_button_slot_layout.setSpacing(0)
        menu_button_slot_layout.addWidget(self.menu_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._menu_button_slot, 0, Qt.AlignmentFlag.AlignTop)

        self.btn_add.clicked.connect(self._open_single_dialog)
        self.btn_add_quick.clicked.connect(self._open_multi_dialog)
        self.btn_import_folder.clicked.connect(self._import_folder)
        self.menu_button.clicked.connect(self._show_menu)

    def _button_target_width(self, button: QPushButton, extra_padding: int = 28, minimum: int = 0) -> int:
        return max(minimum, button.fontMetrics().horizontalAdvance(button.text()) + extra_padding)

    def _update_embedded_button_widths(self):
        if self._embedded_in_title_bar:
            # Keep the three primary import actions as a compact, equal-width group.
            for button in (self.btn_add, self.btn_add_quick, self.btn_import_folder):
                button.setMinimumWidth(200)
                button.setMaximumWidth(200)
                button.setFixedWidth(200)
            return

        self.btn_add.setFixedWidth(self._button_target_width(self.btn_add, extra_padding=34, minimum=108))
        self.btn_add_quick.setFixedWidth(self._button_target_width(self.btn_add_quick, extra_padding=34, minimum=132))
        self.btn_import_folder.setFixedWidth(self._button_target_width(self.btn_import_folder, extra_padding=34, minimum=128))

    def _apply_styles(self):
        if self._applying_styles:
            return
        self._applying_styles = True
        try:
            tokens = build_theme_tokens(self.palette())
            push_stylesheet = build_button_stylesheet(tokens, min_height=28, border_radius=4, horizontal_padding=10)
            tool_stylesheet = build_small_button_stylesheet(tokens, border_radius=4)
            if self._embedded_in_title_bar:
                def _blend(first: QColor, second: QColor, first_weight: float) -> QColor:
                    first_weight = max(0.0, min(1.0, first_weight))
                    second_weight = 1.0 - first_weight
                    return QColor(
                        round(first.red() * first_weight + second.red() * second_weight),
                        round(first.green() * first_weight + second.green() * second_weight),
                        round(first.blue() * first_weight + second.blue() * second_weight),
                        round(first.alpha() * first_weight + second.alpha() * second_weight),
                    )

                blue_seed = QColor(108, 176, 255, 255)
                embedded_border = _blend(tokens.button_border, blue_seed, 0.46).name(QColor.NameFormat.HexArgb)
                embedded_hover_border = _blend(tokens.button_hover_border, blue_seed, 0.34).name(QColor.NameFormat.HexArgb)
                embedded_pressed_border = _blend(tokens.button_pressed_border, blue_seed, 0.28).name(QColor.NameFormat.HexArgb)
                embedded_disabled_border = _blend(tokens.button_disabled_border, blue_seed, 0.58).name(QColor.NameFormat.HexArgb)
                menu_hover_border = _blend(tokens.button_hover_border, blue_seed, 0.40).name(QColor.NameFormat.HexArgb)
                menu_pressed_border = _blend(tokens.button_pressed_border, blue_seed, 0.32).name(QColor.NameFormat.HexArgb)
                menu_hover_bg = _blend(tokens.button_hover_bg, blue_seed, 0.88).name(QColor.NameFormat.HexArgb)
                menu_pressed_bg = _blend(tokens.button_pressed_bg, blue_seed, 0.82).name(QColor.NameFormat.HexArgb)
                menu_fg = tokens.text_primary.name(QColor.NameFormat.HexArgb)

                self.setStyleSheet("QFrame#GlobalBarWidget { background: transparent; border: none; }")
                direct_push = push_stylesheet + f"""
QPushButton {{
    min-height: 42px;
    max-height: 42px;
    padding-top: 2px;
    padding-bottom: 0px;
    border-width: 2px;
    border-style: solid;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    border-color: {embedded_border};
}}
QPushButton:hover:!disabled {{
    min-height: 42px;
    max-height: 42px;
    padding-top: 2px;
    padding-bottom: 0px;
    border-width: 2px;
    border-style: solid;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    border-color: {embedded_hover_border};
}}
QPushButton:pressed:!disabled {{
    min-height: 42px;
    max-height: 42px;
    padding-top: 2px;
    padding-bottom: 0px;
    border-width: 2px;
    border-style: solid;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    border-color: {embedded_pressed_border};
}}
QPushButton:disabled {{
    min-height: 42px;
    max-height: 42px;
    padding-top: 2px;
    padding-bottom: 0px;
    border-width: 2px;
    border-style: solid;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    border-color: {embedded_disabled_border};
}}
"""
                direct_tool = tool_stylesheet + "\nQToolButton { min-height: 34px; max-height: 34px; padding-top: 1px; padding-bottom: 1px; }" + f"\nQToolButton#GlobalBarMenuButton {{ background: transparent; color: {menu_fg}; border: 1px solid transparent; border-radius: 4px; padding-left: 2px; padding-right: 0px; }}" + "\nQToolButton#GlobalBarMenuButton::menu-indicator { image: none; width: 0px; height: 0px; }" + f"\nQToolButton#GlobalBarMenuButton:hover:!disabled {{ background: {menu_hover_bg}; color: {menu_fg}; border: 1px solid {menu_hover_border}; border-radius: 4px; }}" + f"\nQToolButton#GlobalBarMenuButton:pressed:!disabled {{ background: {menu_pressed_bg}; color: {menu_fg}; border: 1px solid {menu_pressed_border}; border-radius: 4px; }}"
                for button in (self.btn_add, self.btn_add_quick, self.btn_import_folder):
                    button.setStyleSheet(direct_push)
                # Quando o botão original de 3 pontos está anexado à title bar
                # da lista, o FileListContainer é o dono da geometria/stylesheet
                # final. Evita que uma troca de tema reaplique o estilo genérico
                # do GlobalBar e reduza visualmente o botão em ~2 px.
                if self.menu_button.parent() is self:
                    self.menu_button.setStyleSheet(direct_tool)
            else:
                push_prefixed = push_stylesheet.replace("QPushButton", "#GlobalBarWidget QPushButton")
                tool_prefixed = tool_stylesheet.replace("QToolButton", "#GlobalBarWidget QToolButton")
                self.setStyleSheet(
                    push_prefixed
                    + "\n"
                    + tool_prefixed
                    + "\n#GlobalBarWidget QPushButton { min-height: 28px; max-height: 28px; padding-top: 0px; padding-bottom: 0px; }"
                    + "\n#GlobalBarWidget QToolButton { min-height: 28px; max-height: 28px; padding-top: 0px; padding-bottom: 0px; }"
                    + "\n#GlobalBarWidget QToolButton#GlobalBarMenuButton { padding-left: 2px; padding-right: 0px; }"
                )

            self._apply_menu_styles(tokens)
        finally:
            self._applying_styles = False

    def _apply_menu_styles(self, tokens):
        if not hasattr(self, "menu") or self.menu is None:
            return

        def _mix(first: QColor, second: QColor, first_weight: float) -> QColor:
            first_weight = max(0.0, min(1.0, first_weight))
            second_weight = 1.0 - first_weight
            return QColor(
                round(first.red() * first_weight + second.red() * second_weight),
                round(first.green() * first_weight + second.green() * second_weight),
                round(first.blue() * first_weight + second.blue() * second_weight),
                round(first.alpha() * first_weight + second.alpha() * second_weight),
            )

        menu_bg_color = QColor(tokens.surface_panel)
        menu_selected_bg_color = QColor(tokens.button_hover_bg)
        if menu_bg_color.lightness() >= 128:
            # In light mode the generic button hover token is too close to the
            # popup surface. Add a restrained theme-link tint so hover is readable
            # without introducing a local literal color.
            menu_selected_bg_color = _mix(QColor(tokens.text_linkish), menu_selected_bg_color, 0.18)

        menu_bg = menu_bg_color.name(QColor.NameFormat.HexArgb)
        menu_fg = tokens.text_primary.name(QColor.NameFormat.HexArgb)
        menu_border = tokens.border_panel.name(QColor.NameFormat.HexArgb)
        menu_disabled_fg = tokens.button_disabled_fg.name(QColor.NameFormat.HexArgb)
        menu_selected_bg = menu_selected_bg_color.name(QColor.NameFormat.HexArgb)
        menu_selected_fg = tokens.text_primary.name(QColor.NameFormat.HexArgb)
        separator = tokens.surface_list_divider.name(QColor.NameFormat.HexArgb)

        self.menu.setMinimumWidth(132)
        self.menu.setStyleSheet(
            "QMenu {"
            f" background: {menu_bg};"
            f" color: {menu_fg};"
            f" border: 1px solid {menu_border};"
            " padding: 6px 0px;"
            " }"
            "QMenu::item {"
            " padding: 8px 24px 8px 24px;"
            " background: transparent;"
            " min-width: 80px;"
            f" color: {menu_fg};"
            " }"
            "QMenu::icon {"
            " left: 8px;"
            " }"
            "QMenu::item:selected {"
            f" background: {menu_selected_bg};"
            f" color: {menu_selected_fg};"
            " }"
            "QMenu::item:disabled {"
            f" color: {menu_disabled_fg};"
            " }"
            "QMenu::separator {"
            f" height: 1px; background: {separator};"
            " margin: 6px 12px;"
            " }"
        )
        self._apply_menu_icons(tokens)

    def _make_menu_icon(self, kind: str, color: QColor) -> QIcon:
        """Build small monochrome menu icons from the active palette.

        Icons are generated in code so the menu keeps theme-aware contrast
        without adding a dependency on external icon packs.
        """
        pixmap = QPixmap(14, 14)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, 1.45, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if kind == "prefs":
            for y, knob_x in ((4, 5), (7, 10), (10, 7)):
                painter.drawLine(2, y, 12, y)
                painter.setBrush(QBrush(color))
                painter.drawEllipse(QRectF(knob_x - 1.35, y - 1.35, 2.7, 2.7))
                painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "appearance":
            painter.drawEllipse(QRectF(2.4, 2.4, 9.2, 9.2))
            painter.setBrush(QBrush(color))
            painter.drawPie(QRectF(2.4, 2.4, 9.2, 9.2), 90 * 16, 180 * 16)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif kind == "help":
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "?")
        elif kind == "about":
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSize(10)
            painter.setFont(font)
            painter.drawText(pixmap.rect().adjusted(0, -1, 0, -1), Qt.AlignmentFlag.AlignCenter, "i")
        elif kind == "exit":
            painter.drawLine(3, 2, 9, 2)
            painter.drawLine(3, 12, 9, 12)
            painter.drawLine(3, 2, 3, 12)
            painter.drawLine(6, 7, 12, 7)
            painter.drawLine(9, 5, 12, 7)
            painter.drawLine(9, 9, 12, 7)

        painter.end()
        return QIcon(pixmap)

    def _menu_icon_color(self, tokens) -> QColor:
        # Used only for the initial placeholder QIcon. The visible compact menu
        # icons are drawn directly in _CompactMenuActionWidget.paintEvent() using
        # the exact same foreground color as the row label.
        return QColor(tokens.text_primary)

    def _apply_menu_icons(self, tokens):
        if not hasattr(self, "menu") or self.menu is None:
            return

        for action_name in ("act_prefs", "act_theme_compact", "act_help", "act_about", "act_exit"):
            action = getattr(self, action_name, None)
            if hasattr(action, "set_tokens"):
                action.set_tokens(tokens)

        if hasattr(self, "theme_actions"):
            for action in self.theme_actions.values():
                if hasattr(action, "set_tokens"):
                    action.set_tokens(tokens)


    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self._update_embedded_button_widths()
            self._apply_styles()

    def export_controls_for_title_bar(self):
        self._embedded_in_title_bar = True
        layout = self.layout()
        for widget in (
            self.btn_add,
            self.btn_add_quick,
            self.btn_import_folder,
            self.menu_button,
        ):
            layout.removeWidget(widget)

        for button in (self.btn_add, self.btn_add_quick, self.btn_import_folder):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.menu_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._update_embedded_button_widths()
        self.hide()
        self._apply_styles()
        return {
            "import_buttons": [
                self.btn_add,
                self.btn_add_quick,
                self.btn_import_folder,
            ],
            "menu_button": self.menu_button,
        }

    def set_import_controls_enabled(self, enabled: bool):
        self.btn_add.setEnabled(enabled)
        self.btn_add_quick.setEnabled(enabled)
        self.btn_import_folder.setEnabled(enabled)

    def open_assisted_add_dialog(self):
        self._open_single_dialog()

    def open_quick_add_dialog(self):
        self._open_multi_dialog()

    def open_import_folder_dialog(self):
        self._import_folder()

    def _close_configuration_overlay_before_import(self):
        parent = self.window()
        closer = getattr(parent, "close_configuration", None)
        if callable(closer):
            closer()

    def _dispatch_import_batch(self, *, paths=None, folder=None, job_overrides=None):
        parent = self.window()
        handler = getattr(parent, "start_import_batch", None)
        if callable(handler):
            handler(paths=paths, folder=folder, job_overrides=job_overrides)
            return True
        return False

    def _resolve_job_overrides_for_path(self, path, job_overrides=None, *, media_info=None, source_size=None):
        ext = os.path.splitext(str(path or ""))[1].lower()
        raw_requested_mode = ""
        if isinstance(job_overrides, dict):
            raw_requested_mode = str(
                job_overrides.get("compression_mode")
                or job_overrides.get("profile_mode")
                or ""
            ).strip().lower()

        if raw_requested_mode == "automatic" and media_info is not None:
            profile_data = build_automatic_profile_from_media_info(
                media_info,
                source_size,
                source_path=str(path or "") or None,
            )
        else:
            profile_data = dict(default_quick_profile())
            if isinstance(job_overrides, dict):
                profile_data.update(job_overrides)

        requested_mode = str(profile_data.get("profile_mode") or profile_data.get("compression_mode") or "quick").strip().lower()
        should_force_audio_profile = ext in AUDIO_EXT and requested_mode != "audio"
        if should_force_audio_profile:
            audio_profile = dict(default_audio_profile())
            if isinstance(job_overrides, dict):
                for key in (
                    "audio_output_format",
                    "audio_output_extension",
                    "audio_bitrate_kbps",
                    "audio_track_policy",
                    "selected_track_id",
                    "selected_track_ids",
                    "audio_channel_policy",
                    "audio_sample_rate",
                    "audio_volume_normalization",
                    "audio_metadata_policy",
                    "extract_subtitle",
                ):
                    if key in job_overrides:
                        audio_profile[key] = job_overrides[key]
            profile_data = audio_profile

        return profile_data


    def _open_single_dialog(self):
        self._close_configuration_overlay_before_import()

        media_file_filter = (
            "Todos os arquivos de mídia "
            "(*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts *.mts *.m2ts "
            "*.3gp *.mpg *.mpeg *.vob *.mp3 *.aac *.wav *.flac *.ogg *.m4a *.wma "
            "*.opus *.alac);;"
            "Arquivos de vídeo "
            "(*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts *.mts *.m2ts "
            "*.3gp *.mpg *.mpeg *.vob);;"
            "Arquivos de áudio "
            "(*.mp3 *.aac *.wav *.flac *.ogg *.m4a *.wma *.opus *.alac);;"
            "Todos os arquivos (*.*)"
        )

        files, _ = QFileDialog.getOpenFileNames(
            self, "Selecionar arquivos", self._last_import_dir, media_file_filter
        )

        if not files:
            return

        self._last_import_dir = os.path.dirname(files[0])
        self.settings.setValue("last_import_dir", self._last_import_dir)

        video_count = 0
        audio_count = 0

        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in AUDIO_EXT:
                audio_count += 1
            else:
                video_count += 1

        dlg = AssistedImportDialog(video_count, audio_count, self, files=files)

        from PySide6.QtWidgets import QDialog

        if dlg.exec() != QDialog.Accepted:
            return

        payload = getattr(dlg, "result_payload", None)

        if not payload:
            return

        all_files = payload.get("files", [])
        per_file_payloads = payload.get("per_file_payloads") if isinstance(payload.get("per_file_payloads"), dict) else {}
        if per_file_payloads:
            job_overrides = {
                path: derive_job_profile_from_payload(per_file_payloads.get(path) or payload)
                for path in all_files
            }
        else:
            job_overrides = derive_job_profile_from_payload(payload)

        if all_files and not self._dispatch_import_batch(paths=all_files, job_overrides=job_overrides):
            for p in all_files:
                path_overrides = job_overrides.get(p) if isinstance(job_overrides, dict) and p in job_overrides else job_overrides
                self._create_job(p, job_overrides=path_overrides)

    def _open_multi_dialog(self):
        self._close_configuration_overlay_before_import()

        media_file_filter = (
            "Todos os arquivos de mídia "
            "(*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts *.mts *.m2ts "
            "*.3gp *.mpg *.mpeg *.vob *.mp3 *.aac *.wav *.flac *.ogg *.m4a *.wma "
            "*.opus *.alac);;"
            "Arquivos de vídeo "
            "(*.mp4 *.mkv *.avi *.mov *.webm *.flv *.wmv *.m4v *.ts *.mts *.m2ts "
            "*.3gp *.mpg *.mpeg *.vob);;"
            "Arquivos de áudio "
            "(*.mp3 *.aac *.wav *.flac *.ogg *.m4a *.wma *.opus *.alac);;"
            "Todos os arquivos (*.*)"
        )

        files, _ = QFileDialog.getOpenFileNames(
            self, "Adicionar arquivos diretamente", self._last_import_dir, media_file_filter
        )
        if files:
            self._last_import_dir = os.path.dirname(files[0])
            self.settings.setValue("last_import_dir", self._last_import_dir)
            quick_profile = default_quick_profile()
            if self._dispatch_import_batch(paths=files, job_overrides=quick_profile):
                return
        for p in files:
            self._create_job(p, job_overrides=default_quick_profile())

    def _import_folder(self):
        self._close_configuration_overlay_before_import()

        folder = QFileDialog.getExistingDirectory(
            self, "Selecionar pasta", self._last_import_dir
        )
        if not folder:
            return
        self._last_import_dir = folder
        self.settings.setValue("last_import_dir", self._last_import_dir)
        if self._dispatch_import_batch(folder=folder, job_overrides=default_quick_profile()):
            return
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(VIDEO_EXT + AUDIO_EXT):
                    self._create_job(os.path.join(root, f), job_overrides=default_quick_profile())

    def create_job_from_probe_result(self, path, probe_result, source_size=None, job_overrides=None):
        name = os.path.basename(path)
        audio_streams = []
        audio_track_count = 0
        primary_audio_channels = 2
        primary_audio_sample_rate = None
        if len(probe_result) >= 10:
            (
                codec,
                res,
                fps,
                duration,
                container,
                input_bitrate,
                audio_streams,
                audio_track_count,
                primary_audio_channels,
                primary_audio_sample_rate,
            ) = probe_result[:10]
        elif len(probe_result) >= 9:
            (
                codec,
                res,
                fps,
                duration,
                container,
                input_bitrate,
                audio_streams,
                audio_track_count,
                primary_audio_channels,
            ) = probe_result[:9]
        elif len(probe_result) >= 6:
            codec, res, fps, duration, container, input_bitrate = probe_result[:6]
        else:
            codec, res, fps, duration, container = probe_result
            input_bitrate = None

        profile_data = self._resolve_job_overrides_for_path(
            path,
            job_overrides,
            media_info=probe_result,
            source_size=source_size,
        )

        job = SimpleNamespace(
            file_name=name,
            status="ANALYZING",
            progress=0,
            codec=codec,
            resolution=res,
            fps=fps,
            duration=duration,
            container=container,
            source_path=path,
            source_size=source_size,
            estimated_size_bytes=None,
            input_bitrate=input_bitrate,
            output_bitrate=None,
            audio_streams=audio_streams,
            audio_track_count=audio_track_count,
            primary_audio_channels=primary_audio_channels,
            primary_audio_sample_rate=primary_audio_sample_rate,
            **profile_data,
        )

        ensure_job_state(job)
        event_bridge.emit("job_enqueued", {"job": job})

        patch_job(job, output_size_bytes=None, output_bitrate=None)
        event_bridge.emit("job_updated", {"job": job})

        threading.Thread(
            target=self._generate_thumbnail, args=(job,), daemon=True
        ).start()

        if os.path.splitext(job.source_path)[1].lower() in VIDEO_EXT + AUDIO_EXT:
            threading.Thread(
                target=self._estimate_size_real, args=(job,), daemon=True
            ).start()

        return job

    def _create_job(self, path, job_overrides=None):
        name = os.path.basename(path)

        # validation before import
        try:
            result = probe(path)
            if result is None:
                from PySide6.QtWidgets import QMessageBox

                QMessageBox.warning(
                    self,
                    "Arquivo inválido",
                    f"O arquivo '{os.path.basename(path)}' não pôde ser importado.",
                )
                return

            audio_streams = []
            audio_track_count = 0
            primary_audio_channels = 2
            primary_audio_sample_rate = None
            if len(result) >= 10:
                (
                    codec, res, fps, duration, container, input_bitrate,
                    audio_streams, audio_track_count, primary_audio_channels, primary_audio_sample_rate,
                ) = result[:10]
            elif len(result) >= 9:
                (
                    codec, res, fps, duration, container, input_bitrate,
                    audio_streams, audio_track_count, primary_audio_channels,
                ) = result[:9]
            elif len(result) >= 6:
                codec, res, fps, duration, container, input_bitrate = result[:6]
            else:
                codec, res, fps, duration, container = result
                input_bitrate = None
        except Exception:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "Erro ao analisar arquivo",
                f"Falha ao analisar '{os.path.basename(path)}'.",
            )
            return

        try:
            initial_source_size = os.path.getsize(path)
        except Exception:
            initial_source_size = None

        profile_data = self._resolve_job_overrides_for_path(
            path,
            job_overrides,
            media_info=result,
            source_size=initial_source_size,
        )

        job = SimpleNamespace(
            file_name=name,
            status="ANALYZING",
            progress=0,
            codec=codec,
            resolution=res,
            fps=fps,
            duration=duration,
            container=container,
            source_path=path,
            source_size=initial_source_size,
            estimated_size_bytes=None,
            input_bitrate=input_bitrate,
            output_bitrate=None,
            audio_streams=audio_streams,
            audio_track_count=audio_track_count,
            primary_audio_channels=primary_audio_channels,
            primary_audio_sample_rate=primary_audio_sample_rate,
            **profile_data,
        )

        ensure_job_state(job)
        event_bridge.emit("job_enqueued", {"job": job})

        threading.Thread(target=self._probe_metadata, args=(job, result), daemon=True).start()

    def _probe_metadata(self, job, initial_probe=None):
        result = initial_probe or probe(job.source_path)
        if result is not None:
            audio_streams = []
            audio_track_count = 0
            primary_audio_channels = 2
            primary_audio_sample_rate = None
            if len(result) >= 10:
                (
                    codec, res, fps, duration, container, input_bitrate,
                    audio_streams, audio_track_count, primary_audio_channels, primary_audio_sample_rate,
                ) = result[:10]
            elif len(result) >= 9:
                (
                    codec, res, fps, duration, container, input_bitrate,
                    audio_streams, audio_track_count, primary_audio_channels,
                ) = result[:9]
            elif len(result) >= 6:
                codec, res, fps, duration, container, input_bitrate = result[:6]
            else:
                codec, res, fps, duration, container = result
                input_bitrate = None
            updates = {}
            if codec not in (None, "?"):
                updates["codec"] = codec
            if res not in (None, "?"):
                updates["resolution"] = res
            if fps not in (None, "?"):
                updates["fps"] = fps
            if duration not in (None, "?"):
                updates["duration"] = duration
            if container not in (None, "?"):
                updates["container"] = container
            if input_bitrate not in (None, "?"):
                updates["input_bitrate"] = input_bitrate
            if audio_streams is not None:
                updates["audio_streams"] = audio_streams
            if audio_track_count not in (None, ""):
                updates["audio_track_count"] = audio_track_count
            if primary_audio_channels not in (None, ""):
                updates["primary_audio_channels"] = primary_audio_channels
            if primary_audio_sample_rate not in (None, ""):
                updates["primary_audio_sample_rate"] = primary_audio_sample_rate
            if updates:
                patch_job(job, **updates)

        try:
            patch_job(job, source_size=os.path.getsize(job.source_path))
        except Exception:
            patch_job(job, source_size=None)

        patch_job(job, estimated_size_bytes=None, output_size_bytes=None, output_bitrate=None)

        event_bridge.emit("job_updated", {"job": job})

        threading.Thread(
            target=self._generate_thumbnail, args=(job,), daemon=True
        ).start()

        if os.path.splitext(job.source_path)[1].lower() in VIDEO_EXT + AUDIO_EXT:
            threading.Thread(
                target=self._estimate_size_real, args=(job,), daemon=True
            ).start()

    def _estimate_size_real(self, job):
        try:
            estimated = estimate_size_for_profile(job, estimate_size_crf)
            if estimated is None:
                return

            source_size = get_job_attr(job, "source_size", None)
            if source_size is None:
                try:
                    source_size = os.path.getsize(job.source_path)
                    patch_job(job, source_size=source_size)
                except Exception:
                    _LOGGER.debug("Unable to read source size while estimating output", exc_info=True)
                    source_size = None

            if estimated <= 0:
                return

            estimated_output_bitrate = estimate_output_bitrate_for_profile(job, estimated)

            current = get_job_attr(job, "estimated_size_bytes", None)
            try:
                current = int(current) if current is not None else None
            except Exception:
                _LOGGER.debug("Ignoring invalid current estimated size value", exc_info=True)
                current = None

            current_bitrate = get_job_attr(job, "estimated_output_bitrate", None)
            try:
                current_bitrate = int(current_bitrate) if current_bitrate is not None else None
            except Exception:
                _LOGGER.debug("Ignoring invalid current estimated bitrate value", exc_info=True)
                current_bitrate = None

            current_status = str(getattr(job, "status", "") or "").upper()
            actionable = estimate_output_is_actionable(
                job,
                estimated_size_bytes=estimated,
                source_size_bytes=source_size,
            )
            target_status = None
            if current_status in {"READY", "IDLE", "PENDING", "NO_GAIN", "ANALYZING"}:
                target_status = "NO_GAIN" if actionable is False else "READY"

            if (
                current == estimated
                and current_bitrate == estimated_output_bitrate
                and (target_status is None or current_status == target_status)
            ):
                return

            patch = {
                "estimated_size_bytes": estimated,
                "estimated_output_bitrate": estimated_output_bitrate,
            }
            if target_status is not None:
                patch["status"] = target_status

            patch_job(job, **patch)
            event_bridge.emit("job_updated", {"job": job})

        except Exception:
            _LOGGER.debug("Ignoring background size estimation failure", exc_info=True)
            return

    def _generate_thumbnail(self, job):
        thumb = None
        try:
            tmp = tempfile.gettempdir()
            try:
                src = str(getattr(job, "source_path", "") or "")
                stat = os.stat(src) if src and os.path.exists(src) else None
                stamp = f"{getattr(stat, 'st_mtime_ns', 0)}:{getattr(stat, 'st_size', 0)}"
                digest = hashlib.sha1(f"v2:{src}:{stamp}".encode("utf-8", errors="ignore")).hexdigest()[:16]
                base_name = os.path.splitext(os.path.basename(src or getattr(job, "file_name", "thumb")))[0] or "thumb"
                safe_base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in base_name)[:48] or "thumb"
                out = os.path.join(tmp, f"{safe_base}_{digest}.jpg")
            except Exception:
                _LOGGER.debug("Falling back to simple thumbnail output path", exc_info=True)
                out = os.path.join(tmp, str(getattr(job, "file_name", "thumb")) + "_v2.jpg")
            thumb = generate_thumbnail(job.source_path, out)
        except Exception:
            _LOGGER.debug("Thumbnail generation failed; keeping job without thumbnail", exc_info=True)
            thumb = None

        current_status = getattr(job, "status", None)
        status = "READY" if current_status == "ANALYZING" else (current_status or "READY")
        patch_job(job, thumbnail=thumb, status=status)
        event_bridge.emit("job_updated", {"job": job})

    def _build_menu(self):
        self.menu = QMenu(self)

        font = self.menu.font()
        size = font.pointSize()
        if size <= 0:
            size = 10
        font.setPointSize(size + 1)
        self.menu.setFont(font)

        # The top-level popup uses compact QWidgetAction rows instead of
        # QAction.icon(). Qt reserves a large native icon/check column for
        # QAction icons, which made the icon-to-label gap look excessive.
        # Drawing the row ourselves keeps the menu visually compact without
        # touching the submenu/checkmark behavior.
        menu_tokens = build_theme_tokens(self.palette())
        icon_color = self._menu_icon_color(menu_tokens)
        self.act_prefs = _CompactWidgetAction(self._make_menu_icon("prefs", icon_color), "Preferências", icon_kind="prefs", parent=self.menu)
        self.menu.addAction(self.act_prefs)
        self.menu.addSeparator()

        self.theme_menu = QMenu("Aparência", self.menu)
        self.theme_menu.setFont(font)
        self.theme_menu.setMinimumWidth(96)
        self.act_theme_compact = _CompactWidgetAction(
            self._make_menu_icon("appearance", icon_color),
            "Aparência",
            has_submenu=True,
            icon_kind="appearance",
            close_menu_on_trigger=False,
            parent=self.menu,
        )
        self.act_theme_compact.setMenu(self.theme_menu)
        self.menu.addAction(self.act_theme_compact)
        self.act_theme_compact.widget().hovered.connect(self._show_theme_menu_from_compact_action)
        self.act_theme_compact.widget().left.connect(self._schedule_theme_menu_hide_check)
        self.act_theme_compact.widget().triggered.connect(self._show_theme_menu_from_compact_action)

        self.theme_action_group = None
        self.theme_actions = {}
        for label, mode in (
            ("Sistema", "system"),
            ("Claro", "light"),
            ("Escuro", "dark"),
        ):
            action = _CompactThemeWidgetAction(label, mode, parent=self.theme_menu)
            self.theme_menu.addAction(action)
            self.theme_actions[mode] = action
            action.widget().triggered.connect(lambda theme_mode, _mode=mode: self._handle_theme_action(_mode))

        self.menu.addSeparator()
        self.act_help = _CompactWidgetAction(self._make_menu_icon("help", icon_color), "Ajuda", icon_kind="help", parent=self.menu)
        self.menu.addAction(self.act_help)
        self.act_about = _CompactWidgetAction(self._make_menu_icon("about", icon_color), "Sobre", icon_kind="about", parent=self.menu)
        self.menu.addAction(self.act_about)
        self.menu.addSeparator()
        self.act_exit = _CompactWidgetAction(self._make_menu_icon("exit", icon_color), "Sair", icon_kind="exit", parent=self.menu)
        self.menu.addAction(self.act_exit)

        self.act_prefs.triggered.connect(self._handle_preferences_action)
        self.act_help.triggered.connect(self._handle_help_action)
        self.act_about.triggered.connect(self._handle_about_action)
        self.act_exit.triggered.connect(self._handle_exit_action)

        for action in (self.act_prefs, self.act_help, self.act_about, self.act_exit):
            if hasattr(action, "widget"):
                action.widget().hovered.connect(self._hide_theme_menu_from_compact_action)

        self.menu.aboutToHide.connect(self._on_main_menu_about_to_hide)
        self.theme_menu.aboutToHide.connect(self._clear_compact_menu_hover_states)
        self.menu.installEventFilter(self)
        self.theme_menu.installEventFilter(self)

        self._refresh_theme_menu_checks()
        self._apply_menu_styles(build_theme_tokens(self.palette()))


    def _hide_theme_menu_from_compact_action(self):
        if hasattr(self, "_theme_submenu_hover_timer"):
            self._theme_submenu_hover_timer.stop()
        if hasattr(self, "theme_menu") and self.theme_menu is not None and self.theme_menu.isVisible():
            self.theme_menu.hide()
        action = getattr(self, "act_theme_compact", None)
        widget = action.widget() if hasattr(action, "widget") else None
        if widget is not None and getattr(widget, "_hovered", False):
            widget._hovered = False
            widget.update()

    def _schedule_theme_menu_hide_check(self):
        """Defer submenu closing while the pointer crosses into its popup."""
        if hasattr(self, "_theme_submenu_hover_timer"):
            self._theme_submenu_hover_timer.start()

    def _cursor_is_over_theme_menu_context(self) -> bool:
        action = getattr(self, "act_theme_compact", None)
        widget = action.widget() if hasattr(action, "widget") else None
        cursor_pos = self.cursor().pos()

        if widget is not None:
            local_pos = widget.mapFromGlobal(cursor_pos)
            if widget.rect().contains(local_pos):
                return True

        if hasattr(self, "theme_menu") and self.theme_menu is not None and self.theme_menu.isVisible():
            submenu_local_pos = self.theme_menu.mapFromGlobal(cursor_pos)
            if self.theme_menu.rect().contains(submenu_local_pos):
                return True

            if widget is not None:
                row_top_left = widget.mapToGlobal(QPoint(0, 0))
                row_bottom_right = widget.mapToGlobal(QPoint(widget.width(), widget.height()))
                submenu_top_left = self.theme_menu.mapToGlobal(QPoint(0, 0))
                bridge_left = min(row_top_left.x(), submenu_top_left.x())
                bridge_right = max(row_bottom_right.x(), submenu_top_left.x())
                bridge_top = min(row_top_left.y(), submenu_top_left.y())
                bridge_bottom = max(row_bottom_right.y(), submenu_top_left.y() + 4)
                if bridge_left <= cursor_pos.x() <= bridge_right and bridge_top <= cursor_pos.y() <= bridge_bottom:
                    return True

        return False

    def _hide_theme_menu_if_focus_lost(self):
        if not hasattr(self, "theme_menu") or self.theme_menu is None or not self.theme_menu.isVisible():
            if hasattr(self, "_theme_submenu_hover_timer"):
                self._theme_submenu_hover_timer.stop()
            return
        self._sync_theme_menu_hover_states()
        if self._cursor_is_over_theme_menu_context():
            return
        self._hide_theme_menu_from_compact_action()

    def _sync_theme_menu_hover_states(self):
        """Keep custom QWidgetAction hover state aligned with the real cursor.

        QMenu can retain a QWidgetAction's leave event while the cursor crosses
        between the parent popup and its submenu.  Rendering from that stale
        local state leaves two theme rows highlighted until the next menu
        transition.  The submenu watchdog already runs while this popup is
        visible, so use it to reconcile only the lightweight hover flags.
        """
        if not hasattr(self, "theme_actions"):
            return
        cursor_pos = self.cursor().pos()
        for action in self.theme_actions.values():
            widget = action.widget() if hasattr(action, "widget") else None
            if widget is None:
                continue
            is_hovered = widget.rect().contains(widget.mapFromGlobal(cursor_pos))
            if hasattr(widget, "set_hovered"):
                widget.set_hovered(is_hovered)

    def _show_theme_menu_from_compact_action(self):
        widget = getattr(getattr(self, "act_theme_compact", None), "widget", lambda: None)()
        if widget is None:
            return
        if hasattr(self, "menu") and self.menu is not None and not self.menu.isVisible():
            self.menu.popup(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height())))
        submenu_pos = widget.mapToGlobal(QPoint(widget.width() - 1, 0))
        if not (self.theme_menu.isVisible() and self.theme_menu.pos() == submenu_pos):
            self.theme_menu.popup(submenu_pos)
        QTimer.singleShot(0, lambda: self._focus_popup_menu(self.theme_menu))

        # QWidgetAction leaveEvent is not reliable enough while nested QMenu
        # popups are active. Keep a lightweight watchdog running while the
        # submenu is visible. It closes the submenu only after the cursor leaves
        # the parent row, the bridge area, and the submenu itself.
        if hasattr(self, "_theme_submenu_hover_timer"):
            self._theme_submenu_hover_timer.start()

    @staticmethod
    def _focus_popup_menu(menu: QMenu):
        """Restore keyboard navigation for non-modal QMenu popups."""
        if menu is not None and menu.isVisible():
            menu.setFocus(Qt.FocusReason.PopupFocusReason)

    @staticmethod
    def _menu_selectable_actions(menu: QMenu):
        return [action for action in menu.actions() if action.isEnabled() and not action.isSeparator()]

    def _set_keyboard_menu_action(self, menu: QMenu, action):
        menu.setActiveAction(action)
        for candidate in self._menu_selectable_actions(menu):
            widget = candidate.widget() if hasattr(candidate, "widget") else None
            if widget is not None and hasattr(widget, "set_hovered"):
                widget.set_hovered(candidate is action)
            elif widget is not None and hasattr(widget, "_hovered"):
                widget._hovered = candidate is action
                widget.update()

    def eventFilter(self, watched, event):
        if watched not in (getattr(self, "menu", None), getattr(self, "theme_menu", None)):
            return super().eventFilter(watched, event)
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(watched, event)

        menu = watched
        key = event.key()
        actions = self._menu_selectable_actions(menu)
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and actions:
            try:
                current_index = actions.index(menu.activeAction())
            except ValueError:
                current_index = -1 if key == Qt.Key.Key_Down else 0
            step = 1 if key == Qt.Key.Key_Down else -1
            self._set_keyboard_menu_action(menu, actions[(current_index + step) % len(actions)])
            return True

        active = menu.activeAction()
        if menu is self.menu and key == Qt.Key.Key_Right and active is self.act_theme_compact:
            self._show_theme_menu_from_compact_action()
            return True
        if menu is self.theme_menu and key == Qt.Key.Key_Left:
            self.theme_menu.hide()
            self._focus_popup_menu(self.menu)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and active is not None:
            if menu is self.menu and active is self.act_theme_compact:
                self._show_theme_menu_from_compact_action()
            elif menu is self.theme_menu and hasattr(active, "data"):
                self._handle_theme_action(active.data())
            else:
                active.trigger()
            return True
        if key == Qt.Key.Key_Escape:
            if menu is self.theme_menu:
                self.theme_menu.hide()
                self._focus_popup_menu(self.menu)
            else:
                self.menu.hide()
            return True
        return super().eventFilter(watched, event)

    def _clear_compact_menu_hover_states(self):
        if hasattr(self, "_theme_submenu_hover_timer"):
            self._theme_submenu_hover_timer.stop()
        if hasattr(self, "theme_menu") and self.theme_menu is not None and self.theme_menu.isVisible():
            self.theme_menu.hide()

        for action_name in ("act_prefs", "act_theme_compact", "act_help", "act_about", "act_exit"):
            action = getattr(self, action_name, None)
            widget = action.widget() if hasattr(action, "widget") else None
            if widget is not None and getattr(widget, "_hovered", False):
                widget._hovered = False
                widget.update()

        if hasattr(self, "theme_actions"):
            for action in self.theme_actions.values():
                widget = action.widget() if hasattr(action, "widget") else None
                if widget is not None and getattr(widget, "_hovered", False):
                    widget._hovered = False
                    widget.update()

    def _show_menu(self):
        if not hasattr(self, "menu") or self.menu is None:
            return
        try:
            self.menu_button.setDown(False)
            self.menu_button.clearFocus()
        except Exception:
            _LOGGER.debug("Ignoring menu button pre-popup focus reset failure", exc_info=True)
        self._clear_compact_menu_hover_states()
        self._refresh_theme_menu_checks()
        pos = self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height()))
        # Keep popup handling on the application's regular event loop.  exec()
        # starts a nested modal loop; changing the application palette inside
        # that loop can leave Qt's native hover state attached to whichever
        # theme closed the popup until another mouse press arrives.
        self.menu.popup(pos)
        QTimer.singleShot(0, lambda: self._focus_popup_menu(self.menu))

    def _on_main_menu_about_to_hide(self):
        self._clear_compact_menu_hover_states()
        try:
            self.menu_button.setDown(False)
            self.menu_button.clearFocus()
            self.menu_button.update()
        except Exception:
            _LOGGER.debug("Ignoring menu button post-popup state reset failure", exc_info=True)

    def _refresh_theme_menu_checks(self):
        if not hasattr(self, "theme_actions"):
            return
        mode = normalized_theme_mode(ConfigurationService.instance().get().theme_mode)
        for action_mode, action in self.theme_actions.items():
            if hasattr(action, "set_checked"):
                action.set_checked(action_mode == mode)
            elif hasattr(action, "setChecked"):
                action.setChecked(action_mode == mode)

    def _handle_theme_action(self, mode: str):
        mode = normalized_theme_mode(mode)
        ConfigurationService.instance().update(theme_mode=mode)
        apply_theme_mode(mode)
        self._refresh_theme_menu_checks()
        self._apply_styles()

    def _handle_preferences_action(self):
        parent = self.window()
        if hasattr(parent, "open_preferences"):
            parent.open_preferences()

    def _handle_help_action(self):
        parent = self.window()
        if hasattr(parent, "open_help"):
            parent.open_help()

    def _handle_about_action(self):
        parent = self.window()
        if hasattr(parent, "open_about"):
            parent.open_about()

    def _handle_exit_action(self):
        event_bridge.emit("shutdown_requested", None)
