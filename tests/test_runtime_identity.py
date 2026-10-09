import os
import unittest
from pathlib import Path

from app.app_metadata import APP_NAME as METADATA_APP_NAME
from app.core.app_version import APP_NAME, APP_VERSION, WINDOWS_APP_USER_MODEL_ID
from app.core.output_naming import DEFAULT_SOURCE_OUTPUT_SUBDIR, _resolve_source_output_directory
from app.core.paths import (
    COMPACTME_DATA_PARTS,
    LEGACY_APP_FOLDER_NAME,
    LEGACY_USER_DATA_DIR,
    USER_DATA_DIR,
)
from app.core.user_data_migration import (
    CANONICAL_SETTINGS_APPLICATION,
    CANONICAL_SETTINGS_ORGANIZATION,
    LEGACY_ENVIRONMENT_ALIASES,
    LEGACY_SETTINGS_APPLICATION,
    LEGACY_SETTINGS_ORGANIZATION,
    migrate_qsettings_last_import_dir,
)


class FakeSettings:
    values = {}

    def __init__(self, organization, application):
        self.key = (organization, application)
        self.values.setdefault(self.key, {})

    def contains(self, key):
        return key in self.values[self.key]

    def value(self, key, default=None):
        return self.values[self.key].get(key, default)

    def setValue(self, key, value):
        self.values[self.key][key] = value

    def sync(self):
        pass


class RuntimeIdentityTests(unittest.TestCase):
    def setUp(self):
        FakeSettings.values = {}

    def test_active_runtime_name_is_canonical(self):
        self.assertEqual(APP_NAME, "MTools CompactMe")
        self.assertEqual(METADATA_APP_NAME, APP_NAME)
        self.assertEqual(APP_VERSION, "1.1.0-rc395")

    def test_qsettings_namespace_is_canonical(self):
        self.assertEqual((CANONICAL_SETTINGS_ORGANIZATION, CANONICAL_SETTINGS_APPLICATION), ("MTools", "CompactMe"))
        self.assertEqual((LEGACY_SETTINGS_ORGANIZATION, LEGACY_SETTINGS_APPLICATION), ("MediaCompressor", "MediaCompressor"))

    def test_new_qsettings_value_precedes_legacy(self):
        canonical = (CANONICAL_SETTINGS_ORGANIZATION, CANONICAL_SETTINGS_APPLICATION)
        legacy = (LEGACY_SETTINGS_ORGANIZATION, LEGACY_SETTINGS_APPLICATION)
        FakeSettings.values[canonical] = {"last_import_dir": "C:/New"}
        FakeSettings.values[legacy] = {"last_import_dir": "C:/Legacy"}
        self.assertFalse(migrate_qsettings_last_import_dir(FakeSettings))
        self.assertEqual(FakeSettings.values[canonical]["last_import_dir"], "C:/New")

    def test_legacy_qsettings_completes_missing_canonical_value(self):
        legacy = (LEGACY_SETTINGS_ORGANIZATION, LEGACY_SETTINGS_APPLICATION)
        canonical = (CANONICAL_SETTINGS_ORGANIZATION, CANONICAL_SETTINGS_APPLICATION)
        FakeSettings.values[legacy] = {"last_import_dir": "C:/Legacy"}
        self.assertTrue(migrate_qsettings_last_import_dir(FakeSettings))
        self.assertEqual(FakeSettings.values[canonical]["last_import_dir"], "C:/Legacy")
        self.assertEqual(FakeSettings.values[legacy]["last_import_dir"], "C:/Legacy")

    def test_app_user_model_id_is_canonical(self):
        self.assertEqual(WINDOWS_APP_USER_MODEL_ID, "MTools.CompactMe")

    def test_data_path_is_canonical_and_legacy_path_is_explicit(self):
        self.assertEqual(COMPACTME_DATA_PARTS, ("MTools", "CompactMe"))
        self.assertEqual(USER_DATA_DIR.parts[-2:], ("MTools", "CompactMe"))
        self.assertEqual(LEGACY_APP_FOLDER_NAME, "CompriMidia")
        self.assertEqual(LEGACY_USER_DATA_DIR.name, "CompriMidia")

    def test_new_source_output_uses_compactme_without_moving_legacy_folder(self):
        self.assertEqual(DEFAULT_SOURCE_OUTPUT_SUBDIR, "CompactMe")
        legacy_source = os.path.join("C:", "media", "CompriMidia")
        self.assertEqual(
            _resolve_source_output_directory(legacy_source),
            os.path.join(legacy_source, "CompactMe"),
        )
        compactme_source = os.path.join("C:", "media", "CompactMe")
        self.assertEqual(_resolve_source_output_directory(compactme_source), compactme_source)

    def test_transitional_environment_aliases_are_retained(self):
        self.assertIn("COMPRIMIDIA_PYTHON", LEGACY_ENVIRONMENT_ALIASES["COMPACTME_PYTHON"])
        self.assertIn("MEDIACOMPRESSOR_PYTHON", LEGACY_ENVIRONMENT_ALIASES["COMPACTME_PYTHON"])
        self.assertIn("COMPRIMIDIA_ENABLE_FROZEN_FRAME_SAMPLING", LEGACY_ENVIRONMENT_ALIASES["COMPACTME_ENABLE_FROZEN_FRAME_SAMPLING"])


if __name__ == "__main__":
    unittest.main()

