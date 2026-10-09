"""Safe, idempotent migration of CompriMidia user data to MTools CompactMe."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from app.core.paths import (
    CONFIG_PATH,
    LEGACY_CONFIG_PATH,
    LEGACY_LOG_DIR,
    LEGACY_SNAPSHOTS_DIR,
)

LEGACY_FALLBACK_CONFIG_PATH = Path("config.json")
CANONICAL_SETTINGS_ORGANIZATION = "MTools"
CANONICAL_SETTINGS_APPLICATION = "CompactMe"
LEGACY_SETTINGS_ORGANIZATION = "MediaCompressor"
LEGACY_SETTINGS_APPLICATION = "MediaCompressor"

LEGACY_ENVIRONMENT_ALIASES = {
    "COMPACTME_PYTHON": ("COMPRIMIDIA_PYTHON", "MEDIACOMPRESSOR_PYTHON"),
    "COMPACTME_EMBED_FFMPEG": ("COMPRIMIDIA_EMBED_FFMPEG",),
    "COMPACTME_RUN_FFMPEG_CAPABILITY_CHECK": (
        "COMPRIMIDIA_RUN_FFMPEG_CAPABILITY_CHECK",
    ),
    "COMPACTME_SKIP_FFMPEG_CAPABILITY_CHECK": (
        "COMPRIMIDIA_SKIP_FFMPEG_CAPABILITY_CHECK",
    ),
    "COMPACTME_ENABLE_FROZEN_FRAME_SAMPLING": (
        "COMPRIMIDIA_ENABLE_FROZEN_FRAME_SAMPLING",
    ),
}


class SettingsStore(Protocol):
    def contains(self, key: str) -> bool: ...
    def value(self, key: str, default=None): ...
    def setValue(self, key: str, value) -> None: ...
    def sync(self) -> None: ...


@dataclass(frozen=True)
class MigrationResult:
    config_migrated_from: Path | None = None
    qsettings_migrated: bool = False
    preserved_legacy_logs: bool = False
    preserved_legacy_snapshots: bool = False
    skipped: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def resolve_legacy_environment(
    canonical_name: str, *, environ: Mapping[str, str] | None = None
) -> str | None:
    """Use the canonical value first, then its supported legacy aliases."""
    environment = os.environ if environ is None else environ
    if environment.get(canonical_name):
        return environment[canonical_name]
    for legacy_name in LEGACY_ENVIRONMENT_ALIASES.get(canonical_name, ()):
        if environment.get(legacy_name):
            return environment[legacy_name]
    return None


def _read_json_object(path: Path) -> dict | None:
    try:
        # Legacy Windows tools may write UTF-8 JSON with a BOM. Accept it so
        # a valid CompriMidia configuration is not silently treated as absent.
        with path.open("r", encoding="utf-8-sig") as stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _atomic_write_text(
    target: Path, text: str, *, replace: Callable[[str, str], None] = os.replace
) -> None:
    """Publish only a complete config; leave its source untouched."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = temporary.name
        replace(temporary_path, str(target))
        temporary_path = None
    finally:
        if temporary_path:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                pass


def migrate_configuration(
    *,
    destination: Path = CONFIG_PATH,
    legacy_config: Path = LEGACY_CONFIG_PATH,
    fallback_config: Path = LEGACY_FALLBACK_CONFIG_PATH,
    replace: Callable[[str, str], None] = os.replace,
) -> tuple[Path | None, tuple[str, ...], tuple[str, ...]]:
    """Migrate the first usable source only when no destination exists.

    A valid destination wins. An invalid existing destination is also left
    intact: replacing it would be destructive and needs an explicit recovery
    policy, outside this phase.
    """
    destination = Path(destination)
    if destination.exists():
        if _read_json_object(destination) is not None:
            return None, ("destination configuration already valid",), ()
        return None, ("destination configuration exists but is not valid JSON",), ()

    for candidate in (Path(legacy_config), Path(fallback_config)):
        if not candidate.exists():
            continue
        contents = _read_json_object(candidate)
        if contents is None:
            continue
        try:
            _atomic_write_text(destination, json.dumps(contents, indent=2), replace=replace)
        except OSError as exc:
            return None, (), (f"configuration migration failed: {exc}",)
        return candidate, (), ()
    return None, ("no usable legacy configuration found",), ()


def migrate_qsettings_last_import_dir(
    settings_factory: Callable[[str, str], SettingsStore] | None = None,
) -> bool:
    """Copy only the known last-import-directory preference."""
    if settings_factory is None:
        from PySide6.QtCore import QSettings
        settings_factory = QSettings

    destination = settings_factory(
        CANONICAL_SETTINGS_ORGANIZATION, CANONICAL_SETTINGS_APPLICATION
    )
    if destination.contains("last_import_dir"):
        return False
    legacy = settings_factory(LEGACY_SETTINGS_ORGANIZATION, LEGACY_SETTINGS_APPLICATION)
    if not legacy.contains("last_import_dir"):
        return False
    value = legacy.value("last_import_dir")
    if value in (None, ""):
        return False
    destination.setValue("last_import_dir", value)
    destination.sync()
    return True


def migrate_user_data(
    *,
    destination: Path = CONFIG_PATH,
    legacy_config: Path = LEGACY_CONFIG_PATH,
    fallback_config: Path = LEGACY_FALLBACK_CONFIG_PATH,
    legacy_log_dir: Path = LEGACY_LOG_DIR,
    legacy_snapshots_dir: Path = LEGACY_SNAPSHOTS_DIR,
    settings_factory: Callable[[str, str], SettingsStore] | None = None,
    replace: Callable[[str, str], None] = os.replace,
) -> MigrationResult:
    """Run phase-one migration without deleting or rewriting legacy state.

    Logs and snapshots are deliberately only reported. Their retention and
    cleanup semantics remain undecided, so moving them is deferred.
    """
    destination = Path(destination)
    for directory in (destination.parent, destination.parent / "logs", destination.parent / "snapshots"):
        directory.mkdir(parents=True, exist_ok=True)

    source, skipped, errors = migrate_configuration(
        destination=destination, legacy_config=legacy_config,
        fallback_config=fallback_config, replace=replace,
    )
    try:
        settings_migrated = migrate_qsettings_last_import_dir(settings_factory)
    except Exception as exc:
        settings_migrated = False
        errors = (*errors, f"QSettings migration failed: {exc}")
    return MigrationResult(
        config_migrated_from=source,
        qsettings_migrated=settings_migrated,
        preserved_legacy_logs=Path(legacy_log_dir).exists(),
        preserved_legacy_snapshots=Path(legacy_snapshots_dir).exists(),
        skipped=skipped,
        errors=errors,
    )
