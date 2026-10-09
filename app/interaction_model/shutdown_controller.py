from .state_machine import StateMachine

class ShutdownController:
    def __init__(self, state_machine: StateMachine):
        self._state_machine = state_machine
    def request_shutdown(self):
        self._state_machine.begin_shutdown()
        self._state_machine.finalize_shutdown()
