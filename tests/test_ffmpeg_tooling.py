from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "ffmpeg_tooling.py"
SPEC = importlib.util.spec_from_file_location("ffmpeg_tooling", MODULE_PATH)
assert SPEC and SPEC.loader
ffmpeg_tooling = importlib.util.module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = ffmpeg_tooling
SPEC.loader.exec_module(ffmpeg_tooling)

def _pair(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (folder / "ffprobe.exe").write_bytes(b"ffprobe")
class FFmpegToolingResolverTests(unittest.TestCase):
    def test_locates_complete_pair_in_mtools_shared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "CompactMe"
            shared = Path(temporary) / "MTools-Shared" / "tools" / "ffmpeg" / "ffmpeg-9.0.1-essentials_build"
            _pair(shared / "bin")
            resolution = ffmpeg_tooling.resolve_tooling(root, shared_root=shared)
            self.assertIsNotNone(resolution)
            assert resolution is not None
            self.assertEqual("MTools-Shared", resolution.source)
            self.assertEqual(shared / "bin", resolution.folder)

    def test_absence_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "CompactMe"
            self.assertIsNone(ffmpeg_tooling.resolve_tooling(root, shared_root=Path(temporary) / "missing"))

    def test_explicit_developer_override_precedes_shared_and_local(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, shared, explicit = base / "CompactMe", base / "shared", base / "explicit"
            _pair(shared / "bin")
            _pair(root / "bin")
            _pair(explicit / "bin")
            resolution = ffmpeg_tooling.resolve_tooling(root, environ={"COMPACTME_FFMPEG_DIR": str(explicit)}, shared_root=shared)
            self.assertIsNotNone(resolution)
            assert resolution is not None
            self.assertEqual("COMPACTME_FFMPEG_DIR", resolution.source)

    def test_incomplete_pair_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / "shared"
            (shared / "bin").mkdir(parents=True)
            (shared / "bin" / "ffmpeg.exe").write_bytes(b"ffmpeg")
            self.assertIsNone(ffmpeg_tooling.resolve_tooling(Path(temporary) / "CompactMe", shared_root=shared))

    def test_validator_rejects_invalid_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            _pair(folder)
            resolution = ffmpeg_tooling.ToolingResolution(folder, "test", folder / "ffmpeg.exe", folder / "ffprobe.exe")
            def invalid_run(*args, **kwargs):
                return subprocess.CompletedProcess(args[0], 1, "", "not executable")
            valid, _ = ffmpeg_tooling.validate_tooling(resolution, run=invalid_run)
            self.assertFalse(valid)

    def test_validator_accepts_capability_validator_input_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            _pair(folder)
            resolution = ffmpeg_tooling.ToolingResolution(folder, "test", folder / "ffmpeg.exe", folder / "ffprobe.exe")
            def valid_run(command, **kwargs):
                name = Path(command[0]).stem
                return subprocess.CompletedProcess(command, 0, f"{name} version 9.0.1-essentials_build", "")
            valid, message = ffmpeg_tooling.validate_tooling(resolution, run=valid_run)
            self.assertTrue(valid, message)
