
import os
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app.engine.ffprobe_probe import probe
from app.models.file_table_model import FileTableModel
from app.core.profiles import audio as audio_profile
from app.ui.theme_tokens import build_theme_tokens, build_button_stylesheet, tooltip_text_html, color_to_css


AUDIO_EXTENSIONS = (
    ".mp3",".aac",".wav",".flac",".ogg",".m4a",".wma",".opus",".alac"
)

VIDEO_EXTENSIONS = (
    ".mp4",".mkv",".webm",".mov",".avi",".flv",".ts",".m4v",".wmv"
)

SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS + VIDEO_EXTENSIONS


def natural_key(path):
    name = os.path.basename(path).lower()
    return [
        int(text) if text.isdigit() else text
        for text in re.split(r'(\d+)', name)
    ]


class AssistedImportDialog(QDialog):

    def __init__(self, video_count, audio_count, parent=None, files=None):
        super().__init__(parent)

        self.files = files[:] if files else []
        self._file_import_settings: dict[str, dict] = {}
        self._loading_file_import_settings = False
        self._smart_audio_context_cache = {"audio_streams": [], "audio_track_count": 0, "primary_audio_channels": 2}

        self.setWindowTitle("Importação Assistida")
        self.resize(700, 560)
        self.setMinimumSize(660, 520)
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.status_bar = QWidget()
        self.status_bar.setObjectName("AssistedImportHeaderStatus")
        self.status_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        status_bar_layout = QHBoxLayout(self.status_bar)
        status_bar_layout.setContentsMargins(0, 0, 0, 0)
        status_bar_layout.setSpacing(0)

        self.status_primary = QLabel()
        self.status_primary.setObjectName("AssistedImportHeaderPrimary")
        self.status_primary.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        self.status_sep_1 = QLabel(" · ")
        self.status_sep_1.setObjectName("AssistedImportHeaderSeparator")
        self.status_sep_1.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        self.status_video = QLabel()
        self.status_video.setObjectName("AssistedImportHeaderSecondary")
        self.status_video.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        self.status_sep_2 = QLabel(" · ")
        self.status_sep_2.setObjectName("AssistedImportHeaderSeparator")
        self.status_sep_2.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        self.status_audio = QLabel()
        self.status_audio.setObjectName("AssistedImportHeaderSecondary")
        self.status_audio.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        status_bar_layout.addWidget(self.status_primary)
        status_bar_layout.addWidget(self.status_sep_1)
        status_bar_layout.addWidget(self.status_video)
        status_bar_layout.addWidget(self.status_sep_2)
        status_bar_layout.addWidget(self.status_audio)
        status_bar_layout.addStretch(1)

        self.add_files_btn = QPushButton("Adicionar arquivos")
        self.remove_btn = QPushButton("Remover selecionado")
        self.add_files_btn.setMinimumWidth(150)
        self.remove_btn.setMinimumWidth(150)
        self.add_files_btn.clicked.connect(self._add_files)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)
        header_row.addWidget(self.status_bar, 1)
        header_row.addWidget(self.add_files_btn)
        header_row.addWidget(self.remove_btn)
        layout.addLayout(header_row)

        self.list_panel = QFrame()
        self.list_panel.setObjectName("AssistedImportListPanel")
        self.list_panel.setProperty("dragActive", False)
        list_panel_layout = QVBoxLayout(self.list_panel)
        list_panel_layout.setContentsMargins(0, 0, 0, 0)
        list_panel_layout.setSpacing(0)

        self.table = QTableView()
        self.table.setProperty("dragActive", False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(33)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setSortingEnabled(True)
        self.table.setCornerButtonEnabled(False)
        self.table.setMinimumHeight(240)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.setFrameShape(QFrame.NoFrame)

        self.drop_hint = QLabel(self.table.viewport())
        self.drop_hint.setObjectName("AssistedImportDropHint")
        self.drop_hint.setAlignment(Qt.AlignCenter)
        self.drop_hint.setTextFormat(Qt.RichText)
        self.drop_hint.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.model = FileTableModel(self.files)
        self.table.setModel(self.model)
        self.table.sortByColumn(0, Qt.AscendingOrder)

        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setFixedHeight(26)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Fixed)

        self.table.setColumnWidth(1, 92)
        self.table.setColumnWidth(2, 72)

        self.status_container = QFrame()
        self.status_container.setObjectName("AssistedImportListStatus")
        status_layout = QHBoxLayout(self.status_container)
        status_layout.setContentsMargins(8, 4, 8, 4)
        status_layout.setSpacing(0)
        status_layout.addWidget(QLabel(""), 1)
        self.status_container.setVisible(False)

        list_panel_layout.addWidget(self.table, 1)
        list_panel_layout.addWidget(self.status_container)
        layout.addWidget(self.list_panel, 1)

        self._update_drop_hint()

        self.del_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), self.table)
        self.del_shortcut.activated.connect(self._remove_selected)

        self.remove_btn.setEnabled(False)
        sel_model = self.table.selectionModel()
        sel_model.selectionChanged.connect(self._on_selection_changed)
        self.remove_btn.clicked.connect(self._remove_selected)

        panels = QGridLayout()
        panels.setContentsMargins(0, 0, 0, 0)
        panels.setHorizontalSpacing(10)
        panels.setVerticalSpacing(10)
        layout.addLayout(panels)

        self.video_box = QGroupBox("Arquivos de vídeo")
        self.video_form = QFormLayout(self.video_box)
        self.video_form.setContentsMargins(12, 10, 12, 10)
        self.video_form.setHorizontalSpacing(10)
        self.video_form.setVerticalSpacing(8)

        self.v_compress = QRadioButton("Compactar vídeo")
        self.v_convert = QRadioButton("Converter vídeo")
        self.v_extract = QRadioButton("Extrair áudio")
        self.v_compress.setChecked(True)

        self.v_mode_video = QComboBox()
        self.v_mode_video.addItems(["Compactar", "Converter", "Extrair áudio"])
        vg = QButtonGroup(self)
        vg.addButton(self.v_compress)
        vg.addButton(self.v_convert)
        vg.addButton(self.v_extract)
        self.video_form.addRow("Modo do vídeo", self.v_mode_video)

        self.v_container = QComboBox()
        self._video_container_options = ["MP4", "MKV", "WEBM", "MOV", "AVI", "FLV", "TS", "M4V"]
        self._audio_extract_format_options = list(audio_profile.AUDIO_FORMAT_OPTIONS)
        self.v_container.addItems(self._video_container_options)
        self.video_form.addRow("Formato de saída", self.v_container)
        panels.addWidget(self.video_box, 0, 0)

        self.audio_box = QGroupBox("Arquivos de áudio")
        self.audio_form = QFormLayout(self.audio_box)
        self.audio_form.setContentsMargins(12, 10, 12, 10)
        self.audio_form.setHorizontalSpacing(10)
        self.audio_form.setVerticalSpacing(8)

        self.a_convert = QRadioButton("Converter áudio")
        self.a_keep = QRadioButton("Copiar áudio")
        self.a_remove = QRadioButton("Remover áudio")
        self.a_convert.setChecked(True)

        self.v_mode_audio = QComboBox()
        self.v_mode_audio.addItems(["Converter", "Copiar áudio", "Remover"])
        ag = QButtonGroup(self)
        ag.addButton(self.a_convert)
        ag.addButton(self.a_keep)
        ag.addButton(self.a_remove)
        self.audio_form.addRow("Modo do áudio", self.v_mode_audio)

        self.a_format = QComboBox()
        for fmt in audio_profile.AUDIO_FORMAT_OPTIONS:
            self.a_format.addItem(audio_profile.display_label_for_format(fmt), fmt)
        self.audio_form.addRow("Formato de saída", self.a_format)
        panels.addWidget(self.audio_box, 0, 1)

        panels.setColumnStretch(0, 1)
        panels.setColumnStretch(1, 1)

        footer_wrap = QWidget()
        footer_wrap.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        footer = QHBoxLayout(footer_wrap)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)

        self.cancel_btn = QPushButton("Cancelar")
        self.add_btn = QPushButton("Enviar para fila")
        self.add_btn.setObjectName("AssistedImportPrimaryAction")
        self.add_btn.setDefault(True)
        self.add_btn.setAutoDefault(True)
        self.add_btn.setFocusPolicy(Qt.StrongFocus)
        self.add_btn.setEnabled(len(self.files) > 0)
        self.cancel_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.add_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cancel_btn.clicked.connect(self.reject)
        self.add_btn.clicked.connect(self._enqueue)
        footer.addWidget(self.cancel_btn, 1)
        footer.addWidget(self.add_btn, 1)
        layout.addWidget(footer_wrap)

        self.v_mode_video.currentIndexChanged.connect(self._update_media_controls)
        self.v_mode_video.currentIndexChanged.connect(self._on_import_control_changed)
        self.v_container.currentIndexChanged.connect(self._on_import_control_changed)
        self.v_mode_audio.currentIndexChanged.connect(self._update_media_controls)
        self.v_mode_audio.currentIndexChanged.connect(self._on_import_control_changed)
        self.a_format.currentIndexChanged.connect(self._on_import_control_changed)

        # Hidden compression/profile state (kept only for internal payload defaults)
        self.profile_box = QGroupBox("Perfil de compressão", self)
        self.profile_box.hide()
        profile_layout = QVBoxLayout(self.profile_box)
        self.profile_intro = QLabel()
        self.profile_intro.setWordWrap(True)
        self.profile_intro.setObjectName("AssistedImportMutedHint")
        profile_layout.addWidget(self.profile_intro)

        self.compression_mode_group = QButtonGroup(self)
        self.compression_mode_group.setExclusive(True)
        self.compression_mode_buttons = {}
        for key in ("Estratégico", "Avançado"):
            button = QPushButton(key, self.profile_box)
            button.setCheckable(True)
            self.compression_mode_group.addButton(button)
            self.compression_mode_buttons[key] = button
        self.compression_mode_buttons["Estratégico"].setChecked(True)
        self.compression_mode_group.buttonToggled.connect(self._on_compression_mode_toggled)

        self.profile_mode_stack = QStackedWidget(self.profile_box)
        profile_layout.addWidget(self.profile_mode_stack)

        self.smart_panel = QWidget(self.profile_box)
        smart_layout = QVBoxLayout(self.smart_panel)
        self.smart_strategy_group = QButtonGroup(self)
        self.smart_strategy_group.setExclusive(True)
        self.smart_strategy_buttons = {}
        for index, label in enumerate(("Menor tamanho", "Equilíbrio", "Maior qualidade")):
            button = QPushButton(label, self.smart_panel)
            button.setCheckable(True)
            self.smart_strategy_group.addButton(button, index)
            self.smart_strategy_buttons[index] = button
            smart_layout.addWidget(button)
        self.smart_strategy_buttons[1].setChecked(True)
        self.smart_strategy_group.idToggled.connect(self._on_smart_strategy_toggled)

        self.smart_intensity = QSlider(Qt.Horizontal, self.smart_panel)
        self.smart_intensity.setRange(0, 100)
        self.smart_intensity.setSingleStep(10)
        self.smart_intensity.setPageStep(10)
        self.smart_intensity.setTickInterval(10)
        self.smart_intensity.setValue(50)
        self.smart_intensity.valueChanged.connect(self._update_estimate_preview)
        self.smart_intensity_min = QLabel("0.0", self.smart_panel)
        self.smart_intensity_max = QLabel("10.0", self.smart_panel)
        self.smart_intensity_value = QLabel("5.0", self.smart_panel)
        smart_layout.addWidget(self.smart_intensity)

        self.smart_audio_box = QWidget(self.smart_panel)
        smart_audio_layout = QVBoxLayout(self.smart_audio_box)
        self.smart_audio_title = QLabel("Bloco de Otimização de Áudio", self.smart_audio_box)
        self.smart_audio_title.setObjectName("AssistedImportSectionTitle")
        smart_audio_layout.addWidget(self.smart_audio_title)
        self.smart_audio_intro = QLabel(self.smart_audio_box)
        self.smart_audio_intro.setWordWrap(True)
        smart_audio_layout.addWidget(self.smart_audio_intro)
        self.smart_audio_keep_all = QRadioButton("Manter todas as trilhas", self.smart_audio_box)
        self.smart_audio_keep_default = QRadioButton("Manter apenas a trilha padrão", self.smart_audio_box)
        self.smart_audio_select_specific = QRadioButton("Selecionar uma trilha específica", self.smart_audio_box)
        self.smart_audio_select_specific.toggled.connect(self._update_profile_controls)
        self.smart_audio_keep_default.toggled.connect(self._update_estimate_preview)
        self.smart_audio_keep_all.toggled.connect(self._update_estimate_preview)
        self.smart_audio_select_specific.toggled.connect(self._update_estimate_preview)
        smart_audio_layout.addWidget(self.smart_audio_keep_all)
        smart_audio_layout.addWidget(self.smart_audio_keep_default)
        smart_audio_layout.addWidget(self.smart_audio_select_specific)
        self.smart_audio_track_picker = QComboBox(self.smart_audio_box)
        self.smart_audio_track_picker.currentIndexChanged.connect(self._update_estimate_preview)
        smart_audio_layout.addWidget(self.smart_audio_track_picker)
        self.smart_channel_intro = QLabel("A trilha principal possui múltiplos canais (ex: 5.1).", self.smart_audio_box)
        self.smart_channel_intro.setWordWrap(True)
        smart_audio_layout.addWidget(self.smart_channel_intro)
        self.smart_audio_keep_channels = QRadioButton("Manter canais originais", self.smart_audio_box)
        self.smart_audio_downmix = QRadioButton("Converter para estéreo para reduzir tamanho", self.smart_audio_box)
        self.smart_audio_keep_channels.toggled.connect(self._update_estimate_preview)
        self.smart_audio_downmix.toggled.connect(self._update_estimate_preview)
        smart_audio_layout.addWidget(self.smart_audio_keep_channels)
        smart_audio_layout.addWidget(self.smart_audio_downmix)
        smart_layout.addWidget(self.smart_audio_box)
        self.profile_mode_stack.addWidget(self.smart_panel)

        self.advanced_panel = QFrame(self.profile_box)
        advanced_form = QFormLayout(self.advanced_panel)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setSpacing(5)
        self.advanced_target_mode = QComboBox(self.advanced_panel)
        self.advanced_target_mode.addItems(["Tamanho", "Bitrate"])
        advanced_form.addRow("Alvo avançado", self.advanced_target_mode)
        self.advanced_target_size_mb = QSpinBox(self.advanced_panel)
        self.advanced_target_size_mb.setRange(1, 102400)
        self.advanced_target_size_mb.setValue(25)
        advanced_form.addRow("Tamanho alvo (MB)", self.advanced_target_size_mb)
        self.advanced_target_bitrate_kbps = QSpinBox(self.advanced_panel)
        self.advanced_target_bitrate_kbps.setRange(100, 50000)
        self.advanced_target_bitrate_kbps.setSingleStep(100)
        self.advanced_target_bitrate_kbps.setValue(1200)
        advanced_form.addRow("Bitrate alvo (kbps)", self.advanced_target_bitrate_kbps)
        self.profile_mode_stack.addWidget(self.advanced_panel)

        self.profile_empty_hint = QLabel("Adicione ao menos um arquivo de vídeo para configurar a compressão.", self.profile_box)
        self.profile_empty_hint.setWordWrap(True)
        self.profile_empty_hint.setObjectName("AssistedImportMutedHint")
        profile_layout.addWidget(self.profile_empty_hint)

        self.advanced_target_mode.currentIndexChanged.connect(self._update_profile_controls)
        self.advanced_target_size_mb.valueChanged.connect(self._update_estimate_preview)
        self.advanced_target_bitrate_kbps.valueChanged.connect(self._update_estimate_preview)

        self.estimate_primary = QLabel("Aguardando arquivos", self)
        self.estimate_secondary = QLabel("Adicione arquivos para gerar uma leitura aproximada do lote.", self)
        self.estimate_secondary.setObjectName("AssistedImportMutedHint")
        self.estimate_note = QLabel("", self)
        self.estimate_note.setObjectName("AssistedImportMutedHint")
        self.estimate_primary.hide()
        self.estimate_secondary.hide()
        self.estimate_note.hide()

        self.update_header()
        self._apply_theme_styles()
        self._update_drop_hint()
        self.add_btn.setEnabled(len(self.files) > 0)
        self._update_media_controls()
        self._update_profile_controls()
        self._update_estimate_preview()
        self._schedule_default_action_focus()


    def _theme_tokens(self):
        return build_theme_tokens(self.palette())

    def _muted_text_color(self):
        return self._theme_tokens().dialog_hint_text.name(QColor.HexArgb)

    def _apply_theme_styles(self):
        if not hasattr(self, "drop_hint"):
            return
        muted = self._muted_text_color()
        self.drop_hint.setText(
            '<div style="text-align:center;">'
            f'<div style="font-size:16px;">{tooltip_text_html("Arraste arquivos aqui", self._theme_tokens().dialog_hint_text)}</div>'
            f'<div style="font-size:13px;">{tooltip_text_html("ou clique em Adicionar arquivos", self._theme_tokens().dialog_hint_text)}</div>'
            '</div>'
        )
        stylesheet = build_button_stylesheet(self._theme_tokens())
        prefixed_stylesheet = stylesheet.replace("QPushButton", "QDialog QPushButton")
        drop_border = color_to_css(self._theme_tokens().dialog_drop_border)
        active_drop_border = color_to_css(self._theme_tokens().drop_target_border)
        self.setStyleSheet(
            prefixed_stylesheet
            + f"""
            QLabel#AssistedImportDropHint {{
                background: transparent;
                border: none;
            }}
            QFrame#AssistedImportListPanel[dragActive="false"] {{
                background: {color_to_css(self._theme_tokens().surface_panel)};
                border: 1px solid {drop_border};
                border-radius: 0px;
            }}
            QFrame#AssistedImportListPanel[dragActive="true"] {{
                background: {color_to_css(self._theme_tokens().surface_panel)};
                border: 2px dashed {active_drop_border};
                border-radius: 0px;
            }}
            QTableView {{
                border: none;
                background: transparent;
            }}
            QTableView::item:selected {{
                background: rgba(32, 120, 200, 0.22);
                color: palette(text);
            }}
            QTableView::item:selected:active {{
                background: rgba(32, 120, 200, 0.26);
                color: palette(text);
            }}
            QTableView::item:selected:!active {{
                background: rgba(32, 120, 200, 0.18);
                color: palette(text);
            }}
            QHeaderView::section {{
                background: rgba(128, 128, 128, 0.10);
                color: {color_to_css(self._theme_tokens().surface_list_header_text)};
                border-top: none;
                border-left: none;
                border-bottom: 1px solid {drop_border};
                border-right: 1px solid {drop_border};
                padding: 0 8px;
            }}
            QHeaderView::section:last {{
                border-right: none;
            }}
            QLabel#AssistedImportMutedHint {{
                color: {muted};
            }}
            QWidget#AssistedImportHeaderStatus {{
                background: transparent;
            }}
            QLabel#AssistedImportHeaderPrimary {{
                color: {color_to_css(self._theme_tokens().text_primary)};
                font-weight: 600;
            }}
            QLabel#AssistedImportHeaderSecondary {{
                color: {color_to_css(self._theme_tokens().text_secondary)};
            }}
            QLabel#AssistedImportHeaderSeparator {{
                color: {color_to_css(self._theme_tokens().dialog_hint_text)};
            }}
            QFrame#AssistedImportFlatPanel {{
                background: {color_to_css(self._theme_tokens().surface_card)};
                border: 1px solid {color_to_css(self._theme_tokens().border_card)};
                border-radius: 8px;
            }}
            QFrame#AssistedImportListStatus {{
                background: transparent;
                border: none;
            }}
            QLabel#AssistedImportSectionTitle {{
                color: {muted};
                font-weight: 600;
                padding: 0 0 2px 0;
            }}
            QPushButton#AssistedImportModeButton {{
                min-height: 25px;
                max-height: 27px;
                padding: 0px 10px 0px 10px;
                border-radius: 0px;
                border-right-width: 0px;
                border-bottom-width: 1px;
            }}
            QPushButton#AssistedImportModeButton[segment="left"] {{
                border-top-left-radius: 7px;
                border-bottom-left-radius: 7px;
            }}
            QPushButton#AssistedImportModeButton[segment="right"] {{
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
                border-right-width: 1px;
            }}
            QPushButton#AssistedImportModeButton:checked {{
                background: {color_to_css(self._theme_tokens().button_pressed_bg)};
                border-color: {active_drop_border};
                border-bottom-color: {active_drop_border};
                color: {color_to_css(self._theme_tokens().button_fg)};
                font-weight: 700;
            }}
            QPushButton#AssistedImportStrategyButton {{
                min-height: 27px;
                max-height: 29px;
                padding: 0px 6px 0px 6px;
                border-radius: 0px;
                border-right-width: 0px;
                border-bottom-width: 1px;
                font-size: 11px;
            }}
            QPushButton#AssistedImportStrategyButton[segment="left"] {{
                border-top-left-radius: 7px;
                border-bottom-left-radius: 7px;
            }}
            QPushButton#AssistedImportStrategyButton[segment="right"] {{
                border-top-right-radius: 7px;
                border-bottom-right-radius: 7px;
                border-right-width: 1px;
            }}
            QPushButton#AssistedImportPrimaryAction:default,
            QPushButton#AssistedImportPrimaryAction:focus {{
                background: {color_to_css(self._theme_tokens().button_bg)};
                color: {color_to_css(self._theme_tokens().button_fg)};
                border: 2px solid {color_to_css(self._theme_tokens().button_hover_border)};
                padding-left: 9px;
                padding-right: 9px;
                font-weight: 600;
            }}
            QPushButton#AssistedImportPrimaryAction:hover {{
                background: {color_to_css(self._theme_tokens().button_hover_bg)};
                border: 2px solid {color_to_css(self._theme_tokens().button_hover_border)};
                padding-left: 9px;
                padding-right: 9px;
            }}
            QPushButton#AssistedImportPrimaryAction:pressed {{
                background: {color_to_css(self._theme_tokens().button_pressed_bg)};
                border: 2px solid {color_to_css(self._theme_tokens().button_pressed_border)};
                padding-left: 9px;
                padding-right: 9px;
            }}
            QPushButton#AssistedImportStrategyButton:checked {{
                background: {color_to_css(self._theme_tokens().button_pressed_bg)};
                border-color: {active_drop_border};
                border-bottom-color: {active_drop_border};
                color: {color_to_css(self._theme_tokens().button_fg)};
                font-weight: 700;
            }}
            """
        )

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (event.Type.PaletteChange, event.Type.ApplicationPaletteChange, event.Type.ThemeChange):
            self._apply_theme_styles()

    # ------------------------------------------------

    def dragEnterEvent(self, event):

        if event.mimeData().hasUrls():
            self.list_panel.setProperty("dragActive", True)
            self.list_panel.style().unpolish(self.list_panel)
            self.list_panel.style().polish(self.list_panel)
            self.list_panel.update()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.list_panel.setProperty("dragActive", False)
        self.list_panel.style().unpolish(self.list_panel)
        self.list_panel.style().polish(self.list_panel)
        self.list_panel.update()
        event.accept()

    def dropEvent(self, event):

        self.list_panel.setProperty("dragActive", False)
        self.list_panel.style().unpolish(self.list_panel)
        self.list_panel.style().polish(self.list_panel)
        self.list_panel.update()

        if not event.mimeData().hasUrls():
            return

        paths = []

        for url in event.mimeData().urls():
            path = url.toLocalFile()

            if path and os.path.isfile(path):
                paths.append(path)

        if paths:
            self._add_paths(paths)

        event.acceptProposedAction()

    # ------------------------------------------------

    def update_header(self):

        video = 0
        audio = 0

        for f in self.files:

            ext = os.path.splitext(f)[1].lower()

            if ext in AUDIO_EXTENSIONS:
                audio += 1
            else:
                video += 1

        total = video + audio

        has_video = video > 0
        has_audio = audio > 0

        self.video_box.setVisible(True)
        self.video_box.setEnabled(has_video)
        self.audio_box.setVisible(True)
        self.audio_box.setEnabled(has_audio)
        self.profile_box.setVisible(True)
        self.profile_empty_hint.setVisible(not has_video)

        file_phrase = "1 arquivo selecionado" if total == 1 else f"{total} arquivos selecionados"
        self.status_primary.setText(file_phrase)
        self.status_video.setText(f"Vídeos: {video}")
        self.status_audio.setText(f"Áudios: {audio}")

    # ------------------------------------------------

    def _total_input_size_bytes(self):
        total = 0
        for path in self.files:
            try:
                total += os.path.getsize(path)
            except OSError:
                continue
        return total

    def _count_media_types(self):
        video = 0
        audio = 0
        for path in self.files:
            ext = os.path.splitext(path)[1].lower()
            if ext in AUDIO_EXTENSIONS:
                audio += 1
            else:
                video += 1
        return video, audio

    def _selected_smart_strategy(self):
        return {
            0: "Economia Máxima",
            1: "Equilíbrio",
            2: "Qualidade Prioritária",
        }.get(self._smart_strategy_index(), "Equilíbrio")

    def _smart_strategy_index(self):
        checked = getattr(self, "smart_strategy_group", None)
        if checked is not None:
            idx = self.smart_strategy_group.checkedId()
            if idx >= 0:
                return idx
        return 1

    def _on_smart_strategy_toggled(self, strategy_id, checked):
        if checked:
            self._update_estimate_preview()

    def _smart_slider_value(self):
        return round(self.smart_intensity.value() / 10.0, 1)

    def _smart_effective_intensity(self):
        return max(0, min(10, int(round(self._smart_slider_value()))))

    def _current_compression_mode_text(self):
        if getattr(self, "compression_mode_buttons", {}).get("Avançado") and self.compression_mode_buttons["Avançado"].isChecked():
            return "Avançado"
        return "Estratégico"

    def _on_compression_mode_toggled(self, button, checked):
        if checked:
            self._update_profile_controls()

    def _current_smart_audio_track_policy(self):
        if self.smart_audio_select_specific.isChecked():
            return "SELECTED_ONLY"
        if self.smart_audio_keep_default.isChecked():
            return "KEEP_DEFAULT_ONLY"
        return "KEEP_ALL"

    def _current_smart_audio_channel_policy(self):
        return "DOWNMIX_TO_STEREO" if self.smart_audio_downmix.isChecked() else "KEEP_ORIGINAL"

    def _refresh_smart_audio_context(self):
        cache = {"audio_streams": [], "audio_track_count": 0, "primary_audio_channels": 2}
        representative = None
        for path in self.files:
            if os.path.splitext(path)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            result = probe(path)
            if not result or len(result) < 9:
                continue
            audio_streams, track_count, channels = result[6], result[7], result[8]
            candidate = {
                "audio_streams": audio_streams or [],
                "audio_track_count": int(track_count or len(audio_streams or [])),
                "primary_audio_channels": int(channels or 2),
            }
            if candidate["audio_track_count"] > 1 or candidate["primary_audio_channels"] > 2:
                representative = candidate
                break
            if representative is None:
                representative = candidate
        if representative is not None:
            cache = representative
        self._smart_audio_context_cache = cache

        show_track_policy = cache["audio_track_count"] > 1
        show_channel_policy = cache["primary_audio_channels"] > 2
        self.smart_audio_box.setVisible(show_track_policy or show_channel_policy)
        self.smart_audio_keep_all.setVisible(show_track_policy)
        self.smart_audio_keep_default.setVisible(show_track_policy)
        self.smart_audio_select_specific.setVisible(show_track_policy)
        self.smart_audio_track_picker.setVisible(show_track_policy)
        self.smart_channel_intro.setVisible(show_channel_policy)
        self.smart_audio_keep_channels.setVisible(show_channel_policy)
        self.smart_audio_downmix.setVisible(show_channel_policy)
        if show_track_policy:
            self.smart_audio_intro.setText("Foram encontradas múltiplas trilhas de áudio. O que você deseja fazer?")
        elif show_channel_policy:
            self.smart_audio_intro.setText("A trilha principal possui múltiplos canais (ex: 5.1).")
        self.smart_audio_track_picker.blockSignals(True)
        self.smart_audio_track_picker.clear()
        for stream in cache["audio_streams"]:
            self.smart_audio_track_picker.addItem(str(stream.get("label") or f"Trilha {stream.get('id', 0) + 1}"), int(stream.get("id", 0)))
        self.smart_audio_track_picker.blockSignals(False)
        self.smart_audio_keep_all.setChecked(True)
        self.smart_audio_keep_channels.setChecked(True)
        self.smart_audio_track_picker.setEnabled(self.smart_audio_select_specific.isChecked() and self.smart_audio_track_picker.count() > 0)

    def _selected_lossy_to_lossless_warning(self, output_format: str | None, *, paths=None) -> str:
        candidates = list(paths) if paths is not None else list(self.files)
        for path in candidates:
            warning = audio_profile.lossy_to_lossless_warning(path, output_format)
            if warning:
                return warning
        return ""

    def _estimate_output_summary(self):
        total_bytes = self._total_input_size_bytes()
        video_count, audio_count = self._count_media_types()

        if total_bytes <= 0 or not self.files:
            return (
                "Resumo da configuração",
                "Adicione arquivos para revisar o modo ativo.",
                "",
            )

        if video_count == 0:
            audio_mode = self.v_mode_audio.currentText()
            if audio_mode == "Copiar áudio":
                return (
                    "Áudio sem recompressão",
                    "Modo ativo: copiar áudio.",
                    "",
                )
            if audio_mode == "Converter":
                target_format = self._current_audio_format()
                target_label = self._audio_format_label(target_format)
                warning = self._selected_lossy_to_lossless_warning(target_format, paths=[p for p in self.files if os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS])
                return (
                    f"Conversão de áudio para {target_label}",
                    "Modo ativo: converter áudio.",
                    warning,
                )
            return (
                "Remoção de áudio",
                "Modo ativo: remover áudio.",
                "",
            )

        video_mode = self.v_mode_video.currentText()
        if video_mode == "Extrair áudio":
            target_format = self._current_video_panel_audio_format()
            target_label = self._audio_format_label(target_format)
            warning = self._selected_lossy_to_lossless_warning(target_format, paths=[p for p in self.files if os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS])
            return (
                "Extração de áudio",
                f"Modo ativo: extrair áudio do lote para {target_label}.",
                warning,
            )

        if self._current_compression_mode_text() == "Avançado":
            if self.advanced_target_mode.currentText() == "Tamanho":
                target_mb = self.advanced_target_size_mb.value()
                return (
                    f"Alvo técnico: {target_mb} MB por vídeo",
                    "Modo avançado por tamanho.",
                    "",
                )

            bitrate = self.advanced_target_bitrate_kbps.value()
            return (
                f"Alvo técnico: {bitrate} kbps por vídeo",
                "Modo avançado por bitrate.",
                "",
            )

        strategy = self._selected_smart_strategy()
        slider_value = self._smart_slider_value()
        self.smart_intensity_value.setText(f"{slider_value:.1f}")
        display_strategy = {"Economia Máxima": "Menor tamanho", "Equilíbrio": "Equilíbrio", "Qualidade Prioritária": "Maior qualidade"}.get(strategy, strategy)
        primary = f"Perfil ativo: {display_strategy}"
        secondary = f"Modo estratégico • intensidade {slider_value:.1f}/10.0."
        note_parts = []
        if self.smart_audio_box.isVisible():
            note_parts.append({
                "KEEP_ALL": "Áudio: manter todas as trilhas.",
                "KEEP_DEFAULT_ONLY": "Áudio: manter apenas a trilha padrão.",
                "SELECTED_ONLY": f"Áudio: usar {self.smart_audio_track_picker.currentText() or 'uma trilha específica'}.",
            }[self._current_smart_audio_track_policy()])
            if self.smart_channel_intro.isVisible():
                note_parts.append("Canais: downmix para estéreo." if self.smart_audio_downmix.isChecked() else "Canais: manter originais.")
        return primary, secondary, " ".join(note_parts)

    def _update_estimate_preview(self):
        self.smart_audio_track_picker.setEnabled(self.smart_audio_select_specific.isChecked() and self.smart_audio_track_picker.count() > 0)
        primary, secondary, note = self._estimate_output_summary()
        self.estimate_primary.setText(primary)
        self.estimate_secondary.setText(secondary)
        self.estimate_note.setText(note)
        self.estimate_note.setVisible(bool(note))


    def _on_selection_changed(self,*args):

        indexes = self.table.selectionModel().selectedRows()
        self.remove_btn.setEnabled(len(indexes) > 0)
        self._load_settings_for_current_selection()

    # ------------------------------------------------

    def _is_audio_path(self, path: str) -> bool:
        return os.path.splitext(str(path or ""))[1].lower() in AUDIO_EXTENSIONS

    def _combo_value(self, combo: QComboBox) -> str:
        data = combo.currentData()
        if data not in (None, ""):
            return str(data)
        return str(combo.currentText() or "")

    def _combo_payloads(self, combo: QComboBox) -> list[str]:
        values: list[str] = []
        for index in range(combo.count()):
            data = combo.itemData(index)
            values.append(str(data) if data not in (None, "") else str(combo.itemText(index) or ""))
        return values

    def _set_combo_by_value(self, combo: QComboBox, value: str | None) -> bool:
        target = str(value or "").strip()
        if not target:
            return False
        index = combo.findData(target)
        if index < 0:
            index = combo.findText(target)
        if index < 0:
            index = combo.findText(target.upper())
        if index >= 0:
            combo.setCurrentIndex(index)
            return True
        return False

    def _current_audio_format(self) -> str:
        return audio_profile.normalize_output_format(self._combo_value(self.a_format))

    def _current_video_panel_audio_format(self) -> str:
        return audio_profile.normalize_output_format(self._combo_value(self.v_container))

    def _audio_format_label(self, output_format: str | None) -> str:
        return audio_profile.display_label_for_format(output_format)

    def _selected_paths(self) -> list[str]:
        if not hasattr(self, "table") or self.table.selectionModel() is None:
            return []
        paths = []
        for index in self.table.selectionModel().selectedRows():
            try:
                path = self.model.files[index.row()]
            except Exception:
                continue
            if path in self.files:
                paths.append(path)
        return paths

    def _default_settings_for_path(self, path: str) -> dict:
        if self._is_audio_path(path):
            return {
                "audio_mode": self.v_mode_audio.currentText(),
                "audio_format": self._current_audio_format(),
            }
        video_mode = self.v_mode_video.currentText()
        extracting_audio = video_mode == "Extrair áudio"
        selected_audio_format = self._current_video_panel_audio_format() if extracting_audio else self._current_audio_format()
        return {
            "video_mode": video_mode,
            "video_format": "" if extracting_audio else self.v_container.currentText(),
            "audio_format": selected_audio_format,
            "compression_mode_text": self._current_compression_mode_text(),
            "advanced_target_mode": self.advanced_target_mode.currentText(),
            "advanced_target_size_mb": self.advanced_target_size_mb.value(),
            "advanced_target_bitrate_kbps": self.advanced_target_bitrate_kbps.value(),
        }

    def _settings_for_path(self, path: str) -> dict:
        settings = dict(self._default_settings_for_path(path))
        settings.update(self._file_import_settings.get(path, {}))
        return settings

    def _on_import_control_changed(self, *args):
        if self._loading_file_import_settings:
            return
        selected = self._selected_paths()
        if not selected:
            return
        for path in selected:
            if self._is_audio_path(path):
                self._file_import_settings[path] = {
                    **self._file_import_settings.get(path, {}),
                    "audio_mode": self.v_mode_audio.currentText(),
                    "audio_format": self._current_audio_format(),
                }
            else:
                video_mode = self.v_mode_video.currentText()
                extracting_audio = video_mode == "Extrair áudio"
                selected_audio_format = self._current_video_panel_audio_format() if extracting_audio else self._current_audio_format()
                self._file_import_settings[path] = {
                    **self._file_import_settings.get(path, {}),
                    "video_mode": video_mode,
                    "video_format": "" if extracting_audio else self.v_container.currentText(),
                    "audio_format": selected_audio_format,
                    "compression_mode_text": self._current_compression_mode_text(),
                    "advanced_target_mode": self.advanced_target_mode.currentText(),
                    "advanced_target_size_mb": self.advanced_target_size_mb.value(),
                    "advanced_target_bitrate_kbps": self.advanced_target_bitrate_kbps.value(),
                }
        self._update_estimate_preview()

    def _load_settings_for_current_selection(self):
        selected = self._selected_paths()
        if not selected:
            return
        path = selected[0]
        settings = self._settings_for_path(path)
        self._loading_file_import_settings = True
        try:
            if self._is_audio_path(path):
                audio_mode = str(settings.get("audio_mode") or self.v_mode_audio.currentText())
                index = self.v_mode_audio.findText(audio_mode)
                if index >= 0:
                    self.v_mode_audio.setCurrentIndex(index)
                audio_format = audio_profile.normalize_output_format(settings.get("audio_format") or self._current_audio_format())
                self._set_combo_by_value(self.a_format, audio_format)
            else:
                video_mode = str(settings.get("video_mode") or self.v_mode_video.currentText())
                index = self.v_mode_video.findText(video_mode)
                if index >= 0:
                    self.v_mode_video.setCurrentIndex(index)
                self._update_media_controls()
                if video_mode == "Extrair áudio":
                    audio_format = audio_profile.normalize_output_format(settings.get("audio_format") or self._current_video_panel_audio_format())
                    self._set_combo_by_value(self.v_container, audio_format)
                else:
                    video_format = str(settings.get("video_format") or self.v_container.currentText()).upper()
                    self._set_combo_by_value(self.v_container, video_format)
        finally:
            self._loading_file_import_settings = False
        self._update_media_controls()
        self._update_estimate_preview()

    # ------------------------------------------------

    def _remove_selected(self):

        rows = sorted(
            [i.row() for i in self.table.selectionModel().selectedRows()],
            reverse=True
        )

        for r in rows:
            if r < len(self.files):
                removed = self.files.pop(r)
                self._file_import_settings.pop(removed, None)

        self._refresh_table()

    # ------------------------------------------------

    def _add_paths(self, paths):

        added = False

        for p in paths:

            ext = os.path.splitext(p)[1].lower()

            if ext not in SUPPORTED_EXTENSIONS:
                continue

            if p not in self.files:
                self.files.append(p)
                added = True

        if not added:
            return

        self.files.sort(key=natural_key)

        self._refresh_table()

    # ------------------------------------------------

    def _add_files(self):

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

        paths, _ = QFileDialog.getOpenFileNames(
            self, "Adicionar arquivos", "", media_file_filter
        )

        if not paths:
            return

        self._add_paths(paths)

    # ------------------------------------------------

    def _refresh_table(self):

        self.model = FileTableModel(self.files)
        self.table.setModel(self.model)

        self.table.sortByColumn(0, Qt.AscendingOrder)

        sel_model = self.table.selectionModel()
        sel_model.selectionChanged.connect(self._on_selection_changed)

        self.update_header()
        self._apply_theme_styles()
        self._update_drop_hint()
        self.add_btn.setEnabled(len(self.files) > 0)
        self._update_media_controls()
        self._update_profile_controls()
        self._update_estimate_preview()
        self._schedule_default_action_focus()

    # ------------------------------------------------

    def _schedule_default_action_focus(self):
        if not self.add_btn.isEnabled():
            return
        self.add_btn.setDefault(True)
        self.add_btn.setAutoDefault(True)
        QTimer.singleShot(0, lambda: self.add_btn.setFocus(Qt.OtherFocusReason))

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_default_action_focus()

    # ------------------------------------------------

    def _update_drop_hint(self):
        if len(self.files) == 0:
            self.drop_hint.show()
            self.drop_hint.setGeometry(self.table.viewport().rect())
        else:
            self.drop_hint.hide()

    def _set_form_row_visible(self, form, field, visible):
        label = form.labelForField(field)
        if label is not None:
            label.setVisible(visible)
        field.setVisible(visible)

    def _set_video_output_format_options(self, options, preferred=None):
        current = self._combo_value(self.v_container)
        wanted = [str(option) for option in (options or [])]
        if self._combo_payloads(self.v_container) == wanted:
            return
        self.v_container.blockSignals(True)
        self.v_container.clear()
        for option in wanted:
            if audio_profile.normalize_output_format(option) == option.lower() and option.lower() in audio_profile.AUDIO_FORMAT_OPTIONS:
                self.v_container.addItem(audio_profile.display_label_for_format(option), option.lower())
            else:
                self.v_container.addItem(option)
        preferred_value = str(preferred or "").strip()
        target = preferred_value if preferred_value in wanted else (current if current in wanted else (wanted[0] if wanted else ""))
        if target:
            self._set_combo_by_value(self.v_container, target)
        self.v_container.blockSignals(False)

    def _update_media_controls(self):
        video_mode = self.v_mode_video.currentText()
        extracting_audio = video_mode == "Extrair áudio"
        if extracting_audio:
            self._set_video_output_format_options(self._audio_extract_format_options, preferred=self._current_audio_format())
        else:
            self._set_video_output_format_options(self._video_container_options)
        self._set_form_row_visible(self.video_form, self.v_container, True)

        audio_mode = self.v_mode_audio.currentText()
        show_audio_format = audio_mode == "Converter"
        self._set_form_row_visible(self.audio_form, self.a_format, show_audio_format)
        self._update_estimate_preview()

    def _has_video_files(self):
        for path in self.files:
            if os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS:
                return True
        return False

    def _update_profile_controls(self):
        has_video = self._has_video_files()
        self.profile_box.setVisible(False)
        self.profile_empty_hint.setVisible(False)
        self.profile_intro.setVisible(False)
        self.profile_mode_stack.setVisible(False)
        for button in self.compression_mode_buttons.values():
            button.setEnabled(has_video)

        if not has_video:
            self.profile_mode_stack.setCurrentWidget(self.smart_panel)
            self.profile_intro.setText("")
            return

        is_advanced = self._current_compression_mode_text() == "Avançado"
        self.profile_mode_stack.setCurrentWidget(self.advanced_panel if is_advanced else self.smart_panel)

        if is_advanced:
            self.profile_intro.setText("Defina um alvo técnico específico para o lote de vídeo.")
        else:
            self.profile_intro.setText("Defina a relação entre qualidade e tamanho.")
            self._refresh_smart_audio_context()

        target_is_size = self.advanced_target_mode.currentText() == "Tamanho"
        self._set_form_row_visible(self.advanced_panel.layout(), self.advanced_target_size_mb, target_is_size)
        self._set_form_row_visible(self.advanced_panel.layout(), self.advanced_target_bitrate_kbps, not target_is_size)
        self._update_estimate_preview()


    def _enqueue(self):

        video_mode = self.v_mode_video.currentText()
        extract_video_audio = self._has_video_files() and video_mode == "Extrair áudio"
        compression_mode = "audio" if (not self._has_video_files() or extract_video_audio) else "smart"
        advanced_target_mode = "size"
        if self._has_video_files() and not extract_video_audio:
            compression_mode = "advanced" if self._current_compression_mode_text() == "Avançado" else "smart"
            advanced_target_mode = "bitrate" if self.advanced_target_mode.currentText() == "Bitrate" else "size"

        selected_audio_format = self._current_video_panel_audio_format() if extract_video_audio else self._current_audio_format()

        def _payload_for_path(path: str) -> dict:
            settings = self._settings_for_path(path)
            if self._is_audio_path(path):
                audio_format = audio_profile.normalize_output_format(settings.get("audio_format") or self._current_audio_format())
                return {
                    "files": [path],
                    "video_mode": "",
                    "video_format": "",
                    "audio_mode": settings.get("audio_mode") or self.v_mode_audio.currentText(),
                    "audio_format": audio_format,
                    "audio_output_format": audio_format,
                    "audio_output_extension": audio_profile.extension_for_format(audio_format),
                    "compression_mode": "audio",
                }

            path_video_mode = str(settings.get("video_mode") or video_mode)
            path_extract_audio = path_video_mode == "Extrair áudio"
            path_audio_format = audio_profile.normalize_output_format(settings.get("audio_format") or selected_audio_format)
            path_video_format = "" if path_extract_audio else str(settings.get("video_format") or self.v_container.currentText()).strip().upper()
            path_compression_mode_text = str(settings.get("compression_mode_text") or self._current_compression_mode_text())
            path_compression_mode = "audio" if path_extract_audio else ("advanced" if path_compression_mode_text == "Avançado" else "smart")
            path_advanced_target_mode = "bitrate" if str(settings.get("advanced_target_mode") or self.advanced_target_mode.currentText()) == "Bitrate" else "size"
            return {
                "files": [path],
                "video_mode": path_video_mode,
                "video_format": path_video_format,
                "video_output_format": path_video_format,
                "video_output_extension": ("." + path_video_format.lower()) if path_video_format else "",
                "audio_mode": self.v_mode_audio.currentText(),
                "audio_format": path_audio_format,
                "audio_output_format": path_audio_format,
                "audio_output_extension": audio_profile.extension_for_format(path_audio_format),
                "compression_mode": path_compression_mode,
                "control_mode": "STRATEGY" if path_compression_mode == "smart" else None,
                "strategy_type": self._selected_smart_strategy(),
                "slider_value": self._smart_slider_value(),
                "smart_strategy": self._selected_smart_strategy(),
                "smart_intensity": self._smart_effective_intensity(),
                "audio_track_policy": self._current_smart_audio_track_policy(),
                "selected_track_id": self.smart_audio_track_picker.currentData() if self._current_smart_audio_track_policy() == "SELECTED_ONLY" else None,
                "audio_channel_policy": self._current_smart_audio_channel_policy(),
                "advanced_target_mode": path_advanced_target_mode,
                "advanced_target_size_mb": settings.get("advanced_target_size_mb", self.advanced_target_size_mb.value()),
                "advanced_target_bitrate_kbps": settings.get("advanced_target_bitrate_kbps", self.advanced_target_bitrate_kbps.value()),
            }

        per_file_payloads = {path: _payload_for_path(path) for path in self.files}

        self.result_payload = {
            "files": list(self.files),
            "per_file_payloads": per_file_payloads,
            "video_mode": video_mode,
            "video_format": "" if extract_video_audio else self.v_container.currentText(),
            "video_output_format": "" if extract_video_audio else self.v_container.currentText(),
            "video_output_extension": "" if extract_video_audio else "." + str(self.v_container.currentText() or "").strip().lower(),
            "audio_mode": self.v_mode_audio.currentText(),
            "audio_format": selected_audio_format,
            "audio_output_format": selected_audio_format,
            "audio_output_extension": audio_profile.extension_for_format(selected_audio_format),
            "compression_mode": compression_mode,
            "control_mode": "STRATEGY" if compression_mode == "smart" else None,
            "strategy_type": self._selected_smart_strategy(),
            "slider_value": self._smart_slider_value(),
            "smart_strategy": self._selected_smart_strategy(),
            "smart_intensity": self._smart_effective_intensity(),
            "audio_track_policy": self._current_smart_audio_track_policy(),
            "selected_track_id": self.smart_audio_track_picker.currentData() if self._current_smart_audio_track_policy() == "SELECTED_ONLY" else None,
            "audio_channel_policy": self._current_smart_audio_channel_policy(),
            "advanced_target_mode": advanced_target_mode,
            "advanced_target_size_mb": self.advanced_target_size_mb.value(),
            "advanced_target_bitrate_kbps": self.advanced_target_bitrate_kbps.value(),
        }

        self.accept()
