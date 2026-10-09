import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.interaction_model.execution_controller import ExecutionController


class OutputDirectoryCreationTests(unittest.TestCase):
    def test_missing_directory_with_mixed_separators_is_normalized(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mixed_source = str(root).replace("\\", "/") + "\\sample-8s.mp4"
            output = str(root).replace("\\", "/") + "\\CompactMe\\sample-8s_compressed.mp4"
            job = SimpleNamespace(source_path=mixed_source, output_path=output)
            controller = ExecutionController()
            controller.file_list = object()
            with patch.object(controller, "_ask_create_directory", return_value=True):
                self.assertTrue(controller._ensure_output_directories([job]))
            self.assertTrue((root / "CompactMe").is_dir())


if __name__ == "__main__":
    unittest.main()
