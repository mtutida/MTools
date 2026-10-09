"""Application entry point for the verified Shared Media Runtime contract."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from tools.shared_media_runtime import ResolutionError, resolve
_HANDLE = None

_REQUIREMENTS = {"runtimeContractMajor": 1, "certifiedRuntimeIds": ["media-0.1.0-win-x64-candidate"], "requiredCapabilities": {"features": ["ffprobe.json"]}, "policy": "shared-then-bundled"}

def _shared_root() -> Path:
    configured = os.environ.get("COMPACTME_SHARED_RUNTIME_ROOT", "").strip()
    if configured: return Path(configured)
    return Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "MTools" / "Shared" / "media"

def _bundled_root() -> Path | None:
    anchor = Path(__file__).resolve().parents[2]
    for candidate in (anchor / "_internal", anchor):
        if (candidate / "bin" / "ffmpeg.exe").is_file(): return candidate
    return None

def _distribution_mode() -> str:
    explicit = os.environ.get("COMPACTME_DISTRIBUTION_MODE", "").strip().lower()
    if explicit in {"full", "apponly"}: return explicit
    anchor = Path(__file__).resolve().parents[2]
    for marker, mode in (("full.marker", "full"), ("apponly.marker", "apponly")):
        if (anchor / marker).is_file() or (anchor / "_internal" / marker).is_file(): return mode
    return "full"

def _resolve():
    global _HANDLE
    if _HANDLE is not None: return _HANDLE
    app_only = _distribution_mode() == "apponly"
    requirements = dict(_REQUIREMENTS); requirements["policy"] = "shared-only" if app_only else "shared-then-bundled"
    _HANDLE = resolve(requirements, runtime_root=_shared_root(), bundled_root=None if app_only else _bundled_root())
    return _HANDLE

def resolve_ffmpeg() -> Optional[str]:
    try: return str(_resolve().ffmpeg)
    except (ResolutionError, OSError, ValueError): return None

def resolve_ffprobe(ffmpeg_path: Optional[str] = None) -> Optional[str]:
    try:
        handle = _resolve()
        if ffmpeg_path and Path(ffmpeg_path).resolve() != handle.ffmpeg.resolve(): return None
        return str(handle.ffprobe)
    except (ResolutionError, OSError, ValueError): return None
