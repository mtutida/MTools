from PySide6.QtCore import QEvent, Qt

from app.interaction_model.event_bridge import event_bridge
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QProgressBar

from app.ui.theme_tokens import build_theme_tokens


class GlobalProgressWidget(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("GlobalProgressWidget")
        self.setFixedHeight(24)
        self._applying_styles = False

        self._status_text = ""

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximum(100)
        self.progress.setValue(0)

        self.status_label = QLabel("")
        self.status_label.setObjectName("GlobalProgressStatusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.status_label.setMinimumWidth(0)
        self.status_label.hide()

        layout.addWidget(self.progress, 0, 0)
        layout.addWidget(self.status_label, 0, 0)
        self._apply_styles()

    def _apply_styles(self):
        if self._applying_styles:
            return
        self._applying_styles = True
        try:
            tokens = build_theme_tokens(self.palette())
            track = tokens.progress_track.name(QColor.NameFormat.HexArgb)
            fill = tokens.progress_fill.name(QColor.NameFormat.HexArgb)
            # The status label is painted over the active progress bar/track.
            # Use the semantic light-on-dark token instead of text_primary,
            # otherwise light theme renders dark text on a dark progress surface.
            label_color = QColor(tokens.text_on_dark_surface)
            label_color.setAlpha(245)
            label_fg = label_color.name(QColor.NameFormat.HexArgb)

            self.setStyleSheet(
                f"""
                QFrame#GlobalProgressWidget {{
                    background: {track};
                }}

                QProgressBar {{
                    border: none;
                    background: transparent;
                }}

                QProgressBar::chunk {{
                    background: {fill};
                }}

                QLabel#GlobalProgressStatusLabel {{
                    background: transparent;
                    color: {label_fg};
                    padding-left: 10px;
                    padding-right: 10px;
                }}
                """
            )
            self._refresh_status_label()
        finally:
            self._applying_styles = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.ThemeChange):
            self._apply_styles()

    def mousePressEvent(self, event):
        event_bridge.emit("clear_selection_requested", None)
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_status_label()

    def _refresh_status_label(self):
        text = str(self._status_text or "").strip()
        if not text:
            self.status_label.clear()
            self.status_label.hide()
            return

        available_width = max(20, self.status_label.width() - 20)
        metrics = QFontMetrics(self.status_label.font())
        self.status_label.setText(metrics.elidedText(text, Qt.ElideMiddle, available_width))
        self.status_label.setToolTip(text)
        self.status_label.show()

    def set_idle(self):
        self.progress.setValue(0)
        self._status_text = ""
        self._refresh_status_label()

    def set_progress(self, value, maximum, status_text=None):
        self.progress.setMaximum(maximum)
        self.progress.setValue(value)
        self._status_text = str(status_text or "").strip()
        self._refresh_status_label()
