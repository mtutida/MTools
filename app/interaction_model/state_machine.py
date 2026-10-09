from enum import Enum, auto
from typing import Callable, List

class ApplicationState(Enum):
    INITIALIZING = auto()
    IDLE = auto()
    RUNNING = auto()
    SHUTTING_DOWN = auto()
    TERMINATED = auto()

class InvalidTransitionError(Exception):
    pass

class StateMachine:

    def __init__(self):
        self._state = ApplicationState.INITIALIZING
        self._listeners: List[Callable[[ApplicationState], None]] = []

    @property
    def state(self):
        return self._state

    def register_listener(self, callback: Callable[[ApplicationState], None]):
        self._listeners.append(callback)

    def _notify(self):
        for callback in self._listeners:
            callback(self._state)

    def _transition(self, expected_current, new_state):
        if self._state == ApplicationState.TERMINATED:
            raise InvalidTransitionError("StateMachine already TERMINATED")
        if self._state != expected_current:
            raise InvalidTransitionError(
                f"Invalid transition: {self._state} -> {new_state}"
            )
        self._state = new_state
        self._notify()

    def initialize_complete(self):
        self._transition(ApplicationState.INITIALIZING, ApplicationState.IDLE)

    def start_execution(self):
        self._transition(ApplicationState.IDLE, ApplicationState.RUNNING)

    def execution_finished(self):
        self._transition(ApplicationState.RUNNING, ApplicationState.IDLE)

    def begin_shutdown(self):
        if self._state not in (ApplicationState.IDLE, ApplicationState.RUNNING):
            raise InvalidTransitionError(
                f"Shutdown not allowed from {self._state}"
            )
        self._state = ApplicationState.SHUTTING_DOWN
        self._notify()

    def finalize_shutdown(self):
        self._transition(ApplicationState.SHUTTING_DOWN, ApplicationState.TERMINATED)
