from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ActiveProductIdentityTests(unittest.TestCase):
    def read(self, relative_path):
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_build_documentation_describes_current_compactme_artifact(self):
        text = self.read("BUILD_WINDOWS.txt")
        self.assertIn("dist\\CompactMe\\CompactMe.exe", text)
        self.assertIn("COMPACTME_PYTHON", text)
        self.assertIn("COMPRIMIDIA_PYTHON legacy alias", text)
        self.assertIn("MEDIACOMPRESSOR_PYTHON legacy alias", text)
        self.assertNotIn("dist\\CompriMidia\\CompriMidia.exe", text)
        self.assertIn("Historical release notes", text)

    def test_build_tooling_uses_canonical_environment_before_legacy_aliases(self):
        build = self.read("build_windows.bat")
        self.assertLess(build.index("if defined COMPACTME_PYTHON"), build.index("if defined COMPRIMIDIA_PYTHON"))
        self.assertLess(build.index("if defined COMPRIMIDIA_PYTHON"), build.index("if not defined PY_CMD if defined MEDIACOMPRESSOR_PYTHON"))
        self.assertIn("COMPACTME_RUN_FFMPEG_CAPABILITY_CHECK", build)

        spec = self.read("CompactMe.spec")
        self.assertIn('os.environ.get("COMPACTME_EMBED_FFMPEG") or os.environ.get("COMPRIMIDIA_EMBED_FFMPEG"', spec)

        binary_validator = self.read("validate_ffmpeg_binaries.bat")
        self.assertLess(binary_validator.index("set \"EMBED=%COMPACTME_EMBED_FFMPEG%\""), binary_validator.index("set \"EMBED=%COMPRIMIDIA_EMBED_FFMPEG%\""))

        capability_validator = self.read("validate_ffmpeg_capabilities.ps1")
        self.assertIn("$env:COMPACTME_SKIP_FFMPEG_CAPABILITY_CHECK", capability_validator)
        self.assertLess(capability_validator.index("$env:COMPACTME_PYTHON"), capability_validator.index("$env:COMPRIMIDIA_PYTHON"))
        self.assertNotIn("[CompriMidia]", capability_validator)
        self.assertIn("[CompactMe] FFmpeg capability validation started.", capability_validator)

    def test_temporary_runtime_assets_use_compactme_names(self):
        profile = self.read("app/core/automatic_profile.py")
        self.assertIn('FRAME_SAMPLING_TEMP_PREFIX = "compactme_frames_"', profile)
        self.assertNotIn("comprimidia_frames_", profile)

        preferences = self.read("app/ui/app_preferences_dialog.py")
        self.assertIn('THEME_ASSET_TEMP_DIR_NAME = "compactme_theme_assets"', preferences)
        self.assertNotIn("mediacompressorpro_theme_assets", preferences)

    def test_installer_version_documentation_separates_rollback_and_baseline(self):
        text = self.read("INSTALLER_NSIS.txt")
        self.assertIn("`1.0.0-rc392`: rollback certificado.", text)
        self.assertIn("`1.0.0-rc393`: baseline publicada", text)
        self.assertIn("`1.1.0-rc395`: candidata técnica atual, ainda não certificada.", text)
        self.assertIn("ProductVersion: 1.1.0-rc395", text)
        self.assertIn("FileVersion:    1.1.0.395", text)

    def test_candidate_version_is_synchronized_across_active_metadata(self):
        self.assertIn('APP_VERSION = "1.1.0-rc395"', self.read("app/core/app_version.py"))
        self.assertIn('APP_VERSION = "1.1.0-rc395"', self.read("app/app_metadata.py"))
        version_info = self.read("version_info.txt")
        self.assertIn("filevers=(1, 1, 0, 395)", version_info)
        self.assertIn("prodvers=(1, 1, 0, 395)", version_info)
        self.assertIn("StringStruct('FileVersion', '1.1.0.395')", version_info)
        self.assertIn("StringStruct('ProductVersion', '1.1.0-rc395')", version_info)
        installer = self.read("CompactMe.nsi")
        self.assertIn('!define APP_VERSION "1.1.0-rc395"', installer)
        self.assertIn('!define APP_VERSION_NUMERIC "1.1.0.395"', installer)
        self.assertIn('!define LEGACY_UNINSTALL_KEY "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\CompriMidia"', installer)


if __name__ == "__main__":
    unittest.main()


