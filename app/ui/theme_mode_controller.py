from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QToolTip

from app.ancillary.configuration import ConfigurationService
from app.ui.theme_tokens import (
    build_theme_tokens,
    build_tooltip_palette,
    build_tooltip_stylesheet,
    color_to_qss,
    color_to_win_bgr,
)

_LOGGER = logging.getLogger(__name__)

_THEME_MODES = {"system", "light", "dark"}
_BASELINE_SYSTEM_PALETTE: QPalette | None = None
_THEME_EVENT_FILTER: QObject | None = None
_APPLYING_THEME = False
_INTERNAL_THEME_TRANSITION = False
_REAPPLY_SCHEDULED = False
_NATIVE_STYLE_NAME: str | None = None
_LAST_APPLIED_MODE: str | None = None


def normalized_theme_mode(value: str | None) -> str:
    mode = str(value or "system").strip().lower()
    return mode if mode in _THEME_MODES else "system"


class _ThemeModeEventFilter(QObject):
    """Keep manual app theme modes isolated from Windows theme changes.

    Sistema intentionally follows Qt/Windows. Claro and Escuro are explicit app
    modes; if Windows emits ThemeChange/ApplicationPaletteChange while either
    manual mode is active, the controller reapplies the configured app palette
    after Qt finishes its native palette transition. This centralizes the
    correction in the theme controller instead of patching individual windows.
    """

    def eventFilter(self, watched, event):
        if event.type() in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.ThemeChange,
        ):
            if _INTERNAL_THEME_TRANSITION:
                return False
            _schedule_theme_reapply()
        return False

    def handle_color_scheme_changed(self, _color_scheme) -> None:
        """React to Qt's direct Windows color-scheme notification."""
        _schedule_theme_reapply()


def _ensure_theme_event_filter(app: QApplication) -> None:
    global _THEME_EVENT_FILTER
    if _THEME_EVENT_FILTER is not None:
        return
    _THEME_EVENT_FILTER = _ThemeModeEventFilter(app)
    app.installEventFilter(_THEME_EVENT_FILTER)
    try:
        app.styleHints().colorSchemeChanged.connect(
            _THEME_EVENT_FILTER.handle_color_scheme_changed
        )
    except (AttributeError, RuntimeError, TypeError):
        # Older Qt/platform plugins still reach the event-filter fallback.
        _LOGGER.debug("Qt colorSchemeChanged signal is unavailable", exc_info=True)


def _schedule_theme_reapply() -> None:
    global _REAPPLY_SCHEDULED
    if _APPLYING_THEME or _REAPPLY_SCHEDULED:
        return

    _REAPPLY_SCHEDULED = True

    def _reapply() -> None:
        global _REAPPLY_SCHEDULED
        _REAPPLY_SCHEDULED = False
        if _APPLYING_THEME:
            return
        current_mode = normalized_theme_mode(ConfigurationService.instance().get().theme_mode)
        apply_theme_mode(current_mode, force=True)

    QTimer.singleShot(0, _reapply)



def _standard_palette(app: QApplication) -> QPalette:
    try:
        return QPalette(app.style().standardPalette())
    except Exception:
        _LOGGER.debug("Falling back to current application palette", exc_info=True)
        return QPalette(app.palette())


def _is_dark_palette(palette: QPalette) -> bool:
    return QColor(palette.window().color()).lightness() < 128


def _system_prefers_dark(app: QApplication) -> bool:
    """Read the Windows app-theme preference without changing Qt's scheme."""
    if sys.platform == "win32":
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                apps_use_light_theme, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(apps_use_light_theme) == 0
        except (OSError, ValueError, TypeError):
            _LOGGER.debug("Unable to read Windows app theme preference", exc_info=True)
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return _is_dark_palette(app.palette())


