from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class BuildIdentityTests(unittest.TestCase):
    def test_compactme_spec_produces_compactme_artifact(self):
        spec = (ROOT / "CompactMe.spec").read_text(encoding="utf-8")
        self.assertIn('name="CompactMe"', spec)
        self.assertIn('icons" / "compactme.ico"', spec)

    def test_windows_version_metadata_matches_active_identity(self):
        metadata = (ROOT / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn("StringStruct('ProductName', 'MTools CompactMe')", metadata)
        self.assertIn("StringStruct('FileDescription', 'MTools CompactMe')", metadata)
        self.assertIn("StringStruct('InternalName', 'CompactMe')", metadata)
        self.assertIn("StringStruct('OriginalFilename', 'CompactMe.exe')", metadata)

    def test_build_script_targets_compactme_output(self):
        script = (ROOT / "build_windows.bat").read_text(encoding="utf-8")
        self.assertIn("CompactMe.spec", script)
        self.assertIn("dist\\CompactMe\\CompactMe.exe", script)

    def test_active_compactme_icons_exist(self):
        icons = ROOT / "app" / "ui" / "icons"
        for name in ("compactme.ico", "compactme_titlebar.ico", "compactme_icon_256.png"):
            self.assertTrue((icons / name).is_file(), name)

if __name__ == "__main__":
    unittest.main()