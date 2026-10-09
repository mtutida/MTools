"""Resolve FFmpeg tooling used only by local build and validation workflows.

The distributed application never resolves this shared development location at
runtime. PyInstaller copies the selected ffmpeg.exe and ffprobe.exe into the
frozen application's ``bin`` directory.

Resolution precedence is deliberately explicit:
1. ``COMPACTME_FFMPEG_DIR`` developer override;
2. canonical MTools-Shared installation next to the repository;
3. the existing project-local source folders, kept as compatibility fallbacks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

CANONICAL_SHARED_RELATIVE = Path("MTools-Shared/tools/ffmpeg/ffmpeg-9.0.1-essentials_build")
LOCAL_SOURCE_DIRS = ("bin", "tools", "ffmpeg", "ffmpeg/bin")

@dataclass(frozen=True)
class ToolingResolution:
    folder: Path
    source: str
    ffmpeg: Path
    ffprobe: Path

def _as_bin_folder(path: Path) -> Path:
    return path if path.name.lower() == "bin" else path / "bin"

def candidate_folders(project_root: Path, *, environ: Mapping[str, str] | None = None, shared_root: Path | None = None) -> list[tuple[str, Path]]:
    environment = os.environ if environ is None else environ
    candidates: list[tuple[str, Path]] = []
    explicit = environment.get("COMPACTME_FFMPEG_DIR", "").strip()
    if explicit:
        candidates.append(("COMPACTME_FFMPEG_DIR", _as_bin_folder(Path(explicit))))
    canonical_root = shared_root if shared_root is not None else project_root.parent / CANONICAL_SHARED_RELATIVE
    candidates.append(("MTools-Shared", _as_bin_folder(canonical_root)))
    candidates.extend((f"project:{relative}", project_root / relative) for relative in LOCAL_SOURCE_DIRS)
    return candidates

def resolve_tooling(project_root: Path, *, environ: Mapping[str, str] | None = None, shared_root: Path | None = None) -> ToolingResolution | None:
    for source, folder in candidate_folders(project_root, environ=environ, shared_root=shared_root):
        ffmpeg = folder / "ffmpeg.exe"
        ffprobe = folder / "ffprobe.exe"
        if ffmpeg.is_file() and ffprobe.is_file():
            return ToolingResolution(folder, source, ffmpeg, ffprobe)
    return None

def validate_tooling(resolution: ToolingResolution, *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> tuple[bool, str]:
    for executable, expected in ((resolution.ffmpeg, "ffmpeg version"), (resolution.ffprobe, "ffprobe version")):
        try:
            result = run([str(executable), "-version"], capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{executable.name} could not run: {exc}"
        output = f"{result.stdout}\n{result.stderr}".lower()
        if result.returncode != 0 or expected not in output:
            return False, f"{executable.name} is not a valid {expected.split()[0]} executable"
    return True, "validated"

def _main(arguments: Sequence[str]) -> int:
    resolution = resolve_tooling(Path.cwd())
    if resolution is None:
        print("ERROR: no complete ffmpeg.exe/ffprobe.exe pair was found.")
        return 10
    valid, message = validate_tooling(resolution)
    if not valid:
        print(f"ERROR: {message}")
        return 11
    if "--json" in arguments:
        import json
        print(json.dumps({"folder": str(resolution.folder), "source": resolution.source, "ffmpeg": str(resolution.ffmpeg), "ffprobe": str(resolution.ffprobe)}))
        return 0
    print(f"Selected source: {resolution.source}")
    print(f"Selected source folder: {resolution.folder}")
    print(f"ffmpeg: {resolution.ffmpeg}")
    print(f"ffprobe: {resolution.ffprobe}")
    return 0

if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
