from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor

from app.interaction_model.event_bridge import event_bridge
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

from app.ui.theme_tokens import build_theme_tokens, build_button_stylesheet

BTN_WIDTH = 180


class SelectionActionBarWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SelectionActionBarWidget")
        self.setFrameShape(QFrame.StyledPanel)
        self._applying_styles = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(12)

        self.btn_config = QPushButton("Configurar")
        self.btn_enqueue = QPushButton("Enfileirar")
        self.btn_select_all = QPushButton("Selecionar tudo")
        self.btn_delete = QPushButton("Excluir")
        self.btn_clear_selection = QPushButton("Cancelar seleção")

        self._buttons = [
            self.btn_config,
            self.btn_enqueue,
            self.btn_select_all,
            self.btn_delete,
            self.btn_clear_selection,
        ]

        for b in self._buttons:
            b.setFixedWidth(BTN_WIDTH)

        layout.addWidget(self.btn_config)
        layout.addWidget(self.btn_enqueue)
        layout.addWidget(self.btn_select_all)
        layout.addWidget(self.btn_delete)
        layout.addWidget(self.btn_clear_selection)
        layout.addStretch()

        self._apply_styles()

        # hidden until selection exists
        self.setVisible(False)

    def _apply_styles(self):
        # When this widget is embedded as the file-list title bar, its metrics
        # and final button styles are owned by FileListContainer. Reapplying the
        # generic context-bar stylesheet here during PaletteChange/ThemeChange
        # shrinks the right toolbar buttons in manual dark mode.
        if self.objectName() == "FileListTitleBar":
            return
        if self._applying_styles:
            return
        self._applying_styles = True
        try:
            tokens = build_theme_tokens(self.palette())
            button_stylesheet = build_button_stylesheet(
                tokens,
                min_height=30,
                border_radius=4,
                horizontal_padding=10,
            )
            analysis_warning_fg = tokens.status_analyzing.name(QColor.NameFormat.HexArgb)
            analysis_warning_border = tokens.status_analyzing.name(QColor.NameFormat.HexArgb)
            analysis_warning_bg = tokens.surface_panel.name(QColor.NameFormat.HexArgb)
            button_stylesheet += f"""
                QPushButton[analysisBlocked="true"]:disabled {{
                    background: {analysis_warning_bg};
                    color: {analysis_warning_fg};
                    border: 1px solid {analysis_warning_border};
                    font-weight: 600;
                }}
            """
            for button in self._buttons:
                button.setStyleSheet(button_stylesheet)
        finally:
            self._applying_styles = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self._apply_styles()

    def mousePressEvent(self, event):
        event_bridge.emit("clear_selection_requested", None)
        super().mousePressEvent(event)


# backward compatibility alias
ContextBarWidget = SelectionActionBarWidget
