import os
import tempfile
import unittest
from pathlib import Path


def resolve_import_directory(saved_value: str | None, home: str, public_desktop: str) -> str:
    candidate = os.path.normpath(str(saved_value or ""))
    user_desktop = os.path.normpath(os.path.join(home, "Desktop"))
    if os.path.normcase(candidate) == os.path.normcase(user_desktop) and os.path.isdir(public_desktop):
        return public_desktop
    return candidate if os.path.isdir(candidate) else public_desktop


class ImportDirectorySelectionTests(unittest.TestCase):
    def test_missing_saved_directory_falls_back_to_home(self):
        with tempfile.TemporaryDirectory() as temp:
            home = str(Path(temp) / "home")
            os.mkdir(home)
            missing = str(Path(temp) / "removed")
            public_desktop = str(Path(temp) / "public")
            os.mkdir(public_desktop)
            self.assertEqual(resolve_import_directory(missing, home, public_desktop), public_desktop)

    def test_existing_saved_directory_is_preserved_and_normalized(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "input"
            folder.mkdir()
            mixed = str(folder).replace("\\", "/")
            public_desktop = str(Path(temp) / "public")
            os.mkdir(public_desktop)
            self.assertEqual(resolve_import_directory(mixed, str(Path(temp)), public_desktop), os.path.normpath(mixed))

    def test_user_desktop_saved_value_prefers_public_desktop(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "home"
            user_desktop = home / "Desktop"
            public_desktop = Path(temp) / "public"
            user_desktop.mkdir(parents=True)
            public_desktop.mkdir()
            self.assertEqual(
                resolve_import_directory(str(user_desktop), str(home), str(public_desktop)),
                str(public_desktop),
            )


if __name__ == "__main__":
    unittest.main()
