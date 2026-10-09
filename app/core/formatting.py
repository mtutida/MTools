"""Shared display-formatting helpers.

This module intentionally contains only pure formatting helpers so it can be
used by both core profile code and UI presenter/dialog code without creating
Qt dependencies or import cycles.
"""

from __future__ import annotations


def format_bytes(size_bytes: int | float | None, *, allow_non_positive: bool = False) -> str | None:
    """Format a byte count with binary units.

    Args:
        size_bytes: Raw byte count.
        allow_non_positive: Preserve legacy call sites that displayed 0 as
            ``0.0 B``. Most UI summary fields treat non-positive values as
            unavailable and should keep the default ``False``.
    """
    if size_bytes is None:
        return None
    try:
        size = float(size_bytes)
    except Exception:
        return None
    if size <= 0 and not allow_non_positive:
        return None
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_bitrate(bitrate_bps: int | float | None) -> str | None:
    """Format a bitrate in bps as kbps/Mbps for display."""
    if bitrate_bps is None:
        return None
    try:
        value = float(bitrate_bps)
    except Exception:
        return None
    if value <= 0:
        return None
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} Mbps"
    return f"{value / 1000:.0f} kbps"


def format_mb_value(value: int | float | str | None, *, max_decimals: int = 2) -> str:
    """Format a decimal MB value without locale commas or unnecessary zeros."""
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if abs(number - round(number)) < 0.005:
        return str(int(round(number)))
    return f"{number:.{max_decimals}f}".rstrip("0").rstrip(".")


def parse_duration_seconds(raw: object) -> float | None:
    """Parse numeric, MM:SS or HH:MM:SS duration values into seconds."""
    if raw in (None, "", "?", "N/A"):
        return None
    text = str(raw).strip().replace(",", ".")
    if not text:
        return None
    try:
        if ":" in text:
            parts = text.split(":")
            if len(parts) == 3:
                h, m, sec = parts
                value = int(h) * 3600 + int(m) * 60 + float(sec)
            elif len(parts) == 2:
                m, sec = parts
                value = int(m) * 60 + float(sec)
            else:
                return None
        else:
            value = float(text)
    except Exception:
        return None
    return value if value > 0 else None


def parse_resolution(raw: object) -> tuple[int, int] | None:
    """Parse WIDTHxHEIGHT-style values into a positive integer resolution."""
    if raw in (None, "", "?", "N/A"):
        return None
    text = str(raw).strip().lower().replace("×", "x").replace("*", "x").replace(" ", "")
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        width = int(float(left))
        height = int(float(right))
    except Exception:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def parse_fps(raw: object) -> float | None:
    """Parse FPS values, including ffprobe rational strings such as 30000/1001."""
    if raw in (None, "", "?", "N/A"):
        return None
    text = str(raw).strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except Exception:
        try:
            if "/" not in text:
                return None
            numerator_text, denominator_text = text.split("/", 1)
            numerator = float(numerator_text)
            denominator = float(denominator_text)
            if denominator <= 0:
                return None
            value = numerator / denominator
        except Exception:
            return None
    return value if value > 0 else None
