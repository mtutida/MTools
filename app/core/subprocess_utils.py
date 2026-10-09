from __future__ import annotations

import os
import subprocess
from typing import Any


def no_window_subprocess_kwargs() -> dict[str, Any]:
    """Return subprocess options that suppress child console windows on Windows.

    PyInstaller windowed builds (console=False/runw.exe) can still show a
    console whenever ffmpeg/ffprobe is launched. CREATE_NO_WINDOW prevents
    those transient cmd windows without changing behavior on other platforms.
    """
    if os.name != "nt":
        return {}
    kwargs: dict[str, Any] = {}
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if create_no_window:
        kwargs["creationflags"] = create_no_window
    startupinfo_class = getattr(subprocess, "STARTUPINFO", None)
    startf_use_showwindow = getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    sw_hide = getattr(subprocess, "SW_HIDE", 0)
    if startupinfo_class is not None:
        startupinfo = startupinfo_class()
        startupinfo.dwFlags |= startf_use_showwindow
        startupinfo.wShowWindow = sw_hide
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _merge_no_window_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    merged = dict(kwargs)

    # Defensive text decoding for FFmpeg/FFprobe output on Windows.
    # Without an explicit error strategy, Python uses the active ANSI code page
    # in text mode and can raise UnicodeDecodeError for bytes such as 0x81,
    # aborting compression before the real FFmpeg failure can be reported.
    if merged.get("text") or merged.get("universal_newlines"):
        merged.setdefault("errors", "replace")

    for key, value in no_window_subprocess_kwargs().items():
        merged.setdefault(key, value)
    return merged


def run_no_window(*popenargs: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(*popenargs, **_merge_no_window_kwargs(kwargs))


def popen_no_window(*popenargs: Any, **kwargs: Any) -> subprocess.Popen:
    return subprocess.Popen(*popenargs, **_merge_no_window_kwargs(kwargs))