def _frozen_palette(source: QPalette) -> QPalette:
    """Copy every palette brush as an explicit role.

    Native Windows palettes contain inherited roles. Passing a regular copy
    back to QApplication after a manual theme lets Qt resolve those roles
    against a different style palette, changing the visible grays. Freezing
    the brushes preserves the exact startup appearance.
    """
    palette = QPalette(source)
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role_value in range(QPalette.ColorRole.NColorRoles.value):
            role = QPalette.ColorRole(role_value)
            palette.setBrush(group, role, source.brush(group, role))
    return palette


def _set_common_groups(palette: QPalette, colors: dict[QPalette.ColorRole, QColor]) -> QPalette:
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, color in colors.items():
            palette.setColor(group, role, QColor(color))

    disabled_text = QColor(colors.get(QPalette.ColorRole.PlaceholderText, colors[QPalette.ColorRole.Text]))
    disabled_button = QColor(colors[QPalette.ColorRole.Button])
    disabled_base = QColor(colors[QPalette.ColorRole.Base])
    disabled_window = QColor(colors[QPalette.ColorRole.Window])

    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.PlaceholderText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled_text)
    for role, color in (
        (QPalette.ColorRole.Window, disabled_window),
        (QPalette.ColorRole.Base, disabled_base),
        (QPalette.ColorRole.AlternateBase, colors[QPalette.ColorRole.AlternateBase]),
        (QPalette.ColorRole.Button, disabled_button),
        (QPalette.ColorRole.Mid, colors[QPalette.ColorRole.Mid]),
        (QPalette.ColorRole.Midlight, colors[QPalette.ColorRole.Midlight]),
        (QPalette.ColorRole.Dark, colors[QPalette.ColorRole.Dark]),
        (QPalette.ColorRole.Light, colors[QPalette.ColorRole.Light]),
        (QPalette.ColorRole.Highlight, colors[QPalette.ColorRole.Highlight]),
        (QPalette.ColorRole.HighlightedText, colors[QPalette.ColorRole.HighlightedText]),
        (QPalette.ColorRole.ToolTipBase, colors[QPalette.ColorRole.ToolTipBase]),
        (QPalette.ColorRole.ToolTipText, colors[QPalette.ColorRole.ToolTipText]),
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(color))
    return palette


def _light_palette(seed: QPalette) -> QPalette:
    """Stable manual light palette.

    This intentionally does not learn from runtime theme transitions. Dynamic
    palette caching caused manual Claro/Escuro to regress depending on the order
    in which Windows and app theme modes were toggled.
    """
    # When Windows already supplies a light palette, use that palette verbatim.
    # Approximating its roles with fixed grays made Sistema, startup and Claro
    # derive three different list surfaces from the same Windows light theme.
    if not _is_dark_palette(seed):
        return QPalette(seed)

    highlight = QColor(seed.highlight().color())
    if not highlight.isValid() or highlight.lightness() < 80:
        highlight = QColor("#2d7dd2")
    palette = QPalette(seed)
    return _set_common_groups(
        palette,
        {
            # Windows 11 light fallback for hosts whose native seed is dark.
            QPalette.ColorRole.Window: QColor("#f3f3f3"),
            QPalette.ColorRole.Base: QColor("#ffffff"),
            QPalette.ColorRole.AlternateBase: QColor("#e9e7e3"),
            QPalette.ColorRole.Text: QColor("#000000"),
            QPalette.ColorRole.WindowText: QColor("#000000"),
            QPalette.ColorRole.Button: QColor("#ffffff"),
            QPalette.ColorRole.ButtonText: QColor("#000000"),
            QPalette.ColorRole.Mid: QColor("#a0a0a0"),
            QPalette.ColorRole.Midlight: QColor("#ffffff"),
            QPalette.ColorRole.Dark: QColor("#787878"),
            QPalette.ColorRole.Light: QColor("#ffffff"),
            QPalette.ColorRole.Shadow: QColor("#000000"),
            QPalette.ColorRole.Highlight: highlight,
            QPalette.ColorRole.HighlightedText: QColor("#ffffff"),
            QPalette.ColorRole.ToolTipBase: QColor("#f3f3f3"),
            QPalette.ColorRole.ToolTipText: QColor("#000000"),
            QPalette.ColorRole.PlaceholderText: QColor("#000000"),
        },
    )


