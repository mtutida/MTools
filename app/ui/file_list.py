from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QAbstractItemView, QFrame, QListView, QStyleOptionViewItem

from app.core.compression_profiles import estimated_output_is_actionable
from app.interaction_model.event_bridge import event_bridge
from app.interaction_model.execution_controller import execution_controller
from app.ui.file_card_delegate import FileCardDelegate
from app.ui.file_card_presenter import resolve_playable_output_path
from app.ui.semantic_tooltip import SemanticTooltip
from app.ui.theme_tokens import build_theme_tokens, color_to_css


class FileList(QListView):

    add_requested = Signal()
    add_quick_requested = Signal()
    import_folder_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setObjectName("InnerFileList")

        self.setContentsMargins(0, 0, 0, 0)
        self.setFrameShape(QFrame.NoFrame)
        self.setLineWidth(0)
        self.setMidLineWidth(0)

        # Selection is handled explicitly from the card title band below.
        # Leaving QListView in an extended native selection mode lets Qt select
        # a row from viewport paths that do not belong to the card controls.
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)

        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setLayoutMode(QListView.Batched)

        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.delegate = FileCardDelegate(self)
        self.setItemDelegate(self.delegate)

        # Hover tracking
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

        self._hover_index = None
        self._hover_action = None
        self._suppress_card_action_release = False
        self._card_tooltip = SemanticTooltip(self.window())

        # Drag state
        self._drag_active = False
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)  # IMPORTANT FIX

        self.setStyleSheet("QListView#InnerFileList { border: none; }")
        self.setSpacing(4)

        self._base_viewport_margins = (4, 7, 4, 4)
        self._requested_bottom_content_inset = 0
        self._tail_snap_content_inset = 0
        self._bottom_content_inset = 0
        self._apply_viewport_margins()

        self._empty_state_link_rects = {}
        self._empty_state_geometry_rect = QRect()
        self._empty_state_theme_repolish_active = False
        self.apply_theme()

    def apply_theme(self):
        app = QApplication.instance()
        source_palette = app.palette() if app is not None else self.palette()
        tokens = build_theme_tokens(source_palette)
        surface = color_to_css(tokens.surface_list)
        viewport = self.viewport()
        # Keep the viewport palette inherited from the application. A local
        # palette survives the first phase of an app-level theme switch and can
        # paint one stale dark frame before the PaletteChange handler runs.
        viewport.setAutoFillBackground(False)
        viewport.setStyleSheet(f"background-color: {surface};")
        self._card_tooltip.apply_theme(tokens)
        viewport.update()
        # PySide6 exposes QListView.update() only through its rect/region
        # overloads in the bundled runtime; repaint the viewport explicitly
        # for a full, argument-safe refresh.
        self.viewport().update()

    def _current_empty_state_rect(self) -> QRect:
        """Return a stable drawing rect for the empty-state canvas.

        During a manual Qt color-scheme switch, Windows/Qt can repolish the
        QListView viewport after the first repaint and briefly report a
        slightly different viewport rectangle. If the drop-zone frame is drawn
        directly from that transient rectangle, the dashed border appears to
        grow after the theme transition. The empty-state layout is visual chrome,
        not list content, so it should only change when the widget is actually
        resized by layout/window changes.
        """
        if self._empty_state_geometry_rect.isNull():
            self._empty_state_geometry_rect = QRect(self.viewport().rect())
        return QRect(self._empty_state_geometry_rect)

    def _begin_empty_state_theme_repolish_guard(self) -> None:
        self._empty_state_theme_repolish_active = True

        def _clear_guard() -> None:
            self._empty_state_theme_repolish_active = False
            self.viewport().update()

        # Qt may emit one or more late polish/layout passes after
        # QStyleHints.setColorScheme(). Keep the cached empty-state geometry
        # through the current and next event-loop turns so theme-only viewport
        # metric noise cannot resize the dashed frame.
        QTimer.singleShot(0, _clear_guard)
        QTimer.singleShot(30, _clear_guard)

    def _is_importing_locked(self) -> bool:
        return bool(self.property("importing"))


    def _apply_viewport_margins(self):
        left, top, right, bottom = self._base_viewport_margins
        self.setViewportMargins(left, top, right, bottom + self._bottom_content_inset)

    def _apply_bottom_content_inset(self, inset: int):
        inset = max(0, int(inset or 0))
        if inset == self._bottom_content_inset:
            return

        self._bottom_content_inset = inset
        self._apply_viewport_margins()
        self.doItemsLayout()
        self.updateGeometries()
        self.viewport().update()
        self.viewport().update()

    def _calculate_tail_snap_content_inset(self) -> int:
        if self._requested_bottom_content_inset <= 0:
            return 0

        model = self.model()
        if model is None:
            return 0

        row_count = int(model.rowCount() or 0)
        if row_count <= 1:
            return 0

        scroll_bar = self.verticalScrollBar()
        last_card_top_target = (row_count - 1) * self._card_scroll_step()
        missing_scroll_room = last_card_top_target - scroll_bar.maximum()

        # At the end of the list Qt naturally clamps the scrollbar to the point
        # where the content bottom meets the viewport bottom. When the overlay is
        # open and scrolling is snapped by card, that clamp can leave the last
        # real card a little below the same top boundary used by all other cards.
        # Add only the missing tail room, instead of changing the card height or
        # globally shrinking every card layout.
        return max(0, int(missing_scroll_room))

    def set_bottom_content_inset(self, inset: int):
        requested_inset = max(0, int(inset or 0))
        if requested_inset == self._requested_bottom_content_inset:
            return

        self._requested_bottom_content_inset = requested_inset
        self._tail_snap_content_inset = 0
        self._apply_bottom_content_inset(requested_inset)

        tail_inset = self._calculate_tail_snap_content_inset()
        if tail_inset > 0:
            self._tail_snap_content_inset = tail_inset
            self._apply_bottom_content_inset(requested_inset + tail_inset)

    # ------------------------------------------------
    # Drag highlight
    # ------------------------------------------------

    def dragEnterEvent(self, event):

        if event.mimeData().hasUrls():
            self._drag_active = True
            self.viewport().update()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):

        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drag_active = False
        self.viewport().update()
        event.accept()

    def dropEvent(self, event):

        self._drag_active = False
        self.viewport().update()

        if not event.mimeData().hasUrls():
            event.ignore()
            return

        paths = []

        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path:
                paths.append(path)

        if paths:
            event_bridge.emit("files_dropped", {"paths": paths})

        event.acceptProposedAction()

    # ------------------------------------------------
    # EMPTY STATE (dropzone UI)
    # ------------------------------------------------

    def paintEvent(self, event):

        super().paintEvent(event)

        model = self.model()

        if not model or model.rowCount() != 0:
            return

        painter = QPainter(self.viewport())

        if not painter.isActive():
            return

        try:
            painter.setRenderHint(QPainter.Antialiasing)

            rect = self._current_empty_state_rect()
            center_y = rect.center().y()

            palette = self.viewport().palette()
            tokens = build_theme_tokens(palette)

            secondary = tokens.surface_empty_state_hint

            # -------------------------
            # Drop zone border
            # -------------------------

            # pen = QPen(palette.mid().color())

            border = tokens.border_empty_state
            pen = QPen(border)

            pen.setStyle(Qt.DashLine)
            pen.setWidth(2)

            if self._drag_active:
                pen.setColor(tokens.drop_target_border)

            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)

            # QRect draws a 2px dashed stroke around integer edges differently
            # after the native Windows repolish pass. A half-pixel QRectF keeps
            # the stroke visually anchored in the same pixels before and after
            # manual theme switches.
            drop_rect = QRectF(rect.adjusted(60, 60, -60, -60)).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.drawRoundedRect(drop_rect, 10, 10)
            painter.restore()

            # -------------------------
            # Icon
            # -------------------------

            font = painter.font()

            icon_color = QColor(
                tokens.drop_target_border if self._drag_active else tokens.empty_state_link_text
            )
            icon_color.setAlpha(225 if self._drag_active else 205)
            icon_x = rect.center().x()
            icon_y = center_y - 142

            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(Qt.NoBrush)

            # Flat indicator, not a button: use an outlined downward arrow with
            # a simple tray. Avoid filled triangles or boxed backgrounds because
            # they read as a clickable control in the empty-state drop zone.
            icon_pen = QPen(icon_color, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(icon_pen)
            painter.drawLine(icon_x, icon_y, icon_x, icon_y + 30)
            painter.drawLine(icon_x, icon_y + 30, icon_x - 13, icon_y + 17)
            painter.drawLine(icon_x, icon_y + 30, icon_x + 13, icon_y + 17)

            tray_pen = QPen(icon_color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(tray_pen)
            painter.drawLine(icon_x - 25, icon_y + 49, icon_x + 25, icon_y + 49)
            painter.drawLine(icon_x - 25, icon_y + 49, icon_x - 18, icon_y + 42)
            painter.drawLine(icon_x + 25, icon_y + 49, icon_x + 18, icon_y + 42)
            painter.restore()

            # -------------------------
            # Title
            # -------------------------

            font.setPointSize(22)
            font.setBold(True)
            painter.setFont(font)

            title = (
                "Solte os arquivos para adicionar"
                if self._drag_active
                else "Arraste arquivos aqui"
            )

            title_color = QColor(tokens.text_primary)
            title_color.setAlpha(255)
            painter.setPen(title_color)

            painter.drawText(
                rect.adjusted(0, center_y - 70, 0, 0), Qt.AlignHCenter, title
            )

            # -------------------------
            # Subtitle
            # -------------------------

            font.setPointSize(13)
            font.setBold(False)
            painter.setFont(font)

            painter.setPen(secondary)

            painter.drawText(
                rect.adjusted(0, center_y - 4, 0, 0),
                Qt.AlignHCenter,
                "ou escolha uma opção abaixo:",
            )

            # -------------------------
            # Separator
            # -------------------------

            sep_y = center_y - 24

            separator_color = QColor(tokens.border_empty_state)
            separator_color.setAlpha(150)
            painter.setPen(QPen(separator_color, 1))
            painter.drawLine(
                rect.center().x() - 140, sep_y, rect.center().x() + 140, sep_y
            )

            # -------------------------
            # Instructions
            # -------------------------

            font.setPointSize(11)
            painter.setFont(font)

            painter.setPen(secondary)

            self._empty_state_link_rects = {}

            action_color = QColor(tokens.empty_state_link_text)
            action_color.setAlpha(255)
            arrow_color = QColor(secondary)
            arrow_color.setAlpha(210)
            description_color = QColor(secondary)
            description_color.setAlpha(218)

            action_font = painter.font()
            action_font.setPointSize(11)
            action_font.setBold(False)
            action_font.setUnderline(False)
            action_metrics = QFontMetrics(action_font)

            description_font = painter.font()
            description_font.setPointSize(11)
            description_font.setBold(False)
            description_font.setUnderline(False)
            description_metrics = QFontMetrics(description_font)

            action_rows = [
                ("add", "Adicionar arquivos", "escolher arquivos e configurar a saída", center_y + 34),
                ("add_quick", "Adicionar rápido", "adicionar arquivos diretamente à fila", center_y + 63),
                ("import_folder", "Importar pasta", "adicionar todos os arquivos de uma pasta", center_y + 92),
            ]

            action_column_width = max(
                action_metrics.horizontalAdvance(label)
                for _, label, _, _ in action_rows
            )
            arrow = "→"
            arrow_width = description_metrics.horizontalAdvance(arrow)
            gap_after_action = 10
            gap_after_arrow = 10
            description_width = max(
                description_metrics.horizontalAdvance(description)
                for _, _, description, _ in action_rows
            )
            total_width = (
                action_column_width
                + gap_after_action
                + arrow_width
                + gap_after_arrow
                + description_width
            )
            block_offset_x = 6
            start_x = rect.center().x() - (total_width // 2) + block_offset_x
            arrow_x = start_x + action_column_width + gap_after_action
            description_x = arrow_x + arrow_width + gap_after_arrow

            for key, label, description, y in action_rows:
                baseline_y = y + action_metrics.ascent()

                painter.setFont(action_font)
                painter.setPen(action_color)
                painter.drawText(start_x, baseline_y, label)

                label_width = action_metrics.horizontalAdvance(label)
                link_rect = QRect(
                    start_x,
                    y,
                    label_width,
                    max(action_metrics.height(), description_metrics.height()),
                )
                self._empty_state_link_rects[key] = link_rect

                painter.setFont(description_font)
                painter.setPen(arrow_color)
                painter.drawText(arrow_x, baseline_y, arrow)
                painter.setPen(description_color)
                painter.drawText(description_x, baseline_y, description)

        finally:
            painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not getattr(self, "_empty_state_theme_repolish_active", False):
            self._empty_state_geometry_rect = QRect(self.viewport().rect())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.ThemeChange,
        ):
            self.setFrameShape(QFrame.NoFrame)
            self.setLineWidth(0)
            self.setMidLineWidth(0)
            self._apply_viewport_margins()
            self.updateGeometries()
            self._begin_empty_state_theme_repolish_guard()
            self.apply_theme()

    def _empty_state_drop_rect(self):
        return self.viewport().rect().adjusted(60, 60, -60, -60)

    def _empty_state_link_at(self, pos):
        if not self._empty_state_link_rects:
            return None

        for key, rect in self._empty_state_link_rects.items():
            if rect.contains(pos):
                return key
        return None

    def _empty_state_drop_area_contains(self, pos):
        return (
            self.model() is not None
            and self.model().rowCount() == 0
            and self._empty_state_drop_rect().contains(pos)
        )

    def _action_at_pos(self, pos):
        index = self.indexAt(pos)
        if not index.isValid():
            return None, None

        option = QStyleOptionViewItem()
        option.rect = self.visualRect(index)
        rects = self.delegate.get_action_rects(option, index)

        for action in ("thumbnail", "open_folder", "replace_folder", "settings", "remove", "run"):
            rect = rects.get(action)
            if rect and rect.contains(pos):
                return index, action

        # Card-wide hover should only be active on the title/header band.
        # The close/remove button is handled above as its own action, so moving
        # over the X does not also trigger the card hover overlay.
        header_rect = rects.get("header")
        if header_rect and header_rect.contains(pos):
            return index, None

        return None, None

    def _failure_tooltip_for_job(self, job):
        status = str(getattr(job, "status", "") or "").upper()
        if status not in {"FAILED", "ERROR"}:
            return None

        reason = str(getattr(job, "error", "") or "").strip()
        if not reason:
            reason = "O processamento falhou, mas o motivo técnico não foi informado."

        max_reason_len = 180
        if len(reason) > max_reason_len:
            reason = reason[: max_reason_len - 1].rstrip() + "…"

        return f"Falha no processamento: {reason}\nClique para tentar comprimir novamente."

    def _tooltip_for_action(self, index, action):
        if not index or not index.isValid() or not action:
            return None

        job = index.data(self.delegate.ROLE_JOB_REF)
        if job is None:
            return None

        if action == "thumbnail":
            return "Reproduzir mídia original"
        if action == "open_folder":
            return "Abrir pasta"
        if action == "replace_folder":
            return "Alterar pasta de saída"
        if action == "settings":
            return "Alterar perfil de compressão"
        if action == "remove":
            return "Remover da fila"
        if action == "run":
            status = str(getattr(job, "status", "READY") or "READY").upper()
            if status in {"FAILED", "ERROR"}:
                return self._failure_tooltip_for_job(job)
            if status == "QUEUED":
                return "Remover este arquivo da fila"
            if status in ("RUNNING", "PROCESSING"):
                return "Cancelar processamento deste arquivo"
            if resolve_playable_output_path(job):
                return "Reproduzir arquivo gerado"
            if status == "NO_GAIN" or estimated_output_is_actionable(job) is False:
                return "Ajuste os parâmetros para permitir compressão"
            profile_mode = str(getattr(job, "profile_mode", "") or "").strip().lower()
            if profile_mode == "audio":
                return "Gravar arquivo de áudio"
            return "Comprimir este arquivo"
        return None

    # ------------------------------------------------
    # Hover detection
    # ------------------------------------------------

    def mouseMoveEvent(self, event):

        if self._is_importing_locked():
            if self._hover_index and self._hover_index.isValid():
                self.viewport().update(self.visualRect(self._hover_index))
            self._hover_index = None
            self._hover_action = None
            self._hide_card_tooltip()
            self.viewport().setCursor(Qt.ArrowCursor)
            super().mouseMoveEvent(event)
            return

        pos = event.pos()

        if self.model() is not None and self.model().rowCount() == 0:
            hovered_link = self._empty_state_link_at(pos)
            hovered_drop_area = self._empty_state_drop_area_contains(pos)
            self._hide_card_tooltip()
            self.viewport().setCursor(
                Qt.PointingHandCursor if hovered_link or hovered_drop_area else Qt.ArrowCursor
            )
            super().mouseMoveEvent(event)
            return

        index, hovered_action = self._action_at_pos(pos)

        prev_index = self._hover_index
        prev_action = self._hover_action

        hover_is_disabled_run = False
        if hovered_action == "run" and index and index.isValid():
            job = index.data(self.delegate.ROLE_JOB_REF)
            hover_is_disabled_run = (
                str(getattr(job, "status", "") or "").upper() == "NO_GAIN"
                or estimated_output_is_actionable(job) is False
            )
        if hovered_action and not hover_is_disabled_run:
            self.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self.viewport().setCursor(Qt.ArrowCursor)

        self._hover_index = index if index and index.isValid() else None
        self._hover_action = hovered_action

        tooltip = self._tooltip_for_action(self._hover_index, self._hover_action)
        immediate_tooltip_actions = {"thumbnail", "settings", "remove", "run"}
        if tooltip and hovered_action in immediate_tooltip_actions:
            self._show_card_tooltip(event.globalPosition().toPoint(), tooltip)
        else:
            self._hide_card_tooltip()

        if prev_index != self._hover_index or prev_action != self._hover_action:

            if prev_index and prev_index.isValid():
                self.viewport().update(self.visualRect(prev_index))

            if self._hover_index and self._hover_index.isValid():
                self.viewport().update(self.visualRect(self._hover_index))

        super().mouseMoveEvent(event)

    # ------------------------------------------------
    # Clear hover
    # ------------------------------------------------

    def leaveEvent(self, event):

        if self._hover_index and self._hover_index.isValid():
            self.viewport().update(self.visualRect(self._hover_index))

        self._hover_index = None
        self._hover_action = None
        self._hide_card_tooltip()

        self.viewport().setCursor(Qt.ArrowCursor)

        super().leaveEvent(event)

    def _show_card_tooltip(self, global_pos: QPoint, text: str):
        self._card_tooltip.show_for(self.viewport(), global_pos, text)

    def _hide_card_tooltip(self):
        self._card_tooltip.hide_tooltip()

    def viewportEvent(self, event):

        if event.type() == QEvent.ToolTip:
            index, action = self._action_at_pos(event.pos())
            if action and index and index.isValid():
                tooltip = self._tooltip_for_action(index, action)
                if tooltip:
                    self._show_card_tooltip(event.globalPos(), tooltip)
                    return True
            self._hide_card_tooltip()
            event.ignore()
            return True

        if event.type() in (QEvent.Leave, QEvent.Wheel, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            self._hide_card_tooltip()

        # QAbstractItemView owns the viewport event loop and can apply its
        # default selection before the list's higher-level click handling has
        # completed. Route every non-title press through our card handler here
        # and consume its matching release. Only the title band reaches Qt's
        # native selection path.
        if event.type() == QEvent.Type.MouseButtonPress:
            try:
                pos = event.position().toPoint()
                index = self.indexAt(pos)
                if index.isValid():
                    option = QStyleOptionViewItem()
                    option.rect = self.visualRect(index)
                    header_rect = self.delegate.get_action_rects(option, index).get("header")
                    if header_rect is None or not header_rect.contains(pos):
                        self._suppress_card_action_release = True
                        self.mousePressEvent(event)
                        return True
            except Exception:
                pass
        elif event.type() == QEvent.Type.MouseButtonRelease and self._suppress_card_action_release:
            self.mouseReleaseEvent(event)
            return True

        return super().viewportEvent(event)

    # ------------------------------------------------
    # Click handling
    # ------------------------------------------------

    def _deselect_card_index(self, index):
        selection_model = self.selectionModel()
        if selection_model is not None and selection_model.isSelected(index):
            selection_model.select(index, QItemSelectionModel.SelectionFlag.Deselect)

    def _select_card_from_header(self, index, event):
        """Apply the only mouse-driven selection action for a card."""
        selection_model = self.selectionModel()
        if selection_model is None:
            return
        modifiers = event.modifiers()
        selected = selection_model.isSelected(index)
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            selection_model.select(index, QItemSelectionModel.SelectionFlag.Toggle)
        elif selected:
            selection_model.select(index, QItemSelectionModel.SelectionFlag.Deselect)
        else:
            selection_model.select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.setCurrentIndex(index)

    def selectionCommand(self, index, event=None):
        """Allow mouse selection only from a card's title/header band.

        This is the authoritative QListView selection hook.  It covers native
        selection paths that may run independently of the action handlers,
        such as a mouse-release processed after a folder dialog opens.
        """
        mouse_events = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick)
        if index.isValid() and event is not None and event.type() in mouse_events:
            return QItemSelectionModel.SelectionFlag.NoUpdate
        return super().selectionCommand(index, event)

    def mousePressEvent(self, event):

        self._hide_card_tooltip()

        if self._is_importing_locked():
            event.ignore()
            return

        pos = event.pos()

        if self.model() is not None and self.model().rowCount() == 0:
            action = self._empty_state_link_at(pos)
            if action == "add":
                self.add_requested.emit()
                return
            if action == "add_quick":
                self.add_quick_requested.emit()
                return
            if action == "import_folder":
                self.import_folder_requested.emit()
                return
            if self._empty_state_drop_area_contains(pos):
                self.add_requested.emit()
                return
            return super().mousePressEvent(event)

        index = self.indexAt(pos)

        if not index.isValid():
            # Native selection is disabled so cards can be selected only from
            # their title band. Mirror the expected list behavior explicitly:
            # clicking the empty canvas clears the current card selection.
            self.clearSelection()
            event.accept()
            return

        item_rect = self.visualRect(index)

        option = QStyleOptionViewItem()
        option.rect = item_rect

        rects = self.delegate.get_action_rects(option, index)

        job = index.data(self.delegate.ROLE_JOB_REF)

        if job is None:
            return super().mousePressEvent(event)

        # QListView may complete a row selection on mouse release.  Every
        # interactive region must consume that release so selection remains an
        # explicit title/header action, never a side effect of a card control.
        card_action_rects = (
            "thumbnail",
            "open_folder",
            "replace_folder",
            "settings",
            "remove",
            "run",
            "progress",
        )
        if any(rects.get(action) and rects[action].contains(pos) for action in card_action_rects):
            self._deselect_card_index(index)
            self._suppress_card_action_release = True
            event.accept()

        if rects.get("thumbnail") and rects["thumbnail"].contains(pos):
            event_bridge.emit("job_play_source_requested", job)
            return

        if rects["progress"].contains(pos) and not rects["run"].contains(pos):
            return

        if rects["run"].contains(pos):
            status = str(getattr(job, "status", "READY") or "READY").upper()
            if status in ("RUNNING", "PROCESSING", "QUEUED"):
                event_bridge.emit("job_cancel_requested", job)
            elif resolve_playable_output_path(job):
                event_bridge.emit("job_play_output_requested", job)
            elif status == "NO_GAIN" or estimated_output_is_actionable(job) is False:
                return
            else:
                execution_controller.run_job(job)
            return

        if rects["settings"].contains(pos):
            event_bridge.emit("job_settings_requested", job)
            return

        if rects["remove"].contains(pos):
            event_bridge.emit("job_remove_requested", job)
            return

        if rects["open_folder"].contains(pos):
            event_bridge.emit("job_open_folder_requested", job)
            return

        if rects["replace_folder"].contains(pos):
            event_bridge.emit("job_replace_folder_requested", job)
            return

        action_column_start = min(
            rects["open_folder"].left(),
            rects["replace_folder"].left(),
            rects["settings"].left(),
            rects["run"].left(),
            rects["remove"].left(),
        )

        header_rect = rects.get("header")
        click_in_header = bool(header_rect is not None and header_rect.contains(pos))

        if pos.x() >= action_column_start and not click_in_header:
            return

        # Card selection is intentionally limited to the title/header band.
        # Interactive controls and the card body handle their own actions or
        # remain passive; they must not fall through to QListView's default
        # row selection behavior.
        if not click_in_header:
            self._deselect_card_index(index)
            return

        self._select_card_from_header(index, event)
        event.accept()
        return

    def mouseReleaseEvent(self, event):
        if self._suppress_card_action_release:
            self._suppress_card_action_release = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------

    def _card_scroll_step(self) -> int:
        delegate_height = getattr(self.delegate, "CARD_HEIGHT", 96)
        # QListView spacing is applied around each list item. The visual pitch
        # between two card top edges is therefore the card height plus the
        # vertical spacing above and below the item. Using only one spacing unit
        # left the next card a few pixels too low after wheel snapping.
        return max(1, int(delegate_height) + (int(self.spacing()) * 2))

    def _scroll_one_card_step(self, direction: int):
        if direction == 0:
            return

        scroll_bar = self.verticalScrollBar()
        current = scroll_bar.value()
        step = self._card_scroll_step()

        if direction > 0:
            target = ((current // step) + 1) * step
        else:
            target = ((max(0, current - 1) // step)) * step

        target = max(scroll_bar.minimum(), min(scroll_bar.maximum(), target))
        scroll_bar.setValue(target)

    def wheelEvent(self, event):
        self._hide_card_tooltip()

        if self._bottom_content_inset > 0:
            delta_y = event.angleDelta().y()
            if delta_y == 0:
                delta_y = event.pixelDelta().y()

            if delta_y != 0:
                # While the profile overlay is open, the visible list area is
                # intentionally shorter. A pixel-based wheel step can leave the
                # next card partially hidden behind the overlay. Snap each wheel
                # action to the next complete card boundary instead.
                direction = -1 if delta_y > 0 else 1
                self._scroll_one_card_step(direction)
                event.accept()
                return

        super().wheelEvent(event)

    def focusOutEvent(self, event):
        self._hide_card_tooltip()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._hide_card_tooltip()
        super().hideEvent(event)


    def keyPressEvent(self, event):

        if self._is_importing_locked():
            event.ignore()
            return

        if event.key() == Qt.Key_Delete:

            indexes = self.selectedIndexes()
            if not indexes:
                return

            jobs = []
            for index in indexes:
                job = index.data(self.delegate.ROLE_JOB_REF)
                if job:
                    jobs.append(job)

            if not jobs:
                return

            from PySide6.QtWidgets import QCheckBox, QMessageBox

            from app.ancillary.configuration import ConfigurationService

            cfg_service = ConfigurationService.instance()
            cfg = cfg_service.get()

            if cfg.confirm_delete:

                # collect job names for preview
                names = []
                for job in jobs:
                    name = getattr(job, "file_name", None)
                    if name:
                        names.append(name)

                preview = "\n".join(names[:3])
                if len(names) > 3:
                    preview += f"\n... e mais {len(names) - 3}"

                msg = QMessageBox(self)
                msg.setWindowTitle("Remover itens da fila")
                msg.setText(f"Remover {len(jobs)} item(ns) selecionado(s)?")
                if preview:
                    msg.setInformativeText(preview)

                msg.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
                msg.setDefaultButton(QMessageBox.Cancel)

                checkbox = QCheckBox("Não mostrar novamente")
                msg.setCheckBox(checkbox)

                result = msg.exec()

                if checkbox.isChecked():
                    cfg_service.update(confirm_delete=False)

                if result != QMessageBox.Yes:
                    return

            seen = set()
            for job in jobs:
                if id(job) in seen:
                    continue
                seen.add(id(job))

                event_bridge.emit("job_remove_requested", job)

            return

        super().keyPressEvent(event)
