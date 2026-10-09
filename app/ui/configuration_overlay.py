from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QEvent, Property, QRect, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QVBoxLayout

from app.ancillary.configuration import ConfigurationService
from app.core.compression_profiles import build_profile_signature, estimate_output_bitrate_for_profile, estimate_output_is_actionable, estimate_size_for_profile
from app.engine.encode_estimator import estimate_size_crf
from app.interaction_model.event_bridge import event_bridge
from app.interaction_model.job_state import patch_job
from app.ui.configuration_panel import ConfigurationPanelWidget
from app.ui.theme_tokens import build_button_stylesheet, build_theme_tokens


class ConfigurationOverlay(QFrame):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("ConfigurationOverlay")
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

        self._applying_styles = False
        self._target_rect = QRect()
        self._animation = QPropertyAnimation(self, b"animatedGeometry", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)
        self._closing_after_animation = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._context_job = None
        self._all_jobs_provider = None

        self.panel = ConfigurationPanelWidget(self)
        self.panel.closeRequested.connect(self.close_overlay)
        self.panel.applyRequested.connect(self.apply_changes)
        self.panel.quickPresetAutoApplyRequested.connect(self.apply_changes_live)
        self.panel.smartProfileAutoApplyRequested.connect(self.apply_changes_live)
        self.panel.restoreRequested.connect(self.panel.restore_defaults)
        layout.addWidget(self.panel, 1)

        self._esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._esc_shortcut.activated.connect(self.close_overlay)

        self._apply_theme_styles()
        event_bridge.subscribe(self._on_app_event)

    def _on_app_event(self, event_type, payload):
        if event_type not in {"job_updated", "job_progress", "job_finished", "job_failed"}:
            return
        job = payload.get("job") if isinstance(payload, dict) else payload
        if job is not self._context_job:
            return
        self._sync_context_lock_from_job_status()

    def _sync_context_lock_from_job_status(self):
        if self._context_job is None:
            return
        locked, reason = self._is_job_edit_locked(self._context_job)
        self.panel.set_locked(locked, reason)

    def _is_job_edit_locked(self, job) -> tuple[bool, str | None]:
        status = str(getattr(job, "status", "") or "").strip().upper()
        if status in {"QUEUED", "RUNNING", "PROCESSING", "ANALYZING"}:
            return True, "Ajustes ficam indisponíveis enquanto o item está na fila ou em processamento."
        return False, None

    def set_context_job(self, job, *, selected_count: int = 1):
        self._context_job = job
        self.panel.set_context_job(job, selected_count=selected_count)
        self._sync_context_lock_from_job_status()

    def clear_context_job(self):
        self._context_job = None
        self.panel.clear_context_job()
        self.panel.set_locked(False)

    def set_all_jobs_provider(self, provider):
        self._all_jobs_provider = provider

    def _get_animated_geometry(self):
        return self.geometry()

    def _set_animated_geometry(self, rect):
        self.setGeometry(rect)

    animatedGeometry = Property(QRect, _get_animated_geometry, _set_animated_geometry)

    def _apply_theme_styles(self):
        if self._applying_styles:
            return
        self._applying_styles = True
        try:
            tokens = build_theme_tokens(self.palette())
            shell_bg = tokens.overlay_shell_bg.name(QColor.NameFormat.HexArgb)
            border = tokens.overlay_border_strong.name(QColor.NameFormat.HexArgb)
            button_stylesheet = build_button_stylesheet(
                tokens,
                min_height=30,
                border_radius=4,
                horizontal_padding=10,
            ).replace("QPushButton", "QFrame#ConfigurationOverlay QPushButton")
            self.setStyleSheet(
                f"""
                QFrame#ConfigurationOverlay {{
                    background: {shell_bg};
                    border: 1px solid {border};
                    border-radius: 4px;
                }}

                {button_stylesheet}
                """
            )
        finally:
            self._applying_styles = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.ThemeChange):
            self._apply_theme_styles()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close_overlay()
            event.accept()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _patch_changes_job_configuration(job, patch: dict) -> bool:
        if job is None or not isinstance(patch, dict):
            return False

        def _normalize(value):
            if isinstance(value, float):
                return round(value, 6)
            if isinstance(value, tuple):
                return [_normalize(item) for item in value]
            if isinstance(value, list):
                return [_normalize(item) for item in value]
            if isinstance(value, dict):
                return {str(key): _normalize(val) for key, val in sorted(value.items(), key=lambda item: str(item[0]))}
            return value

        for key, value in patch.items():
            if _normalize(getattr(job, key, None)) != _normalize(value):
                return True
        return False

    def _recalculate_job_estimates(self, job, *, profile_changed: bool = False, configuration_changed: bool = False):
        estimated = estimate_size_for_profile(job, estimate_size_crf)
        if estimated is None:
            estimated_bitrate = None
        else:
            estimated_bitrate = estimate_output_bitrate_for_profile(job, estimated)

        source_size = getattr(job, "source_size", None)
        if source_size in (None, 0, "0"):
            source_path = getattr(job, "source_path", None)
            if source_path:
                try:
                    source_size = os.path.getsize(source_path)
                except Exception:
                    source_size = None

        patch = {
            "estimated_size_bytes": estimated,
            "estimated_output_bitrate": estimated_bitrate,
            "output_size_bytes": None,
            "output_bitrate": None,
            "last_output_profile_mode": None,
            "last_output_profile_signature": None,
        }
        current_status = str(getattr(job, "status", "") or "").upper()
        actionable = estimate_output_is_actionable(
            job,
            estimated_size_bytes=estimated,
            source_size_bytes=source_size,
        )
        if current_status in {"READY", "IDLE", "PENDING", "NO_GAIN"}:
            patch["status"] = "NO_GAIN" if actionable is False else "READY"
            patch["progress"] = 0
            patch["error"] = None
        elif (profile_changed or configuration_changed) and current_status == "CANCELLED":
            patch["status"] = "NO_GAIN" if actionable is False else "READY"
            patch["progress"] = 0
            patch["error"] = None
        if source_size is not None:
            patch["source_size"] = source_size
        patch_job(job, **patch)

    def _profile_cleanup_patch(self, data: dict) -> dict:
        patch = dict(data or {})
        current_mode = str(patch.get("profile_mode", "quick") or "quick").strip().lower()

        if current_mode == "advanced":
            patch.update({
                "strategy_type": None,
                "smart_strategy": None,
                "slider_value": None,
                "smart_intensity": None,
                "quick_profile_preset": None,
            })
        elif current_mode == "smart":
            patch.update({
                "manual_control_type": None,
                "advanced_target_mode": None,
                "advanced_target_size_mb": None,
                "advanced_target_bitrate_kbps": None,
                "advanced_estimated_size_mb": None,
                "advanced_width": None,
                "advanced_height": None,
                "quick_profile_preset": None,
            })
        elif current_mode == "audio":
            patch.update({
                "manual_control_type": None,
                "advanced_target_mode": None,
                "advanced_target_size_mb": None,
                "advanced_target_bitrate_kbps": None,
                "advanced_estimated_size_mb": None,
                "advanced_width": None,
                "advanced_height": None,
                "strategy_type": None,
                "smart_strategy": None,
                "slider_value": None,
                "smart_intensity": None,
                "quick_profile_preset": None,
            })
        else:
            patch.update({
                "manual_control_type": None,
                "advanced_target_mode": None,
                "advanced_target_size_mb": None,
                "advanced_target_bitrate_kbps": None,
                "advanced_estimated_size_mb": None,
                "advanced_width": None,
                "advanced_height": None,
                "strategy_type": None,
                "smart_strategy": None,
                "slider_value": None,
                "smart_intensity": None,
            })
        return patch

    def _iter_all_jobs(self):
        provider = self._all_jobs_provider
        if provider is None:
            return []
        try:
            jobs = list(provider() or [])
        except Exception:
            return []

        unique_jobs = []
        seen = set()
        for job in jobs:
            if job is None:
                continue
            marker = id(job)
            if marker in seen:
                continue
            seen.add(marker)
            unique_jobs.append(job)
        return unique_jobs

    @staticmethod
    def _job_source_extension(job) -> str:
        source_path = (
            getattr(job, "source_path", None)
            or getattr(job, "input_path", None)
            or getattr(job, "file_path", None)
            or ""
        )
        return os.path.splitext(str(source_path or ""))[1].lower()

    @classmethod
    def _job_is_audio_target(cls, job) -> bool:
        profile_mode = str(getattr(job, "profile_mode", "") or "").strip().lower()
        if profile_mode == "audio":
            return True
        return cls._job_source_extension(job) in {
            ".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus", ".alac"
        }

    @classmethod
    def _job_is_video_target(cls, job) -> bool:
        if cls._job_is_audio_target(job):
            return False
        ext = cls._job_source_extension(job)
        if not ext:
            return True
        return ext not in {
            ".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus", ".alac"
        }

    def _job_accepts_apply_all_patch(self, job, patch: dict) -> bool:
        target_profile = str(patch.get("profile_mode", "") or "").strip().lower()
        carries_video_output = "video_output_format" in patch or "video_output_extension" in patch

        # Perfis de vídeo carregam campos video_output_* por design. Em seleção
        # mista, esses campos não podem contaminar jobs de áudio nem jobs de
        # extração/conversão de áudio, pois mudam indevidamente o container final.
        if target_profile in {"quick", "smart", "advanced"} or carries_video_output:
            return self._job_is_video_target(job)

        return True

    def _apply_configuration_data(self, data, *, apply_to_all: bool = False):
        if not isinstance(data, dict):
            return False

        patch = self._profile_cleanup_patch(data)

        if apply_to_all:
            jobs = self._iter_all_jobs()
            if not jobs and self._context_job is not None:
                jobs = [self._context_job]
            applied = False
            for job in jobs:
                locked, _reason = self._is_job_edit_locked(job)
                if locked or not self._job_accepts_apply_all_patch(job, patch):
                    continue
                previous_signature = build_profile_signature(job)
                configuration_changed = self._patch_changes_job_configuration(job, patch)
                patch_job(job, **patch)
                profile_changed = tuple(previous_signature) != tuple(build_profile_signature(job))
                self._recalculate_job_estimates(
                    job,
                    profile_changed=profile_changed,
                    configuration_changed=configuration_changed,
                )
                event_bridge.emit("job_updated", {"job": job})
                applied = True
            if applied:
                event_bridge.emit("configuration_changed", {})
            return applied

        if self._context_job is not None:
            previous_signature = build_profile_signature(self._context_job)
            configuration_changed = self._patch_changes_job_configuration(self._context_job, patch)
            patch_job(self._context_job, **patch)
            profile_changed = tuple(previous_signature) != tuple(build_profile_signature(self._context_job))
            self._recalculate_job_estimates(
                self._context_job,
                profile_changed=profile_changed,
                configuration_changed=configuration_changed,
            )
            event_bridge.emit("job_updated", {"job": self._context_job})
            return True

        quick_only = {}
        if "quick_profile_preset" in patch:
            quick_only["quick_profile_preset"] = patch["quick_profile_preset"]
        if not quick_only:
            return False
        ConfigurationService.instance().update(**quick_only)
        event_bridge.emit("configuration_changed", {})
        return True

    def apply_changes_live(self, data):
        self._apply_configuration_data(data)

    def apply_changes(self):
        data = self.panel.collect_values() or {}
        if not self._apply_configuration_data(data, apply_to_all=True):
            return
        self.accepted.emit()
        self.close_overlay()

    def open_overlay(self, target_rect: QRect):
        self._target_rect = QRect(target_rect)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

        if self.isVisible() and not self._closing_after_animation:
            if self._animation.state() == QPropertyAnimation.State.Running:
                self._animation.stop()
            self.setGeometry(QRect(target_rect))
            return

        self._closing_after_animation = False
        start_rect = QRect(target_rect.x(), target_rect.bottom(), target_rect.width(), 0)
        if self._animation.state() == QPropertyAnimation.State.Running:
            self._animation.stop()
        self.setGeometry(start_rect)
        self._animation.setStartValue(start_rect)
        self._animation.setEndValue(QRect(target_rect))
        self._animation.start()

    def close_overlay(self):
        if not self.isVisible() or self._closing_after_animation:
            return
        end_rect = QRect(self.geometry().x(), self._target_rect.bottom(), self.geometry().width(), 0)
        self._closing_after_animation = True
        self._animation.stop()
        self._animation.setStartValue(self.geometry())
        self._animation.setEndValue(end_rect)
        self._animation.start()

    def sync_to_rect(self, target_rect: QRect):
        self._target_rect = QRect(target_rect)
        if self._animation.state() == QPropertyAnimation.State.Running:
            self._animation.stop()
        if self.isVisible() and not self._closing_after_animation:
            self.setGeometry(target_rect)

    def _on_animation_finished(self):
        if self._closing_after_animation:
            self.hide()
            self._closing_after_animation = False
            self.rejected.emit()
