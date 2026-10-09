from PySide6.QtWidgets import QMessageBox
import os

from app.core.compression_profiles import estimated_output_is_actionable
from app.core.output_naming import generate_output_path
from app.interaction_model.event_bridge import event_bridge
from app.interaction_model.job_state import patch_job
from app.ui.file_list_model import FileListModel


class ExecutionController:

    @staticmethod
    def _display_path(path):
        """Return a user-facing filesystem path using native Windows separators.

        Some output paths are assembled from preferences/placeholders and may
        contain a mix of '/' and '\\'. The actual filesystem path is kept
        untouched for creation; this helper normalizes only the text shown in
        dialogs.
        """
        text = os.path.normpath(str(path))
        if os.name == "nt":
            text = text.replace("/", "\\")
        return text

    def __init__(self):
        self.file_list = None
        self.selection_controller = None

    def set_context(self, file_list, selection_controller):
        self.file_list = file_list
        self.selection_controller = selection_controller

    def _collect_jobs(self, rows):
        if not rows or not self.file_list:
            return []

        model = self.file_list.model()
        jobs = []

        for r in rows:
            index = model.index(r)
            job = model.data(index, FileListModel.ROLE_JOB_REF)
            if job:
                jobs.append(job)

        return jobs

    def _ask_create_directory(self, output_dir):
        display_output_dir = self._display_path(output_dir)
        msg = QMessageBox(self.file_list)
        msg.setWindowTitle("Criar pasta de destino")
        msg.setIcon(QMessageBox.Question)
        msg.setText(
            f"A pasta de destino não existe:\n\n{display_output_dir}\n\nDeseja criá-la?"
        )
        criar_btn = msg.addButton("Criar", QMessageBox.AcceptRole)
        msg.addButton("Cancelar", QMessageBox.RejectRole)
        msg.setDefaultButton(criar_btn)
        msg.exec()
        return msg.clickedButton() is criar_btn

    def _ensure_output_directories(self, jobs):
        checked_dirs = set()

        for job in jobs:
            output_path = getattr(job, "output_path", None)
            if not output_path:
                output_path = generate_output_path(job.source_path, job=job)
            output_dir = os.path.dirname(output_path)
            # QFileDialog/Qt may return a source path with mixed separators
            # and a trailing separator. Normalize before checking or creating
            # the directory so the exact filesystem path is used consistently.
            output_dir = os.path.normpath(output_dir)

            if not output_dir or output_dir in checked_dirs:
                continue

            checked_dirs.add(output_dir)

            if os.path.exists(output_dir):
                continue

            if not self._ask_create_directory(output_dir):
                return False

            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception as exc:
                QMessageBox.critical(
                    self.file_list,
                    "Falha ao criar pasta",
                    f"Não foi possível criar a pasta de destino.\n\n{self._display_path(output_dir)}\n\nErro: {exc}",
                )
                return False

        return True

    def _filter_runnable_jobs(self, jobs):
        runnable = []
        for job in jobs:
            if not job:
                continue
            if str(getattr(job, "status", "") or "").upper() == "NO_GAIN" or estimated_output_is_actionable(job) is False:
                patch_job(job, status="NO_GAIN", progress=0)
                event_bridge.emit("job_updated", {"job": job})
                continue
            runnable.append(job)
        return runnable

    def run_jobs(self, jobs):
        if not jobs:
            return

        jobs = self._filter_runnable_jobs(jobs)
        if not jobs:
            return

        if not self._ensure_output_directories(jobs):
            return

        for job in jobs:
            event_bridge.emit("job_run_requested", {"job": job})

    def run_job(self, job):
        if not job:
            return
        self.run_jobs([job])

    # ------------------------
    # Compress
    # ------------------------

    def compress_selected(self):
        rows = self.selection_controller.get_selected_rows()
        self.run_jobs(self._collect_jobs(rows))

    def compress_all(self):
        if not self.file_list:
            return

        model = self.file_list.model()
        self.run_jobs(self._collect_jobs(range(model.rowCount())))

    # ------------------------
    # Cancel
    # ------------------------

    def cancel_selected(self):

        rows = self.selection_controller.get_selected_rows()

        if not rows:
            return

        model = self.file_list.model()

        for r in rows:
            index = model.index(r)
            job = model.data(index, FileListModel.ROLE_JOB_REF)

            if job:
                event_bridge.emit("job_cancel_requested", job)

    def cancel_all(self):
        event_bridge.emit("cancel_all_requested", None)


execution_controller = ExecutionController()
