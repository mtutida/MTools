import logging
import os

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

_LOGGER = logging.getLogger(__name__)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    QT_MULTIMEDIA_AVAILABLE = True
except Exception:
    _LOGGER.debug("Qt multimedia modules are unavailable; media preview will use fallback behavior.", exc_info=True)
    QAudioOutput = None
    QMediaPlayer = None
    QVideoWidget = None
    QT_MULTIMEDIA_AVAILABLE = False


class _NativeStderrSilencer:
    """Temporarily silence native FFmpeg stderr chatter during media probing."""

    def __enter__(self):
        self._devnull = None
        self._saved_stderr_fd = None
        try:
            self._saved_stderr_fd = os.dup(2)
            self._devnull = open(os.devnull, "w")
            os.dup2(self._devnull.fileno(), 2)
        except Exception:
            self._cleanup()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._cleanup()
        return False

    def _cleanup(self):
        try:
            if self._saved_stderr_fd is not None:
                os.dup2(self._saved_stderr_fd, 2)
        except Exception:
            _LOGGER.debug("Failed to silence native stderr during media preview probing.", exc_info=True)
        try:
            if self._saved_stderr_fd is not None:
                os.close(self._saved_stderr_fd)
        except Exception:
            pass
        try:
            if self._devnull is not None:
                self._devnull.close()
        except Exception:
            pass
        self._saved_stderr_fd = None
        self._devnull = None


class MediaPreviewOverlayButton(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_playing = False
        self._is_dark = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(76, 76)
        self.setToolTip("Reproduzir")

    def sizeHint(self):
        return QSize(76, 76)

    def set_theme_variant(self, is_dark: bool):
        self._is_dark = bool(is_dark)
        self.update()

    def set_playing_state(self, is_playing: bool):
        self._is_playing = bool(is_playing)
        self.setToolTip("Pausar" if self._is_playing else "Reproduzir")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = QRectF(1.5, 1.5, self.width() - 3.0, self.height() - 3.0)
        if self._is_dark:
            fill = QColor(15, 23, 42, 178)
            border = QColor(248, 250, 252, 110)
        else:
            fill = QColor(15, 23, 42, 158)
            border = QColor(255, 255, 255, 130)

        if self.isDown():
            fill = fill.darker(120)
            border = border.lighter(110)
        elif self.underMouse():
            fill = fill.lighter(112)
            border = border.lighter(115)

        painter.setPen(QPen(border, 1.2))
        painter.setBrush(fill)
        painter.drawEllipse(rect)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 248))

        if self._is_playing:
            bar_width = 8.0
            bar_height = 24.0
            gap = 8.0
            total_width = (bar_width * 2.0) + gap
            start_x = (self.width() - total_width) / 2.0
            start_y = (self.height() - bar_height) / 2.0
            painter.drawRoundedRect(QRectF(start_x, start_y, bar_width, bar_height), 2.5, 2.5)
            painter.drawRoundedRect(QRectF(start_x + bar_width + gap, start_y, bar_width, bar_height), 2.5, 2.5)
        else:
            path = QPainterPath()
            path.moveTo(self.width() / 2.0 - 8.0, self.height() / 2.0 - 14.0)
            path.lineTo(self.width() / 2.0 - 8.0, self.height() / 2.0 + 14.0)
            path.lineTo(self.width() / 2.0 + 16.0, self.height() / 2.0)
            path.closeSubpath()
            painter.drawPath(path)


