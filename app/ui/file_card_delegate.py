import os
from collections import OrderedDict

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QBrush, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QStyle, QStyledItemDelegate
from PySide6.QtSvg import QSvgRenderer

from app.ui.assets import icon as asset_icon

from app.ui.file_card_presenter import build_view_data, resolve_playable_output_path
from app.ui.theme_tokens import (
    build_theme_tokens,
    resolve_status_progress_text_color,
)


class ThumbCache(OrderedDict):
    MAX_ITEMS = 256

    def __setitem__(self, key, value):
        if key in self:
            super().__delitem__(key)
        elif len(self) >= self.MAX_ITEMS:
            self.popitem(last=False)
        super().__setitem__(key, value)


THUMB_CACHE = ThumbCache()


def _blend_qcolor(base: QColor, target: QColor, factor: float) -> QColor:
    factor = max(0.0, min(1.0, float(factor)))
    inv = 1.0 - factor
    return QColor(
        int(round(base.red() * inv + target.red() * factor)),
        int(round(base.green() * inv + target.green() * factor)),
        int(round(base.blue() * inv + target.blue() * factor)),
        int(round(base.alpha() * inv + target.alpha() * factor)),
    )

def _compact_audio_parts(size_text, bitrate_text, channels_text, sample_rate_text):
    parts = []
    for value in (size_text, bitrate_text, channels_text, sample_rate_text):
        value_text = str(value).strip() if value not in (None, "") else ""
        if value_text:
            parts.append(value_text)
    return parts


def _audio_output_summary(view_data):
    input_parts = _compact_audio_parts(
        getattr(view_data, "input_size_text", None),
        getattr(view_data, "input_bitrate_text", None),
        getattr(view_data, "input_channels_text", None),
        getattr(view_data, "input_sample_rate_text", None),
    )
    output_parts = _compact_audio_parts(
        getattr(view_data, "output_size_text", None),
        getattr(view_data, "output_bitrate_text", None),
        getattr(view_data, "output_channels_text", None),
        getattr(view_data, "output_sample_rate_text", None),
    )
    if not output_parts:
        return [("Saída: ", "sem alterações")]
    if input_parts == output_parts:
        return [("Saída: ", "sem alterações")]
    return [("Saída: ", output_parts[0])] + [("", part) for part in output_parts[1:]]


def _format_video_resolution(value):
    value_text = str(value).strip() if value not in (None, "") else ""
    if not value_text or value_text == "?":
        return ""
    normalized = value_text.lower()
    if normalized.endswith(" px") or normalized.endswith("px"):
        return value_text
    return f"{value_text} px"


def _format_video_fps(value):
    value_text = str(value).strip() if value not in (None, "") else ""
    if not value_text or value_text == "?":
        return ""
    normalized = value_text.lower()
    if normalized.endswith(" fps") or normalized.endswith("fps"):
        return value_text
    return f"{value_text} fps"


def _compact_video_parts(size_text, resolution_text, fps_text, bitrate_text):
    parts = []
    formatted_values = (
        size_text,
        _format_video_resolution(resolution_text),
        _format_video_fps(fps_text),
        bitrate_text,
    )
    for value in formatted_values:
        value_text = str(value).strip() if value not in (None, "") else ""
        if value_text:
            parts.append(value_text)
    return parts


def _video_output_summary(view_data):
    input_parts = _compact_video_parts(
        getattr(view_data, "input_size_text", None),
        getattr(view_data, "resolution", None),
        getattr(view_data, "fps", None),
        getattr(view_data, "input_bitrate_text", None),
    )
    output_parts = _compact_video_parts(
        getattr(view_data, "output_size_text", None),
        getattr(view_data, "output_resolution", None),
        getattr(view_data, "output_fps", None),
        getattr(view_data, "output_bitrate_text", None),
    )
    if not output_parts:
        return [("Saída: ", "sem alterações")]
    if input_parts == output_parts:
        return [("Saída: ", "sem alterações")]
    return [("Saída: ", output_parts[0])] + [("", part) for part in output_parts[1:]]


