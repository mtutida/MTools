from PySide6.QtCore import QEventLoop, QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from app.core.app_version import APP_NAME, APP_VERSION
from app.ui.window_icon import app_window_icon


class StartupSplash(QWidget):
    """Small startup feedback window shown while the main UI is being built.

    The progress bar uses controlled startup milestones. It is not a strict
    measurement of real work, but it prevents the splash from disappearing
    with the bar visually stuck halfway through startup.
    """

    def __init__(self):
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("StartupSplash")
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_window_icon())
        self.setFixedSize(360, 138)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(10)

        self.title_label = QLabel(f"{APP_NAME}  {APP_VERSION}")
        title_font = QFont("Segoe UI", 12)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.message_label = QLabel("Carregando...")
        self.message_label.setFont(QFont("Segoe UI", 9))
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(12)

        layout.addWidget(self.title_label)
        layout.addWidget(self.message_label)
        layout.addSpacing(2)
        layout.addWidget(self.progress)

        self.setStyleSheet(
            "#StartupSplash {"
            " background: palette(window);"
            " border: 1px solid palette(mid);"
            "}"
            "QLabel { color: palette(window-text); }"
            "QProgressBar {"
            " border: 1px solid palette(mid);"
            " border-radius: 3px;"
            " background: palette(base);"
            "}"
            "QProgressBar::chunk {"
            " border-radius: 2px;"
            " background: palette(highlight);"
            "}"
        )

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)

    def set_progress(self, value: int) -> None:
        self.progress.setValue(max(0, min(100, int(value))))

    def set_step(self, message: str, value: int) -> None:
        self.set_message(message)
        self.set_progress(value)

    def finish(self, qt_app, message: str = f"Abrindo {APP_NAME}...", pause_ms: int = 180) -> None:
        self.set_step(message, 100)
        qt_app.processEvents()
        pause_ms = max(0, int(pause_ms))
        if pause_ms:
            loop = QEventLoop(self)
            QTimer.singleShot(pause_ms, loop.quit)
            loop.exec()

    def show_centered(self) -> None:
        screen = self.screen()
        if screen is None:
            from PySide6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            self.move(
                available.center().x() - self.width() // 2,
                available.center().y() - self.height() // 2,
            )
        self.show()
