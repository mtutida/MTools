from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# Native Windows caption chrome must retain one stable brand blue regardless of
# the app palette selected below.  Palette Highlight intentionally differs by
# theme for in-app interaction states, so it cannot also be the caption source.
_WINDOWS_CAPTION_ACCENT = QColor("#0078d4")


@dataclass(frozen=True)
class ThemeTokens:
    """Semantic colors for custom-painted widgets.

    Usage contract
    - Widgets and delegates should consume these semantic tokens instead of inventing
      local QColor literals.
    - Adjust theme behavior here first. Avoid scattering lighter()/darker() calls in
      multiple UI files.
    - Surface tokens are for backgrounds only. Border tokens are for contours.
      Text tokens are for glyphs/text. Accent tokens are for hover/selection.
    - `surface_card` is reserved for card bodies. Do not reuse it for the list or main
      window backgrounds.
    - `surface_card_header` is reserved for the title strip and should remain slightly
      darker than `surface_card` so the close control stays legible.
    - `danger_*` tokens are only for destructive affordances such as the remove button.
    """

    surface_main: QColor
    surface_panel: QColor
    surface_card: QColor
    surface_card_header: QColor
    surface_card_header_text: QColor
    surface_list: QColor
    surface_list_header: QColor
    surface_list_header_text: QColor
    surface_list_divider: QColor
    surface_context_bar: QColor
    surface_empty_state_hint: QColor
    surface_context_bar_text: QColor
    border_panel: QColor
    border_context_bar: QColor
    border_empty_state: QColor
    border_card: QColor
    border_card_hover: QColor
    accent_soft: QColor
    text_primary: QColor
    text_secondary: QColor
    text_linkish: QColor
    empty_state_link_text: QColor
    text_on_dark_surface: QColor
    progress_track: QColor
    progress_fill: QColor
    danger_fg: QColor
    danger_hover_bg: QColor
    button_bg: QColor
    button_fg: QColor
    button_border: QColor
    button_disabled_fg: QColor
    button_disabled_border: QColor
    button_hover_bg: QColor
    button_hover_border: QColor
    button_pressed_bg: QColor
    button_pressed_border: QColor
    quick_quality_button_bg: QColor
    quick_quality_button_fg: QColor
    quick_quality_button_border: QColor
    quick_quality_button_hover_bg: QColor
    quick_quality_button_hover_fg: QColor
    quick_quality_button_checked_bg: QColor
    quick_quality_button_checked_fg: QColor
    quick_quality_button_checked_border: QColor
    quick_quality_hint_text: QColor
    quick_balanced_button_bg: QColor
    quick_balanced_button_fg: QColor
    quick_balanced_button_border: QColor
    quick_balanced_button_hover_bg: QColor
    quick_balanced_button_hover_fg: QColor
    quick_balanced_button_checked_bg: QColor
    quick_balanced_button_checked_fg: QColor
    quick_balanced_button_checked_border: QColor
    quick_balanced_hint_text: QColor
    quick_scale_button_bg: QColor
    quick_scale_button_fg: QColor
    quick_scale_button_border: QColor
    quick_scale_button_hover_bg: QColor
    quick_scale_button_hover_fg: QColor
    quick_scale_button_checked_bg: QColor
    quick_scale_button_checked_fg: QColor
    quick_scale_button_checked_border: QColor
    quick_scale_hint_text: QColor
    quick_aggressive_button_bg: QColor
    quick_aggressive_button_fg: QColor
    quick_aggressive_button_border: QColor
    quick_aggressive_button_hover_bg: QColor
    quick_aggressive_button_hover_fg: QColor
    quick_aggressive_button_checked_bg: QColor
    quick_aggressive_button_checked_fg: QColor
    quick_aggressive_button_checked_border: QColor
    quick_aggressive_hint_text: QColor
    small_button_bg: QColor
    small_button_border: QColor
    small_button_hover_bg: QColor
    small_button_pressed_bg: QColor
    attached_menu_button_hover_bg: QColor
    attached_menu_button_hover_border: QColor
    close_button_hover_bg: QColor
    close_button_hover_border: QColor
    close_button_pressed_bg: QColor
    close_button_pressed_border: QColor
    card_run_button_bg: QColor
    card_run_button_border: QColor
    card_run_button_hover_border: QColor
    card_run_button_hover_overlay: QColor
    card_run_button_pressed_overlay: QColor
    card_play_output_button_bg: QColor
    card_play_output_button_border: QColor
    card_profile_button_bg: QColor
    card_profile_button_border: QColor
    card_destination_button_hover_bg: QColor
    card_remove_hover_bg: QColor
    card_tool_hover_bg: QColor
    status_ready: QColor
    status_analyzing: QColor
    status_processing: QColor
    status_done: QColor
    status_error: QColor
    status_cancelled: QColor
    surface_toast: QColor
    text_toast: QColor
    border_toast: QColor
    surface_tooltip: QColor
    text_tooltip: QColor
    border_tooltip: QColor
    surface_window_caption: QColor
    dialog_hint_text: QColor
    dialog_drop_border: QColor
    drop_target_border: QColor
    overlay_shell_bg: QColor
    overlay_border_strong: QColor
    configuration_section_border: QColor
    configuration_section_border_subtle: QColor



def _copy(color: QColor) -> QColor:
    return QColor(color)



def _mix(first: QColor, second: QColor, first_weight: float) -> QColor:
    """Mix two colors without hard-coded target colors.

    `first_weight` is expected in [0, 1]. The remainder is taken from the second color.
    Alpha is mixed as well so the result can be used directly as a brush/pen color.
    """

    first_weight = max(0.0, min(1.0, first_weight))
    second_weight = 1.0 - first_weight
    mixed = QColor(
        round(first.red() * first_weight + second.red() * second_weight),
        round(first.green() * first_weight + second.green() * second_weight),
        round(first.blue() * first_weight + second.blue() * second_weight),
        round(first.alpha() * first_weight + second.alpha() * second_weight),
    )
    return mixed







def _accent_variant(base: QColor, hue_degrees: int, *, saturation_scale: float = 1.0, value_scale: float = 1.0) -> QColor:
    base = QColor(base)
    hsv = base.toHsv()
    saturation = max(40, min(255, round(hsv.saturation() * saturation_scale)))
    value = max(70, min(255, round(hsv.value() * value_scale)))
    alpha = hsv.alpha() or 255
    return QColor.fromHsv(round((hue_degrees % 360) * 359 / 360), saturation, value, alpha)


