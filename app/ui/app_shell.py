import os

from PySide6.QtCore import QEvent, QPoint, QRect, QThread, QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QVBoxLayout, QWidget

from app.core.app_version import APP_NAME, APP_VERSION
from app.interaction_model.event_bridge import event_bridge
from app.interaction_model.execution_controller import execution_controller
from app.ui.configuration_overlay import ConfigurationOverlay
from app.ui.app_preferences_dialog import AppPreferencesDialog
from app.ui.ancillary_dialogs import AboutDialog, HelpDialog
from app.ui.window_icon import apply_window_icon
from app.ui.execution_footer import ExecutionFooterWidget
from app.ui.file_list_container import FileListContainer
from app.ui.file_list_model import FileListModel
from app.ui.global_bar import GlobalBarWidget
from app.ui.import_batch_worker import ImportBatchRequest, ImportBatchWorker
from app.ui.global_progress import GlobalProgressWidget
from app.ui.selection_controller import SelectionController
from app.ui.theme_mode_controller import apply_configured_theme_mode
from app.ui.theme_tokens import build_theme_tokens
from app.ui.toast_manager import ToastManager
from app.ui.media_preview_dialog import MediaPreviewDialog
from app.ui.file_card_presenter import resolve_playable_output_path


def local_drop_paths(urls):
    """Return existing local files from dropped URL objects in input order."""
    return [
        path
        for url in urls
        if url.isLocalFile()
        and os.path.isfile(path := url.toLocalFile())
    ]


