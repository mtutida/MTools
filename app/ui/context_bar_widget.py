import logging
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QSizePolicy, QWidget

from app.interaction_model.event_bridge import event_bridge
from app.ui.file_list_model import FileListModel
from app.ui.theme_tokens import build_theme_tokens, build_small_button_stylesheet


_LOGGER = logging.getLogger(__name__)

class StatusDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor()
        self.setFixedSize(7, 7)

    def set_color(self, color: QColor):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect())


class ContextBarWidget(QFrame):
    BAR_HEIGHT = 22
    BUTTON_HEIGHT = 18

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("ContextBarWidget")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWidth(0)
        self.setMidLineWidth(0)
        self.setFixedHeight(self.BAR_HEIGHT)
        self.setContentsMargins(0, 0, 0, 0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._jobs = {}
        self._model = None
        self._applying_styles = False
        self._import_state = {"active": False, "current": 0, "total": 0, "valid": 0, "invalid": 0}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)

        self.dot = StatusDot(self)

        self.text_label = QLabel(self)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        self.text_label.setContentsMargins(0, 0, 0, 0)
        self.text_label.setMargin(0)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.text_label.setFixedHeight(self.BAR_HEIGHT)

        self.btn_remove_invalid = QToolButton(self)
        self.btn_remove_invalid.setObjectName("RemoveInvalidButton")
        self.btn_remove_invalid.setText("Remover inválidos")
        self.btn_remove_invalid.setAutoRaise(True)
        self.btn_remove_invalid.setFixedHeight(self.BUTTON_HEIGHT)
        self.btn_remove_invalid.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_remove_invalid.clicked.connect(self._remove_invalid)
        self.btn_remove_invalid.setVisible(False)

        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.text_label, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(
            self.btn_remove_invalid,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

        self._apply_styles()

        event_bridge.subscribe(self._on_event)
        self._update_stats()

    def _color_hex(self, color: QColor) -> str:
        return color.name(QColor.NameFormat.HexArgb)

    def _apply_styles(self):
        if self._applying_styles:
            return
        self._applying_styles = True
        try:
            tokens = build_theme_tokens(self.palette())
            bar_bg_hex = self._color_hex(tokens.surface_context_bar)
            bar_text_hex = self._color_hex(tokens.surface_context_bar_text)
            bar_border_hex = self._color_hex(tokens.border_context_bar)
            small_button_styles = build_small_button_stylesheet(tokens, border_radius=4)

            self.setStyleSheet(
                f"""
                QFrame#ContextBarWidget {{
                    background: {bar_bg_hex};
                    border-top: 1px solid {bar_border_hex};
                    border-radius: 0 0 3px 3px;
                }}
                QFrame#ContextBarWidget QLabel {{
                    color: {bar_text_hex};
                    background: transparent;
                }}
                QFrame#ContextBarWidget QToolButton#RemoveInvalidButton {{
                    padding: 0 8px;
                    color: {bar_text_hex};
                }}
                QFrame#ContextBarWidget QToolButton#RemoveInvalidButton:disabled {{
                    color: {self._color_hex(tokens.button_disabled_fg)};
                    border: 1px solid {self._color_hex(tokens.button_disabled_border)};
                }}
                {small_button_styles}
                """
            )
            self.dot.set_color(tokens.text_secondary)
        finally:
            self._applying_styles = False

    def closeEvent(self, event):
        unsubscribe = getattr(event_bridge, "unsubscribe", None)
        if callable(unsubscribe):
            try:
                unsubscribe(self._on_event)
            except Exception:
                _LOGGER.debug("Failed to unsubscribe context bar event bridge listener.", exc_info=True)
        super().closeEvent(event)

    def changeEvent(self, event):
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange, QEvent.Type.ThemeChange):
            self._apply_styles()
            self._update_stats()
        super().changeEvent(event)

    def set_model(self, model):
        if self._model is model:
            self._rebuild_jobs_from_model()
            return

        if self._model is not None:
            try:
                self._model.dataChanged.disconnect(self._on_model_changed)
                self._model.rowsInserted.disconnect(self._on_model_changed)
                self._model.rowsRemoved.disconnect(self._on_model_changed)
                self._model.modelReset.disconnect(self._on_model_changed)
            except Exception:
                _LOGGER.debug("Failed to disconnect context bar model signals.", exc_info=True)

        self._model = model

        if self._model is not None:
            try:
                self._model.dataChanged.connect(self._on_model_changed)
                self._model.rowsInserted.connect(self._on_model_changed)
                self._model.rowsRemoved.connect(self._on_model_changed)
                self._model.modelReset.connect(self._on_model_changed)
            except Exception:
                _LOGGER.debug("Failed to connect context bar model signals.", exc_info=True)

        self._rebuild_jobs_from_model()

    def _on_model_changed(self, *args):
        self._rebuild_jobs_from_model()

    def _rebuild_jobs_from_model(self):
        self._jobs.clear()

        model = self._model
        if model is None:
            self._update_stats()
            return

        try:
            total = model.rowCount()
        except Exception:
            self._update_stats()
            return

        for row in range(total):
            index = model.index(row)
            job = model.data(index, FileListModel.ROLE_JOB)
            if not job:
                continue

            key = getattr(job, "source_path", None) or id(job)
            self._jobs[key] = job

        self._update_stats()

    def mousePressEvent(self, event):
        event_bridge.emit("clear_selection_requested", None)
        super().mousePressEvent(event)

    def _remove_invalid(self):
        event_bridge.emit("remove_invalid_requested", None)

    def _on_event(self, event_type, payload):
        if event_type == "clear_all_jobs":
            self._jobs.clear()
            self._update_stats()
            return

        if event_type == "job_remove_requested":
            job = payload if not isinstance(payload, dict) else payload.get("job")
            if not job:
                return

            key = getattr(job, "source_path", None) or id(job)
            self._jobs.pop(key, None)
            self._update_stats()
            return

        if event_type == "remove_invalid_requested":
            self._rebuild_jobs_from_model()
            return

        if not payload:
            return

        if event_type not in ("job_enqueued", "job_updated", "job_progress", "job_finished", "job_failed"):
            return

        job = payload.get("job")
        if not job:
            return

        key = getattr(job, "source_path", None) or id(job)
        self._jobs[key] = job
        self._update_stats()

    def set_import_state(self, active: bool, current: int = 0, total: int = 0, valid: int = 0, invalid: int = 0):
        self._import_state = {
            "active": bool(active),
            "current": max(0, int(current or 0)),
            "total": max(0, int(total or 0)),
            "valid": max(0, int(valid or 0)),
            "invalid": max(0, int(invalid or 0)),
        }
        self.btn_remove_invalid.setEnabled(not active)
        self._update_stats()

    def _resolve_state(self, total, active, ready, queued, done, error, cancelled):
        tokens = build_theme_tokens(self.palette())
        if total == 0:
            return "Ocioso", tokens.text_secondary
        if error > 0:
            return "Com erro", tokens.status_error
        if active > 0:
            return "Processando", tokens.status_processing
        if queued > 0:
            return "Na fila", tokens.status_ready
        if ready > 0:
            return "Pronto!", tokens.status_ready
        if cancelled > 0:
            return "Cancelado", tokens.status_cancelled
        if done == total:
            return "Concluído", tokens.status_done
        return "Ocioso", tokens.text_secondary

    def _update_stats(self):
        import_state = self._import_state
        if import_state.get("active"):
            tokens = build_theme_tokens(self.palette())
            color = tokens.status_processing
            current = import_state.get("current", 0)
            total_items = import_state.get("total", 0)
            valid = import_state.get("valid", 0)
            invalid = import_state.get("invalid", 0)
            self.btn_remove_invalid.setVisible(False)
            self.dot.set_color(color)
            self.text_label.setText(
                f"Importando arquivos... {current}/{total_items} | Válidos: {valid} | Inválidos: {invalid}"
            )
            return

        total = len(self._jobs)
        active = ready = queued = done = error = cancelled = invalid = 0

        for job in self._jobs.values():
            status = str(getattr(job, "status", "")).upper()
            if status in ("ANALYZING", "PROCESSING", "RUNNING"):
                active += 1
            elif status == "READY":
                ready += 1
            elif status == "QUEUED":
                queued += 1
            elif status in ("DONE", "FINISHED", "COMPLETED"):
                done += 1
            elif status in ("ERROR", "FAILED"):
                error += 1
                invalid += 1
            elif status == "INVALID":
                invalid += 1
            elif status == "CANCELLED":
                cancelled += 1

        self.btn_remove_invalid.setVisible(invalid > 0)
        state, color = self._resolve_state(total, active, ready, queued, done, error, cancelled)
        text = (
            f"{state}"
            f" | Itens: {total}"
            f" | Ativo: {active}"
            f" | Pronto!: {ready}"
            f" | Fila: {queued}"
            f" | Concluído: {done}"
            f" | Cancelado: {cancelled}"
            f" | Erro: {error}"
        )
        self.dot.set_color(color)
        self.text_label.setText(text)