def _dark_palette(seed: QPalette) -> QPalette:
    """Stable manual dark palette.

    Do not cache native dark palettes dynamically. The same fixed palette must be
    applied every time the user chooses Escuro, regardless of previous Windows
    theme transitions.
    """
    highlight = QColor("#4a90e2")
    palette = QPalette(seed)
    return _set_common_groups(
        palette,
        {
            QPalette.ColorRole.Window: QColor("#202124"),
            QPalette.ColorRole.Base: QColor("#2b2d31"),
            QPalette.ColorRole.AlternateBase: QColor("#25272b"),
            QPalette.ColorRole.Text: QColor("#f0f0f0"),
            QPalette.ColorRole.WindowText: QColor("#f0f0f0"),
            QPalette.ColorRole.Button: QColor("#303238"),
            QPalette.ColorRole.ButtonText: QColor("#f0f0f0"),
            QPalette.ColorRole.Mid: QColor("#3a3c40"),
            QPalette.ColorRole.Midlight: QColor("#505258"),
            QPalette.ColorRole.Dark: QColor("#17191c"),
            QPalette.ColorRole.Light: QColor("#5a5c62"),
            QPalette.ColorRole.Shadow: QColor("#000000"),
            QPalette.ColorRole.Highlight: highlight,
            QPalette.ColorRole.HighlightedText: QColor("#ffffff"),
            QPalette.ColorRole.ToolTipBase: QColor("#303238"),
            QPalette.ColorRole.ToolTipText: QColor("#f0f0f0"),
            QPalette.ColorRole.PlaceholderText: QColor("#9a9a9a"),
        },
    )


def palette_for_styled_dialog(source: QPalette) -> QPalette:
    """Return a stable palette source for controlled ancillary dialogs."""
    palette = QPalette(source)
    return _dark_palette(_standard_palette(QApplication.instance())) if _is_dark_palette(palette) and QApplication.instance() is not None else palette


def _palette_for_mode(app: QApplication, mode: str) -> QPalette:
    """Return a deterministic application palette for each theme mode.

    Sistema is deliberately native/dynamic and therefore returns an empty
    palette. Manual Claro/Escuro must not depend on the current Windows theme,
    the startup palette, or the order in which theme changes were made.
    """
    mode = normalized_theme_mode(mode)
    if mode == "system":
        seed = _BASELINE_SYSTEM_PALETTE if _BASELINE_SYSTEM_PALETTE is not None else app.palette()
        if _system_prefers_dark(app):
            return _dark_palette(_standard_palette(app))
        return _light_palette(seed)
    if mode == "light":
        seed = _BASELINE_SYSTEM_PALETTE if _BASELINE_SYSTEM_PALETTE is not None else app.palette()
        return _light_palette(seed)
    if mode == "dark":
        return _dark_palette(_standard_palette(app))
    return QPalette()


def _native_style_name(app: QApplication) -> str:
    global _NATIVE_STYLE_NAME
    if _NATIVE_STYLE_NAME is None:
        try:
            _NATIVE_STYLE_NAME = app.style().objectName()
        except Exception:
            _LOGGER.debug("Unable to read native style name", exc_info=True)
            _NATIVE_STYLE_NAME = ""
    return _NATIVE_STYLE_NAME or ""


