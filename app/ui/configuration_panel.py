from types import SimpleNamespace
from dataclasses import replace as dataclass_replace
import html
import json
from pathlib import Path
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, QTimer, Qt, Signal, QSignalBlocker, QLocale
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QGraphicsOpacityEffect,
    QGridLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProxyStyle,
    QPushButton,
    QMessageBox,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QDoubleSpinBox,
    QDialog,
    QStackedLayout,
    QStackedWidget,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionSpinBox,
    QStyleOptionToolButton,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from app.ancillary.configuration import AppConfiguration, ConfigurationService
from app.core.profiles import advanced as advanced_profile
from app.core.profiles import audio as audio_profile
from app.core import ffmpeg_binaries
from app.core.automatic_profile import build_automatic_recommendation, automatic_recommendation_to_profile_payload
from app.core.subprocess_utils import run_no_window
from app.core.profiles import quick as quick_profile
from app.core.profiles.strategic import SMART_STRATEGIES
from app.core.formatting import format_bitrate as _format_bitrate, format_bytes as _shared_format_bytes, format_mb_value as _format_mb_value, parse_fps, parse_resolution
from app.core.compression_profiles import (
    default_audio_profile,
    default_advanced_size_profile,
    default_quick_profile,
    default_smart_profile,
    estimate_output_bitrate_for_profile,
    estimate_output_is_actionable,
    estimate_output_video_traits,
    estimate_size_for_profile,
    build_profile_signature,
    profile_output_matches_current_settings,
    quick_minimum_total_bitrate_floor_for_profile,
    quick_preset_title,
    resolve_job_source_size_bytes,
)
from app.engine.encode_estimator import estimate_size_crf
from app.interaction_model.event_bridge import event_bridge
from app.ui.theme_tokens import build_button_stylesheet, build_theme_tokens
from app.ui.theme_mode_controller import _dark_palette, _standard_palette
SMART_STRATEGY_DISPLAY_LABELS = {
    "Economia Máxima": "Reduzir escala",
    "Equilíbrio": "Otimizar arquivo",
    "Qualidade Prioritária": "Preservar qualidade",
}
SMART_STRATEGY_UI_ORDER = (
    "Qualidade Prioritária",
    "Equilíbrio",
    "Economia Máxima",
)
_AUDIO_ONLY_DISABLED_MODES = {"quick", "smart", "advanced"}
_AUDIO_ONLY_MODE_TOOLTIP = "Disponível apenas para arquivos com vídeo."
class _InlineComboBorderProxyStyle(QProxyStyle):
    def __init__(self, base_style, border_color: QColor):
        super().__init__(base_style)
        self._border_color = QColor(border_color)
    def set_border_color(self, border_color: QColor):
        self._border_color = QColor(border_color)
    def drawComplexControl(self, control, option, painter, widget=None):
        super().drawComplexControl(control, option, painter, widget)
        if control != QStyle.ComplexControl.CC_ComboBox:
            return
        if widget is None or widget.objectName() != "ConfigurationInlineCombo":
            return
        if not isinstance(option, QStyleOptionComboBox):
            return
        color = QColor(self._border_color)
        if not widget.isEnabled():
            color.setAlpha(144)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = option.rect.adjusted(0, 0, -1, -1)
        painter.drawRoundedRect(rect, 4.0, 4.0)
        painter.restore()


class _NeutralComboPopupDelegate(QStyledItemDelegate):
    """Paint combo popup selection as a neutral filled row in every theme."""

    def __init__(
        self,
        selection_background: QColor,
        selection_foreground: QColor,
        current_row: int,
        parent=None,
    ):
        super().__init__(parent)
        self.set_selection(selection_background, selection_foreground, current_row)

    def set_selection(
        self,
        selection_background: QColor,
        selection_foreground: QColor,
        current_row: int,
    ):
        self._selection_background = QColor(selection_background)
        self._selection_foreground = QColor(selection_foreground)
        self._current_row = int(current_row)
        self._hovered_row = -1

    def set_hovered_row(self, hovered_row: int):
        hovered_row = int(hovered_row)
        if self._hovered_row == hovered_row:
            return
        self._hovered_row = hovered_row
        popup_view = self.parent()
        if popup_view is not None:
            popup_view.viewport().update()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseMove:
            popup_view = self.parent()
            if popup_view is not None:
                self.set_hovered_row(popup_view.indexAt(event.position().toPoint()).row())
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.Hide):
            self.set_hovered_row(-1)
        return super().eventFilter(watched, event)

    def paint(self, painter, option, index):
        styled_option = QStyleOptionViewItem(option)
        highlighted_row = self._hovered_row if self._hovered_row >= 0 else self._current_row
        if index.row() == highlighted_row:
            # Qt's native popup style can repaint the row background after a
            # delegate returns. Draw the selected option completely here so
            # the neutral highlight remains visible in either palette.
            painter.save()
            painter.fillRect(styled_option.rect, self._selection_background)
            painter.setFont(styled_option.font)
            painter.setPen(self._selection_foreground)
            text_rect = styled_option.rect.adjusted(8, 0, -8, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
            )
            painter.restore()
            return
        # QComboBox can retain an internal selected state for the last row
        # visited by the mouse. Paint only the explicit current/hover row.
        styled_option.state &= ~QStyle.StateFlag.State_Selected
        styled_option.state &= ~QStyle.StateFlag.State_MouseOver
        super().paint(painter, styled_option, index)


class _AudioTrackPopupDelegate(QStyledItemDelegate):
    """Give the checkbox popup an explicit row size and unchecked outline.

    The Windows item-view style ignores parts of a QListView indicator
    stylesheet, notably its border in the light palette.  Painting the empty
    indicator here keeps it legible and independent from that native quirk.
    """

    def __init__(self, background: QColor, border: QColor, parent=None):
        super().__init__(parent)
        self.set_visual_tokens(background, border)

    def set_visual_tokens(self, background: QColor, border: QColor):
        self._background = QColor(background)
        self._border = QColor(border)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), 29))
        return hint

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        check_state = index.data(Qt.ItemDataRole.CheckStateRole)
        if check_state not in (Qt.CheckState.Unchecked, 0):
            return
        styled_option = QStyleOptionViewItem(option)
        self.initStyleOption(styled_option, index)
        style = option.widget.style() if option.widget is not None else QApplication.style()
        check_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator,
            styled_option,
            option.widget,
        )
        if check_rect.isEmpty():
            return
        # Match the native checked indicator (14 logical pixels) so the two
        # states retain identical geometry at every display scale.
        side = 14
        check_rect = check_rect.adjusted(
            (check_rect.width() - side) // 2,
            (check_rect.height() - side) // 2,
            -((check_rect.width() - side + 1) // 2),
            -((check_rect.height() - side + 1) // 2),
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(self._border, 1))
        painter.setBrush(self._background)
        painter.drawRoundedRect(QRectF(check_rect).adjusted(0.5, 0.5, -0.5, -0.5), 5.0, 5.0)
        painter.restore()


class _ConfigurationComboBox(QComboBox):
    """Configuration combo that raises its actual popup container."""

    def showPopup(self):
        super().showPopup()
        popup_delegate = getattr(self.view(), "_neutral_combo_popup_delegate", None)
        if isinstance(popup_delegate, _NeutralComboPopupDelegate):
            popup_delegate.set_selection(
                popup_delegate._selection_background,
                popup_delegate._selection_foreground,
                self.currentIndex(),
            )
            self.view().viewport().update()
        self._raise_popup_container()
        QTimer.singleShot(0, self._raise_popup_container)

    def _raise_popup_container(self):
        popup_container = self.view().window()
        if popup_container is not self.window():
            popup_container.raise_()

class _HoldRepeatDoubleSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox with explicit press-and-hold repeat on arrow buttons.

    Some styles/platforms do not repeat reliably for styled spinboxes. This
    keeps the native look while handling the up/down sub-controls directly.
    """
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._hold_direction = 0
        self._hold_first_timeout = True
        self._hold_repeat_count = 0
        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(320)
        self._hold_timer.timeout.connect(self._on_hold_repeat_timeout)

    def _spinbox_option(self) -> QStyleOptionSpinBox:
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        return option

    def _arrow_direction_at(self, pos) -> int:
        option = self._spinbox_option()
        style = self.style()
        up_rect = style.subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxUp, self)
        down_rect = style.subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxDown, self)
        if up_rect.contains(pos):
            return 1
        if down_rect.contains(pos):
            return -1
        return 0

    def _start_hold_repeat(self, direction: int):
        self._hold_direction = 1 if direction > 0 else -1
        self._hold_first_timeout = True
        self._hold_repeat_count = 0
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.stepBy(self._hold_direction)
        self._hold_timer.start(320)

    def _stop_hold_repeat(self):
        self._hold_timer.stop()
        self._hold_direction = 0
        self._hold_first_timeout = True
        self._hold_repeat_count = 0

    def _on_hold_repeat_timeout(self):
        if not self._hold_direction:
            self._stop_hold_repeat()
            return
        if self._hold_first_timeout:
            self._hold_first_timeout = False
            self._hold_timer.setInterval(85)
        self._hold_repeat_count += 1
        # Keep normal 0.10 MB steps at first; after a longer hold, speed up
        # gently without skipping too aggressively.
        steps = 5 if self._hold_repeat_count >= 18 else 1
        self.stepBy(self._hold_direction * steps)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            direction = self._arrow_direction_at(event.position().toPoint())
            if direction:
                self._start_hold_repeat(direction)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._hold_direction:
            self._stop_hold_repeat()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._stop_hold_repeat()
        super().leaveEvent(event)

    def focusOutEvent(self, event):
        self._stop_hold_repeat()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._stop_hold_repeat()
        super().hideEvent(event)


class _HoldRepeatSpinBox(QSpinBox):
    """QSpinBox with explicit press-and-hold repeat on arrow buttons.

    Mirrors _HoldRepeatDoubleSpinBox so integer advanced controls repeat while
    the arrow button is held down, independent of platform/style behavior.
    """
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._hold_direction = 0
        self._hold_first_timeout = True
        self._hold_repeat_count = 0
        self._hold_timer = QTimer(self)
        self._hold_timer.setInterval(320)
        self._hold_timer.timeout.connect(self._on_hold_repeat_timeout)

    def _spinbox_option(self) -> QStyleOptionSpinBox:
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        return option

    def _arrow_direction_at(self, pos) -> int:
        option = self._spinbox_option()
        style = self.style()
        up_rect = style.subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxUp, self)
        down_rect = style.subControlRect(QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxDown, self)
        if up_rect.contains(pos):
            return 1
        if down_rect.contains(pos):
            return -1
        return 0

    def _start_hold_repeat(self, direction: int):
        self._hold_direction = 1 if direction > 0 else -1
        self._hold_first_timeout = True
        self._hold_repeat_count = 0
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.stepBy(self._hold_direction)
        self._hold_timer.start(320)

    def _stop_hold_repeat(self):
        self._hold_timer.stop()
        self._hold_direction = 0
        self._hold_first_timeout = True
        self._hold_repeat_count = 0

    def _on_hold_repeat_timeout(self):
        if not self._hold_direction:
            self._stop_hold_repeat()
            return
        if self._hold_first_timeout:
            self._hold_first_timeout = False
            self._hold_timer.setInterval(85)
        self._hold_repeat_count += 1
        steps = 5 if self._hold_repeat_count >= 18 else 1
        self.stepBy(self._hold_direction * steps)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            direction = self._arrow_direction_at(event.position().toPoint())
            if direction:
                self._start_hold_repeat(direction)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._hold_direction:
            self._stop_hold_repeat()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._stop_hold_repeat()
        super().leaveEvent(event)

    def focusOutEvent(self, event):
        self._stop_hold_repeat()
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._stop_hold_repeat()
        super().hideEvent(event)


class _QuickExtremeArrowButton(QPushButton):
    def __init__(self, text: str, direction: str, parent: QWidget | None = None):
        super().__init__(text, parent)
        self._direction = direction
        self._arrow_width = 10
        self._normal_bg = QColor(0, 0, 0, 0)
        self._hover_bg = QColor(0, 0, 0, 0)
        self._pressed_bg = QColor(0, 0, 0, 0)
        self._border = QColor(0, 0, 0, 0)
        self._text = QColor(self.palette().buttonText().color())
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def set_visual_colors(self, *, background: QColor, hover_background: QColor, pressed_background: QColor, border: QColor, text: QColor):
        self._normal_bg = QColor(background)
        self._hover_bg = QColor(hover_background)
        self._pressed_bg = QColor(pressed_background)
        self._border = QColor(border)
        self._text = QColor(text)
        self.update()

    def _shape_path(self) -> QPainterPath:
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        arrow = min(float(self._arrow_width), max(6.0, rect.width() * 0.14))
        radius = min(6.0, rect.height() * 0.18, max(0.0, (rect.width() - arrow) * 0.18))
        shoulder_inset = 1.0
        path = QPainterPath()
        if self._direction == "left":
            # Rounded outer side, sharp directional tip. The small shoulder inset
            # softens the body-to-tip transition without changing the hit area.
            path.moveTo(rect.right() - radius, rect.top())
            path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
            path.lineTo(rect.right(), rect.bottom() - radius)
            path.quadTo(rect.right(), rect.bottom(), rect.right() - radius, rect.bottom())
            path.lineTo(rect.left() + arrow + shoulder_inset, rect.bottom())
            path.lineTo(rect.left(), rect.center().y())
            path.lineTo(rect.left() + arrow + shoulder_inset, rect.top())
        else:
            path.moveTo(rect.left() + radius, rect.top())
            path.lineTo(rect.right() - arrow - shoulder_inset, rect.top())
            path.lineTo(rect.right(), rect.center().y())
            path.lineTo(rect.right() - arrow - shoulder_inset, rect.bottom())
            path.lineTo(rect.left() + radius, rect.bottom())
            path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - radius)
            path.lineTo(rect.left(), rect.top() + radius)
            path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
        path.closeSubpath()
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg = self._pressed_bg if self.isDown() else self._hover_bg if self.underMouse() else self._normal_bg
        pen_width = 2 if self.underMouse() or self.isDown() else 1
        painter.setPen(QPen(self._border, pen_width))
        painter.setBrush(bg)
        painter.drawPath(self._shape_path())
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font = QFont(self.font())
        font.setWeight(QFont.Weight.Bold if self.underMouse() else QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QPen(self._text))
        text_rect = self.rect().adjusted(12 if self._direction == "left" else 4, 1, -4 if self._direction == "left" else -12, -1)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

class _SmartStrategyCardButton(QPushButton):
    """Strategy card header with isolated per-line text hierarchy.

    Keeps the control as a single checkable QPushButton so the existing
    button group, signals, colors and card layout remain unchanged.
    """

    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None):
        super().__init__(f"{title}\n{subtitle}", parent)
        self._title = title
        self._subtitle_lines = [line for line in subtitle.splitlines() if line.strip()]

    def _resolved_text_color(self) -> QColor:
        forced_color = self.property("forcedTextColor")
        if forced_color:
            return QColor(str(forced_color))
        checked_color = self.property("checkedTextColor")
        normal_color = self.property("normalTextColor")
        color_value = checked_color if self.isChecked() and checked_color else normal_color
        if color_value:
            return QColor(str(color_value))
        return QColor(self.palette().buttonText().color())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setPen(QPen(self._resolved_text_color()))

        body_font = QFont(self.font())
        body_font.setPixelSize(12)
        body_font.setWeight(QFont.Weight.Normal)

        title_font = QFont(body_font)
        title_font.setPixelSize(15)
        title_font.setWeight(QFont.Weight.Bold)

        painter.setFont(title_font)
        title_metrics = painter.fontMetrics()
        painter.setFont(body_font)
        body_metrics = painter.fontMetrics()

        title_height = title_metrics.height()
        body_height = body_metrics.height()
        title_body_gap = 4
        body_line_gap = 1
        subtitle_count = min(2, len(self._subtitle_lines))
        total_height = title_height
        if subtitle_count:
            total_height += title_body_gap + subtitle_count * body_height + max(0, subtitle_count - 1) * body_line_gap
        y = int((self.height() - total_height) / 2)

        rect = self.rect().adjusted(2, 0, -2, 0)
        painter.setFont(title_font)
        painter.drawText(
            QRectF(rect.left(), y, rect.width(), title_height),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._title,
        )
        y += title_height + title_body_gap

        painter.setFont(body_font)
        for line in self._subtitle_lines[:2]:
            painter.drawText(
                QRectF(rect.left(), y, rect.width(), body_height),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                line,
            )
            y += body_height + body_line_gap

class _SmartStepButton(QPushButton):
    def __init__(self, symbol: str, parent: QWidget | None = None):
        super().__init__("", parent)
        self._symbol = symbol
        self.setObjectName("ConfigurationSmartStepButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(18, 18)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def sizeHint(self):
        return QSize(18, 18)

    def minimumSizeHint(self):
        return QSize(18, 18)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        outer = self.rect().adjusted(1, 1, -1, -1)
        fg = QColor(self.palette().buttonText().color())
        ring = QColor(255, 255, 255, 0)
        fill = QColor(255, 255, 255, 0)
        if not self.isEnabled():
            fg.setAlpha(120)
        elif self.isDown():
            fg = QColor("#ffffff")
            ring = QColor(255, 255, 255, 205)
            fill = QColor(255, 255, 255, 22)
        elif self.underMouse():
            fg = QColor("#ffffff")
            ring = QColor(255, 255, 255, 165)
            fill = QColor(255, 255, 255, 10)
        if ring.alpha() > 0:
            pen = QPen(ring)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawEllipse(outer)
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(fg)
        text_rect = self.rect()
        if self._symbol == "+":
            text_rect = text_rect.adjusted(0, -1, 0, 0)
        else:
            text_rect = text_rect.adjusted(0, -2, 0, 0)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), self._symbol)

class _MultiSelectComboBox(QComboBox):
    selectionChanged = Signal()
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # Keep the control non-editable and paint the summary text ourselves.
        # The editable QComboBox path creates a platform QLineEdit inside the
        # combo; on Windows that editor can keep a native bottom frame that
        # appears as an extra horizontal line, different from the other combos.
        self.setEditable(False)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        model = QStandardItemModel(self)
        self.setModel(model)
        self.view().pressed.connect(self._toggle_pressed_index)
        model.itemChanged.connect(self._on_item_changed)
        self._display_text = ""
        self._updating_text = False
        self._suppress_item_changed = False
        self._placeholder_text = "Selecione uma ou mais trilhas"
        self._all_selected_text = "Todas as trilhas"
        self._none_available_text = "Nenhuma trilha disponível"
        self._user_pressed = False
    def mousePressEvent(self, event):
        if self.isEnabled():
            self.showPopup()
            event.accept()
            return
        super().mousePressEvent(event)
    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = self._display_text
        painter = QPainter(self)
        self.style().drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option, painter, self)
        self.style().drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option, painter, self)
    def setPlaceholderTexts(self, placeholder: str, all_selected: str, none_available: str):
        self._placeholder_text = placeholder
        self._all_selected_text = all_selected
        self._none_available_text = none_available
        self._refresh_text()
    def clearItems(self):
        self._suppress_item_changed = True
        try:
            self.model().clear()
        finally:
            self._suppress_item_changed = False
        self._refresh_text()
    def addCheckItem(self, label: str, value, checked: bool = False):
        item = QStandardItem(label)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        item.setData(value, Qt.ItemDataRole.UserRole)
        item.setData(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
        self._suppress_item_changed = True
        try:
            self.model().appendRow(item)
        finally:
            self._suppress_item_changed = False
        self._refresh_text()
    def checkedValues(self):
        values = []
        for row in range(self.model().rowCount()):
            item = self.model().item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                values.append(item.data(Qt.ItemDataRole.UserRole))
        return values
    def setCheckedValues(self, values):
        wanted = {str(v) for v in (values or [])}
        self._suppress_item_changed = True
        try:
            for row in range(self.model().rowCount()):
                item = self.model().item(row)
                if item is None:
                    continue
                item.setCheckState(Qt.CheckState.Checked if str(item.data(Qt.ItemDataRole.UserRole)) in wanted else Qt.CheckState.Unchecked)
        finally:
            self._suppress_item_changed = False
        self._refresh_text()
    def _toggle_pressed_index(self, index):
        item = self.model().itemFromIndex(index)
        if item is None:
            return
        state = item.checkState()
        item.setCheckState(Qt.CheckState.Unchecked if state == Qt.CheckState.Checked else Qt.CheckState.Checked)

    def _on_item_changed(self, item):
        if self._suppress_item_changed:
            return
        self._refresh_text()
        self.selectionChanged.emit()
    def _refresh_text(self):
        labels = []
        total = self.model().rowCount()
        for row in range(total):
            item = self.model().item(row)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                labels.append(item.text())
        if total == 0:
            text = self._none_available_text
        elif len(labels) >= total and total > 1:
            text = self._all_selected_text
        elif not labels:
            text = self._placeholder_text
        elif len(labels) == 1:
            text = labels[0]
        else:
            text = f"{len(labels)} trilhas selecionadas"
        self._updating_text = True
        try:
            self._display_text = text
            # Do not touch the current index while the popup is being opened or
            # is visible. On Qt/Windows, setCurrentIndex(-1) from an item change
            # can immediately close the popup or prevent it from expanding.
            if not self.view().isVisible():
                self.setCurrentIndex(-1)
            self.update()
        finally:
            self._updating_text = False
class _SegmentedChoice(QWidget):
    currentIndexChanged = Signal(int)

    _FRAME_HEIGHT = 32
    _RADIUS = 6

    def __init__(self, parent: QWidget | None = None, *, width: int = 220):
        super().__init__(parent)
        self._values: list[str] = []
        self._labels: list[str] = []
        self._tooltips: list[str] = []
        self._current_index = -1
        self._background_color = QColor("#ffffff")
        self._border_color = QColor("#999999")
        self._divider_color = QColor("#c6c6c6")
        self._selected_bg_color = QColor("#d6a500")
        self._text_color = QColor("#111111")
        self._selected_text_color = QColor("#ffffff")
        self.setObjectName("ConfigurationSegmentedChoice")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)
        self.setMinimumHeight(self._FRAME_HEIGHT)
        self.setMaximumHeight(self._FRAME_HEIGHT)

    def set_visual_tokens(
        self,
        *,
        background: QColor,
        border: QColor,
        divider: QColor,
        selected_bg: QColor,
        text: QColor,
        selected_text: QColor,
    ):
        self._background_color = QColor(background)
        self._border_color = QColor(border)
        self._divider_color = QColor(divider)
        self._selected_bg_color = QColor(selected_bg)
        self._text_color = QColor(text)
        self._selected_text_color = QColor(selected_text)
        self.update()

    def add_option(self, text: str, data: str, *, position: str, tooltip: str = ""):
        self._labels.append(text)
        self._values.append(data)
        self._tooltips.append(str(tooltip or ""))
        if self._current_index < 0:
            self.setCurrentIndex(0)
        else:
            self.update()

    def _tooltip_for_index(self, index: int) -> str:
        if 0 <= index < len(self._tooltips):
            return self._tooltips[index]
        return ""

    def currentData(self):
        if 0 <= self._current_index < len(self._values):
            return self._values[self._current_index]
        return None

    def currentText(self) -> str:
        if 0 <= self._current_index < len(self._labels):
            return self._labels[self._current_index]
        return ""

    def currentIndex(self) -> int:
        return self._current_index

    def setCurrentIndex(self, index: int):
        if not (0 <= index < len(self._values)) or index == self._current_index:
            return
        self._current_index = index
        self.update()
        self.currentIndexChanged.emit(index)

    def setCurrentData(self, value: str):
        if value in self._values:
            self.setCurrentIndex(self._values.index(value))

    def sizeHint(self) -> QSize:
        return QSize(self.minimumWidth(), self._FRAME_HEIGHT)

    def _index_at_position(self, x: int) -> int:
        count = len(self._labels)
        if count <= 0:
            return -1
        inner_width = max(1.0, float(self.width() - 2))
        relative_x = min(max(float(x - 1), 0.0), inner_width - 0.01)
        return min(count - 1, int(relative_x / (inner_width / count)))

    def mouseMoveEvent(self, event):
        index = self._index_at_position(int(event.position().x()))
        tooltip = self._tooltip_for_index(index)
        if self.toolTip() != tooltip:
            self.setToolTip(tooltip)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.toolTip():
            self.setToolTip("")
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._index_at_position(int(event.position().x()))
            if index >= 0:
                self.setCurrentIndex(index)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.setCurrentIndex(max(0, self._current_index - 1))
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.setCurrentIndex(min(len(self._labels) - 1, self._current_index + 1))
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        outer_rect = QRectF(0.5, 0.5, max(1, self.width() - 1), max(1, self.height() - 1))
        inner_rect = QRectF(1.0, 1.0, max(1, self.width() - 2), max(1, self.height() - 2))

        count = len(self._labels)
        if count <= 0:
            return

        outer_path = QPainterPath()
        outer_path.addRoundedRect(outer_rect, self._RADIUS, self._RADIUS)
        painter.fillPath(outer_path, self._background_color)

        segment_width = outer_rect.width() / count
        painter.save()
        painter.setClipPath(outer_path)
        if 0 <= self._current_index < count:
            # Fill the selected segment all the way to the outer clipped shape.
            # This prevents the selected left/right segment from showing an
            # accidental dark button border over the yellow fill.
            selected_rect = QRectF(
                outer_rect.left() + segment_width * self._current_index,
                outer_rect.top(),
                segment_width,
                outer_rect.height(),
            )
            if self._current_index == 0:
                selected_rect.adjust(0.0, 0.0, 1.0, 0.0)
            elif self._current_index == count - 1:
                selected_rect.adjust(-1.0, 0.0, 0.0, 0.0)
            else:
                selected_rect.adjust(-0.5, 0.0, 0.5, 0.0)
            painter.fillRect(selected_rect, self._selected_bg_color)

        painter.setPen(QPen(self._divider_color, 1))
        for index in range(1, count):
            if index == self._current_index or index == self._current_index + 1:
                continue
            x = outer_rect.left() + segment_width * index
            painter.drawLine(int(round(x)), int(inner_rect.top()), int(round(x)), int(inner_rect.bottom()))
        painter.restore()

        border_color = QColor(self._border_color)
        if 0 <= self._current_index < count:
            border_color.setAlpha(min(border_color.alpha(), 170))
        painter.setPen(QPen(border_color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(outer_rect, self._RADIUS, self._RADIUS)

        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        text_segment_width = inner_rect.width() / count
        for index, label in enumerate(self._labels):
            text_rect = QRectF(
                inner_rect.left() + text_segment_width * index,
                inner_rect.top(),
                text_segment_width,
                inner_rect.height(),
            )
            painter.setPen(self._selected_text_color if index == self._current_index else self._text_color)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)
class _PreserveReduceSelector(QWidget):
    """Single-combo control for Advanced FPS/Audio policy.
    The first entry preserves the original value. Any other entry represents
    an explicit reduction target. Keeping the old class name preserves the
    rest of the panel wiring while removing the separate "Preservar" button.
    """
    currentIndexChanged = Signal(int)
    def __init__(self, parent: QWidget | None = None, *, width: int = 220, unit: str = "", values: tuple[int, ...] = ()): 
        super().__init__(parent)
        self._unit = unit
        self._values = list(values)
        self._original_label = "Original"
        self._original_value: float | None = None
        self._suppress = False
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(width)
        self.setMaximumWidth(width)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._combo = _ConfigurationComboBox(self)
        self._combo.setObjectName("ConfigurationInlineCombo")
        self._combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        self._combo.activated.connect(self._on_combo_changed)
        layout.addWidget(self._combo, 1)
        self._rebuild_items()
    def _format_reduction_label(self, value: int) -> str:
        suffix = f" {self._unit}" if self._unit else ""
        return f"{value}{suffix}"
    def _rebuild_items(self, selected_policy: str | None = None, selected_value: int | None = None):
        current_policy = selected_policy or self.currentData() or "keep"
        current_value = selected_value if selected_value is not None else self.currentValue()
        self._suppress = True
        try:
            self._combo.clear()
            self._combo.addItem(self._original_label, ("keep", None))
            for value in self._values:
                try:
                    numeric = float(value)
                except Exception:
                    numeric = None
                if self._original_value is not None and numeric is not None and numeric >= float(self._original_value):
                    continue
                self._combo.addItem(self._format_reduction_label(int(value)), ("reduce_to_value", int(value)))
            self._set_current_data_unblocked(current_policy, current_value)
        finally:
            self._suppress = False
    def _on_combo_changed(self, index: int):
        if self._suppress:
            return
        self.currentIndexChanged.emit(index)
    def currentData(self):
        data = self._combo.currentData()
        if isinstance(data, tuple) and len(data) >= 1:
            return data[0]
        return "keep"
    def currentText(self) -> str:
        return self._combo.currentText()
    def currentIndex(self) -> int:
        return self._combo.currentIndex()
    def currentValue(self) -> int | None:
        data = self._combo.currentData()
        if isinstance(data, tuple) and len(data) >= 2:
            value = data[1]
            if value is None:
                return None
            try:
                return int(value)
            except Exception:
                return None
        return None
    def setCurrentIndex(self, index: int):
        self._combo.setCurrentIndex(max(0, min(index, self._combo.count() - 1)))
    def _set_current_data_unblocked(self, value: str, reduce_value: int | None = None):
        normalized = str(value or "keep").strip().lower()
        if normalized in {"keep", "preserve", "preservar"}:
            self._combo.setCurrentIndex(0)
            return
        if reduce_value is not None:
            try:
                target_value = int(round(float(reduce_value)))
            except Exception:
                target_value = None
            for idx in range(self._combo.count()):
                data = self._combo.itemData(idx)
                if isinstance(data, tuple) and len(data) >= 2:
                    try:
                        if data[0] == "reduce_to_value" and target_value is not None and int(data[1]) == target_value:
                            self._combo.setCurrentIndex(idx)
                            return
                    except Exception:
                        continue
            if target_value is not None:
                if self._original_value is None or float(target_value) < float(self._original_value):
                    insert_at = max(1, self._combo.count())
                    for idx in range(1, self._combo.count()):
                        data = self._combo.itemData(idx)
                        if not (isinstance(data, tuple) and len(data) >= 2):
                            continue
                        try:
                            existing = int(data[1])
                        except Exception:
                            continue
                        if target_value > existing:
                            insert_at = idx
                            break
                    self._combo.insertItem(insert_at, self._format_reduction_label(target_value), ("reduce_to_value", target_value))
                    self._combo.setCurrentIndex(insert_at)
                    return
        self._combo.setCurrentIndex(1 if self._combo.count() > 1 else 0)
    def setCurrentData(self, value: str, reduce_value: int | None = None):
        self._suppress = True
        try:
            self._set_current_data_unblocked(value, reduce_value)
        finally:
            self._suppress = False
    def setOriginalOption(self, label: str, original_value: float | None = None):
        label = str(label or "Original").strip() or "Original"
        current_policy = self.currentData() or "keep"
        current_value = self.currentValue()
        self._original_label = label
        try:
            self._original_value = float(original_value) if original_value is not None else None
        except Exception:
            self._original_value = None
        self._rebuild_items(current_policy, current_value)
def _format_bytes(size_bytes) -> str | None:
    return _shared_format_bytes(size_bytes)
def _summary_pair(label: str, before: str | None, after: str | None, *, bold_label: bool = False) -> str:
    left = before or "?"
    right = after or left
    rendered_label = f"<b>{label}</b>" if bold_label else label
    return f"{rendered_label}: {left} → {right}"
def _format_intensity(value: float) -> str:
    return f"{int(round(value))} / 10"
def _describe_intensity(value: float) -> str:
    if value <= 3.3:
        return "Baixa"
    if value <= 6.6:
        return "Média"
    return "Alta"
def _segment_position(index: int, total: int) -> str:
    if total <= 1:
        return "single"
    if index == 0:
        return "left"
    if index == total - 1:
        return "right"
    return "middle"
class _ConfigurationCloseButton(QToolButton):
    """Tool button that paints a centered vector close mark.

    Text glyphs have font baselines, so even AlignCenter can look low. This
    button draws the X as two centered vector strokes, independent of font
    metrics and consistent in light/dark themes.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x_color = QColor()
        self._x_size = 8
        self._x_pen_width = 1.35
        self.setText("")
        self.setIcon(self.icon())

    def set_x_style(self, color: QColor, *, font_size: int = 13, font_weight: QFont.Weight = QFont.Weight.DemiBold) -> None:
        self._x_color = QColor(color)
        # Keep the public signature stable for callers, but convert the former
        # font-size intent into vector geometry.
        self._x_size = max(7, min(10, int(round(font_size * 0.62))))
        self._x_pen_width = 1.45 if font_weight >= QFont.Weight.DemiBold else 1.15
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.text = ""
        option.icon = self.icon().__class__()
        self.style().drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option, painter, self)

        color = QColor(self._x_color)
        if not color.isValid():
            color = self.palette().buttonText().color()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(color, self._x_pen_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = self.rect()
        half = self._x_size / 2.0
        # Visual calibration from the rendered button screenshots: the vector
        # mark was one pixel high/left inside the button frame. Use the button
        # geometry, not font metrics, then apply a single-pixel optical offset.
        cx = (rect.left() + rect.right()) / 2.0 + 1.0
        cy = (rect.top() + rect.bottom()) / 2.0 + 1.0
        painter.drawLine(QPointF(cx - half, cy - half), QPointF(cx + half, cy + half))
        painter.drawLine(QPointF(cx + half, cy - half), QPointF(cx - half, cy + half))
        painter.end()



class _ConfigurationCheckBox(QCheckBox):
    """Theme-stable checkbox with custom-painted indicator.

    Native checkbox indicators can keep stale palette colors after a light/dark
    transition. Painting the indicator here keeps checked/unchecked states
    explicit and repainted whenever _apply_theme_styles() runs.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._indicator_bg = QColor()
        self._indicator_border = QColor()
        self._indicator_checked_bg = QColor()
        self._indicator_checked_border = QColor()
        self._indicator_check = QColor()
        self._text_color = QColor()
        self._indicator_size = 16
        self._indicator_radius = 3
        self._spacing = 7
        self.setMinimumHeight(20)

    def set_theme_colors(
        self,
        *,
        text_color: QColor,
        indicator_bg: QColor,
        indicator_border: QColor,
        checked_bg: QColor,
        checked_border: QColor,
        check_color: QColor,
    ) -> None:
        self._text_color = QColor(text_color)
        self._indicator_bg = QColor(indicator_bg)
        self._indicator_border = QColor(indicator_border)
        self._indicator_checked_bg = QColor(checked_bg)
        self._indicator_checked_border = QColor(checked_border)
        self._indicator_check = QColor(check_color)
        self.update()

    def sizeHint(self):
        hint = super().sizeHint()
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(max(hint.width(), self._indicator_size + self._spacing + text_width + 2), max(hint.height(), 20))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        enabled = self.isEnabled()
        checked = self.isChecked()
        box = self._indicator_size
        x = 0
        y = max(0, (self.height() - box) // 2)
        rect = QRectF(x + 0.5, y + 0.5, box - 1, box - 1)

        bg = QColor(self._indicator_checked_bg if checked else self._indicator_bg)
        border = QColor(self._indicator_checked_border if checked else self._indicator_border)
        check = QColor(self._indicator_check)
        text_color = QColor(self._text_color)

        if not enabled:
            bg.setAlpha(max(70, bg.alpha() // 2))
            border.setAlpha(max(80, border.alpha() // 2))
            check.setAlpha(max(90, check.alpha() // 2))
            text_color.setAlpha(max(90, text_color.alpha() // 2))

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, self._indicator_radius, self._indicator_radius)

        if checked:
            pen = QPen(check, 1.9)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPointF(x + 4.2, y + 8.4), QPointF(x + 7.0, y + 11.1))
            painter.drawLine(QPointF(x + 7.0, y + 11.1), QPointF(x + 12.2, y + 5.0))

        painter.setPen(text_color if text_color.isValid() else self.palette().text().color())
        text_rect = self.rect().adjusted(box + self._spacing, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        painter.end()




class ConfigurationPanelWidget(QFrame):
    closeRequested = Signal()
    applyRequested = Signal()
    restoreRequested = Signal()
    quickPresetAutoApplyRequested = Signal(dict)
    smartProfileAutoApplyRequested = Signal(dict)
    QUICK_PRESETS = [
        ("Compressão Muito Baixa", "Compressão mínima com foco total em qualidade"),
        ("Compressão Baixa", "Compressão muito leve com perda quase imperceptível"),
        ("Compressão Baixa+", "Prioriza qualidade visual com redução discreta"),
        ("Compressão Moderada", "Alta qualidade com redução moderada"),
        ("Compressão Moderada+", "Reduz bitrate mantendo resolução/FPS"),
        ("Compressão Equilibrada", "Reduz bitrate com boa legibilidade"),
        ("Compressão Alta", "Reduz mais bitrate para uso geral"),
        ("Compressão Alta+", "Arquivo menor com perda controlada"),
        ("Compressão Muito Alta", "Reduz resolução mantendo FPS"),
        ("Compressão Máxima", "Reduz resolução para economizar espaço"),
        ("Compressão Extrema 1", "Reduz resolução com maior economia"),
        ("Compressão Extrema 2", "Maior redução de resolução"),
        ("Compressão Extrema 3", "Menor tamanho com perda visual alta"),
        ("Compressão Extrema 4", "Menor tamanho com forte perda visual"),
        ("Compressão Extrema 5", "Menor tamanho com grande queda de qualidade"),
        ("Compressão Extrema 6", "Menor tamanho com queda extrema de qualidade"),
    ]
    MODE_INDEX_BY_NAME = {
        "quick": 0,
        "smart": 1,
        "advanced": 2,
        "audio": 3,
    }
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ConfigurationPanelWidget")
        self.setFrameShape(QFrame.NoFrame)
        self.service = ConfigurationService.instance()
        self._mode_buttons: list[QToolButton] = []
        self._context_job = None
        self._context_selected_count = 1
        self._locked = False
        self._quick_preset_index = default_quick_profile()["quick_profile_preset"]
        self._quick_level_buttons: list[QPushButton] = []
        self._quick_aggressive_buttons: list[QPushButton] = []
        self._suppress_live_updates = False
        self._smart_audio_track_mode = "all"
        self._smart_summary_timer = QTimer(self)
        self._smart_summary_timer.setSingleShot(True)
        self._smart_summary_timer.setInterval(75)
        self._smart_summary_timer.timeout.connect(self._refresh_smart_summary)
        self._advanced_resolution_sync = False
        self._advanced_sync_in_progress = False
        self._advanced_last_driver = "target_size_mb"
        self._advanced_last_resolution_driver = None
        self._advanced_estimated_size_mb = None
        self._advanced_estimated_bitrate_bps = None
        self._advanced_target_auto_adjusted_by_audio = False
        self._advanced_auto_high_compression_notice = False
        self._advanced_auto_balanced_notice = False
        self._quick_preview_cache_key = None
        self._quick_preview_cache_value = (None, None, None, None)
        self._quick_preset_apply_timer = QTimer(self)
        self._quick_preset_apply_timer.setSingleShot(True)
        self._quick_preset_apply_timer.setInterval(85)
        self._quick_preset_apply_timer.timeout.connect(self._on_quick_preset_value_changed)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(0)
        self.mode_bar = QFrame(self)
        self.mode_bar.setObjectName("ConfigurationModeBar")
        mode_bar_layout = QHBoxLayout(self.mode_bar)
        mode_bar_layout.setContentsMargins(0, 0, 0, 0)
        mode_bar_layout.setSpacing(2)
        root.addWidget(self.mode_bar)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.idClicked.connect(self._on_mode_changed)
        self.btn_quick = self._create_mode_button("Rápido", 0)
        self.btn_smart = self._create_mode_button("Estratégico", 1)
        self.btn_advanced = self._create_mode_button("Avançado", 2)
        self.btn_audio = self._create_mode_button("Áudio", 3)
        for button in self._mode_buttons:
            mode_bar_layout.addWidget(button)
        mode_bar_layout.addStretch(1)
        self._build_video_output_panel()
        self.btn_analyze_media = QPushButton("Analisar mídia", self.mode_bar)
        self.btn_analyze_media.setObjectName("ConfigurationPanelAnalyzeButton")
        self.btn_analyze_media.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_analyze_media.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.btn_analyze_media.setIconSize(QSize(14, 14))
        self.btn_analyze_media.setToolTip("Analisa o arquivo em foco e mostra a sugestão automática.")
        self.btn_analyze_media.clicked.connect(self._show_automatic_analysis_dialog)
        mode_bar_layout.addWidget(self.btn_analyze_media)
        self.btn_defaults = QPushButton("Restaurar padrões", self.mode_bar)
        self.btn_defaults.setObjectName("ConfigurationPanelDefaultsButton")
        self.btn_defaults.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_defaults.clicked.connect(self.restoreRequested.emit)
        mode_bar_layout.addWidget(self.btn_defaults)
        self.btn_apply = QPushButton("Aplicar a todos", self.mode_bar)
        self.btn_apply.setObjectName("ConfigurationPanelApplyButton")
        self.btn_apply.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_apply.clicked.connect(self._on_apply_clicked)
        mode_bar_layout.addWidget(self.btn_apply)
        self.btn_close = _ConfigurationCloseButton(self.mode_bar)
        self.btn_close.setObjectName("ConfigurationPanelCloseButton")
        self.btn_close.setToolTip("Fechar")
        self.btn_close.setAutoRaise(False)
        self.btn_close.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_close.clicked.connect(self.closeRequested.emit)
        mode_bar_layout.addWidget(self.btn_close)
        self.mode_stack = QStackedWidget(self)
        self.mode_stack.setObjectName("ConfigurationModeStack")
        root.addWidget(self.mode_stack, 1)
        self.mode_stack.addWidget(self._build_quick_page())
        self.mode_stack.addWidget(self._build_smart_page())
        self.mode_stack.addWidget(self._build_advanced_page())
        self.mode_stack.addWidget(self._build_audio_page())
        self._load()
        self.btn_quick.setChecked(True)
        self.mode_stack.setCurrentIndex(0)
        self._sync_analyze_media_button_state()
        self._place_video_output_panel()
        self._apply_theme_styles()
        self._sync_mode_bar_action_button_widths()
    def _build_video_output_panel(self):
        self.video_output_panel = QWidget(self)
        self.video_output_panel.setObjectName("VideoTopHighlightRow")
        self.video_output_panel.setStyleSheet(
            "QWidget#VideoTopHighlightRow { background: rgba(0,0,0,0.035); border-radius: 8px; }"
        )
        self.video_output_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        panel_layout = QHBoxLayout(self.video_output_panel)
        panel_layout.setContentsMargins(12, 7, 12, 7)
        panel_layout.setSpacing(8)
        self.video_output_label = QLabel("Formato de saída", self.video_output_panel)
        self.video_output_label.setObjectName("ConfigurationFormLabel")
        self.video_output_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label_font = self.video_output_label.font()
        label_font.setBold(True)
        self.video_output_label.setFont(label_font)
        panel_layout.addWidget(self.video_output_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.video_output_format = _ConfigurationComboBox(self.video_output_panel)
        self.video_output_format.setObjectName("ConfigurationInlineCombo")
        self.video_output_format.setMinimumWidth(96)
        self.video_output_format.setMaximumWidth(104)
        self.video_output_format.setToolTip("Define o formato/container final do vídeo.")
        self._video_output_options = ("MP4", "MKV", "WEBM", "MOV", "AVI", "FLV", "TS", "M4V")
        for fmt in self._video_output_options:
            self.video_output_format.addItem(fmt, fmt.lower())
        self.video_output_format.currentIndexChanged.connect(self._on_video_output_format_changed)
        panel_layout.addWidget(self.video_output_format, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.video_output_hint = QLabel("• Define o formato/container final do vídeo", self.video_output_panel)
        self.video_output_hint.setObjectName("ConfigurationMutedHint")
        self.video_output_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.video_output_hint.setWordWrap(False)
        self.video_output_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        panel_layout.addWidget(self.video_output_hint, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_top_target_size_group = QWidget(self.video_output_panel)
        self.advanced_top_target_size_group.setObjectName("AdvancedTopTargetSizeGroup")
        self.advanced_top_target_size_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_top_target_size_layout = QHBoxLayout(self.advanced_top_target_size_group)
        self.advanced_top_target_size_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_top_target_size_layout.setSpacing(8)
        self.advanced_top_target_size_label = QLabel("Tamanho desejado", self.advanced_top_target_size_group)
        self.advanced_top_target_size_label.setObjectName("ConfigurationFormLabel")
        self.advanced_top_target_size_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top_target_font = self.advanced_top_target_size_label.font()
        top_target_font.setBold(True)
        self.advanced_top_target_size_label.setFont(top_target_font)
        self.advanced_top_target_size_layout.addWidget(self.advanced_top_target_size_label, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        panel_layout.addWidget(self.advanced_top_target_size_group, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_top_target_size_group.hide()
        self.reduce_audio_quality = _ConfigurationCheckBox("Permitir reduzir qualidade do áudio", self.video_output_panel)
        self.reduce_audio_quality.setObjectName("ConfigurationReduceAudioQualityCheck")
        self.reduce_audio_quality.setToolTip("Autoriza reduzir mais o bitrate do áudio para gerar arquivos menores quando necessário.")
        self.reduce_audio_quality.setChecked(False)
        self.reduce_audio_quality.toggled.connect(self._on_video_audio_quality_changed)
        self.reduce_audio_quality.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        panel_layout.addWidget(self.reduce_audio_quality, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._video_output_panel_layout = None
        self.video_output_panel.hide()

    def _place_video_output_panel(self):
        panel = getattr(self, "video_output_panel", None)
        if panel is None:
            return
        visible = self._current_mode_name() in {"quick", "smart", "advanced"} and not self._is_audio_only_context()
        target_layout = None
        if visible:
            mode = self._current_mode_name()
            target_layout = {
                "quick": getattr(self, "quick_content_layout", None),
                "smart": getattr(self, "smart_content_layout", None),
                "advanced": getattr(self, "advanced_content_layout", None),
            }.get(mode)
        previous_layout = getattr(self, "_video_output_panel_layout", None)
        if target_layout is not None and previous_layout is not target_layout:
            if previous_layout is not None:
                previous_index = previous_layout.indexOf(panel)
                if previous_index >= 0:
                    previous_layout.takeAt(previous_index)
            target_layout.insertWidget(0, panel, 0)
            self._video_output_panel_layout = target_layout
        panel.setVisible(bool(visible and target_layout is not None))

    def _current_video_output_format(self) -> str:
        combo = getattr(self, "video_output_format", None)
        if combo is None:
            return "mp4"
        return str(combo.currentData() or combo.currentText() or "mp4").strip().lower()

    def _current_video_output_extension(self) -> str:
        return "." + self._current_video_output_format().lstrip(".")

    def _set_video_output_format_from_source(self, source):
        raw = getattr(source, "video_output_format", None)
        if raw is None:
            raw = getattr(source, "video_output_extension", None)
        if raw is None and isinstance(source, dict):
            raw = source.get("video_output_format") or source.get("video_output_extension") or source.get("video_format")
        fmt = str(raw or "mp4").strip().lower().lstrip(".")
        aliases = {"mpeg4": "mp4"}
        fmt = aliases.get(fmt, fmt)
        combo = getattr(self, "video_output_format", None)
        if combo is None:
            return
        index = combo.findData(fmt)
        if index < 0:
            index = combo.findText(fmt.upper())
        if index >= 0:
            combo.setCurrentIndex(index)

    def _sync_video_output_visibility(self):
        current_mode = self._current_mode_name()
        visible = current_mode in {"quick", "smart", "advanced"} and not self._is_audio_only_context()
        self._place_video_output_panel()
        if hasattr(self, "video_output_format"):
            self.video_output_format.setEnabled(visible and not self._locked)
        if hasattr(self, "advanced_top_target_size_group"):
            self.advanced_top_target_size_group.setVisible(visible and current_mode == "advanced")
            self.advanced_target_size_mb.setEnabled(visible and current_mode == "advanced" and not self._locked)
        if hasattr(self, "reduce_audio_quality"):
            self.reduce_audio_quality.setVisible(visible and current_mode in {"quick", "smart"})

    def _on_video_output_format_changed(self, *_args):
        if self._suppress_live_updates:
            return
        if self._current_mode_name() in {"quick", "smart", "advanced"}:
            self._sync_profile_preview_to_context(emit_apply=False)

    def _on_video_audio_quality_changed(self, *_args):
        if self._suppress_live_updates:
            return
        current_mode = self._current_mode_name()
        if current_mode == "quick":
            self._quick_preview_cache_key = None
            self._quick_preview_cache_value = None
            self._set_quick_estimate_pending_placeholders()
            self._schedule_quick_preset_apply()
            return
        if current_mode == "smart":
            self._smart_summary_timer.stop()
            job = self._context_job
            if job is not None:
                setattr(job, "estimated_size_bytes", None)
                setattr(job, "estimated_output_bitrate", None)
                if not profile_output_matches_current_settings(job):
                    setattr(job, "output_size_bytes", None)
                    setattr(job, "output_bitrate", None)
            self._sync_profile_preview_to_context(emit_apply=False)
            self._update_smart_summary()
            return
        if current_mode == "advanced":
            self._advanced_last_driver = self._advanced_last_driver or "target_size_mb"
            self._recalculate_advanced_targets_from_driver(self._advanced_last_driver)
            return

    def _audio_quality_reduction_allowed(self) -> bool:
        checkbox = getattr(self, "reduce_audio_quality", None)
        return bool(checkbox.isChecked()) if checkbox is not None else False

    def _create_mode_button(self, text: str, mode_id: int) -> QToolButton:
        button = QToolButton(self.mode_bar)
        button.setText(text)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setAutoRaise(False)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.mode_group.addButton(button, mode_id)
        self._mode_buttons.append(button)
        return button
    def _build_automatic_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("ConfigurationAutomaticPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        content_frame = QFrame(page)
        content_frame.setObjectName("ConfigurationContentFrame")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(12)
        page_layout.addWidget(content_frame, 1)

        section = self._create_section()
        section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        section_layout = section.layout()
        section_layout.setContentsMargins(22, 20, 22, 20)
        section_layout.setSpacing(10)

        self.automatic_title = QLabel("Análise automática", section)
        self.automatic_title.setObjectName("ConfigurationPlaceholderTitle")
        self.automatic_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.automatic_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        section_layout.addWidget(self.automatic_title)

        self.automatic_status = QLabel("Aguardando arquivo selecionado", section)
        self.automatic_status.setObjectName("ConfigurationPlaceholderBody")
        self.automatic_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.automatic_status.setWordWrap(True)
        self.automatic_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        section_layout.addWidget(self.automatic_status)

        self.automatic_hint = QLabel("Selecione um arquivo para ver a sugestão automática calculada por metadados.", section)
        self.automatic_hint.setObjectName("ConfigurationPlaceholderHint")
        self.automatic_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.automatic_hint.setWordWrap(True)
        self.automatic_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        section_layout.addWidget(self.automatic_hint)

        self.automatic_result = QLabel("", section)
        self.automatic_result.setObjectName("ConfigurationPlaceholderBody")
        self.automatic_result.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.automatic_result.setTextFormat(Qt.TextFormat.RichText)
        self.automatic_result.setWordWrap(True)
        self.automatic_result.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        section_layout.addWidget(self.automatic_result)

        section_layout.addStretch(1)

        content_layout.addWidget(section, 1)
        return page
    def _build_quick_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("ConfigurationQuickPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        content_frame = QFrame(page)
        content_frame.setObjectName("ConfigurationContentFrame")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(12)
        self.quick_content_layout = content_layout
        page_layout.addWidget(content_frame, 1)
        slider_section = self._create_section()
        slider_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        slider_layout = slider_section.layout()
        slider_layout.setContentsMargins(14, 12, 14, 12)
        slider_layout.setSpacing(0)
        slider_layout.addStretch(1)
        self.quick_instruction_label = QLabel("Escolha o nível de compressão")
        self.quick_instruction_label.setObjectName("ConfigurationQuickInstruction")
        self.quick_instruction_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        slider_layout.addWidget(self.quick_instruction_label, 0, Qt.AlignmentFlag.AlignHCenter)
        slider_layout.addSpacing(24)
        quick_levels_row = QFrame(slider_section)
        quick_levels_row.setObjectName("ConfigurationQuickLevelsRow")
        quick_levels_layout = QGridLayout(quick_levels_row)
        quick_levels_layout.setContentsMargins(0, 0, 0, 0)
        quick_levels_layout.setHorizontalSpacing(5)
        quick_levels_layout.setVerticalSpacing(10)
        self.quick_left_hint = _QuickExtremeArrowButton("Maior\nqualidade", "left", quick_levels_row)
        self.quick_left_hint.setObjectName("ConfigurationQuickExtremeHint")
        self.quick_left_hint.setProperty("qualityExtreme", True)
        self.quick_left_hint.setToolTip("Volta um nível de compressão, priorizando mais qualidade.")
        self.quick_left_hint.setFixedWidth(72)
        self.quick_left_hint.clicked.connect(lambda checked=False: self._on_quick_extreme_step(-1))
        quick_levels_layout.addWidget(self.quick_left_hint, 0, 0, 1, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for index in range(len(self.QUICK_PRESETS)):
            button = QPushButton(str(index + 1), quick_levels_row)
            button.setObjectName("ConfigurationQuickLevelButton")
            button.setCheckable(True)
            button.setProperty("levelIndex", index)
            is_quality = 0 <= index <= 3
            is_balanced = 4 <= index <= 7
            is_scale = 8 <= index <= 11
            is_aggressive = index >= 12
            button.setProperty("qualityLevel", is_quality)
            button.setProperty("balancedLevel", is_balanced)
            button.setProperty("scaleLevel", is_scale)
            button.setProperty("aggressiveLevel", is_aggressive)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            preset_title, preset_summary = self.QUICK_PRESETS[index]
            tooltip = f"Nível {index + 1} — {preset_title}\n{preset_summary}"
            if is_scale:
                tooltip += "\nReduz resolução mantendo o FPS original."
            if is_aggressive:
                tooltip += "\nReduz resolução e FPS para buscar arquivos menores."
                tooltip += "\nAviso: a qualidade pode cair bastante nestes níveis."
                self._quick_aggressive_buttons.append(button)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda checked=False, value=index: self._on_quick_level_button_clicked(value))
            self._quick_level_buttons.append(button)
            quick_levels_layout.addWidget(button, 0, index + 1)
            quick_levels_layout.setColumnMinimumWidth(index + 1, 37)
        self.quick_right_hint = _QuickExtremeArrowButton("Menor\ntamanho", "right", quick_levels_row)
        self.quick_right_hint.setObjectName("ConfigurationQuickExtremeHint")
        self.quick_right_hint.setProperty("aggressiveExtreme", True)
        self.quick_right_hint.setToolTip("Avança um nível de compressão, priorizando menor tamanho.")
        self.quick_right_hint.setFixedWidth(72)
        self.quick_right_hint.clicked.connect(lambda checked=False: self._on_quick_extreme_step(1))
        quick_levels_layout.addWidget(self.quick_right_hint, 0, len(self.QUICK_PRESETS) + 1, 1, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.quick_quality_hint = QLabel("Preserva qualidade")
        self.quick_quality_hint.setObjectName("ConfigurationQuickQualityHint")
        self.quick_quality_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        quick_levels_layout.addWidget(self.quick_quality_hint, 1, 1, 1, 4, Qt.AlignmentFlag.AlignHCenter)
        self.quick_balanced_hint = QLabel("Reduz bitrate")
        self.quick_balanced_hint.setObjectName("ConfigurationQuickBalancedHint")
        self.quick_balanced_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        quick_levels_layout.addWidget(self.quick_balanced_hint, 1, 5, 1, 4, Qt.AlignmentFlag.AlignHCenter)
        self.quick_scale_hint = QLabel("Reduz resolução")
        self.quick_scale_hint.setObjectName("ConfigurationQuickScaleHint")
        self.quick_scale_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        quick_levels_layout.addWidget(self.quick_scale_hint, 1, 9, 1, 4, Qt.AlignmentFlag.AlignHCenter)
        self.quick_aggressive_hint = QLabel("Reduz resolução/FPS")
        self.quick_aggressive_hint.setObjectName("ConfigurationQuickAggressiveHint")
        self.quick_aggressive_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        quick_levels_layout.addWidget(self.quick_aggressive_hint, 1, 13, 1, 4, Qt.AlignmentFlag.AlignHCenter)
        slider_layout.addWidget(quick_levels_row, 0, Qt.AlignmentFlag.AlignHCenter)
        slider_layout.addSpacing(20)
        self.quick_selected_line = QLabel()
        self.quick_selected_line.setObjectName("ConfigurationQuickPresetLine")
        self.quick_selected_line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.quick_selected_line.setTextFormat(Qt.TextFormat.RichText)
        self.quick_selected_line.setWordWrap(False)
        slider_layout.addWidget(self.quick_selected_line, 0, Qt.AlignmentFlag.AlignHCenter)
        # Mantido apenas como atributo de compatibilidade. O aviso extremo agora
        # entra na própria linha do nível selecionado, para não criar uma linha
        # extra nem empurrar o conteúdo do overlay.
        self.quick_quality_drop_warning = None
        slider_layout.addStretch(1)
        summary_section = self._create_section()
        summary_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        summary_layout = summary_section.layout()
        summary_layout.setContentsMargins(14, 15, 14, 15)
        summary_layout.setSpacing(6)
        self.quick_result_summary = QLabel()
        self.quick_result_summary.setObjectName("ConfigurationPreviewLabel")
        self.quick_result_summary.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.quick_result_summary.setWordWrap(True)
        self.quick_result_summary.setTextFormat(Qt.TextFormat.RichText)
        self.quick_result_summary.setContentsMargins(0, 0, 0, 0)
        summary_layout.addWidget(self.quick_result_summary)
        content_layout.addWidget(slider_section, 1)
        content_layout.addWidget(summary_section, 0)
        return page
    def _create_segmented_button(self, text: str, parent: QWidget, group: QButtonGroup, *, position: str, checked_slot=None) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName("ConfigurationSegmentButton")
        button.setProperty("segmentPosition", position)
        button.setCheckable(True)
        button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        if checked_slot is not None:
            button.toggled.connect(checked_slot)
        group.addButton(button)
        return button
    def _build_smart_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        content_frame = QFrame(page)
        content_frame.setObjectName("ConfigurationContentFrame")
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(4)
        self.smart_content_layout = content_layout
        layout.addWidget(content_frame, 1)
        strategy_section = self._create_section("Estratégia")
        strategy_section.setObjectName("ConfigurationSmartStrategySection")
        strategy_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        strategy_layout = strategy_section.layout()
        # Transparent section: align the strategy cards with the audio block
        # below and avoid the large empty padding left by the former bordered
        # section frame.
        strategy_layout.setContentsMargins(0, 0, 0, 0)
        strategy_layout.setSpacing(0)
        strategy_section_title = strategy_layout.itemAt(0).widget()
        if isinstance(strategy_section_title, QLabel):
            strategy_layout.removeWidget(strategy_section_title)
            strategy_section_title.deleteLater()
        # The cards communicate the available strategies directly; keeping an
        # extra title here adds visual noise and consumes vertical room that is
        # better used by the explanatory card copy.
        strategy_layout.addSpacing(0)
        self.smart_strategy_group = QButtonGroup(self)
        self.smart_strategy_group.setExclusive(True)
        self.smart_strategy_buttons = {}
        self._smart_intensity_values = {}
        default_intensity = int(round(float(default_smart_profile().get("slider_value", 5.0)) * 10))
        default_intensity = max(0, min(100, default_intensity))
        for strategy in SMART_STRATEGY_UI_ORDER:
            self._smart_intensity_values[strategy] = default_intensity
        strategy_cards = QWidget(strategy_section)
        strategy_cards.setObjectName("ConfigurationSmartStrategyCards")
        strategy_cards.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Keep explicit paint room for the card bottom border. Without an
        # explicit wrapper height, Qt may compress this fixed-policy row to the
        # cards' exact height, and the rounded bottom border gets clipped.
        strategy_cards.setMinimumHeight(202)
        strategy_cards.setMaximumHeight(202)
        strategy_cards_layout = QHBoxLayout(strategy_cards)
        strategy_cards_layout.setContentsMargins(0, 0, 0, 8)
        strategy_cards_layout.setSpacing(10)
        strategy_subtitles = {
            "Qualidade Prioritária": "Mantém resolução\nMenor perda visual",
            "Equilíbrio": "Reduz bitrate com controle\nBoa qualidade",
            "Economia Máxima": "Diminui resolução\nProcura evitar excesso de borrão",
        }
        strategy_tones = {
            "Qualidade Prioritária": "quality",
            "Equilíbrio": "balanced",
            "Economia Máxima": "aggressive",
        }
        self.smart_strategy_cards = {}
        self.smart_intensity_sliders = {}
        self.smart_intensity_minus_buttons = {}
        self.smart_intensity_plus_buttons = {}
        self.smart_intensity_controls = {}
        self.smart_intensity_title_labels = {}
        self.smart_intensity_value_labels = {}
        for strategy in SMART_STRATEGY_UI_ORDER:
            label = SMART_STRATEGY_DISPLAY_LABELS.get(strategy, strategy)
            subtitle = strategy_subtitles.get(strategy, "Estratégia")
            card = QFrame(strategy_cards)
            card.setObjectName("ConfigurationSmartStrategyCard")
            card.setProperty("strategy_key", strategy)
            card.setProperty("strategyTone", strategy_tones.get(strategy, "balanced"))
            card.setProperty("active", False)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.mousePressEvent = lambda event, key=strategy: self._select_smart_strategy_card(key, event)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            card.setMinimumHeight(194)
            card.setMaximumHeight(194)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(6)
            card_layout.addStretch(1)
            header_button = _SmartStrategyCardButton(label, subtitle, card)
            header_button.setObjectName("ConfigurationSmartStrategyCardButton")
            header_button.setProperty("strategy_key", strategy)
            header_button.setProperty("strategyTone", strategy_tones.get(strategy, "balanced"))
            header_button.setCheckable(True)
            header_button.setCursor(Qt.CursorShape.PointingHandCursor)
            header_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            header_button.setMinimumHeight(66)
            header_button.setMaximumHeight(66)
            header_button.toggled.connect(lambda checked, key=strategy: self._on_smart_strategy_toggled(key, checked))
            self.smart_strategy_group.addButton(header_button)
            card_layout.addWidget(header_button)
            controls = QWidget(card)
            controls.setObjectName("ConfigurationSmartCardControls")
            controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            controls.setMinimumHeight(82)
            controls.setMaximumHeight(82)
            controls_layout = QVBoxLayout(controls)
            controls_layout.setContentsMargins(0, 2, 0, 4)
            controls_layout.setSpacing(3)
            controls_title = QLabel("Nível de compressão", controls)
            controls_title.setObjectName("ConfigurationSmartCardControlsTitle")
            controls_title.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            controls_title.setMinimumHeight(18)
            controls_title.setMaximumHeight(18)
            controls_layout.addWidget(controls_title)
            slider_row = QWidget(controls)
            slider_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            slider_row.setMinimumHeight(30)
            slider_row.setMaximumHeight(30)
            slider_layout = QHBoxLayout(slider_row)
            slider_layout.setContentsMargins(0, 0, 0, 0)
            slider_layout.setSpacing(4)
            minus_button = _SmartStepButton("−", slider_row)
            minus_button.setObjectName("ConfigurationSmartStepButton")
            minus_button.clicked.connect(lambda checked=False, key=strategy: self._step_smart_intensity(key, -10))
            slider_layout.addWidget(minus_button, 0)
            slider = QSlider(Qt.Orientation.Horizontal, slider_row)
            slider.setRange(0, 100)
            slider.setSingleStep(10)
            slider.setPageStep(10)
            slider.setTickInterval(10)
            slider.setTracking(True)
            slider.setValue(default_intensity)
            slider.valueChanged.connect(lambda value, key=strategy: self._on_smart_intensity_slider_changed(key, value))
            slider.sliderReleased.connect(self._update_smart_summary)
            slider_layout.addWidget(slider, 1)
            plus_button = _SmartStepButton("+", slider_row)
            plus_button.setObjectName("ConfigurationSmartStepButton")
            plus_button.clicked.connect(lambda checked=False, key=strategy: self._step_smart_intensity(key, 10))
            slider_layout.addWidget(plus_button, 0)
            controls_layout.addWidget(slider_row)
            intensity_label = QLabel("Intensidade: Média (5 / 10)", controls)
            intensity_label.setObjectName("ConfigurationSmartCardIntensityLabel")
            intensity_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
            intensity_label.setMinimumHeight(22)
            intensity_label.setMaximumHeight(22)
            controls_layout.addWidget(intensity_label)
            card_layout.addWidget(controls)
            card_layout.addStretch(1)
            strategy_cards_layout.addWidget(card, 1)
            self.smart_strategy_cards[strategy] = card
            self.smart_strategy_buttons[strategy] = header_button
            self.smart_intensity_sliders[strategy] = slider
            self.smart_intensity_minus_buttons[strategy] = minus_button
            self.smart_intensity_plus_buttons[strategy] = plus_button
            self.smart_intensity_controls[strategy] = controls
            self.smart_intensity_title_labels[strategy] = controls_title
            self.smart_intensity_value_labels[strategy] = intensity_label
        strategy_layout.addWidget(strategy_cards, 0, Qt.AlignmentFlag.AlignTop)
        self.smart_summary = QLabel(strategy_section)
        self.smart_summary.setObjectName("ConfigurationPreviewLabel")
        self.smart_summary.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.smart_summary.setTextFormat(Qt.TextFormat.RichText)
        self.smart_summary.setWordWrap(False)
        self.smart_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.smart_summary.setMinimumHeight(0)
        self.smart_summary.setMaximumHeight(0)
        self.smart_summary.setVisible(False)
        strategy_layout.addStretch(1)
        self.smart_audio_section = self._create_section()
        self.smart_audio_section.layout().setContentsMargins(12, 10, 12, 10)
        self.smart_audio_section.layout().setSpacing(8)
        audio_header = QWidget(self.smart_audio_section)
        audio_header_layout = QHBoxLayout(audio_header)
        audio_header_layout.setContentsMargins(0, 0, 0, 0)
        audio_header_layout.setSpacing(10)
        self.smart_audio_title = None
        self.smart_audio_keep_original = _ConfigurationCheckBox("Manter configurações originais do áudio:", audio_header)
        self.smart_audio_keep_original.setChecked(True)
        self.smart_audio_keep_original.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.smart_audio_keep_original.toggled.connect(self._on_smart_audio_keep_original_toggled)
        audio_header_layout.addWidget(self.smart_audio_keep_original, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.smart_audio_meta = QLabel(audio_header)
        self.smart_audio_meta.setObjectName("ConfigurationPreviewLabel")
        self.smart_audio_meta.setWordWrap(False)
        self.smart_audio_meta.setText("saída do vídeo")
        self.smart_audio_meta.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        audio_header_layout.addWidget(self.smart_audio_meta, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        audio_header_layout.addStretch(1)
        self.smart_audio_section.layout().addWidget(audio_header)
        self.smart_audio_controls_row = QWidget(self.smart_audio_section)
        self.smart_audio_controls_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.smart_audio_controls_row_layout = QHBoxLayout(self.smart_audio_controls_row)
        self.smart_audio_controls_row_layout.setContentsMargins(0, 0, 0, 0)
        self.smart_audio_controls_row_layout.setSpacing(28)
        self.smart_audio_controls_row_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.smart_audio_controls_effect = QGraphicsOpacityEffect(self.smart_audio_controls_row)
        self.smart_audio_controls_effect.setOpacity(1.0)
        self.smart_audio_controls_row.setGraphicsEffect(self.smart_audio_controls_effect)
        self.smart_audio_track_controls = QWidget(self.smart_audio_controls_row)
        self.smart_audio_track_controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.smart_audio_track_controls_layout = QHBoxLayout(self.smart_audio_track_controls)
        self.smart_audio_track_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.smart_audio_track_controls_layout.setSpacing(4)
        self.smart_audio_track_label = QLabel("Selecione a trilha", self.smart_audio_track_controls)
        self.smart_audio_track_label.setObjectName("ConfigurationSectionTitle")
        self.smart_audio_track_controls_layout.addWidget(self.smart_audio_track_label)
        self.smart_audio_track_picker = _ConfigurationComboBox(self.smart_audio_track_controls)
        self.smart_audio_track_picker.setObjectName("ConfigurationInlineCombo")
        self.smart_audio_track_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.smart_audio_track_picker.setMinimumContentsLength(14)
        self.smart_audio_track_picker.setMinimumWidth(344)
        self.smart_audio_track_picker.setMaximumWidth(372)
        self.smart_audio_track_picker.setMinimumHeight(28)
        self.smart_audio_track_picker.currentIndexChanged.connect(self._on_smart_audio_track_picker_changed)
        self.smart_audio_track_controls_layout.addWidget(self.smart_audio_track_picker, 1)
        self.smart_audio_channel_controls = QWidget(self.smart_audio_controls_row)
        self.smart_audio_channel_controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.smart_audio_channel_controls_layout = QHBoxLayout(self.smart_audio_channel_controls)
        self.smart_audio_channel_controls_layout.setContentsMargins(0, 0, 0, 0)
        self.smart_audio_channel_controls_layout.setSpacing(8)
        self.smart_audio_channel_label = QLabel("Canais", self.smart_audio_channel_controls)
        self.smart_audio_channel_label.setObjectName("ConfigurationSectionTitle")
        self.smart_audio_channel_controls_layout.addWidget(self.smart_audio_channel_label)
        self.smart_audio_channel_segment_row = QWidget(self.smart_audio_channel_controls)
        self.smart_audio_channel_segment_row_layout = QHBoxLayout(self.smart_audio_channel_segment_row)
        self.smart_audio_channel_segment_row_layout.setContentsMargins(0, 0, 0, 0)
        self.smart_audio_channel_segment_row_layout.setSpacing(0)
        self.smart_audio_channel_group = QButtonGroup(self)
        channel_specs = (
            ("Todos", "smart_audio_keep_channels"),
            ("Estéreo", "smart_audio_downmix"),
            ("Mono", "smart_audio_mono"),
        )
        for index, (label, attr_name) in enumerate(channel_specs):
            button = self._create_segmented_button(
                label,
                self.smart_audio_channel_segment_row,
                self.smart_audio_channel_group,
                position=_segment_position(index, len(channel_specs)),
                checked_slot=self._schedule_smart_summary_update,
            )
            button.setProperty("smartAudioChannel", "true")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setMinimumWidth(82)
            button.setMaximumWidth(82)
            button.setFixedHeight(28)
            setattr(self, attr_name, button)
            self.smart_audio_channel_segment_row_layout.addWidget(button)
        self.smart_audio_channel_controls_layout.addWidget(self.smart_audio_channel_segment_row)
        self.smart_audio_controls_row_layout.addStretch(1)
        self.smart_audio_controls_row_layout.addWidget(self.smart_audio_track_controls)
        self.smart_audio_controls_row_layout.addWidget(self.smart_audio_channel_controls)
        self.smart_audio_controls_row_layout.addStretch(1)
        self.smart_audio_section.layout().addWidget(self.smart_audio_controls_row)
        audio_controls_width = (
            self.smart_audio_track_controls.sizeHint().width()
            + self.smart_audio_channel_controls.sizeHint().width()
            + self.smart_audio_controls_row_layout.spacing()
        )
        # Three independent Strategic sliders replaced the old single intensity block.
        # The removed widget must not be referenced during page construction.
        _target_intensity_width = max(260, int(round(audio_controls_width * 0.4)))
        _ = _target_intensity_width
        content_layout.addWidget(strategy_section, 1)
        content_layout.addWidget(self.smart_audio_section, 0)
        return page
    def _build_advanced_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        content_frame = QFrame(page)
        content_frame.setObjectName("ConfigurationContentFrame")
        content_layout = QVBoxLayout(content_frame)
        # Match the top/side rhythm used by Quick and Strategic so the shared
        # video output panel sits in the exact same visual position on all
        # video profiles.
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(10)
        self.advanced_content_layout = content_layout
        layout.addWidget(content_frame, 1)
        driver_section = self._create_section()
        driver_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        driver_layout = driver_section.layout()
        driver_layout.setContentsMargins(22, 18, 12, 10)
        driver_layout.setSpacing(8)
        self.advanced_driver_split = QWidget(driver_section)
        driver_split_layout = QHBoxLayout(self.advanced_driver_split)
        driver_split_layout.setContentsMargins(0, 0, 0, 4)
        driver_split_layout.setSpacing(8)
        self.advanced_driver_editor = QWidget(self.advanced_driver_split)
        self.advanced_driver_editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor_layout = QVBoxLayout(self.advanced_driver_editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(10)
        self.advanced_primary_title = QLabel("Tamanho desejado", self.advanced_driver_editor)
        self.advanced_primary_title.setObjectName("ConfigurationSectionTitle")
        self.advanced_primary_title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.advanced_primary_title.hide()
        self.advanced_target_mode = _ConfigurationComboBox(self.advanced_driver_editor)
        self.advanced_target_mode.setObjectName("ConfigurationInlineCombo")
        self.advanced_target_mode.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.advanced_target_mode.addItem("Bitrate direto", "DIRECT_BITRATE")
        self.advanced_target_mode.addItem("Tamanho alvo", "TARGET_SIZE")
        self.advanced_target_mode.addItem("Resolução", "RESOLUTION_DRIVEN")
        self.advanced_target_mode.currentIndexChanged.connect(self._sync_advanced_target_mode_buttons)
        self.advanced_target_mode.currentIndexChanged.connect(self._on_advanced_strategy_changed)
        self.advanced_target_mode.hide()
        self.advanced_target_mode_selector = QWidget(self.advanced_driver_editor)
        self.advanced_target_mode_selector.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_target_mode_selector.setMinimumHeight(46)
        self.advanced_target_mode_selector.setMaximumHeight(46)
        self.advanced_target_column_width = 584
        self.advanced_strategy_context_width = self.advanced_target_column_width - 28
        self.advanced_output_card_width = 262
        self.advanced_columns_gap_width = 24
        self.advanced_full_target_row_width = (
            self.advanced_target_column_width
            + self.advanced_columns_gap_width
            + self.advanced_output_card_width
        )
        self.advanced_driver_split.setMinimumWidth(self.advanced_full_target_row_width)
        self.advanced_driver_split.setMaximumWidth(self.advanced_full_target_row_width)
        self.advanced_control_field_width = 236
        self.advanced_compact_control_field_width = 160
        self.advanced_target_group_width = self.advanced_target_column_width
        self.advanced_target_nav_width = self.advanced_target_column_width
        self.advanced_target_mode_selector_width = self.advanced_target_group_width
        self.advanced_target_mode_selector.setMinimumWidth(self.advanced_target_mode_selector_width)
        self.advanced_target_mode_selector.setMaximumWidth(self.advanced_target_mode_selector_width)
        self.advanced_target_mode_selector_layout = QHBoxLayout(self.advanced_target_mode_selector)
        self.advanced_target_mode_selector_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_target_mode_selector_layout.setSpacing(0)
        self.advanced_target_mode_group = QButtonGroup(self.advanced_target_mode_selector)
        self.advanced_target_mode_group.setExclusive(True)
        self.advanced_target_mode_buttons: dict[str, QPushButton] = {}
        self.advanced_strategy_cards: dict[str, QFrame] = {}
        self.advanced_strategy_card_layouts: dict[str, QVBoxLayout] = {}
        strategy_specs = (
            ("Priorizar bitrate", "DIRECT_BITRATE"),
            ("Equilibrar", "TARGET_SIZE"),
            ("Priorizar resolução", "RESOLUTION_DRIVEN"),
        )
        for index, (text, data) in enumerate(strategy_specs):
            button = self._create_segmented_button(
                text,
                self.advanced_target_mode_selector,
                self.advanced_target_mode_group,
                position=_segment_position(index, len(strategy_specs)),
            )
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(42)
            button.setMaximumHeight(42)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.toggled.connect(lambda checked, value=data: self._on_advanced_target_mode_button_toggled(value, checked))
            self.advanced_target_mode_selector_layout.addWidget(button, 1)
            self.advanced_target_mode_buttons[data] = button
        self.advanced_target_mode_row = self._create_form_row(self.advanced_driver_editor, "Estratégia", self.advanced_target_mode_selector)
        self.advanced_target_mode_row.setMinimumWidth(self.advanced_target_mode_selector_width)
        self.advanced_target_mode_row.setMaximumWidth(self.advanced_target_mode_selector_width)
        self.advanced_target_bitrate_kbps = _HoldRepeatSpinBox(self.advanced_driver_editor)
        self.advanced_target_bitrate_kbps.setRange(1, 50000)
        self.advanced_target_bitrate_kbps.setAccelerated(True)
        self.advanced_target_bitrate_kbps.setSingleStep(10)
        self.advanced_target_bitrate_kbps.setValue(1)
        self.advanced_target_bitrate_kbps.setSuffix(" kbps")
        self.advanced_target_bitrate_kbps.setMinimumWidth(176)
        self.advanced_target_bitrate_kbps.setMaximumWidth(176)
        self.advanced_target_bitrate_kbps.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_target_bitrate_kbps.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_target_bitrate_kbps.valueChanged.connect(self._on_advanced_bitrate_changed)
        self.advanced_bitrate_fields = QWidget(self.advanced_driver_editor)
        self.advanced_bitrate_fields.setObjectName("ConfigurationBitrateFields")
        self.advanced_bitrate_fields.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_bitrate_fields.setMinimumWidth(self.advanced_strategy_context_width)
        self.advanced_bitrate_fields.setMaximumWidth(self.advanced_strategy_context_width)
        self.advanced_bitrate_fields.setMinimumHeight(34)
        self.advanced_bitrate_fields.setMaximumHeight(34)
        self.advanced_bitrate_fields_layout = QHBoxLayout(self.advanced_bitrate_fields)
        self.advanced_bitrate_fields_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_bitrate_fields_layout.setSpacing(10)
        self.advanced_bitrate_fields_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_bitrate_label = QLabel("Taxa de bitrate de vídeo", self.advanced_bitrate_fields)
        self.advanced_bitrate_label.setObjectName("ConfigurationResolutionAxisLabel")
        self.advanced_bitrate_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_bitrate_fields_layout.addWidget(self.advanced_bitrate_label, 0)
        self.advanced_bitrate_fields_layout.addWidget(self.advanced_target_bitrate_kbps, 0)
        self.advanced_strategy_field = QWidget(self.advanced_driver_editor)
        self.advanced_strategy_field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_strategy_field.setMinimumWidth(self.advanced_strategy_context_width)
        self.advanced_strategy_field.setMaximumWidth(self.advanced_strategy_context_width)
        self.advanced_strategy_field.setMinimumHeight(36)
        self.advanced_strategy_field.setMaximumHeight(36)
        self.advanced_strategy_stack = QStackedLayout(self.advanced_strategy_field)
        self.advanced_strategy_stack.setContentsMargins(0, 0, 0, 0)
        self.advanced_strategy_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self.advanced_target_size_mb = _HoldRepeatDoubleSpinBox(self.advanced_driver_editor)
        self.advanced_target_size_mb.setAccelerated(True)
        self.advanced_target_size_mb.setDecimals(2)
        self.advanced_target_size_mb.setSingleStep(0.1)
        self.advanced_target_size_mb.setRange(0.1, 102400.0)
        self.advanced_target_size_mb.setValue(25.0)
        self.advanced_target_size_mb.setSuffix(" MB")
        self.advanced_target_size_mb.setLocale(QLocale.c())
        self.advanced_target_size_mb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_target_size_mb.setMinimumWidth(164)
        self.advanced_target_size_mb.setMaximumWidth(164)
        self.advanced_target_size_mb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.advanced_target_size_mb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_target_size_mb.valueChanged.connect(self._on_advanced_target_size_changed)
        if hasattr(self, "advanced_top_target_size_layout"):
            self.advanced_top_target_size_layout.addWidget(self.advanced_target_size_mb, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_original_label = QLabel("Arquivo original:", self.advanced_driver_editor)
        self.advanced_target_size_original_label.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_target_size_original_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_original_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.advanced_target_size_hint = QLabel("", self.advanced_driver_editor)
        self.advanced_target_size_hint.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_target_size_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_hint.setMinimumWidth(72)
        self.advanced_target_size_hint.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.advanced_target_size_arrow_label = QLabel("→", self.advanced_driver_editor)
        self.advanced_target_size_arrow_label.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_target_size_arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_target_size_arrow_label.setMinimumWidth(24)
        self.advanced_target_size_arrow_label.setMaximumWidth(24)
        self.advanced_target_size_arrow_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.advanced_target_size_arrow_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.advanced_target_size_desired_label = QLabel("Meta:", self.advanced_driver_editor)
        self.advanced_target_size_desired_label.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_target_size_desired_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_desired_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.advanced_target_size_meta_value_label = QLabel("—", self.advanced_driver_editor)
        self.advanced_target_size_meta_value_label.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_target_size_meta_value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_meta_value_label.setMinimumWidth(64)
        self.advanced_target_size_meta_value_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.advanced_target_size_second_arrow_label = QLabel("→", self.advanced_driver_editor)
        self.advanced_target_size_second_arrow_label.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_target_size_second_arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_target_size_second_arrow_label.setMinimumWidth(24)
        self.advanced_target_size_second_arrow_label.setMaximumWidth(24)
        self.advanced_target_size_second_arrow_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.advanced_target_size_second_arrow_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.advanced_target_size_reduction_label = QLabel("Redução estimada: —", self.advanced_driver_editor)
        self.advanced_target_size_reduction_label.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_target_size_reduction_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_reduction_label.setMinimumWidth(140)
        self.advanced_target_size_reduction_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.advanced_target_size_hint.setWordWrap(False)
        self.advanced_target_size_field = QWidget(self.advanced_driver_editor)
        self.advanced_target_size_field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_target_size_field.setMinimumWidth(704)
        self.advanced_target_size_field.setMaximumWidth(704)
        self.advanced_target_size_field_layout = QHBoxLayout(self.advanced_target_size_field)
        self.advanced_target_size_field_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_target_size_field_layout.setSpacing(0)
        self.advanced_target_size_field_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_target_size_field_layout.addWidget(self.advanced_target_size_original_label, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_field_layout.addSpacing(10)
        self.advanced_target_size_field_layout.addWidget(self.advanced_target_size_hint, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_field_layout.addSpacing(20)
        self.advanced_target_size_field_layout.addWidget(self.advanced_target_size_arrow_label, 0, Qt.AlignmentFlag.AlignCenter)
        self.advanced_target_size_field_layout.addSpacing(20)
        self.advanced_target_size_field_layout.addWidget(self.advanced_target_size_desired_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_field_layout.addSpacing(8)
        self.advanced_target_size_field_layout.addWidget(self.advanced_target_size_meta_value_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_field_layout.addSpacing(20)
        self.advanced_target_size_field_layout.addWidget(self.advanced_target_size_second_arrow_label, 0, Qt.AlignmentFlag.AlignCenter)
        self.advanced_target_size_field_layout.addSpacing(20)
        self.advanced_target_size_field_layout.addWidget(self.advanced_target_size_reduction_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_target_size_row = self._create_form_row(self.advanced_driver_editor, "", self.advanced_target_size_field)
        advanced_target_size_layout = self.advanced_target_size_row.layout()
        if advanced_target_size_layout is not None:
            advanced_target_size_layout.setSpacing(8)
            advanced_target_size_label = self.advanced_target_size_row.findChild(QLabel, "ConfigurationFormLabel")
            if advanced_target_size_label is not None:
                advanced_target_size_label.setMinimumWidth(0)
                advanced_target_size_label.setMaximumWidth(0)
                advanced_target_size_label.hide()
        self.advanced_target_size_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.advanced_target_size_row.setObjectName("ConfigurationPrimaryTargetRow")
        self.advanced_target_size_row.setMinimumWidth(self.advanced_full_target_row_width)
        self.advanced_target_size_row.setMaximumWidth(self.advanced_full_target_row_width)
        self.advanced_resolution_bitrate_mode = _ConfigurationComboBox(self.advanced_driver_editor)
        self.advanced_resolution_bitrate_mode.setObjectName("ConfigurationInlineCombo")
        self.advanced_resolution_bitrate_mode.setMinimumWidth(self.advanced_control_field_width)
        self.advanced_resolution_bitrate_mode.setMaximumWidth(self.advanced_control_field_width)
        self.advanced_resolution_bitrate_mode.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_resolution_bitrate_mode.addItem("Automático", "auto")
        self.advanced_resolution_bitrate_mode.addItem("Explícito", "explicit")
        self.advanced_resolution_bitrate_mode.currentIndexChanged.connect(self._on_advanced_strategy_changed)
        self.advanced_resolution_bitrate_mode_row = self._create_form_row(self.advanced_driver_editor, "Bitrate da resolução", self.advanced_resolution_bitrate_mode)
        self.advanced_resolution_bitrate_mode_row.setVisible(False)
        editor_layout.addWidget(self.advanced_resolution_bitrate_mode_row)
        self.advanced_resolution_bitrate_kbps = _HoldRepeatSpinBox(self.advanced_driver_editor)
        self.advanced_resolution_bitrate_kbps.setRange(1, 50000)
        self.advanced_resolution_bitrate_kbps.setAccelerated(True)
        self.advanced_resolution_bitrate_kbps.setSingleStep(10)
        self.advanced_resolution_bitrate_kbps.setValue(1)
        self.advanced_resolution_bitrate_kbps.setSuffix(" kbps")
        self.advanced_resolution_bitrate_kbps.setMinimumWidth(self.advanced_control_field_width)
        self.advanced_resolution_bitrate_kbps.setMaximumWidth(self.advanced_control_field_width)
        self.advanced_resolution_bitrate_kbps.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_resolution_bitrate_kbps.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_resolution_bitrate_kbps.valueChanged.connect(self._on_advanced_bitrate_changed)
        self.advanced_resolution_fields = QWidget(self.advanced_strategy_field)
        self.advanced_resolution_fields.setObjectName("ConfigurationResolutionFields")
        self.advanced_resolution_fields.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_resolution_fields.setMinimumWidth(self.advanced_strategy_context_width)
        self.advanced_resolution_fields.setMaximumWidth(self.advanced_strategy_context_width)
        self.advanced_resolution_fields.setMinimumHeight(34)
        self.advanced_resolution_fields.setMaximumHeight(34)
        self.advanced_resolution_fields_layout = QHBoxLayout(self.advanced_resolution_fields)
        self.advanced_resolution_fields_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_resolution_fields_layout.setSpacing(8)
        self.advanced_resolution_fields_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.advanced_width_label = QLabel("Largura", self.advanced_resolution_fields)
        self.advanced_width_label.setObjectName("ConfigurationResolutionAxisLabel")
        self.advanced_width_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_resolution_fields_layout.addWidget(self.advanced_width_label, 0)
        self.advanced_width = _HoldRepeatSpinBox(self.advanced_resolution_fields)
        self.advanced_width.setObjectName("ConfigurationSpinBox")
        self.advanced_width.setRange(2, 16384)
        self.advanced_width.setAccelerated(True)
        self.advanced_width.setSingleStep(2)
        self.advanced_width.setMinimumHeight(28)
        self.advanced_width.setMaximumHeight(28)
        self.advanced_width.setMinimumWidth(108)
        self.advanced_width.setMaximumWidth(108)
        self.advanced_width.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_width.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.advanced_width.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_width.valueChanged.connect(self._on_advanced_width_changed)
        self.advanced_resolution_fields_layout.addWidget(self.advanced_width, 0)

        self.advanced_height_label = QLabel("Altura", self.advanced_resolution_fields)
        self.advanced_height_label.setObjectName("ConfigurationResolutionAxisLabel")
        self.advanced_height_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_resolution_fields_layout.addWidget(self.advanced_height_label, 0)
        self.advanced_height = _HoldRepeatSpinBox(self.advanced_resolution_fields)
        self.advanced_height.setObjectName("ConfigurationSpinBox")
        self.advanced_height.setRange(2, 16384)
        self.advanced_height.setAccelerated(True)
        self.advanced_height.setSingleStep(2)
        self.advanced_height.setMinimumHeight(28)
        self.advanced_height.setMaximumHeight(28)
        self.advanced_height.setMinimumWidth(108)
        self.advanced_height.setMaximumWidth(108)
        self.advanced_height.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_height.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
        self.advanced_height.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_height.valueChanged.connect(self._on_advanced_height_changed)
        self.advanced_resolution_fields_layout.addWidget(self.advanced_height, 0)
        self.advanced_strategy_auto_hint = QLabel("Bitrate e resolução calculados automaticamente para atingir o tamanho desejado.", self.advanced_strategy_field)
        self.advanced_strategy_auto_hint.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_strategy_auto_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_strategy_auto_hint.setContentsMargins(0, 0, 0, 0)
        self.advanced_strategy_auto_hint.setWordWrap(True)
        self.advanced_strategy_auto_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.advanced_strategy_auto_hint.setMinimumHeight(28)
        self.advanced_strategy_stack.addWidget(self.advanced_strategy_auto_hint)
        self.advanced_balance_fields = QWidget(self.advanced_strategy_field)
        self.advanced_balance_fields.setObjectName("ConfigurationBalanceFields")
        self.advanced_balance_fields.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_balance_fields.setMinimumWidth(self.advanced_strategy_context_width)
        self.advanced_balance_fields.setMaximumWidth(self.advanced_strategy_context_width)
        self.advanced_balance_fields.setMinimumHeight(34)
        self.advanced_balance_fields.setMaximumHeight(34)
        self.advanced_balance_fields_layout = QHBoxLayout(self.advanced_balance_fields)
        self.advanced_balance_fields_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_balance_fields_layout.setSpacing(0)
        self.advanced_balance_fields_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_balance_hint = QLabel("bitrate × resolução", self.advanced_balance_fields)
        self.advanced_balance_hint.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_balance_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_balance_hint.setMinimumHeight(28)
        self.advanced_balance_fields_layout.addWidget(self.advanced_balance_hint, 0, Qt.AlignmentFlag.AlignCenter)
        self.advanced_strategy_stack.addWidget(self.advanced_bitrate_fields)
        self.advanced_strategy_stack.addWidget(self.advanced_balance_fields)
        self.advanced_strategy_stack.addWidget(self.advanced_resolution_fields)
        self.advanced_strategy_stack.setAlignment(self.advanced_bitrate_fields, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_strategy_stack.setAlignment(self.advanced_balance_fields, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_strategy_stack.setAlignment(self.advanced_resolution_fields, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_strategy_row = self._create_form_row(self.advanced_driver_editor, "Bitrate", self.advanced_strategy_field)
        self.advanced_resolution_placeholder = QWidget(self.advanced_driver_editor)
        self.advanced_resolution_placeholder.setMinimumWidth(self.advanced_control_field_width)
        self.advanced_resolution_placeholder.setMaximumWidth(self.advanced_control_field_width)
        self.advanced_resolution_placeholder.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_resolution_row = self._create_form_row(self.advanced_driver_editor, "Dimensão de referência", self.advanced_resolution_placeholder)
        self.advanced_fps_policy = _PreserveReduceSelector(
            self.advanced_driver_editor,
            width=self.advanced_compact_control_field_width,
            unit="fps",
            values=(30, 24, 20, 15, 12, 10),
        )
        self.advanced_fps_policy.currentIndexChanged.connect(self._on_advanced_context_policy_changed)
        self.advanced_fps_row = self._create_form_row(self.advanced_driver_editor, "Taxa de quadros", self.advanced_fps_policy)
        self.advanced_audio_policy = _PreserveReduceSelector(
            self.advanced_driver_editor,
            width=self.advanced_compact_control_field_width,
            unit="kbps",
            values=(128, 112, 96, 64, 48, 32),
        )
        self.advanced_audio_policy.currentIndexChanged.connect(self._on_advanced_context_policy_changed)
        self.advanced_audio_row = self._create_form_row(self.advanced_driver_editor, "Bitrate de áudio", self.advanced_audio_policy)
        self.advanced_context_field = QWidget(self.advanced_driver_editor)
        self.advanced_context_field.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_context_field.setMinimumWidth(self.advanced_control_field_width)
        self.advanced_context_field.setMaximumWidth(self.advanced_control_field_width)
        self.advanced_context_field.setMinimumHeight(28)
        self.advanced_context_stack = QStackedLayout(self.advanced_context_field)
        self.advanced_context_stack.setContentsMargins(0, 0, 0, 0)
        self.advanced_context_stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self.advanced_context_status = QLabel("Saída ajustada automaticamente.", self.advanced_context_field)
        self.advanced_context_status.setObjectName("ConfigurationInlineStatusLabel")
        self.advanced_context_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_context_status.setContentsMargins(0, 0, 0, 0)
        self.advanced_context_status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.advanced_context_status.setMinimumHeight(28)
        self.advanced_context_stack.addWidget(self.advanced_context_status)
        self.advanced_context_stack.setAlignment(self.advanced_context_status, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_context_stack.addWidget(self.advanced_resolution_bitrate_kbps)
        self.advanced_resolution_context_row = self._create_form_row(self.advanced_driver_editor, "Saída", self.advanced_context_field)
        self.advanced_resolution_context_row.setVisible(False)
        self._configure_advanced_form_row(self.advanced_target_size_row, label_width=0, spacing=0)
        self.advanced_target_size_row.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_target_size_row.setMinimumWidth(self.advanced_full_target_row_width)
        self.advanced_target_size_row.setMaximumWidth(self.advanced_full_target_row_width)
        self.advanced_target_size_row.layout().setContentsMargins(18, 4, 18, 4)
        self.advanced_target_size_row.layout().setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        editor_layout.setAlignment(self.advanced_target_size_row, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._set_form_row_label(self.advanced_target_mode_row, "")
        self._configure_advanced_form_row(self.advanced_target_mode_row, label_width=0, spacing=0)
        self.advanced_target_mode_row.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_target_mode_row.setMinimumWidth(self.advanced_target_column_width)
        self.advanced_target_mode_row.setMaximumWidth(self.advanced_target_column_width)
        self.advanced_target_mode_row.setMinimumHeight(46)
        self.advanced_target_mode_row.setMaximumHeight(46)
        self.advanced_target_mode_row.layout().setContentsMargins(0, 0, 0, 0)
        self.advanced_target_mode_row.layout().setAlignment(self.advanced_target_mode_selector, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        editor_layout.setAlignment(self.advanced_target_mode_row, Qt.AlignmentFlag.AlignLeft)
        self._configure_advanced_form_row(self.advanced_strategy_row, label_width=0, spacing=0)
        self._configure_advanced_form_row(self.advanced_resolution_row, label_width=82, spacing=2)
        self._configure_advanced_form_row(self.advanced_fps_row, label_width=96, spacing=4)
        self._configure_advanced_form_row(self.advanced_audio_row, label_width=96, spacing=4)
        self._configure_advanced_form_row(self.advanced_resolution_context_row, label_width=82, spacing=2)
        self._configure_advanced_form_row(self.advanced_resolution_bitrate_mode_row, label_width=82, spacing=2)
        for row in (
            self.advanced_strategy_row,
            self.advanced_resolution_row,
            self.advanced_fps_row,
            self.advanced_audio_row,
            self.advanced_resolution_context_row,
            self.advanced_resolution_bitrate_mode_row,
        ):
            if row is self.advanced_strategy_row:
                row.setMinimumHeight(52)
                row.setMaximumHeight(52)
                row.layout().setContentsMargins(14, 8, 14, 8)
            else:
                row.setMinimumHeight(42)
                row.setMaximumHeight(42)
                row.layout().setContentsMargins(0, 2, 0, 2)
            row.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = row.findChild(QLabel, "ConfigurationFormLabel")
            if label is not None:
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                label.setContentsMargins(0, 0, 0, 0)
            editor_layout.setAlignment(row, Qt.AlignmentFlag.AlignLeft)
        self.advanced_target_mode_selector.setMinimumWidth(self.advanced_target_mode_selector_width)
        self.advanced_target_mode_selector.setMaximumWidth(self.advanced_target_mode_selector_width)
        self.advanced_controls_split = QWidget(self.advanced_driver_editor)
        self.advanced_controls_split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.advanced_controls_split_layout = QVBoxLayout(self.advanced_controls_split)
        self.advanced_controls_split_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_controls_split_layout.setSpacing(18)
        self.advanced_controls_split_layout.addWidget(self.advanced_target_mode_selector, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.advanced_strategy_row.setObjectName("ConfigurationStrategyContextRow")
        self.advanced_strategy_row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.advanced_strategy_row.setMinimumWidth(self.advanced_target_column_width)
        self.advanced_strategy_row.setMaximumWidth(self.advanced_target_column_width)
        self.advanced_strategy_row.setMinimumHeight(52)
        self.advanced_strategy_row.setMaximumHeight(52)
        self.advanced_strategy_row.layout().setContentsMargins(14, 8, 14, 8)
        self.advanced_strategy_row.layout().setSpacing(0)
        self.advanced_strategy_row.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.advanced_strategy_row.layout().setAlignment(self.advanced_strategy_field, Qt.AlignmentFlag.AlignCenter)
        self.advanced_controls_split_layout.addWidget(self.advanced_strategy_row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.advanced_secondary_compact_row = QWidget(self.advanced_controls_split)
        self.advanced_secondary_compact_row.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_secondary_compact_row.setMinimumWidth(self.advanced_target_column_width)
        self.advanced_secondary_compact_row.setMaximumWidth(self.advanced_target_column_width)
        self.advanced_secondary_compact_layout = QHBoxLayout(self.advanced_secondary_compact_row)
        self.advanced_secondary_compact_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_secondary_compact_layout.setSpacing(24)
        self.advanced_compact_left_row_width = 260
        self.advanced_compact_right_row_width = self.advanced_target_column_width - self.advanced_compact_left_row_width - 24
        self.advanced_fps_row.setMinimumWidth(self.advanced_compact_left_row_width)
        self.advanced_fps_row.setMaximumWidth(self.advanced_compact_left_row_width)
        self.advanced_audio_row.setMinimumWidth(self.advanced_compact_right_row_width)
        self.advanced_audio_row.setMaximumWidth(self.advanced_compact_right_row_width)
        self.advanced_fps_row.layout().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_audio_row.layout().setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_secondary_compact_layout.addWidget(self.advanced_fps_row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_secondary_compact_layout.addWidget(self.advanced_audio_row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_controls_split_layout.addWidget(self.advanced_secondary_compact_row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.advanced_controls_split_layout.addWidget(self.advanced_resolution_context_row)
        self.advanced_controls_split_layout.addWidget(self.advanced_resolution_bitrate_mode_row)
        for row in (self.advanced_fps_row, self.advanced_audio_row):
            row.layout().setContentsMargins(0, 1, 0, 1)
            label = row.findChild(QLabel, "ConfigurationFormLabel")
            if label is not None:
                label.setContentsMargins(0, 0, 0, 0)
        editor_layout.addSpacing(2)
        editor_layout.addWidget(self.advanced_controls_split)
        # Avisos contextuais do Avançado são exibidos na barra inferior (`advanced_summary`).
        # Manter essa área livre evita que alertas empurrem ou cubram os controles principais.
        self.advanced_output_card = QFrame(self.advanced_driver_split)
        self.advanced_output_card.setObjectName("ConfigurationAdvancedOutputCard")
        self.advanced_output_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.advanced_output_card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_output_card.setMinimumWidth(self.advanced_output_card_width)
        self.advanced_output_card.setMaximumWidth(self.advanced_output_card_width)
        # Keep the card slightly lower than the left control stack so the
        # bottom breathing room matches the right-side inset of the main frame.
        self.advanced_output_card.setMinimumHeight(150)
        self.advanced_output_card.setMaximumHeight(150)
        output_layout = QVBoxLayout(self.advanced_output_card)
        output_layout.setContentsMargins(16, 7, 16, 7)
        output_layout.setSpacing(0)
        self.advanced_output_content = QWidget(self.advanced_output_card)
        self.advanced_output_content.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_output_content.setMinimumWidth(190)
        self.advanced_output_content.setMaximumWidth(190)
        output_content_layout = QVBoxLayout(self.advanced_output_content)
        output_content_layout.setContentsMargins(0, 0, 0, 0)
        output_content_layout.setSpacing(6)
        self.advanced_output_title = QLabel("Saída estimada", self.advanced_output_content)
        self.advanced_output_title.setObjectName("ConfigurationAdvancedOutputTitle")
        self.advanced_output_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        output_content_layout.addWidget(self.advanced_output_title, 0, Qt.AlignmentFlag.AlignLeft)
        self.advanced_output_metrics = QWidget(self.advanced_output_content)
        self.advanced_output_metrics.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_output_metrics.setMinimumWidth(162)
        self.advanced_output_metrics.setMaximumWidth(162)
        self.advanced_output_metrics_layout = QVBoxLayout(self.advanced_output_metrics)
        self.advanced_output_metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced_output_metrics_layout.setSpacing(0)
        self.advanced_output_size_row, self.advanced_output_size_value = self._create_output_metric(self.advanced_output_metrics, "Tamanho")
        self.advanced_output_resolution_row, self.advanced_output_resolution_value = self._create_output_metric(self.advanced_output_metrics, "Resolução")
        self.advanced_output_fps_row, self.advanced_output_fps_value = self._create_output_metric(self.advanced_output_metrics, "Quadros")
        self.advanced_output_bitrate_row, self.advanced_output_bitrate_value = self._create_output_metric(self.advanced_output_metrics, "Bitrate total")
        self.advanced_output_audio_bitrate_row, self.advanced_output_audio_bitrate_value = self._create_output_metric(self.advanced_output_metrics, "Áudio")
        self.advanced_output_audio_bitrate_row.hide()
        for metric_row in (
            self.advanced_output_size_row,
            self.advanced_output_resolution_row,
            self.advanced_output_fps_row,
        ):
            metric_row.setProperty("withBottomDivider", True)
        output_content_layout.addWidget(self.advanced_output_metrics, 0, Qt.AlignmentFlag.AlignRight)
        self.advanced_resolution_meta = QLabel("A saída preserva a proporção original e respeita os limites da mídia de origem.")
        self.advanced_resolution_meta.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.advanced_resolution_meta.setWordWrap(True)
        self.advanced_resolution_meta.setContentsMargins(0, 0, 0, 0)
        self.advanced_resolution_meta.setMinimumWidth(0)
        self.advanced_resolution_meta.setMaximumWidth(0)
        self.advanced_resolution_meta.setMinimumHeight(0)
        self.advanced_resolution_meta.setMaximumHeight(0)
        self.advanced_resolution_meta.hide()
        output_layout.addStretch(1)
        output_layout.addWidget(self.advanced_output_content, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        output_layout.addStretch(1)
        self._set_form_row_label(self.advanced_strategy_row, "")
        self._set_form_row_label(self.advanced_resolution_row, "Resolução")
        self._set_form_row_label(self.advanced_resolution_context_row, "Saída")
        driver_layout.addWidget(self.advanced_target_size_row, 0, Qt.AlignmentFlag.AlignLeft)
        driver_layout.addSpacing(4)
        driver_split_layout.addWidget(self.advanced_driver_editor, 1)
        driver_split_layout.addSpacing(16)
        
        self.advanced_output_slot = QWidget(self.advanced_driver_split)
        self.advanced_output_slot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.advanced_output_slot_layout = QVBoxLayout(self.advanced_output_slot)
        self.advanced_output_slot_layout.setContentsMargins(0, 4, 0, 0)
        self.advanced_output_slot_layout.setSpacing(0)
        self.advanced_output_slot_layout.addWidget(self.advanced_output_card, 0, Qt.AlignmentFlag.AlignTop)
        driver_split_layout.addWidget(self.advanced_output_slot, 0, Qt.AlignmentFlag.AlignTop)
        driver_layout.addWidget(self.advanced_driver_split, 0, Qt.AlignmentFlag.AlignLeft)
        summary = self._create_section(None)
        summary.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        summary_layout = summary.layout()
        summary_layout.setContentsMargins(12, 5, 12, 5)
        summary_layout.setSpacing(0)
        self.advanced_summary = QLabel(summary)
        self.advanced_summary.setObjectName("ConfigurationAdvancedSummaryLabel")
        self.advanced_summary.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_summary.setWordWrap(False)
        self.advanced_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.advanced_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        summary_layout.addWidget(self.advanced_summary, 0, Qt.AlignmentFlag.AlignCenter)
        summary.setMinimumHeight(40)
        summary.setMaximumHeight(40)
        self.advanced_summary_block = summary
        content_layout.addWidget(driver_section, 1)
        content_layout.addWidget(self.advanced_summary_block, 0)
        return page
    def _build_audio_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        content_frame = QFrame(page)
        content_frame.setObjectName("ConfigurationContentFrame")
        content_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(12, 12, 12, 10)
        content_layout.setSpacing(10)
        main_section = self._create_section()
        main_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        main_layout = main_section.layout()
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(14)
        self.audio_output_format = _ConfigurationComboBox(main_section)
        self.audio_output_format.setObjectName("ConfigurationInlineCombo")
        self.audio_output_format.setMinimumWidth(194)
        self.audio_output_format.setMaximumWidth(208)
        for fmt in audio_profile.AUDIO_FORMAT_OPTIONS:
            self.audio_output_format.addItem(audio_profile.display_label_for_format(fmt), fmt)
        self.audio_output_format.currentIndexChanged.connect(self._on_audio_profile_changed)
        self.audio_bitrate = _ConfigurationComboBox(main_section)
        self.audio_bitrate.setObjectName("ConfigurationInlineCombo")
        self.audio_bitrate.setMinimumWidth(172)
        self.audio_bitrate.setMaximumWidth(184)
        for kbps in audio_profile.AUDIO_BITRATE_OPTIONS:
            self.audio_bitrate.addItem(f"{kbps} kbps", kbps)
        self.audio_bitrate_help_text = "Bitrate é a quantidade de dados do áudio por segundo. Mais kbps aumenta a qualidade e o tamanho."
        self.audio_bitrate_value_help_text = "Referência: vídeo 96–128 kbps; música 160–192 kbps."
        self.audio_bitrate.setToolTip(self.audio_bitrate_value_help_text)
        self.audio_bitrate.currentIndexChanged.connect(self._on_audio_profile_changed)
        self.audio_quality_mode = _ConfigurationComboBox(main_section)
        self.audio_quality_mode.setObjectName("ConfigurationInlineCombo")
        self.audio_quality_mode.setMinimumWidth(172)
        self.audio_quality_mode.setMaximumWidth(184)
        self.audio_quality_mode.addItem("Preservar qualidade", "QUALITY")
        self.audio_quality_mode.addItem("Equilibrado", "BALANCED")
        self.audio_quality_mode.addItem("Economizar espaço", "SPACE")
        self.audio_quality_mode_help_text = "Define a prioridade entre qualidade e tamanho do áudio."
        self.audio_quality_mode_value_help_text = "Equilibrado é recomendado para a maioria dos vídeos."
        self.audio_quality_mode.setToolTip(self.audio_quality_mode_value_help_text)
        self.audio_quality_mode.currentIndexChanged.connect(self._on_audio_profile_changed)
        self.audio_channel_policy = _ConfigurationComboBox(main_section)
        self.audio_channel_policy.setObjectName("ConfigurationInlineCombo")
        self.audio_channel_policy.setMinimumWidth(172)
        self.audio_channel_policy.setMaximumWidth(184)
        self.audio_channel_policy.addItem("Canais originais", "KEEP_ORIGINAL")
        self.audio_channel_policy.addItem("Estéreo", "DOWNMIX_TO_STEREO")
        self.audio_channel_policy.addItem("Mono", "DOWNMIX_TO_MONO")
        self.audio_channel_policy.currentIndexChanged.connect(self._on_audio_profile_changed)
        self.audio_channels_help_text = "Define se os canais de áudio serão preservados ou convertidos."
        self.audio_channels_value_help_text = "Original preserva o áudio; Estéreo é padrão; Mono reduz tamanho para voz."
        self.audio_channel_policy.setToolTip(self.audio_channels_value_help_text)
        self.audio_track_help_text = "Escolha quais trilhas de áudio serão mantidas no arquivo final."
        self.audio_track_value_help_text = "Use todas as trilhas ou selecione apenas a principal."
        self.audio_volume_help_text = "Ajusta o volume automaticamente para deixar o áudio mais uniforme."
        self.audio_volume_value_help_text = "Desativada mantém o volume; Leve corrige pouco; Padrão uniformiza mais."
        self.audio_volume_normalization = _ConfigurationComboBox(main_section)
        self.audio_volume_normalization.setObjectName("ConfigurationInlineCombo")
        self.audio_volume_normalization.hide()
        self.audio_volume_normalization.addItem("Desativada", "OFF")
        self.audio_volume_normalization.addItem("Leve", "LIGHT")
        self.audio_volume_normalization.addItem("Padrão", "STANDARD")
        self.audio_volume_normalization.currentIndexChanged.connect(self._on_audio_profile_changed)
        self.audio_volume_segmented = _SegmentedChoice(main_section, width=306)
        self.audio_volume_segmented.add_option("Desativada", "OFF", position="left")
        self.audio_volume_segmented.add_option(
            "Leve",
            "LIGHT",
            position="middle",
            tooltip="Leve: corrige pequenas diferenças de volume sem alterar muito a dinâmica original.",
        )
        self.audio_volume_segmented.add_option(
            "Padrão",
            "STANDARD",
            position="right",
            tooltip="Padrão: uniformiza mais o volume, útil quando o áudio está baixo ou irregular.",
        )
        self.audio_volume_segmented.setToolTip(self.audio_volume_value_help_text)
        self.audio_volume_segmented.currentIndexChanged.connect(self._on_audio_volume_segmented_changed)
        self.audio_metadata_policy = QComboBox(self)
        self.audio_metadata_policy.addItem("Preservar", "PRESERVE")
        self.audio_metadata_policy.addItem("Remover", "REMOVE")
        self.audio_metadata_policy.hide()
        self.audio_track_selector = _MultiSelectComboBox(main_section)
        self.audio_track_selector.setObjectName("ConfigurationInlineCombo")
        self.audio_track_selector.setMinimumWidth(306)
        self.audio_track_selector.setMaximumWidth(306)
        self.audio_track_selector.setPlaceholderTexts("Selecione uma ou mais trilhas", "Todas as trilhas", "Nenhuma trilha disponível")
        self.audio_track_selector.setToolTip(self.audio_track_value_help_text)
        self.audio_track_selector.selectionChanged.connect(self._on_audio_profile_changed)
        self.audio_subtitle_selector = _ConfigurationComboBox(main_section)
        self.audio_subtitle_selector.setObjectName("ConfigurationInlineCombo")
        self.audio_subtitle_selector.setMinimumWidth(272)
        self.audio_subtitle_selector.setMaximumWidth(272)
        self.audio_subtitle_selector.currentIndexChanged.connect(self._on_audio_profile_changed)
        self.audio_extract_subtitle_button = QToolButton(main_section)
        self.audio_extract_subtitle_button.setObjectName("ConfigurationIconActionButton")
        self.audio_extract_subtitle_button.setToolTip("Extrair legenda para arquivo separado")
        self.audio_extract_subtitle_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.audio_extract_subtitle_button.setAutoRaise(False)
        self.audio_extract_subtitle_button.setFixedSize(30, 30)
        self.audio_extract_subtitle_button.setIconSize(QSize(14, 14))
        self.audio_extract_subtitle_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.audio_extract_subtitle_button.clicked.connect(self._on_extract_audio_subtitle_clicked)
        subtitle_control = QWidget(main_section)
        subtitle_control_layout = QHBoxLayout(subtitle_control)
        subtitle_control_layout.setContentsMargins(0, 0, 0, 0)
        subtitle_control_layout.setSpacing(2)
        subtitle_control_layout.addWidget(self.audio_subtitle_selector, 1, Qt.AlignmentFlag.AlignVCenter)
        subtitle_control_layout.addWidget(self.audio_extract_subtitle_button, 0, Qt.AlignmentFlag.AlignVCenter)
        subtitle_control_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        subtitle_control.setFixedWidth(306)
        subtitle_control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.audio_subtitle_help_text = "Legenda detectada pode ser selecionada ou salva separadamente."
        self.audio_subtitle_value_help_text = "Arquivos de áudio normalmente não têm legenda."
        self.audio_bitrate_row = self._create_form_row(main_section, "Bitrate de áudio", self.audio_bitrate, self.audio_bitrate_help_text, self.audio_bitrate_value_help_text, label_width=136)
        self.audio_quality_mode_row = self._create_form_row(main_section, "Modo de áudio", self.audio_quality_mode, self.audio_quality_mode_help_text, self.audio_quality_mode_value_help_text, label_width=136)
        self.audio_channels_row = self._create_form_row(main_section, "Canais de áudio", self.audio_channel_policy, self.audio_channels_help_text, self.audio_channels_value_help_text, label_width=136)
        self.audio_tracks_row = self._create_form_row(main_section, "Trilhas de áudio", self.audio_track_selector, self.audio_track_help_text, self.audio_track_value_help_text, label_width=136)
        self.audio_volume_row = self._create_form_row(main_section, "Normalização", self.audio_volume_segmented, self.audio_volume_help_text, self.audio_volume_value_help_text, label_width=136)
        self.audio_subtitle_row = self._create_form_row(main_section, "Legenda", subtitle_control, self.audio_subtitle_help_text, self.audio_subtitle_value_help_text, label_width=136)
        subtitle_label = self.audio_subtitle_row.findChild(QLabel, "ConfigurationFormLabel")
        if subtitle_label is not None:
            subtitle_label.setText("Legenda")
            subtitle_label.setToolTip(self.audio_subtitle_help_text)
        self.audio_subtitle_row.setToolTip(self.audio_subtitle_value_help_text)
        subtitle_control.setToolTip(self.audio_subtitle_value_help_text)
        self.audio_subtitle_selector.setToolTip(self.audio_subtitle_value_help_text)
        self.audio_extract_subtitle_button.setToolTip("Salvar legenda disponível como arquivo de texto")
        right_column_rows = {self.audio_tracks_row, self.audio_volume_row, self.audio_subtitle_row}
        for row in (
            self.audio_bitrate_row,
            self.audio_quality_mode_row,
            self.audio_channels_row,
            self.audio_tracks_row,
            self.audio_volume_row,
            self.audio_subtitle_row,
        ):
            if row in right_column_rows:
                label_width = 110
                spacing = 6
            else:
                label_width = 118
                spacing = 6
            self._configure_advanced_form_row(row, label_width=label_width, spacing=spacing)
            row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            row.setMinimumHeight(38)
            row.setMaximumHeight(38)
            row.layout().setContentsMargins(0, 0, 0, 0)
            row.layout().setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = row.findChild(QLabel, "ConfigurationFormLabel")
            if label is not None:
                label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.audio_output_format.setMinimumWidth(186)
        self.audio_output_format.setMaximumWidth(198)
        self.audio_volume_segmented.setMinimumWidth(306)
        self.audio_volume_segmented.setMaximumWidth(306)
        top_row = QWidget(content_frame)
        top_row.setObjectName("AudioTopHighlightRow")
        top_row.setStyleSheet("QWidget#AudioTopHighlightRow { background: rgba(0,0,0,0.035); border-radius: 8px; }")
        top_row_layout = QHBoxLayout(top_row)
        top_row_layout.setContentsMargins(12, 7, 12, 7)
        top_row_layout.setSpacing(8)
        self.audio_output_label = QLabel("Formato de saída", top_row)
        self.audio_output_label.setObjectName("ConfigurationFormLabel")
        self.audio_output_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        audio_output_label_font = self.audio_output_label.font()
        audio_output_label_font.setBold(True)
        self.audio_output_label.setFont(audio_output_label_font)
        self.audio_output_format.setParent(top_row)
        self.audio_output_hint = QLabel("• Selecione o formato de saída do áudio", top_row)
        self.audio_output_hint.setObjectName("ConfigurationMutedHint")
        self.audio_output_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.audio_output_hint.setWordWrap(False)
        self.audio_output_hint.setStyleSheet("")
        self.audio_output_hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        top_row_layout.addWidget(self.audio_output_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_row_layout.addWidget(self.audio_output_format, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_row_layout.addWidget(self.audio_output_hint, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        lower_split = QWidget(main_section)
        lower_split.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lower_layout = QHBoxLayout(lower_split)
        lower_layout.setContentsMargins(0, 10, 0, 10)
        lower_layout.setSpacing(0)
        lower_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        left_col = QWidget(lower_split)
        left_col.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        left_col.setFixedWidth(332)
        left_layout = QVBoxLayout(left_col)
        left_layout.setContentsMargins(12, 8, 0, 8)
        left_layout.setSpacing(0)
        # Use explicit inter-row spacing instead of stretch ratios. Stretch-only
        # spacing was absorbed by Qt's size hints and produced almost no visible
        # change in the audio profile. Fixed gaps make the three control lines
        # breathe consistently while keeping the block vertically balanced.
        left_layout.addStretch(1)
        # Modo de áudio é um preset/orientação geral do perfil e deve vir antes
        # dos ajustes específicos que ele influencia, como bitrate e canais.
        left_layout.addWidget(self.audio_quality_mode_row, 0, Qt.AlignmentFlag.AlignVCenter)
        left_layout.addSpacing(30)
        left_layout.addWidget(self.audio_bitrate_row, 0, Qt.AlignmentFlag.AlignVCenter)
        left_layout.addSpacing(30)
        left_layout.addWidget(self.audio_channels_row, 0, Qt.AlignmentFlag.AlignVCenter)
        left_layout.addStretch(1)
        right_col = QWidget(lower_split)
        right_col.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        right_col.setFixedWidth(452)
        right_layout = QVBoxLayout(right_col)
        right_layout.setContentsMargins(0, 8, 0, 8)
        right_layout.setSpacing(0)
        right_layout.addStretch(1)
        right_layout.addWidget(self.audio_tracks_row, 0, Qt.AlignmentFlag.AlignVCenter)
        right_layout.addSpacing(30)
        right_layout.addWidget(self.audio_volume_row, 0, Qt.AlignmentFlag.AlignVCenter)
        right_layout.addSpacing(30)
        right_layout.addWidget(self.audio_subtitle_row, 0, Qt.AlignmentFlag.AlignVCenter)
        right_layout.addStretch(1)
        lower_layout.addStretch(1)
        lower_layout.addWidget(left_col, 0)
        lower_layout.addSpacing(58)
        lower_layout.addWidget(right_col, 0)
        lower_layout.addStretch(1)
        main_layout.addWidget(lower_split, 1)
        summary_section = self._create_section()
        summary_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        summary_layout = summary_section.layout()
        summary_layout.setContentsMargins(12, 9, 12, 9)
        summary_layout.setSpacing(0)
        self.audio_summary_bar = QLabel("—", summary_section)
        self.audio_summary_bar.setObjectName("ConfigurationInlineStatusLabel")
        self.audio_summary_bar.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.audio_summary_bar.setWordWrap(True)
        self.audio_summary_bar.setTextFormat(Qt.TextFormat.RichText)
        self.audio_summary_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        summary_layout.addWidget(self.audio_summary_bar, 1)
        summary_section.setMinimumHeight(68)
        summary_section.setMaximumHeight(68)
        self._audio_profile_streams = tuple()
        self._audio_subtitle_streams = tuple()
        self.audio_original_section = None
        self.audio_original_info = None
        self.audio_summary_input = None
        content_layout.addWidget(top_row, 0)
        content_layout.addWidget(main_section, 1)
        content_layout.addWidget(summary_section, 0)
        layout.addWidget(content_frame, 1)
        return page
    def _audio_selected_track_ids(self) -> list[int]:
        selected_ids: list[int] = []
        selector = getattr(self, "audio_track_selector", None)
        if selector is None:
            return selected_ids
        for value in selector.checkedValues():
            try:
                selected_ids.append(int(value))
            except Exception:
                continue
        return list(dict.fromkeys(selected_ids))
    def _selected_audio_profile_track(self) -> tuple[str, int | None]:
        selected_ids = self._audio_selected_track_ids()
        stream_count = len(getattr(self, "_audio_profile_streams", ()) or ())
        if stream_count <= 0:
            return ("KEEP_DEFAULT_ONLY", None)
        if len(selected_ids) >= stream_count and stream_count > 1:
            return ("KEEP_ALL", None)
        if not selected_ids:
            return ("KEEP_DEFAULT_ONLY", None)
        if len(selected_ids) == 1 and selected_ids[0] == 0:
            return ("KEEP_DEFAULT_ONLY", None)
        return ("SELECTED_ONLY", selected_ids[0])
    def _selected_audio_profile_track_ids(self) -> list[int] | None:
        selected_ids = self._audio_selected_track_ids()
        if not selected_ids:
            return None
        stream_count = len(getattr(self, "_audio_profile_streams", ()) or ())
        if stream_count > 0 and len(selected_ids) >= stream_count:
            return None
        return selected_ids
    def _format_audio_original_info(self, streams) -> str:
        if not streams:
            return "Nenhum áudio detectado no arquivo atual."
        primary = streams[0] if streams else {}
        codec = str(primary.get("codec_name") or "audio").upper()
        bitrate = _format_bitrate(primary.get("bit_rate")) or "bitrate desconhecido"
        channels = int(primary.get("channels") or 2)
        track_count = len(streams)
        duration = getattr(self._context_job, "duration", None)
        duration_label = ""
        try:
            total_seconds = int(round(float(duration))) if duration not in (None, "") else 0
        except Exception:
            total_seconds = 0
        if total_seconds > 0:
            minutes, seconds = divmod(total_seconds, 60)
            hours, minutes = divmod(minutes, 60)
            duration_label = f"<br><b>Duração:</b> {hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"<br><b>Duração:</b> {minutes:d}:{seconds:02d}"
        return (
            f"<b>Tipo</b>  {'vídeo' if str(getattr(self._context_job, 'source_path', '') or '').lower().endswith(('.mp4', '.mkv', '.webm', '.mov', '.avi', '.flv', '.ts', '.m4v', '.wmv', '.mts', '.m2ts', '.3gp', '.mpg', '.mpeg', '.vob')) else 'áudio'}"
            f"<br><b>Codec</b>  {html.escape(codec)}"
            f"<br><b>Bitrate</b>  {html.escape(bitrate)} • <b>Canais</b>  {channels} • <b>Trilhas</b>  {track_count}{duration_label}"
        )
    def _probe_subtitle_stream_details(self, source_path: str) -> tuple[dict, ...]:
        source_path = str(source_path or "").strip()
        if not source_path:
            return tuple()
        ffprobe_path = ffmpeg_binaries.resolve_ffprobe()
        if not ffprobe_path:
            return tuple()
        try:
            result = run_no_window(
                [ffprobe_path, "-v", "error", "-select_streams", "s", "-show_entries", "stream=index,codec_name:stream_tags=language,title", "-of", "json", source_path],
                capture_output=True, text=True, check=False, timeout=12,
            )
            if result.returncode != 0:
                return tuple()
            payload = json.loads(result.stdout or "{}")
            streams = payload.get("streams") or []
            if isinstance(streams, list):
                return tuple(stream for stream in streams if isinstance(stream, dict))
        except Exception:
            return tuple()
        return tuple()
    def _is_video_like_source(self, source_path: str) -> bool:
        return str(source_path or "").lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v", ".wmv", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".vob"))
    def _refresh_audio_subtitle_context(self, source_path: str):
        self._audio_subtitle_streams = self._probe_subtitle_stream_details(source_path) if self._is_video_like_source(source_path) else tuple()
        selector = getattr(self, "audio_subtitle_selector", None)
        button = getattr(self, "audio_extract_subtitle_button", None)
        if selector is None or button is None:
            return
        current_data = selector.currentData()
        with QSignalBlocker(selector):
            selector.clear()
            if not self._is_video_like_source(source_path):
                selector.addItem("Sem legenda para arquivos de áudio", None)
            elif not self._audio_subtitle_streams:
                selector.addItem("Nenhuma legenda disponível", None)
            else:
                for stream in self._audio_subtitle_streams:
                    tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
                    language = str(tags.get("language") or "").strip()
                    title = str(tags.get("title") or "").strip()
                    codec = str(stream.get("codec_name") or "legenda").upper()
                    label_parts = [f"Trilha {int(stream.get('index', 0)) + 1}"]
                    if language:
                        label_parts.append(language)
                    if title:
                        label_parts.append(title)
                    label_parts.append(codec)
                    selector.addItem(" • ".join(label_parts), int(stream.get("index", 0)))
            restore_index = selector.findData(current_data) if current_data is not None else -1
            selector.setCurrentIndex(restore_index if restore_index >= 0 else 0)
        has_subtitle = selector.currentData() is not None and bool(self._audio_subtitle_streams)
        selector.setEnabled(has_subtitle)
        button.setEnabled(has_subtitle and not self._locked)
        button.setToolTip("Extrair legenda para arquivo separado" if has_subtitle else "Nenhuma legenda disponível para extração")
    def _build_audio_subtitle_output_path(self, source_path: str, stream_index: int, codec_name: str, language: str) -> str:
        source = Path(source_path)
        ext_map = {"subrip": ".srt", "srt": ".srt", "ass": ".ass", "ssa": ".ass", "webvtt": ".vtt", "mov_text": ".srt"}
        ext = ext_map.get(str(codec_name or "").lower(), ".srt")
        lang_suffix = f".{language}" if language else ""
        candidate = source.with_name(f"{source.stem}_subtitle_{stream_index + 1}{lang_suffix}{ext}")
        counter = 2
        while candidate.exists():
            candidate = source.with_name(f"{source.stem}_subtitle_{stream_index + 1}{lang_suffix}_{counter}{ext}")
            counter += 1
        return str(candidate)
    def _on_extract_audio_subtitle_clicked(self):
        source_path = str(getattr(self._context_job, "source_path", None) or "").strip()
        selector = getattr(self, "audio_subtitle_selector", None)
        if not source_path or selector is None or selector.currentData() is None:
            return
        ffmpeg_path = ffmpeg_binaries.resolve_ffmpeg()
        if not ffmpeg_path:
            QMessageBox.warning(self, "Extrair legenda", "FFmpeg não encontrado para extrair a legenda.")
            return
        stream_index = int(selector.currentData())
        stream = next((item for item in self._audio_subtitle_streams if int(item.get("index", -1)) == stream_index), None)
        if not stream:
            QMessageBox.warning(self, "Extrair legenda", "Nenhuma trilha de legenda válida foi encontrada.")
            return
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        language = str(tags.get("language") or "").strip()
        codec_name = str(stream.get("codec_name") or "").strip()
        output_path = self._build_audio_subtitle_output_path(source_path, stream_index, codec_name, language)
        try:
            result = run_no_window(
                [ffmpeg_path, "-y", "-i", source_path, "-map", f"0:s:{stream_index}?", output_path],
                capture_output=True, text=True, check=False, timeout=120,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Extrair legenda", f"Falha ao iniciar a extração da legenda.\n{exc}")
            return
        if result.returncode != 0 or not Path(output_path).exists():
            QMessageBox.warning(self, "Extrair legenda", "Não foi possível extrair a legenda selecionada para um arquivo separado.")
            return
        QMessageBox.information(self, "Extrair legenda", f"Legenda extraída com sucesso:\n{output_path}")
    def _refresh_audio_profile_context(self):
        source_path = str(getattr(self._context_job, "source_path", None) or "").strip()
        streams = advanced_profile._probe_audio_stream_details(source_path) if source_path else tuple()
        self._audio_profile_streams = tuple(streams or ())
        selected_ids = set(self._selected_audio_profile_track_ids() or [])
        self.audio_track_selector.clearItems()
        if streams:
            if not selected_ids:
                selected_ids = {0}
            for idx, stream in enumerate(streams):
                channels = int(stream.get("channels") or 2)
                codec = str(stream.get("codec_name") or "audio").upper()
                tags = stream.get("tags") if isinstance(stream, dict) else {}
                language = str((tags or {}).get("language") or "").strip().upper()
                parts = [f"Trilha {idx + 1}"]
                if language:
                    parts.append(language)
                parts.extend((codec, f"{channels} ch"))
                label = " • ".join(parts)
                self.audio_track_selector.addCheckItem(label, idx, idx in selected_ids)
        if self.audio_original_info is not None:
            self.audio_original_info.setText(self._format_audio_original_info(streams))
        self._sync_audio_bitrate_to_detected_track()
        self._sync_audio_bitrate_to_effective_cap()
        self._refresh_audio_subtitle_context(source_path)
        self._update_audio_profile_summary()

    def _audio_profile_detected_track_bitrate_kbps(self) -> int | None:
        streams = tuple(getattr(self, "_audio_profile_streams", ()) or ())
        if not streams:
            return None
        selected_ids = self._audio_selected_track_ids()
        if not selected_ids:
            selected_ids = [0]
        bitrates: list[int] = []
        for selected_id in selected_ids:
            try:
                stream = streams[int(selected_id)]
            except Exception:
                continue
            if not isinstance(stream, dict):
                continue
            kbps = self._parse_bitrate_kbps(stream.get("bit_rate"), plain_unit="bps")
            if kbps is not None:
                bitrates.append(kbps)
        if not bitrates:
            return None
        return max(1, int(round(sum(bitrates) / len(bitrates))))

    def _ensure_audio_bitrate_option(self, kbps: int) -> int:
        try:
            kbps = max(1, int(round(float(kbps))))
        except Exception:
            kbps = 128
        existing = self.audio_bitrate.findData(kbps)
        if existing >= 0:
            return existing
        label = f"{kbps} kbps"
        insert_at = self.audio_bitrate.count()
        for idx in range(self.audio_bitrate.count()):
            try:
                current = int(self.audio_bitrate.itemData(idx))
            except Exception:
                continue
            if kbps < current:
                insert_at = idx
                break
        self.audio_bitrate.insertItem(insert_at, label, kbps)
        return insert_at

    def _sync_audio_bitrate_to_detected_track(self):
        output_format = str(self.audio_output_format.currentData() or audio_profile.AUDIO_DEFAULT_OUTPUT_FORMAT)
        if not audio_profile.uses_target_bitrate(output_format):
            return
        detected = self._audio_profile_detected_track_bitrate_kbps()
        if detected is None:
            return
        # Keep the exact rounded bitrate displayed by the probed track label,
        # including non-preset values such as 94 kbps.
        suggested = max(1, int(round(float(detected))))
        index = self._ensure_audio_bitrate_option(suggested)
        if self.audio_bitrate.currentData() != suggested:
            blocker = QSignalBlocker(self.audio_bitrate)
            self.audio_bitrate.setCurrentIndex(index)
            del blocker
        if self._context_job is not None:
            try:
                setattr(self._context_job, "audio_bitrate_kbps", int(suggested))
            except Exception:
                pass
    def _audio_quality_mode_target_bitrate_kbps(self) -> int | None:
        output_format = str(self.audio_output_format.currentData() or audio_profile.AUDIO_DEFAULT_OUTPUT_FORMAT)
        if not audio_profile.uses_target_bitrate(output_format):
            return None
        mode = str(self.audio_quality_mode.currentData() or "BALANCED").strip().upper()
        detected = self._audio_profile_detected_track_bitrate_kbps()
        if mode == "QUALITY":
            return max(128, min(192, int(detected or 192)))
        if mode == "SPACE":
            return 96
        return 128

    def _apply_audio_quality_mode_preset(self) -> None:
        target = self._audio_quality_mode_target_bitrate_kbps()
        if target is None:
            return
        index = self._ensure_audio_bitrate_option(target)
        if index >= 0 and self.audio_bitrate.currentData() != target:
            blocker = QSignalBlocker(self.audio_bitrate)
            try:
                self.audio_bitrate.setCurrentIndex(index)
            finally:
                del blocker
        self._sync_audio_bitrate_to_effective_cap()

    def _on_audio_volume_segmented_changed(self, *args):
        data = self.audio_volume_segmented.currentData()
        combo = getattr(self, "audio_volume_normalization", None)
        if combo is not None:
            index = combo.findData(data)
            if index >= 0 and combo.currentIndex() != index:
                blocker = QSignalBlocker(combo)
                combo.setCurrentIndex(index)
                del blocker
        self._on_audio_profile_changed()

    def _on_audio_profile_changed(self, *args):
        # Keep the initial/default bitrate aligned with the probed track only
        # when source-dependent controls change. A direct user selection in
        # "Bitrate de áudio" must not be overwritten back to the detected value.
        self._reset_cancelled_audio_context_on_control_change()
        sender = self.sender()
        if sender is getattr(self, "audio_quality_mode", None):
            if not self._suppress_live_updates:
                self._apply_audio_quality_mode_preset()
        elif sender in (None, self.audio_output_format, self.audio_track_selector):
            self._sync_audio_bitrate_to_detected_track()
            self._sync_audio_bitrate_to_effective_cap()
        self._update_audio_profile_summary()
        self._sync_profile_preview_to_context(emit_apply=False)

    def _is_reprocessable_terminal_status(self, job) -> bool:
        status = str(getattr(job, "status", "") or "").strip().upper()
        return status in {
            "CANCELLED", "CANCELED", "CANCELADO",
            "DONE", "COMPLETED", "COMPLETE", "FINISHED", "SUCCESS",
            "CONCLUIDO", "CONCLUÍDO",
        }

    def _reset_cancelled_audio_context_on_control_change(self) -> None:
        job = self._context_job
        if (
            job is None
            or self._suppress_live_updates
            or self._current_mode_name() != "audio"
            or not self._is_reprocessable_terminal_status(job)
        ):
            return
        # The dedicated Audio page has controls whose handlers normalize bitrate,
        # track and segmented state before the generic collect_values() comparison
        # can see the previous value. A real signal from those controls must still
        # invalidate CANCELADO and make the job compressible again, even if a later
        # preview recalculation short-circuits before emitting job_updated.
        setattr(job, "status", "READY")
        setattr(job, "progress", 0)
        setattr(job, "error", None)
        event_bridge.emit("job_updated", {"job": job})

    def _build_audio_profile_preview_job(self, requested_bitrate_kbps: int | None = None):
        source = self._context_job
        data = {}
        if source is not None:
            try:
                data.update(vars(source))
            except Exception:
                for attr in (
                    "source_path", "input_path", "duration", "input_bitrate", "source_bitrate",
                    "total_bitrate", "bitrate", "primary_audio_bitrate", "source_audio_bitrate",
                    "input_audio_bitrate", "audio_bitrate", "primary_audio_channels",
                    "estimated_size_bytes", "estimated_output_bitrate",
                ):
                    if hasattr(source, attr):
                        data[attr] = getattr(source, attr)
        track_policy, selected_track_id = self._selected_audio_profile_track()
        selected_track_ids = self._selected_audio_profile_track_ids()
        output_format = str(self.audio_output_format.currentData() or audio_profile.AUDIO_DEFAULT_OUTPUT_FORMAT)
        data.update({
            "profile_mode": "audio",
            "audio_output_format": output_format,
            "audio_output_extension": audio_profile.extension_for_format(output_format),
            "audio_bitrate_kbps": int(requested_bitrate_kbps if requested_bitrate_kbps is not None else (self.audio_bitrate.currentData() or 128)),
            "audio_track_policy": track_policy,
            "selected_track_id": selected_track_id if track_policy == "SELECTED_ONLY" else None,
            "selected_track_ids": selected_track_ids if track_policy == "SELECTED_ONLY" else None,
            "audio_channel_policy": self.audio_channel_policy.currentData() or "KEEP_ORIGINAL",
            "audio_sample_rate": "ORIGINAL",
            "audio_quality_mode": self.audio_quality_mode.currentData() or "BALANCED",
            "audio_volume_normalization": self.audio_volume_normalization.currentData() or "OFF",
            "audio_metadata_policy": self.audio_metadata_policy.currentData() or "PRESERVE",
        })
        return SimpleNamespace(**data)

    def _effective_audio_profile_bitrate_kbps(self, requested_bitrate_kbps: int | None = None) -> int:
        output_format = str(self.audio_output_format.currentData() or audio_profile.AUDIO_DEFAULT_OUTPUT_FORMAT)
        requested = audio_profile.normalize_bitrate_kbps(
            requested_bitrate_kbps if requested_bitrate_kbps is not None else self.audio_bitrate.currentData(),
            output_format=output_format,
        )
        if not audio_profile.uses_target_bitrate(output_format):
            return requested
        try:
            return max(1, int(audio_profile.effective_audio_bitrate_kbps(self._build_audio_profile_preview_job(requested))))
        except Exception:
            return requested

    def _sync_audio_bitrate_to_effective_cap(self):
        output_format = str(self.audio_output_format.currentData() or audio_profile.AUDIO_DEFAULT_OUTPUT_FORMAT)
        if not audio_profile.uses_target_bitrate(output_format):
            return
        requested = int(self.audio_bitrate.currentData() or 128)
        effective = self._effective_audio_profile_bitrate_kbps(requested)
        if effective >= requested:
            return
        index = self._ensure_audio_bitrate_option(effective)
        blocker = QSignalBlocker(self.audio_bitrate)
        self.audio_bitrate.setCurrentIndex(index)
        del blocker

    def _update_audio_profile_summary(self):
        effective_bitrate = self._effective_audio_profile_bitrate_kbps()
        original_bitrate = self._source_audio_bitrate_kbps()
        normalization_label = self.audio_volume_normalization.currentText() if self.audio_volume_normalization.count() > 0 else "Desativada"
        output_format = str(self.audio_output_format.currentData() or audio_profile.AUDIO_DEFAULT_OUTPUT_FORMAT)
        source_path = getattr(self._context_job, "source_path", None) if self._context_job is not None else None
        lossless_warning = audio_profile.lossy_to_lossless_warning(source_path, output_format)

        if lossless_warning:
            message = lossless_warning
        elif original_bitrate is not None and effective_bitrate < original_bitrate:
            message = "Compressão adicional: menor tamanho, menor fidelidade."
        elif original_bitrate is not None and effective_bitrate == original_bitrate:
            message = "Bitrate original preservado para evitar aumento de tamanho."
        elif original_bitrate is not None and effective_bitrate > original_bitrate:
            message = "Atenção: bitrate acima do original pode aumentar o arquivo."
        else:
            message = "Conversão de áudio com parâmetros seguros."

        if normalization_label.upper() != "DESATIVADA":
            message += f" Normalização {normalization_label.lower()} ativa."

        format_label = output_format.upper()
        bitrate_label = f"{effective_bitrate} kbps" if audio_profile.uses_target_bitrate(output_format) else "bitrate automático"
        quality_mode_label = self.audio_quality_mode.currentText().strip() if self.audio_quality_mode.count() > 0 else "Equilibrado"
        channel_label = self.audio_channel_policy.currentText().strip() if self.audio_channel_policy.count() > 0 else "Canais originais"
        profile_line = (
            f"<b>Perfil Áudio:</b> {html.escape(format_label)} • "
            f"{html.escape(bitrate_label)} • {html.escape(quality_mode_label)} • {html.escape(channel_label)}"
        )
        self.audio_summary_bar.setText(
            f'<div style="line-height: 150%;">{profile_line}<br>{html.escape(message)}</div>'
        )
    def _create_section(self, title: str | None = None) -> QFrame:
        section = QFrame(self)
        section.setObjectName("ConfigurationSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        if title:
            label = QLabel(title)
            label.setObjectName("ConfigurationSectionTitle")
            layout.addWidget(label)
        return section
    def _create_help_icon(self, tooltip: str, parent: QWidget) -> QToolButton:
        icon = QToolButton(parent)
        icon.setObjectName("ConfigurationHelpIcon")
        icon.setText("?")
        icon.setToolTip(str(tooltip or ""))
        icon.setAutoRaise(False)
        icon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        icon.setCursor(Qt.CursorShape.WhatsThisCursor)
        icon.setFixedSize(14, 14)
        icon.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return icon

    def _create_form_row(self, parent: QWidget, label_text: str, field: QWidget, help_text: str | None = None, value_help_text: str | None = None, label_width: int = 120) -> QWidget:
        row = QWidget(parent)
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        label_cell = QWidget(row)
        label_cell.setObjectName("ConfigurationFormLabelCell")
        label_cell.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        label_layout = QHBoxLayout(label_cell)
        label_layout.setContentsMargins(0, 0, 0, 0)
        label_layout.setSpacing(4)
        label_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label = QLabel(label_text, label_cell)
        label.setObjectName("ConfigurationFormLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        if help_text:
            label.setToolTip(str(help_text))
        label_layout.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        if help_text:
            label_layout.addWidget(self._create_help_icon(str(help_text), label_cell), 0, Qt.AlignmentFlag.AlignVCenter)
        label_layout.addStretch(1)
        label_cell.setMinimumWidth(label_width)
        label_cell.setMaximumWidth(label_width)
        layout.addWidget(label_cell)
        field.setParent(row)
        if value_help_text:
            field.setToolTip(str(value_help_text))
        if field.minimumHeight() < 28:
            field.setMinimumHeight(28)
        layout.addWidget(field, 1)
        return row
    def _set_form_row_label(self, row: QWidget, text: str):
        label = row.findChild(QLabel, "ConfigurationFormLabel")
        if label is not None:
            label.setText(text)
    def _configure_advanced_form_row(self, row: QWidget, label_width: int = 96, spacing: int = 6):
        layout = row.layout()
        if layout is not None:
            layout.setSpacing(spacing)
        label_cell = row.findChild(QWidget, "ConfigurationFormLabelCell")
        if label_cell is not None:
            label_cell.setMinimumWidth(label_width)
            label_cell.setMaximumWidth(label_width)
        label = row.findChild(QLabel, "ConfigurationFormLabel")
        if label is not None:
            label.setMinimumWidth(0)
            label.setMaximumWidth(16777215)
    def _create_output_metric(self, parent: QWidget, title: str) -> tuple[QWidget, QLabel]:
        row = QWidget(parent)
        row.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row.setMinimumWidth(176)
        row.setMaximumWidth(176)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title_label = QLabel(title, row)
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_label.setMinimumWidth(72)
        title_label.setMaximumWidth(72)
        title_label.setObjectName("ConfigurationAdvancedOutputMetricLabel")
        row.setMinimumHeight(23)
        row.setMaximumHeight(23)
        layout.addWidget(title_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        value_label = QLabel("—", row)
        value_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        value_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        value_label.setMinimumWidth(96)
        value_label.setMaximumWidth(96)
        value_label.setObjectName("ConfigurationAdvancedOutputMetricValue")
        value_label.setWordWrap(False)
        layout.addWidget(value_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_output_metrics_layout.addWidget(row, 0, Qt.AlignmentFlag.AlignRight)
        return row, value_label
    def _on_advanced_target_mode_button_toggled(self, value: str, checked: bool):
        if not checked:
            return
        index = self.advanced_target_mode.findData(value)
        if index >= 0 and self.advanced_target_mode.currentIndex() != index:
            self.advanced_target_mode.setCurrentIndex(index)
    def _sync_advanced_target_mode_buttons(self):
        current_value = self.advanced_target_mode.currentData() or "TARGET_SIZE"
        for value, button in getattr(self, "advanced_target_mode_buttons", {}).items():
            button.blockSignals(True)
            button.setChecked(value == current_value)
            button.blockSignals(False)
    def _current_mode_name(self) -> str:
        for name, index in self.MODE_INDEX_BY_NAME.items():
            if self.mode_stack.currentIndex() == index:
                return name
        return "quick"
    def _set_mode(self, mode_name: str):
        normalized = str(mode_name or "quick").strip().lower()
        if normalized == "automatic":
            normalized = "quick"
        index = self.MODE_INDEX_BY_NAME.get(normalized, 0)
        button = self.mode_group.button(index)
        if button is not None:
            button.setChecked(True)
        self.mode_stack.setCurrentIndex(index)

    def _is_audio_only_context(self) -> bool:
        job = self._context_job
        if job is None:
            return False
        source_path = str(getattr(job, "source_path", "") or "").strip()
        if not source_path:
            return False
        return not self._is_video_like_source(source_path)

    def _update_mode_availability(self):
        audio_only = self._is_audio_only_context()
        for name, index in self.MODE_INDEX_BY_NAME.items():
            button = self.mode_group.button(index)
            if button is None:
                continue
            is_disabled = audio_only and name in _AUDIO_ONLY_DISABLED_MODES
            button.setEnabled(not is_disabled)
            button.setToolTip(_AUDIO_ONLY_MODE_TOOLTIP if is_disabled else "")
        if audio_only and self._current_mode_name() in _AUDIO_ONLY_DISABLED_MODES:
            self._set_mode("audio")

    def set_context_job(self, job, *, selected_count: int = 1):
        preferred_mode = self._current_mode_name()
        self._context_job = job
        try:
            self._context_selected_count = max(1, int(selected_count or 1))
        except Exception:
            self._context_selected_count = 1
        self._load_from_context(preferred_mode=preferred_mode)
        self._update_mode_availability()
        self._sync_analyze_media_button_state()
    def clear_context_job(self):
        self._context_job = None
        self._context_selected_count = 1
        self._load()
        self._update_mode_availability()
        self._sync_analyze_media_button_state()
    def _restore_audio_help_tooltips(self):
        tooltip_pairs = (
            (getattr(self, "audio_bitrate", None), getattr(self, "audio_bitrate_value_help_text", "")),
            (getattr(self, "audio_quality_mode", None), getattr(self, "audio_quality_mode_value_help_text", "")),
            (getattr(self, "audio_channel_policy", None), getattr(self, "audio_channels_value_help_text", "")),
            (getattr(self, "audio_track_selector", None), getattr(self, "audio_track_value_help_text", "")),
            (getattr(self, "audio_volume_segmented", None), getattr(self, "audio_volume_value_help_text", "")),
            (getattr(self, "audio_subtitle_selector", None), getattr(self, "audio_subtitle_value_help_text", "")),
        )
        for widget, tooltip in tooltip_pairs:
            if widget is not None and tooltip:
                widget.setToolTip(str(tooltip))
        subtitle_row = getattr(self, "audio_subtitle_row", None)
        if subtitle_row is not None and getattr(self, "audio_subtitle_value_help_text", ""):
            subtitle_row.setToolTip(str(self.audio_subtitle_value_help_text))

    def _sync_analyze_media_button_state(self):
        button = getattr(self, "btn_analyze_media", None)
        if button is None:
            return
        has_context = self._context_job is not None
        button.setEnabled((not self._locked) and has_context)
        if self._locked:
            button.setToolTip("Indisponível durante o processamento.")
        elif has_context:
            button.setToolTip("Analisa o arquivo em foco e mostra a sugestão automática.")
        else:
            button.setToolTip("Selecione um arquivo para analisar a mídia.")

    def _sync_mode_bar_action_button_widths(self):
        buttons = [
            getattr(self, "btn_analyze_media", None),
            getattr(self, "btn_defaults", None),
            getattr(self, "btn_apply", None),
        ]
        buttons = [button for button in buttons if button is not None]
        if not buttons:
            return
        for button in buttons:
            button.setMinimumWidth(0)
            button.setMaximumWidth(16777215)
            button.adjustSize()
        target_width = max(button.sizeHint().width() for button in buttons)
        for button in buttons:
            button.setFixedWidth(target_width)

    def _automatic_analysis(self):
        return build_automatic_recommendation(
            self._automatic_media_info_from_context(),
            self._source_size_bytes() or 0,
        )

    def _automatic_estimation_range(self, analysis, source_size: int | None) -> tuple[int | None, int | None]:
        estimated = getattr(analysis, "estimated_output_bytes", None)
        if not estimated:
            return None, None
        label = str(getattr(analysis, "detected_label", "") or "").lower()
        if any(token in label for token in ("aula", "slides", "tela")):
            low_factor, high_factor = 0.78, 1.12
        elif any(token in label for token in ("karaok", "lyrics", "legenda", "baixa variação visual")):
            low_factor, high_factor = 0.86, 1.18
        elif "muito comprimido" in label:
            low_factor, high_factor = 0.94, 1.08
        else:
            low_factor, high_factor = 0.88, 1.16
        low = max(1, int(round(float(estimated) * low_factor)))
        high = max(low, int(round(float(estimated) * high_factor)))
        if source_size and source_size > 0:
            high = min(high, int(source_size))
            low = min(low, high)
        return low, high

    @staticmethod
    def _automatic_reduction_range_text(source_size: int | None, low_bytes: int | None, high_bytes: int | None) -> str:
        if not source_size or not low_bytes or not high_bytes or source_size <= 0:
            return "—"
        best = max(0.0, (1.0 - (float(low_bytes) / float(source_size))) * 100.0)
        conservative = max(0.0, (1.0 - (float(high_bytes) / float(source_size))) * 100.0)
        if round(conservative) == round(best):
            return f"aprox. {best:.0f}%"
        return f"aprox. {conservative:.0f}%–{best:.0f}%"

    @staticmethod
    def _automatic_table_row(label: str, value: str) -> str:
        return (
            "<tr>"
            f"<td style='padding:1px 12px 1px 0; white-space:nowrap;'><b>{html.escape(label)}</b></td>"
            f"<td style='padding:1px 0;'>{html.escape(value)}</td>"
            "</tr>"
        )

    def _automatic_analysis_config_rows(self, analysis, *, include_format: bool = True) -> str:
        source_resolution = f"{analysis.width}×{analysis.height}" if analysis.width and analysis.height else "sem vídeo"
        suggested_resolution = (
            f"{analysis.suggested_width}×{analysis.suggested_height}"
            if analysis.suggested_width and analysis.suggested_height
            else f"{source_resolution} (original)" if analysis.media_kind == "video" else "sem vídeo"
        )
        suggested_fps = (
            self._format_automatic_fps(analysis.suggested_fps)
            if analysis.suggested_fps
            else "manter original"
        )
        rows = ""
        if analysis.media_kind == "video":
            rows += self._automatic_table_row("Resolução:", suggested_resolution)
            rows += self._automatic_table_row("FPS:", suggested_fps)
            rows += self._automatic_table_row("Bitrate de vídeo:", self._format_automatic_bitrate(analysis.suggested_video_bitrate_bps))
        rows += self._automatic_table_row("Bitrate de áudio:", self._automatic_option_audio_text(analysis))
        if include_format:
            rows += self._automatic_table_row("Formato de saída:", str(analysis.output_format or "").upper() or "—")
        return rows

    def _automatic_analysis_result_rows(self, analysis, source_size: int | None) -> str:
        low_estimate, high_estimate = self._automatic_estimation_range(analysis, source_size)
        if low_estimate and high_estimate and low_estimate != high_estimate:
            estimated_text = f"{_format_bytes(low_estimate)}–{_format_bytes(high_estimate)}"
        else:
            estimated_text = _format_bytes(getattr(analysis, "estimated_output_bytes", None)) or "?"
        reduction_text = self._automatic_reduction_range_text(source_size, low_estimate, high_estimate)
        return "".join([
            self._automatic_table_row("Tamanho provável:", estimated_text),
            self._automatic_table_row("Redução provável:", reduction_text),
        ])

    def _automatic_original_media_pairs(self, analysis, source_size: int | None) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = [
            ("Tamanho:", _format_bytes(source_size) or "?"),
        ]
        if getattr(analysis, "media_kind", "") == "video":
            if getattr(analysis, "width", None) and getattr(analysis, "height", None):
                pairs.append(("Resolução:", f"{int(analysis.width)}×{int(analysis.height)}"))
            if getattr(analysis, "fps", None):
                pairs.append(("FPS:", self._format_automatic_fps(analysis.fps)))
            pairs.append(("Bitrate de vídeo:", self._format_automatic_bitrate(getattr(analysis, "video_bitrate_bps", None))))
        source_audio_kbps = self._source_audio_bitrate_kbps()
        if source_audio_kbps:
            audio_text = f"{int(source_audio_kbps)} kbps"
        else:
            audio_text = self._format_automatic_bitrate(getattr(analysis, "audio_bitrate_bps", None))
        pairs.append(("Bitrate de áudio:", audio_text))
        output_format = str(getattr(analysis, "output_format", "") or "").upper()
        if output_format:
            pairs.append(("Formato:", output_format))
        return pairs

    def _automatic_original_media_html(self, analysis, source_size: int | None) -> str:
        pairs = self._automatic_original_media_pairs(analysis, source_size)
        column_count = 3 if len(pairs) >= 5 else 2
        rows = []
        for row_start in range(0, len(pairs), column_count):
            row_cells = []
            for label_text, value_text in pairs[row_start:row_start + column_count]:
                row_cells.append(
                    "<td style='padding:2px 18px 2px 0; white-space:nowrap;'>"
                    f"<b>{html.escape(label_text)}</b> {html.escape(value_text)}"
                    "</td>"
                )
            while len(row_cells) < column_count:
                row_cells.append("<td></td>")
            rows.append("<tr>" + "".join(row_cells) + "</tr>")

        tokens = build_theme_tokens(self.palette())
        header_bg = QColor(tokens.surface_panel)
        header_bg.setRed(round((header_bg.red() * 2 + tokens.surface_card_header.red()) / 3))
        header_bg.setGreen(round((header_bg.green() * 2 + tokens.surface_card_header.green()) / 3))
        header_bg.setBlue(round((header_bg.blue() * 2 + tokens.surface_card_header.blue()) / 3))
        header_bg.setAlpha(255)
        header_text = QColor(tokens.text_secondary)
        border_color = QColor(tokens.border_panel)
        return (
            "<table width='100%' cellspacing='0' cellpadding='0' "
            f"style='margin:8px 0 3px 0; border:1px solid {border_color.name()};'>"
            f"<tr><td align='center' style='padding:4px 8px; background:{header_bg.name()}; color:{header_text.name()};'>"
            "Mídia original"
            "</td></tr>"
            "<tr><td style='padding:6px 9px;'>"
            "<table width='100%' cellspacing='0' cellpadding='0'>"
            + "".join(rows) +
            "</table></td></tr></table>"
        )

    @staticmethod
    def _automatic_should_show_fps_option(analysis) -> bool:
        suggested_fps = getattr(analysis, "suggested_fps", None)
        source_fps = getattr(analysis, "fps", None)
        if not suggested_fps:
            return False
        try:
            if source_fps and float(suggested_fps) >= float(source_fps) - 0.05:
                return False
        except Exception:
            pass
        return True

    def _automatic_option_resolution_text(self, analysis) -> str:
        source_width = getattr(analysis, "width", None)
        source_height = getattr(analysis, "height", None)
        target_width = getattr(analysis, "suggested_width", None)
        target_height = getattr(analysis, "suggested_height", None)
        if target_width and target_height:
            target_text = f"{int(target_width)}×{int(target_height)}"
            if source_width and source_height and int(target_width) == int(source_width) and int(target_height) == int(source_height):
                return f"{target_text} (original)"
            return target_text
        if source_width and source_height:
            return f"{int(source_width)}×{int(source_height)} (original)"
        return "sem vídeo"

    def _automatic_effective_audio_kbps_for_advanced(self, analysis) -> int | None:
        """Return the audio value that Advanced can actually apply for a recommendation.

        The analysis model may suggest a generic audio ceiling such as 128 kbps.
        In Advanced, however, the audio selector treats the first entry as the
        original stream and only offers explicit values below the detected
        original.  Therefore a suggestion at or above the detected original must
        be represented as preserving the original bitrate; otherwise the card can
        promise 128 kbps while the Advanced panel applies 64 kbps (original).
        """
        suggested_kbps = self._automatic_bps_to_kbps(getattr(analysis, "suggested_audio_bitrate_bps", None))
        if not suggested_kbps:
            return None
        original_kbps = self._source_audio_bitrate_kbps()
        try:
            if original_kbps and int(suggested_kbps) >= int(original_kbps):
                return int(original_kbps)
        except Exception:
            pass
        return int(suggested_kbps)

    def _automatic_option_audio_text(self, analysis) -> str:
        effective_kbps = self._automatic_effective_audio_kbps_for_advanced(analysis)
        if not effective_kbps:
            return self._format_automatic_bitrate(getattr(analysis, "suggested_audio_bitrate_bps", None))
        original_kbps = self._source_audio_bitrate_kbps()
        try:
            if original_kbps and int(effective_kbps) == int(original_kbps):
                return f"{int(effective_kbps)} kbps (original)"
        except Exception:
            pass
        return f"{int(effective_kbps)} kbps"

    def _automatic_option_total_bitrate_text(self, analysis) -> str:
        video_kbps = 0
        if getattr(analysis, "media_kind", "") == "video":
            try:
                video_bps = int(float(getattr(analysis, "suggested_video_bitrate_bps", None) or 0))
                video_kbps = max(0, int(round(video_bps / 1000.0)))
            except Exception:
                video_kbps = 0
        try:
            audio_kbps = int(self._automatic_effective_audio_kbps_for_advanced(analysis) or 0)
        except Exception:
            audio_kbps = 0
        total_kbps = video_kbps + audio_kbps
        return f"{total_kbps} kbps" if total_kbps > 0 else "?"

    def _automatic_option_rows(self, analysis, source_size: int | None, *, force_show_fps: bool = False) -> list[tuple[str, str]]:
        low_estimate, high_estimate = self._automatic_estimation_range(analysis, source_size)
        if low_estimate and high_estimate and low_estimate != high_estimate:
            estimated_text = f"{_format_bytes(low_estimate)}–{_format_bytes(high_estimate)}"
        else:
            estimated_text = _format_bytes(getattr(analysis, "estimated_output_bytes", None)) or "?"
        rows: list[tuple[str, str]] = [
            ("Tamanho provável:", estimated_text),
            ("Redução provável:", self._automatic_reduction_range_text(source_size, low_estimate, high_estimate)),
        ]
        if getattr(analysis, "media_kind", "") == "video":
            rows.extend([
                ("Resolução:", self._automatic_option_resolution_text(analysis)),
                ("Vídeo:", self._format_automatic_bitrate(getattr(analysis, "suggested_video_bitrate_bps", None))),
            ])
        rows.append(("Áudio:", self._automatic_option_audio_text(analysis)))
        if getattr(analysis, "media_kind", "") == "video" and (force_show_fps or self._automatic_should_show_fps_option(analysis)):
            suggested_fps = getattr(analysis, "suggested_fps", None)
            if suggested_fps:
                fps_text = self._format_automatic_fps(suggested_fps)
            else:
                source_fps = getattr(analysis, "fps", None)
                fps_text = f"{self._format_automatic_fps(source_fps)} original" if source_fps else "original"
            rows.append(("FPS:", fps_text))
        return rows

    def _build_automatic_option_card(self, parent: QWidget, title: str, subtitle: str, analysis, source_size: int | None, *, width: int, force_show_fps: bool = False) -> QFrame:
        card = QFrame(parent)
        # Use a plain one-pixel box instead of StyledPanel so the visible card
        # border occupies the same geometry used by the apply button below it.
        # StyledPanel is theme/style dependent and can draw the border inset,
        # which made the card appear slightly narrower than the button.
        card.setFrameShape(QFrame.Shape.Box)
        card.setFrameShadow(QFrame.Shadow.Plain)
        card.setLineWidth(1)
        card.setMidLineWidth(0)
        card.setFixedWidth(width)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(5)

        title_label = QLabel(title, card)
        title_font = QFont(title_label.font())
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setWordWrap(False)
        layout.addWidget(title_label)

        subtitle_label = QLabel(subtitle, card)
        subtitle_label.setStyleSheet("color: #555;")
        subtitle_label.setWordWrap(False)
        layout.addWidget(subtitle_label)

        separator = QFrame(card)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Plain)
        separator.setStyleSheet("color: #c7c7c7;")
        layout.addWidget(separator)

        grid = QGridLayout()
        grid.setContentsMargins(0, 3, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        for row_index, (label_text, value_text) in enumerate(self._automatic_option_rows(analysis, source_size, force_show_fps=force_show_fps)):
            label = QLabel(label_text, card)
            label_font = QFont(label.font())
            label_font.setWeight(QFont.Weight.DemiBold if row_index < 2 else QFont.Weight.Normal)
            label.setFont(label_font)
            label.setWordWrap(False)
            value = QLabel(value_text, card)
            value.setWordWrap(False)
            grid.addWidget(label, row_index, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(value, row_index, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(grid)
        return card

    def _automatic_dual_option_preview_html(self, analysis, source_size: int | None) -> str:
        high_analysis = self._automatic_high_reduction_variant(analysis, source_size)
        balanced_config = self._automatic_analysis_config_rows(analysis, include_format=False)
        balanced_result = self._automatic_analysis_result_rows(analysis, source_size)
        high_config = self._automatic_analysis_config_rows(high_analysis, include_format=False)
        high_result = self._automatic_analysis_result_rows(high_analysis, source_size)

        option_width = 272
        option_gap = 18
        options_total_width = option_width * 2 + option_gap

        def option_card(title: str, subtitle: str, config_rows: str, result_rows: str) -> str:
            return (
                f"<table width='{option_width}' border='1' cellspacing='0' cellpadding='0' style='border-color:#c7c7c7;'>"
                f"<tr><td width='{option_width}' valign='top' bgcolor='#f4f4f4' style='padding:6px 7px 5px 7px;'>"
                f"<p style='margin:0 0 2px 0;'><b>{html.escape(title)}</b></p>"
                f"<p style='margin:0; color:#555;'>{html.escape(subtitle)}</p>"
                "</td></tr>"
                f"<tr><td width='{option_width}' valign='top' style='padding:7px;'>"
                f"<table cellspacing='0' cellpadding='0'>{config_rows}{result_rows}</table>"
                "</td></tr></table>"
            )

        balanced_card = option_card(
            "Equilibrado",
            "mais qualidade, arquivo maior",
            balanced_config,
            balanced_result,
        )
        high_card = option_card(
            "Alta compressão",
            "arquivo menor, mais perda",
            high_config,
            high_result,
        )
        return (
            f"<table align='center' width='{options_total_width}' cellspacing='0' cellpadding='0' style='margin:12px 0 7px 0;'>"
            "<tr><td align='center' style='padding:0 0 6px 0; border-bottom:1px solid #c7c7c7; color:#333;'>"
            "Opções de aplicação"
            "</td></tr></table>"
            f"<table align='center' width='{options_total_width}' cellspacing='0' cellpadding='0'>"
            "<tr>"
            f"<td width='{option_width}' valign='top'>{balanced_card}</td>"
            f"<td width='{option_gap}'></td>"
            f"<td width='{option_width}' valign='top'>{high_card}</td>"
            "</tr>"
            "</table>"
        )

    def _automatic_analysis_html(self, analysis=None) -> str:
        job = self._context_job
        if job is None:
            return "<p>Selecione um arquivo para analisar a mídia.</p>"
        analysis = analysis or self._automatic_analysis()
        source_size = self._source_size_bytes()
        confidence = max(0, min(100, int(round(float(analysis.confidence or 0) * 100))))
        summary_rows = "".join([
            self._automatic_table_row("Detectado:", str(analysis.detected_label or "—")),
            self._automatic_table_row("Estratégia:", str(analysis.strategy_label or "—")),
            self._automatic_table_row("Confiança:", f"{confidence}%"),
        ])
        visual_label = str(getattr(analysis, "visual_motion_label", "") or "").strip()
        if visual_label:
            summary_rows += self._automatic_table_row("Variação visual:", visual_label)

        original_details_html = self._automatic_original_media_html(analysis, source_size)
        if self._automatic_has_high_reduction_variant(analysis):
            suggestion_html = ""
        else:
            config_rows = self._automatic_analysis_config_rows(analysis)
            result_rows = self._automatic_analysis_result_rows(analysis, source_size)
            suggestion_html = (
                "<p style='margin:10px 0 3px 0;'><b>Resultado estimado</b></p>"
                f"<table cellspacing='0' cellpadding='0'>{result_rows}</table>"
                "<p style='margin:10px 0 3px 0;'><b>Configuração sugerida</b></p>"
                f"<table cellspacing='0' cellpadding='0'>{config_rows}</table>"
            )

        intro_text = (
            "Análise por metadados + amostragem leve de frames concluída.<br>"
            "Sugestão calculada com verificação rápida de variação visual."
            if str(getattr(analysis, "visual_motion_label", "") or "").strip()
            else "Análise por metadados concluída.<br>Sugestão calculada sem análise pesada de frames."
        )
        return (
            "<div style='line-height:122%; text-align:left; min-width:535px;'>"
            f"<p style='margin:0 0 5px 0;'>{intro_text}</p>"
            "<p style='margin:9px 0 3px 0;'><b>Resumo da análise</b></p>"
            f"<table cellspacing='0' cellpadding='0'>{summary_rows}</table>"
            f"{original_details_html}"
            f"{suggestion_html}"
            "</div>"
        )

    def _show_automatic_analysis_dialog(self):
        if self._context_job is None:
            QMessageBox.information(self, "Analisar mídia", "Selecione um arquivo para analisar a mídia.")
            return

        analyze_button = getattr(self, "btn_analyze_media", None)
        original_analyze_text = analyze_button.text() if analyze_button is not None else "Analisar mídia"
        if analyze_button is not None:
            analyze_button.setText("Analisando...")
            analyze_button.setEnabled(False)
            analyze_button.repaint()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            analysis = self._automatic_analysis()
            details = self._automatic_analysis_html(analysis)
        except Exception as exc:
            QMessageBox.warning(self, "Analisar mídia", f"Não foi possível montar a sugestão automática: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
            if analyze_button is not None:
                analyze_button.setText(original_analyze_text)
                self._sync_analyze_media_button_state()
                self._sync_mode_bar_action_button_widths()
        dialog = QDialog(self)
        dialog.setWindowTitle("Análise da mídia")
        dialog.setModal(True)
        dialog.setMinimumWidth(635)
        dialog.resize(635, 1)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        title = QLabel("Sugestão automática", dialog)
        title_font = QFont(title.font())
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        body = QLabel(details, dialog)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        body.setMargin(0)
        body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(body)

        observation = QLabel(
            "Observação: a estimativa pode variar conforme o conteúdo real e a eficiência do encoder.",
            dialog,
        )
        observation.setWordWrap(True)
        observation.setStyleSheet("color: #666;")
        observation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        apply_button = QPushButton("Aplicar no Avançado", dialog)
        high_button = None
        cancel_button = QPushButton("Cancelar", dialog)
        if self._automatic_has_high_reduction_variant(analysis):
            apply_button.setText("Aplicar equilibrado")
            high_button = QPushButton("Aplicar alta compressão", dialog)
            option_width = 292
            option_gap = 18

            section_label = QLabel("Opções de aplicação", dialog)
            section_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            section_label.setStyleSheet("color: #333;")
            section_label.setContentsMargins(0, 4, 0, 0)
            layout.addWidget(section_label)

            section_line = QFrame(dialog)
            section_line.setFrameShape(QFrame.Shape.HLine)
            section_line.setFrameShadow(QFrame.Shadow.Plain)
            section_line.setStyleSheet("color: #c7c7c7;")
            layout.addWidget(section_line)

            source_size = self._source_size_bytes()
            high_analysis = self._automatic_high_reduction_variant(analysis, source_size)
            show_fps_row = (
                self._automatic_should_show_fps_option(analysis)
                or self._automatic_should_show_fps_option(high_analysis)
            )

            balanced_card = self._build_automatic_option_card(
                dialog,
                "Equilibrado",
                "mais qualidade, arquivo maior",
                analysis,
                source_size,
                width=option_width,
                force_show_fps=show_fps_row,
            )
            high_card = self._build_automatic_option_card(
                dialog,
                "Alta compressão",
                "arquivo menor, mais perda",
                high_analysis,
                source_size,
                width=option_width,
                force_show_fps=show_fps_row,
            )

            def _option_column(card: QFrame, button: QPushButton) -> QWidget:
                column = QWidget(dialog)
                column.setFixedWidth(option_width)
                column.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                column_layout = QVBoxLayout(column)
                column_layout.setContentsMargins(0, 0, 0, 0)
                column_layout.setSpacing(10)

                card.setFixedWidth(option_width)
                button.setFixedWidth(option_width)
                card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

                column_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignLeft)
                column_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
                return column

            balanced_column = _option_column(balanced_card, apply_button)
            high_column = _option_column(high_card, high_button)

            options_layout = QHBoxLayout()
            options_layout.setContentsMargins(0, 2, 0, 0)
            options_layout.setSpacing(option_gap)
            options_layout.addWidget(balanced_column)
            options_layout.addWidget(high_column)
            layout.addLayout(options_layout)
        else:
            actions = QHBoxLayout()
            actions.setContentsMargins(0, 8, 0, 0)
            actions.addStretch(1)
            actions.addWidget(apply_button)
            layout.addLayout(actions)

        footer_actions = QHBoxLayout()
        footer_actions.setContentsMargins(0, 6, 0, 0)
        footer_actions.setSpacing(10)
        footer_actions.addWidget(observation, 1)
        footer_actions.addWidget(cancel_button, 0)
        layout.addLayout(footer_actions)

        apply_button.clicked.connect(dialog.accept)
        if high_button is not None:
            high_button.clicked.connect(lambda: dialog.done(2))
        cancel_button.clicked.connect(dialog.reject)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            self._apply_automatic_analysis_to_advanced(analysis)
        elif result == 2:
            self._apply_automatic_analysis_to_advanced(self._automatic_high_reduction_variant(analysis, self._source_size_bytes()), high_compression=True)

    @staticmethod
    def _automatic_has_high_reduction_variant(analysis) -> bool:
        # A tela de análise deve manter uma experiência previsível: todo arquivo
        # com vídeo oferece duas aplicações explícitas, Equilibrado e Alta
        # compressão. Arquivos somente áudio continuam com sugestão única porque
        # não há variante visual/resolução/FPS a comparar.
        return getattr(analysis, "media_kind", "") == "video"

    @staticmethod
    def _automatic_high_reduction_variant(analysis, source_size: int | None = None):
        duration = getattr(analysis, "duration_seconds", None)
        label = str(getattr(analysis, "detected_label", "") or "").lower()
        suggested_video_bps = getattr(analysis, "suggested_video_bitrate_bps", None)
        suggested_audio_bps = getattr(analysis, "suggested_audio_bitrate_bps", None)

        if any(token in label for token in ("aula", "slides", "tela")):
            target_video_bps = 160_000
            target_audio_bps = 96_000
            target_height_limit = 720
            strategy_label = "alta compressão: manter leitura e reduzir bitrate/FPS"
        elif any(token in label for token in ("lyric", "lyrics", "karaokê", "karaoke", "karaok")):
            target_video_bps = 96_000
            target_audio_bps = 64_000
            target_height_limit = 360
            strategy_label = "alta compressão: reduzir resolução/vídeo e preservar áudio essencial"
        else:
            try:
                target_video_bps = int((suggested_video_bps or 600_000) * 0.65)
            except Exception:
                target_video_bps = 390_000
            target_video_bps = max(220_000, min(target_video_bps, 1_200_000))
            target_audio_bps = 96_000 if (suggested_audio_bps or 0) >= 96_000 else 64_000
            target_height_limit = 720
            strategy_label = "alta compressão: reduzir bitrate e limitar resolução quando necessário"

        base_estimated = getattr(analysis, "estimated_output_bytes", None)
        estimated = base_estimated
        try:
            if duration and float(duration) > 0:
                estimated = int(((target_video_bps + target_audio_bps) * float(duration) / 8.0) * 1.04)
        except Exception:
            estimated = base_estimated
        estimated = ConfigurationPanelWidget._automatic_clamped_high_reduction_estimate(
            estimated,
            base_estimated,
            source_size,
        )
        try:
            width = getattr(analysis, "width", None)
            height = getattr(analysis, "height", None)
            target_width = getattr(analysis, "suggested_width", None)
            target_height = getattr(analysis, "suggested_height", None)
            if width and height and int(height) > target_height_limit:
                target_height = target_height_limit
                target_width = max(2, int(round((int(width) * (target_height / int(height))) / 2) * 2))
            return dataclass_replace(
                analysis,
                strategy_label=strategy_label,
                confidence=min(0.72, float(getattr(analysis, "confidence", 0.0) or 0.0) + 0.02),
                suggested_width=target_width,
                suggested_height=target_height,
                suggested_video_bitrate_bps=target_video_bps,
                suggested_audio_bitrate_bps=target_audio_bps,
                suggested_fps=24.0 if getattr(analysis, "fps", None) and float(getattr(analysis, "fps")) > 24.0 else getattr(analysis, "suggested_fps", None),
                estimated_output_bytes=estimated,
            )
        except Exception:
            return analysis


    @staticmethod
    def _automatic_clamped_high_reduction_estimate(estimated, base_estimated, source_size: int | None):
        """Return a safe estimate for the high-compression option.

        The high-compression card must never regress above the balanced suggestion
        or above the original file size. Some very small/low-resolution videos can
        already be below the bitrate target computed from duration, so the raw
        duration-based formula may look larger than the original. Clamp it to a
        conservative reduction cap to keep the recommendation coherent.
        """
        try:
            value = int(round(float(estimated))) if estimated is not None else None
        except Exception:
            value = None
        caps: list[int] = []
        try:
            if base_estimated and float(base_estimated) > 0:
                caps.append(max(1, int(round(float(base_estimated) * 0.72))))
        except Exception:
            pass
        try:
            if source_size and int(source_size) > 0:
                caps.append(max(1, int(round(float(source_size) * 0.88))))
        except Exception:
            pass
        if value is None:
            value = min(caps) if caps else None
        elif caps:
            value = min(value, min(caps))
        return value

    def _apply_automatic_analysis_to_advanced(self, analysis, *, high_compression: bool = False):
        if self._context_job is None:
            return
        if getattr(analysis, "media_kind", "") != "video":
            QMessageBox.information(
                self,
                "Analisar mídia",
                "A aplicação automática no Avançado está disponível apenas para arquivos com vídeo nesta etapa.",
            )
            return
        try:
            payload = dict(automatic_recommendation_to_profile_payload(analysis))
        except Exception as exc:
            QMessageBox.warning(self, "Analisar mídia", f"Não foi possível aplicar a sugestão: {exc}")
            return

        video_kbps = self._automatic_bps_to_kbps(getattr(analysis, "suggested_video_bitrate_bps", None))
        audio_kbps = self._automatic_effective_audio_kbps_for_advanced(analysis)
        estimated_mb = self._automatic_estimated_output_mb(analysis)
        source_width, source_height = self._source_resolution()
        target_width = getattr(analysis, "suggested_width", None) or getattr(analysis, "width", None) or source_width
        target_height = getattr(analysis, "suggested_height", None) or getattr(analysis, "height", None) or source_height

        # Aplicar no Avançado deve preencher os controles visíveis, não apenas
        # trocar a aba. Para isso, o payload é normalizado para Priorizar bitrate:
        # bitrate de vídeo no campo Taxa de bits e áudio no seletor de áudio.
        payload.update({
            "profile_mode": "advanced",
            "compression_mode": "advanced",
            "profile_label": "Avançado/Sugestão automática",
            "manual_control_type": "DIRECT_BITRATE",
            "advanced_target_mode": "DIRECT_BITRATE",
            "advanced_size_strategy": "BITRATE_PRIORITY",
            "advanced_resolution_bitrate_mode": "explicit",
        })
        if video_kbps:
            payload["advanced_target_bitrate_kbps"] = video_kbps
            payload["advanced_resolution_bitrate_kbps"] = video_kbps
        if estimated_mb:
            payload["advanced_target_size_mb"] = estimated_mb
            payload["advanced_estimated_size_mb"] = estimated_mb
        if target_width:
            payload["advanced_width"] = int(target_width)
        if target_height:
            payload["advanced_height"] = int(target_height)
        original_audio_kbps = self._source_audio_bitrate_kbps()
        audio_preserves_original = False
        try:
            audio_preserves_original = bool(audio_kbps and original_audio_kbps and int(audio_kbps) >= int(original_audio_kbps))
        except Exception:
            audio_preserves_original = False
        if audio_kbps:
            if audio_preserves_original:
                payload["advanced_audio_policy"] = "keep"
                payload["advanced_audio_bitrate_kbps"] = None
                payload["allow_audio_quality_reduction"] = False
            else:
                payload["advanced_audio_policy"] = "reduce_to_value"
                payload["advanced_audio_bitrate_kbps"] = audio_kbps
                payload["allow_audio_quality_reduction"] = True

        if video_kbps:
            self._advanced_automatic_bitrate_cap_kbps = int(video_kbps)

        previous_suppress = self._suppress_live_updates
        self._suppress_live_updates = True
        try:
            self._set_mode("advanced")
            self._set_profile_values(payload)
            self._force_automatic_values_to_advanced_controls(analysis, video_kbps, audio_kbps, estimated_mb, target_width, target_height)
        finally:
            self._suppress_live_updates = previous_suppress

        # _set_profile_values() and the live recalculation slots may clear the
        # contextual high-compression notice while controls are being populated.
        # Restore the flag after all automatic values are applied so the Advanced
        # footer explains the deliberate high-compression trade-off instead of
        # showing the generic low-bitrate warning.
        self._advanced_auto_high_compression_notice = bool(high_compression)
        self._advanced_auto_balanced_notice = not bool(high_compression)

        # Atualizar fora do modo suprimido: _update_advanced_summary() retorna
        # imediatamente quando _suppress_live_updates=True. Na rc299 isso deixava
        # o painel lateral/topo com a estimativa anterior, mesmo depois de os
        # controles receberem parte da sugestão automática.
        #
        # A aplicação automática troca de aba por _set_mode(), que não dispara
        # _on_mode_changed(). Por isso a faixa superior do Avançado (Formato de
        # saída / Tamanho desejado) precisa ser sincronizada explicitamente aqui;
        # sem isso o painel ficava visualmente diferente do fluxo manual.
        self._sync_video_output_visibility()
        self._advanced_last_driver = "target_bitrate_kbps"
        self._update_advanced_controls()
        self._update_advanced_summary()
        self._sync_profile_preview_to_context(emit_apply=False)
        self._update_advanced_summary()

    @staticmethod
    def _automatic_bps_to_kbps(value) -> int | None:
        try:
            parsed = int(round(float(value) / 1000.0))
        except Exception:
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _automatic_estimated_output_mb(analysis) -> float | None:
        try:
            value = float(getattr(analysis, "estimated_output_bytes", None)) / float(1024 * 1024)
        except Exception:
            return None
        return max(0.1, value) if value > 0 else None

    def _force_automatic_values_to_advanced_controls(self, analysis, video_kbps, audio_kbps, estimated_mb, target_width, target_height):
        blockers = []
        try:
            for widget in (
                self.advanced_target_mode,
                self.advanced_target_bitrate_kbps,
                self.advanced_resolution_bitrate_kbps,
                self.advanced_width,
                self.advanced_height,
                self.advanced_fps_policy,
                self.advanced_audio_policy,
                self.advanced_target_size_mb,
            ):
                blockers.append(QSignalBlocker(widget))

            mode_index = self.advanced_target_mode.findData("DIRECT_BITRATE")
            if mode_index >= 0:
                self.advanced_target_mode.setCurrentIndex(mode_index)
            if video_kbps:
                self._set_advanced_bitrate_values(video_kbps)
                self._advanced_last_driver = "target_bitrate_kbps"
            if target_width and target_height:
                self._set_advanced_resolution_values(int(target_width), int(target_height))
            source_fps = getattr(analysis, "fps", None)
            target_fps = getattr(analysis, "suggested_fps", None)
            try:
                source_fps_value = float(source_fps) if source_fps is not None else None
                target_fps_value = float(target_fps) if target_fps is not None else None
            except Exception:
                source_fps_value = None
                target_fps_value = None
            if target_fps_value and source_fps_value and target_fps_value < source_fps_value - 0.25:
                self.advanced_fps_policy.setCurrentData("reduce_to_value", int(round(target_fps_value)))
            else:
                self.advanced_fps_policy.setCurrentData("keep", None)
            if audio_kbps:
                original_audio_kbps = self._source_audio_bitrate_kbps()
                try:
                    preserves_original_audio = bool(original_audio_kbps and int(audio_kbps) >= int(original_audio_kbps))
                except Exception:
                    preserves_original_audio = False
                if preserves_original_audio:
                    self.advanced_audio_policy.setCurrentData("keep", None)
                else:
                    self.advanced_audio_policy.setCurrentData("reduce_to_value", int(audio_kbps))
            else:
                self.advanced_audio_policy.setCurrentData("keep", None)
            if estimated_mb:
                self.advanced_target_size_mb.setValue(estimated_mb)
                self._advanced_estimated_size_mb = estimated_mb
            total_bps = 0
            if video_kbps:
                total_bps += int(video_kbps) * 1000
            if audio_kbps:
                total_bps += int(audio_kbps) * 1000
            if total_bps > 0:
                self._advanced_estimated_bitrate_bps = total_bps
        finally:
            blockers.clear()

    def _automatic_media_info_from_context(self) -> dict:
        job = self._context_job
        if job is None:
            return {}
        return {
            "codec": getattr(job, "codec", None),
            "resolution": getattr(job, "resolution", None),
            "fps": getattr(job, "fps", None),
            "duration": getattr(job, "duration", None),
            "container": getattr(job, "container", None),
            "input_bitrate_bps": getattr(job, "input_bitrate", None),
            "audio_streams": getattr(job, "audio_streams", None),
            "audio_track_count": getattr(job, "audio_track_count", None),
            "primary_audio_channels": getattr(job, "primary_audio_channels", None),
            "primary_audio_sample_rate": getattr(job, "primary_audio_sample_rate", None),
            "source_path": getattr(job, "source_path", None),
        }

    def _format_automatic_bitrate(self, value) -> str:
        try:
            parsed = int(float(value))
        except Exception:
            return "?"
        if parsed <= 0:
            return "?"
        try:
            return _format_bitrate(parsed)
        except Exception:
            return f"{max(1, int(round(parsed / 1000.0)))} kbps"

    def _format_automatic_fps(self, value) -> str:
        try:
            parsed = float(value)
        except Exception:
            return "?"
        if parsed <= 0:
            return "?"
        rounded = int(round(parsed))
        return f"{rounded} fps" if abs(parsed - rounded) < 0.05 else f"{parsed:.2f} fps"

    def _refresh_automatic_analysis_display(self):
        return

    def set_locked(self, locked: bool, reason: str | None = None):
        self._locked = bool(locked)
        self._update_mode_availability()
        widgets = [
            *self._quick_level_buttons,
            *self.smart_strategy_buttons.values(),
            *self.smart_intensity_sliders.values(),
            *self.smart_intensity_minus_buttons.values(),
            *self.smart_intensity_plus_buttons.values(),
            self.smart_audio_track_picker,
            self.smart_audio_keep_channels,
            self.smart_audio_downmix,
            self.smart_audio_mono,
            self.smart_audio_keep_original,
            self.advanced_target_mode,
            self.advanced_target_size_mb,
            self.advanced_target_bitrate_kbps,
            self.advanced_resolution_bitrate_mode,
            self.advanced_resolution_bitrate_kbps,
            self.advanced_width,
            self.advanced_height,
            self.audio_output_format,
            self.audio_bitrate,
            self.audio_quality_mode,
            self.audio_channel_policy,
            self.audio_volume_normalization,
            self.audio_subtitle_selector,
            self.audio_extract_subtitle_button,
            self.video_output_format,
            self.reduce_audio_quality,
            self.btn_analyze_media,
            self.btn_defaults,
            self.btn_apply,
        ]
        if hasattr(self, "audio_track_selector"):
            widgets.append(self.audio_track_selector)
        for widget in widgets:
            widget.setEnabled(not self._locked)
            if reason:
                widget.setToolTip(reason if self._locked else "")
            else:
                widget.setToolTip("")
        self._sync_smart_intensity_sliders()
        self._sync_video_output_visibility()
        if not self._locked:
            self.btn_close.setToolTip("Fechar")
            self._restore_audio_help_tooltips()
            if hasattr(self, "advanced_target_size_mb"):
                self._sync_advanced_target_size_bounds()
        self._sync_analyze_media_button_state()
    def _on_mode_changed(self, mode_id: int):
        previous_mode = str(getattr(self._context_job, "profile_mode", "") or "").strip().lower() if self._context_job is not None else ""
        self.mode_stack.setCurrentIndex(mode_id)
        current_mode = self._current_mode_name()
        # Troca de perfil precisa publicar o preview completo, não apenas o
        # profile_mode. Se emitirmos job_updated antes de collect_values(), o card
        # redesenha com estimativa antiga de outro perfil e o Avançado parece não
        # obedecer ao tamanho desejado.
        if current_mode == "advanced":
            if previous_mode != "advanced":
                self._prepare_advanced_preview_on_entry()
            else:
                self._recalculate_advanced_targets_from_driver(self._advanced_last_driver or "target_size_mb")
        elif current_mode == "smart":
            self._update_smart_summary(allow_heavy_estimate=False)
        elif current_mode == "audio":
            self._refresh_audio_profile_context()
        self._sync_video_output_visibility()
        self._sync_analyze_media_button_state()
        # Switching tabs should update the in-memory preview state, but must not
        # auto-apply/close the overlay. Actual apply remains bound to the Apply button.
        self._sync_profile_preview_to_context(emit_apply=False)
        if current_mode == "advanced":
            self._update_advanced_summary()
    def _install_inline_combo_border_style(self, combo: QComboBox, border_color: QColor):
        existing_proxy = getattr(combo, "_inline_border_proxy_style", None)
        if isinstance(existing_proxy, _InlineComboBorderProxyStyle):
            existing_proxy.set_border_color(border_color)
            combo.update()
            return
        base_style = getattr(combo, "_inline_border_base_style", None)
        if base_style is None:
            base_style = combo.style()
            combo._inline_border_base_style = base_style
        combo._inline_border_proxy_style = _InlineComboBorderProxyStyle(base_style, border_color)
        combo.setStyle(combo._inline_border_proxy_style)
    def _apply_theme_styles(self):
        tokens = build_theme_tokens(self.palette())
        button_stylesheet = build_button_stylesheet(
            tokens,
            min_height=28,
            border_radius=4,
            horizontal_padding=10,
        ).replace("QPushButton", "QFrame#ConfigurationPanelWidget QPushButton")
        text_primary = tokens.text_primary.name()
        text_secondary = tokens.text_secondary.name()
        muted_hint_color = QColor(tokens.text_secondary)
        muted_hint_color.setAlpha(190)
        muted_hint = muted_hint_color.name(QColor.NameFormat.HexArgb)
        content_bg = tokens.surface_card.name()
        content_border = tokens.border_card.name()
        section_border = tokens.border_panel.name()
        # Keep configuration section frames thin in every theme.
        section_border_width = "1px"
        # Keep the overlay close button visually aligned with the adjacent
        # standard action buttons. Its hover must use the same button family,
        # border strength and radius as "Restaurar padrões" / "Aplicar a todos".
        close_button_bg = tokens.button_bg.name(QColor.NameFormat.HexArgb)
        close_button_border = tokens.button_border.name(QColor.NameFormat.HexArgb)
        close_button_hover_bg = tokens.button_hover_bg.name(QColor.NameFormat.HexArgb)
        close_button_hover_border = tokens.button_hover_border.name(QColor.NameFormat.HexArgb)
        close_button_pressed_bg = tokens.button_pressed_bg.name(QColor.NameFormat.HexArgb)
        close_button_pressed_border = tokens.button_pressed_border.name(QColor.NameFormat.HexArgb)
        close_button_text = tokens.text_primary.name()
        close_button_font_size = 13
        close_button_font_weight = QFont.Weight.DemiBold
        advanced_metric_divider = "rgba(0, 0, 0, 0.050)"
        checkbox_text_color = QColor(tokens.text_primary)
        checkbox_indicator_bg = QColor(tokens.button_bg)
        checkbox_indicator_border = QColor(tokens.button_border)
        checkbox_checked_bg = QColor(tokens.quick_quality_button_checked_bg)
        checkbox_checked_border = QColor(tokens.quick_quality_button_checked_border)
        checkbox_check_color = QColor(tokens.text_on_dark_surface)
        is_dark_theme = QColor(tokens.surface_main).lightness() <= 128
        if is_dark_theme:
            content_border = tokens.configuration_section_border.name(QColor.NameFormat.HexArgb)
            section_border = tokens.configuration_section_border.name(QColor.NameFormat.HexArgb)
            section_border_width = "1px"
            close_button_bg = tokens.button_bg.name(QColor.NameFormat.HexArgb)
            close_button_border = tokens.button_border.name(QColor.NameFormat.HexArgb)
            close_button_hover_bg = tokens.button_hover_bg.name(QColor.NameFormat.HexArgb)
            close_button_hover_border = tokens.button_hover_border.name(QColor.NameFormat.HexArgb)
            close_button_pressed_bg = tokens.button_pressed_bg.name(QColor.NameFormat.HexArgb)
            close_button_pressed_border = tokens.button_pressed_border.name(QColor.NameFormat.HexArgb)
            close_button_text = tokens.text_primary.name(QColor.NameFormat.HexArgb)
            close_button_font_size = 13
            close_button_font_weight = QFont.Weight.DemiBold
            advanced_metric_divider = tokens.configuration_section_border_subtle.name(QColor.NameFormat.HexArgb)
            checkbox_indicator_bg = QColor(tokens.button_bg)
            checkbox_indicator_border = QColor(tokens.configuration_section_border)
            checkbox_checked_bg = QColor(tokens.quick_quality_button_checked_bg)
            checkbox_checked_border = QColor(tokens.quick_quality_button_checked_border)
            checkbox_check_color = QColor(tokens.text_on_dark_surface)
        subtitle_action_bg = "#f7f7f7" if is_dark_theme else "#f2f4f7"
        subtitle_action_hover_bg = "#ffffff" if is_dark_theme else "#e9edf2"
        subtitle_action_border = "#b7bbc1" if is_dark_theme else "#c4cad2"
        subtitle_action_disabled_bg = "#f5f5f5" if is_dark_theme else "#eef1f4"
        subtitle_action_disabled_border = "#d2d5d9" if is_dark_theme else "#cfd4db"
        placeholder_bg = tokens.surface_panel.name()
        # Use one combo geometry in both palettes. The theme only supplies
        # colors; the field, arrow column, divider and chevron are identical.
        combo_border = (
            QColor(tokens.button_border)
            if is_dark_theme
            else QColor(tokens.button_border).lighter(118)
        ).name()
        combo_divider = (
            QColor(tokens.button_border).lighter(108)
            if is_dark_theme
            else QColor(tokens.button_border).lighter(128)
        ).name()
        # Match the configuration content surface in both themes. The border
        # and arrow divider define the control; it must not become a darker
        # filled block in the dark palette.
        combo_background = content_bg
        combo_arrow_bg = combo_background
        chevron_name = "media_preview_chevron_down_light.svg" if is_dark_theme else "media_preview_chevron_down_dark.svg"
        chevron_path = (Path(__file__).resolve().parent / "icons" / chevron_name).as_posix()
        inline_combo_styles = f"""
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo {{
                background: {combo_background};
                color: {text_primary};
                border: 1px solid {combo_border};
                border-radius: 5px;
                min-height: 28px;
                padding: 0px 32px 0px 10px;
            }}
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo:hover {{
                background: {tokens.button_hover_bg.name()};
                border-color: {tokens.button_hover_border.name()};
            }}
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo:on,
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo:focus {{
                background: {combo_background};
                border-color: {tokens.button_hover_border.name()};
            }}
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 28px;
                background: {combo_arrow_bg};
                border: none;
                border-left: 1px solid {combo_divider};
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }}
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo::down-arrow {{
                image: url({chevron_path});
                width: 10px;
                height: 10px;
            }}
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo:disabled {{
                background: {combo_background};
                border-color: {combo_border};
                color: {text_secondary};
            }}
            """
        shell_bg = tokens.overlay_shell_bg.name()
        active_tab_bg = tokens.surface_card.name()
        inactive_tab_bg = tokens.surface_list.name()
        divider = tokens.overlay_border_strong.name()
        quick_checked_fg = tokens.text_on_dark_surface.name()
        quick_warning_fg = tokens.status_error.name()
        quick_aggressive_bg = tokens.quick_aggressive_button_bg.name()
        quick_aggressive_fg = tokens.quick_aggressive_button_fg.name()
        quick_aggressive_border = tokens.quick_aggressive_button_border.name()
        quick_aggressive_hover_bg = tokens.quick_aggressive_button_hover_bg.name()
        quick_aggressive_hover_fg = tokens.quick_aggressive_button_hover_fg.name()
        quick_aggressive_checked_bg = tokens.quick_aggressive_button_checked_bg.name()
        quick_aggressive_checked_fg = tokens.quick_aggressive_button_checked_fg.name()
        quick_aggressive_checked_border = tokens.quick_aggressive_button_checked_border.name()

        smart_card_checked_text = QColor(Qt.GlobalColor.white).name()
        smart_palette_source_tokens = tokens
        app = QApplication.instance()
        if app is not None:
            try:
                smart_palette_source_tokens = build_theme_tokens(
                    _dark_palette(_standard_palette(app)),
                    use_application_palette=False,
                )
            except Exception:
                smart_palette_source_tokens = tokens
        smart_quality_checked_bg_color = QColor(smart_palette_source_tokens.quick_quality_button_checked_bg)
        smart_quality_checked_border_color = QColor(smart_palette_source_tokens.quick_quality_button_checked_border)
        smart_balanced_checked_bg_color = QColor(smart_palette_source_tokens.quick_balanced_button_checked_bg)
        smart_balanced_checked_border_color = QColor(smart_palette_source_tokens.quick_balanced_button_checked_border)
        smart_aggressive_checked_bg_color = QColor(smart_palette_source_tokens.quick_aggressive_button_checked_bg)
        smart_aggressive_checked_border_color = QColor(smart_palette_source_tokens.quick_aggressive_button_checked_border)
        # Strategic selected cards must match the approved dark-theme appearance
        # in every theme. Use dark-theme checked tokens as the reference palette,
        # then apply the same controlled darkening that defines the current dark UI.
        smart_quality_checked_bg_color = smart_quality_checked_bg_color.darker(118)
        smart_quality_checked_border_color = smart_quality_checked_border_color.darker(116)
        smart_balanced_checked_bg_color = smart_balanced_checked_bg_color.darker(145)
        smart_balanced_checked_border_color = smart_balanced_checked_border_color.darker(136)
        smart_aggressive_checked_bg_color = smart_aggressive_checked_bg_color.darker(145)
        smart_aggressive_checked_border_color = smart_aggressive_checked_border_color.darker(136)
        smart_quality_checked_bg = smart_quality_checked_bg_color.name()
        smart_quality_checked_border = smart_quality_checked_border_color.name()
        smart_balanced_checked_bg = smart_balanced_checked_bg_color.name()
        smart_balanced_checked_border = smart_balanced_checked_border_color.name()
        smart_aggressive_checked_bg = smart_aggressive_checked_bg_color.name()
        smart_aggressive_checked_border = smart_aggressive_checked_border_color.name()
        self._smart_selected_text_color = smart_card_checked_text
        self._smart_selected_card_styles = {
            "quality": (
                f"background: {smart_quality_checked_bg}; "
                f"border: 2px solid {smart_quality_checked_border}; "
                "border-radius: 10px;"
            ),
            "balanced": (
                f"background: {smart_balanced_checked_bg}; "
                f"border: 2px solid {smart_balanced_checked_border}; "
                "border-radius: 10px;"
            ),
            "aggressive": (
                f"background: {smart_aggressive_checked_bg}; "
                f"border: 2px solid {smart_aggressive_checked_border}; "
                "border-radius: 10px;"
            ),
        }
        quick_scale_bg = tokens.quick_scale_button_bg.name()
        quick_scale_fg = tokens.quick_scale_button_fg.name()
        quick_scale_border = tokens.quick_scale_button_border.name()
        quick_scale_hover_bg = tokens.quick_scale_button_hover_bg.name()
        quick_scale_hover_fg = tokens.quick_scale_button_hover_fg.name()
        quick_scale_checked_bg = tokens.quick_scale_button_checked_bg.name()
        quick_scale_checked_border = tokens.quick_scale_button_checked_border.name()

        def _subtle_extreme_color(color: QColor, alpha: int = 222) -> QColor:
            softened = QColor(color)
            softened.setAlpha(alpha)
            return softened

        quick_left_extreme_bg = _subtle_extreme_color(tokens.quick_quality_button_checked_bg)
        quick_left_extreme_hover_bg = QColor(tokens.quick_quality_button_checked_bg)
        quick_left_extreme_border = _subtle_extreme_color(tokens.quick_quality_button_checked_border, 230)
        quick_right_extreme_bg = _subtle_extreme_color(tokens.quick_aggressive_button_checked_bg)
        quick_right_extreme_hover_bg = QColor(tokens.quick_aggressive_button_checked_bg)
        quick_right_extreme_border = _subtle_extreme_color(tokens.quick_aggressive_button_checked_border, 230)
        quick_left_extreme_text_color = QColor(Qt.GlobalColor.white)
        quick_right_extreme_text_color = QColor(Qt.GlobalColor.white)
        quick_aggressive_hint_color = QColor(tokens.quick_aggressive_button_checked_border)
        if is_dark_theme:
            quick_aggressive_hint_color = quick_aggressive_hint_color.lighter(170)

        if hasattr(self, "btn_close") and hasattr(self.btn_close, "set_x_style"):
            self.btn_close.set_x_style(
                QColor(close_button_text),
                font_size=close_button_font_size,
                font_weight=close_button_font_weight,
            )
        if hasattr(self, "audio_volume_segmented"):
            self.audio_volume_segmented.set_visual_tokens(
                background=tokens.button_bg,
                border=tokens.border_card,
                divider=tokens.overlay_border_strong,
                selected_bg=tokens.quick_balanced_button_checked_bg,
                text=tokens.text_primary,
                selected_text=tokens.text_on_dark_surface,
            )
        if hasattr(self, "smart_strategy_buttons"):
            tone_colors = {
                "quality": (
                    tokens.quick_quality_button_fg.name(),
                    smart_card_checked_text,
                ),
                "balanced": (
                    tokens.quick_balanced_button_fg.name(),
                    smart_card_checked_text,
                ),
                "aggressive": (
                    quick_aggressive_fg,
                    smart_card_checked_text,
                ),
            }
            for button in self.smart_strategy_buttons.values():
                tone = str(button.property("strategyTone") or "balanced")
                normal_color, checked_color = tone_colors.get(
                    tone,
                    (
                        tokens.quick_balanced_button_fg.name(),
                        tokens.quick_balanced_button_checked_fg.name(),
                    ),
                )
                button.setProperty("normalTextColor", normal_color)
                button.setProperty("checkedTextColor", checked_color)
                button.update()
        if hasattr(self, "quick_left_hint") and hasattr(self.quick_left_hint, "set_visual_colors"):
            self.quick_left_hint.set_visual_colors(
                background=quick_left_extreme_bg,
                hover_background=quick_left_extreme_hover_bg,
                pressed_background=tokens.quick_quality_button_hover_bg,
                border=quick_left_extreme_border,
                text=quick_left_extreme_text_color,
            )
        if hasattr(self, "quick_right_hint") and hasattr(self.quick_right_hint, "set_visual_colors"):
            self.quick_right_hint.set_visual_colors(
                background=quick_right_extreme_bg,
                hover_background=quick_right_extreme_hover_bg,
                pressed_background=tokens.quick_aggressive_button_hover_bg,
                border=quick_right_extreme_border,
                text=quick_right_extreme_text_color,
            )
        self.setStyleSheet(
            button_stylesheet
            + "\n"
            + f"""
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationModeBar {{
                background: transparent;
                border: none;
                padding: 2px 2px 0 2px;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationMutedHint {{
                color: {muted_hint};
                font-size: 11px;
                line-height: 1.25;
                padding-top: 1px;
            }}
            QFrame#ConfigurationPanelWidget QToolButton {{
                color: {text_secondary};
                background: {inactive_tab_bg};
                border: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                min-width: 96px;
                min-height: 30px;
                padding: 0 14px;
                margin-top: 6px;
                margin-bottom: 0px;
            }}
            QFrame#ConfigurationPanelWidget QToolButton:hover {{
                color: {text_primary};
                background: {active_tab_bg};
            }}
            QFrame#ConfigurationPanelWidget QToolButton:checked {{
                color: {text_primary};
                background: {content_bg};
                border: none;
                font-weight: 600;
                margin-top: 2px;
                margin-bottom: -1px;
                padding-top: 1px;
            }}
            QFrame#ConfigurationPanelWidget QToolButton#ConfigurationIconActionButton {{
                background: {subtitle_action_bg};
                border: 1px solid {subtitle_action_border};
                border-radius: 6px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0px;
                margin: 0px;
            }}
            QFrame#ConfigurationPanelWidget QToolButton#ConfigurationIconActionButton:hover:!disabled {{
                background: {subtitle_action_hover_bg};
                border-color: {tokens.button_hover_border.name()};
            }}
            QFrame#ConfigurationPanelWidget QToolButton#ConfigurationIconActionButton:disabled {{
                background: {subtitle_action_disabled_bg};
                border-color: {subtitle_action_disabled_border};
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard {{
                min-height: 194px;
                max-height: 194px;
                border-radius: 10px;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="quality"] {{
                background: {tokens.quick_quality_button_bg.name()};
                border: 1px solid {tokens.quick_quality_button_border.name()};
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="quality"][active="true"] {{
                background: {smart_quality_checked_bg};
                border: 2px solid {smart_quality_checked_border};
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="balanced"] {{
                background: {tokens.quick_balanced_button_bg.name()};
                border: 1px solid {tokens.quick_balanced_button_border.name()};
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="balanced"][active="true"] {{
                background: {smart_balanced_checked_bg};
                border: 2px solid {smart_balanced_checked_border};
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="aggressive"] {{
                background: {quick_aggressive_bg};
                border: 1px solid {quick_aggressive_border};
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="aggressive"][active="true"] {{
                background: {smart_aggressive_checked_bg};
                border: 2px solid {smart_aggressive_checked_border};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStrategyCardButton {{
                min-height: 66px;
                max-height: 66px;
                padding: 0px 8px;
                border: none;
                background: transparent;
                font-size: 12px;
                font-weight: 700;
                text-align: center;
                line-height: 1.25;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStrategyCardButton[strategyTone="quality"] {{
                color: {tokens.quick_quality_button_fg.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStrategyCardButton[strategyTone="quality"]:checked {{
                color: {tokens.quick_quality_button_checked_fg.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStrategyCardButton[strategyTone="balanced"] {{
                color: {tokens.quick_balanced_button_fg.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStrategyCardButton[strategyTone="balanced"]:checked {{
                color: {tokens.quick_balanced_button_checked_fg.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStrategyCardButton[strategyTone="aggressive"] {{
                color: {quick_aggressive_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStrategyCardButton[strategyTone="aggressive"]:checked {{
                color: {quick_aggressive_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationSmartCardControlsTitle {{
                color: {text_primary};
                font-weight: 650;
                font-size: 12px;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationSmartCardIntensityLabel {{
                color: {text_primary};
                font-weight: 600;
                font-size: 11px;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="quality"][active="true"] QLabel#ConfigurationSmartCardControlsTitle,
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="quality"][active="true"] QLabel#ConfigurationSmartCardIntensityLabel,
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="quality"][active="true"] QPushButton#ConfigurationSmartStepButton {{
                color: {smart_card_checked_text};
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="balanced"][active="true"] QLabel#ConfigurationSmartCardControlsTitle,
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="balanced"][active="true"] QLabel#ConfigurationSmartCardIntensityLabel,
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="balanced"][active="true"] QPushButton#ConfigurationSmartStepButton {{
                color: {smart_card_checked_text};
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="aggressive"][active="true"] QLabel#ConfigurationSmartCardControlsTitle,
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="aggressive"][active="true"] QLabel#ConfigurationSmartCardIntensityLabel,
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategyCard[strategyTone="aggressive"][active="true"] QPushButton#ConfigurationSmartStepButton {{
                color: {smart_card_checked_text};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStepButton {{
                min-width: 18px;
                max-width: 18px;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
                border: none;
                background: transparent;
                color: {text_primary};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStepButton:hover {{
                background: transparent;
                border: none;
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStepButton:pressed {{
                background: transparent;
                border: none;
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStepButton:disabled {{
                background: transparent;
                color: {text_secondary};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationSmartCardControlsTitle[active="true"] {{
                color: {quick_checked_fg};
                font-weight: 750;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationSmartCardIntensityLabel[active="true"],
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSmartStepButton[active="true"] {{
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton {{
                min-height: 40px;
                padding: 0 12px;
                font-weight: 600;
                border-radius: 0px;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[segmentPosition="left"] {{
                border-top-left-radius: 6px;
                border-bottom-left-radius: 6px;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[segmentPosition="right"] {{
                border-top-right-radius: 6px;
                border-bottom-right-radius: 6px;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[segmentPosition="single"] {{
                border-radius: 6px;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[segmentPosition="middle"],
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[segmentPosition="right"] {{
                margin-left: -1px;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[smartAudioChannel="true"] {{
                min-width: 82px;
                max-width: 82px;
                min-height: 28px;
                max-height: 28px;
                padding: 0 10px;
                border: 1px solid {tokens.button_border.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[smartAudioChannel="true"]:hover,
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[smartAudioChannel="true"]:pressed,
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[smartAudioChannel="true"]:focus {{
                border: 1px solid {tokens.button_border.name()};
                padding: 0 10px;
                color: {text_primary};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[smartAudioChannel="true"]:checked,
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[smartAudioChannel="true"]:checked:hover,
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[smartAudioChannel="true"]:checked:pressed,
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton[smartAudioChannel="true"]:checked:focus {{
                background: {tokens.quick_balanced_button_checked_bg.name()};
                border: 1px solid {tokens.quick_balanced_button_checked_border.name()};
                color: {quick_checked_fg};
                padding: 0 10px;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton:hover {{
                color: {text_primary};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationSegmentButton:checked {{
                background: {tokens.quick_balanced_button_checked_bg.name()};
                border-color: {tokens.quick_balanced_button_checked_border.name()};
                color: {quick_checked_fg};
            }}
            {inline_combo_styles}
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo,
            QFrame#ConfigurationPanelWidget QSpinBox,
            QFrame#ConfigurationPanelWidget QDoubleSpinBox {{
                min-height: 28px;
            }}
            QFrame#ConfigurationPanelWidget QComboBox#ConfigurationInlineCombo QLineEdit {{
                border: none;
                background: transparent;
                padding: 0px;
                margin: 0px;
                selection-background-color: transparent;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationPanelAnalyzeButton,
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationPanelDefaultsButton,
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationPanelApplyButton {{
                min-height: 28px;
            }}
            QFrame#ConfigurationPanelWidget QStackedWidget#ConfigurationModeStack {{
                background: {shell_bg};
                border: none;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationContentFrame {{
                background: {content_bg};
                border: none;
                border-radius: 4px;
            }}
            QFrame#ConfigurationPanelWidget QToolButton#ConfigurationPanelCloseButton {{
                background: {close_button_bg};
                border: 1px solid {close_button_border};
                border-radius: 4px;
                min-width: 28px;
                max-width: 28px;
                min-height: 28px;
                max-height: 28px;
                padding: 0px;
                margin-top: 0px;
                margin-bottom: 0px;
            }}
            QFrame#ConfigurationPanelWidget QToolButton#ConfigurationPanelCloseButton:hover {{
                background: {close_button_hover_bg};
                border: 2px solid {close_button_hover_border};
                border-radius: 4px;
            }}
            QFrame#ConfigurationPanelWidget QToolButton#ConfigurationPanelCloseButton:pressed {{
                background: {close_button_pressed_bg};
                border: 2px solid {close_button_pressed_border};
                border-radius: 4px;
            }}
            QFrame#ConfigurationPanelWidget QCheckBox {{
                background: transparent;
                color: {text_primary};
                spacing: 7px;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSection {{
                background: {content_bg};
                border: {section_border_width} solid {content_border};
                border-radius: 4px;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationSmartStrategySection {{
                background: transparent;
                border: none;
                border-radius: 0px;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationAdvancedOutputCard {{
                background: rgba(0, 0, 0, 0.040);
                border: none;
                border-radius: 8px;
            }}
            QFrame#ConfigurationPanelWidget QWidget#ConfigurationStrategyContextRow {{
                background: rgba(0, 0, 0, 0.028);
                border: 1px solid rgba(0, 0, 0, 0.085);
                border-radius: 6px;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationStrategyCard {{
                background: rgba(0, 0, 0, 0.020);
                border: 1px solid rgba(0, 0, 0, 0.110);
                border-radius: 6px;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationStrategyCard[activeStrategy="true"] {{
                background: rgba(214, 166, 0, 0.105);
                border: 1px solid rgba(214, 166, 0, 0.520);
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationStrategyCardTitleButton {{
                background: transparent;
                border: none;
                color: {text_primary};
                font-weight: 700;
                padding: 0px;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationStrategyCardTitleButton:hover {{
                background: rgba(0, 0, 0, 0.030);
                border: none;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationStrategyCardTitleButton:checked {{
                background: transparent;
                border: none;
                color: {text_primary};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationStrategyCardCaption {{
                color: {text_secondary};
                font-size: 12px;
                font-weight: 400;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationResolutionAxisLabel {{
                color: {text_secondary};
                font-weight: 600;
            }}
            QFrame#ConfigurationPanelWidget QSpinBox#ConfigurationResolutionSpinBox {{
                min-height: 28px;
                max-height: 28px;
                padding-left: 6px;
                padding-right: 2px;
            }}
            QFrame#ConfigurationPanelWidget QWidget[withBottomDivider="true"] {{
                border-bottom: 1px solid {advanced_metric_divider};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationAdvancedOutputTitle {{
                color: {text_primary};
                font-size: 12px;
                font-weight: 400;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationAdvancedOutputMetricLabel {{
                color: {text_secondary};
                font-size: 12px;
                font-weight: 400;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationAdvancedOutputMetricValue {{
                color: {text_primary};
                font-size: 12px;
                font-weight: 500;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationSectionTitle,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationPlaceholderTitle,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickPresetName {{
                color: {text_primary};
                font-weight: 600;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationFormLabel {{
                color: {text_primary};
                font-weight: 500;
            }}
            QFrame#ConfigurationPanelWidget QToolButton#ConfigurationHelpIcon {{
                background: transparent;
                border: 1px solid {tokens.border_card.name()};
                border-radius: 7px;
                color: {text_secondary};
                font-size: 9px;
                font-weight: 700;
                padding: 0px;
                margin: 0px;
                min-width: 14px;
                max-width: 14px;
                min-height: 14px;
                max-height: 14px;
            }}
            QFrame#ConfigurationPanelWidget QToolButton#ConfigurationHelpIcon:hover {{
                background: {tokens.button_hover_bg.name()};
                border: 1px solid {tokens.button_hover_border.name()};
                color: {text_primary};
            }}
            QFrame#ConfigurationPanelWidget QWidget#ConfigurationPrimaryTargetRow {{
                background: rgba(0, 0, 0, 0.060);
                border: none;
                border-radius: 0px;
            }}
            QFrame#ConfigurationPanelWidget QWidget#ConfigurationPrimaryTargetRow QLabel#ConfigurationFormLabel {{
                font-weight: 600;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickInstruction {{
                color: {text_primary};
                font-size: 15px;
                font-weight: 600;
                qproperty-alignment: AlignHCenter;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickPresetLine {{
                color: {text_secondary};
                font-size: 14px;
                font-weight: 500;
                qproperty-alignment: AlignHCenter;
                margin-top: 2px;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationPreviewLabel {{
                qproperty-alignment: AlignHCenter;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationPlaceholderBody,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationPlaceholderHint,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationPreviewLabel {{
                color: {text_secondary};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationAdvancedQualityWarning,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationAdvancedSummaryLabel {{
                color: {text_primary};
                background: transparent;
                border: none;
                border-radius: 0px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 500;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationPlaceholderCard {{
                background: rgba(0, 0, 0, 0.055);
                border: 1px solid {section_border};
                border-radius: 4px;
            }}
            QFrame#ConfigurationPanelWidget QFrame#ConfigurationQuickLevelsRow {{
                background: transparent;
                border: none;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickQualityDropWarning {{
                color: {quick_warning_fg};
                background: transparent;
                border: none;
                padding: 0px 8px;
                font-size: 12px;
                font-weight: 700;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton {{
                min-width: 37px;
                max-width: 37px;
                min-height: 42px;
                max-height: 42px;
                padding: 0;
                font-weight: 600;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint {{
                min-width: 72px;
                max-width: 72px;
                min-height: 44px;
                max-height: 44px;
                padding: 0px 4px;
                background: transparent;
                border: 1px solid {content_border};
                border-radius: 6px;
                color: {text_secondary};
                font-size: 11px;
                font-weight: 600;
                text-align: center;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint:hover {{
                background: rgba(0, 0, 0, 0.035);
                border-color: {divider};
                color: {text_primary};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint:pressed {{
                background: rgba(0, 0, 0, 0.070);
                border-color: {divider};
                color: {text_primary};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint[qualityExtreme="true"] {{
                background: {tokens.quick_quality_button_checked_bg.name()};
                border: 1px solid {tokens.quick_quality_button_checked_border.name()};
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint[qualityExtreme="true"]:hover {{
                background: {tokens.quick_quality_button_checked_bg.name()};
                border: 2px solid {tokens.quick_quality_button_checked_border.name()};
                color: {quick_checked_fg};
                font-weight: 700;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint[qualityExtreme="true"]:pressed {{
                background: {tokens.quick_quality_button_hover_bg.name()};
                border: 2px solid {tokens.quick_quality_button_checked_border.name()};
                color: {quick_checked_fg};
                font-weight: 700;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint[aggressiveExtreme="true"] {{
                background: {quick_aggressive_checked_bg};
                border: 1px solid {quick_aggressive_checked_border};
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint[aggressiveExtreme="true"]:hover {{
                background: {quick_aggressive_checked_bg};
                border: 2px solid {quick_aggressive_checked_border};
                color: {quick_checked_fg};
                font-weight: 700;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickExtremeHint[aggressiveExtreme="true"]:pressed {{
                background: {quick_aggressive_hover_bg};
                border: 2px solid {quick_aggressive_checked_border};
                color: {quick_checked_fg};
                font-weight: 700;
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[qualityLevel="true"] {{
                background: {tokens.quick_quality_button_bg.name()};
                border: 1px solid {tokens.quick_quality_button_border.name()};
                color: {tokens.quick_quality_button_fg.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[qualityLevel="true"]:hover {{
                background: {tokens.quick_quality_button_hover_bg.name()};
                color: {tokens.quick_quality_button_hover_fg.name()};
                border-color: {tokens.quick_quality_button_border.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[qualityLevel="true"]:checked {{
                background: {tokens.quick_quality_button_checked_bg.name()};
                border-color: {tokens.quick_quality_button_checked_border.name()};
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[balancedLevel="true"] {{
                background: {tokens.quick_balanced_button_bg.name()};
                border: 1px solid {tokens.quick_balanced_button_border.name()};
                color: {tokens.quick_balanced_button_fg.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[balancedLevel="true"]:hover {{
                background: {tokens.quick_balanced_button_hover_bg.name()};
                color: {tokens.quick_balanced_button_hover_fg.name()};
                border-color: {tokens.quick_balanced_button_border.name()};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[balancedLevel="true"]:checked {{
                background: {tokens.quick_balanced_button_checked_bg.name()};
                border-color: {tokens.quick_balanced_button_checked_border.name()};
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[scaleLevel="true"] {{
                background: {quick_scale_bg};
                border: 1px solid {quick_scale_border};
                color: {quick_scale_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[scaleLevel="true"]:hover {{
                background: {quick_scale_hover_bg};
                color: {quick_scale_hover_fg};
                border-color: {quick_scale_border};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[scaleLevel="true"]:checked {{
                background: {quick_scale_checked_bg};
                border-color: {quick_scale_checked_border};
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[aggressiveLevel="true"] {{
                background: {quick_aggressive_bg};
                border: 1px solid {quick_aggressive_border};
                color: {quick_aggressive_fg};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[aggressiveLevel="true"]:hover {{
                background: {quick_aggressive_hover_bg};
                color: {quick_aggressive_hover_fg};
                border-color: {quick_aggressive_border};
            }}
            QFrame#ConfigurationPanelWidget QPushButton#ConfigurationQuickLevelButton[aggressiveLevel="true"]:checked {{
                background: {quick_aggressive_checked_bg};
                border-color: {quick_aggressive_checked_border};
                color: {quick_checked_fg};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickQualityHint,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickBalancedHint,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickScaleHint,
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickAggressiveHint {{
                font-size: 11px;
                font-weight: 600;
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickQualityHint {{
                color: {tokens.quick_quality_hint_text.name()};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickBalancedHint {{
                color: {tokens.quick_balanced_hint_text.name()};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickScaleHint {{
                color: {quick_scale_border};
            }}
            QFrame#ConfigurationPanelWidget QLabel#ConfigurationQuickAggressiveHint {{
                color: {quick_aggressive_hint_color.name()};
            }}
            """
        )
        # The combo popup is a separate QListView window, so it does not
        # inherit the panel selector above. Style its selected row directly
        # to avoid the Windows accent stripe; selection is a neutral fill in
        # both palettes.
        combo_popup_selection = QColor("#d6d6d6")
        if is_dark_theme:
            combo_popup_selection = QColor("#5a5d63")
        # Keep popup windows visually separate from the panel without turning
        # them into a second, competing surface.  The popup surface is visibly
        # distinct while the border remains its primary delimiter.
        combo_popup_background = QColor(content_bg).darker(118 if is_dark_theme else 110)
        combo_popup_selection_name = combo_popup_selection.name()
        combo_popup_stylesheet = f"""
            QListView {{
                background: {combo_popup_background.name()};
                border: 1px solid {combo_border};
                outline: 0px;
                selection-background-color: {combo_popup_selection_name};
                selection-color: {text_primary};
            }}
            QListView::item {{
                border: none;
                min-height: 21px;
                padding: 0px 8px;
            }}
            QListView::item:selected,
            QListView::item:selected:active,
            QListView::item:selected:!active {{
                background: {combo_popup_selection};
                color: {text_primary};
                border: none;
            }}
        """
        # The multi-select audio-track menu has checkbox rows.  Give only that
        # popup enough vertical room for its checkbox, leaving the compact single-
        # selection popups at their calibrated 21 px height.
        audio_track_checkbox_border = QColor("#a4acb8") if is_dark_theme else QColor("#5d6570")
        audio_track_popup_stylesheet = combo_popup_stylesheet + """
            QListView::item {
                min-height: 29px;
            }
        """ + f"""
            QListView::indicator:unchecked {{
                width: 14px;
                height: 14px;
                background: {combo_popup_background.name()};
                border: 1px solid {checkbox_indicator_border.name()};
                border-radius: 5px;
            }}
        """
        for combo in self.findChildren(QComboBox, "ConfigurationInlineCombo"):
            popup_view = combo.view()
            if popup_view is not None:
                popup_palette = popup_view.palette()
                popup_palette.setColor(QPalette.ColorRole.Highlight, combo_popup_selection)
                popup_palette.setColor(QPalette.ColorRole.HighlightedText, QColor(text_primary))
                popup_view.setPalette(popup_palette)
                if isinstance(combo, _ConfigurationComboBox):
                    popup_delegate = getattr(popup_view, "_neutral_combo_popup_delegate", None)
                    if isinstance(popup_delegate, _NeutralComboPopupDelegate):
                        popup_delegate.set_selection(
                            combo_popup_selection,
                            QColor(text_primary),
                            combo.currentIndex(),
                        )
                    else:
                        popup_delegate = _NeutralComboPopupDelegate(
                            combo_popup_selection,
                            QColor(text_primary),
                            combo.currentIndex(),
                            popup_view,
                        )
                        popup_view._neutral_combo_popup_delegate = popup_delegate
                        popup_view.setItemDelegate(popup_delegate)
                        popup_view.viewport().installEventFilter(popup_delegate)
                        popup_view.viewport().setMouseTracking(True)
                elif combo is getattr(self, "audio_track_selector", None):
                    popup_delegate = getattr(popup_view, "_audio_track_popup_delegate", None)
                    if isinstance(popup_delegate, _AudioTrackPopupDelegate):
                        popup_delegate.set_visual_tokens(combo_popup_background, audio_track_checkbox_border)
                    else:
                        popup_delegate = _AudioTrackPopupDelegate(
                            combo_popup_background,
                            audio_track_checkbox_border,
                            popup_view,
                        )
                        popup_view._audio_track_popup_delegate = popup_delegate
                        popup_view.setItemDelegate(popup_delegate)
                popup_view.setStyleSheet(
                    audio_track_popup_stylesheet
                    if combo is getattr(self, "audio_track_selector", None)
                    else combo_popup_stylesheet
                )
        self.advanced_context_status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.advanced_context_status.setStyleSheet(f"color: {text_secondary};")
        for checkbox in (
            getattr(self, "reduce_audio_quality", None),
            getattr(self, "smart_audio_keep_original", None),
        ):
            if hasattr(checkbox, "set_theme_colors"):
                checkbox.set_theme_colors(
                    text_color=checkbox_text_color,
                    indicator_bg=checkbox_indicator_bg,
                    indicator_border=checkbox_indicator_border,
                    checked_bg=checkbox_checked_bg,
                    checked_border=checkbox_checked_border,
                    check_color=checkbox_check_color,
                )
                checkbox.style().unpolish(checkbox)
                checkbox.style().polish(checkbox)
                checkbox.update()
        self._sync_mode_bar_action_button_widths()
    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            self._apply_theme_styles()
    def _quick_process_text(self, index: int, fallback: str) -> str:
        return {
            0: "Qualidade máxima, compressão mínima",
            1: "Compressão leve, perda quase imperceptível",
            2: "Redução discreta com boa qualidade",
            3: "Redução moderada com boa qualidade",
            4: "Reduz bitrate mantendo resolução/FPS",
            5: "Reduz bitrate com boa legibilidade",
            6: "Redução maior de bitrate para uso geral",
            7: "Arquivo menor com perda controlada",
            8: "Reduz resolução mantendo FPS",
            9: "Reduz resolução para economizar espaço",
            10: "Maior redução de resolução",
            11: "Redução forte de resolução",
            12: "⚠ Menor tamanho com perda visual alta",
            13: "⚠ Menor tamanho com forte perda visual",
            14: "⚠ Menor tamanho com grande queda de qualidade",
            15: "⚠ Menor tamanho com queda extrema de qualidade",
        }.get(index, fallback)

    def _update_quick_selection_text_only(self):
        index = self._quick_preset_index
        title, summary = self.QUICK_PRESETS[index]
        process_text = self._quick_process_text(index, summary)
        title_html = html.escape(title)
        process_html = html.escape(process_text)
        self.quick_selected_line.setText(
            f'<span style="font-weight:600;">{title_html}</span> — '
            f'<span style="font-weight:400;">{process_html}</span>'
        )
        if getattr(self, "quick_quality_drop_warning", None) is not None:
            self.quick_quality_drop_warning.setVisible(False)

    def _update_quick_preset_labels(self):
        index = self._quick_preset_index
        title, summary = self.QUICK_PRESETS[index]
        self._update_quick_selection_text_only()
        visual_quality = {
            0: "máxima",
            1: "muito alta",
            2: "alta",
            3: "boa",
            4: "equilibrada",
            5: "moderada e controlada",
            6: "mais reduzida",
            7: "reduzida",
            8: "redução leve de resolução",
            9: "redução moderada de resolução",
            10: "redução forte de resolução",
            11: "redução muito forte de resolução",
            12: "redução leve de resolução/FPS",
            13: "redução moderada de resolução/FPS",
            14: "redução forte de resolução/FPS",
            15: "máxima compressão",
        }.get(index, "equilibrada")
        impact = {
            0: "preservação visual máxima",
            1: "redução leve com foco em qualidade",
            2: "redução moderada com boa preservação visual",
            3: "boa redução com manutenção visual consistente",
            4: "redução de bitrate mantendo resolução/FPS",
            5: "redução de bitrate com boa legibilidade",
            6: "reduz mais bitrate para uso geral",
            7: "redução intensa de bitrate",
            8: "redução leve de resolução",
            9: "redução moderada de resolução",
            10: "redução forte de resolução",
            11: "redução muito forte de resolução",
            12: "redução leve de resolução/FPS",
            13: "redução moderada de resolução/FPS",
            14: "redução forte de resolução/FPS",
            15: "máxima compressão e menor tamanho",
        }.get(index, summary)
        job = self._context_job
        if job is None:
            self.quick_result_summary.setText(
                "<div style='line-height:125%;'>"
                f"<b>Modo rápido:</b> {index + 1}/{len(self.QUICK_PRESETS)}"
                "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                "<b>Resultado estimado:</b> —"
                "<br>"
                "<b>Resolução</b>: —"
                "&nbsp;&nbsp;|&nbsp;&nbsp;"
                "<b>FPS</b>: —"
                "&nbsp;&nbsp;|&nbsp;&nbsp;"
                "<b>Bitrate</b>: —"
                "</div>"
            )
            return
        allow_heavy = (not self._suppress_live_updates) and (self._current_mode_name() == "quick")
        estimated_size_bytes, estimated_output_bitrate, output_resolution, output_fps = self._quick_preview_metrics(allow_heavy=allow_heavy)
        input_size = _format_bytes(self._source_size_bytes())
        output_size = _format_bytes(estimated_size_bytes)
        input_resolution = str(getattr(job, "resolution", "?"))
        input_fps = str(getattr(job, "fps", "?"))
        input_bitrate = _format_bitrate(self._source_input_bitrate_bps())
        output_bitrate = _format_bitrate(estimated_output_bitrate)
        size_line = _summary_pair("Resultado estimado", input_size, output_size)
        detail_parts = [
            _summary_pair("Resolução", input_resolution, output_resolution, bold_label=True),
            _summary_pair("FPS", input_fps, output_fps, bold_label=True),
            _summary_pair("Bitrate", input_bitrate, output_bitrate, bold_label=True),
        ]
        self.quick_result_summary.setText(
            "<div style='line-height:125%;'>"
            f"<b>Modo rápido:</b> {index + 1}/{len(self.QUICK_PRESETS)}"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
            f"{size_line}"
            "<br>"
            f"{'&nbsp;&nbsp;|&nbsp;&nbsp;'.join(detail_parts)}"
            "</div>"
        )
    def _selected_smart_strategy(self) -> str:
        for strategy, button in self.smart_strategy_buttons.items():
            if button.isChecked():
                return strategy
        return default_smart_profile()["smart_strategy"]
    def _select_smart_strategy_card(self, strategy: str, event=None):
        if self._locked:
            if event is not None:
                event.ignore()
            return
        button = self.smart_strategy_buttons.get(strategy)
        if button is not None:
            button.setChecked(True)
        if event is not None:
            event.accept()
    def _set_smart_strategy(self, strategy: str):
        target = self.smart_strategy_buttons.get(strategy) or self.smart_strategy_buttons[default_smart_profile()["smart_strategy"]]
        target.setChecked(True)
        self._sync_smart_intensity_sliders()
    def _reset_smart_profile_ui_defaults(self, defaults: dict | None = None):
        defaults = defaults or default_smart_profile()
        try:
            default_intensity = int(round(float(defaults.get("slider_value", 5.0)) * 10))
        except Exception:
            default_intensity = 50
        default_intensity = max(0, min(100, default_intensity))
        for strategy in SMART_STRATEGIES:
            self._smart_intensity_values[strategy] = default_intensity
        default_strategy = str(defaults.get("smart_strategy") or defaults.get("strategy_type") or default_smart_profile()["smart_strategy"])
        self._set_smart_strategy(default_strategy)
        if getattr(self, "smart_audio_keep_original", None) is not None:
            previous = self.smart_audio_keep_original.blockSignals(True)
            self.smart_audio_keep_original.setChecked(True)
            self.smart_audio_keep_original.blockSignals(previous)
        self._smart_audio_track_mode = "all"
        if getattr(self, "smart_audio_keep_channels", None) is not None:
            previous = self.smart_audio_keep_channels.blockSignals(True)
            self.smart_audio_keep_channels.setChecked(True)
            self.smart_audio_keep_channels.blockSignals(previous)
        if getattr(self, "smart_audio_downmix", None) is not None:
            previous = self.smart_audio_downmix.blockSignals(True)
            self.smart_audio_downmix.setChecked(False)
            self.smart_audio_downmix.blockSignals(previous)
        if getattr(self, "smart_audio_mono", None) is not None:
            previous = self.smart_audio_mono.blockSignals(True)
            self.smart_audio_mono.setChecked(False)
            self.smart_audio_mono.blockSignals(previous)
        self._refresh_smart_audio_context()
        self._update_smart_audio_controls()
    def _on_smart_strategy_toggled(self, strategy: str, checked: bool):
        if checked:
            self._sync_smart_intensity_sliders()
            self._schedule_smart_summary_update()
    def _on_smart_intensity_slider_changed(self, strategy: str, value: int):
        # Backward-compatible hook retained for older signal connections.
        clamped_value = max(0, min(100, int(value)))
        self._smart_intensity_values[strategy] = clamped_value
        intensity_label = self.smart_intensity_value_labels.get(strategy)
        if intensity_label is not None:
            display_value = int(round(clamped_value / 10.0))
            intensity_label.setText(
                f"Intensidade: {_describe_intensity(display_value)} ({_format_intensity(display_value)})"
            )
        if strategy == self._selected_smart_strategy():
            self._schedule_smart_summary_update()
    def _step_smart_intensity(self, strategy: str, delta: int):
        if self._locked or strategy != self._selected_smart_strategy():
            return
        slider = self.smart_intensity_sliders.get(strategy)
        if slider is None:
            return
        next_value = max(slider.minimum(), min(slider.maximum(), slider.value() + int(delta)))
        slider.setValue(next_value)
        self._update_smart_summary()
    def _sync_smart_intensity_sliders(self):
        selected = self._selected_smart_strategy()
        active = not self._locked
        for strategy, slider in self.smart_intensity_sliders.items():
            stored_value = max(0, min(100, int(self._smart_intensity_values.get(strategy, slider.value()))))
            previous = slider.blockSignals(True)
            slider.setValue(stored_value)
            slider.blockSignals(previous)
            is_selected = strategy == selected
            slider.setEnabled(active and is_selected)
            slider.setVisible(is_selected)
            minus_button = self.smart_intensity_minus_buttons.get(strategy)
            plus_button = self.smart_intensity_plus_buttons.get(strategy)
            controls = self.smart_intensity_controls.get(strategy)
            intensity_label = self.smart_intensity_value_labels.get(strategy)
            card = self.smart_strategy_cards.get(strategy)
            header_button = self.smart_strategy_buttons.get(strategy)
            if minus_button is not None:
                minus_button.setEnabled(active and is_selected)
                minus_button.setVisible(is_selected)
            if plus_button is not None:
                plus_button.setEnabled(active and is_selected)
                plus_button.setVisible(is_selected)
            if controls is not None:
                controls.setVisible(is_selected)
            controls_title = self.smart_intensity_title_labels.get(strategy)
            for state_widget in (controls_title, intensity_label, minus_button, plus_button):
                if state_widget is not None:
                    state_widget.setProperty("active", is_selected)
                    state_widget.style().unpolish(state_widget)
                    state_widget.style().polish(state_widget)
            if intensity_label is not None:
                display_value = int(round(stored_value / 10.0))
                intensity_label.setText(f"Intensidade: {_describe_intensity(display_value)} ({_format_intensity(display_value)})")
            selected_text_color = getattr(self, "_smart_selected_text_color", QColor(Qt.GlobalColor.white).name())
            selected_card_styles = getattr(self, "_smart_selected_card_styles", {})
            active_text_style = f"color: {selected_text_color};"
            for widget in (card, header_button):
                if widget is not None:
                    widget.setProperty("active", is_selected)
                    if widget is card:
                        widget.setStyleSheet(selected_card_styles.get(strategy, "") if is_selected else "")
                    if widget is header_button:
                        widget.setProperty("forcedTextColor", selected_text_color if is_selected else "")
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
                    widget.update()
            for text_widget in (controls_title, intensity_label, minus_button, plus_button):
                if text_widget is not None:
                    text_widget.setStyleSheet(active_text_style if is_selected else "")
                    text_widget.update()
    def _slider_value(self) -> int:
        selected = self._selected_smart_strategy()
        value = self._smart_intensity_values.get(selected, 0)
        return max(0, min(10, int(round(value / 10.0))))
    def _effective_smart_intensity(self) -> int:
        return self._slider_value()
    def _current_audio_track_selection(self) -> tuple[str, int | None]:
        data = self.smart_audio_track_picker.currentData() if self.smart_audio_track_picker.count() > 0 else None
        if isinstance(data, tuple) and len(data) == 2:
            policy, track_id = data
            return str(policy), track_id if isinstance(track_id, int) else None
        return "KEEP_ALL", None
    def _on_smart_audio_track_picker_changed(self, index: int):
        self._reset_cancelled_smart_audio_context_on_control_change()
        policy, _ = self._current_audio_track_selection()
        self._smart_audio_track_mode = "selected" if policy == "SELECTED_ONLY" and index >= 1 else "all"
        self._update_smart_audio_controls()
        self._schedule_smart_summary_update()
    def _current_audio_track_policy(self) -> str:
        if self._is_smart_audio_keep_original():
            return "KEEP_ALL"
        policy, _ = self._current_audio_track_selection()
        return policy
    def _current_audio_channel_policy(self) -> str:
        if getattr(self, "smart_audio_keep_original", None) is not None and self.smart_audio_keep_original.isChecked():
            return "KEEP_ORIGINAL"
        if self.smart_audio_mono.isChecked():
            return "DOWNMIX_TO_MONO"
        if self.smart_audio_downmix.isChecked():
            return "DOWNMIX_TO_STEREO"
        return "KEEP_ORIGINAL"
    def _is_smart_audio_keep_original(self) -> bool:
        checkbox = getattr(self, "smart_audio_keep_original", None)
        return bool(checkbox is None or checkbox.isChecked())
    def _reset_cancelled_smart_audio_context_on_control_change(self) -> None:
        job = self._context_job
        if (
            job is None
            or self._suppress_live_updates
            or self._current_mode_name() != "smart"
            or not self._is_reprocessable_terminal_status(job)
        ):
            return
        # Some Strategic audio controls, especially "Manter original", can
        # legitimately change the UI state without changing the derived encode
        # values when the current selection remains KEEP_ALL/KEEP_ORIGINAL.
        # A user-triggered change must still invalidate CANCELADO so the card
        # returns to the primary action immediately.
        setattr(job, "status", "READY")
        setattr(job, "progress", 0)
        setattr(job, "error", None)
        event_bridge.emit("job_updated", {"job": job})
    def _on_smart_audio_keep_original_toggled(self, checked: bool):
        self._reset_cancelled_smart_audio_context_on_control_change()
        self._update_smart_audio_controls()
        self._update_smart_summary(allow_heavy_estimate=False)
        self._schedule_smart_summary_update()
    def _format_smart_audio_meta(self, streams: list[dict], track_count: int, primary_channels: int) -> str:
        default_stream = next((stream for stream in streams if bool(stream.get("is_default"))), streams[0] if streams else None)
        codec = "AAC"
        if default_stream is not None:
            codec = str(
                default_stream.get("codec_label")
                or default_stream.get("codec_name")
                or default_stream.get("codec")
                or codec
            ).strip() or codec
        codec = codec.upper()
        track_context = f"{track_count} trilha" + ("s" if track_count != 1 else "")
        if primary_channels <= 1:
            layout_context = "mono"
        elif primary_channels == 2:
            layout_context = "estéreo"
        else:
            layout_context = "multicanal"
        channel_context = f"{primary_channels} canal" if primary_channels == 1 else f"{primary_channels} canais"
        return f"{track_context} • {codec} • {layout_context} • {channel_context}"
    def _smart_audio_context(self) -> tuple[list[dict], int, int]:
        job = self._context_job
        streams = list(getattr(job, "audio_streams", []) or []) if job is not None else []
        track_count = int(getattr(job, "audio_track_count", len(streams)) or len(streams)) if job is not None else 0
        primary_channels = int(getattr(job, "primary_audio_channels", 2) or 2) if job is not None else 2
        if not streams and track_count > 0:
            streams = [{"id": idx, "label": f"Trilha {idx + 1}", "channels": 2, "is_default": idx == 0} for idx in range(track_count)]
        return streams, track_count, primary_channels
    def _refresh_smart_audio_context(self):
        streams, track_count, primary_channels = self._smart_audio_context()
        show_track_policy = track_count > 1
        show_channel_policy = primary_channels > 2
        track_context = f"{track_count} trilha" + ("s" if track_count != 1 else "")
        channel_context = "multicanal" if show_channel_policy else ("estéreo" if primary_channels == 2 else "mono")
        self.smart_audio_meta.setText(self._format_smart_audio_meta(streams, track_count, primary_channels))
        self.smart_audio_section.setVisible(True)
        if not show_channel_policy:
            self.smart_audio_keep_channels.setChecked(True)
        self.smart_audio_track_controls.setVisible(True)
        self.smart_audio_track_label.setEnabled(True)
        previous_policy, previous_track_id = self._current_audio_track_selection()
        self.smart_audio_track_picker.blockSignals(True)
        self.smart_audio_track_picker.clear()
        self.smart_audio_track_picker.addItem("Todas", ("KEEP_ALL", None))
        default_stream = next((stream for stream in streams if bool(stream.get("is_default"))), streams[0] if streams else None)
        default_track_id = int(default_stream.get("id", 0)) if default_stream is not None else None
        seen_track_ids: set[int] = set()
        for stream in streams:
            track_id = int(stream.get("id", 0))
            if track_id in seen_track_ids:
                continue
            seen_track_ids.add(track_id)
            track_label = str(stream.get("label") or f"Trilha {track_id + 1}")
            self.smart_audio_track_picker.addItem(track_label, ("SELECTED_ONLY", track_id))
        target_index = 0
        target_track_id = previous_track_id if previous_policy == "SELECTED_ONLY" else None
        if show_track_policy and self._smart_audio_track_mode == "default" and default_track_id is not None:
            target_track_id = default_track_id
        if show_track_policy and target_track_id is not None:
            for idx in range(self.smart_audio_track_picker.count()):
                item_data = self.smart_audio_track_picker.itemData(idx)
                if isinstance(item_data, tuple) and len(item_data) == 2 and item_data == ("SELECTED_ONLY", target_track_id):
                    target_index = idx
                    break
        self.smart_audio_track_picker.setCurrentIndex(target_index)
        self.smart_audio_track_picker.blockSignals(False)
        self._smart_audio_track_mode = "selected" if target_index > 0 else "all"
        self.smart_audio_channel_controls.setVisible(True)
        self.smart_audio_channel_label.setEnabled(True)
        self._update_smart_audio_controls()
    def _build_smart_preview_job(self):
        base = vars(self._context_job).copy() if self._context_job is not None else {}
        _, selected_track_id = self._current_audio_track_selection()
        base.update({
            "profile_mode": "smart",
            "control_mode": "STRATEGY",
            "strategy_type": self._selected_smart_strategy(),
            "slider_value": self._slider_value(),
            "smart_strategy": self._selected_smart_strategy(),
            "smart_intensity": self._effective_smart_intensity(),
            "audio_track_policy": self._current_audio_track_policy(),
            "selected_track_id": selected_track_id if self._current_audio_track_policy() == "SELECTED_ONLY" else None,
            "audio_channel_policy": self._current_audio_channel_policy(),
            "allow_audio_quality_reduction": self._audio_quality_reduction_allowed(),
        })
        return SimpleNamespace(**base)
    def _update_smart_audio_controls(self):
        manual_enabled = (not self._locked) and (not self._is_smart_audio_keep_original())
        combo_enabled = self.smart_audio_track_picker.count() > 1 and manual_enabled
        _, _, primary_channels = self._smart_audio_context()
        primary_channels = int(primary_channels or 0)

        self.smart_audio_controls_row.setEnabled(manual_enabled)
        self.smart_audio_track_controls.setEnabled(manual_enabled)
        self.smart_audio_channel_controls.setEnabled(manual_enabled)
        self.smart_audio_track_label.setEnabled(manual_enabled)
        self.smart_audio_channel_label.setEnabled(manual_enabled)
        self.smart_audio_track_picker.setEnabled(combo_enabled)

        keep_channels_enabled = manual_enabled and primary_channels > 0
        stereo_enabled = manual_enabled and primary_channels > 2
        mono_enabled = manual_enabled and primary_channels > 1
        self.smart_audio_keep_channels.setEnabled(keep_channels_enabled)
        self.smart_audio_downmix.setEnabled(stereo_enabled)
        self.smart_audio_mono.setEnabled(mono_enabled)

        if self.smart_audio_downmix.isChecked() and not stereo_enabled:
            self.smart_audio_keep_channels.setChecked(True)
        if self.smart_audio_mono.isChecked() and not mono_enabled:
            self.smart_audio_keep_channels.setChecked(True)

        if getattr(self, "smart_audio_controls_effect", None) is not None:
            self.smart_audio_controls_effect.setOpacity(1.0 if manual_enabled else 0.45)
    def _schedule_smart_summary_update(self):
        slider_value = self._slider_value()
        self._reset_cancelled_context_if_configuration_values_changed()
        if self._suppress_live_updates:
            self._update_smart_summary(allow_heavy_estimate=False)
            return
        if self._smart_summary_timer.isActive():
            self._smart_summary_timer.stop()
        self._smart_summary_timer.start()
    def _refresh_smart_summary(self):
        if self._suppress_live_updates:
            self._update_smart_summary(allow_heavy_estimate=False)
            return
        if not self._locked and self._context_job is not None and self._current_mode_name() == "smart":
            self.smartProfileAutoApplyRequested.emit(self.collect_values())
        self._update_smart_summary(allow_heavy_estimate=True)
    def _display_smart_strategy(self, strategy: str) -> str:
        return SMART_STRATEGY_DISPLAY_LABELS.get(strategy, strategy)
    def _update_smart_summary(self, *, allow_heavy_estimate: bool = True):
        strategy = self._selected_smart_strategy()
        display_strategy = self._display_smart_strategy(strategy)
        slider_value = self._slider_value()
        summary = {
            "Economia Máxima": "ajuste fino da redução de escala.",
            "Equilíbrio": "ajuste fino da otimização do arquivo.",
            "Qualidade Prioritária": "ajuste fino da preservação visual.",
        }.get(strategy, "Ajuste fino da estratégia selecionada.")
        estimated_size = None
        estimated_bitrate = None
        if self._context_job is not None and self._current_mode_name() == "smart":
            estimated_size = getattr(self._context_job, "estimated_size_bytes", None)
            estimated_bitrate = getattr(self._context_job, "estimated_output_bitrate", None)
        if estimated_size is None and estimated_bitrate is None and allow_heavy_estimate:
            preview_job = self._build_smart_preview_job()
            estimated_size = estimate_size_for_profile(preview_job, estimate_size_crf)
            estimated_bitrate = estimate_output_bitrate_for_profile(preview_job, estimated_size)
        audio_policy_text = {
            "KEEP_ALL": "todas as trilhas",
            "KEEP_DEFAULT_ONLY": "trilha principal",
            "SELECTED_ONLY": f"{self.smart_audio_track_picker.currentText() or 'trilha específica'}",
        }[self._current_audio_track_policy()]
        channel_policy_text = {
            "KEEP_ORIGINAL": "canais originais",
            "DOWNMIX_TO_STEREO": "estéreo",
            "DOWNMIX_TO_MONO": "mono",
        }[self._current_audio_channel_policy()]
        html = (
            f"<div style='text-align:center;'>"
            f"<b>{display_strategy}</b> — {summary} "
            f"Intensidade: {_describe_intensity(slider_value)} ({_format_intensity(slider_value)})"
            f"</div>"
        )
        self.smart_summary.setText(html)
    def _source_resolution(self) -> tuple[int | None, int | None]:
        job = self._context_job
        parsed = parse_resolution(getattr(job, "resolution", None) if job is not None else None)
        return parsed if parsed is not None else (None, None)
    def _source_size_bytes(self):
        job = self._context_job
        if job is None:
            return None
        candidates = [
            getattr(job, "source_size", None),
            getattr(job, "source_size_bytes", None),
            getattr(job, "input_size_bytes", None),
            getattr(job, "original_size_bytes", None),
            getattr(job, "file_size", None),
            getattr(job, "size_bytes", None),
        ]
        for value in candidates:
            try:
                value = float(value)
            except Exception:
                continue
            if value > 0:
                return int(round(value))
        try:
            source_size_bytes = resolve_job_source_size_bytes(job)
        except Exception:
            source_size_bytes = None
        try:
            return int(round(float(source_size_bytes))) if source_size_bytes is not None else None
        except Exception:
            return None
    def _source_size_limit_mb(self) -> float | None:
        source_size_bytes = self._source_size_bytes()
        if source_size_bytes is None:
            return None
        # O perfil Avançado aceita frações de MB para ajuste fino do alvo.
        # O teto usa o tamanho real da entrada, sem arredondar para cima.
        source_size_mb = float(source_size_bytes) / float(1024 * 1024)
        return max(0.1, round(source_size_mb, 2))
    def _source_size_display_text(self) -> str | None:
        source_size_bytes = self._source_size_bytes()
        formatted = _format_bytes(source_size_bytes)
        if formatted:
            return formatted
        size_limit_mb = self._source_size_limit_mb()
        if size_limit_mb is None:
            return None
        return f"{size_limit_mb} MB"
    def _sync_advanced_target_size_bounds(self):
        max_size_mb = self._source_size_limit_mb()
        if max_size_mb is None:
            self.advanced_target_size_mb.setMaximum(102400.0)
            self.advanced_target_size_mb.setToolTip("")
            return
        self.advanced_target_size_mb.setMaximum(max_size_mb)
        if self.advanced_target_size_mb.value() > max_size_mb:
            self.advanced_target_size_mb.blockSignals(True)
            self.advanced_target_size_mb.setValue(max_size_mb)
            self.advanced_target_size_mb.blockSignals(False)
        limit_text = self._source_size_display_text() or _format_bytes(max_size_mb * 1024 * 1024) or f"{_format_mb_value(max_size_mb)} MB"
        self.advanced_target_size_mb.setToolTip(f"Máximo permitido: {limit_text} (tamanho da entrada).")
    def _prepare_advanced_preview_on_entry(self):
        if not hasattr(self, "advanced_target_size_mb"):
            return
        # Ao entrar no Avançado, preservar o tamanho desejado já visível no
        # controle. Só normalizar limites. Não semear novamente pelo tamanho da
        # entrada, porque isso sobrescreve o alvo escolhido e mantém o card com
        # estimativa de outro perfil.
        self._sync_advanced_target_size_bounds()
        current_value = max(0.1, float(self.advanced_target_size_mb.value()))
        bounded_value = max(0.1, min(self._advanced_target_size_limit(), current_value))
        if bounded_value != current_value:
            with QSignalBlocker(self.advanced_target_size_mb):
                self.advanced_target_size_mb.setValue(bounded_value)
        self._advanced_last_driver = "target_size_mb"
        self._recalculate_advanced_targets_from_driver("target_size_mb")
    def _resolved_advanced_resolution(self) -> tuple[int | None, int | None]:
        source_width, source_height = self._source_resolution()
        width = int(self.advanced_width.value()) if self.advanced_width.value() > 0 else None
        height = int(self.advanced_height.value()) if self.advanced_height.value() > 0 else None
        if source_width and source_height:
            width = min(width or source_width, source_width)
            height = min(height or source_height, source_height)
        if width is not None and width % 2:
            width = max(2, width - 1)
        if height is not None and height % 2:
            height = max(2, height - 1)
        return width, height
    def _sync_advanced_resolution_bounds(self):
        source_width, source_height = self._source_resolution()
        max_width = max(2, source_width or 16384)
        max_height = max(2, source_height or 16384)
        self.advanced_width.setMaximum(max_width)
        self.advanced_height.setMaximum(max_height)
        if source_width and self.advanced_width.value() <= 0:
            self.advanced_width.setValue(source_width)
        elif source_width and self.advanced_width.value() > source_width:
            self.advanced_width.setValue(source_width)
        if source_height and self.advanced_height.value() <= 0:
            self.advanced_height.setValue(source_height)
        elif source_height and self.advanced_height.value() > source_height:
            self.advanced_height.setValue(source_height)
        if source_width and source_height:
            source_resolution_text = f"{source_width}x{source_height} px"
            current_width = self.advanced_width.value()
            current_height = self.advanced_height.value()
            if current_width and current_height and (int(current_width) < int(source_width) or int(current_height) < int(source_height)):
                self.advanced_resolution_meta.setText(
                    f"A saída preserva a proporção original e reduz a resolução em relação à mídia de origem ({source_resolution_text})."
                )
            else:
                self.advanced_resolution_meta.setText(
                    f"A saída preserva a proporção original e não excede a mídia de origem ({source_resolution_text})."
                )
        else:
            self.advanced_resolution_meta.setText("A saída preserva a proporção original e respeita os limites da mídia de origem.")
    def _apply_advanced_resolution_sync(self, *, changed: str):
        if self._advanced_resolution_sync:
            return
        source_width, source_height = self._source_resolution()
        if not (source_width and source_height):
            self._update_advanced_summary()
            return
        self._advanced_resolution_sync = True
        try:
            if changed == "width":
                width = min(max(2, int(self.advanced_width.value())), source_width)
                height = max(2, int(round((width / source_width) * source_height)))
                if height % 2:
                    height = max(2, height - 1)
                self.advanced_height.setValue(min(height, source_height))
            else:
                height = min(max(2, int(self.advanced_height.value())), source_height)
                width = max(2, int(round((height / source_height) * source_width)))
                if width % 2:
                    width = max(2, width - 1)
                self.advanced_width.setValue(min(width, source_width))
        finally:
            self._advanced_resolution_sync = False
        self._update_advanced_summary()
    def _on_advanced_width_changed(self, value: int):
        self._apply_advanced_resolution_sync(changed="width")
        if self._advanced_resolution_sync or self._advanced_sync_in_progress:
            return
        self._advanced_last_resolution_driver = "width"
        self._advanced_last_driver = "target_resolution"
        self._recalculate_advanced_targets_from_driver("target_resolution")
    def _on_advanced_height_changed(self, value: int):
        self._apply_advanced_resolution_sync(changed="height")
        if self._advanced_resolution_sync or self._advanced_sync_in_progress:
            return
        self._advanced_last_resolution_driver = "height"
        self._advanced_last_driver = "target_resolution"
        self._recalculate_advanced_targets_from_driver("target_resolution")
    def _build_advanced_preview_job(self):
        base = vars(self._context_job).copy() if self._context_job is not None else {}
        manual_type = self.advanced_target_mode.currentData() or "TARGET_SIZE"
        width, height = self._resolved_advanced_resolution()
        desired_size_mb = max(0.1, float(self.advanced_target_size_mb.value()))
        base.update({
            "profile_mode": "advanced",
            "control_mode": "MANUAL_LEVEL",
            "manual_control_type": manual_type,
            "advanced_target_mode": manual_type,
            "advanced_size_strategy": {
                "DIRECT_BITRATE": "BITRATE_PRIORITY",
                "TARGET_SIZE": "BALANCED",
                "RESOLUTION_DRIVEN": "RESOLUTION_PRIORITY",
            }.get(manual_type, "BALANCED"),
            "advanced_target_mode_name": "size",
            "advanced_target_size_mb": desired_size_mb,
            "advanced_estimated_size_mb": desired_size_mb,
            "advanced_target_bitrate_kbps": max(1, int(self.advanced_target_bitrate_kbps.value())),
            "advanced_width": width,
            "advanced_height": height,
            "advanced_resolution_lock": True,
            "advanced_resolution_bitrate_mode": self.advanced_resolution_bitrate_mode.currentData() or "auto",
            "advanced_resolution_bitrate_kbps": max(1, int(self.advanced_resolution_bitrate_kbps.value())),
            "advanced_fps_policy": self.advanced_fps_policy.currentData() or "keep",
            "advanced_fps_value": self.advanced_fps_policy.currentValue(),
            "advanced_audio_policy": self.advanced_audio_policy.currentData() or "keep",
            "advanced_audio_bitrate_kbps": self.advanced_audio_policy.currentValue(),
            "allow_audio_quality_reduction": True,
            "audio_track_policy": getattr(self._context_job, "audio_track_policy", "KEEP_ALL") if self._context_job is not None else "KEEP_ALL",
            "selected_track_id": getattr(self._context_job, "selected_track_id", None) if self._context_job is not None else None,
            "audio_channel_policy": getattr(self._context_job, "audio_channel_policy", "KEEP_ORIGINAL") if self._context_job is not None else "KEEP_ORIGINAL",
            "quick_profile_preset": None,
            "strategy_type": None,
            "smart_strategy": None,
            "slider_value": None,
            "smart_intensity": None,
            "allow_audio_quality_reduction": self._audio_quality_reduction_allowed(),
        })
        return SimpleNamespace(**base)
    def _build_quick_preview_job(self):
        base = vars(self._context_job).copy() if self._context_job is not None else {}
        base.update({
            "profile_mode": "quick",
            "profile_label": f"Rápido/{quick_preset_title(self._quick_preset_index)}",
            "quick_profile_preset": self._quick_preset_index,
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
        return SimpleNamespace(**base)
    def _source_input_bitrate_bps(self) -> int | None:
        job = self._context_job
        if job is None:
            return None
        for attr in ("input_bitrate", "source_bitrate", "bitrate", "video_bitrate"):
            value = getattr(job, attr, None)
            try:
                value = float(value)
            except Exception:
                continue
            if value > 0:
                return int(round(value))
        return None
    def _quick_preview_cache_signature(self):
        job = self._context_job
        if job is None:
            return None
        attrs = (
            "source_path",
            "source_size",
            "duration_seconds",
            "duration",
            "resolution",
            "fps",
            "input_bitrate",
            "source_bitrate",
            "bitrate",
            "video_bitrate",
            "estimated_size_bytes",
            "estimated_output_bitrate",
            "output_size_bytes",
            "output_bitrate",
            "allow_audio_quality_reduction",
        )
        return (self._quick_preset_index,) + tuple(getattr(job, attr, None) for attr in attrs)
    def _quick_lightweight_preview_metrics(self):
        """Preview imediato do Rápido sem executar estimativa CRF por amostra.
        A troca de aba precisa ser instantânea. A estimativa pesada do Rápido
        chama FFmpeg para codificar uma amostra do vídeo; isso é adequado para
        refinamentos posteriores, mas não para o clique de navegação entre
        perfis. Esta prévia usa a regra mínima de ganho já consolidada do
        perfil Rápido, sem reaproveitar métricas antigas do job.
        """
        job = self._context_job
        if job is None:
            return None, None, None, None
        preview_job = self._build_quick_preview_job()
        source_size_bytes = self._source_size_bytes()
        estimated_size_bytes = None
        if source_size_bytes is not None and source_size_bytes > 1:
            try:
                gain_ratio = float(quick_profile.minimum_gain_ratio(preview_job))
            except Exception:
                gain_ratio = 0.08
            required_reduction = max(
                1,
                int(round(source_size_bytes * max(0.0, gain_ratio))),
                min(128 * 1024, max(1, source_size_bytes - 1)),
            )
            estimated_size_bytes = max(1, min(source_size_bytes - required_reduction, source_size_bytes - 1))
        try:
            estimated_output_bitrate = estimate_output_bitrate_for_profile(preview_job, estimated_size_bytes)
        except Exception:
            estimated_output_bitrate = None
        try:
            floor_bitrate = quick_minimum_total_bitrate_floor_for_profile(preview_job)
        except Exception:
            floor_bitrate = None
        if floor_bitrate is not None and floor_bitrate > 0:
            if self._audio_quality_reduction_allowed():
                # With explicit permission to reduce audio quality, the quick
                # preview must reflect the compact runtime floor instead of
                # keeping the safer audio-preserving estimate.
                estimated_output_bitrate = int(floor_bitrate)
            elif estimated_output_bitrate is None or int(estimated_output_bitrate) < int(floor_bitrate):
                estimated_output_bitrate = int(floor_bitrate)
        try:
            output_resolution, output_fps = estimate_output_video_traits(preview_job)
        except Exception:
            output_resolution = getattr(job, "resolution", None)
            output_fps = getattr(job, "fps", None)
        return estimated_size_bytes, estimated_output_bitrate, output_resolution, output_fps
    def _quick_preview_metrics(self, *, allow_heavy: bool = True):
        job = self._context_job
        if job is None:
            return None, None, None, None
        cache_key = (allow_heavy, self._quick_preview_cache_signature())
        if cache_key == self._quick_preview_cache_key:
            return self._quick_preview_cache_value
        if allow_heavy:
            preview_job = self._build_quick_preview_job()
            try:
                estimated_size_bytes = estimate_size_for_profile(preview_job, estimate_size_crf)
            except Exception:
                estimated_size_bytes = getattr(job, "estimated_size_bytes", None) or getattr(job, "output_size_bytes", None)
            try:
                estimated_output_bitrate = estimate_output_bitrate_for_profile(preview_job, estimated_size_bytes)
            except Exception:
                estimated_output_bitrate = getattr(job, "estimated_output_bitrate", None) or getattr(job, "output_bitrate", None)
            try:
                floor_bitrate = quick_minimum_total_bitrate_floor_for_profile(preview_job)
            except Exception:
                floor_bitrate = None
            if floor_bitrate is not None and floor_bitrate > 0:
                if self._audio_quality_reduction_allowed():
                    estimated_output_bitrate = int(floor_bitrate)
                elif estimated_output_bitrate is None or int(estimated_output_bitrate) < int(floor_bitrate):
                    estimated_output_bitrate = int(floor_bitrate)
            try:
                output_resolution, output_fps = estimate_output_video_traits(preview_job)
            except Exception:
                output_resolution, output_fps = estimate_output_video_traits(job)
        else:
            estimated_size_bytes = getattr(job, "estimated_size_bytes", None) or getattr(job, "output_size_bytes", None)
            estimated_output_bitrate = getattr(job, "estimated_output_bitrate", None) or getattr(job, "output_bitrate", None)
            try:
                output_resolution, output_fps = estimate_output_video_traits(job)
            except Exception:
                output_resolution = getattr(job, "resolution", None)
                output_fps = getattr(job, "fps", None)
        try:
            estimated_size_bytes = int(round(float(estimated_size_bytes))) if estimated_size_bytes is not None else None
        except Exception:
            estimated_size_bytes = None
        try:
            estimated_output_bitrate = int(round(float(estimated_output_bitrate))) if estimated_output_bitrate is not None else None
        except Exception:
            estimated_output_bitrate = None
        source_size_bytes = self._source_size_bytes()
        if source_size_bytes is not None and estimated_size_bytes is not None:
            estimated_size_bytes = min(estimated_size_bytes, source_size_bytes)
        source_input_bitrate = self._source_input_bitrate_bps()
        if source_input_bitrate is not None and estimated_output_bitrate is not None:
            estimated_output_bitrate = min(estimated_output_bitrate, source_input_bitrate)
        result = (estimated_size_bytes, estimated_output_bitrate, output_resolution, output_fps)
        self._quick_preview_cache_key = cache_key
        self._quick_preview_cache_value = result
        return result
    def _advanced_strategy_mode(self) -> str:
        return self.advanced_target_mode.currentData() or "TARGET_SIZE"
    def _advanced_target_size_limit(self) -> float:
        self._sync_advanced_target_size_bounds()
        return max(0.1, float(self.advanced_target_size_mb.maximum()))
    def _source_fps_value(self, source=None) -> float | None:
        source = source if source is not None else self._context_job
        for attr in ("fps", "input_fps", "source_fps"):
            try:
                value = getattr(source, attr, None)
            except Exception:
                value = None
            if value is None and isinstance(source, dict):
                value = source.get(attr)
            numeric = parse_fps(value)
            if numeric is not None:
                return numeric
        return None
    def _parse_bitrate_kbps(self, value, *, plain_unit: str = "kbps") -> int | None:
        """Return bitrate in kbps, handling plain numeric bps from ffprobe."""
        if value is None:
            return None
        text = str(value).strip().lower().replace(",", ".")
        if not text:
            return None
        multiplier = 1.0
        if "mbps" in text:
            multiplier = 1000.0
        elif "kbps" in text:
            multiplier = 1.0
        elif "bps" in text:
            multiplier = 1 / 1000
        elif str(plain_unit or "kbps").lower() == "bps":
            multiplier = 1 / 1000
        import re
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return None
        try:
            kbps = int(round(float(match.group(1)) * multiplier))
        except Exception:
            return None
        return kbps if kbps > 0 else None
    def _source_audio_bitrate_kbps(self, source=None) -> int | None:
        source = source if source is not None else self._context_job

        # Prefer probed stream data, matching the same values displayed in
        # "Trilhas de áudio". Cached job attributes may contain an old suggested
        # output bitrate, not the detected source-track bitrate.
        source_path = None
        for attr in ("source_path", "input_path", "path"):
            try:
                source_path = getattr(source, attr, None)
            except Exception:
                source_path = None
            if source_path is None and isinstance(source, dict):
                source_path = source.get(attr)
            if source_path:
                break
        if source_path:
            try:
                streams = advanced_profile._probe_audio_stream_details(str(source_path))
            except Exception:
                streams = tuple()
            bitrates = []
            for stream in streams or ():
                kbps = self._parse_bitrate_kbps(
                    stream.get("bit_rate") if isinstance(stream, dict) else None,
                    plain_unit="bps",
                )
                if kbps is not None:
                    bitrates.append(kbps)
            if bitrates:
                return int(round(sum(bitrates) / len(bitrates)))

        candidates = (
            "audio_bitrate",
            "input_audio_bitrate",
            "source_audio_bitrate",
            "primary_audio_bitrate",
            "audio_bitrate_kbps",
        )
        for attr in candidates:
            try:
                value = getattr(source, attr, None)
            except Exception:
                value = None
            if value is None and isinstance(source, dict):
                value = source.get(attr)
            plain_unit = "kbps" if attr.endswith("_kbps") else "bps"
            kbps = self._parse_bitrate_kbps(value, plain_unit=plain_unit)
            if kbps is not None:
                return kbps
        return None
    def _source_audio_stream_count(self, source=None) -> int | None:
        source = source if source is not None else self._context_job
        for attr in ("audio_track_count", "audio_tracks", "audio_stream_count"):
            try:
                value = getattr(source, attr, None)
            except Exception:
                value = None
            if value is None and isinstance(source, dict):
                value = source.get(attr)
            try:
                count = int(value)
            except Exception:
                continue
            return max(0, count)
        try:
            streams = getattr(source, "audio_streams", None)
        except Exception:
            streams = None
        if streams is None and isinstance(source, dict):
            streams = source.get("audio_streams")
        if streams is not None:
            try:
                return len(list(streams))
            except Exception:
                pass
        source_path = None
        for attr in ("source_path", "input_path", "path"):
            try:
                source_path = getattr(source, attr, None)
            except Exception:
                source_path = None
            if source_path is None and isinstance(source, dict):
                source_path = source.get(attr)
            if source_path:
                break
        if source_path:
            try:
                return len(advanced_profile._probe_audio_stream_details(str(source_path)))
            except Exception:
                return None
        return None
    def _sync_advanced_original_combo_options(self, source=None):
        source = source if source is not None else self._context_job
        # Restaurar padrões aplica apenas o dicionário do perfil Avançado.
        # Esse dicionário não contém metadados da mídia; nesse caminho, a
        # primeira opção dos combos deve continuar vindo do item em contexto.
        context_source = self._context_job if source is not self._context_job else None
        fps_value = self._source_fps_value(source)
        if fps_value is None and context_source is not None:
            fps_value = self._source_fps_value(context_source)
        if fps_value is not None:
            fps_label = f"{fps_value:.2f}".rstrip("0").rstrip(".") + " fps (original)"
        else:
            fps_label = "FPS original"
        self.advanced_fps_policy.setOriginalOption(fps_label, fps_value)
        audio_kbps = self._source_audio_bitrate_kbps(source)
        audio_stream_count = self._source_audio_stream_count(source)
        if context_source is not None and audio_kbps is None and audio_stream_count is None:
            audio_kbps = self._source_audio_bitrate_kbps(context_source)
            audio_stream_count = self._source_audio_stream_count(context_source)
        if audio_kbps is not None:
            audio_label = f"{audio_kbps} kbps (original)"
        elif audio_stream_count == 0:
            audio_label = "Sem áudio (original)"
        else:
            audio_label = "Áudio original"
        self.advanced_audio_policy.setOriginalOption(audio_label, audio_kbps)
    def _context_bitrate_kbps(self) -> int:
        candidates = []
        job = self._context_job
        if job is not None:
            # Source/input cap only. Do not include Advanced estimates or
            # previous outputs here, because that feeds stale values back into
            # the target-size contract.
            for attr in (
                "input_bitrate",
                "source_bitrate",
                "bitrate",
                "video_bitrate",
            ):
                candidates.append(getattr(job, attr, None))
        for value in candidates:
            try:
                value = float(value)
            except Exception:
                continue
            if value <= 0:
                continue
            if value >= 100000:
                value = value / 1000.0
            return max(1, min(50000, int(round(value))))
        return 1
    def _advanced_bitrate_cap_kbps(self) -> int:
        cap = max(1, min(50000, int(self._context_bitrate_kbps())))
        # Sugestões automáticas podem usar um bitrate de vídeo calibrado pela
        # heurística acima do bitrate de vídeo detectado quando o arquivo já é
        # pequeno, mas ainda há redução total por áudio/tamanho. Nesse fluxo o
        # campo do Avançado deve refletir exatamente o card aplicado, em vez de
        # ser truncado pelo cap da entrada durante a sincronização dos controles.
        try:
            automatic_cap = int(getattr(self, "_advanced_automatic_bitrate_cap_kbps", 0) or 0)
        except Exception:
            automatic_cap = 0
        if automatic_cap > 0:
            cap = max(cap, min(50000, automatic_cap))
        return cap
    def _sync_advanced_bitrate_bounds(self):
        max_bitrate_kbps = self._advanced_bitrate_cap_kbps()
        tooltip = f"Máximo permitido: {max_bitrate_kbps} kbps (bitrate da entrada)."
        for widget in (self.advanced_target_bitrate_kbps, self.advanced_resolution_bitrate_kbps):
            widget.setMaximum(max_bitrate_kbps)
            widget.setToolTip(tooltip)
            if widget.value() > max_bitrate_kbps:
                widget.blockSignals(True)
                widget.setValue(max_bitrate_kbps)
                widget.blockSignals(False)
    def _set_advanced_bitrate_values(self, bitrate_kbps: int):
        bitrate_kbps = max(1, min(self._advanced_bitrate_cap_kbps(), int(bitrate_kbps)))
        self.advanced_target_bitrate_kbps.setValue(bitrate_kbps)
        self.advanced_resolution_bitrate_kbps.setValue(bitrate_kbps)
    def _advanced_effective_bitrate_kbps(self) -> int:
        strategy_mode = self._advanced_strategy_mode()
        bitrate_cap_kbps = self._advanced_bitrate_cap_kbps()
        if strategy_mode == "DIRECT_BITRATE":
            return max(1, min(bitrate_cap_kbps, int(self.advanced_target_bitrate_kbps.value())))
        if strategy_mode == "RESOLUTION_DRIVEN" and (self.advanced_resolution_bitrate_mode.currentData() or "auto") == "explicit":
            return max(1, min(bitrate_cap_kbps, int(self.advanced_resolution_bitrate_kbps.value())))
        value = self._advanced_estimated_bitrate_bps
        if value:
            return max(1, min(bitrate_cap_kbps, int(round(float(value) / 1000.0))))
        return bitrate_cap_kbps

    def _ensure_advanced_direct_bitrate_reduction_default(self) -> bool:
        """Seed Priorizar bitrate with a real reduction when switching modes.

        After a terminal state, changing the Advanced priority button must reset
        the card to an actionable Compress state.  If the direct bitrate control
        is still equal/near-equal to the source bitrate, the no-gain guard
        correctly returns Pronto!.  On mode switch only, seed a conservative
        bitrate reduction so the selected priority represents a real change.
        """
        source_kbps = self._context_bitrate_kbps()
        if source_kbps is None or source_kbps <= 0:
            return False
        cap_kbps = self._advanced_bitrate_cap_kbps()
        current_kbps = max(1, int(self.advanced_target_bitrate_kbps.value()))
        no_gain_floor = max(1, int(round(float(source_kbps) * 0.99)))
        if current_kbps < no_gain_floor:
            return False
        seeded_kbps = max(1, min(cap_kbps, int(round(float(source_kbps) * 0.90))))
        if seeded_kbps >= current_kbps:
            seeded_kbps = max(1, min(cap_kbps, current_kbps - 1))
        if seeded_kbps <= 0 or seeded_kbps == current_kbps:
            return False
        blocker = QSignalBlocker(self.advanced_target_bitrate_kbps)
        try:
            self.advanced_target_bitrate_kbps.setValue(seeded_kbps)
        finally:
            del blocker
        return True
    def _estimate_advanced_size_mb(self, bitrate_kbps: int, width: int | None, height: int | None) -> int:
        source_size_limit_mb = self._source_size_limit_mb() or max(0.1, float(self.advanced_target_size_mb.value()))
        source_width, source_height = self._source_resolution()
        source_pixels = max(1, int(source_width or width or 1920) * int(source_height or height or 1080))
        target_pixels = max(1, int(width or source_width or 1920) * int(height or source_height or 1080))
        pixel_ratio = max(0.1, min(1.0, target_pixels / source_pixels))
        source_bitrate = getattr(self._context_job, "input_bitrate", None) if self._context_job is not None else None
        try:
            source_kbps = max(1, float(source_bitrate) / 1000.0)
        except Exception:
            est_output_bitrate = getattr(self._context_job, "estimated_output_bitrate", None) if self._context_job is not None else None
            try:
                source_kbps = max(1, float(est_output_bitrate) / 1000.0)
            except Exception:
                source_kbps = max(1.0, float(bitrate_kbps))
        compression_ratio = max(0.05, min(4.0, float(bitrate_kbps) / source_kbps))
        estimated = int(round(source_size_limit_mb * pixel_ratio * compression_ratio))
        return max(1, min(self._advanced_target_size_limit(), estimated))
    def _estimate_bitrate_for_target_size(self, target_size_mb: int, width: int | None, height: int | None) -> int:
        preview_job = self._build_advanced_preview_job()
        try:
            setattr(preview_job, "advanced_target_size_mb", max(0.1, float(target_size_mb)))
            total_bitrate = advanced_profile.estimate_total_bitrate_bps(preview_job)
        except Exception:
            total_bitrate = None
        if total_bitrate is not None and total_bitrate > 0:
            return max(1, int(round(total_bitrate / 1000.0)))
        # Fallback only when duration is unavailable.
        source_size_limit_mb = self._source_size_limit_mb() or max(0.1, float(target_size_mb))
        source_kbps = float(self._advanced_bitrate_cap_kbps())
        size_ratio = max(0.05, min(1.0, float(target_size_mb) / max(1.0, float(source_size_limit_mb))))
        bitrate = int(round(source_kbps * size_ratio))
        return max(1, min(self._advanced_bitrate_cap_kbps(), bitrate))
    def _estimate_resolution_for_target_size(self, target_size_mb: int, preferred_bitrate_kbps: int) -> tuple[int | None, int | None]:
        source_width, source_height = self._source_resolution()
        if not (source_width and source_height):
            return self._resolved_advanced_resolution()
        source_size_limit_mb = self._source_size_limit_mb() or max(0.1, float(target_size_mb))
        source_kbps = float(self._context_bitrate_kbps())
        if source_kbps <= 0:
            source_kbps = max(1.0, float(preferred_bitrate_kbps or 1))
        size_ratio = max(0.05, min(1.0, float(target_size_mb) / max(1.0, float(source_size_limit_mb))))
        bitrate_ratio = max(0.1, min(2.5, float(preferred_bitrate_kbps) / source_kbps))
        pixel_ratio = max(0.1, min(1.0, size_ratio * bitrate_ratio))
        scale = pixel_ratio ** 0.5
        width = max(2, min(source_width, int(round(source_width * scale))))
        height = max(2, min(source_height, int(round(source_height * scale))))
        if width % 2:
            width -= 1
        if height % 2:
            height -= 1
        return max(2, width), max(2, height)
    def _set_advanced_resolution_values(self, width: int | None, height: int | None):
        if width is None or height is None:
            return
        self.advanced_width.setValue(max(2, int(width)))
        self.advanced_height.setValue(max(2, int(height)))
    def _recalculate_advanced_targets_from_driver(self, driver: str):
        if self._advanced_sync_in_progress:
            return
        self._advanced_sync_in_progress = True
        try:
            self._sync_advanced_resolution_bounds()
            self._sync_advanced_target_size_bounds()
            self._sync_advanced_bitrate_bounds()
            strategy_mode = self._advanced_strategy_mode()
            source_width, source_height = self._source_resolution()
            width, height = self._resolved_advanced_resolution()
            target_size_mb = max(0.1, min(self._advanced_target_size_limit(), float(self.advanced_target_size_mb.value())))
            source_bitrate_kbps = self._context_bitrate_kbps()
            bitrate_kbps = self._advanced_effective_bitrate_kbps()
            if driver == "target_size_mb":
                baseline_width = width or source_width
                baseline_height = height or source_height
                computed_bitrate = self._estimate_bitrate_for_target_size(target_size_mb, baseline_width, baseline_height)
                if strategy_mode == "DIRECT_BITRATE":
                    # Priorizar bitrate: o tamanho desejado continua mandando;
                    # a redução é refletida no bitrate calculado e a resolução é preservada.
                    computed_bitrate = self._estimate_bitrate_for_target_size(
                        target_size_mb,
                        source_width or baseline_width,
                        source_height or baseline_height,
                    )
                    bitrate_kbps = max(1, min(self._advanced_bitrate_cap_kbps(), int(round(computed_bitrate))))
                    if source_width and source_height:
                        width, height = source_width, source_height
                        self._set_advanced_resolution_values(width, height)
                elif strategy_mode == "RESOLUTION_DRIVEN":
                    # Priorizar resolução: ao mudar o tamanho desejado, a resolução
                    # precisa ser recalculada imediatamente, não apenas ao alternar modo.
                    preferred_bitrate = max(1, min(self._advanced_bitrate_cap_kbps(), int(source_bitrate_kbps or computed_bitrate or 1)))
                    width, height = self._estimate_resolution_for_target_size(target_size_mb, preferred_bitrate)
                    self._set_advanced_resolution_values(width, height)
                    bitrate_kbps = self._estimate_bitrate_for_target_size(target_size_mb, width, height)
                else:
                    bitrate_kbps = max(1, int(round(computed_bitrate)))
                    width, height = self._estimate_resolution_for_target_size(target_size_mb, bitrate_kbps)
                    self._set_advanced_resolution_values(width, height)
                self._set_advanced_bitrate_values(max(1, int(round(bitrate_kbps))))
            elif driver == "target_bitrate_kbps":
                if strategy_mode == "RESOLUTION_DRIVEN" and (self.advanced_resolution_bitrate_mode.currentData() or "auto") == "explicit":
                    bitrate_kbps = max(1, min(self._advanced_bitrate_cap_kbps(), int(self.advanced_resolution_bitrate_kbps.value())))
                else:
                    bitrate_kbps = max(1, min(self._advanced_bitrate_cap_kbps(), int(self.advanced_target_bitrate_kbps.value())))
                self._set_advanced_bitrate_values(bitrate_kbps)
                target_size_mb = self._estimate_advanced_size_mb(bitrate_kbps, width, height)
                self.advanced_target_size_mb.setValue(target_size_mb)
            elif driver == "target_resolution":
                width, height = self._resolved_advanced_resolution()
                if strategy_mode == "RESOLUTION_DRIVEN" and (self.advanced_resolution_bitrate_mode.currentData() or "auto") == "explicit":
                    bitrate_kbps = max(1, min(self._advanced_bitrate_cap_kbps(), int(self.advanced_resolution_bitrate_kbps.value())))
                else:
                    bitrate_kbps = self._advanced_effective_bitrate_kbps()
                self._set_advanced_bitrate_values(bitrate_kbps)
                target_size_mb = self._estimate_advanced_size_mb(bitrate_kbps, width, height)
                self.advanced_target_size_mb.setValue(target_size_mb)
            elif driver == "bitrate_resolution":
                width, height = self._resolved_advanced_resolution()
                bitrate_kbps = self._advanced_effective_bitrate_kbps()
                self._set_advanced_bitrate_values(bitrate_kbps)
                target_size_mb = self._estimate_advanced_size_mb(bitrate_kbps, width, height)
                self.advanced_target_size_mb.setValue(target_size_mb)
            self._advanced_estimated_size_mb = max(0.1, float(target_size_mb))
            preview_job = self._build_advanced_preview_job()
            try:
                setattr(preview_job, "advanced_target_size_mb", self._advanced_estimated_size_mb)
                target_total_bps = advanced_profile.estimate_total_bitrate_bps(preview_job)
            except Exception:
                target_total_bps = None
            if target_total_bps is not None and target_total_bps > 0:
                self._advanced_estimated_bitrate_bps = int(target_total_bps)
            else:
                self._advanced_estimated_bitrate_bps = max(1, int(bitrate_kbps) * 1000)
        finally:
            self._advanced_sync_in_progress = False
        self._update_advanced_controls()
        self._update_advanced_summary()
        if not self._suppress_live_updates:
            self._sync_profile_preview_to_context(emit_apply=False)
    def _on_advanced_target_size_changed(self, value: int):
        if self._advanced_sync_in_progress:
            return
        self._advanced_target_auto_adjusted_by_audio = False
        self._advanced_auto_high_compression_notice = False
        self._advanced_auto_balanced_notice = False
        self._advanced_last_driver = "target_size_mb"
        self._recalculate_advanced_targets_from_driver("target_size_mb")
    def _on_advanced_bitrate_changed(self, value: int):
        if self._advanced_sync_in_progress:
            return
        self._advanced_auto_high_compression_notice = False
        self._advanced_auto_balanced_notice = False
        self._advanced_last_driver = "target_bitrate_kbps"
        driver = "target_bitrate_kbps"
        if self._advanced_last_resolution_driver is not None:
            driver = "bitrate_resolution"
        self._recalculate_advanced_targets_from_driver(driver)
    def _on_advanced_strategy_changed(self, *_args):
        if self._advanced_sync_in_progress:
            return
        self._advanced_auto_high_compression_notice = False
        self._advanced_auto_balanced_notice = False
        self._update_advanced_controls()
        strategy_mode = self._advanced_strategy_mode()
        driver = self._advanced_last_driver or "target_size_mb"
        if strategy_mode == "DIRECT_BITRATE":
            self._ensure_advanced_direct_bitrate_reduction_default()
            self._advanced_last_driver = "target_bitrate_kbps"
            driver = "target_bitrate_kbps"
        self._recalculate_advanced_targets_from_driver(driver)
    def _advanced_duration_seconds(self) -> float | None:
        job = self._context_job
        if job is None:
            return None
        for attr in ("duration_seconds", "duration"):
            raw = getattr(job, attr, None)
            if raw is None:
                continue
            if isinstance(raw, (int, float)):
                value = float(raw)
                return value if value > 0 else None
            text = str(raw).strip()
            if not text:
                continue
            try:
                value = float(text.replace(",", "."))
                return value if value > 0 else None
            except Exception:
                pass
            try:
                parts = [float(part.replace(",", ".")) for part in text.split(":")]
                if len(parts) == 2:
                    value = parts[0] * 60 + parts[1]
                elif len(parts) == 3:
                    value = parts[0] * 3600 + parts[1] * 60 + parts[2]
                else:
                    continue
                return value if value > 0 else None
            except Exception:
                continue
        return None
    def _auto_adjust_target_size_from_audio_reduction(self) -> bool:
        """Shrink the target size when audio is explicitly reduced from an original-size target.
        In Advanced, the desired size is the encode budget. However, when the
        current desired size is effectively the original/source budget and the
        user only reduces audio, filling the freed audio budget back into video
        makes the displayed total size/bitrate look unchanged. In that specific
        case, move the desired size down immediately so the preview reflects the
        audio reduction before the user clicks Compress.
        """
        if self._context_job is None:
            return False
        audio_policy = str(self.advanced_audio_policy.currentData() or "keep").strip().lower()
        original_audio_kbps = self._source_audio_bitrate_kbps()
        selected_audio_kbps = self.advanced_audio_policy.currentValue()
        max_size_mb = self._source_size_limit_mb()
        source_total_bps = self._source_input_bitrate_bps()
        duration_seconds = self._advanced_duration_seconds()
        if max_size_mb is None or source_total_bps is None or duration_seconds is None:
            return False
        if audio_policy == "keep":
            if self._advanced_target_auto_adjusted_by_audio:
                with QSignalBlocker(self.advanced_target_size_mb):
                    self.advanced_target_size_mb.setValue(max(0.1, float(max_size_mb)))
                self._advanced_target_auto_adjusted_by_audio = False
                return True
            return False
        if original_audio_kbps is None or selected_audio_kbps is None:
            return False
        if int(selected_audio_kbps) >= int(original_audio_kbps):
            return False
        try:
            current_target_bps = advanced_profile.estimate_total_bitrate_bps(self._build_advanced_preview_job())
        except Exception:
            current_target_bps = None
        source_total_bps = int(source_total_bps)
        if source_total_bps <= 0:
            return False
        close_to_original_budget = (
            self._advanced_target_auto_adjusted_by_audio
            or abs(float(current_target_bps or 0) - float(source_total_bps)) <= max(8_000.0, float(source_total_bps) * 0.08)
            or float(self.advanced_target_size_mb.value()) >= float(max_size_mb) * 0.96
        )
        if not close_to_original_budget:
            return False
        reduced_total_bps = max(1, source_total_bps - int(round((float(original_audio_kbps) - float(selected_audio_kbps)) * 1000.0)))
        target_bytes = (float(reduced_total_bps) * float(duration_seconds)) / 8.0
        target_mb = max(0.1, min(float(max_size_mb), target_bytes / float(1024 * 1024)))
        if abs(target_mb - float(self.advanced_target_size_mb.value())) < 0.005:
            return False
        with QSignalBlocker(self.advanced_target_size_mb):
            self.advanced_target_size_mb.setValue(target_mb)
        self._advanced_target_auto_adjusted_by_audio = True
        return True
    def _on_advanced_context_policy_changed(self, *_args):
        if self._advanced_sync_in_progress:
            return
        self._advanced_last_driver = "target_size_mb"
        self._auto_adjust_target_size_from_audio_reduction()
        self._recalculate_advanced_targets_from_driver("target_size_mb")
    def _update_advanced_controls(self):
        manual_type = self.advanced_target_mode.currentData() or "TARGET_SIZE"
        is_bitrate = manual_type == "DIRECT_BITRATE"
        is_size = manual_type == "TARGET_SIZE"
        is_resolution = manual_type == "RESOLUTION_DRIVEN"
        self._sync_advanced_resolution_bounds()
        self._sync_advanced_target_size_bounds()
        self.advanced_target_size_row.setVisible(True)
        self.advanced_strategy_row.setVisible(True)
        self.advanced_resolution_row.setVisible(False)
        self.advanced_fps_row.setVisible(True)
        self.advanced_audio_row.setVisible(True)
        self.advanced_resolution_bitrate_mode_row.setVisible(False)
        self.advanced_resolution_context_row.setVisible(False)
        self._set_form_row_label(self.advanced_strategy_row, "")
        self._configure_advanced_form_row(self.advanced_strategy_row, label_width=0, spacing=0)
        self._set_form_row_label(self.advanced_resolution_row, "Resolução")
        self._set_form_row_label(self.advanced_resolution_context_row, "Saída")
        if is_bitrate:
            self.advanced_strategy_stack.setCurrentWidget(self.advanced_bitrate_fields)
        elif is_resolution:
            self.advanced_strategy_stack.setCurrentWidget(self.advanced_resolution_fields)
        else:
            self.advanced_strategy_stack.setCurrentWidget(self.advanced_balance_fields)
        self.advanced_strategy_row.setVisible(True)
        self.advanced_context_stack.setCurrentWidget(self.advanced_context_status)
        self.advanced_resolution_bitrate_kbps.setEnabled(False)
        self.advanced_target_bitrate_kbps.setEnabled(not self._locked and is_bitrate)
        self.advanced_strategy_auto_hint.setEnabled(True)
        self.advanced_target_size_mb.setEnabled(not self._locked)
        self.advanced_fps_policy.setEnabled(not self._locked)
        self.advanced_audio_policy.setEnabled(not self._locked)
        self.advanced_width.setEnabled(not self._locked and is_resolution)
        self.advanced_height.setEnabled(not self._locked and is_resolution)
        for mode_key, card in self.advanced_strategy_cards.items():
            card.setProperty("activeStrategy", mode_key == manual_type)
            card.style().unpolish(card)
            card.style().polish(card)
            card.update()
        self.advanced_target_bitrate_kbps.setProperty("inactiveField", False)
        self.advanced_fps_policy.setProperty("inactiveField", False)
        self.advanced_audio_policy.setProperty("inactiveField", False)
        self.advanced_width.setProperty("inactiveField", False)
        self.advanced_height.setProperty("inactiveField", False)
        for widget in (self.advanced_target_bitrate_kbps, self.advanced_width, self.advanced_height, self.advanced_strategy_auto_hint):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        self.advanced_output_title.setText("Saída estimada")
        for button in self.advanced_target_mode_buttons.values():
            button.setEnabled(not self._locked)
        self._update_advanced_summary()
    def _advanced_audio_reduction_status_data(self) -> tuple[str, str]:
        """Return status text + severity for audio-only reduction in Advanced.
        Reducing only audio must not produce a fake video-budget warning. Very
        low audio bitrates still get their own yellow warning in the bottom bar.
        Severity values: neutral, notice, strong.
        """
        if not bool(getattr(self, "_advanced_target_auto_adjusted_by_audio", False)):
            return "", "neutral"
        audio_policy = str(self.advanced_audio_policy.currentData() or "keep").strip().lower()
        if audio_policy == "keep":
            return "", "neutral"
        original_audio_kbps = self._source_audio_bitrate_kbps()
        selected_audio_kbps = self.advanced_audio_policy.currentValue()
        try:
            selected_audio_kbps = int(round(float(selected_audio_kbps)))
        except Exception:
            selected_audio_kbps = None
        try:
            original_audio_kbps = int(round(float(original_audio_kbps)))
        except Exception:
            original_audio_kbps = None
        if selected_audio_kbps is None or selected_audio_kbps <= 0:
            return "Áudio reduzido; economia aplicada ao tamanho estimado.", "notice"
        if selected_audio_kbps <= 48:
            severity = "strong"
            quality_note = "pode haver perda audível de qualidade"
        elif selected_audio_kbps <= 64:
            severity = "notice"
            quality_note = "pode haver leve perda audível"
        else:
            severity = "neutral"
            quality_note = "economia aplicada ao tamanho estimado"
        if original_audio_kbps is not None and original_audio_kbps > selected_audio_kbps:
            saved = max(1, int(original_audio_kbps - selected_audio_kbps))
            if severity == "neutral":
                return f"Áudio reduzido para {selected_audio_kbps} kbps; economia de {saved} kbps aplicada ao tamanho estimado.", severity
            return f"Áudio reduzido para {selected_audio_kbps} kbps; economia de {saved} kbps aplicada, mas {quality_note}.", severity
        if severity == "neutral":
            return f"Áudio reduzido para {selected_audio_kbps} kbps; economia aplicada ao tamanho estimado.", severity
        return f"Áudio reduzido para {selected_audio_kbps} kbps; {quality_note}.", severity
    @staticmethod
    def _qcolor_rgba(color: QColor, alpha: int | None = None) -> str:
        c = QColor(color)
        if alpha is not None:
            c.setAlpha(max(0, min(255, int(alpha))))
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"

    def _advanced_status_warning_style(self, *, strong: bool = True) -> str:
        """Return a neutral footer style for Advanced alerts.

        Keep the alert readable across live theme changes by avoiding cached
        theme colors in inline styles. The text color comes from the active QSS;
        only spacing and weight are set here. The red alert marker is injected
        as rich text by `_advanced_alert_text_html`.
        """
        return (
            "background: transparent; "
            "border: none; "
            "border-radius: 0px; "
            "padding: 4px 8px; "
            f"font-weight: {600 if strong else 500};"
        )

    @staticmethod
    def _advanced_alert_text_html(message: str) -> str:
        escaped = html.escape(str(message or "").strip())
        if not escaped:
            return ""
        return (
            '<span style="color:#d93025; font-weight:700; font-size:13px;">⚠</span>'
            '&nbsp;<span>' + escaped + '</span>'
        )

    def _set_advanced_summary_plain(self, message: str, style: str = "") -> None:
        self.advanced_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.advanced_summary.setText(str(message or ""))
        self.advanced_summary.setStyleSheet(style)

    def _set_advanced_summary_alert(self, message: str, *, strong: bool = True) -> None:
        self.advanced_summary.setTextFormat(Qt.TextFormat.RichText)
        self.advanced_summary.setText(self._advanced_alert_text_html(message))
        self.advanced_summary.setStyleSheet(self._advanced_status_warning_style(strong=strong))

    def _advanced_audio_reduction_status_style(self, severity: str) -> str:
        if severity == "strong":
            return self._advanced_status_warning_style(strong=True)
        if severity == "notice":
            return self._advanced_status_warning_style(strong=False)
        return ""

    @staticmethod
    def _advanced_high_compression_notice_text() -> str:
        return "Alta compressão aplicada: prioriza menor tamanho final e pode reduzir nitidez/qualidade."

    @staticmethod
    def _advanced_balanced_notice_text() -> str:
        return "Sugestão equilibrada aplicada: prioriza boa legibilidade com redução segura de tamanho."

    def _advanced_quality_warning_text(self, total_bitrate_bps: int | None = None) -> str:
        """Return a contextual low-quality warning for the current Advanced strategy.
        The warning is intentionally displayed in the Advanced status bar, not in
        the control area. It must not block the target-size contract; it only
        explains which trade-off is most likely for the active priority.
        """
        if self._context_job is None:
            return ""
        target_size_mb = max(0.1, float(self.advanced_target_size_mb.value()))
        source_size_bytes = self._source_size_bytes()
        source_size_mb = (float(source_size_bytes) / float(1024 * 1024)) if source_size_bytes else None
        size_ratio = None
        if source_size_mb is not None and source_size_mb > 0:
            size_ratio = float(target_size_mb) / source_size_mb
        preview_job = self._build_advanced_preview_job()
        try:
            setattr(preview_job, "advanced_target_size_mb", target_size_mb)
            target_total_bps = int(total_bitrate_bps) if total_bitrate_bps is not None else advanced_profile.estimate_total_bitrate_bps(preview_job)
        except Exception:
            target_total_bps = None
        try:
            video_bps = advanced_profile.compute_video_bitrate_bps(preview_job)
        except Exception:
            video_bps = None
        try:
            audio_bps = advanced_profile.audio_bitrate_for_output(preview_job)
        except Exception:
            audio_bps = None
        # Reduções leves não devem disparar alerta. Ex.: 4.8 MB -> 4 MB
        # continua acima de 70% da entrada e deve permanecer sem aviso.
        if size_ratio is not None and size_ratio >= 0.70:
            return ""
        risky_size = size_ratio is not None and size_ratio < 0.45
        low_total = target_total_bps is not None and target_total_bps < 100_000
        low_video = video_bps is not None and video_bps < 80_000
        audio_policy = str(self.advanced_audio_policy.currentData() or "keep").strip().lower()
        audio_auto_reduction = bool(getattr(self, "_advanced_target_auto_adjusted_by_audio", False)) and audio_policy != "keep"
        # If the desired size was reduced automatically because the user lowered
        # the audio bitrate, do not reinterpret the smaller total budget as a
        # video-quality warning. The video budget did not get worse; the audio
        # stream simply became cheaper.
        if audio_auto_reduction:
            return ""
        audio_pressure = False
        if audio_policy == "keep" and audio_bps is not None and target_total_bps is not None and target_total_bps > 0:
            audio_pressure = audio_bps >= int(target_total_bps * 0.35)
        if not (risky_size or low_total or low_video or audio_pressure):
            return ""
        manual_type = self.advanced_target_mode.currentData() or "TARGET_SIZE"
        if manual_type == "DIRECT_BITRATE":
            details: list[str] = []
            if low_total:
                details.append("bitrate total abaixo de 100 kbps")
            if low_video:
                details.append("vídeo abaixo de 80 kbps")
            if audio_pressure:
                details.append("áudio preservado reduz o orçamento do vídeo")
            if not details and risky_size:
                details.append("alvo muito abaixo da entrada")
            return "Alvo baixo no bitrate: podem surgir artefatos e borrões (" + "; ".join(details[:2]) + ")."
        if manual_type == "RESOLUTION_DRIVEN":
            width, height = self._resolved_advanced_resolution()
            details: list[str] = []
            if width and height:
                details.append(f"resolução calculada em {width}x{height}")
            if risky_size:
                details.append("alvo muito abaixo da entrada")
            if low_video:
                details.append("orçamento de vídeo abaixo de 80 kbps")
            if not details and low_total:
                details.append("bitrate total abaixo de 100 kbps")
            return "Alvo baixo na resolução: a imagem pode perder nitidez (" + "; ".join(details[:2]) + ")."
        details: list[str] = []
        if risky_size:
            details.append("alvo muito abaixo da entrada")
        if low_total:
            details.append("bitrate total abaixo de 100 kbps")
        if low_video:
            details.append("orçamento de vídeo abaixo de 80 kbps")
        if audio_pressure:
            details.append("áudio preservado reduz o orçamento do vídeo")
        return "Alvo baixo no equilíbrio: pode haver perda visível de qualidade (" + "; ".join(details[:2]) + ")."
    def _update_advanced_summary(self):
        if self._suppress_live_updates:
            return
        manual_type = self.advanced_target_mode.currentData() or "TARGET_SIZE"
        width, height = self._resolved_advanced_resolution()
        resolution_text = f"{width}x{height} px" if width and height else "Original"
        actual_output_valid = profile_output_matches_current_settings(self._context_job) if self._context_job is not None else False
        actual_size_bytes = getattr(self._context_job, "output_size_bytes", None) if actual_output_valid else None
        actual_bitrate_bps = getattr(self._context_job, "output_bitrate", None) if actual_output_valid else None
        estimated_size_bytes = getattr(self._context_job, "estimated_size_bytes", None) if self._context_job is not None else None
        estimated_output_bitrate_bps = getattr(self._context_job, "estimated_output_bitrate", None) if self._context_job is not None else None
        try:
            actual_size_bytes = int(round(float(actual_size_bytes))) if actual_size_bytes is not None else None
        except Exception:
            actual_size_bytes = None
        try:
            actual_bitrate_bps = int(round(float(actual_bitrate_bps))) if actual_bitrate_bps is not None else None
        except Exception:
            actual_bitrate_bps = None
        try:
            estimated_size_bytes = int(round(float(estimated_size_bytes))) if estimated_size_bytes is not None else None
        except Exception:
            estimated_size_bytes = None
        try:
            estimated_output_bitrate_bps = int(round(float(estimated_output_bitrate_bps))) if estimated_output_bitrate_bps is not None else None
        except Exception:
            estimated_output_bitrate_bps = None
        has_actual_output = actual_size_bytes is not None or actual_bitrate_bps is not None
        estimated_size = self._advanced_estimated_size_mb
        if has_actual_output:
            estimated_size = None
        if estimated_size is None and estimated_size_bytes is not None:
            try:
                estimated_size = max(0.1, float(estimated_size_bytes) / float(1024 * 1024))
            except Exception:
                estimated_size = None
        estimated_bitrate = self._advanced_estimated_bitrate_bps
        if has_actual_output:
            estimated_bitrate = None
        if estimated_bitrate is None:
            estimated_bitrate = estimated_output_bitrate_bps
        if estimated_bitrate is not None:
            try:
                estimated_bitrate = int(float(estimated_bitrate))
            except Exception:
                estimated_bitrate = None
        display_bitrate = actual_bitrate_bps if has_actual_output and actual_bitrate_bps is not None else estimated_bitrate
        if display_bitrate is not None:
            try:
                display_bitrate = max(1, int(display_bitrate))
            except Exception:
                display_bitrate = None
        bitrate_text = _format_bitrate(display_bitrate) or "Estimativa ao aplicar"
        target_size_value = max(0.1, float(self.advanced_target_size_mb.value()))
        size_target_text = f"{_format_mb_value(target_size_value)} MB"
        source_size_text = self._source_size_display_text()
        derived_size_text = _format_bytes(actual_size_bytes) if has_actual_output and actual_size_bytes is not None else (f"{_format_mb_value(estimated_size)} MB" if estimated_size is not None else size_target_text)
        size_text = derived_size_text if has_actual_output or self._advanced_last_driver in {"target_bitrate_kbps", "target_resolution"} else size_target_text
        fps_policy = self.advanced_fps_policy.currentData() or "keep"
        audio_policy = self.advanced_audio_policy.currentData() or "keep"
        fps_value = self.advanced_fps_policy.currentValue()
        audio_value = self.advanced_audio_policy.currentValue()
        fps_text = "preservado" if fps_policy == "keep" else f"até {fps_value} fps"
        audio_text = "preservado" if audio_policy == "keep" else f"até {audio_value} kbps"
        if manual_type == "DIRECT_BITRATE":
            strategy_text = "Priorizar bitrate"
            reference_text = f"{self.advanced_target_bitrate_kbps.value()} kbps"
        elif manual_type == "RESOLUTION_DRIVEN":
            strategy_text = "Priorizar resolução"
            reference_text = resolution_text
        else:
            strategy_text = "Equilibrar"
            reference_text = "bitrate + resolução"
        driver_text = {
            "target_size_mb": "tamanho",
            "target_bitrate_kbps": "bitrate",
            "target_resolution": "resolução",
        }.get(self._advanced_last_driver, "tamanho")
        summary = f"Estratégia ativa: {strategy_text}. Driver atual: {driver_text}. Tamanho {size_text}. Bitrate {bitrate_text}. Resolução {resolution_text}."
        self.advanced_output_title.setText("Saída atual" if has_actual_output else "Saída estimada")
        fps_display_text = (self.advanced_fps_policy.currentText().strip() or "Original").replace(" (original)", "").strip()
        audio_display_text = (self.advanced_audio_policy.currentText().strip() or "Original").replace(" (original)", "").strip()
        self.advanced_output_resolution_value.setText(resolution_text)
        self.advanced_output_fps_value.setText(fps_display_text)
        self.advanced_output_bitrate_value.setText(bitrate_text)
        self.advanced_output_audio_bitrate_value.setText(audio_display_text)
        self.advanced_output_size_value.setText(size_text)
        source_width, source_height = self._source_resolution()
        source_size_bytes = resolve_job_source_size_bytes(self._context_job) if self._context_job is not None else None
        source_bitrate_bps = self._source_input_bitrate_bps()
        reduction_ratio = None
        bitrate_ratio = None
        if source_size_bytes and source_size_bytes > 0:
            try:
                displayed_size_bytes = actual_size_bytes if has_actual_output and actual_size_bytes is not None else estimated_size_bytes
                if displayed_size_bytes is None and estimated_size is not None:
                    displayed_size_bytes = int(round(float(estimated_size) * 1024 * 1024))
                if displayed_size_bytes is not None:
                    reduction_ratio = float(displayed_size_bytes) / float(source_size_bytes)
            except Exception:
                reduction_ratio = None
        if source_bitrate_bps and display_bitrate:
            try:
                bitrate_ratio = float(display_bitrate) / float(source_bitrate_bps)
            except Exception:
                bitrate_ratio = None
        if source_width and source_height:
            source_resolution_text = f"{source_width}x{source_height} px"
            current_width = None
            current_height = None
            try:
                current_width = int(width) if width else None
                current_height = int(height) if height else None
            except Exception:
                current_width = None
                current_height = None
            resolution_reduced = bool(
                current_width and current_height and (
                    current_width < int(source_width) or current_height < int(source_height)
                )
            )
            bitrate_reduced = bitrate_ratio is not None and bitrate_ratio <= 0.35
            aggressive_reduction = reduction_ratio is not None and reduction_ratio <= 0.20
            if resolution_reduced and (bitrate_reduced or aggressive_reduction):
                self.advanced_resolution_meta.setText(
                    f"A saída reduz resolução e bitrate em relação à origem ({source_resolution_text})."
                )
            elif resolution_reduced:
                self.advanced_resolution_meta.setText(
                    f"A saída reduz a resolução em relação à origem ({source_resolution_text})."
                )
            elif bitrate_reduced or aggressive_reduction:
                self.advanced_resolution_meta.setText(
                    f"A saída reduz o bitrate em relação à origem ({source_resolution_text})."
                )
            else:
                self.advanced_resolution_meta.setText(
                    f"A saída preserva a proporção original e não excede a origem ({source_resolution_text})."
                )
        else:
            bitrate_reduced = bitrate_ratio is not None and bitrate_ratio <= 0.35
            aggressive_reduction = reduction_ratio is not None and reduction_ratio <= 0.20
            if bitrate_reduced or aggressive_reduction:
                self.advanced_resolution_meta.setText(
                    "A saída reduz o bitrate em relação à origem."
                )
            else:
                self.advanced_resolution_meta.setText(
                    "A saída preserva a proporção original e respeita os limites da origem."
                )
        warning_text = self._advanced_quality_warning_text(display_bitrate if display_bitrate is not None else estimated_bitrate)
        audio_status_text, audio_status_severity = self._advanced_audio_reduction_status_data()
        fps_text = fps_display_text
        audio_text = audio_display_text
        hint_text = ""
        if source_size_text:
            hint_text = f"{source_size_text}"
        self.advanced_target_size_hint.setText(hint_text)
        self.advanced_target_size_desired_label.setText("Meta:")
        reduction_text = "—"
        try:
            source_limit_mb = self._source_size_limit_mb()
            if source_limit_mb is not None and source_limit_mb > 0:
                reduction_percent = max(0.0, (1.0 - (float(target_size_value) / float(source_limit_mb))) * 100.0)
                reduction_text = f"{reduction_percent:.0f}%"
        except Exception:
            reduction_text = "—"
        if hasattr(self, "advanced_target_size_meta_value_label"):
            self.advanced_target_size_meta_value_label.setText(size_target_text)
        if hasattr(self, "advanced_target_size_reduction_label"):
            self.advanced_target_size_reduction_label.setText(f"Redução estimada: {reduction_text}")
        show_target_flow = bool(source_size_text)
        self.advanced_target_size_arrow_label.setVisible(show_target_flow)
        if hasattr(self, "advanced_target_size_second_arrow_label"):
            self.advanced_target_size_second_arrow_label.setVisible(show_target_flow)
        self.advanced_target_size_mb.setToolTip(f"Tamanho da entrada: {source_size_text}. O tamanho desejado não pode exceder esse valor." if source_size_text else "")
        if manual_type == "DIRECT_BITRATE":
            mode_explanation = "No modo Priorizar bitrate, o bitrate definido é preservado e a resolução é ajustada quando necessário."
        elif manual_type == "RESOLUTION_DRIVEN":
            mode_explanation = "No modo Priorizar resolução, a resolução definida é priorizada e o bitrate é ajustado quando necessário."
        else:
            mode_explanation = "No modo Equilibrar, bitrate e resolução são ajustados automaticamente para atingir o tamanho desejado."
        if bool(getattr(self, "_advanced_auto_high_compression_notice", False)):
            self._set_advanced_summary_plain(
                self._advanced_high_compression_notice_text(),
                self._advanced_status_warning_style(strong=False),
            )
        elif bool(getattr(self, "_advanced_auto_balanced_notice", False)):
            self._set_advanced_summary_plain(
                self._advanced_balanced_notice_text(),
                self._advanced_status_warning_style(strong=False),
            )
        elif warning_text:
            self._set_advanced_summary_alert(warning_text, strong=True)
        elif audio_status_text:
            if audio_status_severity in {"strong", "notice"}:
                self._set_advanced_summary_alert(audio_status_text, strong=(audio_status_severity == "strong"))
            else:
                self._set_advanced_summary_plain(audio_status_text, self._advanced_audio_reduction_status_style(audio_status_severity))
        else:
            self._set_advanced_summary_plain(mode_explanation, "")
    def _on_quick_preset_value_changed(self):
        self._update_quick_preset_labels()
        self._sync_profile_preview_to_context(emit_apply=False)
        if not self._locked:
            self.quickPresetAutoApplyRequested.emit(self.collect_values())
    def _load(self):
        self._suppress_live_updates = True
        try:
            cfg = self.service.get()
            index = getattr(cfg, "quick_profile_preset", 4)
            index = max(0, min(index, len(self.QUICK_PRESETS) - 1))
            self._set_quick_preset_index(index)
            self._set_mode("quick")
            self._update_quick_preset_labels()
            self._set_video_output_format_from_source({"video_output_format": "mp4"})
            self._set_profile_values(default_smart_profile())
            self._set_profile_values(default_advanced_size_profile())
            self._set_profile_values(default_audio_profile())
            self._update_smart_summary(allow_heavy_estimate=False)
            self._update_advanced_controls()
            self._refresh_audio_profile_context()
            self._refresh_audio_profile_context()
            self._recalculate_advanced_targets_from_driver("target_size_mb")
            self.set_locked(False)
            self._sync_analyze_media_button_state()
        finally:
            self._suppress_live_updates = False
        if self._current_mode_name() == "audio":
            self._sync_audio_bitrate_to_detected_track()
            self._sync_audio_bitrate_to_effective_cap()
            self._sync_profile_preview_to_context(emit_apply=False)
        self._update_advanced_summary()
    def _load_from_context(self, preferred_mode: str | None = None):
        job = self._context_job
        if job is None:
            self._load()
            return
        self._suppress_live_updates = True
        try:
            raw_profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
            profile_mode = "quick" if raw_profile_mode == "automatic" else raw_profile_mode
            self._set_mode(profile_mode)
            self._update_mode_availability()
            self._set_video_output_format_from_source(job)
            self._set_profile_values(job)
            self._update_quick_preset_labels()
            self._update_smart_summary(allow_heavy_estimate=False)
            self._update_advanced_controls()
        finally:
            self._suppress_live_updates = False
        if self._current_mode_name() == "audio":
            self._sync_audio_bitrate_to_detected_track()
            self._sync_audio_bitrate_to_effective_cap()
            self._sync_profile_preview_to_context(emit_apply=False)
        self._sync_video_output_visibility()
        self._sync_analyze_media_button_state()
        self._update_advanced_summary()
    def _set_profile_values(self, source):
        try:
            quick_index = int(getattr(source, "quick_profile_preset", None))
        except Exception:
            try:
                quick_index = int(source.get("quick_profile_preset", 4))
            except Exception:
                quick_index = default_quick_profile()["quick_profile_preset"]
        quick_index = max(0, min(quick_index, len(self.QUICK_PRESETS) - 1))
        self._set_quick_preset_index(quick_index)
        allow_audio_quality_reduction = getattr(source, "allow_audio_quality_reduction", None)
        if allow_audio_quality_reduction is None and isinstance(source, dict):
            allow_audio_quality_reduction = source.get("allow_audio_quality_reduction")
        self.reduce_audio_quality.setChecked(bool(allow_audio_quality_reduction))
        strategy = getattr(source, "strategy_type", None)
        if strategy is None:
            strategy = getattr(source, "smart_strategy", None)
        if strategy is None and isinstance(source, dict):
            strategy = source.get("strategy_type") or source.get("smart_strategy")
        if strategy not in SMART_STRATEGIES:
            strategy = default_smart_profile()["smart_strategy"]
        self._set_smart_strategy(strategy)
        slider_value = getattr(source, "slider_value", None)
        if slider_value is None:
            slider_value = getattr(source, "smart_intensity", None)
        if slider_value is None and isinstance(source, dict):
            slider_value = source.get("slider_value", source.get("smart_intensity"))
        try:
            slider_value = float(slider_value)
        except Exception:
            slider_value = float(default_smart_profile()["slider_value"])
        slider_value = max(0.0, min(10.0, slider_value))
        self._smart_intensity_values[strategy] = int(round(slider_value * 10))
        self._sync_smart_intensity_sliders()
        track_policy = getattr(source, "audio_track_policy", None)
        if track_policy is None and isinstance(source, dict):
            track_policy = source.get("audio_track_policy")
        track_policy = str(track_policy or default_smart_profile()["audio_track_policy"]).strip().upper()
        if track_policy == "KEEP_DEFAULT_ONLY":
            self._smart_audio_track_mode = "default"
        elif track_policy == "SELECTED_ONLY":
            self._smart_audio_track_mode = "selected"
        else:
            self._smart_audio_track_mode = "all"
        channel_policy = getattr(source, "audio_channel_policy", None)
        if channel_policy is None and isinstance(source, dict):
            channel_policy = source.get("audio_channel_policy")
        channel_policy = str(channel_policy or default_smart_profile()["audio_channel_policy"]).strip().upper()
        if channel_policy == "DOWNMIX_TO_MONO":
            self.smart_audio_mono.setChecked(True)
        elif channel_policy == "DOWNMIX_TO_STEREO":
            self.smart_audio_downmix.setChecked(True)
        else:
            self.smart_audio_keep_channels.setChecked(True)
        self._refresh_smart_audio_context()
        selected_track_id = getattr(source, "selected_track_id", None)
        if selected_track_id is None and isinstance(source, dict):
            selected_track_id = source.get("selected_track_id")
        if selected_track_id is not None:
            for idx in range(self.smart_audio_track_picker.count()):
                item_data = self.smart_audio_track_picker.itemData(idx)
                if isinstance(item_data, tuple) and len(item_data) == 2 and item_data[1] == selected_track_id:
                    self.smart_audio_track_picker.setCurrentIndex(idx)
                    self._smart_audio_track_mode = "selected"
                    break
        self._update_smart_audio_controls()
        manual_control_type = getattr(source, "manual_control_type", None)
        if manual_control_type is None and isinstance(source, dict):
            manual_control_type = source.get("manual_control_type")
        target_mode = getattr(source, "advanced_target_mode", None)
        if target_mode is None and isinstance(source, dict):
            target_mode = source.get("advanced_target_mode")
        manual_control_type = str(manual_control_type or target_mode or "TARGET_SIZE").strip().upper()
        mode_index = {
            "DIRECT_BITRATE": 0,
            "TARGET_SIZE": 1,
            "RESOLUTION_DRIVEN": 2,
            "BITRATE": 0,
            "SIZE": 1,
            "RESOLUTION": 2,
        }.get(manual_control_type, 1)
        self.advanced_target_mode.setCurrentIndex(mode_index)
        target_size_mb = getattr(source, "advanced_target_size_mb", None)
        if target_size_mb is None and isinstance(source, dict):
            target_size_mb = source.get("advanced_target_size_mb")
        try:
            target_size_mb = float(target_size_mb)
        except Exception:
            target_size_mb = default_advanced_size_profile()["advanced_target_size_mb"]
        self._sync_advanced_target_size_bounds()
        self._sync_advanced_bitrate_bounds()
        max_size_mb = self.advanced_target_size_mb.maximum()
        self.advanced_target_size_mb.setValue(max(0.1, min(float(target_size_mb), float(max_size_mb))))
        target_bitrate_kbps = getattr(source, "advanced_target_bitrate_kbps", None)
        if target_bitrate_kbps is None and isinstance(source, dict):
            target_bitrate_kbps = source.get("advanced_target_bitrate_kbps")
        try:
            target_bitrate_kbps = int(float(target_bitrate_kbps))
        except Exception:
            target_bitrate_kbps = self._context_bitrate_kbps()
        self.advanced_target_bitrate_kbps.setValue(max(1, min(self._advanced_bitrate_cap_kbps(), target_bitrate_kbps)))
        self._sync_advanced_original_combo_options(source)
        fps_policy = getattr(source, "advanced_fps_policy", None)
        if fps_policy is None and isinstance(source, dict):
            fps_policy = source.get("advanced_fps_policy")
        fps_value = getattr(source, "advanced_fps_value", None)
        if fps_value is None and isinstance(source, dict):
            fps_value = source.get("advanced_fps_value")
        try:
            fps_value = int(float(fps_value)) if fps_value is not None else None
        except Exception:
            fps_value = None
        fps_policy_text = str(fps_policy or "keep").strip().lower()
        if fps_policy_text == "reduce_if_needed":
            fps_policy_text = "reduce_to_value"
            fps_value = fps_value or 24
        self.advanced_fps_policy.setCurrentData(fps_policy_text, fps_value)
        audio_policy = getattr(source, "advanced_audio_policy", None)
        if audio_policy is None and isinstance(source, dict):
            audio_policy = source.get("advanced_audio_policy")
        audio_value = getattr(source, "advanced_audio_bitrate_kbps", None)
        if audio_value is None and isinstance(source, dict):
            audio_value = source.get("advanced_audio_bitrate_kbps")
        try:
            audio_value = int(float(audio_value)) if audio_value is not None else None
        except Exception:
            audio_value = None
        audio_policy_text = str(audio_policy or "keep").strip().lower()
        if audio_policy_text == "reduce_if_needed":
            audio_policy_text = "reduce_to_value"
            audio_value = audio_value or 96
        self.advanced_audio_policy.setCurrentData(audio_policy_text, audio_value)
        resolution_bitrate_mode = getattr(source, "advanced_resolution_bitrate_mode", None)
        if resolution_bitrate_mode is None and isinstance(source, dict):
            resolution_bitrate_mode = source.get("advanced_resolution_bitrate_mode")
        resolution_bitrate_mode = "explicit" if str(resolution_bitrate_mode or "auto").strip().lower() == "explicit" else "auto"
        self.advanced_resolution_bitrate_mode.setCurrentIndex(1 if resolution_bitrate_mode == "explicit" else 0)
        resolution_bitrate_kbps = getattr(source, "advanced_resolution_bitrate_kbps", None)
        if resolution_bitrate_kbps is None and isinstance(source, dict):
            resolution_bitrate_kbps = source.get("advanced_resolution_bitrate_kbps")
        try:
            resolution_bitrate_kbps = int(float(resolution_bitrate_kbps))
        except Exception:
            resolution_bitrate_kbps = self._context_bitrate_kbps()
        self.advanced_resolution_bitrate_kbps.setValue(max(1, min(self._advanced_bitrate_cap_kbps(), resolution_bitrate_kbps)))
        audio_output_format = getattr(source, "audio_output_format", None)
        if audio_output_format is None and isinstance(source, dict):
            audio_output_format = source.get("audio_output_format")
        audio_output_format = audio_profile.normalize_output_format(audio_output_format)
        audio_format_index = self.audio_output_format.findData(audio_output_format)
        if audio_format_index >= 0:
            self.audio_output_format.setCurrentIndex(audio_format_index)
        audio_bitrate_value = getattr(source, "audio_bitrate_kbps", None)
        if audio_bitrate_value is None and isinstance(source, dict):
            audio_bitrate_value = source.get("audio_bitrate_kbps")
        audio_bitrate_value = audio_profile.normalize_bitrate_kbps(audio_bitrate_value, output_format=audio_output_format)
        audio_bitrate_index = self.audio_bitrate.findData(audio_bitrate_value)
        if audio_bitrate_index >= 0:
            self.audio_bitrate.setCurrentIndex(audio_bitrate_index)
        self._refresh_audio_profile_context()
        selected_track_id = getattr(source, "selected_track_id", None)
        if selected_track_id is None and isinstance(source, dict):
            selected_track_id = source.get("selected_track_id")
        selected_track_ids = getattr(source, "selected_track_ids", None)
        if selected_track_ids is None and isinstance(source, dict):
            selected_track_ids = source.get("selected_track_ids")
        audio_track_policy = getattr(source, "audio_track_policy", None)
        if audio_track_policy is None and isinstance(source, dict):
            audio_track_policy = source.get("audio_track_policy")
        audio_track_policy = str(audio_track_policy or "KEEP_DEFAULT_ONLY").strip().upper()
        if audio_track_policy == "KEEP_ALL":
            ids_to_check = {idx for idx, _stream in enumerate(getattr(self, "_audio_profile_streams", ()) or ())}
        elif isinstance(selected_track_ids, (list, tuple, set)) and selected_track_ids:
            ids_to_check = {int(value) for value in selected_track_ids if str(value).strip() not in {"", "None"}}
        elif audio_track_policy == "SELECTED_ONLY" and selected_track_id is not None:
            try:
                ids_to_check = {int(selected_track_id)}
            except Exception:
                ids_to_check = {0}
        else:
            ids_to_check = {0}
        selector = getattr(self, "audio_track_selector", None)
        if selector is not None:
            selector.setCheckedValues(sorted(ids_to_check))
            self._sync_audio_bitrate_to_detected_track()
            self._sync_audio_bitrate_to_effective_cap()
        audio_channel_policy_value = getattr(source, "audio_channel_policy", None)
        if audio_channel_policy_value is None and isinstance(source, dict):
            audio_channel_policy_value = source.get("audio_channel_policy")
        audio_channel_policy_value = str(audio_channel_policy_value or "KEEP_ORIGINAL").strip().upper()
        audio_channel_index = self.audio_channel_policy.findData(audio_channel_policy_value)
        if audio_channel_index >= 0:
            self.audio_channel_policy.setCurrentIndex(audio_channel_index)
        audio_quality_mode_value = getattr(source, "audio_quality_mode", None)
        if audio_quality_mode_value is None and isinstance(source, dict):
            audio_quality_mode_value = source.get("audio_quality_mode")
        audio_quality_mode_value = str(audio_quality_mode_value or "BALANCED").strip().upper()
        audio_quality_mode_index = self.audio_quality_mode.findData(audio_quality_mode_value)
        if audio_quality_mode_index >= 0:
            self.audio_quality_mode.setCurrentIndex(audio_quality_mode_index)
        volume_normalization_value = getattr(source, "audio_volume_normalization", None)
        if volume_normalization_value is None and isinstance(source, dict):
            volume_normalization_value = source.get("audio_volume_normalization")
        volume_normalization_value = str(volume_normalization_value or "OFF").strip().upper()
        volume_normalization_index = self.audio_volume_normalization.findData(volume_normalization_value)
        if volume_normalization_index >= 0:
            self.audio_volume_normalization.setCurrentIndex(volume_normalization_index)
            if hasattr(self, "audio_volume_segmented"):
                self.audio_volume_segmented.setCurrentData(self.audio_volume_normalization.currentData())
        metadata_policy_value = getattr(source, "audio_metadata_policy", None)
        if metadata_policy_value is None and isinstance(source, dict):
            metadata_policy_value = source.get("audio_metadata_policy")
        metadata_policy_value = str(metadata_policy_value or "PRESERVE").strip().upper()
        metadata_policy_index = self.audio_metadata_policy.findData(metadata_policy_value)
        if metadata_policy_index >= 0:
            self.audio_metadata_policy.setCurrentIndex(metadata_policy_index)
        width = getattr(source, "advanced_width", None)
        if width is None and isinstance(source, dict):
            width = source.get("advanced_width")
        height = getattr(source, "advanced_height", None)
        if height is None and isinstance(source, dict):
            height = source.get("advanced_height")
        source_width, source_height = self._source_resolution()
        try:
            width = int(float(width))
        except Exception:
            width = source_width or 1920
        try:
            height = int(float(height))
        except Exception:
            height = source_height or 1080
        self.advanced_width.setValue(max(2, width))
        self.advanced_height.setValue(max(2, height))
        self._sync_advanced_resolution_bounds()
        self._advanced_last_driver = "target_size_mb"
        self._advanced_last_resolution_driver = None
        self._recalculate_advanced_targets_from_driver("target_size_mb")
    def _set_quick_estimate_pending_placeholders(self):
        """Show a lightweight pending state before quick estimate recalculation.

        This updates both the overlay summary and the file card model.  The
        card presenter intentionally falls back to a lightweight estimate when
        estimated values are None, so a dedicated pending flag is required to
        make the transient "--" state visible outside the overlay.
        """
        try:
            index = self._quick_preset_index
            job = self._context_job
            if job is not None:
                values = self.collect_values()
                for key, value in values.items():
                    setattr(job, key, value)
                setattr(job, "quick_estimate_pending", True)
                setattr(job, "estimated_size_bytes", None)
                setattr(job, "estimated_output_bitrate", None)
                setattr(job, "current_profile_signature", build_profile_signature(job))
                if not profile_output_matches_current_settings(job):
                    setattr(job, "output_size_bytes", None)
                    setattr(job, "output_bitrate", None)
                event_bridge.emit("job_updated", {"job": job})
            if job is None:
                self.quick_result_summary.setText(
                    "<div style='line-height:125%;'>"
                    f"<b>Modo rápido:</b> {index + 1}/{len(self.QUICK_PRESETS)}"
                    "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                    "<b>Resultado estimado:</b> —"
                    "<br>"
                    "<b>Resolução</b>: —"
                    "&nbsp;&nbsp;|&nbsp;&nbsp;"
                    "<b>FPS</b>: —"
                    "&nbsp;&nbsp;|&nbsp;&nbsp;"
                    "<b>Bitrate</b>: —"
                    "</div>"
                )
            else:
                input_size = _format_bytes(self._source_size_bytes())
                input_resolution = str(getattr(job, "resolution", "?"))
                input_fps = str(getattr(job, "fps", "?"))
                input_bitrate = _format_bitrate(self._source_input_bitrate_bps())
                size_line = _summary_pair("Resultado estimado", input_size, "--")
                detail_parts = [
                    _summary_pair("Resolução", input_resolution, "--", bold_label=True),
                    _summary_pair("FPS", input_fps, "--", bold_label=True),
                    _summary_pair("Bitrate", input_bitrate, "--", bold_label=True),
                ]
                self.quick_result_summary.setText(
                    "<div style='line-height:125%;'>"
                    f"<b>Modo rápido:</b> {index + 1}/{len(self.QUICK_PRESETS)}"
                    "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                    f"{size_line}"
                    "<br>"
                    f"{'&nbsp;&nbsp;|&nbsp;&nbsp;'.join(detail_parts)}"
                    "</div>"
                )
            self.quick_result_summary.update()
        except Exception:
            pass

    def _flush_quick_selection_visuals(self):
        """Force the quick selector/card to repaint before preview estimation starts."""
        try:
            for button in self._quick_level_buttons:
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()
            self.quick_selected_line.update()
            if getattr(self, "quick_quality_drop_warning", None) is not None:
                self.quick_quality_drop_warning.update()
            self.quick_result_summary.update()
            self.update()
            QApplication.processEvents()
        except Exception:
            pass

    def _set_quick_preset_index(self, index: int, *, update_preview: bool = True):
        index = max(0, min(int(index), len(self.QUICK_PRESETS) - 1))
        self._quick_preset_index = index
        for button_index, button in enumerate(self._quick_level_buttons):
            button.blockSignals(True)
            button.setChecked(button_index == index)
            button.setDown(False)
            button.blockSignals(False)
        if update_preview:
            self._update_quick_preset_labels()
        else:
            # Atualiza feedback visual imediato e invalida os valores estimados
            # antes de agendar o recálculo pesado.
            self._update_quick_selection_text_only()
            self._set_quick_estimate_pending_placeholders()
            self._flush_quick_selection_visuals()

    def _schedule_quick_preset_apply(self):
        if self._quick_preset_apply_timer.isActive():
            self._quick_preset_apply_timer.stop()
        self._quick_preset_apply_timer.start(85)

    def _on_quick_level_button_clicked(self, index: int):
        self._set_quick_preset_index(index, update_preview=False)
        self._schedule_quick_preset_apply()

    def _on_quick_extreme_step(self, delta: int):
        current_index = self._quick_preset_index
        next_index = max(0, min(current_index + int(delta), len(self.QUICK_PRESETS) - 1))
        if next_index == current_index:
            return
        self._set_quick_preset_index(next_index, update_preview=False)
        self._schedule_quick_preset_apply()
    @staticmethod
    def _values_differ_for_job(job, values: dict) -> bool:
        if job is None or not isinstance(values, dict):
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

        for key, value in values.items():
            if _normalize(getattr(job, key, None)) != _normalize(value):
                return True
        return False

    def _reset_cancelled_context_if_configuration_values_changed(self) -> None:
        job = self._context_job
        if (
            job is None
            or self._suppress_live_updates
            or str(getattr(job, "status", "") or "").upper() != "CANCELLED"
        ):
            return
        try:
            values = self.collect_values()
            values_changed = self._values_differ_for_job(job, values)
        except Exception:
            values_changed = False
        if not values_changed:
            return
        setattr(job, "status", "READY")
        setattr(job, "progress", 0)
        setattr(job, "error", None)
        event_bridge.emit("job_updated", {"job": job})

    def _sync_profile_preview_to_context(self, *, emit_apply: bool = False):
        job = self._context_job
        if job is None:
            return
        try:
            previous_signature = build_profile_signature(job)
            values = self.collect_values()
            values_changed = self._values_differ_for_job(job, values)
            for key, value in values.items():
                setattr(job, key, value)
            profile_changed = tuple(previous_signature) != tuple(build_profile_signature(job))
            profile_mode = str(values.get("profile_mode") or "quick").strip().lower()
            estimated_size_bytes = None
            estimated_output_bitrate = None
            if profile_mode == "quick":
                # Troca para Rápido deve recalcular pelo preview próprio do Rápido,
                # mas sem chamar a estimativa pesada por CRF/amostra. Essa chamada
                # dispara FFmpeg de forma síncrona e causa delay ao entrar no Rápido.
                # Também não podemos usar _quick_preview_metrics(allow_heavy=False),
                # pois ele reaproveita métricas antigas do job e mantém valores do
                # perfil anterior no card.
                estimated_size_bytes, estimated_output_bitrate, _, _ = self._quick_lightweight_preview_metrics()
                self._quick_preview_cache_key = None
                self._quick_preview_cache_value = None
            elif profile_mode == "smart":
                # Estratégico não pode manter a estimativa do perfil anterior no card.
                # Após o isolamento em módulos, o roteador continua sendo a fonte única
                # para estimativa, então o preview deve passar por ele explicitamente.
                estimated_size_bytes = estimate_size_for_profile(job, estimate_size_crf)
                estimated_output_bitrate = estimate_output_bitrate_for_profile(job, estimated_size_bytes)
            elif profile_mode == "advanced":
                estimated_size_bytes = advanced_profile.estimate_size_bytes(job)
                estimated_output_bitrate = advanced_profile.estimate_total_bitrate_bps(job)
            elif profile_mode == "audio":
                estimated_size_bytes = estimate_size_for_profile(job, estimate_size_crf)
                # Store the effective audio target as the visible bitrate source
                # for Audio jobs. Size estimation may account for duration and
                # container overhead, but displayed bitrate must remain the
                # profile's effective target.
                estimated_output_bitrate = audio_profile._effective_audio_bitrate_bps(job)
            # Atualiza sempre, inclusive com None, para limpar estimativas antigas
            # quando a nova configuração ainda não tem cálculo disponível.
            setattr(job, "quick_estimate_pending", False)
            setattr(job, "estimated_size_bytes", estimated_size_bytes)
            setattr(job, "estimated_output_bitrate", estimated_output_bitrate)
            setattr(job, "current_profile_signature", build_profile_signature(job))

            current_status = str(getattr(job, "status", "") or "").strip().upper()
            if (not self._suppress_live_updates) and values_changed:
                if self._is_reprocessable_terminal_status(job):
                    setattr(job, "status", "READY")
                    setattr(job, "progress", 0)
                    setattr(job, "error", None)
                    current_status = "READY"
                elif current_status == "NO_GAIN":
                    source_size = resolve_job_source_size_bytes(job)
                    actionable = estimate_output_is_actionable(
                        job,
                        estimated_size_bytes=estimated_size_bytes,
                        source_size_bytes=source_size,
                    )
                    if actionable is not False:
                        setattr(job, "status", "READY")
                        setattr(job, "progress", 0)
                        setattr(job, "error", None)

            if not profile_output_matches_current_settings(job):
                setattr(job, "output_size_bytes", None)
                setattr(job, "output_bitrate", None)
        except Exception:
            return
        event_bridge.emit("job_updated", {"job": job})
        if emit_apply:
            self.applyRequested.emit()
    def collect_values(self):
        mode_name = self._current_mode_name()
        if mode_name == "smart":
            strategy = self._selected_smart_strategy()
            slider_value = self._slider_value()
            intensity = self._effective_smart_intensity()
            _, selected_track_id = self._current_audio_track_selection()
            return {
                "profile_mode": "smart",
                "profile_label": f"Estratégico/{self._display_smart_strategy(strategy)}",
                "control_mode": "STRATEGY",
                "strategy_type": strategy,
                "slider_value": slider_value,
                "smart_strategy": strategy,
                "smart_intensity": intensity,
                "audio_track_policy": self._current_audio_track_policy(),
                "selected_track_id": selected_track_id if self._current_audio_track_policy() == "SELECTED_ONLY" else None,
                "audio_channel_policy": self._current_audio_channel_policy(),
                "allow_audio_quality_reduction": self._audio_quality_reduction_allowed(),
                "quick_profile_preset": None,
                "video_output_format": self._current_video_output_format().upper(),
                "video_output_extension": self._current_video_output_extension(),
            }
        if mode_name == "audio":
            track_policy, selected_track_id = self._selected_audio_profile_track()
            selected_track_ids = self._selected_audio_profile_track_ids()
            output_format = str(self.audio_output_format.currentData() or audio_profile.AUDIO_DEFAULT_OUTPUT_FORMAT)
            selected_bitrate = int(self.audio_bitrate.currentData() or 128)
            output_bitrate = self._effective_audio_profile_bitrate_kbps(selected_bitrate)
            return {
                "profile_mode": "audio",
                "profile_label": f"Áudio/{audio_profile.display_label_for_format(output_format)} {output_bitrate} kbps",
                "control_mode": "audio",
                "audio_output_format": output_format,
                "audio_output_extension": audio_profile.extension_for_format(output_format),
                "audio_bitrate_kbps": selected_bitrate,
                "audio_track_policy": track_policy,
                "selected_track_id": selected_track_id if track_policy == "SELECTED_ONLY" else None,
                "selected_track_ids": selected_track_ids if track_policy == "SELECTED_ONLY" else None,
                "audio_channel_policy": self.audio_channel_policy.currentData() or "KEEP_ORIGINAL",
                "audio_sample_rate": "ORIGINAL",
                "audio_quality_mode": self.audio_quality_mode.currentData() or "BALANCED",
                "audio_volume_normalization": self.audio_volume_normalization.currentData() or "OFF",
                "audio_metadata_policy": self.audio_metadata_policy.currentData() or "PRESERVE",
                "extract_subtitle": False,
                "quick_profile_preset": None,
            }
        if mode_name == "advanced":
            manual_type = self.advanced_target_mode.currentData() or "TARGET_SIZE"
            width, height = self._resolved_advanced_resolution()
            desired_size_mb = max(0.1, float(self.advanced_target_size_mb.value()))
            estimated_size_mb = max(0.1, float(self._advanced_estimated_size_mb or desired_size_mb))
            base = {
                "profile_mode": "advanced",
                "control_mode": "MANUAL_LEVEL",
                "manual_control_type": manual_type,
                "advanced_target_mode": manual_type,
                "advanced_size_strategy": {
                    "DIRECT_BITRATE": "BITRATE_PRIORITY",
                    "TARGET_SIZE": "BALANCED",
                    "RESOLUTION_DRIVEN": "RESOLUTION_PRIORITY",
                }.get(manual_type, "BALANCED"),
                "advanced_target_mode_name": "size",
                "advanced_width": width,
                "advanced_height": height,
                "advanced_resolution_lock": True,
                "advanced_resolution_bitrate_mode": self.advanced_resolution_bitrate_mode.currentData() or "auto",
                "advanced_resolution_bitrate_kbps": None,
                "advanced_fps_policy": self.advanced_fps_policy.currentData() or "keep",
                "advanced_fps_value": self.advanced_fps_policy.currentValue(),
                "advanced_audio_policy": self.advanced_audio_policy.currentData() or "keep",
                "advanced_audio_bitrate_kbps": self.advanced_audio_policy.currentValue(),
                "allow_audio_quality_reduction": True,
                "audio_track_policy": getattr(self._context_job, "audio_track_policy", "KEEP_ALL") if self._context_job is not None else "KEEP_ALL",
                "selected_track_id": getattr(self._context_job, "selected_track_id", None) if self._context_job is not None else None,
                "audio_channel_policy": getattr(self._context_job, "audio_channel_policy", "KEEP_ORIGINAL") if self._context_job is not None else "KEEP_ORIGINAL",
                "quick_profile_preset": None,
                "video_output_format": self._current_video_output_format().upper(),
                "video_output_extension": self._current_video_output_extension(),
            }
            try:
                preview_job = self._build_advanced_preview_job()
                setattr(preview_job, "advanced_target_size_mb", desired_size_mb)
                target_total_bps = advanced_profile.estimate_total_bitrate_bps(preview_job)
                effective_bitrate = max(1, int(round(target_total_bps / 1000.0))) if target_total_bps else self._advanced_effective_bitrate_kbps()
            except Exception:
                effective_bitrate = self._advanced_effective_bitrate_kbps()
            if manual_type == "DIRECT_BITRATE":
                bitrate = max(1, self.advanced_target_bitrate_kbps.value())
                base.update({
                    "profile_label": f"Avançado/Priorizar bitrate {_format_mb_value(desired_size_mb)} MB",
                    # Em Priorizar bitrate, o contrato deve preservar o valor
                    # escolhido no controle visível. A estimativa total continua
                    # derivada do tamanho alvo, mas não deve regravar o campo
                    # Taxa de bits com o bitrate total calculado.
                    "advanced_target_bitrate_kbps": bitrate,
                    "advanced_target_size_mb": desired_size_mb,
                    "advanced_estimated_size_mb": desired_size_mb,
                })
                return base
            if manual_type == "RESOLUTION_DRIVEN":
                bitrate_mode = self.advanced_resolution_bitrate_mode.currentData() or "auto"
                bitrate = max(1, self.advanced_resolution_bitrate_kbps.value()) if bitrate_mode == "explicit" else effective_bitrate
                label = f"{width}x{height}" if width and height else "original"
                base.update({
                    "profile_label": f"Avançado/Priorizar resolução {_format_mb_value(desired_size_mb)} MB",
                    "advanced_target_bitrate_kbps": effective_bitrate,
                    "advanced_target_size_mb": desired_size_mb,
                    "advanced_estimated_size_mb": desired_size_mb,
                    "advanced_resolution_bitrate_mode": bitrate_mode,
                    "advanced_resolution_bitrate_kbps": effective_bitrate if bitrate_mode == "explicit" else None,
                })
                return base
            base.update({
                "profile_label": f"Avançado/{_format_mb_value(desired_size_mb)} MB",
                "advanced_target_size_mb": desired_size_mb,
                "advanced_estimated_size_mb": desired_size_mb,
                "advanced_target_bitrate_kbps": effective_bitrate,
            })
            return base
        return {
            "profile_mode": "quick",
            "profile_label": f"Rápido/{quick_preset_title(self._quick_preset_index)}",
            "quick_profile_preset": self._quick_preset_index,
            "allow_audio_quality_reduction": self._audio_quality_reduction_allowed(),
            "video_output_format": self._current_video_output_format().upper(),
            "video_output_extension": self._current_video_output_extension(),
        }
    def _on_apply_clicked(self):
        self.applyRequested.emit()
    def restore_defaults(self):
        if self._context_job is None:
            defaults = AppConfiguration()
            self._set_quick_preset_index(defaults.quick_profile_preset)
            self._set_mode("quick")
            self._sync_analyze_media_button_state()
        else:
            mode_name = self._current_mode_name()
            if mode_name == "smart":
                smart_defaults = default_smart_profile()
                self._set_profile_values(smart_defaults)
                self._reset_smart_profile_ui_defaults(smart_defaults)
                self._set_mode("smart")
            elif mode_name == "advanced":
                self._set_profile_values(default_advanced_size_profile())
                self._set_mode("advanced")
            elif mode_name == "audio":
                self._set_profile_values(default_audio_profile())
                self._set_mode("audio")
            else:
                self._set_profile_values(default_quick_profile())
                self._set_mode("quick")
        self._update_quick_preset_labels()
        self._update_smart_summary()
        self._update_advanced_controls()
