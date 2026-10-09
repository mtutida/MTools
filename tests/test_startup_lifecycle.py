import unittest
from types import SimpleNamespace

from app.main import _startup_window_was_closed


class StartupLifecycleTests(unittest.TestCase):
    def test_close_during_splash_is_detected_before_event_loop(self):
        window = SimpleNamespace(isVisible=lambda: False)
        self.assertTrue(_startup_window_was_closed(window))

    def test_visible_window_enters_event_loop(self):
        window = SimpleNamespace(isVisible=lambda: True)
        self.assertFalse(_startup_window_was_closed(window))


if __name__ == "__main__":
    unittest.main()