def _restore_native_style(app: QApplication) -> None:
    """Keep all theme modes on the same native style/metrics path.

    Manual themes must look like Sistema claro/escuro, not like a secondary
    Qt style.  Reusing the original native style preserves button geometry,
    toolbar vertical alignment, native hover/pressed states, and spacing.
    """
    native_name = _native_style_name(app)
    if not native_name:
        return
    try:
        current_name = app.style().objectName().lower()
        if current_name == native_name.lower():
            return
        # Re-selecting the same native style by name is intentionally avoided
        # unless a previous build switched to another style.  The create/setStyle
        # path is best-effort so older Qt builds keep working.
        from PySide6.QtWidgets import QStyleFactory

        style = QStyleFactory.create(native_name)
        if style is not None:
            app.setStyle(style)
    except Exception:
        _LOGGER.debug("Ignoring native style restore failure", exc_info=True)


def _refresh_tooltips(app: QApplication, palette: QPalette) -> None:
    effective_palette = QPalette(app.palette() if palette is None else palette)
    tokens = build_theme_tokens(effective_palette)
    QToolTip.setPalette(build_tooltip_palette(effective_palette, tokens))
    app.setStyleSheet(
        build_tooltip_stylesheet(tokens)
        + f"\nQWidget[toolTip=\"true\"] {{ background-color: {color_to_qss(tokens.surface_tooltip, include_alpha=False)}; color: {color_to_qss(tokens.text_tooltip, include_alpha=False)}; border: 1px solid {color_to_qss(tokens.border_tooltip, include_alpha=False)}; }}"
    )


def _refresh_windows_caption(app: QApplication, palette: QPalette) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        effective_palette = QPalette(app.palette() if palette is None else palette)
        tokens = build_theme_tokens(effective_palette)
        color = ctypes.c_int(color_to_win_bgr(tokens.surface_window_caption))
        DWMWA_CAPTION_COLOR = 35
        for widget in app.topLevelWidgets():
            if not widget.isVisible():
                continue
            if isinstance(widget, QDialog):
                continue
            hwnd = int(widget.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_int(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
    except Exception:
        _LOGGER.debug("Ignoring Windows caption color refresh failure", exc_info=True)


def apply_theme_mode(mode: str | None = None, *, force: bool = False) -> str:
    global _APPLYING_THEME, _BASELINE_SYSTEM_PALETTE, _INTERNAL_THEME_TRANSITION, _LAST_APPLIED_MODE

    app = QApplication.instance()
    normalized = normalized_theme_mode(mode)
    if app is None:
        return normalized

    _ensure_theme_event_filter(app)

    if mode is None:
        normalized = normalized_theme_mode(ConfigurationService.instance().get().theme_mode)

    if _APPLYING_THEME:
        return normalized

    # Palette/theme notifications are delivered asynchronously on Windows.
    # Reapplying an already active manual mode from those notifications starts
    # another palette transition and leaves the event loop continuously busy.
    if normalized == _LAST_APPLIED_MODE and not force:
        return normalized

    _APPLYING_THEME = True
    _INTERNAL_THEME_TRANSITION = True
    try:
        _restore_native_style(app)
        if _BASELINE_SYSTEM_PALETTE is None:
            _BASELINE_SYSTEM_PALETTE = _frozen_palette(app.palette())

        palette = _palette_for_mode(app, normalized)

        if app.palette() != palette:
            app.setPalette(palette)

        _refresh_tooltips(app, palette)
        _refresh_windows_caption(app, palette)
    finally:
        _APPLYING_THEME = False

    # Qt may enqueue ThemeChange/ApplicationPaletteChange after setPalette()
    # returns.  Those are consequences of this explicit app-mode choice, not a
    # Windows theme change, and a second forced apply makes the title-bar menu
    # briefly unresponsive in Escuro.  Release the guard on the next event-loop
    # turn so genuine later Windows notifications still remain observable.
    QTimer.singleShot(0, _clear_internal_theme_transition)
    _LAST_APPLIED_MODE = normalized
    return normalized


def _clear_internal_theme_transition() -> None:
    global _INTERNAL_THEME_TRANSITION
    _INTERNAL_THEME_TRANSITION = False


def apply_configured_theme_mode() -> str:
    return apply_theme_mode(ConfigurationService.instance().get().theme_mode)
