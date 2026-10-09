import logging
from PySide6.QtCore import QEvent, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProxyStyle,
    QPushButton,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtSvg import QSvgRenderer

from app.ui.assets import icon as asset_icon
from app.ui.context_bar import SelectionActionBarWidget
from app.ui.context_bar_widget import ContextBarWidget
from app.ui.file_list import FileList
from app.ui.theme_tokens import build_button_stylesheet, build_theme_tokens, color_to_css

_LOGGER = logging.getLogger(__name__)


def _lightness_delta(first: QColor, second: QColor) -> int:
    return abs(QColor(first).lightness() - QColor(second).lightness())


def _contrast_color(background: QColor, preferred: QColor, fallback_a: QColor, fallback_b: QColor, *, min_delta: int = 128) -> QColor:
    background = QColor(background)
    candidates = [QColor(preferred), QColor(fallback_a), QColor(fallback_b)]
    best = max(candidates, key=lambda candidate: _lightness_delta(candidate, background))
    if _lightness_delta(best, background) >= min_delta:
        return best
    if background.lightness() < 128:
        return QColor(255, 255, 255, QColor(preferred).alpha() or 255)
    return QColor(0, 0, 0, QColor(preferred).alpha() or 255)


def _text_icon(glyph: str, color: QColor, *, size: int = 16, point_size: int = 12, weight: QFont.Weight = QFont.Weight.Normal) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QColor(color))
    font = QFont()
    font.setPointSize(point_size)
    font.setWeight(weight)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), int(Qt.AlignmentFlag.AlignCenter), glyph)
    painter.end()
    icon = QIcon()
    icon.addPixmap(pixmap, QIcon.Mode.Normal)
    icon.addPixmap(pixmap, QIcon.Mode.Disabled)
    icon.addPixmap(pixmap, QIcon.Mode.Active)
    return icon


def _tinted_svg_icon(icon_name: str, color: QColor, *, size: int = 16, disabled_color: QColor | None = None) -> QIcon:
    """Build a QIcon from the same SVG asset used by the file-card profile button.

    The profile button is custom-painted through QSvgRenderer and then tinted by
    the theme token. A regular QPushButton icon does not get that custom tint,
    so we render the SVG to pixmaps here with the same technique. This preserves
    the approved gear shape while keeping it readable in dark mode.
    """
    renderer = QSvgRenderer(asset_icon(icon_name))
    if not renderer.isValid():
        fallback = QIcon(asset_icon(icon_name))
        if not fallback.isNull():
            return fallback
        return _text_icon("⚙", color, size=size, point_size=max(9, size - 5))

    def _render(tint: QColor) -> QPixmap:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(tint))
        painter.end()
        return pixmap

    icon = QIcon()
    normal_pixmap = _render(color)
    disabled_pixmap = _render(disabled_color if disabled_color is not None else color)
    icon.addPixmap(normal_pixmap, QIcon.Mode.Normal)
    icon.addPixmap(disabled_pixmap, QIcon.Mode.Disabled)
    icon.addPixmap(normal_pixmap, QIcon.Mode.Active)
    return icon




