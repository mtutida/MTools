import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

from app.core.paths import CONFIG_PATH

CONFIG_FILE = str(CONFIG_PATH)
LEGACY_CONFIG_FILE = Path("config.json")


@dataclass(frozen=True)
class AppConfiguration:
    # app preferences
    theme_mode: str = "system"
    assisted_import_default: bool = True
    auto_open_profile_settings_after_import: bool = False
    show_global_progress: bool = True
    confirm_delete: bool = True
    allow_exit_during_processing: bool = False
    detailed_logging: bool = False

    # backward-compatible legacy preference alias
    auto_open_configuration_after_import: bool = False

    # global output behavior (legacy surface, not App Preferences)
    output_directory: str = ""
    use_source_directory: bool = True
    output_naming_mode: int = 1
    output_suffix: str = "_compressed"
    output_filename_pattern: str = "{name}_compressed"
    collision_policy: int = 0

    # contextual quick-mode selection baseline
    quick_profile_preset: int = 4


class ConfigurationService:
    _instance = None

    def __init__(self):
        self._config = self._load()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = ConfigurationService()
        return cls._instance

    def get(self):
        return self._config

    def update(self, **kwargs):
        data = asdict(self._config)
        data.update(kwargs)

        if "auto_open_profile_settings_after_import" in data:
            data["auto_open_configuration_after_import"] = data["auto_open_profile_settings_after_import"]
        elif "auto_open_configuration_after_import" in data:
            data["auto_open_profile_settings_after_import"] = data["auto_open_configuration_after_import"]

        filtered = {
            k: v for k, v in data.items()
            if k in AppConfiguration.__annotations__
        }

        new_cfg = AppConfiguration(**filtered)
        self._save(new_cfg)
        self._config = new_cfg

    def _load(self):
        source = Path(CONFIG_FILE)
        if not source.exists() and LEGACY_CONFIG_FILE.exists():
            source = LEGACY_CONFIG_FILE

        if not source.exists():
            cfg = AppConfiguration()
            self._save(cfg)
            return cfg

        try:
            with open(source, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "auto_open_profile_settings_after_import" not in data and "auto_open_configuration_after_import" in data:
                data["auto_open_profile_settings_after_import"] = data["auto_open_configuration_after_import"]
            if "auto_open_configuration_after_import" not in data and "auto_open_profile_settings_after_import" in data:
                data["auto_open_configuration_after_import"] = data["auto_open_profile_settings_after_import"]

            filtered = {
                k: v for k, v in data.items()
                if k in AppConfiguration.__annotations__
            }
            cfg = AppConfiguration(**filtered)
            if Path(CONFIG_FILE) != source:
                self._save(cfg)
            return cfg
        except Exception:
            cfg = AppConfiguration()
            self._save(cfg)
            return cfg

    def _save(self, cfg):
        target = Path(CONFIG_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(cfg), f, indent=2)
        os.replace(tmp, target)
