import json
import os
import subprocess
import tempfile
from typing import Optional

from app.core.formatting import parse_duration_seconds as _parse_duration_seconds

from app.core.ffmpeg_binaries import resolve_ffmpeg, resolve_ffprobe
from app.core.subprocess_utils import run_no_window


PROBE_TIMEOUT = 30
ENCODE_TIMEOUT = 120
_PROBE_COMMANDS = (
    [],
    ["-analyzeduration", "100M", "-probesize", "100M"],
    ["-fflags", "+genpts", "-err_detect", "ignore_err", "-analyzeduration", "100M", "-probesize", "100M"],
)



def _probe_format(ffprobe: str, path: str) -> Optional[dict]:
    for extra in _PROBE_COMMANDS:
        cmd = [
            ffprobe,
            "-v", "quiet",
            *extra,
            "-print_format", "json",
            "-show_streams",
            "-show_format",
            path,
        ]
        try:
            result = run_no_window(cmd, capture_output=True, text=True, check=False, timeout=PROBE_TIMEOUT)
            data = json.loads(result.stdout or "{}")
            fmt = data.get("format") or {}
            if fmt:
                streams = data.get("streams") or []
                fmt["_streams"] = streams
                return fmt
        except Exception:
            continue
    return None


def _pick_duration_seconds(fmt: dict) -> float | None:
    streams = fmt.get("_streams") or []
    candidates = [fmt.get("duration")]
    for stream in streams:
        candidates.extend([
            stream.get("duration"),
            (stream.get("tags") or {}).get("DURATION"),
            (stream.get("tags") or {}).get("duration"),
        ])

    for raw in candidates:
        if isinstance(raw, str) and ":" in raw:
            try:
                h, m, s = raw.split(":")
                value = int(h) * 3600 + int(m) * 60 + float(s)
                if value > 0:
                    return value
            except Exception:
                pass
        value = _safe_float(raw)
        if value and value > 0:
            return value
    return None


def _pick_size_bytes(fmt: dict) -> int | None:
    value = fmt.get("size")
    try:
        size = int(float(value))
        return size if size > 0 else None
    except Exception:
        return None


def _run_sample_encode(cmd: list[str]) -> bool:
    result = run_no_window(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=ENCODE_TIMEOUT,
    )
    return result.returncode == 0


def _build_sample_commands(
    ffmpeg: str,
    path: str,
    tmp_path: str,
    effective_offset: float,
    effective_sample_duration: int,
    crf: int,
) -> list[list[str]]:
    common_tail = [
        "-map", "0:v:0?",
        "-map", "0:a:0?",
        "-sn",
        "-dn",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", str(crf),
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        tmp_path,
    ]

    fast_seek = [
        ffmpeg,
        "-y",
        "-ss", str(effective_offset),
        "-t", str(effective_sample_duration),
        "-i", path,
        *common_tail,
    ]

    robust_seek = [
        ffmpeg,
        "-y",
        "-fflags", "+genpts",
        "-err_detect", "ignore_err",
        "-analyzeduration", "100M",
        "-probesize", "100M",
        "-i", path,
        "-ss", str(effective_offset),
        "-t", str(effective_sample_duration),
        "-avoid_negative_ts", "make_zero",
        *common_tail,
    ]

    zero_seek = [
        ffmpeg,
        "-y",
        "-fflags", "+genpts",
        "-err_detect", "ignore_err",
        "-analyzeduration", "100M",
        "-probesize", "100M",
        "-i", path,
        "-t", str(effective_sample_duration),
        "-avoid_negative_ts", "make_zero",
        *common_tail,
    ]

    return [fast_seek, robust_seek, zero_seek]


def _fallback_estimate_from_source(original_size: int, ratio: float = 0.90) -> Optional[int]:
    if original_size <= 0:
        return None
    estimated = int(original_size * ratio)
    return estimated if estimated > 0 else None


def _safe_float(value) -> float | None:
    if value in (None, "", "?", "N/A"):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
        return number if number > 0 else None
    except Exception:
        return None


def parse_duration_seconds(raw) -> float | None:
    return _parse_duration_seconds(raw)