class MediaPreviewDialog(QDialog):
    """Internal media preview dialog with Qt Multimedia and player fallback."""

    fallback_requested = Signal(str)

    def __init__(self, media_path: str, parent=None, *, resume_position_ms: int = 0, resume_playing: bool = False, playback_rate: float = 1.0, volume_percent: int = 65):
        super().__init__(parent)
        self.media_path = os.path.normpath(media_path)
        self._duration_ms = 0
        self._seeking = False
        self._fallback_emitted = False
        self._media_ready = False
        self._play_requested_when_ready = False
        self._media_prepare_timeout_ms = 8000
        self._media_prepare_timer = QTimer(self)
        self._media_prepare_timer.setSingleShot(True)
        self._media_prepare_timer.timeout.connect(self._on_media_prepare_timeout)
        self._resume_position_ms = max(0, int(resume_position_ms or 0))
        self._resume_playing = bool(resume_playing)
        self._resume_applied = False
        self._playback_rate = self._normalize_playback_rate(playback_rate)
        self._volume_percent = self._normalize_volume_percent(volume_percent)
        self._last_audible_volume_percent = self._volume_percent if self._volume_percent > 0 else 65
        self._is_fullscreen = False
        self._center_overlay_auto_hide_ms = 1800
        self._center_overlay_hide_timer = QTimer(self)
        self._center_overlay_hide_timer.setSingleShot(True)
        self._center_overlay_hide_timer.timeout.connect(self._hide_center_play_overlay_after_delay)

        self.setWindowTitle("Preview da mídia")
        self.setModal(False)
        self.setMinimumSize(760, 480)
        self.resize(900, 560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._build_ui()

        if not QT_MULTIMEDIA_AVAILABLE:
            self._set_status("Preview interno indisponível. Abrindo no player padrão...")
            self._emit_fallback_once()
            return

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.video_widget = QVideoWidget(self.video_container)
        self.video_widget.installEventFilter(self)

        video_layout = QVBoxLayout(self.video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.addWidget(self.video_widget)

        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        self.audio_output.setVolume(self._volume_percent / 100.0)
        self.volume_slider.setValue(self._volume_percent)
        self._update_mute_button()

        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.errorOccurred.connect(self._on_player_error)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)

        with _NativeStderrSilencer():
            self.player.setSource(QUrl.fromLocalFile(self.media_path))
        self._apply_playback_rate()
        self._set_play_button_label(False)
        self._set_center_play_overlay_icon(False)
        self._set_center_play_overlay_visible(not self._resume_playing)
        if self._resume_position_ms <= 0 and not self._resume_playing:
            self._resume_applied = True

    def _build_ui(self):
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(14, 14, 14, 14)
        self.root_layout.setSpacing(10)

        self.header = QFrame(self)
        self.header_layout = QHBoxLayout(self.header)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(8)

        file_name = os.path.basename(self.media_path) or "Mídia"
        self.title_label = QLabel(file_name, self.header)
        self.title_label.setObjectName("MediaPreviewTitle")
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self.header_layout.addWidget(self.title_label, 1)

        self.video_container = QFrame(self)
        self.video_container.setObjectName("MediaPreviewVideoContainer")
        self.video_container.setMinimumHeight(320)
        self.video_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_container.installEventFilter(self)

        self.center_play_overlay = QWidget(self, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.center_play_overlay.setObjectName("MediaPreviewCenterPlayOverlay")
        self.center_play_overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.center_play_overlay.setFixedSize(82, 82)
        center_play_layout = QVBoxLayout(self.center_play_overlay)
        center_play_layout.setContentsMargins(3, 3, 3, 3)
        center_play_layout.setSpacing(0)
        self.center_play_button = MediaPreviewOverlayButton(self.center_play_overlay)
        self.center_play_button.setObjectName("MediaPreviewCenterPlayButton")
        self.center_play_button.clicked.connect(self._toggle_playback)
        center_play_layout.addWidget(self.center_play_button, 0, Qt.AlignmentFlag.AlignCenter)
        self._set_center_play_overlay_icon(False)
        self.center_play_overlay.hide()

        self.timeline_panel = QFrame(self)
        self.timeline_panel.setObjectName("MediaPreviewTimelinePanel")
        self.timeline_layout = QHBoxLayout(self.timeline_panel)
        self.timeline_layout.setContentsMargins(0, 0, 0, 0)
        self.timeline_layout.setSpacing(0)

        self.position_slider = QSlider(Qt.Orientation.Horizontal, self.timeline_panel)
        self.position_slider.setObjectName("MediaPreviewPositionSlider")
        # The handle extends 5px above and below the groove in the stylesheet.
        # Reserve that vertical space so the native layout never clips it.
        self.position_slider.setMinimumHeight(24)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self._on_seek_start)
        self.position_slider.sliderReleased.connect(self._on_seek_end)
        self.position_slider.sliderMoved.connect(self._on_seek_moved)
        self.timeline_layout.addWidget(self.position_slider, 1)

        self.controls = QFrame(self)
        self.controls.setObjectName("MediaPreviewControls")
        self.controls_layout = QHBoxLayout(self.controls)
        self.controls_layout.setContentsMargins(8, 8, 8, 8)
        self.controls_layout.setSpacing(8)

        self.play_button = QPushButton(self.controls)
        self.play_button.setObjectName("MediaPreviewPrimaryButton")
        self.play_button.setFixedSize(78, 38)
        self.play_button.setIconSize(QSize(18, 18))
        self.play_button.setToolTip("Reproduzir/Pausar (Espaço)")
        self.play_button.clicked.connect(self._toggle_playback)

        self.rewind_button = QPushButton("-10s", self.controls)
        self.rewind_button.setObjectName("MediaPreviewControlButton")
        self.rewind_button.setFixedSize(58, 38)
        self.rewind_button.setToolTip("Voltar 10 segundos (←)")
        self.rewind_button.clicked.connect(lambda: self._seek_relative(-10000))

        self.forward_button = QPushButton("+10s", self.controls)
        self.forward_button.setObjectName("MediaPreviewControlButton")
        self.forward_button.setFixedSize(58, 38)
        self.forward_button.setToolTip("Avançar 10 segundos (→)")
        self.forward_button.clicked.connect(lambda: self._seek_relative(10000))

        self.time_label = QLabel("00:00 / 00:00", self.controls)
        self.time_label.setObjectName("MediaPreviewTimeLabel")
        self.time_label.setMinimumWidth(122)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.mute_button = QPushButton("Som", self.controls)
        self.mute_button.setObjectName("MediaPreviewControlButton")
        self.mute_button.setFixedSize(66, 38)
        self.mute_button.setToolTip("Ativar/desativar som (M)")
        self.mute_button.clicked.connect(self._toggle_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal, self.controls)
        self.volume_slider.setObjectName("MediaPreviewVolumeSlider")
        self.volume_slider.setMinimumHeight(24)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(65)
        self.volume_slider.setMaximumWidth(104)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)

        self.speed_combo = QComboBox(self.controls)
        self.speed_combo.setObjectName("MediaPreviewSpeedCombo")
        self.speed_combo.setFixedSize(76, 38)
        self.speed_combo.setToolTip("Velocidade de reprodução")
        for label, rate in (
            ("0.5x", 0.5),
            ("0.75x", 0.75),
            ("1x", 1.0),
            ("1.25x", 1.25),
            ("1.5x", 1.5),
            ("2x", 2.0),
            ("2.5x", 2.5),
            ("3x", 3.0),
        ):
            self.speed_combo.addItem(label, rate)
        self.speed_combo.currentIndexChanged.connect(self._on_playback_rate_changed)

        self.fullscreen_button = QPushButton("Tela cheia", self.controls)
        self.fullscreen_button.setObjectName("MediaPreviewControlButton")
        self.fullscreen_button.setFixedSize(92, 38)
        self.fullscreen_button.setToolTip("Alternar tela cheia (F)")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)

        self.controls_layout.addWidget(self.play_button, 0)
        self.controls_layout.addWidget(self.rewind_button, 0)
        self.controls_layout.addWidget(self.forward_button, 0)
        self.controls_layout.addWidget(self.time_label, 0)
        self.controls_layout.addStretch(1)
        self.controls_layout.addWidget(self.mute_button, 0)
        self.controls_layout.addWidget(self.volume_slider, 0)
        self.controls_layout.addWidget(self.speed_combo, 0)
        self.controls_layout.addWidget(self.fullscreen_button, 0)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("MediaPreviewStatus")
        self.status_label.setWordWrap(True)

        self.root_layout.addWidget(self.header, 0)
        self.root_layout.addWidget(self.video_container, 1)
        self.root_layout.addWidget(self.timeline_panel, 0)
        self.root_layout.addWidget(self.controls, 0)
        self.root_layout.addWidget(self.status_label, 0)

        self._apply_static_theme_stylesheet()

    def _uses_dark_palette(self) -> bool:
        try:
            return self.palette().window().color().lightness() < 128
        except Exception:
            return True

    def _apply_static_theme_stylesheet(self):
        # Keep this dialog deliberately isolated from ThemeTokens/repolish paths.
        # QVideoWidget/Qt Multimedia is sensitive to dynamic style churn on some
        # Windows/codec combinations, so only choose a static stylesheet at
        # construction time.
        is_dark = self._uses_dark_palette()
        icons_dir = os.path.join(os.path.dirname(__file__), "icons")
        arrow_icon_path = os.path.join(
            icons_dir,
            "media_preview_chevron_down_light.svg" if is_dark else "media_preview_chevron_down_dark.svg",
        ).replace(chr(92), "/")
        if is_dark:
            stylesheet = """
            QDialog {
                background: #111827;
                color: #f9fafb;
            }
            QLabel {
                color: #f9fafb;
            }
            QLabel#MediaPreviewTitle {
                font-size: 14px;
                font-weight: 700;
                color: #f9fafb;
            }
            QFrame#MediaPreviewVideoContainer {
                background: #05070b;
                border: 1px solid #374151;
                border-radius: 0px;
            }
            QLabel#MediaPreviewStatus {
                color: #cbd5e1;
                font-size: 11px;
            }
            QFrame#MediaPreviewTimelinePanel {
                background: transparent;
            }
            QFrame#MediaPreviewControls {
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 10px;
            }
            QLabel#MediaPreviewTimeLabel {
                color: #f9fafb;
                font-weight: 700;
            }
            QPushButton {
                background: #1f2937;
                color: #f9fafb;
                border: 1px solid #475569;
                border-radius: 8px;
                padding: 0 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #334155;
                border: 1px solid #60a5fa;
            }
            QPushButton:pressed {
                background: #111827;
            }
            QPushButton#MediaPreviewPrimaryButton {
                background: #1683f3;
                color: #ffffff;
                border: 1px solid #4aa3ff;
                border-radius: 10px;
                font-size: 17px;
                font-weight: 800;
                padding: 0;
            }
            QPushButton#MediaPreviewPrimaryButton:hover {
                background: #2f95ff;
                border: 1px solid #93c5fd;
            }
            QPushButton#MediaPreviewControlButton {
                font-size: 12px;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #374151;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                margin: -5px 0;
                background: #e5e7eb;
                border-radius: 7px;
            }
            QSlider#MediaPreviewPositionSlider::groove:horizontal {
                height: 6px;
                background: #475569;
                border-radius: 3px;
            }
            QComboBox#MediaPreviewSpeedCombo {
                background: #1f2937;
                color: #f9fafb;
                border: 1px solid #4b5563;
                border-radius: 6px;
                padding: 0 24px 0 10px;
            }
            QComboBox#MediaPreviewSpeedCombo:hover {
                background: #374151;
            }
            QComboBox#MediaPreviewSpeedCombo::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                border: none;
                width: 20px;
            }
            QComboBox#MediaPreviewSpeedCombo::down-arrow {
                image: url("__MEDIA_PREVIEW_SPEED_ARROW__");
                width: 10px;
                height: 10px;
            }
            """
        else:
            stylesheet = """
            QDialog {
                background: #f3f4f6;
                color: #111827;
            }
            QLabel {
                color: #111827;
            }
            QLabel#MediaPreviewTitle {
                font-size: 14px;
                font-weight: 700;
                color: #111827;
            }
            QFrame#MediaPreviewVideoContainer {
                background: #000000;
                border: 1px solid #cbd5e1;
                border-radius: 0px;
            }
            QLabel#MediaPreviewStatus {
                color: #475569;
                font-size: 11px;
            }
            QFrame#MediaPreviewTimelinePanel {
                background: transparent;
            }
            QFrame#MediaPreviewControls {
                background: #ffffff;
                border: 1px solid #d7dee8;
                border-radius: 10px;
            }
            QLabel#MediaPreviewTimeLabel {
                color: #111827;
                font-weight: 700;
            }
            QPushButton {
                background: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 0 10px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #eef6ff;
                border: 1px solid #4b9df8;
            }
            QPushButton:pressed {
                background: #dbeafe;
            }
            QPushButton#MediaPreviewPrimaryButton {
                background: #0b78e3;
                color: #ffffff;
                border: 1px solid #0a6ed1;
                border-radius: 10px;
                font-size: 17px;
                font-weight: 800;
                padding: 0;
            }
            QPushButton#MediaPreviewPrimaryButton:hover {
                background: #168bff;
                border: 1px solid #4b9df8;
            }
            QPushButton#MediaPreviewControlButton {
                font-size: 12px;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #cbd5e1;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                margin: -5px 0;
                background: #334155;
                border-radius: 7px;
            }
            QSlider#MediaPreviewPositionSlider::groove:horizontal {
                height: 6px;
                background: #94a3b8;
                border-radius: 3px;
            }
            QComboBox#MediaPreviewSpeedCombo {
                background: #ffffff;
                color: #111827;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 0 24px 0 10px;
            }
            QComboBox#MediaPreviewSpeedCombo:hover {
                background: #e5e7eb;
            }
            QComboBox#MediaPreviewSpeedCombo::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                border: none;
                width: 20px;
            }
            QComboBox#MediaPreviewSpeedCombo::down-arrow {
                image: url("__MEDIA_PREVIEW_SPEED_ARROW__");
                width: 10px;
                height: 10px;
            }
            """
        stylesheet = stylesheet.replace("__MEDIA_PREVIEW_SPEED_ARROW__", arrow_icon_path)
        self.setStyleSheet(stylesheet)
        if hasattr(self, "center_play_button"):
            self.center_play_button.set_theme_variant(is_dark)

    def _position_center_play_overlay(self):
        if not hasattr(self, "center_play_overlay"):
            return
        if not self.isVisible():
            return
        target = getattr(self, "video_widget", None) or self.video_container
        try:
            top_left = target.mapToGlobal(QPoint(0, 0))
            x = top_left.x() + max(0, (target.width() - self.center_play_overlay.width()) // 2)
            y = top_left.y() + max(0, (target.height() - self.center_play_overlay.height()) // 2)
            self.center_play_overlay.move(x, y)
        except Exception:
            return

    def _hide_center_play_overlay_after_delay(self):
        if not hasattr(self, "center_play_overlay"):
            return
        try:
            if self.is_playing():
                self.center_play_overlay.hide()
        except Exception:
            self.center_play_overlay.hide()

    def _set_center_play_overlay_visible(self, visible: bool, *, auto_hide: bool = False):
        if not hasattr(self, "center_play_overlay"):
            return
        if hasattr(self, "_center_overlay_hide_timer"):
            self._center_overlay_hide_timer.stop()
        if visible and self.isVisible():
            self._position_center_play_overlay()
            self.center_play_overlay.show()
            self.center_play_overlay.raise_()
            if auto_hide and hasattr(self, "_center_overlay_hide_timer"):
                self._center_overlay_hide_timer.start(self._center_overlay_auto_hide_ms)
        else:
            self.center_play_overlay.hide()

    def _set_center_play_overlay_icon(self, is_playing: bool):
        if not hasattr(self, "center_play_button"):
            return
        self.center_play_button.set_playing_state(is_playing)

    def _format_time(self, ms: int) -> str:
        total_seconds = max(0, int(ms // 1000))
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        hours = minutes // 60
        minutes = minutes % 60
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _set_status(self, message: str):
        self.status_label.setText(message or "")

    def _emit_fallback_once(self):
        if self._fallback_emitted:
            return
        self._fallback_emitted = True
        self.fallback_requested.emit(self.media_path)

    def _normalize_playback_rate(self, rate: float) -> float:
        try:
            value = float(rate)
        except Exception:
            value = 1.0
        allowed = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
        return min(allowed, key=lambda item: abs(item - value))

    def _sync_playback_rate_combo(self):
        if not hasattr(self, "speed_combo"):
            return
        for index in range(self.speed_combo.count()):
            try:
                if abs(float(self.speed_combo.itemData(index)) - self._playback_rate) < 0.001:
                    if self.speed_combo.currentIndex() != index:
                        self.speed_combo.blockSignals(True)
                        self.speed_combo.setCurrentIndex(index)
                        self.speed_combo.blockSignals(False)
                    return
            except Exception:
                continue

    def _apply_playback_rate(self):
        self._playback_rate = self._normalize_playback_rate(self._playback_rate)
        self._sync_playback_rate_combo()
        if hasattr(self, "player"):
            try:
                self.player.setPlaybackRate(self._playback_rate)
            except Exception:
                _LOGGER.debug("Failed to restore native stderr after media preview probing.", exc_info=True)

    def _normalize_volume_percent(self, value: int) -> int:
        try:
            percent = int(round(float(value)))
        except Exception:
            percent = 65
        return max(0, min(100, percent))

    def current_volume_percent(self) -> int:
        try:
            if hasattr(self, "volume_slider"):
                return self._normalize_volume_percent(self.volume_slider.value())
        except Exception:
            pass
        try:
            if hasattr(self, "audio_output"):
                return self._normalize_volume_percent(self.audio_output.volume() * 100.0)
        except Exception:
            pass
        return self._normalize_volume_percent(getattr(self, "_volume_percent", 65))

    def current_playback_rate(self) -> float:
        return self._normalize_playback_rate(getattr(self, "_playback_rate", 1.0))

    def current_position_ms(self) -> int:
        try:
            if hasattr(self, "player"):
                return max(0, int(self.player.position() or 0))
        except Exception:
            pass
        return 0

    def is_playing(self) -> bool:
        try:
            return (
                QT_MULTIMEDIA_AVAILABLE
                and hasattr(self, "player")
                and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            )
        except Exception:
            return False

    def _apply_resume_state_once(self):
        if self._resume_applied:
            return
        if not QT_MULTIMEDIA_AVAILABLE or not hasattr(self, "player"):
            return
        self._resume_applied = True

        if self._resume_position_ms > 0:
            try:
                duration = max(0, int(self.player.duration() or self._duration_ms or 0))
                position = self._resume_position_ms
                if duration > 0:
                    position = min(position, max(0, duration - 250))
                self.player.setPosition(max(0, position))
                self.position_slider.setValue(max(0, position))
                self._update_time_label(max(0, position))
            except Exception:
                pass

        self._apply_playback_rate()

        if self._resume_playing:
            self._request_playback_when_ready()
        else:
            self._set_play_button_label(False)
            self._set_center_play_overlay_icon(False)
            self._set_center_play_overlay_visible(True)

    def _make_playback_button_icon(self, is_playing: bool) -> QIcon:
        pixmap_size = 22
        pixmap = QPixmap(pixmap_size, pixmap_size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 248))

        if is_playing:
            painter.drawRoundedRect(QRectF(6.0, 4.5, 4.2, 13.0), 1.4, 1.4)
            painter.drawRoundedRect(QRectF(12.0, 4.5, 4.2, 13.0), 1.4, 1.4)
        else:
            path = QPainterPath()
            path.moveTo(7.0, 4.5)
            path.lineTo(7.0, 17.5)
            path.lineTo(17.0, 11.0)
            path.closeSubpath()
            painter.drawPath(path)

        painter.end()
        return QIcon(pixmap)

    def _set_play_button_label(self, is_playing: bool):
        if not hasattr(self, "play_button"):
            return
        self.play_button.setText("")
        self.play_button.setIcon(self._make_playback_button_icon(is_playing))
        self.play_button.setToolTip("Pausar (Espaço)" if is_playing else "Reproduzir (Espaço)")

    def _is_media_ready_for_playback(self) -> bool:
        if not QT_MULTIMEDIA_AVAILABLE or not hasattr(self, "player"):
            return False
        try:
            status = self.player.mediaStatus()
        except Exception:
            return bool(getattr(self, "_media_ready", False))
        ready_statuses = (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        )
        return bool(getattr(self, "_media_ready", False) or status in ready_statuses)

    def _start_media_prepare_timeout(self):
        if hasattr(self, "_media_prepare_timer"):
            self._media_prepare_timer.start(self._media_prepare_timeout_ms)

    def _stop_media_prepare_timeout(self):
        if hasattr(self, "_media_prepare_timer"):
            self._media_prepare_timer.stop()

    def _request_playback_when_ready(self):
        if not QT_MULTIMEDIA_AVAILABLE or not hasattr(self, "player"):
            self._emit_fallback_once()
            return
        if self._is_media_ready_for_playback():
            self._play_requested_when_ready = False
            self._stop_media_prepare_timeout()
            self._set_status("")
            try:
                self.player.play()
            except Exception:
                self._set_status("Não foi possível iniciar o preview interno. Abrindo no player padrão...")
                self._emit_fallback_once()
            return

        self._play_requested_when_ready = True
        self._set_status("Carregando mídia para reprodução...")
        self._set_play_button_label(False)
        self._set_center_play_overlay_icon(False)
        self._set_center_play_overlay_visible(True)
        self._start_media_prepare_timeout()

    def _on_media_prepare_timeout(self):
        if not getattr(self, "_play_requested_when_ready", False):
            return
        if self._is_media_ready_for_playback():
            self._request_playback_when_ready()
            return
        self._play_requested_when_ready = False
        self._set_play_button_label(False)
        self._set_center_play_overlay_icon(False)
        self._set_center_play_overlay_visible(True)
        self._set_status("O preview interno demorou para preparar esta mídia. Abrindo no player padrão...")
        self._emit_fallback_once()

    def _toggle_playback(self):
        if not QT_MULTIMEDIA_AVAILABLE or not hasattr(self, "player"):
            self._emit_fallback_once()
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._play_requested_when_ready = False
            self._stop_media_prepare_timeout()
            self.player.pause()
        else:
            self._request_playback_when_ready()

    def _seek_relative(self, delta_ms: int):
        if not QT_MULTIMEDIA_AVAILABLE or not hasattr(self, "player"):
            return
        try:
            current = max(0, int(self.player.position() or 0))
            duration = max(0, int(self.player.duration() or self._duration_ms or 0))
            target = current + int(delta_ms or 0)
            if duration > 0:
                target = min(max(0, target), duration)
            else:
                target = max(0, target)
            self.player.setPosition(target)
            self.position_slider.setValue(target)
            self._update_time_label(target)
        except Exception:
            _LOGGER.debug("Failed to seek media preview.", exc_info=True)

    def _toggle_mute(self):
        if self.current_volume_percent() > 0:
            self._last_audible_volume_percent = self.current_volume_percent()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(max(1, self._normalize_volume_percent(self._last_audible_volume_percent)))

    def _update_mute_button(self):
        if not hasattr(self, "mute_button"):
            return
        muted = self.current_volume_percent() <= 0
        self.mute_button.setText("Mudo" if muted else "Som")
        self.mute_button.setToolTip("Reativar som (M)" if muted else "Ativar mudo (M)")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self._is_fullscreen = False
            if hasattr(self, "fullscreen_button"):
                self.fullscreen_button.setText("Tela cheia")
                self.fullscreen_button.setToolTip("Entrar em tela cheia (F)")
        else:
            self.showFullScreen()
            self._is_fullscreen = True
            if hasattr(self, "fullscreen_button"):
                self.fullscreen_button.setText("Restaurar")
                self.fullscreen_button.setToolTip("Restaurar janela (Esc)")
        QTimer.singleShot(0, self._position_center_play_overlay)

    def _on_duration_changed(self, duration_ms: int):
        self._duration_ms = max(0, int(duration_ms or 0))
        self.position_slider.setRange(0, self._duration_ms)
        self._update_time_label(self.player.position() if hasattr(self, "player") else 0)
        if not self._resume_applied:
            QTimer.singleShot(0, self._apply_resume_state_once)

    def _on_position_changed(self, position_ms: int):
        if not self._seeking:
            self.position_slider.setValue(max(0, int(position_ms or 0)))
        self._update_time_label(position_ms)

    def _update_time_label(self, position_ms: int):
        self.time_label.setText(f"{self._format_time(position_ms)} / {self._format_time(self._duration_ms)}")

    def _on_seek_start(self):
        self._seeking = True

    def _on_seek_moved(self, value: int):
        self._update_time_label(value)

    def _on_seek_end(self):
        self._seeking = False
        if hasattr(self, "player"):
            self.player.setPosition(self.position_slider.value())

    def _on_volume_changed(self, value: int):
        self._volume_percent = self._normalize_volume_percent(value)
        if self._volume_percent > 0:
            self._last_audible_volume_percent = self._volume_percent
        if hasattr(self, "audio_output"):
            self.audio_output.setVolume(max(0.0, min(1.0, self._volume_percent / 100.0)))
        self._update_mute_button()

    def _on_playback_rate_changed(self, index: int):
        if not hasattr(self, "speed_combo"):
            return
        try:
            self._playback_rate = self._normalize_playback_rate(self.speed_combo.itemData(index))
        except Exception:
            self._playback_rate = 1.0
        self._apply_playback_rate()

    def _on_playback_state_changed(self, state):
        if not QT_MULTIMEDIA_AVAILABLE:
            return
        is_playing = state == QMediaPlayer.PlaybackState.PlayingState
        if is_playing:
            self._set_play_button_label(True)
        else:
            self._set_play_button_label(False)
        self._set_center_play_overlay_icon(is_playing)
        self._set_center_play_overlay_visible(True, auto_hide=is_playing)

    def _on_media_status_changed(self, status):
        if not QT_MULTIMEDIA_AVAILABLE:
            return
        ready_statuses = (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.StalledMedia,
        )
        if status in ready_statuses:
            self._media_ready = True
            if not getattr(self, "_play_requested_when_ready", False):
                self._set_status("")
            if not self._resume_applied:
                QTimer.singleShot(0, self._apply_resume_state_once)
            if getattr(self, "_play_requested_when_ready", False):
                QTimer.singleShot(0, self._request_playback_when_ready)
        elif status == QMediaPlayer.MediaStatus.LoadingMedia:
            self._media_ready = False
            if getattr(self, "_play_requested_when_ready", False):
                self._set_status("Carregando mídia para reprodução...")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._play_requested_when_ready = False
            self._stop_media_prepare_timeout()
            self._set_center_play_overlay_icon(False)
            self._set_center_play_overlay_visible(True)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._play_requested_when_ready = False
            self._stop_media_prepare_timeout()
            self._set_status("Não foi possível reproduzir esta mídia no preview interno. Abrindo no player padrão...")
            self._emit_fallback_once()

    def _on_player_error(self, error, error_string: str = ""):
        if error == QMediaPlayer.Error.NoError:
            return
        self._play_requested_when_ready = False
        self._stop_media_prepare_timeout()
        msg = error_string or "Erro de reprodução no preview interno."
        self._set_status(f"{msg} Abrindo no player padrão...")
        self._emit_fallback_once()

    def eventFilter(self, watched, event):
        if watched in (getattr(self, "video_container", None), getattr(self, "video_widget", None)):
            if event.type() == QEvent.Type.MouseButtonDblClick:
                self._toggle_fullscreen()
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._toggle_playback()
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self._toggle_playback()
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            self._seek_relative(-10000)
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            self._seek_relative(10000)
            event.accept()
            return
        if key == Qt.Key.Key_M:
            self._toggle_mute()
            event.accept()
            return
        if key == Qt.Key.Key_F:
            self._toggle_fullscreen()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self.isFullScreen():
            self._toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self._position_center_play_overlay()
        if hasattr(self, "player") and QT_MULTIMEDIA_AVAILABLE:
            is_playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            self._set_center_play_overlay_icon(is_playing)
            self._set_center_play_overlay_visible(True, auto_hide=is_playing)
        elif hasattr(self, "center_play_overlay"):
            self._set_center_play_overlay_icon(False)
            self._set_center_play_overlay_visible(True)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._position_center_play_overlay()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_center_play_overlay()

    def closeEvent(self, event):
        try:
            self._stop_media_prepare_timeout()
        except Exception:
            pass
        try:
            if hasattr(self, "center_play_overlay"):
                self.center_play_overlay.hide()
                self.center_play_overlay.deleteLater()
        except Exception:
            pass
        try:
            if hasattr(self, "player"):
                self.player.stop()
                self.player.setVideoOutput(None)
                self.player.setAudioOutput(None)
        except Exception:
            pass
        super().closeEvent(event)