def _lightness_delta(first: QColor, second: QColor) -> int:
    return abs(QColor(first).lightness() - QColor(second).lightness())


def _with_minimum_lightness_delta(
    background: QColor,
    preferred: QColor,
    fallback_a: QColor,
    fallback_b: QColor,
    *,
    min_delta: int = 153,
) -> QColor:
    background = QColor(background)
    candidates = [QColor(preferred), QColor(fallback_a), QColor(fallback_b)]
    best = max(candidates, key=lambda candidate: _lightness_delta(candidate, background))
    if _lightness_delta(best, background) >= min_delta:
        return best
    alpha = QColor(preferred).alpha() or 255
    if background.lightness() >= 128:
        return QColor(0, 0, 0, alpha)
    return QColor(255, 255, 255, alpha)


def _accent_text_for_surface(
    background: QColor,
    accent: QColor,
    dark_reference: QColor,
    light_reference: QColor,
    *,
    min_delta: int = 110,
) -> QColor:
    """Keep accent-family hint text readable without collapsing to neutral black/white.

    This darkens or brightens the accent progressively toward the palette text color,
    preserving hue as much as possible while still reaching the requested lightness gap.
    """

    background = QColor(background)
    accent = QColor(accent)
    if _lightness_delta(accent, background) >= min_delta:
        return accent

    target = QColor(dark_reference if background.lightness() >= 128 else light_reference)
    candidate = QColor(accent)
    for accent_weight in (0.88, 0.76, 0.64, 0.52, 0.40, 0.28, 0.16):
        candidate = _mix(accent, target, accent_weight)
        if _lightness_delta(candidate, background) >= min_delta:
            return candidate
    return candidate

def build_button_stylesheet(
    tokens: ThemeTokens,
    *,
    min_height: int = 30,
    border_radius: int = 4,
    horizontal_padding: int = 10,
) -> str:
    """Return the shared semantic stylesheet for standard push buttons.

    Keep button-state visuals centralized here so widgets that only differ in
    geometry do not duplicate hover/pressed/disabled color rules locally.
    """

    button_bg = tokens.button_bg.name(QColor.NameFormat.HexArgb)
    button_fg = tokens.button_fg.name(QColor.NameFormat.HexArgb)
    button_border = tokens.button_border.name(QColor.NameFormat.HexArgb)
    button_hover_bg = tokens.button_hover_bg.name(QColor.NameFormat.HexArgb)
    button_hover_border = tokens.button_hover_border.name(QColor.NameFormat.HexArgb)
    button_pressed_bg = tokens.button_pressed_bg.name(QColor.NameFormat.HexArgb)
    button_pressed_border = tokens.button_pressed_border.name(QColor.NameFormat.HexArgb)
    button_disabled_fg = tokens.button_disabled_fg.name(QColor.NameFormat.HexArgb)
    button_disabled_border = tokens.button_disabled_border.name(QColor.NameFormat.HexArgb)

    hover_padding = max(0, horizontal_padding - 1)

    return f"""
        QPushButton {{
            background: {button_bg};
            color: {button_fg};
            border: 1px solid {button_border};
            border-radius: {border_radius}px;
            padding: 0 {horizontal_padding}px;
            min-height: {min_height}px;
        }}
        QPushButton:hover:!disabled {{
            background: {button_hover_bg};
            color: {button_fg};
            border: 2px solid {button_hover_border};
            border-radius: {border_radius}px;
            padding: 0 {hover_padding}px;
        }}
        QPushButton:pressed:!disabled {{
            background: {button_pressed_bg};
            color: {button_fg};
            border: 2px solid {button_pressed_border};
            border-radius: {border_radius}px;
            padding: 0 {hover_padding}px;
        }}
        QPushButton:disabled {{
            color: {button_disabled_fg};
            border: 1px solid {button_disabled_border};
        }}
    """


def build_small_button_stylesheet(
    tokens: ThemeTokens,
    *,
    border_radius: int = 4,
) -> str:
    """Return the shared semantic stylesheet for compact tool/menu buttons."""

    button_fg = tokens.button_fg.name(QColor.NameFormat.HexArgb)
    hover_border = tokens.button_hover_border.name(QColor.NameFormat.HexArgb)
    pressed_border = tokens.button_pressed_border.name(QColor.NameFormat.HexArgb)
    small_button_bg = tokens.small_button_bg.name(QColor.NameFormat.HexArgb)
    small_button_border = tokens.small_button_border.name(QColor.NameFormat.HexArgb)
    small_button_hover_bg = tokens.small_button_hover_bg.name(QColor.NameFormat.HexArgb)
    small_button_pressed_bg = tokens.small_button_pressed_bg.name(QColor.NameFormat.HexArgb)

    return f"""
        QToolButton {{
            background: {small_button_bg};
            color: {button_fg};
            border-radius: {border_radius}px;
            border: 1px solid {small_button_border};
            padding: 0px;
        }}
        QToolButton:hover {{
            border: 2px solid {hover_border};
            background: {small_button_hover_bg};
        }}
        QToolButton:checked {{
            background: {small_button_pressed_bg};
            color: {button_fg};
            border: 2px solid {pressed_border};
        }}
        QToolButton:pressed {{
            background: {small_button_pressed_bg};
            color: {button_fg};
            border: 2px solid {pressed_border};
        }}
    """


def build_tooltip_palette(base_palette: QPalette, tokens: ThemeTokens) -> QPalette:
    """Build a tooltip palette from the centralized semantic tooltip tokens."""

    palette = QPalette(base_palette)
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        palette.setColor(group, QPalette.ColorRole.ToolTipBase, tokens.surface_tooltip)
        palette.setColor(group, QPalette.ColorRole.ToolTipText, tokens.text_tooltip)
        palette.setColor(group, QPalette.ColorRole.Base, tokens.surface_tooltip)
        palette.setColor(group, QPalette.ColorRole.Window, tokens.surface_tooltip)
        palette.setColor(group, QPalette.ColorRole.Text, tokens.text_tooltip)
        palette.setColor(group, QPalette.ColorRole.WindowText, tokens.text_tooltip)
    return palette