def estimate_bitrate_from_size(size_bytes: int | float | None, duration_raw) -> int | None:
    duration_seconds = parse_duration_seconds(duration_raw)
    if duration_seconds is None or duration_seconds <= 0:
        return None
    try:
        size_value = int(float(size_bytes)) if size_bytes is not None else None
    except Exception:
        return None
    if not size_value or size_value <= 0:
        return None
    bitrate = int((size_value * 8) / duration_seconds)
    return bitrate if bitrate > 0 else None


def quick_preset_to_crf(preset: int | None) -> int:
    try:
        value = int(preset) if preset is not None else 4
    except Exception:
        value = 4
    value = max(0, min(15, value))
    crf_map = {
        0: 18, 1: 20, 2: 22, 3: 24,
        4: 26, 5: 28, 6: 30, 7: 32,
        8: 34, 9: 36, 10: 38, 11: 40,
        12: 42, 13: 44, 14: 46, 15: 46,
    }
    return crf_map.get(value, 28)


def _crf_floor_ratio(crf: int) -> float:
    clamped = max(18, min(46, int(crf)))
    # Map quality-oriented CRFs close to the source bitrate and progressively
    # allow stronger reductions for more aggressive compression presets.
    position = (clamped - 18) / 28.0
    return 0.97 - (0.72 * position)

def estimate_size_crf(path: str, crf: int = 28, sample_offset: int = 30, sample_duration: int = 8) -> Optional[int]:
    tmp_path = None
    try:
        ffmpeg = resolve_ffmpeg()
        ffprobe = resolve_ffprobe(ffmpeg)
        if not ffmpeg or not ffprobe:
            return None

        source_format = _probe_format(ffprobe, path)
        if not source_format:
            return None

        full_duration = _pick_duration_seconds(source_format)
        original_size = _pick_size_bytes(source_format)
        if not full_duration or full_duration <= 0 or not original_size or original_size <= 0:
            return _fallback_estimate_from_source(original_size or 0, ratio=0.92)

        effective_sample_duration = max(2, min(int(sample_duration), max(2, int(full_duration))))
        max_offset = max(0.0, full_duration - effective_sample_duration)
        effective_offset = min(float(sample_offset), max_offset)
        if full_duration <= effective_sample_duration + 1:
            effective_offset = 0.0

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        tmp_path = tmp.name
        tmp.close()

        sample_format = None
        for cmd in _build_sample_commands(
            ffmpeg,
            path,
            tmp_path,
            effective_offset,
            effective_sample_duration,
            crf,
        ):
            try:
                if not _run_sample_encode(cmd):
                    continue
                sample_format = _probe_format(ffprobe, tmp_path)
                if sample_format:
                    break
            except Exception:
                continue

        if not sample_format:
            return _fallback_estimate_from_source(original_size)

        sample_size = _pick_size_bytes(sample_format)
        sample_len = _pick_duration_seconds(sample_format)
        if not sample_size or sample_size <= 0 or not sample_len or sample_len <= 0:
            return _fallback_estimate_from_source(original_size)

        sample_bitrate = (sample_size * 8) / sample_len
        original_bitrate = (original_size * 8) / full_duration

        # Keep the estimate conservative. Sample encodes can understate the full-file
        # bitrate, especially for short, small, low-resolution sources where audio and
        # container overhead weigh more heavily. Bias the estimate upward instead of
        # forcing an optimistic reduction.
        safety_multiplier = 1.12
        if original_size < 20 * 1024 * 1024:
            safety_multiplier += 0.08
        if full_duration < 10 * 60:
            safety_multiplier += 0.04
        if original_bitrate < 450_000:
            safety_multiplier += 0.05

        conservative_sample_bitrate = sample_bitrate * safety_multiplier
        profile_floor_bitrate = original_bitrate * _crf_floor_ratio(crf)
        final_bitrate = max(conservative_sample_bitrate, profile_floor_bitrate)

        if final_bitrate <= 0:
            return _fallback_estimate_from_source(original_size)

        estimated = int((final_bitrate * full_duration) / 8)
        if estimated <= 0:
            return _fallback_estimate_from_source(original_size)
        return estimated

    except Exception:
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
