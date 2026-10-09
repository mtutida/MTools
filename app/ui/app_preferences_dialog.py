from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QApplication,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QFrame,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.ancillary.configuration import AppConfiguration, ConfigurationService
from app.ancillary.logging_service import LOG_DIR
from app.interaction_model.event_bridge import event_bridge
from app.ui.dialog_caption import schedule_dialog_caption
from app.ui.theme_mode_controller import palette_for_styled_dialog
from app.ui.theme_tokens import build_button_stylesheet, build_theme_tokens

THEME_ASSET_TEMP_DIR_NAME = "compactme_theme_assets"


_LOGGER = logging.getLogger(__name__)

SOURCE_OUTPUT_DIRECTORY_DISPLAY = "Pasta de origem\\CompactMe"
SOURCE_OUTPUT_DIRECTORY_TOOLTIP = (
    "Quando esta opção está ativa, cada arquivo será salvo em uma subpasta "
    "CompactMe dentro da própria pasta de origem."
)





def _display_output_directory(path: str) -> str:
    """Return output directory text using native separators for the UI.

    Qt's folder picker may return Windows paths with forward slashes
    (for example, D:/teste3).  Keep the saved value usable while showing
    the Preferences field in the platform convention expected by Windows
    users.
    """
    text = str(path or "").strip()
    if not text or text == SOURCE_OUTPUT_DIRECTORY_DISPLAY:
        return text
    native = QDir.toNativeSeparators(text)
    if os.name == "nt" or (len(native) >= 3 and native[1] == ":" and native[2] == "/"):
        native = native.replace("/", "\\")
    return native


def _stored_output_directory_from_text(path: str) -> str:
    """Normalize user-visible output directory text before storing it."""
    text = str(path or "").strip()
    if not text or text == SOURCE_OUTPUT_DIRECTORY_DISPLAY:
        return ""
    return _display_output_directory(text)