class _CenteredPushButtonContentStyle(QProxyStyle):
    """Keep title-bar QPushButton contents vertically centered after theme repolish.

    Qt/Windows can recalculate SE_PushButtonContents a little lower after a
    manual QStyleHints color-scheme change.  The buttons themselves keep the
    correct 40px geometry, but the icon/text content rectangle shifts down on
    the second native polish pass.  Re-centering only that sub-element preserves
    the native style, colors, hover behavior and validated button sizes.
    """

    def subElementRect(self, element, option, widget=None):
        rect = super().subElementRect(element, option, widget)
        if element == QStyle.SubElement.SE_PushButtonContents and widget is not None:
            try:
                outer = widget.rect()
                centered_top = outer.top() + max(0, (outer.height() - rect.height()) // 2)
                rect.moveTop(max(outer.top(), centered_top - 2))
            except Exception:
                _LOGGER.debug("Ignoring title-bar button content recenter failure", exc_info=True)
        return rect


def _install_centered_button_content_style(button: QPushButton) -> None:
    current_style = button.style()
    if isinstance(current_style, _CenteredPushButtonContentStyle):
        style = current_style
    else:
        style = _CenteredPushButtonContentStyle(current_style)
        button.setStyle(style)
        button._centered_button_content_style = style

    # Manual theme changes can trigger a second Qt/Windows polish pass after the
    # proxy style has already been installed. Re-polish even when the proxy is
    # present so SE_PushButtonContents is recalculated against the final palette.
    try:
        style.unpolish(button)
        style.polish(button)
    except Exception:
        _LOGGER.debug("Ignoring title-bar button style repolish failure", exc_info=True)
    button.updateGeometry()
    button.update()

class FileListContainer(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("FileListContainer")
        self._applying_palette_roles = False
        self._attached_menu_widget = None
        self._attached_menu_polish_pending = False
        self._selection_button_polish_pending = False

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.inner_panel = QFrame(self)
        self.inner_panel.setObjectName("FileListInnerPanel")

        inner_layout = QVBoxLayout(self.inner_panel)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)

        self.title_bar = SelectionActionBarWidget(self.inner_panel)
        self.title_bar.setObjectName("FileListTitleBar")
        self.title_bar.btn_enqueue.hide()
        self.title_bar.btn_select_all.setText("Selecionar tudo")
        self.title_bar.btn_select_all.setToolTip("Selecionar todos os arquivos")
        try:
            select_all_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        except Exception:
            select_all_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogYesButton)
        self.title_bar.btn_select_all.setIcon(select_all_icon)
        self.title_bar.btn_select_all.setIconSize(QSize(14, 14))
        self.title_bar.btn_config.setText("Configurar")
        self.title_bar.btn_config.setToolTip("Abrir configurações")
        self.title_bar.btn_config.setAccessibleName("Configurar")
        self.title_bar.btn_config.setIcon(QIcon(asset_icon("settings_gear_outline.svg")))
        self.title_bar.btn_config.setIconSize(QSize(14, 14))
        self.title_bar.btn_delete.setText("")
        self.title_bar.btn_delete.setToolTip("Remover seleção")
        self.title_bar.btn_delete.setAccessibleName("Remover seleção")
        try:
            trash_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        except Exception:
            trash_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogDiscardButton)
        self.title_bar.btn_delete.setIcon(trash_icon)
        self.title_bar.btn_delete.setIconSize(QSize(16, 16))
        self.title_bar.btn_select_all.setObjectName("FileListSelectionTab")
        self.title_bar.btn_config.setObjectName("FileListSelectionTab")
        self.title_bar.btn_delete.setObjectName("FileListTrashButton")

        self.title_layout = self.title_bar.layout()
        # Add a small, intentional horizontal breathing room so the embedded
        # toolbar does not feel glued to the window edge while keeping the
        # separator/list area aligned to the panel frame. The right side is
        # slightly tighter so the three-dot menu remains visually attached to
        # the window edge instead of floating inward.
        self.title_layout.setContentsMargins(6, 0, 3, 0)
        self.title_layout.setSpacing(6)
        selection_button_width = 132
        config_button_width = selection_button_width
        trash_button_width = 42
        self.title_bar.btn_select_all.setFixedWidth(selection_button_width)
        self.title_bar.btn_select_all.setFixedHeight(40)
        self.title_bar.btn_config.setFixedWidth(config_button_width)
        self.title_bar.btn_config.setFixedHeight(40)
        self.title_bar.btn_delete.setFixedWidth(trash_button_width)
        self.title_bar.btn_delete.setFixedHeight(40)
        self._selection_tabs_reserved_width = selection_button_width + config_button_width + trash_button_width + 2
        self.title_bar.setMinimumHeight(42)
        self.title_bar.setMaximumHeight(42)

        self.import_actions_group = QWidget(self.title_bar)
        self.import_actions_group.setObjectName("FileListImportActionsGroup")
        import_actions_layout = QHBoxLayout(self.import_actions_group)
        import_actions_layout.setContentsMargins(0, 0, 0, 0)
        import_actions_layout.setSpacing(0)

        self.import_actions_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.title_layout.addWidget(self.import_actions_group, 1)
        self.title_layout.setAlignment(self.import_actions_group, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.selection_tabs_group = QWidget(self.title_bar)
        self.selection_tabs_group.setObjectName("FileListSelectionTabsGroup")
        selection_tabs_layout = QHBoxLayout(self.selection_tabs_group)
        selection_tabs_layout.setContentsMargins(0, 1, 0, 1)
        selection_tabs_layout.setSpacing(1)

        self.title_layout.removeWidget(self.title_bar.btn_select_all)
        self.title_layout.removeWidget(self.title_bar.btn_delete)
        self.title_layout.removeWidget(self.title_bar.btn_clear_selection)
        self.title_bar.btn_clear_selection.hide()
        selection_tabs_layout.addWidget(self.title_bar.btn_select_all)
        selection_tabs_layout.addWidget(self.title_bar.btn_config)
        selection_tabs_layout.addWidget(self.title_bar.btn_delete)
        self.selection_tabs_group.setFixedWidth(self._selection_tabs_reserved_width)

        self.trailing_actions_group = QWidget(self.title_bar)
        self.trailing_actions_group.setObjectName("FileListTrailingActionsGroup")
        trailing_actions_layout = QHBoxLayout(self.trailing_actions_group)
        trailing_actions_layout.setContentsMargins(0, 1, 0, 1)
        trailing_actions_layout.setSpacing(1)
        trailing_actions_layout.addWidget(self.selection_tabs_group)
        self.trailing_actions_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.title_layout.addWidget(self.trailing_actions_group, 0, Qt.AlignmentFlag.AlignRight)

        self.file_list = FileList()
        self.file_list.setObjectName("InnerFileList")

        self.title_separator = QFrame(self.inner_panel)
        self.title_separator.setObjectName("FileListTitleSeparator")
        self.title_separator.setFixedHeight(1)

        self.context_bar = ContextBarWidget()

        inner_layout.addWidget(self.title_bar)
        self.title_bar.setVisible(True)
        inner_layout.addWidget(self.title_separator)
        inner_layout.addWidget(self.file_list, 1)
        inner_layout.addWidget(self.context_bar)
        outer_layout.addWidget(self.inner_panel)

        self._apply_styles()
        self._apply_palette_roles()

    def _color_hex(self, color: QColor) -> str:
        return color_to_css(color)

    def attach_top_bar_controls(self, import_widgets=None, menu_widget=None):
        import_layout = self.import_actions_group.layout()
        while import_layout.count():
            item = import_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        trailing_layout = self.trailing_actions_group.layout()
        while trailing_layout.count() > 1:
            item = trailing_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        self._attached_menu_widget = None

        import_button_width = 200
        attached_import_widgets = list(import_widgets or ())
        for widget in attached_import_widgets:
            widget.setParent(self.import_actions_group)
            widget.setFixedHeight(42)
            widget.setFixedWidth(import_button_width)
            widget.setMinimumWidth(import_button_width)
            widget.setMaximumWidth(import_button_width)
            widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            import_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)

        if attached_import_widgets:
            group_width = import_button_width * len(attached_import_widgets)
            self.import_actions_group.setMinimumWidth(group_width)
            self.import_actions_group.setMaximumWidth(group_width)
            self.import_actions_group.setFixedWidth(group_width)
            self.title_layout.setAlignment(self.import_actions_group, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if menu_widget is not None:
            menu_widget.setParent(self.trailing_actions_group)
            menu_widget.setFixedHeight(37)
            # Menu-only control: do not keep focus after the popup closes,
            # otherwise Qt/Windows can leave an accent-blue focus frame.
            menu_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            try:
                menu_widget.setAutoRaise(False)
            except Exception:
                _LOGGER.debug("Ignoring attached menu auto-raise update failure", exc_info=True)
            self._attached_menu_widget = menu_widget
            # Anchor the original menu button to the bottom baseline of the
            # trailing toolbar group. Center alignment can round differently
            # after a Windows theme repolish and make the button appear 1 px
            # higher than the adjacent trash button.
            trailing_layout.addWidget(menu_widget, 0, Qt.AlignmentFlag.AlignBottom)
            # Apply/polish only after layout insertion. On cold app open,
            # polishing before insertion can leave the hover frame 1 px lower;
            # after a theme change the widget is already inserted, which is why
            # the same geometry becomes correct after switching themes.
            self._apply_attached_menu_button_style()
            self._schedule_attached_menu_button_late_polish()

        self._update_trailing_reserved_width()

    def _apply_attached_menu_button_style(self):
        menu_widget = getattr(self, "_attached_menu_widget", None)
        if menu_widget is None:
            return

        colors = self._attached_menu_button_colors()
        self._force_attached_menu_button_text_glyph(menu_widget)
        menu_widget.setStyleSheet(self._attached_menu_button_stylesheet(colors))
        self._apply_attached_menu_button_geometry(menu_widget)
        self._reset_attached_menu_button_state(menu_widget)
        self._align_attached_menu_button(menu_widget)
        self._repolish_attached_menu_button(menu_widget)
        menu_widget.updateGeometry()
        menu_widget.update()

    def _attached_menu_button_colors(self) -> dict[str, str]:
        tokens = build_theme_tokens(self.palette())
        # Keep the validated original three-dot tool button, but let the
        # resting state blend into the toolbar/list background. Using the
        # generic compact-button fill here caused stale gray patches after
        # Windows light/dark theme changes, and in dark mode the glyph could
        # lose contrast against that local fill.
        menu_fg_color = _contrast_color(
            tokens.surface_list_header,
            tokens.surface_list_header_text,
            self.palette().brightText().color(),
            self.palette().text().color(),
            min_delta=118,
        )
        return {
            "menu_fg": menu_fg_color.name(QColor.NameFormat.HexArgb),
            "hover_bg": tokens.attached_menu_button_hover_bg.name(QColor.NameFormat.HexArgb),
            "hover_border": tokens.attached_menu_button_hover_border.name(QColor.NameFormat.HexArgb),
            "pressed_bg": tokens.small_button_pressed_bg.name(QColor.NameFormat.HexArgb),
            "pressed_border": tokens.button_pressed_border.name(QColor.NameFormat.HexArgb),
        }

    def _force_attached_menu_button_text_glyph(self, menu_widget):
        # Force the original menu control to use the text glyph path while
        # attached to the title bar. SVG icons do not inherit QSS `color`, so
        # they can become nearly invisible on Windows dark theme. The control,
        # geometry and menu behavior stay unchanged; only the glyph rendering
        # path is normalized to the palette-aware foreground below.
        menu_widget.setIcon(QIcon())
        menu_widget.setText("⋮")
        try:
            font = menu_widget.font()
            font.setPointSize(20)
            font.setWeight(QFont.Weight.DemiBold)
            menu_widget.setFont(font)
        except Exception:
            _LOGGER.debug("Ignoring attached menu font update failure", exc_info=True)

    def _attached_menu_button_stylesheet(self, colors: dict[str, str]) -> str:
        menu_fg = colors["menu_fg"]
        hover_bg = colors["hover_bg"]
        pressed_bg = colors["pressed_bg"]
        pressed_border = colors["pressed_border"]
        return f"""
            QToolButton#GlobalBarMenuButton {{
                background: transparent;
                color: {menu_fg};
                border: 1px solid transparent;
                border-radius: 4px;
                min-height: 37px;
                max-height: 37px;
                padding: 0px 0px 0px 2px;
                margin: 1px 0px 0px 0px;
            }}
            QToolButton#GlobalBarMenuButton:hover:!disabled {{
                background: {hover_bg};
                color: {menu_fg};
                border: 1px solid transparent;
                border-radius: 4px;
                min-height: 37px;
                max-height: 37px;
                padding: 0px 0px 0px 2px;
                margin: 1px 0px 0px 0px;
            }}
            QToolButton#GlobalBarMenuButton:pressed:!disabled {{
                background: {pressed_bg};
                color: {menu_fg};
                border: 1px solid {pressed_border};
                border-radius: 4px;
                min-height: 37px;
                max-height: 37px;
                padding: 0px 0px 0px 2px;
                margin: 1px 0px 0px 0px;
            }}
            QToolButton#GlobalBarMenuButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
            """

    def _apply_attached_menu_button_geometry(self, menu_widget):
        # Reforça a geometria depois da troca de stylesheet/palette.
        # Este é o botão realmente anexado ao cabeçalho da lista. A margem deve
        # ficar no topo do QSS para deslocar o retângulo visual 1 px para baixo;
        # ajustes no GlobalBar original não afetam esta geometria após o reparent.
        menu_widget.setMinimumHeight(37)
        menu_widget.setMaximumHeight(37)
        menu_widget.setFixedHeight(37)
        menu_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _reset_attached_menu_button_state(self, menu_widget):
        try:
            menu_widget.setDown(False)
            menu_widget.clearFocus()
        except Exception:
            _LOGGER.debug("Ignoring attached menu focus-state reset failure", exc_info=True)

    def _align_attached_menu_button(self, menu_widget):
        trailing_layout = self.trailing_actions_group.layout()
        if trailing_layout is not None:
            trailing_layout.setAlignment(menu_widget, Qt.AlignmentFlag.AlignBottom)

    def _repolish_attached_menu_button(self, menu_widget):
        try:
            style = menu_widget.style()
            style.unpolish(menu_widget)
            style.polish(menu_widget)
        except Exception:
            _LOGGER.debug("Ignoring attached menu style repolish failure", exc_info=True)

    def _schedule_attached_menu_button_late_polish(self):
        if getattr(self, "_attached_menu_polish_pending", False):
            return
        self._attached_menu_polish_pending = True

        def _late_polish():
            self._attached_menu_polish_pending = False
            menu_widget = getattr(self, "_attached_menu_widget", None)
            if menu_widget is None:
                return
            # Cold app open and Windows theme changes do not happen at the
            # same point in Qt's polish lifecycle. Theme changes repolish an
            # already inserted/visible QToolButton, while cold open applies the
            # first stylesheet before the widget has reached final native
            # geometry. Reapply exactly the same final styling on the next event
            # loop turn so the hover frame uses the same baseline as after a
            # PaletteChange, without changing the original button or menu.
            self._apply_attached_menu_button_style()
            self._update_trailing_reserved_width()

        QTimer.singleShot(0, _late_polish)


    def _schedule_selection_button_late_polish(self):
        if getattr(self, "_selection_button_polish_pending", False):
            return
        self._selection_button_polish_pending = True

        def _late_polish():
            self._selection_button_polish_pending = False
            for button in (
                self.title_bar.btn_select_all,
                self.title_bar.btn_config,
                self.title_bar.btn_delete,
            ):
                _install_centered_button_content_style(button)

        QTimer.singleShot(0, _late_polish)


    def _update_trailing_reserved_width(self):
        trailing_layout = self.trailing_actions_group.layout()
        reserved_width = self._selection_tabs_reserved_width
        if trailing_layout.count() > 1:
            menu_widget = trailing_layout.itemAt(1).widget()
            if menu_widget is not None:
                menu_width = menu_widget.width() or menu_widget.sizeHint().width()
                reserved_width += trailing_layout.spacing() + menu_width
        self.trailing_actions_group.setFixedWidth(reserved_width)


    def set_file_list_bottom_inset(self, inset: int):
        self.file_list.set_bottom_content_inset(inset)

    def _apply_styles(self):
        self.setStyleSheet(
            """
        QFrame#FileListContainer {
            background: transparent;
            border: none;
            border-radius: 4px;
            padding: 1px;
        }

        QFrame#FileListInnerPanel {
            border: none;
            border-radius: 3px;
        }

        QFrame#FileListTitleBar {
            border: none;
            border-top-left-radius: 3px;
            border-top-right-radius: 3px;
            border-bottom-left-radius: 0px;
            border-bottom-right-radius: 0px;
        }

        QFrame#FileListTitleSeparator {
            background: #b5b5b5;
            border: none;
            min-height: 1px;
            max-height: 1px;
        }

        QListView#InnerFileList {
            background: transparent;
            border: none;
        }

        QWidget#FileListImportActionsGroup QPushButton {
            margin-left: 0px;
            margin-right: 0px;
        }

        """
        )

    def _apply_file_list_panel_palette(self, tokens, app_palette: QPalette):
        title_bar_bg_hex = self._color_hex(tokens.surface_list_header)
        list_surface_bg_hex = self._color_hex(tokens.surface_list)
        inner_panel_border_hex = self._color_hex(tokens.border_panel)
        title_separator_hex = self._color_hex(tokens.border_panel)

        self.inner_panel.setStyleSheet(
            f"""
            QFrame#FileListInnerPanel {{
                background-color: {list_surface_bg_hex};
                border: 1px solid {inner_panel_border_hex};
                border-radius: 3px;
            }}
            """
        )
        self.title_separator.setStyleSheet(
            f"""
            QFrame#FileListTitleSeparator {{
                background-color: {title_separator_hex};
                border: none;
                min-height: 1px;
                max-height: 1px;
            }}
            """
        )
        self.title_bar.setPalette(app_palette)
        self.title_bar.setAutoFillBackground(False)
        self.title_bar.setStyleSheet(
            f"""
            QFrame#FileListTitleBar {{
                background-color: {title_bar_bg_hex};
                border: none;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                padding-bottom: 0px;
            }}
            """
        )

    def _build_selection_tab_stylesheet(self, tokens) -> str:
        button_bg = tokens.button_bg.name(QColor.NameFormat.HexArgb)
        button_fg = tokens.button_fg.name(QColor.NameFormat.HexArgb)
        button_border = tokens.button_border.name(QColor.NameFormat.HexArgb)
        button_hover_bg = tokens.button_hover_bg.name(QColor.NameFormat.HexArgb)
        button_hover_border = tokens.button_hover_border.name(QColor.NameFormat.HexArgb)
        button_pressed_bg = tokens.button_pressed_bg.name(QColor.NameFormat.HexArgb)
        button_pressed_border = tokens.button_pressed_border.name(QColor.NameFormat.HexArgb)
        disabled_text_color = QColor(tokens.button_disabled_fg)
        disabled_text_color.setAlpha(max(24, round(disabled_text_color.alpha() * 0.72)))
        button_disabled_fg = disabled_text_color.name(QColor.NameFormat.HexArgb)
        button_disabled_border = tokens.button_disabled_border.name(QColor.NameFormat.HexArgb)
        return f"""
            QPushButton {{
                background: {button_bg};
                color: {button_fg};
                border: 2px solid {button_border};
                border-radius: 4px;
                padding: 0px 9px 2px 9px;
                min-height: 40px;
                max-height: 40px;
            }}
            QPushButton:hover:!disabled {{
                background: {button_hover_bg};
                color: {button_fg};
                border: 2px solid {button_hover_border};
                border-radius: 4px;
                padding: 0px 9px 2px 9px;
                min-height: 40px;
                max-height: 40px;
            }}
            QPushButton:pressed:!disabled {{
                background: {button_pressed_bg};
                color: {button_fg};
                border: 2px solid {button_pressed_border};
                border-radius: 4px;
                padding: 0px 9px 2px 9px;
                min-height: 40px;
                max-height: 40px;
            }}
            QPushButton:disabled {{
                background: {button_bg};
                color: {button_disabled_fg};
                border: 2px solid {button_disabled_border};
                border-radius: 4px;
                padding: 0px 9px 2px 9px;
                min-height: 40px;
                max-height: 40px;
            }}
        """

    def _apply_selection_buttons_palette(self, selection_tab_stylesheet: str):
        for selection_button in (
            self.title_bar.btn_select_all,
            self.title_bar.btn_config,
            self.title_bar.btn_delete,
        ):
            selection_button.setStyleSheet(selection_tab_stylesheet)
            selection_button.setMinimumHeight(40)
            selection_button.setMaximumHeight(40)
            selection_button.setFixedHeight(40)
            _install_centered_button_content_style(selection_button)
            selection_button.updateGeometry()
            selection_button.update()
        self._schedule_selection_button_late_polish()

    def _apply_config_button_icon(self, tokens):
        # Use the exact same gear asset used by the card Perfil button,
        # rendered through the same tinting approach. This keeps the approved
        # icon shape and avoids the poor-looking font glyph fallback while
        # preserving dark-theme contrast.
        config_icon_color = QColor(tokens.text_secondary)
        # Disabled config icon must stay neutral gray. The previous
        # contrast fallback could inherit the Windows accent/bright color
        # and turn the gear blue in dark theme, unlike the disabled text.
        disabled_source = QColor(tokens.button_disabled_fg)
        disabled_gray = disabled_source.lightness()
        if QColor(tokens.button_bg).lightness() < 128:
            disabled_gray = max(disabled_gray, 118)
        else:
            disabled_gray = min(max(disabled_gray, 92), 152)
        config_disabled_icon_color = QColor(disabled_gray, disabled_gray, disabled_gray, 178)
        self.title_bar.btn_config.setIcon(
            _tinted_svg_icon(
                "settings_gear_outline.svg",
                config_icon_color,
                size=16,
                disabled_color=config_disabled_icon_color,
            )
        )
        self.title_bar.btn_config.setIconSize(QSize(14, 14))

    def _apply_selection_button_qpalette(self):
        app = QApplication.instance()
        default_button_palette = QPalette(app.palette() if app is not None else self.palette())
        for button in (
            self.title_bar.btn_select_all,
            self.title_bar.btn_config,
            self.title_bar.btn_delete,
        ):
            button.setPalette(default_button_palette)
            button.setAutoFillBackground(False)

    def _apply_title_label_palette(self, title_bar_text_hex: str):
        title_label = self.title_bar.findChild(QLabel, "FileListTitleLabel")
        if title_label is None:
            return
        title_label.setAutoFillBackground(False)
        title_label.setBackgroundRole(QPalette.ColorRole.NoRole)
        title_label.setStyleSheet(
            f"""
            QLabel#FileListTitleLabel {{
                background: transparent;
                color: {title_bar_text_hex};
                font-weight: 600;
                padding-left: 4px;
            }}
            """
        )
        title_label.update()

    def _apply_palette_roles(self):
        if self._applying_palette_roles:
            return
        self._applying_palette_roles = True
        try:
            app_palette = self.palette()
            tokens = build_theme_tokens(app_palette)
            title_bar_text_hex = self._color_hex(tokens.surface_list_header_text)

            # Keep this as an intentionally unused construction for parity with
            # the prior palette refresh path. Qt style engines may initialize
            # button subcontrol defaults while building the shared stylesheet.
            build_button_stylesheet(tokens, min_height=42, border_radius=4, horizontal_padding=10)

            self._apply_file_list_panel_palette(tokens, app_palette)
            self._apply_selection_buttons_palette(self._build_selection_tab_stylesheet(tokens))
            self._apply_config_button_icon(tokens)
            self._apply_attached_menu_button_style()
            self._apply_selection_button_qpalette()
            self._apply_title_label_palette(title_bar_text_hex)

            self.inner_panel.update()
            self.title_bar.update()
        finally:
            self._applying_palette_roles = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self._apply_palette_roles()
