from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from app.ui.theme_tokens import ThemeTokens, build_semantic_tooltip_stylesheet, build_theme_tokens


class SemanticTooltip(QFrame):
    """Custom tooltip for card actions.

    This avoids the native QToolTip rendering path inside QListView viewport().
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint | Qt.BypassGraphicsProxyWidget)
        self.setObjectName('SemanticTooltip')
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._label = QLabel(self)
        self._label.setWordWrap(False)
        self._label.setTextFormat(Qt.PlainText)
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(0)
        layout.addWidget(self._label)

        self.apply_theme()
        self.hide()

    def apply_theme(self, tokens: ThemeTokens | None = None):
        tokens = tokens or build_theme_tokens(self.palette())
        self.setStyleSheet(build_semantic_tooltip_stylesheet(tokens))

    def show_for(self, anchor: QWidget, global_pos: QPoint, text: str):
        if not text:
            self.hide_tooltip()
            return
        self._label.setText(text)
        self._label.adjustSize()
        self.adjustSize()

        screen = QGuiApplication.screenAt(global_pos)
        if screen is None and anchor.windowHandle() is not None:
            screen = anchor.windowHandle().screen()
        available = screen.availableGeometry() if screen is not None else QGuiApplication.primaryScreen().availableGeometry()

        x = global_pos.x() + 14
        y = global_pos.y() + 18
        if x + self.width() > available.right() - 8:
            x = max(available.left() + 8, global_pos.x() - self.width() - 14)
        if y + self.height() > available.bottom() - 8:
            y = max(available.top() + 8, global_pos.y() - self.height() - 18)

        self.move(x, y)
        self.show()
        self.raise_()

    def hide_tooltip(self):
        self.hide()