def build_tooltip_stylesheet(tokens: ThemeTokens) -> str:
    """Return the centralized semantic stylesheet for tooltips."""

    tooltip_bg = color_to_qss(tokens.surface_tooltip, include_alpha=False)
    tooltip_fg = color_to_qss(tokens.text_tooltip, include_alpha=False)
    tooltip_border = color_to_qss(tokens.border_tooltip, include_alpha=False)

    return f"""
        QToolTip {{
            background: {tooltip_bg};
            background-color: {tooltip_bg};
            color: {tooltip_fg};
            border: 1px solid {tooltip_border};
            padding: 4px;
        }}
        QToolTip QLabel {{
            background: {tooltip_bg};
            background-color: {tooltip_bg};
            color: {tooltip_fg};
            border: none;
            margin: 0;
            padding: 0;
        }}
        QToolTip * {{
            background: {tooltip_bg};
            background-color: {tooltip_bg};
            color: {tooltip_fg};
        }}
    """



def build_semantic_tooltip_stylesheet(tokens: ThemeTokens) -> str:
    """Return the stylesheet for the custom in-app tooltip used by card actions."""

    tooltip_bg = color_to_css(tokens.surface_tooltip)
    tooltip_fg = color_to_css(tokens.text_tooltip)
    tooltip_border = color_to_css(tokens.border_tooltip)

    return f"""
        QFrame#SemanticTooltip {{
            background-color: {tooltip_bg};
            color: {tooltip_fg};
            border: 1px solid {tooltip_border};
            border-radius: 4px;
        }}
        QFrame#SemanticTooltip QLabel {{
            background: transparent;
            color: {tooltip_fg};
            border: none;
            padding: 0px;
        }}
    """


def color_to_css(color: QColor) -> str:
    return QColor(color).name(QColor.NameFormat.HexArgb)


def color_to_qss(color: QColor, *, include_alpha: bool = True) -> str:
    c = QColor(color)
    if include_alpha and c.alpha() < 255:
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"
    return c.name(QColor.NameFormat.HexRgb)


def tooltip_text_html(text: str, color: QColor) -> str:
    css = color_to_css(color)
    safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    return f'<span style="color:{css};">{safe}</span>'
def color_to_win_bgr(color: QColor) -> int:
    c = QColor(color)
    return (c.blue() << 16) | (c.green() << 8) | c.red()

def _effective_theme_palette(palette: QPalette) -> QPalette:
    app = QApplication.instance()
    if app is not None:
        try:
            return app.palette()
        except RuntimeError:
            pass
    return palette