class _PreferenceCheckBox(QCheckBox):
    """Theme-stable checkbox used by the Preferences dialog.

    Native checkbox indicators may keep stale platform colors after a runtime
    light/dark transition.  Preferences is a styled dialog, so the indicator is
    painted explicitly here while preserving the existing checkbox API and state.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._indicator_bg = QColor()
        self._indicator_border = QColor()
        self._indicator_checked_bg = QColor()
        self._indicator_checked_border = QColor()
        self._indicator_check = QColor()
        self._text_color = QColor()
        self._indicator_size = 16
        self._indicator_radius = 3
        self._spacing = 7
        self.setMinimumHeight(20)

    def set_theme_colors(
        self,
        *,
        text_color: QColor,
        indicator_bg: QColor,
        indicator_border: QColor,
        checked_bg: QColor,
        checked_border: QColor,
        check_color: QColor,
    ) -> None:
        self._text_color = QColor(text_color)
        self._indicator_bg = QColor(indicator_bg)
        self._indicator_border = QColor(indicator_border)
        self._indicator_checked_bg = QColor(checked_bg)
        self._indicator_checked_border = QColor(checked_border)
        self._indicator_check = QColor(check_color)
        self.update()

    def sizeHint(self):
        hint = super().sizeHint()
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(
            max(hint.width(), self._indicator_size + self._spacing + text_width + 2),
            max(hint.height(), 20),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        enabled = self.isEnabled()
        checked = self.isChecked()
        box = self._indicator_size
        x = 0
        y = max(0, (self.height() - box) // 2)
        rect = QRectF(x + 0.5, y + 0.5, box - 1, box - 1)

        bg = QColor(self._indicator_checked_bg if checked else self._indicator_bg)
        border = QColor(self._indicator_checked_border if checked else self._indicator_border)
        check = QColor(self._indicator_check)
        text_color = QColor(self._text_color)

        if not enabled:
            bg.setAlpha(max(70, bg.alpha() // 2))
            border.setAlpha(max(80, border.alpha() // 2))
            check.setAlpha(max(90, check.alpha() // 2))
            text_color.setAlpha(max(90, text_color.alpha() // 2))

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, self._indicator_radius, self._indicator_radius)

        if checked:
            pen = QPen(check, 1.9)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPointF(x + 4.2, y + 8.4), QPointF(x + 7.0, y + 11.1))
            painter.drawLine(QPointF(x + 7.0, y + 11.1), QPointF(x + 12.2, y + 5.0))

        painter.setPen(text_color if text_color.isValid() else self.palette().text().color())
        text_rect = self.rect().adjusted(box + self._spacing, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        painter.end()

class AppPreferencesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AppPreferencesDialog")
        self.setWindowTitle("Preferências")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)
        self.setSizeGripEnabled(True)
        self._applying_styles = False
        self._service = ConfigurationService.instance()
        self._stored_output_directory = ""
        self._stored_theme_mode = "system"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        subtitle = QLabel(
            "Preferências globais de importação, saída e diagnóstico.",
            self,
        )
        subtitle.setWordWrap(False)
        layout.addWidget(subtitle)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("AppPreferencesScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preferences_content = QWidget(self.scroll_area)
        self.preferences_content.setObjectName("AppPreferencesContent")
        content_layout = QVBoxLayout(self.preferences_content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        self.scroll_area.setWidget(self.preferences_content)
        layout.addWidget(self.scroll_area, 1)

        self.grp_import = QGroupBox("Importação e interação", self.preferences_content)
        import_layout = QVBoxLayout(self.grp_import)
        self.chk_assisted_import = _PreferenceCheckBox("Usar importação assistida como modo padrão", self.grp_import)
        self.chk_auto_open_overlay = _PreferenceCheckBox("Abrir configurações de compressão automaticamente após importar", self.grp_import)
        self.chk_confirm_delete = _PreferenceCheckBox("Confirmar antes de remover arquivo da lista", self.grp_import)
        import_layout.addWidget(self.chk_assisted_import)
        import_layout.addWidget(self.chk_auto_open_overlay)
        import_layout.addWidget(self.chk_confirm_delete)
        content_layout.addWidget(self.grp_import)

        self.grp_execution = QGroupBox("Execução e encerramento", self.preferences_content)
        execution_layout = QVBoxLayout(self.grp_execution)
        self.chk_show_global_progress = _PreferenceCheckBox("Exibir progresso global agregado", self.grp_execution)
        self.chk_allow_exit_during_processing = _PreferenceCheckBox("Permitir sair do app durante processamento", self.grp_execution)
        self.chk_allow_exit_during_processing.setToolTip(
            "Quando desativado, o app bloqueia o botão Sair e o fechamento da janela enquanto houver compressão em andamento."
        )
        execution_layout.addWidget(self.chk_show_global_progress)
        execution_layout.addWidget(self.chk_allow_exit_during_processing)
        content_layout.addWidget(self.grp_execution)

        self.grp_output = QGroupBox("Saída padrão", self.preferences_content)
        output_form = QFormLayout(self.grp_output)

        self.chk_use_source_directory = _PreferenceCheckBox(
            "Usar subpasta CompactMe na pasta de origem", self.grp_output
        )
        self.chk_use_source_directory.setToolTip(SOURCE_OUTPUT_DIRECTORY_TOOLTIP)
        output_form.addRow(self.chk_use_source_directory)

        output_dir_row = QHBoxLayout()
        self.edt_output_directory = QLineEdit(self.grp_output)
        self.edt_output_directory.setPlaceholderText(SOURCE_OUTPUT_DIRECTORY_DISPLAY)
        self.edt_output_directory.setToolTip(SOURCE_OUTPUT_DIRECTORY_TOOLTIP)
        self.btn_browse_output_directory = QPushButton("Selecionar...", self.grp_output)
        self.btn_browse_output_directory.setFixedHeight(32)
        self.btn_browse_output_directory.setStyleSheet(
            "QPushButton { min-height: 0px; max-height: 32px; padding: 2px 10px; }"
        )
        output_dir_row.addWidget(self.edt_output_directory, 1)
        output_dir_row.addWidget(self.btn_browse_output_directory)
        output_form.addRow("Pasta de saída", output_dir_row)

        self.cmb_output_naming_mode = QComboBox(self.grp_output)
        self.cmb_output_naming_mode.addItem("Manter nome original", 0)
        self.cmb_output_naming_mode.addItem("Adicionar sufixo", 1)
        self.cmb_output_naming_mode.addItem("Adicionar timestamp", 2)
        output_form.addRow("Formato do nome", self.cmb_output_naming_mode)

        self.edt_output_suffix = QLineEdit(self.grp_output)
        self.edt_output_suffix.setPlaceholderText("_compressed")
        output_form.addRow("Sufixo", self.edt_output_suffix)

        self.cmb_collision_policy = QComboBox(self.grp_output)
        self.cmb_collision_policy.addItem("Numerar automaticamente", 0)
        self.cmb_collision_policy.addItem("Sobrescrever arquivo existente", 1)
        self.cmb_collision_policy.addItem("Cancelar se já existir", 2)
        output_form.addRow("Quando já existir", self.cmb_collision_policy)

        content_layout.addWidget(self.grp_output)

        self.grp_logs = QGroupBox("Logs e diagnóstico", self.preferences_content)
        logs_layout = QVBoxLayout(self.grp_logs)
        self.chk_detailed_logging = _PreferenceCheckBox("Ativar logging detalhado (debug)", self.grp_logs)
        logs_layout.addWidget(self.chk_detailed_logging)

        logs_row = QHBoxLayout()
        self.lbl_logs_path = QLabel(str(Path(LOG_DIR)), self.grp_logs)
        self.lbl_logs_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.btn_open_logs = QPushButton("Abrir pasta de logs", self.grp_logs)
        self.btn_open_logs.setFixedHeight(32)
        self.btn_open_logs.setStyleSheet(
            "QPushButton { min-height: 0px; max-height: 32px; padding: 2px 10px; }"
        )
        logs_row.addWidget(self.lbl_logs_path, 1)
        logs_row.addWidget(self.btn_open_logs)
        logs_layout.addLayout(logs_row)
        content_layout.addWidget(self.grp_logs)

        content_layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if ok_button is not None and cancel_button is not None:
            cancel_button.setText("Cancelar")
            ok_button.setMinimumWidth(88)
            cancel_button.setMinimumWidth(88)
        self.btn_defaults = QPushButton("Restaurar padrões", self)
        buttons.addButton(self.btn_defaults, QDialogButtonBox.ResetRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        self.btn_defaults.clicked.connect(self.restore_defaults)
        layout.addWidget(buttons)

        self.btn_open_logs.clicked.connect(self._open_logs_folder)
        self.btn_browse_output_directory.clicked.connect(self._browse_output_directory)
        self.edt_output_directory.editingFinished.connect(self._normalize_output_directory_field)
        self.chk_use_source_directory.toggled.connect(self._sync_output_controls)
        self.cmb_output_naming_mode.currentIndexChanged.connect(self._sync_output_controls)

        self._load()
        self._apply_theme_styles()
        self._refresh_dialog_size()


    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_dialog_size()
        schedule_dialog_caption(self)

    def _refresh_dialog_size(self):
        self.layout().activate()
        if hasattr(self, "preferences_content"):
            self.preferences_content.layout().activate()

        parent = self.parentWidget()
        screen = None
        if parent is not None and parent.window() is not None:
            parent_window = parent.window()
            if parent_window.screen() is not None:
                screen = parent_window.screen()
            else:
                screen = QApplication.screenAt(parent_window.frameGeometry().center())
        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()

        available = screen.availableGeometry() if screen is not None else None
        max_width = int(available.width() * 0.92) if available is not None else 760
        screen_max_height = int(available.height() * 0.92) if available is not None else 720

        # Size against the current monitor instead of capping by the main window.
        # The main window can be shorter than the available screen area, especially
        # on notebooks or when the user restores the main window; preferences should
        # still use the monitor height and show the full content whenever possible.
        max_height = max(520, screen_max_height)

        layout_hint = self.layout().sizeHint()
        content_hint = self.preferences_content.sizeHint() if hasattr(self, "preferences_content") else QSize(0, 0)
        scroll_hint = self.scroll_area.sizeHint() if hasattr(self, "scroll_area") else QSize(0, 0)
        # Prefer showing the whole preferences content initially when the current
        # monitor height allows it.  When it does not fit, keep the footer fixed
        # and let only the central content scroll.
        extra_height = max(0, layout_hint.height() - scroll_hint.height())
        natural_height = max(layout_hint.height(), content_hint.height() + extra_height + 8)

        natural_width = max(520, self.sizeHint().width())
        width = min(max(520, natural_width), max(520, max_width))
        height = min(max_height, max(520, natural_height))

        self.setMaximumSize(max_width, screen_max_height)
        self.resize(width, height)

    def _apply_theme_styles(self):
        if self._applying_styles:
            return
        self._applying_styles = True
        try:
            app = QApplication.instance()
            source_palette = app.palette() if app is not None else self.palette()
            token_palette = palette_for_styled_dialog(source_palette)
            self.setPalette(token_palette)
            tokens = build_theme_tokens(token_palette, use_application_palette=False)
            stylesheet = build_button_stylesheet(
                tokens,
                min_height=30,
                border_radius=4,
                horizontal_padding=10,
            )

            input_bg = tokens.surface_card.name(QColor.NameFormat.HexArgb)
            input_fg = tokens.text_primary.name(QColor.NameFormat.HexArgb)
            input_border = tokens.button_border.name(QColor.NameFormat.HexArgb)
            selection_bg = tokens.button_hover_bg.name(QColor.NameFormat.HexArgb)
            selection_fg = tokens.text_primary.name(QColor.NameFormat.HexArgb)
            checkbox_text_color = QColor(tokens.text_primary)
            checkbox_indicator_bg = QColor(tokens.button_bg)
            checkbox_indicator_border = QColor(tokens.button_border)
            checkbox_checked_bg = QColor(tokens.quick_quality_button_checked_bg)
            checkbox_checked_border = QColor(tokens.quick_quality_button_checked_border)
            checkbox_check_color = QColor(tokens.text_on_dark_surface)

            # Keep the preferences dialog on the same native control metrics in
            # both themes.  The old dark-only stylesheet styled group boxes,
            # checkboxes, line edits and combo boxes, changing paddings and
            # making the dark dialog look slightly different from the validated
            # light layout.  Only the combo popup is styled because it is a
            # separate native view and can inherit stale colors after a theme
            # switch.
            combo_popup_stylesheet = f"""
                QDialog#AppPreferencesDialog QComboBox QAbstractItemView {{
                    background: {input_bg};
                    color: {input_fg};
                    border: 1px solid {input_border};
                    selection-background-color: {selection_bg};
                    selection-color: {selection_fg};
                    outline: 0px;
                }}
            """
            self.setAutoFillBackground(True)
            self.setStyleSheet(stylesheet + "\n" + combo_popup_stylesheet)
            for checkbox in (
                self.chk_assisted_import,
                self.chk_auto_open_overlay,
                self.chk_confirm_delete,
                self.chk_show_global_progress,
                self.chk_allow_exit_during_processing,
                self.chk_use_source_directory,
                self.chk_detailed_logging,
            ):
                checkbox.set_theme_colors(
                    text_color=checkbox_text_color,
                    indicator_bg=checkbox_indicator_bg,
                    indicator_border=checkbox_indicator_border,
                    checked_bg=checkbox_checked_bg,
                    checked_border=checkbox_checked_border,
                    check_color=checkbox_check_color,
                )
                checkbox.style().unpolish(checkbox)
                checkbox.style().polish(checkbox)
                checkbox.update()
            self.update()
        finally:
            self._applying_styles = False

    def _combo_arrow_svg_url(self, color_name: str) -> str:
        safe_color = str(color_name or "").strip()
        if not safe_color.startswith("#"):
            safe_color = "#000000"
        asset_dir = Path(tempfile.gettempdir()) / THEME_ASSET_TEMP_DIR_NAME
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / f"combo_arrow_{safe_color.lstrip('#')}.svg"
        if not asset_path.exists():
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
                f'<polyline points="4,6 8,10 12,6" fill="none" stroke="{safe_color}" '
                'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
                '</svg>'
            )
            asset_path.write_text(svg, encoding="utf-8")
        return asset_path.as_posix()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self._apply_theme_styles()
            schedule_dialog_caption(self)
    
    def _load(self):
        cfg = self._service.get()
        self._stored_theme_mode = getattr(cfg, "theme_mode", "system")
        self.chk_assisted_import.setChecked(getattr(cfg, "assisted_import_default", True))
        self.chk_auto_open_overlay.setChecked(getattr(cfg, "auto_open_profile_settings_after_import", getattr(cfg, "auto_open_configuration_after_import", False)))
        self.chk_confirm_delete.setChecked(getattr(cfg, "confirm_delete", True))
        self.chk_show_global_progress.setChecked(getattr(cfg, "show_global_progress", True))
        self.chk_allow_exit_during_processing.setChecked(getattr(cfg, "allow_exit_during_processing", False))
        self.chk_detailed_logging.setChecked(getattr(cfg, "detailed_logging", False))

        self._stored_output_directory = _stored_output_directory_from_text(getattr(cfg, "output_directory", ""))
        self.chk_use_source_directory.setChecked(getattr(cfg, "use_source_directory", True))
        self.edt_output_directory.setText(_display_output_directory(self._stored_output_directory))
        naming_index = max(0, self.cmb_output_naming_mode.findData(getattr(cfg, "output_naming_mode", 1)))
        self.cmb_output_naming_mode.setCurrentIndex(naming_index)
        self.edt_output_suffix.setText(getattr(cfg, "output_suffix", "_compressed"))
        collision_index = max(0, self.cmb_collision_policy.findData(getattr(cfg, "collision_policy", 0)))
        self.cmb_collision_policy.setCurrentIndex(collision_index)
        self._sync_output_controls()

    def restore_defaults(self):
        current_cfg = self._service.get()
        self._stored_theme_mode = getattr(current_cfg, "theme_mode", "system")
        cfg = AppConfiguration()
        self.chk_assisted_import.setChecked(cfg.assisted_import_default)
        self.chk_auto_open_overlay.setChecked(cfg.auto_open_profile_settings_after_import)
        self.chk_confirm_delete.setChecked(cfg.confirm_delete)
        self.chk_show_global_progress.setChecked(cfg.show_global_progress)
        self.chk_allow_exit_during_processing.setChecked(cfg.allow_exit_during_processing)
        self.chk_detailed_logging.setChecked(cfg.detailed_logging)

        self._stored_output_directory = _stored_output_directory_from_text(cfg.output_directory)
        self.chk_use_source_directory.setChecked(cfg.use_source_directory)
        self.edt_output_directory.setText(_display_output_directory(cfg.output_directory))
        naming_index = max(0, self.cmb_output_naming_mode.findData(cfg.output_naming_mode))
        self.cmb_output_naming_mode.setCurrentIndex(naming_index)
        self.edt_output_suffix.setText(cfg.output_suffix)
        collision_index = max(0, self.cmb_collision_policy.findData(cfg.collision_policy))
        self.cmb_collision_policy.setCurrentIndex(collision_index)
        self._sync_output_controls()

    def _collect_values(self):
        suffix = self.edt_output_suffix.text().strip() or "_compressed"
        return {
            "theme_mode": self._stored_theme_mode,
            "assisted_import_default": self.chk_assisted_import.isChecked(),
            "auto_open_profile_settings_after_import": self.chk_auto_open_overlay.isChecked(),
            "auto_open_configuration_after_import": self.chk_auto_open_overlay.isChecked(),
            "confirm_delete": self.chk_confirm_delete.isChecked(),
            "show_global_progress": self.chk_show_global_progress.isChecked(),
            "allow_exit_during_processing": self.chk_allow_exit_during_processing.isChecked(),
            "detailed_logging": self.chk_detailed_logging.isChecked(),
            "use_source_directory": self.chk_use_source_directory.isChecked(),
            "output_directory": self._collect_output_directory(),
            "output_naming_mode": self.cmb_output_naming_mode.currentData(),
            "output_suffix": suffix,
            "collision_policy": self.cmb_collision_policy.currentData(),
        }

    def _collect_output_directory(self):
        if self.chk_use_source_directory.isChecked():
            return self._stored_output_directory
        text = self.edt_output_directory.text().strip()
        if text == SOURCE_OUTPUT_DIRECTORY_DISPLAY:
            return ""
        return _stored_output_directory_from_text(text)

    def _sync_output_controls(self):
        use_source = self.chk_use_source_directory.isChecked()
        naming_mode = self.cmb_output_naming_mode.currentData()

        if use_source:
            current_text = self.edt_output_directory.text().strip()
            if current_text and current_text != SOURCE_OUTPUT_DIRECTORY_DISPLAY:
                self._stored_output_directory = _stored_output_directory_from_text(current_text)
            self.edt_output_directory.setText(SOURCE_OUTPUT_DIRECTORY_DISPLAY)
        else:
            if self.edt_output_directory.text().strip() == SOURCE_OUTPUT_DIRECTORY_DISPLAY:
                self.edt_output_directory.setText(_display_output_directory(self._stored_output_directory))

        self.edt_output_directory.setEnabled(not use_source)
        self.btn_browse_output_directory.setEnabled(not use_source)

        suffix_mode = naming_mode == 1
        self.edt_output_suffix.setEnabled(suffix_mode)

    def _normalize_output_directory_field(self):
        if self.chk_use_source_directory.isChecked():
            return
        text = self.edt_output_directory.text().strip()
        if not text or text == SOURCE_OUTPUT_DIRECTORY_DISPLAY:
            return
        self._stored_output_directory = _stored_output_directory_from_text(text)
        self.edt_output_directory.setText(_display_output_directory(self._stored_output_directory))

    def _browse_output_directory(self):
        current = self._collect_output_directory() or str(Path.home())
        directory = QFileDialog.getExistingDirectory(self, "Selecionar pasta de saída", current)
        if directory:
            self._stored_output_directory = _stored_output_directory_from_text(directory)
            self.edt_output_directory.setText(_display_output_directory(self._stored_output_directory))

    def _accept(self):
        self._service.update(**self._collect_values())
        event_bridge.emit("app_preferences_changed", dict(self._collect_values()))
        self.accept()

    def _open_logs_folder(self):
        try:
            folder = str(Path(LOG_DIR))
            if os.name == "nt":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception:
            _LOGGER.debug("Failed to open logs folder from preferences dialog.", exc_info=True)
