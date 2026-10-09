from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer

from app.ui.theme_tokens import build_theme_tokens, color_to_css
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel


class ToastManager:

    def __init__(self, parent):

        self.parent = parent

        self.label = QLabel(parent)
        self.label.hide()

        self.label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.label.setAlignment(Qt.AlignCenter)
        self._apply_styles()

        self.opacity = QGraphicsOpacityEffect()
        self.label.setGraphicsEffect(self.opacity)

        self.fade_in = QPropertyAnimation(self.opacity, b"opacity")
        self.fade_in.setDuration(150)
        self.fade_in.setStartValue(0)
        self.fade_in.setEndValue(1)

        self.fade_out = QPropertyAnimation(self.opacity, b"opacity")
        self.fade_out.setDuration(200)
        self.fade_out.setStartValue(1)
        self.fade_out.setEndValue(0)

        self.slide = QPropertyAnimation(self.label, b"pos")
        self.slide.setDuration(180)
        self.slide.setEasingCurve(QEasingCurve.OutCubic)

        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._start_hide)

        self.fade_out.finished.connect(self.label.hide)

    def _apply_styles(self, tone: str = "default"):
        tokens = build_theme_tokens(self.parent.palette())
        bg = tokens.surface_toast
        text = tokens.text_toast
        border = tokens.border_toast
        font_weight = "normal"

        if tone == "warning":
            # Use existing semantic analysis/accent tokens so the warning remains
            # noticeable in both light and dark themes without hardcoded colors.
            bg = tokens.status_analyzing
            text = tokens.text_on_dark_surface
            border = tokens.button_hover_border
            font_weight = "600"

        self.label.setStyleSheet(
            f"""
            QLabel {{
                background: {color_to_css(bg)};
                color: {color_to_css(text)};
                border: 1px solid {color_to_css(border)};
                border-radius: 6px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: {font_weight};
            }}
            """
        )

    def show(self, text, duration=2000, tone: str = "default"):

        self._apply_styles(tone)
        self.label.setText(text)
        self.label.adjustSize()

        parent_rect = self.parent.rect()

        x = (parent_rect.width() - self.label.width()) // 2
        y = parent_rect.height() // 2 - self.label.height()

        start_pos = QPoint(x, y + 15)
        end_pos = QPoint(x, y)

        self.label.move(start_pos)

        self.slide.stop()
        self.fade_in.stop()
        self.fade_out.stop()

        self.slide.setStartValue(start_pos)
        self.slide.setEndValue(end_pos)

        self.opacity.setOpacity(0)

        self.label.show()
        self.label.raise_()

        self.slide.start()
        self.fade_in.start()

        self.timer.start(duration)

    def _start_hide(self):
        self.fade_out.start()