def build_theme_tokens(palette: QPalette, *, use_application_palette: bool = True) -> ThemeTokens:
    """Derive app-level semantic colors from the active Qt palette.

    Notes for future maintenance
    - Keep all palette derivation here whenever possible.
    - Prefer mixing palette roles over introducing fixed RGB values.
    - Use alpha only for interaction states such as hover/selection.
    """

    if use_application_palette:
        palette = _effective_theme_palette(palette)
    base = _copy(palette.base().color())
    window = _copy(palette.window().color())
    alternate = _copy(palette.alternateBase().color())
    mid = _copy(palette.mid().color())
    midlight = _copy(palette.midlight().color())
    text = _copy(palette.text().color())
    button_text = _copy(palette.buttonText().color())
    highlight = _copy(palette.highlight().color())

    is_dark = window.lightness() < 128

    # Main app background sits behind all framed panels. It should stay slightly
    # separated from the toolbar/list/footer cards without introducing a fixed color.
    if is_dark:
        surface_main = _mix(window, alternate, 0.70)
    else:
        surface_main = _mix(_mix(window, alternate, 0.45), mid, 0.82)
    surface_main.setAlpha(255)

    # Shared framed panel background used by high-level bars such as toolbar/footer.
    surface_panel = _mix(base, window, 0.74 if is_dark else 0.82)
    surface_panel.setAlpha(255)

    # Card body should stay visually lighter than the list/container surfaces.
    # In dark themes the system `base()` is often too dark, so we elevate the window
    # surface strongly instead of forcing a literal white.
    card_seed = _mix(window, base, 0.65)
    if is_dark:
        # Keep the rc221 hierarchy, but sink the dark card body one subtle step
        # back into the neutral button/window range. The profile action remains
        # elevated from this token below, so only the dark card body is softened.
        elevated_card_seed = card_seed.lighter(232)
        surface_card = _mix(palette.button().color(), elevated_card_seed, 0.40)
    else:
        surface_card = card_seed.lighter(112)

    # List/panel surfaces stay distinct from cards so the cards can remain visually lighter.
    surface_list = _mix(mid, midlight, 0.28 if is_dark else 0.22)
    surface_list.setAlpha(255)

    # Keep the list title strip clearly separated from the surrounding panel while
    # avoiding the heavy blue cast that can happen when the Windows accent bleeds
    # too directly into the fill. In light themes, anchor the strip more strongly
    # to neutral mid tones so it reads as its own band instead of blending into the
    # container background.
    if is_dark:
        surface_list_header = _mix(surface_list, midlight, 0.70)
        surface_list_header = _mix(highlight, surface_list_header, 0.05)
    else:
        surface_list_header = _mix(surface_list, mid, 0.52)
        surface_list_header = _mix(highlight, surface_list_header, 0.01)
    if abs(surface_list_header.lightness() - surface_list.lightness()) < 16:
        surface_list_header = _mix(mid, surface_list, 0.40 if is_dark else 0.30)
    surface_list_header_text = _mix(text, button_text, 0.82 if is_dark else 0.9)

    border_panel = _mix(mid, midlight, 0.58)
    border_panel.setAlpha(255)

    surface_context_bar = _mix(surface_list, alternate, 0.72)
    surface_context_bar.setAlpha(255)
    if abs(surface_context_bar.lightness() - surface_list.lightness()) < 6:
        surface_context_bar = _mix(midlight, surface_list, 0.28 if is_dark else 0.18)
    # Keep the status/context strip present but quieter than the list header.
    # Light themes get a slight lift; dark themes sink a little into the panel
    # background. Both directions are derived from theme surfaces, not literals.
    if is_dark:
        surface_context_bar = _mix(surface_context_bar, surface_main, 0.46)
    else:
        surface_context_bar = _mix(surface_context_bar, surface_panel, 0.54)
    surface_context_bar.setAlpha(255)
    surface_context_bar_text = _mix(text, button_text, 0.7 if is_dark else 0.82)
    border_context_bar = _mix(border_panel, surface_context_bar, 0.74)
    border_context_bar.setAlpha(255)

    # Empty-state hints sit on top of the list surface. Keep them softer than the
    # main text while still readable in both themes, and reserve the stronger accent
    # color for actionable links only.
    surface_empty_state_hint = _mix(text, midlight, 0.72 if is_dark else 0.78)
    surface_empty_state_hint.setAlpha(170 if is_dark else 160)
    # Keep the empty-state contour/separator clearly readable in light theme so the
    # horizontal guide below the main title and the dashed drop zone do not wash out
    # against the panel background.
    border_empty_state = _mix(text, midlight, 0.28 if is_dark else 0.62)
    border_empty_state.setAlpha(110 if is_dark else 196)

    # Header stays close to the card body but with a clearer separation so the remove
    # affordance remains readable without needing a fixed icon color.
    header_seed = _mix(alternate, midlight, 0.55)
    if abs(header_seed.lightness() - surface_card.lightness()) < 10:
        header_seed = _mix(midlight, mid, 0.65)
    if is_dark:
        # Keep the dark-theme title strip clearly distinct from the card body
        # without drifting back to a bluish tint.
        surface_card_header = _mix(mid, alternate, 0.60).lighter(102)
    elif surface_card.lightness() > 170:
        surface_card_header = header_seed.darker(60)
    else:
        surface_card_header = header_seed.lighter(106)

    # Use a text color that contrasts against the header strip itself.
    if surface_card_header.lightness() > 150:
        surface_card_header_text = _mix(text, button_text, 0.7).darker(120)
    else:
        surface_card_header_text = _mix(button_text, text, 0.4).lighter(145)

    if is_dark:
        border_card = _mix(highlight, midlight, 0.68)
        border_card.setAlpha(210)
    else:
        # Keep the light-theme card border slightly darker than the list/panel
        # background so cards remain framed without introducing a heavy blue tint.
        border_card = _mix(mid, highlight, 0.82)
        border_card = border_card.darker(118)
        border_card.setAlpha(225)

    # Use a blue-family interaction accent for card hover. Some Windows palettes
    # expose highlight/link as green, purple or magenta; hue shifting the accent
    # can keep it in the wrong family. Normalize only the hue to blue while
    # preserving palette-derived saturation/value/alpha.
    hover_card_accent = _copy(highlight)
    hover_h, hover_s, hover_v, hover_a = hover_card_accent.getHsv()
    if hover_h < 0:
        hover_h = 210
    hover_card_accent.setHsv(
        210,
        min(255, max(88, int(hover_s * (1.04 if is_dark else 0.96)))),
        min(255, max(112, int(hover_v * (1.02 if is_dark else 0.98)))),
        hover_a,
    )
    border_card_hover = _copy(hover_card_accent)
    border_card_hover.setAlpha(122 if is_dark else 116)

    accent_soft = _copy(hover_card_accent)
    accent_soft.setAlpha(28 if is_dark else 24)

    text_secondary = _mix(text, mid, 0.72)
    text_secondary.setAlpha(170)

    # Configuration overlay section borders must remain neutral and visible.
    # Use text_secondary as the dark-theme neutral contrast source because it is
    # already guaranteed to sit above the card surface without carrying accent hue.
    if is_dark:
        configuration_section_border = _mix(text_secondary, surface_card, 0.44)
        configuration_section_border.setAlpha(255)
        configuration_section_border_subtle = _mix(configuration_section_border, surface_card, 0.72)
        configuration_section_border_subtle.setAlpha(255)
    else:
        configuration_section_border = QColor(border_card)
        configuration_section_border_subtle = QColor(border_panel)

    text_linkish = _mix(highlight, text, 0.7)
    if is_dark:
        # In dark theme the card-header suffix sits on a medium neutral strip.
        # Keep it clearly legible by using a deeper link-blue derived from the
        # active palette instead of a washed-out accent mix.
        text_linkish = _copy(palette.link().color())
        text_linkish = text_linkish.darker(116)

    # Empty-state links need a clearer action affordance than the restrained
    # link token used inside cards/tool rows. Keep this derived from the active
    # palette, not a local literal, and tune each theme independently.
    empty_state_link_text = QColor(text_linkish)
    if is_dark:
        # Do not inherit QPalette.Link here: the Windows-native role can be
        # pure #0000ff, which is too dark against the empty list surface.
        # Start from the app accent and lift it toward normal text only until
        # the link keeps a clear, readable separation from that surface.
        empty_state_link_text = _accent_text_for_surface(
            surface_list,
            highlight,
            text,
            text,
            min_delta=120,
        )
        # The contrast lift above deliberately moves toward normal text. Restore
        # enough blue saturation afterwards so actions still read as links rather
        # than as muted secondary copy on the dark empty-state panel.
        hue, saturation, value, alpha = empty_state_link_text.getHsv()
        if hue >= 0:
            empty_state_link_text.setHsv(
                hue,
                max(saturation, 110),
                max(value, 245),
                alpha,
            )
    else:
        # Light theme: make the empty-state actions read more clearly as links.
        # Use the palette link color as the dominant source and only a small
        # accent blend, so this does not affect the restrained card link token.
        empty_state_link_text = _mix(palette.link().color(), highlight, 0.24)
        hue, saturation, value, alpha = empty_state_link_text.getHsv()
        if hue >= 0:
            empty_state_link_text.setHsv(
                hue,
                min(255, max(saturation, int(saturation * 1.36))),
                min(255, max(value, int(value * 1.08))),
                alpha,
            )
    empty_state_link_text.setAlpha(255)

    progress_track = _mix(midlight, mid, 0.5)
    progress_track.setAlpha(130 if is_dark else 145)

    # Progress bars should read as a neutral track with a clear active fill that
    # follows the app accent. Keep the fill semantic here so progress widgets do
    # not derive their own chunk color ad hoc.
    progress_fill = _mix(highlight, palette.link().color(), 0.72 if is_dark else 0.82)
    progress_fill.setAlpha(255)

    # Semantic foreground for any palette-derived dark fills such as completed/error
    # progress bars. Keep it centralized so delegates do not compute ad-hoc contrast.
    text_on_dark_surface = _mix(palette.brightText().color(), button_text, 0.82 if is_dark else 0.92)
    if text_on_dark_surface.lightness() < 190:
        text_on_dark_surface = text_on_dark_surface.lighter(150 if is_dark else 135)
    text_on_dark_surface = _with_minimum_lightness_delta(
        progress_fill,
        text_on_dark_surface,
        palette.brightText().color(),
        text,
    )

    danger_seed = _accent_variant(
        highlight,
        6,
        saturation_scale=1.48 if is_dark else 1.72,
        value_scale=1.02 if is_dark else 0.96,
    )
    danger_fg = _mix(surface_card_header_text, danger_seed, 0.28 if is_dark else 0.18)
    # Centralized destructive surfaces used by both custom-painted remove affordances
    # and the panel close button. Pressed must be stronger than hover, but still
    # derived from the same semantic accent rather than a local literal.
    danger_bg = _mix(danger_seed, mid, 0.70 if is_dark else 0.62)
    danger_bg.setAlpha(210 if is_dark else 185)
    danger_hover_bg = _mix(danger_seed, surface_card_header, 0.34 if is_dark else 0.30)
    danger_hover_bg.setAlpha(190 if is_dark else 175)

    # Shared push-button states for standard action buttons. Keep hover/pressed
    # strength centralized here so footer and list-bar actions stay visually aligned.
    button_bg = _copy(palette.button().color())
    button_fg = _with_minimum_lightness_delta(
        button_bg,
        button_text,
        palette.brightText().color(),
        text,
    )
    if is_dark:
        # In dark theme keep enabled-button contours visibly lighter than the disabled
        # outline so fixed-width actions like the footer exit button do not read smaller.
        # Bias the border toward the palette midlight and a small amount of foreground
        # contrast instead of the darker mid tone.
        button_border = _mix(midlight, button_fg, 0.82)
    else:
        button_border = _mix(mid, palette.dark().color(), 0.45)
    button_disabled_fg = _mix(button_text, mid, 0.3)
    button_disabled_border = _mix(midlight, mid, 0.55)
    button_hover_bg = _mix(midlight, button_bg, 0.72)
    button_hover_border = _copy(highlight)
    button_pressed_bg = _mix(mid, midlight, 0.6)
    button_pressed_border = _copy(highlight)

    # Semantic styling for the four quick-compression zones.
    # The quick scale intentionally progresses from cool to warm accents:
    # 1-4 blue (quality), 5-8 green (bitrate), 9-12 amber (resolution),
    # 13-16 red/orange (resolution/FPS). The colors remain tokenized and
    # palette-aware instead of being hard-coded directly in the panel stylesheet.
    quick_quality_seed = _mix(_accent_variant(highlight, 212, saturation_scale=1.28 if is_dark else 1.42, value_scale=1.08 if is_dark else 1.03), palette.link().color(), 0.72)
    quick_quality_button_bg = _mix(quick_quality_seed, surface_card, 0.46 if is_dark else 0.38)
    quick_quality_button_border = _mix(quick_quality_seed, button_border, 0.70 if is_dark else 0.62)
    quick_quality_button_hover_bg = _mix(quick_quality_seed, quick_quality_button_bg, 0.84 if is_dark else 0.78)
    quick_quality_button_checked_bg = _mix(quick_quality_seed, quick_quality_button_bg, 0.96 if is_dark else 0.92)
    quick_quality_button_checked_border = _mix(quick_quality_seed, quick_quality_button_border, 0.92 if is_dark else 0.88)
    quick_quality_button_fg = _with_minimum_lightness_delta(
        quick_quality_button_bg,
        text,
        palette.brightText().color(),
        button_text,
    )
    quick_quality_button_hover_fg = _with_minimum_lightness_delta(
        quick_quality_button_hover_bg,
        quick_quality_button_fg,
        palette.brightText().color(),
        text,
    )
    quick_quality_button_checked_fg = _with_minimum_lightness_delta(
        quick_quality_button_checked_bg,
        palette.brightText().color(),
        text,
        button_text,
    )
    quick_quality_hint_text = _accent_text_for_surface(
        surface_card,
        _mix(quick_quality_seed, text, 0.78 if is_dark else 0.74),
        text,
        palette.brightText().color(),
        min_delta=110,
    )

    quick_balanced_seed = _accent_variant(highlight, 128, saturation_scale=1.34 if is_dark else 1.52, value_scale=1.10 if is_dark else 1.04)
    quick_balanced_button_bg = _mix(quick_balanced_seed, surface_card, 0.44 if is_dark else 0.38)
    quick_balanced_button_border = _mix(quick_balanced_seed, button_border, 0.68 if is_dark else 0.62)
    quick_balanced_button_hover_bg = _mix(quick_balanced_seed, quick_balanced_button_bg, 0.82 if is_dark else 0.78)
    quick_balanced_button_checked_bg = _mix(quick_balanced_seed, quick_balanced_button_bg, 0.94 if is_dark else 0.91)
    quick_balanced_button_checked_border = _mix(quick_balanced_seed, quick_balanced_button_border, 0.88 if is_dark else 0.86)
    quick_balanced_button_fg = _with_minimum_lightness_delta(
        quick_balanced_button_bg,
        text,
        palette.brightText().color(),
        button_text,
    )
    quick_balanced_button_hover_fg = _with_minimum_lightness_delta(
        quick_balanced_button_hover_bg,
        quick_balanced_button_fg,
        palette.brightText().color(),
        text,
    )
    quick_balanced_button_checked_fg = _with_minimum_lightness_delta(
        quick_balanced_button_checked_bg,
        palette.brightText().color(),
        text,
        button_text,
    )
    quick_balanced_hint_text = _accent_text_for_surface(
        surface_card,
        _mix(quick_balanced_seed, text, 0.88 if is_dark else 0.80),
        text,
        palette.brightText().color(),
        min_delta=110,
    )

    quick_scale_seed = _accent_variant(highlight, 48, saturation_scale=1.46 if is_dark else 1.66, value_scale=1.12 if is_dark else 1.06)
    quick_scale_button_bg = _mix(quick_scale_seed, surface_card, 0.46 if is_dark else 0.40)
    quick_scale_button_border = _mix(quick_scale_seed, button_border, 0.70 if is_dark else 0.64)
    quick_scale_button_hover_bg = _mix(quick_scale_seed, quick_scale_button_bg, 0.84 if is_dark else 0.80)
    quick_scale_button_checked_bg = _mix(quick_scale_seed, quick_scale_button_bg, 0.95 if is_dark else 0.92)
    quick_scale_button_checked_border = _mix(quick_scale_seed, quick_scale_button_border, 0.90 if is_dark else 0.88)
    quick_scale_button_fg = _with_minimum_lightness_delta(
        quick_scale_button_bg,
        text,
        palette.brightText().color(),
        button_text,
    )
    quick_scale_button_hover_fg = _with_minimum_lightness_delta(
        quick_scale_button_hover_bg,
        quick_scale_button_fg,
        palette.brightText().color(),
        text,
    )
    quick_scale_button_checked_fg = _with_minimum_lightness_delta(
        quick_scale_button_checked_bg,
        palette.brightText().color(),
        text,
        button_text,
    )
    quick_scale_hint_text = _accent_text_for_surface(
        surface_card,
        _mix(quick_scale_seed, text, 0.83 if is_dark else 0.81),
        text,
        palette.brightText().color(),
        min_delta=110,
    )

    quick_aggressive_seed = _accent_variant(highlight, 8, saturation_scale=1.58 if is_dark else 1.82, value_scale=1.10 if is_dark else 1.04)
    quick_aggressive_button_bg = _mix(quick_aggressive_seed, surface_card, 0.48 if is_dark else 0.42)
    quick_aggressive_button_border = _mix(quick_aggressive_seed, button_border, 0.72 if is_dark else 0.66)
    quick_aggressive_button_hover_bg = _mix(quick_aggressive_seed, quick_aggressive_button_bg, 0.84 if is_dark else 0.80)
    quick_aggressive_button_checked_bg = _mix(quick_aggressive_seed, quick_aggressive_button_bg, 0.95 if is_dark else 0.93)
    quick_aggressive_button_checked_border = _mix(quick_aggressive_seed, quick_aggressive_button_border, 0.90 if is_dark else 0.88)
    quick_aggressive_button_fg = _with_minimum_lightness_delta(
        quick_aggressive_button_bg,
        text,
        palette.brightText().color(),
        button_text,
    )
    quick_aggressive_button_hover_fg = _with_minimum_lightness_delta(
        quick_aggressive_button_hover_bg,
        quick_aggressive_button_fg,
        palette.brightText().color(),
        text,
    )
    quick_aggressive_button_checked_fg = _with_minimum_lightness_delta(
        quick_aggressive_button_checked_bg,
        palette.brightText().color(),
        text,
        button_text,
    )
    quick_aggressive_hint_text = _accent_text_for_surface(
        surface_card,
        _mix(quick_aggressive_seed, text, 0.84 if is_dark else 0.82),
        text,
        palette.brightText().color(),
        min_delta=110,
    )

    small_button_bg = _mix(alternate, window, 0.62)
    small_button_border = _mix(midlight, mid, 0.45)
    small_button_hover_bg = _mix(base, alternate, 0.65)
    small_button_pressed_bg = _mix(midlight, alternate, 0.58)

    # Dedicated hover token for the title-bar attached menu control. The regular
    # compact-button hover is neutral and can read as gray/dark on the pale list
    # header. Keep this affordance softly blue by deriving it from semantic
    # surfaces plus the app accent, while leaving widgets free of local color math.
    attached_menu_hover_base = _mix(surface_list_header, surface_card, 0.34 if is_dark else 0.24)
    attached_menu_button_hover_bg = _mix(
        button_hover_border if is_dark else progress_fill,
        attached_menu_hover_base,
        0.18 if is_dark else 0.07,
    )
    attached_menu_button_hover_bg.setAlpha(255)
    attached_menu_button_hover_border = _mix(
        button_hover_border,
        small_button_border,
        0.58 if is_dark else 0.42,
    )
    attached_menu_button_hover_border.setAlpha(255)

    close_button_hover_bg = _copy(danger_hover_bg)
    close_button_hover_border = _mix(danger_fg, button_hover_border, 0.72 if is_dark else 0.66)
    close_button_pressed_bg = _mix(danger_bg, small_button_pressed_bg, 0.76 if is_dark else 0.68)
    close_button_pressed_border = _mix(danger_fg, button_pressed_border, 0.74 if is_dark else 0.68)

    # Custom-painted card action button states. These stay centralized here so
    # the delegate consumes ready-to-use colors instead of deriving them locally.
    card_run_button_bg = _mix(small_button_bg, button_bg, 0.5 if is_dark else 0.42)
    card_run_button_border = _mix(small_button_border, button_border, 0.58 if is_dark else 0.48)
    card_run_button_hover_border = _mix(button_hover_border, card_run_button_border, 0.82 if is_dark else 0.78)
    card_run_button_hover_overlay = _copy(button_hover_bg)
    card_run_button_hover_overlay.setAlpha(118 if is_dark else 48)
    if is_dark:
        # Match the dark-card run-button hover to the same hover treatment used
        # by the header action buttons: accent border plus a slightly stronger
        # hover fill. This keeps the custom-painted button aligned with the
        # centralized QPushButton styling without deriving colors locally.
        card_run_button_hover_border = _copy(button_hover_border)
    card_run_button_pressed_overlay = _copy(button_pressed_bg)
    card_run_button_pressed_overlay.setAlpha(108 if is_dark else 64)

    # Output playback uses the same bottom action cell after successful
    # processing. Give it a vivid semantic blue so it reads as a fresh action
    # instead of a passive completed status, while keeping the delegate free of
    # local color decisions.
    card_play_output_button_bg = _mix(button_hover_border, progress_fill, 0.72 if is_dark else 0.82)
    card_play_output_button_bg.setAlpha(255)
    card_play_output_button_border = _mix(button_hover_border, button_pressed_border, 0.72 if is_dark else 0.78)
    card_play_output_button_border.setAlpha(255)

    # Upper action cell used by the per-card profile/settings button. In dark
    # theme the card body now uses the previous neutral elevation, so the button
    # must sit one level above that body as a lighter neutral surface while still
    # staying below the primary blue run action. Keep this centralized here so
    # the delegate only consumes ready-to-use semantic tokens.
    if is_dark:
        card_profile_button_bg = _mix(button_hover_bg, surface_card, 0.58)
        if card_profile_button_bg.lightness() - surface_card.lightness() < 14:
            card_profile_button_bg = surface_card.lighter(126)
        card_profile_button_border = _mix(button_hover_border, border_card, 0.34)
    else:
        card_profile_button_bg = _mix(card_run_button_bg, surface_card, 0.10)
        card_profile_button_border = _copy(border_card)
    card_profile_button_bg.setAlpha(255)
    card_profile_button_border.setAlpha(225 if is_dark else 215)

    # Hover fill for the compact destination icon buttons (open folder / replace
    # folder). Only the hover background needs a stronger light-theme cue; icon
    # and border colors remain exactly as defined by the existing delegate path.
    if is_dark:
        card_destination_button_hover_bg = _copy(accent_soft)
        card_destination_button_hover_bg.setAlpha(76)
    else:
        card_destination_button_hover_bg = _mix(surface_card, progress_fill, 0.88)
        card_destination_button_hover_bg.setAlpha(255)

    card_remove_hover_bg = _copy(danger_hover_bg)
    card_tool_hover_bg = _copy(accent_soft)
    card_tool_hover_bg.setAlpha(76 if is_dark else 42)

    divider = _mix(text, midlight, 0.35)
    divider.setAlpha(140)

    # Status accents for card status dots/text. Keep these semantic so future
    # refinements happen here instead of reintroducing literal RGB values in the
    # presenter or delegate. They intentionally stay distinct but palette-derived.
    if is_dark:
        status_ready = _mix(palette.link().color(), text, 0.42)
    else:
        status_ready = _mix(text, mid, 0.42)
    status_analyzing = _mix(highlight, text, 0.78)
    status_processing = _mix(highlight, palette.link().color(), 0.45)
    status_done = _mix(status_processing, surface_card_header_text, 0.55)
    status_error = _mix(palette.brightText().color(), highlight, 0.35 if is_dark else 0.2)
    status_cancelled = _mix(text_secondary, mid, 0.55)

    # Toasts sit above the main content, so they need a denser panel surface with a
    # slightly stronger border than regular panels while still following the active
    # palette instead of a fixed popup color.
    surface_toast = _mix(surface_panel, window, 0.62 if is_dark else 0.78)
    surface_toast.setAlpha(236)
    overlay_shell_bg = _mix(surface_panel, surface_main, 0.78 if is_dark else 0.86)
    overlay_shell_bg = overlay_shell_bg.darker(112)
    overlay_shell_bg.setAlpha(255)
    overlay_border_strong = _mix(border_panel, border_card, 0.58 if is_dark else 0.64)
    overlay_border_strong.setAlpha(255)

    text_toast = _mix(text, button_text, 0.72 if is_dark else 0.84)
    border_toast = _mix(text_toast, border_panel, 0.34 if is_dark else 0.28)
    border_toast.setAlpha(90 if is_dark else 80)

    # Tooltips must remain strongly legible regardless of platform tooltip quirks.
    # Keep this contrast policy centralized so widgets do not compensate locally.
    tooltip_light_text = _with_minimum_lightness_delta(
        QColor(32, 32, 32),
        palette.brightText().color(),
        _mix(button_text, palette.brightText().color(), 0.35),
        QColor(255, 255, 255),
    )
    tooltip_dark_bg = QColor(30, 34, 40)
    tooltip_light_fg = QColor(246, 248, 252)
    if is_dark:
        surface_tooltip = _mix(tooltip_dark_bg, window, 0.84)
        text_tooltip = _with_minimum_lightness_delta(
            surface_tooltip,
            tooltip_light_text,
            tooltip_light_fg,
            palette.brightText().color(),
        )
        border_tooltip = _mix(highlight, surface_tooltip, 0.30)
    else:
        surface_tooltip = _copy(tooltip_dark_bg)
        text_tooltip = _copy(tooltip_light_fg)
        border_tooltip = _mix(highlight, surface_tooltip, 0.34)
    surface_tooltip.setAlpha(255)
    border_tooltip.setAlpha(255)

    # Use the same native caption blue in Claro and Escuro.  The interactive
    # palette highlight intentionally differs between those themes, but Windows
    # title chrome should not shift hue when the app theme is changed.
    surface_window_caption = _copy(_WINDOWS_CAPTION_ACCENT)

    # Dialog helper colors are intentionally limited to overlays/hints used by
    # transient import flows. Keep them softer than primary panel text so they
    # guide the user without competing with action buttons.
    dialog_hint_text = _mix(text_secondary, text, 0.7 if is_dark else 0.76)
    dialog_hint_text.setAlpha(170 if is_dark else 155)
    dialog_drop_border = _mix(highlight, border_panel, 0.72)
    dialog_drop_border.setAlpha(210 if is_dark else 190)

    # Dedicated drag/drop affordance for list and empty-state drop targets. Keep this
    # separate from text/accent tokens so drop zones do not borrow link semantics.
    drop_target_border = _mix(highlight, border_empty_state, 0.82 if is_dark else 0.76)
    drop_target_border.setAlpha(235 if is_dark else 215)

    return ThemeTokens(
        surface_main=surface_main,
        surface_panel=surface_panel,
        surface_card=surface_card,
        surface_card_header=surface_card_header,
        surface_card_header_text=surface_card_header_text,
        surface_list=surface_list,
        surface_list_header=surface_list_header,
        surface_list_header_text=surface_list_header_text,
        surface_list_divider=divider,
        surface_context_bar=surface_context_bar,
        surface_empty_state_hint=surface_empty_state_hint,
        surface_context_bar_text=surface_context_bar_text,
        border_panel=border_panel,
        border_context_bar=border_context_bar,
        border_empty_state=border_empty_state,
        border_card=border_card,
        border_card_hover=border_card_hover,
        accent_soft=accent_soft,
        text_primary=text,
        text_secondary=text_secondary,
        text_linkish=text_linkish,
        empty_state_link_text=empty_state_link_text,
        text_on_dark_surface=text_on_dark_surface,
        progress_track=progress_track,
        progress_fill=progress_fill,
        danger_fg=danger_fg,
        danger_hover_bg=danger_hover_bg,
        button_bg=button_bg,
        button_fg=button_fg,
        button_border=button_border,
        button_disabled_fg=button_disabled_fg,
        button_disabled_border=button_disabled_border,
        button_hover_bg=button_hover_bg,
        button_hover_border=button_hover_border,
        button_pressed_bg=button_pressed_bg,
        button_pressed_border=button_pressed_border,
        quick_quality_button_bg=quick_quality_button_bg,
        quick_quality_button_fg=quick_quality_button_fg,
        quick_quality_button_border=quick_quality_button_border,
        quick_quality_button_hover_bg=quick_quality_button_hover_bg,
        quick_quality_button_hover_fg=quick_quality_button_hover_fg,
        quick_quality_button_checked_bg=quick_quality_button_checked_bg,
        quick_quality_button_checked_fg=quick_quality_button_checked_fg,
        quick_quality_button_checked_border=quick_quality_button_checked_border,
        quick_quality_hint_text=quick_quality_hint_text,
        quick_balanced_button_bg=quick_balanced_button_bg,
        quick_balanced_button_fg=quick_balanced_button_fg,
        quick_balanced_button_border=quick_balanced_button_border,
        quick_balanced_button_hover_bg=quick_balanced_button_hover_bg,
        quick_balanced_button_hover_fg=quick_balanced_button_hover_fg,
        quick_balanced_button_checked_bg=quick_balanced_button_checked_bg,
        quick_balanced_button_checked_fg=quick_balanced_button_checked_fg,
        quick_balanced_button_checked_border=quick_balanced_button_checked_border,
        quick_balanced_hint_text=quick_balanced_hint_text,
        quick_scale_button_bg=quick_scale_button_bg,
        quick_scale_button_fg=quick_scale_button_fg,
        quick_scale_button_border=quick_scale_button_border,
        quick_scale_button_hover_bg=quick_scale_button_hover_bg,
        quick_scale_button_hover_fg=quick_scale_button_hover_fg,
        quick_scale_button_checked_bg=quick_scale_button_checked_bg,
        quick_scale_button_checked_fg=quick_scale_button_checked_fg,
        quick_scale_button_checked_border=quick_scale_button_checked_border,
        quick_scale_hint_text=quick_scale_hint_text,
        quick_aggressive_button_bg=quick_aggressive_button_bg,
        quick_aggressive_button_fg=quick_aggressive_button_fg,
        quick_aggressive_button_border=quick_aggressive_button_border,
        quick_aggressive_button_hover_bg=quick_aggressive_button_hover_bg,
        quick_aggressive_button_hover_fg=quick_aggressive_button_hover_fg,
        quick_aggressive_button_checked_bg=quick_aggressive_button_checked_bg,
        quick_aggressive_button_checked_fg=quick_aggressive_button_checked_fg,
        quick_aggressive_button_checked_border=quick_aggressive_button_checked_border,
        quick_aggressive_hint_text=quick_aggressive_hint_text,
        small_button_bg=small_button_bg,
        small_button_border=small_button_border,
        small_button_hover_bg=small_button_hover_bg,
        small_button_pressed_bg=small_button_pressed_bg,
        attached_menu_button_hover_bg=attached_menu_button_hover_bg,
        attached_menu_button_hover_border=attached_menu_button_hover_border,
        close_button_hover_bg=close_button_hover_bg,
        close_button_hover_border=close_button_hover_border,
        close_button_pressed_bg=close_button_pressed_bg,
        close_button_pressed_border=close_button_pressed_border,
        card_run_button_bg=card_run_button_bg,
        card_run_button_border=card_run_button_border,
        card_run_button_hover_border=card_run_button_hover_border,
        card_run_button_hover_overlay=card_run_button_hover_overlay,
        card_run_button_pressed_overlay=card_run_button_pressed_overlay,
        card_play_output_button_bg=card_play_output_button_bg,
        card_play_output_button_border=card_play_output_button_border,
        card_profile_button_bg=card_profile_button_bg,
        card_profile_button_border=card_profile_button_border,
        card_destination_button_hover_bg=card_destination_button_hover_bg,
        card_remove_hover_bg=card_remove_hover_bg,
        card_tool_hover_bg=card_tool_hover_bg,
        status_ready=status_ready,
        status_analyzing=status_analyzing,
        status_processing=status_processing,
        status_done=status_done,
        status_error=status_error,
        status_cancelled=status_cancelled,
        surface_toast=surface_toast,
        text_toast=text_toast,
        border_toast=border_toast,
        surface_tooltip=surface_tooltip,
        text_tooltip=text_tooltip,
        border_tooltip=border_tooltip,
        surface_window_caption=surface_window_caption,
        dialog_hint_text=dialog_hint_text,
        dialog_drop_border=dialog_drop_border,
        drop_target_border=drop_target_border,
        overlay_shell_bg=overlay_shell_bg,
        overlay_border_strong=overlay_border_strong,
        configuration_section_border=configuration_section_border,
        configuration_section_border_subtle=configuration_section_border_subtle,
    )



