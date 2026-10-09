from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QSizePolicy

from app.interaction_model.event_bridge import event_bridge
from app.ui.theme_tokens import build_theme_tokens, build_button_stylesheet


class ExecutionFooterWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("ExecutionFooterWidget")
        self.setFrameShape(QFrame.NoFrame)
        self._applying_styles = False

        self._apply_styles()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        self.btn_compress = QPushButton("Processar")
        self.btn_compress_all = QPushButton("Processar Todos")
        self.btn_cancel = QPushButton("Cancelar Selecionados")
        self.btn_cancel_all = QPushButton("Cancelar Todos")
        self.btn_cancel.hide()
        self.btn_cancel_all.hide()
        self.btn_clear_all = QPushButton("Limpar Tudo")
        self.btn_exit = QPushButton("Sair")
        self.btn_exit.setObjectName("ExecutionFooterExitButton")
        self.btn_exit.setFixedWidth(90)

        for b in [
            self.btn_compress,
            self.btn_compress_all,
            self.btn_cancel,
            self.btn_cancel_all,
            self.btn_clear_all,
            self.btn_exit,
        ]:
            b.setMinimumWidth(120)
            b.setFixedHeight(30)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setFocusPolicy(Qt.NoFocus)

        layout.addWidget(self.btn_compress)
        layout.addWidget(self.btn_compress_all)
        layout.addWidget(self.btn_cancel)
        layout.addWidget(self.btn_cancel_all)
        layout.addWidget(self.btn_clear_all)
        layout.addWidget(self.btn_exit)

        self.btn_clear_all.clicked.connect(self._clear_all)
        self.btn_exit.clicked.connect(self._request_shutdown)

    def _request_shutdown(self):
        event_bridge.emit("shutdown_requested", None)

    def _clear_all(self):
        event_bridge.emit("clear_all_requested", None)

    def set_processing_state(self, processing: bool):
        if processing:
            self.btn_compress.hide()
            self.btn_compress_all.hide()
            self.btn_cancel.show()
            self.btn_cancel_all.show()
        else:
            self.btn_cancel.hide()
            self.btn_cancel_all.hide()
            self.btn_compress.show()
            self.btn_compress_all.show()

    def _apply_styles(self):
        if self._applying_styles:
            return
        self._applying_styles = True
        try:
            tokens = build_theme_tokens(self.palette())
            stylesheet = build_button_stylesheet(tokens, min_height=30, border_radius=4, horizontal_padding=0)
            prefixed_stylesheet = stylesheet.replace("QPushButton", "#ExecutionFooterWidget QPushButton")
            self.setStyleSheet(prefixed_stylesheet)
        finally:
            self._applying_styles = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self._apply_styles()
