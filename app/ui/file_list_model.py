import logging
from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QTimer
import os

from app.interaction_model.event_bridge import event_bridge
from app.interaction_model.job_state import ensure_job_state, snapshot_job

_LOGGER = logging.getLogger(__name__)


class FileListModel(QAbstractListModel):

    ROLE_JOB = Qt.UserRole + 1
    ROLE_FILE_NAME = Qt.UserRole + 2
    ROLE_STATUS = Qt.UserRole + 3
    ROLE_PROGRESS = Qt.UserRole + 4
    ROLE_CODEC = Qt.UserRole + 5
    ROLE_RESOLUTION = Qt.UserRole + 6
    ROLE_JOB_REF = Qt.UserRole + 7

    def __init__(self, interaction_model=None):
        super().__init__()

        self._items = []
        self._interaction_model = interaction_model

        # normalized path -> row
        self._job_row_map = {}

        # duplicate aggregation
        self._duplicate_counter = 0
        self._duplicate_timer = QTimer()
        self._duplicate_timer.setSingleShot(True)
        self._duplicate_timer.timeout.connect(self._emit_duplicate_feedback)

        event_bridge.subscribe(self._on_event)

    # ------------------------------------------------
    # Utils
    # ------------------------------------------------

    def _normalize_path(self, path):

        if not path:
            return None

        path = os.path.normpath(path)
        path = path.lower()

        return path

    def _rebuild_row_map(self):

        self._job_row_map.clear()

        for row, job in enumerate(self._items):

            key = self._normalize_path(getattr(job, "source_path", None))

            if key:
                self._job_row_map[key] = row


    def _is_output_name_locked_for_job(self, job):

        if not job:
            return True

        status = str(getattr(job, "status", "") or "").upper()
        output_path = getattr(job, "output_path", None)
        output_size = getattr(job, "output_size_bytes", None)

        # The preview name is allowed to regenerate only while the job is idle.
        # The moment the user clicks Compress the job enters QUEUED, and the
        # displayed output_path must become frozen. Regenerating after QUEUED
        # would call generate_output_path() again and can advance the suffix.
        if status in {
            "QUEUED",
            "ANALYZING",
            "RUNNING",
            "PROCESSING",
            "DONE",
            "FINISHED",
            "COMPLETED",
            "CANCELLED",
            "FAILED",
            "ERROR",
        }:
            return True

        # Once the encoder has written a result, output_path is the actual file
        # produced by the job and must not be recomputed.
        if output_path and output_size is not None:
            return True

        return False

    def _needs_new_preview_output_path_after_config_change(self, job):
        """Return True when a processed output path must roll over during preview.

        If a job was already processed, its output_path points to a real file.
        When the user changes profile parameters, the configuration preview clears
        output_size_bytes/output_bitrate, but the status may still be DONE. In
        that case the next output name must be generated immediately on the
        parameter change, not later when Compress is clicked.
        """

        if not job:
            return False

        source = getattr(job, "source_path", None)
        output_path = getattr(job, "output_path", None)
        output_size = getattr(job, "output_size_bytes", None)

        if not source or not output_path:
            return False

        if output_size is not None:
            return False

        try:
            if not os.path.exists(output_path):
                return False
        except Exception:
            _LOGGER.debug("Unable to verify existing output path during preview rollover check", exc_info=True)
            return False

        status = str(getattr(job, "status", "") or "").upper()
        if status in {"QUEUED", "ANALYZING", "RUNNING", "PROCESSING"}:
            return False

        return True

    def _refresh_output_path_for_job(self, job):

        if not job:
            return False

        source = getattr(job, "source_path", None)
        if not source:
            return False

        force_rollover = self._needs_new_preview_output_path_after_config_change(job)

        if not force_rollover and self._is_output_name_locked_for_job(job):
            return False

        try:
            from app.core.output_naming import generate_output_path

            new_output_path = generate_output_path(source, job=job)
        except Exception:
            _LOGGER.debug("Unable to refresh output path for job", exc_info=True)
            return False

        if not new_output_path:
            return False

        current_output_path = getattr(job, "output_path", None)
        if current_output_path == new_output_path:
            return False

        job.output_path = new_output_path
        return True

    def _emit_duplicate_feedback(self):

        if self._duplicate_counter == 0:
            return

        event_bridge.emit(
            "duplicate_files_ignored",
            {"count": self._duplicate_counter},
        )

        self._duplicate_counter = 0

    def _register_duplicate(self):

        self._duplicate_counter += 1

        # restart small timer to aggregate multiple duplicates
        self._duplicate_timer.start(120)

    # ------------------------------------------------
    # Model API
    # ------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def data(self, index, role):

        if not index.isValid():
            return None

        item = self._items[index.row()]

        if role == Qt.DisplayRole:
            return getattr(item, "file_name", "unknown")

        if role == Qt.ToolTipRole:

            dest = getattr(item, "output_path", None) or getattr(item, "source_path", "")

            if len(dest) > 60:
                return dest

            return None

        if role == self.ROLE_JOB:
            return snapshot_job(item)

        if role == self.ROLE_JOB_REF:
            return item

        if role == self.ROLE_FILE_NAME:
            return getattr(item, "file_name", "unknown")

        if role == self.ROLE_STATUS:
            return getattr(item, "status", "pending")

        if role == self.ROLE_PROGRESS:
            return getattr(item, "progress", 0)

        if role == self.ROLE_CODEC:
            return getattr(item, "codec", "?")

        if role == self.ROLE_RESOLUTION:
            return getattr(item, "resolution", "?")

        return None

    def roleNames(self):
        return {
            self.ROLE_JOB: b"job",
            self.ROLE_FILE_NAME: b"file_name",
            self.ROLE_STATUS: b"status",
            self.ROLE_PROGRESS: b"progress",
            self.ROLE_CODEC: b"codec",
            self.ROLE_RESOLUTION: b"resolution",
            self.ROLE_JOB_REF: b"job_ref",
        }

    # ------------------------------------------------
    # Event bridge
    # ------------------------------------------------

    def _on_event(self, event_type, payload):

        # ------------------------------------------------
        # JOB ENQUEUED
        # ------------------------------------------------

        if event_type == "job_enqueued":

            job = payload.get("job")
            if not job:
                return

            key = self._normalize_path(getattr(job, "source_path", None))

            # duplicate detection
            if key and key in self._job_row_map:
                self._register_duplicate()
                return

            
            # ------------------------------------------------
            # BUILD OUTPUT PATH
            # ------------------------------------------------
            try:
                from app.core.output_naming import generate_output_path

                source = getattr(job, "source_path", None)

                if source:
                    job.output_path = generate_output_path(source, job=job)

            except Exception:
                _LOGGER.debug("Unable to build initial output path for job", exc_info=True)

            ensure_job_state(job)

            row = len(self._items)

            self.beginInsertRows(QModelIndex(), row, row)

            self._items.append(job)

            if key:
                self._job_row_map[key] = row

            self.endInsertRows()

        # ------------------------------------------------
        # JOB UPDATE EVENTS
        # ------------------------------------------------

        elif event_type in (
            "job_progress",
            "job_finished",
            "job_failed",
            "job_updated",
        ):

            job = payload.get("job")
            if not job:
                return

            if event_type == "job_updated":
                self._refresh_output_path_for_job(job)

            key = self._normalize_path(getattr(job, "source_path", None))

            row = self._job_row_map.get(key)

            if row is None or row >= len(self._items):
                return

            index = self.index(row)

            self.dataChanged.emit(
                index,
                index,
                [
                    self.ROLE_JOB,
                    self.ROLE_JOB_REF,
                    self.ROLE_FILE_NAME,
                    self.ROLE_STATUS,
                    self.ROLE_PROGRESS,
                    self.ROLE_CODEC,
                    self.ROLE_RESOLUTION,
                ],
            )

        # ------------------------------------------------
        # CLEAR ALL
        # ------------------------------------------------

        elif event_type == "clear_all_jobs":

            if not self._items:
                return

            self.beginResetModel()
            self._items.clear()
            self._job_row_map.clear()
            self.endResetModel()


        # ------------------------------------------------
        # REMOVE SINGLE JOB
        # ------------------------------------------------

        elif event_type == "job_remove_requested":

            job = payload if not isinstance(payload, dict) else payload.get("job")

            if not job:
                return

            key = self._normalize_path(getattr(job, "source_path", None))
            row = self._job_row_map.get(key)

            if row is None or row >= len(self._items):
                return

            self.beginRemoveRows(QModelIndex(), row, row)
            del self._items[row]
            self.endRemoveRows()

            self._rebuild_row_map()

        # ------------------------------------------------
        # REMOVE INVALID
        # ------------------------------------------------

        elif event_type == "remove_invalid_requested":

            invalid_rows = []

            for i, item in enumerate(self._items):
                status = getattr(item, "status", "").upper()
                if status in ("ERROR", "FAILED", "INVALID"):
                    invalid_rows.append(i)

            if not invalid_rows:
                return

            for row in reversed(invalid_rows):

                self.beginRemoveRows(QModelIndex(), row, row)
                del self._items[row]
                self.endRemoveRows()

            self._rebuild_row_map()

        # ------------------------------------------------
        # CONFIGURATION CHANGED
        # ------------------------------------------------

        elif event_type in {"configuration_changed", "app_preferences_changed"}:

            try:
                from app.core.output_naming import generate_output_path

                for row, job in enumerate(self._items):

                    source = getattr(job, "source_path", None)

                    if source and not self._is_output_name_locked_for_job(job):
                        job.output_path = generate_output_path(source, job=job)

                        index = self.index(row)

                        self.dataChanged.emit(
                            index,
                            index,
                            [
                                self.ROLE_JOB,
                                self.ROLE_JOB_REF,
                                self.ROLE_FILE_NAME,
                                self.ROLE_STATUS,
                                self.ROLE_PROGRESS,
                                self.ROLE_CODEC,
                                self.ROLE_RESOLUTION,
                            ],
                        )

            except Exception:
                _LOGGER.debug("Unable to rebuild output paths after configuration change", exc_info=True)