def resolve_text_on_surface(palette: QPalette, background: QColor) -> QColor:
    """Return the semantic text color for a palette-derived surface."""

    tokens = build_theme_tokens(palette)
    if background.lightness() <= 120:
        return _copy(tokens.text_on_dark_surface)
    return _copy(tokens.text_primary)


def resolve_status_progress_text_color(palette: QPalette, raw_status: str, progress: int) -> QColor:
    """Return the centralized progress-label color for the card status bar."""

    status_color = resolve_status_color(palette, raw_status)
    normalized = str(raw_status or "READY").upper()
    if normalized in {"QUEUED", "DONE", "COMPLETED", "FAILED", "ERROR"} or (progress >= 60 and status_color.lightness() <= 120):
        return resolve_text_on_surface(palette, status_color)
    return _copy(build_theme_tokens(palette).text_primary)

def resolve_status_color(palette: QPalette, raw_status: str) -> QColor:
    """Map job statuses to semantic, palette-derived colors."""

    tokens = build_theme_tokens(palette)
    normalized = str(raw_status or "READY").upper()
    if normalized in {"READY", "QUEUED"}:
        return _copy(tokens.status_ready)
    if normalized == "ANALYZING":
        return _copy(tokens.status_analyzing)
    if normalized in {"RUNNING", "PROCESSING"}:
        return _copy(tokens.status_processing)
    if normalized in {"DONE", "COMPLETED"}:
        return _copy(tokens.status_done)
    if normalized in {"FAILED", "ERROR"}:
        return _copy(tokens.status_error)
    if normalized == "CANCELLED":
        return _copy(tokens.status_cancelled)
    return _copy(tokens.status_ready)