class FileCardDelegate(QStyledItemDelegate):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._close_pen_color = QColor()
        self._header_icon_renderers = {
            "open_folder": QSvgRenderer(asset_icon("folder_open_bold.svg")),
            "replace_folder": QSvgRenderer(asset_icon("refresh_cw_outline.svg")),
            "settings": QSvgRenderer(asset_icon("settings_gear_outline.svg")),
            "run_play": QSvgRenderer(asset_icon("play_outline.svg")),
        }

    ROLE_JOB = Qt.UserRole + 1
    ROLE_JOB_REF = Qt.UserRole + 7

    THUMB_WIDTH = 128
    ACTION_WIDTH = 140

    HEADER_HEIGHT = 26
    ROW_HEIGHT = 24
    PROGRESS_HEIGHT = 18

    CARD_HEIGHT = 96
    # Header utility actions must read as secondary metadata controls.
    # Keep their hit area usable while making the glyphs less dominant than
    # the primary Comprimir action.
    HEADER_ICON_SIZE = 18
    HEADER_ICON_GAP = 10
    ACTION_BUTTON_INSET = 12
    ACTION_DIVIDER_GAP = 10

    def sizeHint(self, option, index):
        return QSize(0, self.CARD_HEIGHT)

    def get_action_rects(self, option, index):

        rect = option.rect.adjusted(8, 6, -8, -6)
        action_panel_left = rect.right() - self.ACTION_WIDTH + 10
        action_panel_right = option.rect.right()
        button_left = action_panel_left
        button_width = max(60, action_panel_right - button_left + 1)
        divider_x = button_left - 10

        close = QRect(
            option.rect.right() - 26,
            rect.top() + (self.HEADER_HEIGHT - 22) // 2,
            22,
            20,
        )

        cell_group_top = rect.top() + self.HEADER_HEIGHT - 1
        cell_group_bottom = option.rect.bottom() - 1
        cell_group_height = max(42, cell_group_bottom - cell_group_top + 1)
        # Keep the two stacked action cells visually identical in height.
        # The previous split gave the lower cell the leftover pixel(s), making
        # "Comprimir" look slightly taller than the profile cell.
        action_cell_height = max(20, cell_group_height // 2)
        bottom_top = cell_group_top + action_cell_height

        settings = QRect(button_left, cell_group_top, button_width, action_cell_height)
        progress = QRect(button_left, bottom_top, button_width, action_cell_height)

        destination_button_size = self.ROW_HEIGHT + 2
        destination_button_gap = 1
        destination_button_top = progress.center().y() - (destination_button_size // 2) - 2
        replace_folder = QRect(
            divider_x + 5 - destination_button_size,
            destination_button_top,
            destination_button_size,
            destination_button_size,
        )
        open_folder = QRect(
            replace_folder.left() - destination_button_gap - destination_button_size,
            destination_button_top,
            destination_button_size,
            destination_button_size,
        )

        run = QRect(progress)

        header = QRect(
            rect.left() + self.THUMB_WIDTH + 4,
            rect.top() + 1,
            rect.right() - (rect.left() + self.THUMB_WIDTH + 4) + 1,
            self.HEADER_HEIGHT,
        )
        thumbnail = QRect(
            rect.left() + 1,
            rect.top() + 1,
            self.THUMB_WIDTH - 2,
            rect.height() - 2,
        )

        return {
            "open_folder": open_folder,
            "replace_folder": replace_folder,
            "settings": settings,
            "remove": close,
            "run": run,
            "progress": progress,
            "divider": QRect(divider_x, rect.top() + 4, 1, rect.height() - 8),
            "header": header,
            "thumbnail": thumbnail,
        }

    def draw_close(self, painter, rect):

        cx = rect.center().x() + 1
        cy = rect.center().y() + 1

        size = 5.5

        pen_color = QColor(self._close_pen_color)
        pen = QPen(pen_color, 1.35, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        painter.drawLine(cx - size, cy - size, cx + size, cy + size)
        painter.drawLine(cx + size, cy - size, cx - size, cy + size)



    def _header_tool_icon_color(self, tokens, *, hovered: bool = False) -> QColor:
        # The folder/refresh tools are secondary actions in the duration row.
        # Normal state is neutral so they do not compete with the primary action;
        # hover may recover a restrained accent cue for discoverability.
        color = QColor(tokens.text_linkish if hovered else tokens.surface_card_header_text)
        color.setAlpha(215 if hovered else 150)
        return color

    def _header_remove_icon_color(self, tokens) -> QColor:
        color = QColor(tokens.surface_card_header_text)
        color.setAlpha(165)
        return color


    def _draw_svg_header_icon(self, painter: QPainter, rect: QRect, tokens, icon_name: str, *, hovered: bool = False) -> None:
        self._draw_svg_header_icon_with_color(
            painter,
            rect,
            self._header_tool_icon_color(tokens, hovered=hovered),
            icon_name,
        )


    def _draw_svg_header_icon_with_color(self, painter: QPainter, rect: QRect, color, icon_name: str) -> None:
        renderer = self._header_icon_renderers[icon_name]
        icon_rect = QRectF(rect.adjusted(-1, -1, 1, 1))
        pixmap = QPixmap(int(icon_rect.width()), int(icon_rect.height()))
        pixmap.fill(Qt.transparent)

        icon_painter = QPainter(pixmap)
        icon_painter.setRenderHint(QPainter.Antialiasing, True)
        renderer.render(icon_painter)
        icon_painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        icon_painter.fillRect(pixmap.rect(), color)
        icon_painter.end()

        painter.drawPixmap(int(icon_rect.left()), int(icon_rect.top()), pixmap)

    def _draw_run_play_icon(self, painter: QPainter, rect: QRect, tokens, *, icon_color=None) -> None:
        # O botão de ação precisa sempre exibir a seta/play.
        # Desenhar a seta via geometria evita regressões quando o SVG/renderer
        # fica indisponível, cacheado ou afetado por mudanças de alinhamento.
        icon_rect = QRectF(rect.adjusted(0, 0, 0, 0))
        center_y = icon_rect.center().y()
        left = icon_rect.left() + 3.0
        right = icon_rect.right() - 2.0
        top = center_y - 4.5
        bottom = center_y + 4.5

        path = QPainterPath()
        path.moveTo(left, top)
        path.lineTo(right, center_y)
        path.lineTo(left, bottom)
        path.closeSubpath()

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(icon_color or tokens.text_primary)
        painter.drawPath(path)
        painter.restore()

    def _draw_queue_remove_x_icon(self, painter: QPainter, rect: QRect, tokens, *, icon_color=None) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(icon_color or tokens.text_primary)
        pen = QPen(color, 1.55, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        inset = 2
        painter.drawLine(rect.left() + inset, rect.top() + inset, rect.right() - inset, rect.bottom() - inset)
        painter.drawLine(rect.left() + inset, rect.bottom() - inset, rect.right() - inset, rect.top() + inset)
        painter.restore()

    def _draw_button_content(
        self,
        painter: QPainter,
        rect: QRect,
        tokens,
        text_value: str,
        font,
        *,
        leading_icon_name: str | None = None,
        text_color=None,
        icon_color=None,
    ) -> None:
        painter.setPen(text_color or tokens.text_primary)
        painter.setFont(font)
        content_rect = rect.adjusted(6, 0, -6, 0)

        icon_size = 12
        icon_gap = 12
        icon_space = 0
        if leading_icon_name:
            icon_space = icon_size + icon_gap

        display = painter.fontMetrics().elidedText(
            text_value,
            Qt.ElideRight,
            max(10, content_rect.width() - icon_space),
        )

        if leading_icon_name:
            icon_left = content_rect.left() + 5
            icon_rect = QRect(
                icon_left,
                rect.top() + (rect.height() - icon_size) // 2,
                icon_size,
                icon_size,
            )
            if leading_icon_name == "settings":
                self._draw_svg_header_icon_with_color(
                    painter,
                    icon_rect,
                    icon_color or tokens.text_linkish,
                    leading_icon_name,
                )
            else:
                self._draw_svg_header_icon(painter, icon_rect, tokens, leading_icon_name)
            text_rect = content_rect
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignCenter, display)
        else:
            painter.drawText(content_rect, Qt.AlignCenter | Qt.AlignVCenter, display)

    def _draw_secondary_button(
        self,
        painter: QPainter,
        rect: QRect,
        tokens,
        text_value: str,
        font,
        *,
        hovered: bool = False,
        leading_icon_name: str | None = None,
    ) -> None:
        # Destination row actions are secondary inline actions inside the
        # destination group. In normal state they should not look like separate
        # push buttons; only hover gets a light capsule for affordance.
        paint_rect = rect.adjusted(0, 0, -1, -1)
        text_color = QColor(tokens.text_linkish)
        text_color = _blend_qcolor(text_color, QColor(tokens.text_secondary), 0.22)
        text_color.setAlpha(228)

        if hovered:
            fill = _blend_qcolor(QColor(tokens.surface_card), QColor(tokens.card_run_button_bg), 0.16)
            border = _blend_qcolor(QColor(tokens.border_card), QColor(tokens.card_run_button_border), 0.45)
            border.setAlpha(145)
            painter.setBrush(fill)
            painter.setPen(QPen(border, 1))
            painter.drawRoundedRect(paint_rect, 3, 3)
            text_color = QColor(tokens.text_linkish)
            text_color.setAlpha(235)

        self._draw_button_content(
            painter,
            paint_rect,
            tokens,
            text_value,
            font,
            leading_icon_name=leading_icon_name,
            text_color=text_color,
            icon_color=text_color,
        )

    def _draw_destination_icon_button(
        self,
        painter: QPainter,
        rect: QRect,
        tokens,
        icon_name: str,
        *,
        hovered: bool = False,
    ) -> None:
        paint_rect = rect.adjusted(0, 0, -1, -1)
        surface_card = QColor(tokens.surface_card)
        text_primary = QColor(tokens.text_primary)
        is_dark_theme = text_primary.lightness() > surface_card.lightness()

        # Keep the stronger blue treatment isolated to these two destination
        # buttons. Detect theme polarity from card/text contrast, not from
        # surface_main: the light Windows palette can produce a medium main
        # surface and route these buttons through the dark branch, which was the
        # reason the light theme kept showing a dark gray face.
        # For dark theme, anchor the accent directly to the normalized
        # blue hover token so the icons do not drift toward cyan/green.
        accent_seed = QColor(tokens.progress_fill)
        normalized_blue = QColor(tokens.border_card_hover)
        normalized_blue.setAlpha(255)
        accent = _blend_qcolor(
            accent_seed,
            normalized_blue,
            0.68 if is_dark_theme else 0.78,
        )
        accent.setAlpha(255)

        if is_dark_theme:
            # Dark theme only: keep the destination buttons on a neutral gray very
            # close to the app background, but raise border/icon clarity a bit more.
            # This branch is intentionally preserved from the prior accepted dark-theme state.
            dark_accent = _blend_qcolor(QColor(normalized_blue), QColor(tokens.progress_fill), 0.72)
            dark_accent.setAlpha(255)
            fill = _blend_qcolor(QColor(tokens.surface_main), QColor(tokens.surface_card), 0.12)
            border = QColor(dark_accent).lighter(122)
            icon_color = QColor(dark_accent).lighter(140)

            if hovered:
                fill = _blend_qcolor(fill, QColor(tokens.text_on_dark_surface), 0.06)
                border = _blend_qcolor(border, dark_accent, 0.98)
                icon_color = QColor(dark_accent).lighter(150)

            fill.setAlpha(255)
            border.setAlpha(228 if hovered else 208)
            icon_color.setAlpha(254 if hovered else 250)
            button_brush = QBrush(fill)
            button_radius = 0
        else:
            # Light theme only: force a clean light face for the compact destination
            # buttons. The print proved that leaving the row substrate or palette
            # blends visible makes the controls read as dark gray blocks. Keep this
            # local to open_folder/replace_folder and do not alter global tokens.
            button_face = QColor(255, 255, 255)
            border = _blend_qcolor(QColor(tokens.border_card), accent, 0.70)
            icon_color = _blend_qcolor(accent, QColor(tokens.progress_fill), 0.90)
            button_face.setAlpha(255)
            border.setAlpha(172)
            icon_color.setAlpha(252)
            button_brush = QBrush(button_face)
            button_radius = 0

            if hovered:
                hover_fill = QColor(tokens.card_destination_button_hover_bg)
                border = _blend_qcolor(border, accent, 0.84)
                border.setAlpha(190)
                icon_color = _blend_qcolor(accent, QColor(tokens.progress_fill), 0.94)
                icon_color.setAlpha(255)
                button_brush = QBrush(hover_fill)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not is_dark_theme:
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.setPen(Qt.NoPen)
            painter.setBrush(button_brush)
            painter.drawRect(paint_rect)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.setBrush(button_brush)
        painter.setPen(QPen(border, 1))
        if button_radius > 0:
            painter.drawRoundedRect(paint_rect, button_radius, button_radius)
        else:
            painter.drawRect(paint_rect)

        icon_size = max(12, min(15, paint_rect.height() - 9))
        icon_rect = QRect(
            paint_rect.center().x() - (icon_size // 2),
            paint_rect.center().y() - (icon_size // 2),
            icon_size,
            icon_size,
        )
        # Destination controls are icon-only. Do not route through text drawing
        # helpers here; compact hover must keep the same icon instead of showing
        # elided text such as "...".
        self._draw_svg_header_icon_with_color(painter, icon_rect, icon_color, icon_name)
        painter.restore()

    def _draw_action_cell(
        self,
        painter: QPainter,
        rect: QRect,
        tokens,
        *,
        fill_color,
        border_color,
        text_value: str,
        font,
        hovered: bool = False,
        pressed: bool = False,
        text_color=None,
        icon_color=None,
        progress_value: int | None = None,
        progress_fill=None,
    ) -> None:
        cell_rect = rect.adjusted(0, 0, -1, -1)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill_color)
        painter.drawRect(cell_rect)

        if progress_value is not None and progress_fill is not None:
            value = max(0, min(progress_value, 100))
            fill_width = int(cell_rect.width() * (value / 100))
            if fill_width > 0:
                fill_rect = QRect(cell_rect.left(), cell_rect.top(), fill_width, cell_rect.height())
                painter.setBrush(progress_fill)
                painter.drawRect(fill_rect)

        overlay = None
        if pressed:
            overlay = tokens.card_run_button_pressed_overlay
        elif hovered:
            overlay = tokens.card_run_button_hover_overlay
        if overlay is not None:
            painter.setBrush(overlay)
            painter.drawRect(cell_rect)

        # Draw the outline one pixel inside the painted cell. The action
        # column is intentionally flush with the card's right edge; drawing the
        # border exactly on that edge can clip the right/bottom stroke on some
        # Qt/Windows paint paths.
        border_rect = cell_rect.adjusted(0, 0, -1, -1)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border_color, 1))
        painter.drawRect(border_rect)
        if hovered:
            painter.setPen(QPen(tokens.card_run_button_hover_border, 1))
            painter.drawRect(border_rect)

        self._draw_button_content(
            painter,
            rect,
            tokens,
            text_value,
            font,
            text_color=text_color or tokens.text_secondary,
            icon_color=icon_color or text_color or tokens.text_secondary,
        )
        painter.restore()


    def _draw_thumbnail_play_overlay(self, painter: QPainter, thumb_media_rect: QRect) -> None:
        if thumb_media_rect.width() < 28 or thumb_media_rect.height() < 28:
            return

        diameter = max(34, min(58, int(min(thumb_media_rect.width(), thumb_media_rect.height()) * 0.52)))
        overlay_rect = QRect(
            thumb_media_rect.center().x() - (diameter // 2),
            thumb_media_rect.center().y() - (diameter // 2),
            diameter,
            diameter,
        )

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Make the affordance visible over both dark and bright thumbnails while
        # keeping it translucent enough to read as an overlay, not a solid badge.
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1.2))
        painter.setBrush(QColor(15, 23, 42, 178))
        painter.drawEllipse(overlay_rect)

        inner_glow = overlay_rect.adjusted(2, 2, -2, -2)
        painter.setPen(QPen(QColor(255, 255, 255, 48), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(inner_glow)

        triangle_w = max(13, int(diameter * 0.34))
        triangle_h = max(15, int(diameter * 0.42))
        triangle_left = overlay_rect.center().x() - (triangle_w // 3)
        triangle_top = overlay_rect.center().y() - (triangle_h // 2)

        play_path = QPainterPath()
        play_path.moveTo(triangle_left, triangle_top)
        play_path.lineTo(triangle_left, triangle_top + triangle_h)
        play_path.lineTo(triangle_left + triangle_w, overlay_rect.center().y())
        play_path.closeSubpath()

        painter.fillPath(play_path, QColor(255, 255, 255, 242))
        painter.restore()

    def paint(self, painter, option, index):

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        palette = option.palette
        tokens = build_theme_tokens(palette)

        card_rect = option.rect.adjusted(0, 1, 0, -1)
        rect = option.rect.adjusted(3, 4, -3, -6)

        actions = self.get_action_rects(option, index)

        job = index.data(self.ROLE_JOB)
        if not job:
            painter.restore()
            return

        view = option.widget

        view_data = build_view_data(job, option.palette)
        name = view_data.name
        resolution = view_data.resolution
        fps = view_data.fps
        duration = view_data.duration
        duration_value = str(duration).strip() if duration not in (None, "") else ""
        progress = view_data.progress
        dest = view_data.destination_dir
        thumb = view_data.thumbnail
        status_text = view_data.status_text
        raw_status = view_data.raw_status
        playable_output_path = resolve_playable_output_path(job)
        profile_mode = str(getattr(job, "profile_mode", "") or "").strip().lower()
        ready_action_text = "Gravar" if profile_mode == "audio" else "Comprimir"
        if isinstance(raw_status, str):
            raw_key = raw_status.strip().lower()
            if raw_key in {"ready", "idle", "pending"}:
                status_text = ready_action_text
            elif raw_key in {"done", "completed", "success", "finished"}:
                status_text = "Reproduzir" if playable_output_path else "Concluído"
        status_color = view_data.status_color
        profile_button_text = view_data.profile_button_text

        body_color = tokens.surface_card
        painter.fillRect(card_rect, body_color)
        painter.setPen(QPen(tokens.border_card, 1.2))
        painter.drawRect(card_rect.adjusted(0, 0, -1, -1))

        thumb_rect = QRect(
            rect.left() + 1,
            rect.top() + 1,
            self.THUMB_WIDTH - 2,
            rect.height() - 2,
        )
        thumb_strip_height = 11
        thumb_side_padding = 1
        thumb_top_padding = 1
        thumb_media_area_height = max(36, thumb_rect.height() - thumb_strip_height - thumb_top_padding)
        thumb_media_width = min(
            max(36, thumb_rect.width() - (thumb_side_padding * 2)),
            max(36, int(thumb_media_area_height * (16 / 9))),
        )
        thumb_media_height = max(36, int(round(thumb_media_width * (9 / 16))))
        thumb_media_left = thumb_rect.left() + ((thumb_rect.width() - thumb_media_width) // 2)
        thumb_media_top = thumb_rect.top() + thumb_top_padding
        thumb_media_rect = QRect(
            thumb_media_left,
            thumb_media_top,
            thumb_media_width,
            min(thumb_media_height, thumb_media_area_height),
        )
        thumb_strip_rect = QRect(
            thumb_rect.left(),
            thumb_rect.bottom() - thumb_strip_height + 1,
            thumb_rect.width(),
            thumb_strip_height,
        )

        painter.fillRect(thumb_rect, QColor(10, 10, 10))

        if thumb and os.path.exists(thumb):

            pix = THUMB_CACHE.get(thumb)

            if pix is None:
                pix = QPixmap()
                if pix.load(thumb):
                    THUMB_CACHE[thumb] = pix

            if pix and not pix.isNull():

                scaled = pix.scaled(
                    thumb_media_rect.size(),
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation,
                )

                painter.drawPixmap(thumb_media_rect, scaled)

        self._draw_thumbnail_play_overlay(painter, thumb_media_rect)

        painter.fillRect(thumb_strip_rect, QColor(0, 0, 0, 255))
        strip_divider = QColor(255, 255, 255, 22)
        painter.setPen(QPen(strip_divider, 1))
        painter.drawLine(
            thumb_strip_rect.left(),
            thumb_strip_rect.top(),
            thumb_strip_rect.right(),
            thumb_strip_rect.top(),
        )

        if duration_value:
            strip_font = painter.font()
            strip_font.setPointSize(max(8, strip_font.pointSize() - 1))
            painter.setFont(strip_font)
            strip_text_rect = thumb_strip_rect.adjusted(6, -1, -6, -2)
            strip_text_color = QColor(255, 255, 255, 240)
            painter.setPen(strip_text_color)
            painter.drawText(strip_text_rect, Qt.AlignRight | Qt.AlignVCenter, duration_value)
            painter.setFont(option.font)

        content_left = rect.left() + self.THUMB_WIDTH + 4
        thumb_separator = QColor(tokens.surface_list_divider)
        thumb_separator.setAlpha(105)
        painter.setPen(QPen(thumb_separator, 1))
        painter.drawLine(content_left - 1, rect.top() + 1, content_left - 1, rect.bottom() - 1)

        divider_rect = actions["divider"]
        content_right = divider_rect.left() - 10

        info_x = rect.left() + self.THUMB_WIDTH + 12
        info_width = max(40, content_right - info_x + 1)

        # Align the title band with the thumbnail top edge. The thumbnail starts
        # at rect.top() + 1 because the card border uses the outermost pixel;
        # keeping the header at rect.top() made the gray band look 1px too high.
        header_y = rect.top() + 1
        header_rect = QRect(
            content_left,
            header_y,
            rect.right() - content_left + 1,
            self.HEADER_HEIGHT,
        )

        # Premium stage 2 correction: keep the approved stage 1 layout, but
        # make the title band slightly lighter and less blocky. The previous
        # blend direction darkened the band on the active theme.
        header_color = QColor(tokens.surface_card_header).lighter(106)
        painter.fillRect(header_rect, header_color)

        self._close_pen_color = self._header_remove_icon_color(tokens)
        separator_color = QColor(tokens.surface_list_divider)
        separator_color.setAlpha(90)
        painter.setPen(QPen(separator_color, 1))
        painter.drawLine(header_rect.left(), header_rect.bottom(), header_rect.right(), header_rect.bottom())


        metrics = painter.fontMetrics()

        painter.setPen(tokens.surface_card_header_text)

        original_font = painter.font()
        font_bold = painter.font()
        font_bold.setBold(True)
        painter.setFont(font_bold)

        metrics = painter.fontMetrics()
        name_rect = QRect(info_x, header_y, max(40, content_right - info_x - 8), self.HEADER_HEIGHT)

        # Detect the generated suffix segment, e.g.:
        #   Source [_compressed].mp4
        # This must work even when the output container changes after switching
        # profiles, so detection belongs to the normalized display name, not the
        # physical extension comparison.
        suffix_start = name.rfind(" [")
        suffix_end = name.rfind("]")

        if suffix_start != -1 and suffix_end != -1 and suffix_end > suffix_start:
            base = name[:suffix_start + 1]
            suffix = name[suffix_start + 1 : suffix_end + 1]
            ext = name[suffix_end + 1 :]
            full_width = (
                metrics.horizontalAdvance(base)
                + metrics.horizontalAdvance(suffix)
                + metrics.horizontalAdvance(ext)
            )

            if full_width <= name_rect.width():
                x = name_rect.left()

                painter.setPen(tokens.surface_card_header_text)
                painter.drawText(
                    QRect(x, name_rect.top(), name_rect.width(), self.HEADER_HEIGHT),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    base,
                )

                x += metrics.horizontalAdvance(base)

                suffix_color = QColor(tokens.text_linkish)
                if QColor(tokens.surface_main).lightness() <= 128:
                    suffix_color = QColor("#B8E3FF")
                else:
                    suffix_color = QColor("#1E78D7")
                painter.setPen(suffix_color)
                painter.drawText(
                    QRect(x, name_rect.top(), max(0, name_rect.right() - x), self.HEADER_HEIGHT),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    suffix,
                )

                x += metrics.horizontalAdvance(suffix)

                painter.setPen(tokens.surface_card_header_text)
                painter.drawText(
                    QRect(x, name_rect.top(), max(0, name_rect.right() - x), self.HEADER_HEIGHT),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    ext,
                )
            else:
                painter.setPen(tokens.surface_card_header_text)
                painter.drawText(
                    name_rect,
                    Qt.AlignLeft | Qt.AlignVCenter,
                    metrics.elidedText(name, Qt.ElideRight, name_rect.width()),
                )
        else:
            painter.setPen(tokens.surface_card_header_text)
            painter.drawText(
                name_rect,
                Qt.AlignLeft | Qt.AlignVCenter,
                metrics.elidedText(name, Qt.ElideRight, name_rect.width()),
            )

        painter.setFont(original_font)

        settings_rect = actions["settings"]
        progress_rect = actions["progress"]

        metadata_rect = QRect(
            info_x,
            settings_rect.center().y() - (self.ROW_HEIGHT // 2),
            info_width,
            self.ROW_HEIGHT,
        )

        destination_band_gap = -2
        destination_group_left = info_x + destination_band_gap - 1
        destination_group_right = actions["divider"].left() + 5
        destination_group_y_offset = -2
        destination_group_rect = QRect(
            destination_group_left,
            progress_rect.center().y() - ((self.ROW_HEIGHT + 2) // 2) + destination_group_y_offset,
            max(80, destination_group_right - destination_group_left + 1),
            self.ROW_HEIGHT + 2,
        )
        destination_rect = QRect(
            destination_group_rect.left() + 8,
            progress_rect.center().y() - (self.ROW_HEIGHT // 2) + destination_group_y_offset,
            max(40, actions["open_folder"].left() - (destination_group_rect.left() + 8) - 6),
            self.ROW_HEIGHT,
        )

        input_size_text = getattr(view_data, "input_size_text", None)
        input_bitrate_text = getattr(view_data, "input_bitrate_text", None)
        is_audio = bool(getattr(view_data, "is_audio", False))
        if is_audio:
            input_values = _compact_audio_parts(
                input_size_text,
                input_bitrate_text,
                getattr(view_data, "input_channels_text", None),
                getattr(view_data, "input_sample_rate_text", None),
            )
        else:
            input_values = _compact_video_parts(
                input_size_text,
                resolution,
                fps,
                input_bitrate_text,
            )

        if is_audio:
            output_metadata_parts = _audio_output_summary(view_data)
        else:
            output_metadata_parts = _video_output_summary(view_data)
        output_values = [str(value).strip() for _, value in output_metadata_parts if str(value).strip()]

        font_normal = painter.font()
        font_bold = painter.font()
        font_bold.setBold(True)

        painter.setFont(font_bold)
        bold_fm = painter.fontMetrics()
        painter.setFont(font_normal)
        value_fm = painter.fontMetrics()

        def _split_value_unit(value: str) -> tuple[str, str]:
            value_text = str(value).strip()
            if not value_text:
                return "", ""
            chunks = value_text.rsplit(" ", 1)
            if len(chunks) == 2 and any(ch.isalpha() for ch in chunks[1]):
                return chunks[0], chunks[1]
            return value_text, ""

        def _numeric_text_equivalent(source: str, target: str) -> bool:
            source_value, source_unit = _split_value_unit(source)
            target_value, target_unit = _split_value_unit(target)
            if source_unit.lower() != target_unit.lower():
                return False
            try:
                return abs(float(source_value.replace(",", ".")) - float(target_value.replace(",", "."))) < 0.005
            except Exception:
                return False

        def _compact_pair(source: str, target: str, *, compare: bool = True) -> str:
            source = str(source).strip().replace("x", "×")
            target = str(target).strip().replace("x", "×")
            if not source:
                return ""
            if not target or target == "sem alterações":
                return source
            if not compare or source == target or _numeric_text_equivalent(source, target):
                return source

            source_value, source_unit = _split_value_unit(source)
            target_value, target_unit = _split_value_unit(target)
            if source_unit and target_unit and source_unit == target_unit:
                return f"{source_value} → {target_value} {source_unit}"
            return f"{source} → {target}"

        def _metric_segments(input_items: list[str], output_items: list[str]) -> list[tuple[str, str, bool]]:
            normalized_output = [
                str(value).strip()
                for value in output_items
                if str(value).strip()
            ]
            if is_audio:
                labels = ["Tamanho", "Bitrate", "Canais", "Taxa"]
                order = [0, 1, 2, 3]
                compare_columns = {0, 1, 2, 3}
            else:
                labels = ["Tamanho", "Bitrate", "Res.", "FPS"]
                order = [0, 3, 1, 2]
                compare_columns = {0, 3}

            segments = []
            for label, column_index in zip(labels, order):
                if column_index >= len(input_items):
                    continue
                target = (
                    normalized_output[column_index]
                    if column_index < len(normalized_output)
                    else ""
                )
                source_text = str(input_items[column_index]).strip().replace("x", "×")
                target_text = str(target).strip().replace("x", "×")
                compare = column_index in compare_columns or (
                    target_text not in ("", "sem alterações") and target_text != source_text
                )
                value = _compact_pair(input_items[column_index], target, compare=compare)
                if value:
                    segments.append((label, value, compare and "→" in value))
            return segments

        def _draw_metric_segments(row_rect: QRect, segments: list[tuple[str, str, bool]]) -> None:
            if not segments:
                painter.setFont(font_normal)
                painter.setPen(tokens.text_secondary)
                painter.drawText(row_rect, Qt.AlignLeft | Qt.AlignVCenter, "sem informações")
                return

            x_cursor = row_rect.left()
            label_value_gap = 5
            segment_gap = 22
            muted_color = QColor(tokens.text_secondary)
            muted_color.setAlpha(220)
            stable_color = QColor(tokens.text_secondary)
            stable_color.setAlpha(235)

            for segment_index, (label, value, changed) in enumerate(segments):
                label_text = f"{label}:"
                label_width = bold_fm.horizontalAdvance(label_text)
                value_width = value_fm.horizontalAdvance(value)
                required = label_width + label_value_gap + value_width
                if segment_index > 0:
                    required += segment_gap
                if x_cursor + required > row_rect.right() + 1:
                    break

                if segment_index > 0:
                    x_cursor += segment_gap

                painter.setFont(font_bold)
                painter.setPen(muted_color)
                painter.drawText(
                    QRect(x_cursor, row_rect.top(), label_width, row_rect.height()),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    label_text,
                )
                x_cursor += label_width + label_value_gap

                painter.setFont(font_normal)
                painter.setPen(tokens.text_primary if changed else stable_color)
                painter.drawText(
                    QRect(x_cursor, row_rect.top(), value_width, row_rect.height()),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    value,
                )
                x_cursor += value_width

        _draw_metric_segments(metadata_rect, _metric_segments(input_values, output_values))

        destination_text = str(dest).strip() if dest not in (None, "") else "Mesma pasta do arquivo"
        if isinstance(destination_text, str):
            destination_text = destination_text.replace("/", "\\")

        # Keep the destination row visually grouped, but lighter than a full
        # framed field. A soft background band works better than a bordered box.
        destination_is_dark = QColor(tokens.surface_main).lightness() <= 128
        destination_group_fill = _blend_qcolor(
            QColor(tokens.surface_card),
            QColor(tokens.card_run_button_bg),
            0.34 if destination_is_dark else 0.20,
        )
        painter.setBrush(destination_group_fill)
        painter.setPen(Qt.NoPen)
        painter.drawRect(destination_group_rect.adjusted(0, 0, -1, -1))

        destination_label_width = bold_fm.horizontalAdvance("Destino:") + 10
        painter.setFont(font_bold)
        painter.setPen(tokens.text_secondary)
        painter.drawText(
            QRect(destination_rect.left(), destination_rect.top(), destination_label_width, destination_rect.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            "Destino:",
        )

        destination_text_rect = QRect(
            destination_rect.left() + destination_label_width,
            destination_rect.top(),
            max(10, destination_rect.width() - destination_label_width),
            destination_rect.height(),
        )
        destination_color = QColor(tokens.text_secondary)
        destination_color.setAlpha(210)
        painter.setFont(font_normal)
        painter.setPen(destination_color)
        painter.drawText(
            destination_text_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            value_fm.elidedText(destination_text, Qt.ElideMiddle, destination_text_rect.width()),
        )

        painter.setPen(tokens.text_primary)

        original_font = painter.font()

        icon_font = painter.font()
        icon_font.setPointSize(icon_font.pointSize() + 2)

        run_font = painter.font()
        run_font.setPointSize(run_font.pointSize() - 1)

        tool_font = painter.font()
        tool_font.setPointSize(max(8, tool_font.pointSize() - 1))

        run_label = f"▶  {ready_action_text}"
        if raw_status in ("RUNNING", "PROCESSING", "QUEUED"):
            run_label = "■  Cancelar"

        def _action_hovered(action_name: str) -> bool:
            return bool(
                view
                and hasattr(view, "_hover_index")
                and view._hover_index == index
                and getattr(view, "_hover_action", None) == action_name
            )

        def _draw_action_glyph(action_name):
            painter.setPen(tokens.text_primary)
            if action_name == "open_folder":
                self._draw_destination_icon_button(
                    painter,
                    actions["open_folder"],
                    tokens,
                    "open_folder",
                    hovered=_action_hovered("open_folder"),
                )
            elif action_name == "replace_folder":
                self._draw_destination_icon_button(
                    painter,
                    actions["replace_folder"],
                    tokens,
                    "replace_folder",
                    hovered=_action_hovered("replace_folder"),
                )
            elif action_name == "settings":
                self._draw_secondary_button(
                    painter,
                    settings_paint_rect,
                    tokens,
                    profile_button_text,
                    tool_font,
                    hovered=False,
                    leading_icon_name="settings",
                )
            elif action_name == "remove":
                self.draw_close(painter, actions["remove"])
            elif action_name == "run":
                pass

        _draw_action_glyph("open_folder")
        _draw_action_glyph("replace_folder")

        settings_hovered = bool(
            view
            and hasattr(view, "_hover_index")
            and view._hover_index == index
            and getattr(view, "_hover_action", None) == "settings"
        )
        run_hovered = bool(
            view
            and hasattr(view, "_hover_index")
            and view._hover_index == index
            and getattr(view, "_hover_action", None) == "run"
        )
        run_pressed = bool(
            view
            and hasattr(view, "_pressed_index")
            and view._pressed_index == index
            and getattr(view, "_pressed_action", None) == "run"
        )

        is_dark_theme = QColor(tokens.surface_main).lightness() <= 128

        settings_paint_rect = QRect(actions["settings"])
        progress_paint_rect = QRect(actions["progress"])
        # Give the two stacked action cells a 2 px outer gap while keeping only
        # 1 px total between them. Do not inset both touching edges, otherwise
        # the internal separation becomes larger than requested.
        settings_paint_rect = settings_paint_rect.adjusted(2, 2, -2, 0)
        progress_paint_rect = progress_paint_rect.adjusted(2, 1, -2, -2)

        # Profile/status action cells consume ready-to-use semantic tokens.
        # The dark-theme profile button elevation is defined in theme_tokens.py
        # to avoid local color decisions inside the delegate.
        cell_border = QColor(tokens.card_profile_button_border)
        top_fill = QColor(tokens.card_profile_button_bg)

        profile_text_color = QColor(tokens.text_secondary)
        if is_dark_theme:
            profile_text_color = _blend_qcolor(
                QColor(tokens.text_primary),
                QColor(tokens.text_secondary),
                0.56,
            )
            profile_text_color.setAlpha(242)

        self._draw_action_cell(
            painter,
            settings_paint_rect,
            tokens,
            fill_color=top_fill,
            border_color=cell_border,
            text_value=profile_button_text,
            font=tool_font,
            hovered=settings_hovered,
            text_color=profile_text_color,
            icon_color=profile_text_color,
        )
        settings_icon_rect = QRect(
            settings_paint_rect.left() + 11,
            settings_paint_rect.top() + (settings_paint_rect.height() - 12) // 2,
            12,
            12,
        )
        self._draw_svg_header_icon_with_color(
            painter,
            settings_icon_rect,
            tokens.text_secondary,
            "settings",
        )

        painter.setFont(original_font)
        _draw_action_glyph("remove")

        progress_rect = progress_paint_rect
        display_text = status_text
        done_playable = bool(
            raw_status in ("DONE", "COMPLETED", "SUCCESS", "FINISHED")
            and playable_output_path
        )
        if raw_status in ("RUNNING", "PROCESSING"):
            # During active processing the bottom action cell must keep acting
            # as the card-level cancellation affordance. Keep the profile
            # button untouched and integrate the progress percentage into the
            # action button itself.
            display_text = f"Cancelar • {progress}%"
        elif raw_status in ("QUEUED",):
            display_text = status_text

        text_pen = resolve_status_progress_text_color(option.palette, raw_status, progress)
        run_border = QColor(cell_border)
        accent_blue = None
        if raw_status in ("READY", "IDLE", "PENDING"):
            # Promote the primary CTA with a solid vivid-blue fill while
            # keeping the rest of the action column unchanged.
            accent_blue = QColor("#006DFF")
            text_pen = QColor("#FFFFFF")
            run_border = QColor(accent_blue)
        elif raw_status == "NO_GAIN":
            text_pen = QColor(tokens.button_disabled_fg)
            run_border = QColor(cell_border)
            if is_dark_theme:
                # Disabled/no-gain status must still be readable. In dark mode
                # the generic disabled token can collapse into the action-cell
                # fill, making "Pronto!" nearly invisible. Keep it subdued but
                # derive it from the card foreground instead of the disabled
                # QPushButton palette.
                text_pen = _blend_qcolor(
                    QColor(tokens.text_primary),
                    QColor(tokens.text_secondary),
                    0.34,
                )
                text_pen.setAlpha(224)
        elif raw_status in ("FAILED", "ERROR"):
            # Failure is a clickable retry affordance, but it must not look like
            # an inactive white/gray button in the light theme. Use the semantic
            # danger token to give the label an explicit red error tone while
            # preserving contrast in both themes.
            error_base = QColor(tokens.danger_fg)
            run_border = QColor(error_base)
            if is_dark_theme:
                text_pen = QColor(tokens.text_on_dark_surface)
            else:
                text_pen = QColor(error_base)

        run_fill = QColor(tokens.card_run_button_bg)
        if raw_status in ("READY", "IDLE", "PENDING"):
            run_fill = QColor(accent_blue or QColor("#006DFF"))
        elif raw_status == "NO_GAIN":
            run_fill = _blend_qcolor(QColor(tokens.card_run_button_bg), QColor(tokens.surface_card), 0.58)
            if is_dark_theme:
                run_fill = _blend_qcolor(QColor(tokens.card_run_button_bg), QColor(tokens.surface_card), 0.34)
        elif raw_status in ("FAILED", "ERROR"):
            error_base = QColor(tokens.danger_fg)
            if is_dark_theme:
                run_fill = _blend_qcolor(error_base, QColor(tokens.surface_card), 0.18)
            else:
                run_fill = _blend_qcolor(error_base, QColor(tokens.surface_card), 0.76)
            run_border = QColor(error_base)
        progress_fill = None
        progress_value = None
        if raw_status in ("RUNNING", "PROCESSING"):
            run_fill = QColor(tokens.progress_track)
            progress_fill = status_color
            progress_value = progress
        elif raw_status in ("DONE", "COMPLETED", "SUCCESS", "FINISHED"):
            if done_playable:
                run_fill = QColor(tokens.card_play_output_button_bg)
                run_border = QColor(tokens.card_play_output_button_border)
            else:
                run_fill = QColor(status_color)
                if QColor(tokens.surface_main).lightness() <= 128:
                    run_fill = _blend_qcolor(run_fill, QColor(tokens.progress_track), 0.52)
            text_pen = QColor(tokens.text_on_dark_surface)
        elif raw_status in ("QUEUED",):
            # Queued is a reversible card-level action. Avoid rendering it like a
            # passive state bar; keep it reading as a button the user can click.
            run_fill = _blend_qcolor(top_fill, QColor(tokens.card_run_button_bg), 0.18)
            text_pen = _blend_qcolor(
                QColor(tokens.danger_fg),
                QColor(tokens.text_secondary),
                0.24,
            )
            text_pen.setAlpha(236)
            if is_dark_theme:
                text_pen = _blend_qcolor(
                    QColor(tokens.text_primary),
                    QColor(tokens.danger_fg),
                    0.26,
                )
                text_pen.setAlpha(244)

        cell_text = display_text
        if raw_status in ("READY", "IDLE", "PENDING", "QUEUED") or done_playable:
            cell_text = ""

        self._draw_action_cell(
            painter,
            progress_rect,
            tokens,
            fill_color=run_fill,
            border_color=run_border,
            text_value=cell_text,
            font=run_font,
            hovered=run_hovered,
            pressed=run_pressed,
            text_color=text_pen,
            icon_color=text_pen,
            progress_value=progress_value,
            progress_fill=progress_fill,
        )

        if raw_status in ("READY", "IDLE", "PENDING") or done_playable:
            content_rect = progress_rect.adjusted(6, 0, -6, 0)
            icon_size = 12
            icon_left = content_rect.left() + 5
            icon_rect = QRect(
                icon_left,
                progress_rect.top() + (progress_rect.height() - icon_size) // 2,
                icon_size,
                icon_size,
            )
            run_icon_color = QColor(text_pen)
            self._draw_run_play_icon(painter, icon_rect, tokens, icon_color=run_icon_color)
            painter.setPen(text_pen)
            display = painter.fontMetrics().elidedText(
                display_text,
                Qt.ElideRight,
                max(10, content_rect.width()),
            )
            text_rect = content_rect.adjusted(0, -1, 0, -1)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignCenter, display)
        elif raw_status in ("QUEUED",):
            content_rect = progress_rect.adjusted(8, 0, -8, 0)
            icon_size = 12
            icon_rect = QRect(
                content_rect.left() + 4,
                progress_rect.top() + (progress_rect.height() - icon_size) // 2 + 1,
                icon_size,
                icon_size,
            )
            run_icon_color = _blend_qcolor(
                QColor(tokens.danger_fg),
                QColor(tokens.text_secondary),
                0.24,
            )
            run_icon_color.setAlpha(236)
            if is_dark_theme:
                run_icon_color = _blend_qcolor(
                    QColor(tokens.text_primary),
                    QColor(tokens.danger_fg),
                    0.30,
                )
                run_icon_color.setAlpha(244)
            self._draw_queue_remove_x_icon(painter, icon_rect, tokens, icon_color=run_icon_color)

            painter.setPen(text_pen)
            text_rect = content_rect.adjusted(0, -2, 0, -2)
            display = painter.fontMetrics().elidedText(
                display_text,
                Qt.ElideRight,
                max(10, text_rect.width()),
            )
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignCenter, display)

        if view:

            if (
                hasattr(view, "_hover_index")
                and view._hover_index == index
                and view._hover_action
            ):

                action = view._hover_action
                hover_rect = actions.get(action)

                if hover_rect:

                    painter.setPen(Qt.NoPen)

                    if action == "remove":
                        self._close_pen_color = tokens.danger_fg
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(tokens.close_button_hover_bg)
                        painter.drawRoundedRect(hover_rect.adjusted(0, 0, -1, -1), 4, 4)
                        painter.setBrush(Qt.NoBrush)
                        painter.setPen(QPen(tokens.close_button_hover_border, 1))
                        painter.drawRoundedRect(hover_rect.adjusted(0, 0, -1, -1), 4, 4)
                    elif action in ("open_folder", "replace_folder"):
                        pass

                    if action == "run":
                        pass
                    elif action == "remove":
                        self.draw_close(painter, hover_rect)
                        self._close_pen_color = self._header_remove_icon_color(tokens)
                    elif action in ("open_folder", "replace_folder"):
                        # Already painted by _draw_destination_icon_button() with
                        # the hovered state before this legacy hover overlay block.
                        # Do not redraw via _draw_secondary_button(), because these
                        # compact icon-only rects elide the old text labels as "...".
                        pass
                    elif action == "settings":
                        pass
        
        if (
            view
            and hasattr(view, "_hover_index")
            and view._hover_index == index
            and not getattr(view, "_hover_action", None)
            and not getattr(view, "_pressed_action", None)
        ):
            # A full-card hover frame is too easily mistaken for selection.
            # The title band is the only clickable selection target, so keep
            # its hover feedback there as well.
            painter.fillRect(header_rect, tokens.accent_soft)

            painter.setPen(QPen(tokens.border_card_hover, 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(header_rect.adjusted(0, 0, -1, -1))


        if option.state & QStyle.State_Selected:
            painter.fillRect(card_rect, tokens.accent_soft)

        painter.restore()
