import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class GlobalBarNudgeLifecycleTests(unittest.TestCase):
    def test_nudge_button_process_exits_normally(self):
        script = textwrap.dedent(
            """
            import sys
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication, QPushButton
            from app.ui.global_bar import _install_normal_state_text_nudge

            app = QApplication([])
            button = QPushButton("nudge")
            _install_normal_state_text_nudge(button, 1)
            button.show()
            QTimer.singleShot(300, button.close)
            sys.exit(app.exec())
            """
        )
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"stdout={result.stdout}\nstderr={result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
