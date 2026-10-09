
class CancelToken:
    """Cooperative cancellation token.
    
    - Controlled externally (Scheduler)
    - Read-only from Engine perspective
    - No lifecycle authority
    - No thread creation
    """

    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled
