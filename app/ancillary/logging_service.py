import faulthandler
import logging
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

from app.core.paths import LOG_DIR



LOG_FILE = LOG_DIR / 'app.log'
ERROR_LOG_FILE = LOG_DIR / 'error.log'

APP_LOG_MAX_BYTES = 2 * 1024 * 1024
APP_LOG_BACKUP_COUNT = 5
ERROR_LOG_MAX_BYTES = 2 * 1024 * 1024
ERROR_LOG_BACKUP_COUNT = 10

_FAULTHANDLER_FILE = None


class LoggingService:

    _initialized = False
    _global_hooks_installed = False

    def __init__(self):

        if LoggingService._initialized:
            self.logger = logging.getLogger('app')
            self.error_logger = logging.getLogger('app.error')
            return

        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        )

        app_logger = logging.getLogger('app')
        app_logger.setLevel(logging.INFO)
        app_logger.propagate = False
        app_logger.handlers.clear()

        app_file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=APP_LOG_MAX_BYTES,
            backupCount=APP_LOG_BACKUP_COUNT,
            encoding='utf-8',
        )
        app_file_handler.setLevel(logging.INFO)
        app_file_handler.setFormatter(formatter)

        app_stream_handler = logging.StreamHandler()
        app_stream_handler.setLevel(logging.INFO)
        app_stream_handler.setFormatter(formatter)

        app_logger.addHandler(app_file_handler)
        app_logger.addHandler(app_stream_handler)

        error_logger = logging.getLogger('app.error')
        error_logger.setLevel(logging.ERROR)
        error_logger.propagate = False
        error_logger.handlers.clear()

        error_file_handler = RotatingFileHandler(
            ERROR_LOG_FILE,
            maxBytes=ERROR_LOG_MAX_BYTES,
            backupCount=ERROR_LOG_BACKUP_COUNT,
            encoding='utf-8',
        )
        error_file_handler.setLevel(logging.ERROR)
        error_file_handler.setFormatter(formatter)

        error_logger.addHandler(error_file_handler)

        self.logger = app_logger
        self.error_logger = error_logger

        LoggingService._initialized = True


    def install_global_hooks(self):
        if LoggingService._global_hooks_installed:
            return

        self._install_sys_excepthook()
        self._install_threading_excepthook()
        self._install_qt_message_handler()
        self._install_faulthandler()

        LoggingService._global_hooks_installed = True
        self.logger.info("Global crash logging hooks installed")

    def log_startup_checkpoint(self, checkpoint: str):
        self.logger.info(f"startup_checkpoint={checkpoint}")

    def _install_sys_excepthook(self):
        previous_hook = sys.excepthook
        error_logger = self.error_logger

        def _hook(exc_type, exc_value, exc_traceback):
            if issubclass(exc_type, KeyboardInterrupt):
                previous_hook(exc_type, exc_value, exc_traceback)
                return

            formatted = ''.join(
                traceback.format_exception(exc_type, exc_value, exc_traceback)
            ).strip()
            error_logger.error(
                "event=unhandled_exception\n"
                "thread=main\n"
                f"exception_type={exc_type.__name__}\n"
                f"exception_message={exc_value}\n"
                f"traceback={formatted}\n"
                "---"
            )

        sys.excepthook = _hook

    def _install_threading_excepthook(self):
        if not hasattr(threading, 'excepthook'):
            return

        previous_hook = threading.excepthook
        error_logger = self.error_logger

        def _hook(args):
            if args.exc_type and issubclass(args.exc_type, KeyboardInterrupt):
                previous_hook(args)
                return

            formatted = ''.join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            ).strip()
            thread_name = getattr(args.thread, 'name', 'unknown')
            error_logger.error(
                "event=thread_exception\n"
                f"thread={thread_name}\n"
                f"exception_type={args.exc_type.__name__}\n"
                f"exception_message={args.exc_value}\n"
                f"traceback={formatted}\n"
                "---"
            )

        threading.excepthook = _hook

    def _install_qt_message_handler(self):
        app_logger = self.logger
        error_logger = self.error_logger

        def _qt_message_handler(message_type, context, message):
            if self._is_ignorable_qt_multimedia_message(message):
                return

            file_name = context.file or '<qt>'
            line_number = context.line or 0
            function_name = context.function or '<unknown>'
            payload = (
                f"event=qt_message\n"
                f"qt_message_type={self._map_qt_message_type(message_type)}\n"
                f"source={file_name}:{line_number}\n"
                f"function={function_name}\n"
                f"message={message}\n"
                "---"
            )

            if message_type in (QtMsgType.QtFatalMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtWarningMsg):
                error_logger.error(payload)
            else:
                app_logger.info(payload)

        qInstallMessageHandler(_qt_message_handler)

    def _install_faulthandler(self):
        global _FAULTHANDLER_FILE

        if _FAULTHANDLER_FILE is None:
            fault_log_path = LOG_DIR / 'crash.log'
            _FAULTHANDLER_FILE = open(fault_log_path, 'a', encoding='utf-8')

        if not faulthandler.is_enabled():
            faulthandler.enable(_FAULTHANDLER_FILE, all_threads=True)

    @staticmethod
    def _is_ignorable_qt_multimedia_message(message) -> bool:
        text = str(message or "")
        noisy_markers = (
            "Using Qt multimedia with FFmpeg",
            "Extra data:",
            "Could not update timestamps for skipped samples",
            "deprecated pixel format used",
        )
        return any(marker in text for marker in noisy_markers)

    @staticmethod
    def _map_qt_message_type(message_type) -> str:
        mapping = {
            QtMsgType.QtDebugMsg: 'debug',
            QtMsgType.QtInfoMsg: 'info',
            QtMsgType.QtWarningMsg: 'warning',
            QtMsgType.QtCriticalMsg: 'critical',
            QtMsgType.QtFatalMsg: 'fatal',
        }
        return mapping.get(message_type, 'unknown')

    def info(self, message: str):
        self.logger.info(message)

    def warning(self, message: str):
        self.logger.warning(message)

    def error(self, message: str):
        self.logger.error(message)

    def exception(self, message: str):
        self.error_logger.exception(message)

    def technical_error(self, message: str):
        self.error_logger.error(message)
