import json
import tempfile
import unittest
from pathlib import Path

from app.core.user_data_migration import (
    CANONICAL_SETTINGS_APPLICATION,
    CANONICAL_SETTINGS_ORGANIZATION,
    LEGACY_SETTINGS_APPLICATION,
    LEGACY_SETTINGS_ORGANIZATION,
    migrate_user_data,
    resolve_legacy_environment,
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


class UserDataMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.destination = self.root / "MTools" / "CompactMe" / "config.json"
        self.legacy = self.root / "CompriMidia" / "config.json"
        self.fallback = self.root / "fallback-config.json"
        self.logs = self.root / "CompriMidia" / "logs"
        self.snapshots = self.root / "CompriMidia" / "snapshots"
        FakeSettings.values = {}

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def migrate(self, **kwargs):
        return migrate_user_data(
            destination=self.destination,
            legacy_config=self.legacy,
            fallback_config=self.fallback,
            legacy_log_dir=self.logs,
            legacy_snapshots_dir=self.snapshots,
            settings_factory=FakeSettings,
            **kwargs,
        )

    def test_first_migration_preserves_source(self):
        original = {"theme_mode": "dark", "output_suffix": "_legacy"}
        self.write_json(self.legacy, original)
        result = self.migrate()
        self.assertEqual(result.config_migrated_from, self.legacy)
        self.assertEqual(json.loads(self.destination.read_text(encoding="utf-8")), original)
        self.assertEqual(json.loads(self.legacy.read_text(encoding="utf-8")), original)

    def test_first_migration_accepts_utf8_bom_and_preserves_source(self):
        original = {"theme_mode": "dark", "output_suffix": "_legacy_upgrade_test"}
        self.legacy.parent.mkdir(parents=True, exist_ok=True)
        self.legacy.write_text(
            json.dumps(original), encoding="utf-8-sig"
        )
        legacy_bytes = self.legacy.read_bytes()

        result = self.migrate()

        self.assertEqual(result.config_migrated_from, self.legacy)
        self.assertEqual(
            json.loads(self.destination.read_text(encoding="utf-8")), original
        )
        self.assertEqual(self.legacy.read_bytes(), legacy_bytes)

    def test_existing_destination_has_precedence(self):
        self.write_json(self.destination, {"theme_mode": "light"})
        self.write_json(self.legacy, {"theme_mode": "dark"})
        result = self.migrate()
        self.assertIsNone(result.config_migrated_from)
        self.assertEqual(json.loads(self.destination.read_text(encoding="utf-8"))["theme_mode"], "light")

    def test_missing_origin_changes_nothing(self):
        result = self.migrate()
        self.assertIsNone(result.config_migrated_from)
        self.assertFalse(self.destination.exists())

    def test_partial_origin_is_migrated(self):
        self.write_json(self.legacy, {"theme_mode": "dark"})
        self.migrate()
        self.assertEqual(json.loads(self.destination.read_text(encoding="utf-8")), {"theme_mode": "dark"})

    def test_repeat_is_idempotent(self):
        self.write_json(self.legacy, {"theme_mode": "dark"})
        first = self.migrate()
        before = self.destination.read_bytes()
        second = self.migrate()
        self.assertEqual(first.config_migrated_from, self.legacy)
        self.assertIsNone(second.config_migrated_from)
        self.assertEqual(self.destination.read_bytes(), before)

    def test_legacy_precedes_fallback_with_both_preferences(self):
        original = {"theme_mode": "dark", "output_suffix": "_legacy_upgrade_test"}
        self.write_json(self.legacy, original)
        self.write_json(self.fallback, {"theme_mode": "system", "output_suffix": "_compressed"})
        before = self.legacy.read_bytes()
        result = self.migrate()
        self.assertEqual(result.config_migrated_from, self.legacy)
        self.assertEqual(json.loads(self.destination.read_text(encoding="utf-8")), original)
        self.assertEqual(self.legacy.read_bytes(), before)

    def test_bom_migration_is_consumed_by_configuration_service(self):
        from unittest.mock import patch
        from app.ancillary.configuration import ConfigurationService
        original = {"theme_mode": "dark", "output_suffix": "_legacy_upgrade_test"}
        self.legacy.parent.mkdir(parents=True, exist_ok=True)
        self.legacy.write_text(json.dumps(original), encoding="utf-8-sig")
        before = self.legacy.read_bytes()
        self.migrate()
        with patch("app.ancillary.configuration.CONFIG_FILE", str(self.destination)):
            service = ConfigurationService()
            self.assertEqual(service.get().theme_mode, "dark")
            self.assertEqual(service.get().output_suffix, "_legacy_upgrade_test")
        self.assertEqual(self.legacy.read_bytes(), before)

    def test_canonical_both_preferences_precede_bom_legacy(self):
        original = {"theme_mode": "light", "output_suffix": "_canonical"}
        self.write_json(self.destination, original)
        self.legacy.parent.mkdir(parents=True, exist_ok=True)
        self.legacy.write_text(json.dumps({"theme_mode": "dark", "output_suffix": "_legacy_upgrade_test"}), encoding="utf-8-sig")
        before = self.destination.read_bytes()
        self.assertIsNone(self.migrate().config_migrated_from)
        self.assertEqual(self.destination.read_bytes(), before)

    def test_migrated_preferences_are_idempotent_after_legacy_changes(self):
        original = {"theme_mode": "dark", "output_suffix": "_legacy_upgrade_test"}
        self.write_json(self.legacy, original)
        self.migrate()
        before = self.destination.read_bytes()
        self.write_json(self.legacy, {"theme_mode": "light", "output_suffix": "_later"})
        self.assertIsNone(self.migrate().config_migrated_from)
        self.assertEqual(self.destination.read_bytes(), before)

    def test_missing_legacy_uses_valid_fallback(self):
        original = {"theme_mode": "dark", "output_suffix": "_fallback"}
        self.write_json(self.fallback, original)
        result = self.migrate()
        self.assertEqual(result.config_migrated_from, self.fallback)
        self.assertEqual(json.loads(self.destination.read_text(encoding="utf-8")), original)

    def test_corrupt_legacy_uses_fallback_without_rewriting_source(self):
        self.legacy.parent.mkdir(parents=True, exist_ok=True)
        self.legacy.write_bytes(b'{broken json')
        before = self.legacy.read_bytes()
        original = {"theme_mode": "dark", "output_suffix": "_fallback"}
        self.write_json(self.fallback, original)
        self.assertEqual(self.migrate().config_migrated_from, self.fallback)
        self.assertEqual(json.loads(self.destination.read_text(encoding="utf-8")), original)
        self.assertEqual(self.legacy.read_bytes(), before)

    def test_invalid_utf8_legacy_is_skipped_and_preserved(self):
        self.legacy.parent.mkdir(parents=True, exist_ok=True)
        self.legacy.write_bytes(b'\xff\xfeinvalid')
        before = self.legacy.read_bytes()
        self.assertIsNone(self.migrate().config_migrated_from)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.legacy.read_bytes(), before)

    def test_non_object_legacy_is_skipped(self):
        self.write_json(self.legacy, ["dark", "_legacy_upgrade_test"])
        before = self.legacy.read_bytes()
        self.assertIsNone(self.migrate().config_migrated_from)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.legacy.read_bytes(), before)

    def test_invalid_destination_is_preserved(self):
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_bytes(b'{invalid destination')
        self.write_json(self.legacy, {"theme_mode": "dark", "output_suffix": "_legacy_upgrade_test"})
        before = self.destination.read_bytes()
        self.assertIsNone(self.migrate().config_migrated_from)
        self.assertEqual(self.destination.read_bytes(), before)

    def test_error_during_replace_preserves_source_and_destination(self):
        original = {"theme_mode": "dark"}
        self.write_json(self.legacy, original)
        def fail_replace(source, target):
            raise OSError("simulated failure")
        result = self.migrate(replace=fail_replace)
        self.assertFalse(self.destination.exists())
        self.assertEqual(json.loads(self.legacy.read_text(encoding="utf-8")), original)
        self.assertTrue(result.errors)

    def test_legacy_environment_resolution(self):
        self.assertEqual(
            resolve_legacy_environment("COMPACTME_PYTHON", environ={"MEDIACOMPRESSOR_PYTHON": "legacy"}),
            "legacy",
        )
        self.assertEqual(
            resolve_legacy_environment("COMPACTME_PYTHON", environ={"COMPRIMIDIA_PYTHON": "old"}),
            "old",
        )
        self.assertEqual(
            resolve_legacy_environment("COMPACTME_PYTHON", environ={"COMPACTME_PYTHON": "new", "MEDIACOMPRESSOR_PYTHON": "old"}),
            "new",
        )
        self.assertEqual(
            resolve_legacy_environment("COMPACTME_EMBED_FFMPEG", environ={"COMPACTME_EMBED_FFMPEG": "0", "COMPRIMIDIA_EMBED_FFMPEG": "1"}),
            "0",
        )
        self.assertEqual(
            resolve_legacy_environment("COMPACTME_RUN_FFMPEG_CAPABILITY_CHECK", environ={"COMPRIMIDIA_RUN_FFMPEG_CAPABILITY_CHECK": "1"}),
            "1",
        )
        self.assertEqual(
            resolve_legacy_environment("COMPACTME_SKIP_FFMPEG_CAPABILITY_CHECK", environ={"COMPACTME_SKIP_FFMPEG_CAPABILITY_CHECK": "0", "COMPRIMIDIA_SKIP_FFMPEG_CAPABILITY_CHECK": "1"}),
            "0",
        )
        self.assertEqual(
            resolve_legacy_environment("COMPACTME_ENABLE_FROZEN_FRAME_SAMPLING", environ={"COMPACTME_ENABLE_FROZEN_FRAME_SAMPLING": "1", "COMPRIMIDIA_ENABLE_FROZEN_FRAME_SAMPLING": "0"}),
            "1",
        )

    def test_qsettings_migration_preserves_legacy_value(self):
        legacy = (LEGACY_SETTINGS_ORGANIZATION, LEGACY_SETTINGS_APPLICATION)
        canonical = (CANONICAL_SETTINGS_ORGANIZATION, CANONICAL_SETTINGS_APPLICATION)
        FakeSettings.values[legacy] = {"last_import_dir": "C:/Media"}
        result = self.migrate()
        self.assertTrue(result.qsettings_migrated)
        self.assertEqual(FakeSettings.values[canonical]["last_import_dir"], "C:/Media")
        self.assertEqual(FakeSettings.values[legacy]["last_import_dir"], "C:/Media")

    def test_new_qsettings_value_has_precedence(self):
        legacy = (LEGACY_SETTINGS_ORGANIZATION, LEGACY_SETTINGS_APPLICATION)
        canonical = (CANONICAL_SETTINGS_ORGANIZATION, CANONICAL_SETTINGS_APPLICATION)
        FakeSettings.values[legacy] = {"last_import_dir": "C:/Legacy"}
        FakeSettings.values[canonical] = {"last_import_dir": "C:/New"}
        result = self.migrate()
        self.assertFalse(result.qsettings_migrated)
        self.assertEqual(FakeSettings.values[canonical]["last_import_dir"], "C:/New")

    def test_logs_and_snapshots_are_explicitly_preserved(self):
        self.logs.mkdir(parents=True)
        self.snapshots.mkdir(parents=True)
        result = self.migrate()
        self.assertTrue(result.preserved_legacy_logs)
        self.assertTrue(result.preserved_legacy_snapshots)
        self.assertTrue(self.logs.exists())
        self.assertTrue(self.snapshots.exists())


if __name__ == "__main__":
    unittest.main()