class AppShell(QWidget):

    def __init__(self, ctx):
        super().__init__()
        self.setAcceptDrops(True)

        self.ctx = ctx
        self.ctx.logger.info("AppShell initialized")

        event_bridge.subscribe(self._on_app_event)

        self._import_thread = None
        self._import_worker = None
        self._import_summary = None
        self._import_active = False
        self._shutdown_pending = False
        self._preferences_dialog = None
        self._configuration_overlay_open = False
        self._configuration_overlay_job = None
        self._configuration_overlay_explicit_open_guard = False
        self._configuration_overlay_stable_rect = QRect()
        self._configuration_overlay_stable_content_size = None
        self._configuration_overlay_theme_transition = False
        self._configuration_button_busy = False
        self._help_dialog = None
        self._about_dialog = None
        self._pending_import_job_overrides = None
        self._media_preview_dialog = None
        self._media_preview_theme_restart_pending = False

        self._build_ui()

        self.toast = ToastManager(self)

        apply_window_icon(self)
        self.update_window_title()

        self.execution_footer.btn_compress.clicked.connect(
            execution_controller.compress_selected
        )
        self.execution_footer.btn_compress_all.clicked.connect(
            execution_controller.compress_all
        )
        self.execution_footer.btn_cancel.clicked.connect(
            execution_controller.cancel_selected
        )
        self.execution_footer.btn_cancel_all.clicked.connect(
            execution_controller.cancel_all
        )

    def dragEnterEvent(self, event):
        mime_data = event.mimeData()
        if mime_data.hasUrls() and any(url.isLocalFile() for url in mime_data.urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = local_drop_paths(event.mimeData().urls())
        if paths:
            self.start_import_batch(paths=paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _build_ui(self):
        self.base_layout = QVBoxLayout(self)
        self.base_layout.setContentsMargins(0, 0, 0, 0)
        self.base_layout.setSpacing(0)

        self.global_bar = GlobalBarWidget(self)

        self.file_list_container = FileListContainer()
        exported_global_controls = self.global_bar.export_controls_for_title_bar()
        self.file_list_container.attach_top_bar_controls(
            import_widgets=exported_global_controls.get("import_buttons"),
            menu_widget=exported_global_controls.get("menu_button"),
        )
        self.context_bar = self.file_list_container.context_bar
        self.selection_bar = self.file_list_container.title_bar if hasattr(self.file_list_container, "title_bar") else None
        self.file_list = self.file_list_container.file_list
        self.file_list.setModel(FileListModel())
        self.file_list.add_requested.connect(self.global_bar.open_assisted_add_dialog)
        self.file_list.add_quick_requested.connect(self.global_bar.open_quick_add_dialog)
        self.file_list.import_folder_requested.connect(self.global_bar.open_import_folder_dialog)

        model = self.file_list.model()
        self.context_bar.set_model(model)

        try:
            model.dataChanged.connect(self._update_footer_state)
            model.rowsInserted.connect(self._update_footer_state)
            model.rowsRemoved.connect(self._update_footer_state)
            model.modelReset.connect(self._update_footer_state)
            model.dataChanged.connect(self._update_global_progress)
            model.rowsInserted.connect(self._update_global_progress)
            model.rowsRemoved.connect(self._update_global_progress)
            model.modelReset.connect(self._update_global_progress)
            model.rowsInserted.connect(self._update_selection_bar_state)
            model.rowsRemoved.connect(self._update_selection_bar_state)
            model.modelReset.connect(self._update_selection_bar_state)
        except Exception:
            pass

        self.selection_controller = SelectionController(self.file_list)
        execution_controller.set_context(self.file_list, self.selection_controller)

        try:
            selection_model = self.file_list.selectionModel()
            selection_model.selectionChanged.connect(self._update_selection_bar_state)
            selection_model.selectionChanged.connect(self._sync_configuration_overlay_context_from_selection)
        except Exception:
            pass

        try:
            self.file_list.verticalScrollBar().valueChanged.connect(
                self._sync_configuration_overlay_context_from_visible_card
            )
        except Exception:
            pass

        self.selection_bar.btn_select_all.clicked.connect(self._select_all_jobs)
        self.selection_bar.btn_config.clicked.connect(self._on_config_button_clicked)
        self.selection_bar.btn_delete.clicked.connect(self._remove_selected_jobs)

        self.execution_footer = ExecutionFooterWidget()
        self.global_progress = GlobalProgressWidget()

        self.content = QWidget()
        self.content.setObjectName("MainContent")
        self.content.setAutoFillBackground(True)

        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.main_container = QWidget()
        main_layout = QVBoxLayout(self.main_container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.middle_container = QWidget()
        middle_layout = QVBoxLayout(self.middle_container)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(6)
        middle_layout.addWidget(self.file_list_container, 1)

        main_layout.addWidget(self.middle_container)
        main_layout.addWidget(self.execution_footer)

        content_layout.addWidget(self.main_container)
        content_layout.addWidget(self.global_progress)


        self.configuration_overlay = ConfigurationOverlay(self.content)
        self.configuration_overlay.set_all_jobs_provider(self._get_all_jobs)
        self.configuration_overlay.accepted.connect(self._on_configuration_overlay_closed)
        self.configuration_overlay.rejected.connect(self._on_configuration_overlay_closed)

        self.base_layout.addWidget(self.content)

        for background_widget in (self, self.content, self.main_container, self.middle_container):
            background_widget.installEventFilter(self)

        self._apply_surface_styles()
        self._apply_preferences_state()
        self._update_footer_state()
        self._update_global_progress()
        self._update_selection_bar_state()

    def _apply_surface_styles(self):
        tokens = build_theme_tokens(self.palette())
        main_content_bg = tokens.surface_main.name()
        panel_bg = tokens.surface_panel.name()
        panel_border = tokens.border_panel.name()


        self.setStyleSheet(
            f"""
            QWidget#MainContent {{
                background: {main_content_bg};
                border: none;
            }}

            QFrame#ExecutionFooterWidget {{
                background: {panel_bg};
                border: 1px solid {panel_border};
                border-radius: 4px;
                padding: 2px;
            }}

            """
        )

    def _apply_preferences_state(self):
        from app.ancillary.configuration import ConfigurationService

        cfg = ConfigurationService.instance().get()
        apply_configured_theme_mode()
        self._apply_surface_styles()
        self._refresh_widget_styles()
        self.global_progress.setVisible(getattr(cfg, "show_global_progress", True))
        self._update_global_progress()
        self._sync_configuration_overlay_geometry()
        self._restart_media_preview_after_theme_change_if_open()

    def update_window_title(self):
        self.setWindowTitle(f"{APP_NAME} — v{APP_VERSION}")

    def eventFilter(self, watched, event):
        if watched in {self, self.content, self.main_container, self.middle_container}:
            if event.type() == QEvent.Type.MouseButtonPress:
                event_bridge.emit("clear_selection_requested", None)
        return super().eventFilter(watched, event)

    def _finish_configuration_overlay_theme_transition(self):
        if (
            (self._configuration_overlay_open or self.configuration_overlay.isVisible())
            and not self._configuration_overlay_stable_rect.isNull()
        ):
            rect = QRect(self._configuration_overlay_stable_rect)
            self.configuration_overlay.sync_to_rect(rect)
            self._update_file_list_overlay_inset(rect)
        self._configuration_overlay_theme_transition = False
        try:
            self.configuration_overlay.setUpdatesEnabled(True)
            self.configuration_overlay.update()
        except Exception:
            pass
        self._sync_configuration_overlay_geometry()

    def _force_configuration_overlay_theme_rect(self):
        if (
            (self._configuration_overlay_open or self.configuration_overlay.isVisible())
            and not self._configuration_overlay_stable_rect.isNull()
        ):
            rect = QRect(self._configuration_overlay_stable_rect)
            self.configuration_overlay.sync_to_rect(rect)
            self._update_file_list_overlay_inset(rect)

    def resizeEvent(self, event):
        overlay_visible = self._configuration_overlay_open or self.configuration_overlay.isVisible()
        if overlay_visible and self._configuration_overlay_theme_transition:
            if not self._configuration_overlay_stable_rect.isNull():
                self.configuration_overlay.sync_to_rect(QRect(self._configuration_overlay_stable_rect))
                self._update_file_list_overlay_inset(QRect(self._configuration_overlay_stable_rect))
            super().resizeEvent(event)
            return

        if overlay_visible:
            current_size = self.content.size() if hasattr(self, "content") else self.size()
            stable_size = self._configuration_overlay_stable_content_size
            if (
                stable_size is None
                or abs(current_size.width() - stable_size.width()) > 2
                or abs(current_size.height() - stable_size.height()) > 2
            ):
                self._configuration_overlay_stable_rect = QRect()
                self._configuration_overlay_stable_content_size = current_size
        self._sync_configuration_overlay_geometry()
        super().resizeEvent(event)

    def changeEvent(self, event):
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.ThemeChange,
        ):
            overlay_visible = self._configuration_overlay_open or self.configuration_overlay.isVisible()
            if overlay_visible and not self._configuration_overlay_stable_rect.isNull():
                self._configuration_overlay_theme_transition = True
                self.configuration_overlay.setUpdatesEnabled(False)
                self.configuration_overlay.sync_to_rect(QRect(self._configuration_overlay_stable_rect))
            self._handle_palette_change()
            if overlay_visible and not self._configuration_overlay_stable_rect.isNull():
                self.configuration_overlay.sync_to_rect(QRect(self._configuration_overlay_stable_rect))
                self._update_file_list_overlay_inset(QRect(self._configuration_overlay_stable_rect))
            QTimer.singleShot(0, self._force_configuration_overlay_theme_rect)
            QTimer.singleShot(35, self._finish_configuration_overlay_theme_transition)
            QTimer.singleShot(160, self._restart_media_preview_after_theme_change_if_open)
        super().changeEvent(event)

    def start_import_batch(self, *, paths=None, folder=None, job_overrides=None):
        if self._import_thread is not None:
            self.toast.show("Importação já está em andamento")
            return

        if self._configuration_overlay_open or self.configuration_overlay.isVisible():
            self.close_configuration()

        request = ImportBatchRequest(paths=list(paths or []), folder=folder)
        if isinstance(job_overrides, dict) and any(isinstance(value, dict) for value in job_overrides.values()):
            self._pending_import_job_overrides = dict(job_overrides)
        else:
            self._pending_import_job_overrides = dict(job_overrides or {}) or None
        self._import_summary = None
        self._import_active = True
        self._set_import_busy_state(True)

        self._import_thread = QThread(self)
        self._import_worker = ImportBatchWorker(request)
        self._import_worker.moveToThread(self._import_thread)

        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.progress.connect(self._on_import_progress)
        self._import_worker.valid_found.connect(self._on_import_valid_found)
        self._import_worker.finished.connect(self._on_import_finished)
        self._import_worker.finished.connect(self._import_thread.quit)
        self._import_thread.finished.connect(self._cleanup_import_worker)

        self._import_thread.start()

    def _set_import_busy_state(self, active: bool):
        self.global_bar.set_import_controls_enabled(not active)
        if active and hasattr(self, "global_progress"):
            self.global_progress.set_idle()

        if hasattr(self.global_bar, "menu_button"):
            self.global_bar.menu_button.setEnabled(not active)

        self.context_bar.set_import_state(active, 0, 0, 0, 0)
        self.file_list.setProperty("importing", active)

        self._update_footer_state()
        self._update_selection_bar_state()

        if active:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        else:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()

        QApplication.processEvents()

    def _on_import_progress(self, current: int, total: int, valid: int, invalid: int):
        self.context_bar.set_import_state(True, current, total, valid, invalid)

    def _on_import_valid_found(self, path, probe_result, source_size):
        overrides = self._pending_import_job_overrides
        if isinstance(overrides, dict) and path in overrides and isinstance(overrides.get(path), dict):
            overrides = overrides.get(path)
        self.global_bar.create_job_from_probe_result(path, probe_result, source_size, job_overrides=overrides)

    def _on_import_finished(self, summary: dict):
        self._import_summary = dict(summary or {})
        self._import_active = False
        self._shutdown_pending = False
        self._preferences_dialog = None
        self._configuration_overlay_open = False
        self._help_dialog = None
        self._about_dialog = None
        self._pending_import_job_overrides = None
        self._set_import_busy_state(False)

        invalid = int(self._import_summary.get("invalid", 0) or 0)
        total = int(self._import_summary.get("total", 0) or 0)

        if total == 0:
            self.toast.show("Nenhum arquivo compatível foi encontrado")
            return

        self._maybe_auto_open_configuration_overlay(total)

        if invalid <= 0:
            return

        invalid_names = list(self._import_summary.get("invalid_names", []) or [])
        preview = "\n".join(f"• {name}" for name in invalid_names[:8])
        extra = invalid - min(invalid, 8)
        if extra > 0:
            preview += f"\n• e mais {extra}"

        message = f"{invalid} arquivo(s) inválido(s) foram ignorado(s)."
        if preview:
            message += f"\n\n{preview}"

        QMessageBox.warning(self, "Importação concluída com avisos", message)

    def _cleanup_import_worker(self):
        if self._import_worker is not None:
            try:
                self._import_worker.deleteLater()
            except Exception:
                pass
        if self._import_thread is not None:
            try:
                self._import_thread.deleteLater()
            except Exception:
                pass
        self._import_worker = None
        self._import_thread = None
        self._import_active = False
        self._shutdown_pending = False
        self._preferences_dialog = None
        self._configuration_overlay_open = False
        self._help_dialog = None
        self._about_dialog = None
        self._pending_import_job_overrides = None
        self.context_bar.set_import_state(False)
        self._update_footer_state()
        self._update_selection_bar_state()

    def _maybe_auto_open_configuration_overlay(self, total):
        if total <= 0:
            return
        try:
            from app.ancillary.configuration import ConfigurationService

            cfg = ConfigurationService.instance().get()
            should_open = getattr(
                cfg,
                "auto_open_profile_settings_after_import",
                getattr(cfg, "auto_open_configuration_after_import", False),
            )
            if should_open:
                self.open_configuration()
        except Exception:
            pass

    def close_configuration(self):
        self._configuration_overlay_stable_rect = QRect()
        self._configuration_overlay_stable_content_size = None
        self._configuration_overlay_theme_transition = False
        try:
            self.configuration_overlay.setUpdatesEnabled(True)
        except Exception:
            pass
        self.configuration_overlay.close_overlay()

    def _show_modal_dialog(self, attr_name, dialog_cls):
        dialog = getattr(self, attr_name, None)
        if dialog is None:
            dialog = dialog_cls(self)
            setattr(self, attr_name, dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.exec()

    def open_preferences(self):
        if self._preferences_dialog is not None:
            try:
                self._preferences_dialog.deleteLater()
            except Exception:
                pass
            self._preferences_dialog = None
        self._show_modal_dialog("_preferences_dialog", AppPreferencesDialog)

    def _set_config_button_busy(self, busy: bool):
        self._configuration_button_busy = bool(busy)
        button = getattr(getattr(self, "selection_bar", None), "btn_config", None)
        if button is None:
            return
        if busy:
            button.setText("Abrindo...")
            button.setEnabled(False)
            button.setToolTip("Abrindo configurações...")
        else:
            button.setText("Configurar")
            button.setToolTip("Abrir configurações")
            self._update_selection_bar_state()

    def _on_config_button_clicked(self, checked=False):
        if self._configuration_button_busy or self._is_configuration_blocked_by_analysis():
            return
        self._set_config_button_busy(True)
        app = QApplication.instance()
        if app is not None:
            app.setOverrideCursor(Qt.CursorShape.WaitCursor)
            app.processEvents()
        try:
            self.open_configuration()
        finally:
            if app is not None:
                app.restoreOverrideCursor()
            QTimer.singleShot(120, lambda: self._set_config_button_busy(False))

    def open_configuration(self, job=None):
        if self._shutdown_pending or self._is_importing():
            return

        explicit_job = job is not None
        if explicit_job and self._is_job_analyzing(job):
            self._update_selection_bar_state()
            try:
                self.toast.show("Aguarde a análise da mídia terminar", tone="warning")
            except Exception:
                pass
            return
        if not explicit_job and self._is_configuration_blocked_by_analysis():
            self._update_selection_bar_state()
            return

        if job is None:
            try:
                selected_jobs = self._get_selected_jobs()
            except Exception:
                selected_jobs = []
            if selected_jobs:
                job = selected_jobs[0]
            else:
                all_jobs = self._get_all_jobs()
                if all_jobs:
                    job = all_jobs[0]

        selected_count = self._resolve_configuration_overlay_selection_count(job)
        same_context = (
            self._configuration_overlay_open
            and self.configuration_overlay.isVisible()
            and job is self._configuration_overlay_job
        )

        if not same_context:
            self._configuration_overlay_job = job
            if job is None:
                self.configuration_overlay.clear_context_job()
            else:
                self.configuration_overlay.set_context_job(job, selected_count=selected_count)

        rect = self._build_configuration_overlay_rect()
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        self._configuration_overlay_open = True
        self._set_configuration_overlay_state(True)

        if explicit_job:
            self._configuration_overlay_explicit_open_guard = True
            self._scroll_job_to_top_for_configuration_overlay(job)

        self._configuration_overlay_stable_rect = QRect(rect)
        self._configuration_overlay_stable_content_size = self.content.size() if hasattr(self, "content") else self.size()
        self._update_file_list_overlay_inset(rect)

        if explicit_job:
            self._scroll_job_to_top_for_configuration_overlay(job)

        self.configuration_overlay.open_overlay(rect)

        if explicit_job:
            QTimer.singleShot(200, self._release_configuration_overlay_explicit_open_guard)

    def _release_configuration_overlay_explicit_open_guard(self):
        self._configuration_overlay_explicit_open_guard = False

    def _scroll_job_to_top_for_configuration_overlay(self, job):
        if job is None:
            return

        model = self.file_list.model()
        if model is None:
            return

        try:
            row_count = int(model.rowCount() or 0)
        except Exception:
            return

        for row in range(row_count):
            index = model.index(row, 0)
            if not index.isValid():
                continue

            try:
                candidate = model.data(index, FileListModel.ROLE_JOB_REF)
            except Exception:
                candidate = None

            if candidate is not job:
                continue

            try:
                scroll_bar = self.file_list.verticalScrollBar()
                step = self.file_list._card_scroll_step()
                scroll_bar.setValue(max(0, row * max(1, step)))
            except Exception:
                pass

            try:
                from PySide6.QtWidgets import QAbstractItemView
                self.file_list.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtTop)
            except Exception:
                pass

            try:
                self.file_list.updateGeometries()
                self.file_list.viewport().update()
            except Exception:
                pass
            return

    def _resolve_configuration_overlay_selection_count(self, job):
        if job is None:
            return 0

        try:
            selected_jobs = self._get_selected_jobs()
        except Exception:
            selected_jobs = []

        if not selected_jobs:
            return 1

        if any(selected is job for selected in selected_jobs):
            return len(selected_jobs)

        return 1

    def _set_configuration_overlay_context_job(self, job):
        if job is None or job is self._configuration_overlay_job:
            return

        self._configuration_overlay_job = job
        selected_count = self._resolve_configuration_overlay_selection_count(job)
        self.configuration_overlay.set_context_job(job, selected_count=selected_count)

    def _sync_configuration_overlay_context_from_selection(self, *args):
        if getattr(self, "_configuration_overlay_explicit_open_guard", False):
            return

        if not (self._configuration_overlay_open and self.configuration_overlay.isVisible()):
            return

        try:
            selected_jobs = self._get_selected_jobs()
        except Exception:
            selected_jobs = []

        # Only migrate the overlay to an unambiguous user target.
        # This keeps multi-select from accidentally retargeting the profile panel,
        # while allowing a single clicked/selected card to become the active card
        # without requiring the profile button to be clicked again.
        if len(selected_jobs) != 1:
            return

        self._set_configuration_overlay_context_job(selected_jobs[0])

    def _get_visible_overlay_anchor_job(self):
        model = self.file_list.model()
        if model is None:
            return None

        row_count = int(model.rowCount() or 0)
        if row_count <= 0:
            return None

        # Prefer the snapped card boundary, because overlay mode scrolls one full
        # card per wheel step. This keeps the overlay profile synchronized with
        # the card visually occupying the top slot of the available list area.
        try:
            scroll_bar = self.file_list.verticalScrollBar()
            step = self.file_list._card_scroll_step()
            row = int((int(scroll_bar.value()) + (step // 2)) // max(1, step))
            row = max(0, min(row_count - 1, row))
            index = model.index(row, 0)
            job = model.data(index, FileListModel.ROLE_JOB_REF)
            if job is not None:
                return job
        except Exception:
            pass

        # Fallback for scrollbar dragging or any future non-snapped scroll mode:
        # ask the view which card is under the top visible card area.
        try:
            probe_x = max(1, self.file_list.viewport().width() // 2)
            for probe_y in (8, 16, 28, 44, max(8, self.file_list.viewport().height() // 3)):
                index = self.file_list.indexAt(QPoint(probe_x, int(probe_y)))
                if index.isValid():
                    job = model.data(index, FileListModel.ROLE_JOB_REF)
                    if job is not None:
                        return job
        except Exception:
            pass

        return None

    def _sync_configuration_overlay_context_from_visible_card(self, *args):
        if getattr(self, "_configuration_overlay_explicit_open_guard", False):
            return

        if not (self._configuration_overlay_open and self.configuration_overlay.isVisible()):
            return

        try:
            selected_jobs = self._get_selected_jobs()
        except Exception:
            selected_jobs = []

        # Preserve the step160 safety rule: multi-selection does not retarget the
        # overlay automatically. With zero or one selected card, scrolling can
        # make the card shown in the list become the overlay target.
        if len(selected_jobs) > 1:
            return

        self._set_configuration_overlay_context_job(self._get_visible_overlay_anchor_job())

    def open_help(self):
        self._show_modal_dialog("_help_dialog", HelpDialog)

    def open_about(self):
        self._show_modal_dialog("_about_dialog", AboutDialog)


    @staticmethod
    def _is_job_analyzing(job) -> bool:
        return str(getattr(job, "status", "") or "").strip().upper() == "ANALYZING"

    def _has_analyzing_jobs(self) -> bool:
        model = self.file_list.model() if getattr(self, "file_list", None) is not None else None
        if model is None:
            return False
        try:
            total = int(model.rowCount() or 0)
        except Exception:
            return False
        for row in range(total):
            try:
                index = model.index(row)
                job = model.data(index, FileListModel.ROLE_JOB_REF)
            except Exception:
                job = None
            if self._is_job_analyzing(job):
                return True
        return False

    def _is_configuration_blocked_by_analysis(self) -> bool:
        return self._has_analyzing_jobs()

    def _has_active_processing_jobs(self):
        model = self.file_list.model()
        total = model.rowCount()

        for r in range(total):
            index = model.index(r)
            job = model.data(index, FileListModel.ROLE_JOB_REF)
            if job and getattr(job, "status", None) in ("PROCESSING", "RUNNING"):
                return True

        run_controller = getattr(self.ctx, "run_controller", None)
        if not run_controller:
            return False

        if getattr(run_controller, "jobs", None):
            return True

        queue = getattr(run_controller, "_queue", None)
        if queue:
            return len(queue) > 0

        return False

    def _allow_exit_during_processing(self):
        try:
            from app.ancillary.configuration import ConfigurationService
            cfg = ConfigurationService.instance().get()
            return bool(getattr(cfg, "allow_exit_during_processing", False))
        except Exception:
            return False

    def _warn_exit_while_processing_blocked(self):
        QMessageBox.warning(
            self,
            "Processamento em andamento",
            "Há compressão em andamento. Aguarde terminar ou cancele o processamento antes de sair.",
        )

    def _request_shutdown_while_processing(self):
        if not self._allow_exit_during_processing():
            self._warn_exit_while_processing_blocked()
            return
        if self._confirm_shutdown_while_processing():
            self._begin_shutdown_protocol()

    def _confirm_shutdown_while_processing(self):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("Encerrar durante processamento")
        msg.setText("Há compressão em andamento.\nDeseja sair mesmo assim?")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)

        yes_button = msg.button(QMessageBox.Yes)
        no_button = msg.button(QMessageBox.No)

        if yes_button is not None:
            yes_button.setText("Sim")
        if no_button is not None:
            no_button.setText("Não")

        return msg.exec() == QMessageBox.Yes

    def _warn_clear_while_processing(self):
        QMessageBox.warning(
            self,
            "Limpar durante processamento",
            "Há compressão em andamento. Cancele o processamento antes de limpar a lista.",
        )

    def _warn_clear_while_importing(self):
        QMessageBox.warning(
            self,
            "Importação em andamento",
            "A lista está sendo importada no momento. Aguarde a importação terminar antes de limpar a lista.",
        )

    def _is_importing(self):
        return bool(self._import_active)

    def _set_shutdown_ui_state(self, active):
        self._shutdown_pending = bool(active)
        if hasattr(self.global_bar, "menu_button"):
            self.global_bar.menu_button.setEnabled(not active)
        self.file_list.setEnabled(not active)
        self.selection_bar.setEnabled(not active)
        self.execution_footer.btn_compress.setEnabled(not active)
        self.execution_footer.btn_compress_all.setEnabled(not active)
        self.execution_footer.btn_clear_all.setEnabled(not active)

    def _begin_shutdown_protocol(self):
        if self._shutdown_pending:
            return
        self._set_shutdown_ui_state(True)
        event_bridge.emit("shutdown_prepare_requested", None)

    def _complete_shutdown_if_idle(self):
        if self._shutdown_pending and not self._has_active_processing_jobs():
            self.close()

    def _stop_run_controller(self) -> bool:
        """Finalize the idle controller before Qt tears down its runtime."""
        controller = getattr(self.ctx, "run_controller", None)
        if controller is None:
            return True
        try:
            return bool(controller.shutdown())
        except Exception:
            self.ctx.logger.exception("event=run_controller_shutdown_failed")
            return False

    def _finalize_idle_close(self, event) -> bool:
        if not self._stop_run_controller():
            event.ignore()
            return False
        event_bridge.unsubscribe(self._on_app_event)
        return True

    def _set_configuration_overlay_state(self, active):
        # The configuration overlay no longer blocks footer actions.
        # Keep only the real shutdown lock here; per-button availability
        # continues to be controlled by _update_footer_state().
        self.execution_footer.setEnabled(not self._shutdown_pending)


    def _update_file_list_overlay_inset(self, overlay_rect=None):
        inset = 0

        if overlay_rect is not None and hasattr(self, "file_list_container") and hasattr(self, "file_list"):
            file_list_top_left = self.file_list.mapTo(self.content, self.file_list.rect().topLeft())
            file_list_rect = QRect(file_list_top_left, self.file_list.size())
            overlap_rect = file_list_rect.intersected(QRect(overlay_rect))
            if not overlap_rect.isNull():
                # Keep the list scrollable only inside the area that remains fully
                # visible above the overlay, but avoid a large artificial buffer.
                # A compact clearance preserves the validated snap/scroll behavior
                # while reducing the empty strip between the last visible card and
                # the overlay.
                inset = max(0, overlap_rect.height() + 4)

        self.file_list_container.set_file_list_bottom_inset(inset)

    def _on_configuration_overlay_closed(self):
        self._configuration_overlay_open = False
        self._configuration_overlay_job = None
        self._configuration_overlay_stable_rect = QRect()
        self._configuration_overlay_stable_content_size = None
        self._configuration_overlay_theme_transition = False
        try:
            self.configuration_overlay.setUpdatesEnabled(True)
        except Exception:
            pass
        self.configuration_overlay.clear_context_job()
        self._set_configuration_overlay_state(False)
        self._update_file_list_overlay_inset(None)
        self._update_footer_state()

    def _build_configuration_overlay_rect(self):
        if not hasattr(self, "configuration_overlay") or not hasattr(self, "execution_footer"):
            return QRect()

        footer_top_left = self.execution_footer.mapTo(self.content, self.execution_footer.rect().topLeft())
        footer_rect = QRect(footer_top_left, self.execution_footer.size())

        if footer_rect.width() <= 0 or footer_rect.height() <= 0:
            return QRect()

        # While the configuration overlay is open, the profile controls are the
        # active task and the execution footer buttons are secondary. Let the
        # overlay extend across the footer area so profile content can keep its
        # native vertical rhythm even when the video format row is shown inside
        # the content area, matching the Audio profile pattern.
        # The overlay intentionally covers the execution footer while open.
        # Do not leave a clearance here: even a small gap renders as the
        # thick light strip reported below the overlay in compact windows.
        footer_gap = 0
        bottom_y = min(self.content.height(), footer_rect.bottom() + 1)
        if self.global_progress.isVisible():
            progress_top_left = self.global_progress.mapTo(self.content, self.global_progress.rect().topLeft())
            bottom_y = min(bottom_y, progress_top_left.y())

        top_limit = self.global_bar.geometry().bottom() + 6
        available_height = bottom_y - top_limit
        if available_height <= 0:
            return QRect()

        preferred_height = max(260, int(self.content.height() * 0.70))
        height = min(preferred_height, available_height)

        x = footer_rect.x()
        width = footer_rect.width()

        y = bottom_y - height
        return QRect(x, y, width, height)

    def _sync_configuration_overlay_geometry(self):
        if not hasattr(self, "configuration_overlay"):
            return
        overlay_visible = self._configuration_overlay_open or self.configuration_overlay.isVisible()
        if overlay_visible and not self._configuration_overlay_stable_rect.isNull():
            rect = QRect(self._configuration_overlay_stable_rect)
            if not self._configuration_overlay_theme_transition:
                current_rect = self._build_configuration_overlay_rect()
                current_size = self.content.size() if hasattr(self, "content") else self.size()
                stable_size = self._configuration_overlay_stable_content_size

                # Preserve exact geometry across palette/theme changes. Recompute only
                # for real work-area changes, not for stylesheet/palette metric jitter.
                material_size_change = (
                    stable_size is None
                    or abs(current_size.width() - stable_size.width()) > 2
                    or abs(current_size.height() - stable_size.height()) > 2
                )
                material_width_change = (
                    current_rect.isNull()
                    or abs(current_rect.width() - rect.width()) > 2
                    or abs(current_rect.x() - rect.x()) > 2
                )
                if material_size_change or material_width_change:
                    rect = current_rect
                    self._configuration_overlay_stable_rect = QRect(rect) if not rect.isNull() else QRect()
                    self._configuration_overlay_stable_content_size = current_size if not rect.isNull() else None
        else:
            rect = self._build_configuration_overlay_rect()
            if overlay_visible and not rect.isNull():
                self._configuration_overlay_stable_rect = QRect(rect)

        if rect.isNull():
            self._update_file_list_overlay_inset(None)
            return
        if overlay_visible:
            self.configuration_overlay.sync_to_rect(rect)
            self._update_file_list_overlay_inset(rect)
        else:
            self._update_file_list_overlay_inset(None)

    def closeEvent(self, event):
        if self._import_worker is not None:
            try:
                self._import_worker.cancel()
            except Exception:
                pass

        if self._shutdown_pending:
            if self._has_active_processing_jobs():
                event.ignore()
                return
            if self._finalize_idle_close(event):
                super().closeEvent(event)
            return

        if self._has_active_processing_jobs():
            if not self._allow_exit_during_processing():
                self._warn_exit_while_processing_blocked()
            elif self._confirm_shutdown_while_processing():
                self._begin_shutdown_protocol()
            event.ignore()
            return
        if self._finalize_idle_close(event):
            super().closeEvent(event)

    def _open_path_in_file_manager(self, folder_path: str, *, select_path: str | None = None) -> bool:
        import os
        import subprocess
        import sys

        if not folder_path:
            return False
        folder_path = os.path.normpath(folder_path)
        if not os.path.isdir(folder_path):
            return False

        try:
            if sys.platform.startswith("win"):
                if select_path:
                    select_path = os.path.normpath(select_path)
                    if os.path.exists(select_path):
                        explorer = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
                        if not os.path.exists(explorer):
                            explorer = "explorer.exe"
                        subprocess.Popen([explorer, "/select,", select_path])
                        return True
                os.startfile(folder_path)
                return True
            if sys.platform == "darwin":
                subprocess.Popen(["open", folder_path])
                return True
            subprocess.Popen(["xdg-open", folder_path])
            return True
        except Exception as exc:
            try:
                self.ctx.logger.warning(f"Could not open folder: {folder_path} ({exc})")
            except Exception:
                pass
            return False

    def _open_job_output_folder(self, job) -> None:
        import os

        if not job:
            return

        output_path = getattr(job, "output_path", None)
        destination_dir = getattr(job, "destination_dir", None)
        source_path = getattr(job, "source_path", None) or getattr(job, "input_path", None)

        candidates = []

        if output_path:
            output_path = os.path.normpath(str(output_path))
            if os.path.exists(output_path):
                candidates.append((os.path.dirname(output_path) or output_path, output_path, False))
            else:
                output_dir = os.path.dirname(output_path)
                if output_dir:
                    candidates.append((output_dir, None, True))

        if destination_dir:
            candidates.append((os.path.normpath(str(destination_dir)), None, True))

        if source_path:
            source_path = os.path.normpath(str(source_path))
            if os.path.exists(source_path):
                candidates.append((os.path.dirname(source_path), source_path, False))
            else:
                source_dir = os.path.dirname(source_path)
                if source_dir:
                    candidates.append((source_dir, None, False))

        seen = set()
        for folder, select_path, allow_create in candidates:
            if not folder:
                continue
            folder = os.path.normpath(folder)
            key = folder.lower() if os.name == "nt" else folder
            if key in seen:
                continue
            seen.add(key)

            if not os.path.isdir(folder) and allow_create:
                try:
                    os.makedirs(folder, exist_ok=True)
                except Exception as exc:
                    try:
                        self.ctx.logger.warning(f"Could not create output folder: {folder} ({exc})")
                    except Exception:
                        pass

            if self._open_path_in_file_manager(folder, select_path=select_path):
                return

        try:
            self.toast.show("Não foi possível abrir a pasta")
        except Exception:
            pass

    def _open_source_in_system_player(self, path: str) -> None:
        import os
        import subprocess
        import sys

        if not path:
            return
        path = os.path.normpath(path)
        if not os.path.exists(path):
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    def _show_media_preview_dialog(self, path: str, *, geometry: QRect | None = None, resume_position_ms: int = 0, resume_playing: bool = False, playback_rate: float = 1.0, volume_percent: int = 65) -> None:
        import os

        if not path:
            return
        path = os.path.normpath(path)
        if not os.path.exists(path):
            return

        dialog = MediaPreviewDialog(path, self, resume_position_ms=resume_position_ms, resume_playing=resume_playing, playback_rate=playback_rate, volume_percent=volume_percent)
        dialog.fallback_requested.connect(self._open_source_in_system_player)
        dialog.destroyed.connect(lambda *_args: setattr(self, "_media_preview_dialog", None))
        self._media_preview_dialog = dialog
        if geometry is not None and geometry.isValid():
            dialog.setGeometry(geometry)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _restart_media_preview_after_theme_change_if_open(self) -> None:
        import os

        if self._media_preview_theme_restart_pending:
            return
        dialog = self._media_preview_dialog
        if dialog is None or not dialog.isVisible():
            return
        path = os.path.normpath(getattr(dialog, "media_path", "") or "")
        if not path or not os.path.exists(path):
            return

        try:
            geometry = QRect(dialog.geometry())
        except Exception:
            geometry = None
        try:
            resume_position_ms = max(0, int(dialog.current_position_ms()))
        except Exception:
            resume_position_ms = 0
        try:
            resume_playing = bool(dialog.is_playing())
        except Exception:
            resume_playing = False
        try:
            playback_rate = float(dialog.current_playback_rate())
        except Exception:
            playback_rate = 1.0
        try:
            volume_percent = int(dialog.current_volume_percent())
        except Exception:
            volume_percent = 65

        self._media_preview_theme_restart_pending = True

        try:
            dialog.close()
        except Exception:
            pass

        def _reopen():
            self._media_preview_theme_restart_pending = False
            self._show_media_preview_dialog(
                path,
                geometry=geometry,
                resume_position_ms=resume_position_ms,
                resume_playing=resume_playing,
                playback_rate=playback_rate,
                volume_percent=volume_percent,
            )

        # Do not restyle QVideoWidget in-place. Let Qt finish the app palette
        # transition, destroy the old native video surface, then create a fresh
        # preview dialog that receives the new static light/dark stylesheet.
        QTimer.singleShot(120, _reopen)

    def _open_media_preview_path(self, path: str | None) -> None:
        import os

        if not path:
            return
        path = os.path.normpath(path)
        if not os.path.exists(path):
            return

        try:
            if self._media_preview_dialog is not None:
                self._media_preview_dialog.close()
        except Exception:
            pass

        self._show_media_preview_dialog(path)

    def _open_media_preview(self, job) -> None:
        if not job:
            return
        self._open_media_preview_path(getattr(job, "source_path", None))

    def _open_output_media_preview(self, job) -> None:
        path = resolve_playable_output_path(job)
        if not path:
            self.toast.show("Arquivo gerado não encontrado")
            return
        self._open_media_preview_path(path)

    def _show_job_failure_feedback(self, payload) -> None:
        if not payload:
            return
        job = payload.get("job") if isinstance(payload, dict) else None
        reason = payload.get("error") if isinstance(payload, dict) else None
        reason = str(reason or getattr(job, "error", "") or "").strip()
        if not reason:
            reason = "motivo não informado"

        name = str(
            getattr(job, "file_name", None)
            or getattr(job, "name", None)
            or getattr(job, "source_path", None)
            or "arquivo"
        )
        try:
            import os
            name = os.path.basename(name) or name
        except Exception:
            pass

        max_name_len = 42
        max_reason_len = 110
        if len(name) > max_name_len:
            name = name[: max_name_len - 1].rstrip() + "…"
        if len(reason) > max_reason_len:
            reason = reason[: max_reason_len - 1].rstrip() + "…"

        self.toast.show(f"Falha em {name}: {reason}")

    def _on_app_event(self, event_type, payload):
        if event_type in {"job_enqueued", "job_updated", "job_progress", "job_finished", "job_failed"}:
            self._update_footer_state()
            self._update_global_progress()
            self._update_selection_bar_state()

        if event_type == "shutdown_requested":
            if self._has_active_processing_jobs():
                self._request_shutdown_while_processing()
            else:
                self.close()

        elif event_type == "clear_all_requested":
            if self._is_importing():
                self._warn_clear_while_importing()
                return
            if self._has_active_processing_jobs():
                self._warn_clear_while_processing()
                return
            event_bridge.emit("clear_all_jobs", None)
            self._update_global_progress()
            if self._configuration_overlay_open or self.configuration_overlay.isVisible():
                self.close_configuration()

        elif event_type == "shutdown_idle_reached":
            self._complete_shutdown_if_idle()

        elif event_type == "job_failed":
            self._show_job_failure_feedback(payload)

        elif event_type == "app_preferences_changed":
            self._apply_preferences_state()

        elif event_type == "configuration_changed":
            self._sync_configuration_overlay_geometry()

        elif event_type == "job_remove_requested":
            if self._configuration_overlay_open and payload is not None and payload is self._configuration_overlay_job:
                self.close_configuration()

        elif event_type == "job_settings_requested":
            job = payload
            if not job:
                return
            self.open_configuration(job)

        elif event_type == "duplicate_files_ignored":
            count = payload.get("count", 0)
            if count <= 0:
                return
            msg = "Arquivo já estava na lista" if count == 1 else f"{count} arquivos já estavam na lista"
            self.toast.show(msg)

        elif event_type == "job_play_source_requested":
            self._open_media_preview(payload)

        elif event_type == "job_play_output_requested":
            self._open_output_media_preview(payload)

        elif event_type == "job_open_folder_requested":
            self._open_job_output_folder(payload)

        elif event_type == "job_replace_folder_requested":
            job = payload
            if not job:
                return

            import os

            current_path = getattr(job, "output_path", None) or getattr(job, "source_path", None) or ""
            current_dir = current_path
            if current_dir and os.path.splitext(current_dir)[1]:
                current_dir = os.path.dirname(current_dir)
            if not current_dir or not os.path.isdir(current_dir):
                source_path = getattr(job, "source_path", None) or ""
                current_dir = os.path.dirname(source_path) if source_path else ""

            selected_dir = QFileDialog.getExistingDirectory(self, "Selecionar pasta de saída", current_dir)
            if not selected_dir:
                return

            filename = os.path.basename(current_path) if current_path else ""
            new_output_path = os.path.join(selected_dir, filename) if filename else selected_dir
            job.output_path = new_output_path

            model = self.file_list.model()
            if model is not None:
                try:
                    model.layoutChanged.emit()
                except Exception:
                    try:
                        model.modelReset.emit()
                    except Exception:
                        pass

            self.file_list.viewport().update()
            self.toast.show("Pasta de saída atualizada")

        elif event_type == "files_dropped":
            paths = payload.get("paths", []) if payload else []
            if not paths:
                return

            self.start_import_batch(paths=paths)

    def _global_progress_contribution(self, job, status):
        terminal_statuses = {"DONE", "FINISHED", "COMPLETED", "CANCELLED", "FAILED", "ERROR"}
        waiting_statuses = {"QUEUED", "ANALYZING"}
        active_statuses = {"PROCESSING", "RUNNING"}

        if status in terminal_statuses:
            return 100
        if status in waiting_statuses:
            return 0
        if status in active_statuses:
            try:
                progress = int(float(getattr(job, "progress", 0) or 0))
            except Exception:
                progress = 0
            return max(0, min(100, progress))
        return None

    def _global_progress_job_name(self, job):
        for attr in ("file_name", "name", "source_path", "input_path", "output_path"):
            value = str(getattr(job, attr, "") or "").strip()
            if not value:
                continue
            if attr.endswith("_path"):
                value = os.path.basename(value)
            if value:
                return value
        return ""

    def _build_global_progress_status_text(self, active_job, active_position, total_tracked):
        if active_job is None:
            return ""

        file_name = self._global_progress_job_name(active_job)
        if not file_name:
            return ""

        if total_tracked > 1 and active_position:
            return f"Processando {active_position}/{total_tracked}: {file_name}"

        return f"Processando: {file_name}"

    def _update_global_progress(self, *args):
        if not hasattr(self, "global_progress"):
            return

        tracked = []
        active_job = None
        active_position = None
        active_statuses = {"PROCESSING", "RUNNING"}

        for job in self._get_all_jobs():
            status = str(getattr(job, "status", "") or "").upper()
            contribution = self._global_progress_contribution(job, status)
            if contribution is not None:
                tracked.append(contribution)
                if active_job is None and status in active_statuses:
                    active_job = job
                    active_position = len(tracked)

        if not tracked:
            self.global_progress.set_idle()
            return

        aggregate = int(round(sum(tracked) / max(1, len(tracked))))
        status_text = self._build_global_progress_status_text(
            active_job,
            active_position,
            len(tracked),
        )
        self.global_progress.set_progress(aggregate, 100, status_text=status_text)

    def _update_footer_state(self):
        model = self.file_list.model()
        total = model.rowCount()

        importing = self._is_importing()
        processing = False

        if model is not None and total > 0:
            for r in range(total):
                index = model.index(r)
                job = model.data(index, FileListModel.ROLE_JOB_REF)
                if job and getattr(job, "status", None) in ("PROCESSING", "RUNNING"):
                    processing = True
                    break

        self.execution_footer.set_processing_state(processing)
        self.execution_footer.btn_exit.setEnabled(True)

        if self._shutdown_pending:
            self.execution_footer.btn_cancel.setEnabled(False)
            self.execution_footer.btn_cancel_all.setEnabled(False)
            self.execution_footer.btn_clear_all.setEnabled(False)
            return

        if importing:
            self.execution_footer.btn_compress.setEnabled(False)
            self.execution_footer.btn_compress_all.setEnabled(False)
            self.execution_footer.btn_cancel.setEnabled(False)
            self.execution_footer.btn_cancel_all.setEnabled(False)
            self.execution_footer.btn_clear_all.setEnabled(False)
            return

        if total == 0:
            self.execution_footer.btn_compress.setEnabled(False)
            self.execution_footer.btn_compress_all.setEnabled(False)
            self.execution_footer.btn_cancel.setEnabled(False)
            self.execution_footer.btn_cancel_all.setEnabled(False)
            self.execution_footer.btn_clear_all.setEnabled(False)
            return

        self.execution_footer.btn_compress.setEnabled(True)
        self.execution_footer.btn_compress_all.setEnabled(True)
        self.execution_footer.btn_cancel.setEnabled(True)
        self.execution_footer.btn_cancel_all.setEnabled(True)
        self.execution_footer.btn_clear_all.setEnabled(not processing)

    def _get_all_jobs(self):
        jobs = []
        model = self.file_list.model()
        if model is None:
            return jobs

        try:
            total = int(model.rowCount() or 0)
        except Exception:
            return jobs

        seen = set()
        for row in range(total):
            try:
                index = model.index(row)
                job = model.data(index, FileListModel.ROLE_JOB_REF)
            except Exception:
                job = None
            if not job:
                continue

            marker = id(job)
            if marker in seen:
                continue

            seen.add(marker)
            jobs.append(job)

        return jobs

    def _get_selected_jobs(self):
        jobs = []
        seen = set()

        for index in self.file_list.selectedIndexes():
            job = self.file_list.model().data(index, FileListModel.ROLE_JOB_REF)
            if not job:
                continue

            marker = id(job)
            if marker in seen:
                continue

            seen.add(marker)
            jobs.append(job)

        return jobs

    def _select_all_jobs(self):
        self.file_list.selectAll()
        self.file_list.setFocus()

    def _remove_selected_jobs(self):
        if self._is_importing():
            return

        for job in self._get_selected_jobs():
            event_bridge.emit("job_remove_requested", job)

    def _update_selection_bar_state(self, *args):
        if self.selection_bar is None:
            return

        has_selection = bool(self._get_selected_jobs())
        model = self.file_list.model()
        has_items = bool(model and model.rowCount() > 0)
        importing = self._is_importing()

        if hasattr(self.selection_bar, "btn_config"):
            analysis_blocked = self._is_configuration_blocked_by_analysis()
            if getattr(self, "_configuration_button_busy", False):
                self.selection_bar.btn_config.setProperty("analysisBlocked", False)
                self.selection_bar.btn_config.style().unpolish(self.selection_bar.btn_config)
                self.selection_bar.btn_config.style().polish(self.selection_bar.btn_config)
                self.selection_bar.btn_config.setEnabled(False)
                self.selection_bar.btn_config.setToolTip("Abrindo configurações...")
            elif analysis_blocked:
                self.selection_bar.btn_config.setProperty("analysisBlocked", True)
                self.selection_bar.btn_config.style().unpolish(self.selection_bar.btn_config)
                self.selection_bar.btn_config.style().polish(self.selection_bar.btn_config)
                self.selection_bar.btn_config.setEnabled(False)
                self.selection_bar.btn_config.setToolTip("Aguarde a análise da mídia terminar")
            else:
                self.selection_bar.btn_config.setProperty("analysisBlocked", False)
                self.selection_bar.btn_config.style().unpolish(self.selection_bar.btn_config)
                self.selection_bar.btn_config.style().polish(self.selection_bar.btn_config)
                self.selection_bar.btn_config.setEnabled(has_items and not importing)
                self.selection_bar.btn_config.setToolTip("Abrir configurações")
        if hasattr(self.selection_bar, "btn_enqueue"):
            self.selection_bar.btn_enqueue.setEnabled(not importing)

        self.selection_bar.btn_select_all.setVisible(True)
        self.selection_bar.btn_delete.setVisible(True)
        self.selection_bar.btn_select_all.setEnabled(has_items and not importing)
        self.selection_bar.btn_delete.setEnabled(has_selection and not importing)

    def _handle_palette_change(self):
        self._apply_surface_styles()
        # PaletteChange already reaches the themed child widgets, each of which
        # updates its own stylesheet. Re-polishing the entire shell here briefly
        # tears down and rebuilds every native control during a theme switch.
        self.update()

    def _refresh_widget_styles(self):
        widgets = [
            self,
            getattr(self, "content", None),
            getattr(self, "main_container", None),
            getattr(self, "middle_container", None),
            getattr(self, "file_list_container", None),
            getattr(self, "execution_footer", None),
            getattr(self, "global_progress", None),
            getattr(self, "global_bar", None),
        ]

        for widget in widgets:
            if widget is None:
                continue

            style = widget.style()
            if style is None:
                continue

            style.unpolish(widget)
            style.polish(widget)
            widget.update()
