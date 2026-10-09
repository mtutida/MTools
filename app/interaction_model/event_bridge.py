import logging
import threading

try:
    from PySide6.QtCore import QObject, Qt, Signal, QThread
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover - keeps non-Qt tooling/imports safe
    QObject = None
    Qt = None
    Signal = None
    QThread = None
    QApplication = None


_LOGGER = logging.getLogger("app.interaction_model.event_bridge")


class _QueuedEventDispatcher(QObject):
    """Qt-owned dispatcher used to deliver background events on the UI thread."""

    event_emitted = Signal(str, object)

    def __init__(self, bridge):
        super().__init__()
        self._bridge = bridge
        self.event_emitted.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    def _dispatch(self, event_type, payload):
        self._bridge._dispatch(event_type, payload)


class InteractionEventBridge:
    """Global event bridge between Scheduler, InteractionModel and UI.

    The compression runner emits progress/status events from a Python worker
    thread. Qt widgets and models must only be touched from the QApplication
    thread; delivering those events synchronously to UI subscribers can crash the
    process without a Python traceback, especially when several files are queued
    and progress events arrive in quick succession. The bridge therefore keeps
    direct delivery on the UI thread, but automatically queues emissions coming
    from background threads once a QApplication exists.
    """

    def __init__(self):
        self._listeners = []
        self._lock = threading.RLock()
        self._dispatcher = None

    def subscribe(self, callback):
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def unsubscribe(self, callback):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _ensure_dispatcher(self):
        if QObject is None or QApplication is None:
            return None

        app = QApplication.instance()
        if app is None:
            return None

        with self._lock:
            if self._dispatcher is None:
                self._dispatcher = _QueuedEventDispatcher(self)
                try:
                    self._dispatcher.moveToThread(app.thread())
                except Exception:
                    _LOGGER.debug("Could not move event dispatcher to QApplication thread", exc_info=True)
            return self._dispatcher

    def _is_ui_thread(self):
        if QThread is None or QApplication is None:
            return True
        app = QApplication.instance()
        if app is None:
            return True
        try:
            return QThread.currentThread() is app.thread()
        except Exception:
            _LOGGER.debug("Could not determine QApplication thread; dispatching directly", exc_info=True)
            return True

    def _dispatch(self, event_type, payload):
        with self._lock:
            listeners = list(self._listeners)

        for cb in listeners:
            try:
                cb(event_type, payload)
            except Exception:
                _LOGGER.debug("Interaction event listener failed for event %s", event_type, exc_info=True)

    def emit(self, event_type, payload):
        if not self._is_ui_thread():
            dispatcher = self._ensure_dispatcher()
            if dispatcher is not None:
                dispatcher.event_emitted.emit(event_type, payload)
                return

        self._dispatch(event_type, payload)


# global singleton bridge
event_bridge = InteractionEventBridge()
