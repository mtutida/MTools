from __future__ import annotations

from typing import Any


def coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(float(value)) if value is not None else None
    except Exception:
        return None
    if parsed is None or parsed <= 0:
        return None
    return parsed


def safe_duration_seconds(raw_duration: Any) -> float | None:
    from app.engine.encode_estimator import parse_duration_seconds

    return parse_duration_seconds(raw_duration)


def format_filter_value(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"
