# =========================================
# Bootstrap UI — FASE 14.3
# Inicialização mínima de QApplication
# Sem alterar Engine/Scheduler
# =========================================

import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QToolTip

from app.core.app_version import APP_NAME, APP_VERSION, WINDOWS_APP_USER_MODEL_ID
from app.ui.startup_splash import StartupSplash


_SUPPORTED_EXPLORER_MEDIA_EXTENSIONS = (
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v", ".wmv",
    ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".vob",
    ".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus", ".alac",
)


def _explorer_file_args(argv):
    """Return media files received from Windows Explorer/Open with.

    Keep this lightweight and local to startup so importing files through
    Explorer does not pull heavy UI/import modules before the splash appears.
    """
    paths = []
    seen = set()
    for raw_arg in list(argv or []):
        arg = str(raw_arg or "").strip().strip('"')
        if not arg or arg.startswith("-"):
            continue
        normalized = os.path.normpath(os.path.abspath(arg))
        if not os.path.isfile(normalized):
            continue
        if not normalized.lower().endswith(_SUPPORTED_EXPLORER_MEDIA_EXTENSIONS):
            continue
        key = os.path.normcase(normalized)
        if key in seen:
            continue
        seen.add(key)
        paths.append(normalized)
    return paths


def _set_windows_app_user_model_id() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        # Runtime identity for the future MTools suite entry.
        app_id = WINDOWS_APP_USER_MODEL_ID
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _flush_startup_ui(qt_app: QApplication, splash: StartupSplash, message: str, progress: int) -> None:
    splash.set_step(message, progress)
    qt_app.processEvents()


def _startup_window_was_closed(window) -> bool:
    """Return whether startup was cancelled by closing the main window."""
    return not window.isVisible()


def main():
    _set_windows_app_user_model_id()
    explorer_paths = _explorer_file_args(sys.argv[1:])
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setOrganizationName("MTools")

    splash = StartupSplash()
    splash.show_centered()
    _flush_startup_ui(qt_app, splash, "Preparando interface...", 12)

    # Imports that can trigger heavier application modules are intentionally
    # delayed until after QApplication and the splash are visible. This keeps
    # the installed build responsive during cold starts.
    from app.application_context import ApplicationContext
    from app.core.user_data_migration import migrate_user_data
    from app.ui.app_shell import AppShell
    from app.ui.theme_mode_controller import apply_configured_theme_mode
    from app.ui.theme_tokens import build_theme_tokens, color_to_win_bgr

    ctx = None
    try:
        _flush_startup_ui(qt_app, splash, "Carregando serviços...", 32)
        migrate_user_data()
        ctx = ApplicationContext()
        ctx.logger.install_global_hooks()
        ctx.logger.info(
            f"{APP_NAME} {APP_VERSION} starting (Python {sys.version.split()[0]})"
        )
        ctx.logger.log_startup_checkpoint('application_context_ready')
        ctx.logger.log_startup_checkpoint('qapplication_created')

        QToolTip.setFont(QFont("Segoe UI", 9))

        _flush_startup_ui(qt_app, splash, "Aplicando tema...", 58)
        apply_configured_theme_mode()
        tokens = build_theme_tokens(qt_app.palette())
        ctx.logger.log_startup_checkpoint('qapplication_configured')

        _flush_startup_ui(qt_app, splash, "Preparando janela principal...", 82)
        window = AppShell(ctx)
        ctx.logger.info("Window object created")
        ctx.logger.log_startup_checkpoint('app_shell_created')

        window.resize(960, 620)
        window.show()
        if explorer_paths:
            ctx.logger.info(f"Explorer/Open with startup import requested for {len(explorer_paths)} file(s)")
            QTimer.singleShot(250, lambda paths=list(explorer_paths): window.start_import_batch(paths=paths))
        splash.finish(qt_app, f"Abrindo {APP_NAME}...", 180)
        splash.close()
        ctx.logger.log_startup_checkpoint('window_shown')

        # CloseMainWindow can arrive while StartupSplash.finish() is running
        # its nested event loop.  In that case AppShell has already performed
        # its normal closeEvent/RunController shutdown, but entering
        # QApplication.exec() afterwards would revive the process without a
        # main window.  Respect the close and terminate naturally.
        if _startup_window_was_closed(window):
            ctx.logger.info("Startup cancelled by AppShell close during splash")
            sys.exit(0)

        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes

                DWMWA_CAPTION_COLOR = 35
                hwnd = int(window.winId())
                color = ctypes.c_int(color_to_win_bgr(tokens.surface_window_caption))

                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd),
                    ctypes.c_int(DWMWA_CAPTION_COLOR),
                    ctypes.byref(color),
                    ctypes.sizeof(color),
                )
                ctx.logger.log_startup_checkpoint('windows_caption_color_applied')
            except Exception:
                ctx.logger.exception('event=windows_caption_color_failed')

        exit_code = qt_app.exec()
        ctx.logger.info(f"Application shutdown (exit_code={exit_code})")
        sys.exit(exit_code)

    except Exception:
        splash.close()
        if ctx is not None:
            ctx.logger.exception('event=startup_failure')
        raise


if __name__ == "__main__":
    main()
