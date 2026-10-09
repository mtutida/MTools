from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from app.core.ffmpeg_binaries import resolve_ffmpeg, resolve_ffprobe
from app.core.subprocess_utils import run_no_window
from app.core.profiles.common import coerce_positive_int, format_filter_value, safe_duration_seconds
from app.core.formatting import parse_fps, parse_resolution

AUDIO_BITRATE_BY_EXT = {
    ".webm": 96_000,
}
DEFAULT_AUDIO_BITRATE = 128_000
ADVANCED_MIN_GAIN_RATIO = 0.020
ADVANCED_MIN_AUDIO_BITRATE = 24_000
ADVANCED_PREFERRED_MIN_VIDEO_BITRATE = 40_000


def _output_ext(output_ref: str | None) -> str:
    text = str(output_ref or "").strip().lower()
    dot = text.rfind(".")
    return text[dot:] if dot >= 0 else text


def _default_audio_bitrate_for_output(output_ref: str | None) -> int:
    return AUDIO_BITRATE_BY_EXT.get(_output_ext(output_ref), DEFAULT_AUDIO_BITRATE)


def _allow_audio_quality_reduction(job) -> bool:
    return bool(getattr(job, "allow_audio_quality_reduction", False))


def _safe_audio_floor_for_output(output_ref: str | None, *, allow_reduction: bool) -> int:
    if allow_reduction:
        return ADVANCED_MIN_AUDIO_BITRATE
    return 48_000 if _output_ext(output_ref) == ".webm" else 64_000


def _safe_int_value(value: Any) -> int | None:
    return coerce_positive_int(value)


def _reduction_value_kbps(job, attr_name: str, *, default: int, allowed: tuple[int, ...]) -> int:
    raw = getattr(job, attr_name, None)
    value = _safe_int_value(raw)
    if value is None:
        return default
    if allowed:
        return min(allowed, key=lambda item: abs(int(item) - int(value)))
    return value


def _fps_reduction_value(job) -> int:
    return _reduction_value_kbps(job, "advanced_fps_value", default=24, allowed=(30, 24, 20, 15, 12, 10))


def _audio_reduction_bitrate(job) -> int:
    return _reduction_value_kbps(job, "advanced_audio_bitrate_kbps", default=96, allowed=(128, 96, 64, 48, 32)) * 1000


def _policy_reduces_to_value(policy: str) -> bool:
    normalized = str(policy or "").strip().lower()
    return normalized in {"reduce_to_value", "reduce", "reduce_if_needed"} or normalized.startswith("reduce")


def _extract_stream_bitrate(stream: dict[str, Any]) -> int | None:
    candidates = [
        stream.get("bit_rate"),
        (stream.get("tags") or {}).get("BPS"),
        (stream.get("tags") or {}).get("BPS-eng"),
    ]
    for candidate in candidates:
        parsed = _safe_int_value(candidate)
        if parsed is not None:
            return parsed
    return None


@lru_cache(maxsize=256)
def _probe_audio_stream_details(source_path: str) -> tuple[dict[str, Any], ...]:
    ffmpeg = resolve_ffmpeg()
    ffprobe = resolve_ffprobe(ffmpeg)
    if not ffprobe or not source_path:
        return tuple()
    try:
        proc = run_no_window(
            [
                ffprobe,
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=index,codec_name,bit_rate,channels:stream_tags=BPS,BPS-eng",
                "-of", "json",
                source_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout or "{}")
        streams = payload.get("streams") or []
        normalized: list[dict[str, Any]] = []
        for stream in streams:
            normalized.append({
                "codec_name": str(stream.get("codec_name") or "").strip().lower(),
                "bit_rate": _extract_stream_bitrate(stream),
                "channels": _safe_int_value(stream.get("channels")) or 2,
            })
        return tuple(normalized)
    except Exception:
        return tuple()


def selected_output_audio_stream_count(job) -> int:
    source_path = str(getattr(job, "source_path", None) or "").strip()
    streams = _probe_audio_stream_details(source_path) if source_path else tuple()
    if not streams:
        return 0

    policy = str(getattr(job, "audio_track_policy", "KEEP_ALL") or "KEEP_ALL").strip().upper()
    if policy == "KEEP_DEFAULT_ONLY":
        return 1
    if policy == "SELECTED_ONLY":
        try:
            track_index = int(getattr(job, "selected_track_id", None))
        except Exception:
            return 0
        return 1 if 0 <= track_index < len(streams) else 0

    # Current FFmpeg commands do not explicitly map all tracks for KEEP_ALL;
    # with no -map, FFmpeg selects one audio stream by default. Keep the budget
    # aligned with the real command.
    return 1


def _source_resolution(job) -> tuple[int, int] | None:
    return parse_resolution(getattr(job, "resolution", None))


def _source_size_bytes(job) -> int | None:
    for attr in ("source_size", "input_size_bytes"):
        value = coerce_positive_int(getattr(job, attr, None))
        if value is not None:
            return value
    source_path = getattr(job, "source_path", None)
    if not source_path:
        return None
    try:
        value = int(os.path.getsize(source_path))
    except Exception:
        return None
    return value if value > 0 else None


def _maximum_target_bytes_with_gain(job) -> int | None:
    source_size = _source_size_bytes(job)
    if source_size is None:
        return None
    reduction = max(1, int(round(source_size * ADVANCED_MIN_GAIN_RATIO)))
    return max(1, source_size - reduction)


def target_size_bytes(job) -> int | None:
    try:
        target_mb = float(getattr(job, "advanced_target_size_mb", None))
    except Exception:
        return None
    if target_mb <= 0:
        return None
    requested = int(target_mb * 1024 * 1024)
    max_target = _maximum_target_bytes_with_gain(job)
    if max_target is not None:
        requested = min(requested, max_target)
    return max(1, requested)


def _selected_audio_preference_bps(job, output_ref: str | None = None) -> int:
    selected = _safe_int_value(getattr(job, "advanced_audio_bitrate_kbps", None))
    if selected is not None and selected > 0:
        return int(selected) * 1000 if selected < 1000 else int(selected)
    return _default_audio_bitrate_for_output(output_ref or getattr(job, "output_path", None))


def _advanced_audio_quality_reduction_delta_bps(job, output_ref: str | None = None) -> int:
    """Return the total-budget delta unlocked by the Advanced audio checkbox.

    In Advanced, the selected audio bitrate is treated as the preferred ceiling.
    When the checkbox is enabled, the encoder may spend less on audio, and the
    estimate should expose that saving instead of silently refilling it with
    video bitrate.
    """
    if selected_output_audio_stream_count(job) <= 0:
        return 0
    if not _allow_audio_quality_reduction(job):
        return 0
    output_ref = output_ref or getattr(job, "output_path", None)
    preferred = _selected_audio_preference_bps(job, output_ref)
    floor = _safe_audio_floor_for_output(output_ref, allow_reduction=True)
    return max(0, int(preferred) - int(floor))


def _raw_target_total_bitrate_bps(job) -> int | None:
    duration_seconds = safe_duration_seconds(getattr(job, "duration", None))
    target_bytes = target_size_bytes(job)
    if duration_seconds is None or duration_seconds <= 0 or target_bytes is None:
        return None
    bitrate = int((target_bytes * 8) / duration_seconds)
    return bitrate if bitrate > 0 else None


def target_total_bitrate_bps(job) -> int | None:
    # In Advanced, desired size is a hard user contract.  Audio-quality
    # permission may change the audio/video allocation, but must not shrink the
    # total target budget shown in the card.
    return _raw_target_total_bitrate_bps(job)


def _target_strategy(job) -> str:
    strategy = str(
        getattr(job, "advanced_size_strategy", None)
        or getattr(job, "manual_control_type", None)
        or getattr(job, "advanced_target_mode", None)
        or "TARGET_SIZE"
    ).strip().upper()
    if strategy in {"DIRECT_BITRATE", "BITRATE_PRIORITY", "BITRATE"}:
        return "BITRATE_PRIORITY"
    if strategy in {"RESOLUTION_DRIVEN", "RESOLUTION_PRIORITY", "RESOLUTION"}:
        return "RESOLUTION_PRIORITY"
    return "BALANCED"


def audio_bitrate_for_output(job, output_ref: str | None = None) -> int:
    if selected_output_audio_stream_count(job) <= 0:
        return 0

    total_budget = target_total_bitrate_bps(job)
    preferred = _selected_audio_preference_bps(job, output_ref or getattr(job, "output_path", None))
    if total_budget is None or total_budget <= 0:
        return preferred

    policy = str(getattr(job, "advanced_audio_policy", "keep") or "keep").strip().lower()
    strategy = _target_strategy(job)
    allow_reduction = _allow_audio_quality_reduction(job)
    safe_floor = _safe_audio_floor_for_output(output_ref or getattr(job, "output_path", None), allow_reduction=allow_reduction)
    if allow_reduction:
        if _policy_reduces_to_value(policy):
            preferred = min(preferred, _audio_reduction_bitrate(job))
        # Checkbox-only mode: the selected/original bitrate remains the
        # preference/ceiling.  The encoder may only go below it when the total
        # target is too tight to leave a minimally viable video budget.
        max_share = 0.22 if strategy == "RESOLUTION_PRIORITY" else 0.28
        minimum = min(safe_floor, preferred)
    else:
        # Preserve when feasible, but target size is still the hard contract.
        max_share = 0.35 if strategy != "RESOLUTION_PRIORITY" else 0.30
        minimum = safe_floor

    available_for_audio = max(0, total_budget - ADVANCED_PREFERRED_MIN_VIDEO_BITRATE)
    capped_by_share = max(minimum, int(total_budget * max_share))
    audio_budget = min(preferred, available_for_audio, capped_by_share)
    if audio_budget <= 0:
        return 0
    return max(minimum, int(audio_budget))


def output_resolution(job) -> tuple[int | None, int | None]:
    source = _source_resolution(job)
    source_width = source_height = None
    if source is not None:
        source_width, source_height = source

    width = coerce_positive_int(getattr(job, "advanced_width", None))
    height = coerce_positive_int(getattr(job, "advanced_height", None))
    lock_enabled = bool(getattr(job, "advanced_resolution_lock", True))

    if source_width and source_height:
        if width is None and height is None:
            width, height = source_width, source_height
        elif lock_enabled:
            if width is not None and height is None:
                height = max(1, int(round((width / source_width) * source_height)))
            elif height is not None and width is None:
                width = max(1, int(round((height / source_height) * source_width)))
        if width is not None:
            width = min(width, source_width)
        if height is not None:
            height = min(height, source_height)

    if width is None or height is None:
        return width, height
    width = max(2, int(width))
    height = max(2, int(height))
    if width % 2:
        width = max(2, width - 1)
    if height % 2:
        height = max(2, height - 1)
    return width, height


def output_fps(job) -> float | None:
    policy = str(getattr(job, "advanced_fps_policy", "keep") or "keep").strip().lower()
    source_fps = parse_fps(getattr(job, "fps", None))

    if not _policy_reduces_to_value(policy):
        return source_fps

    target_fps = float(_fps_reduction_value(job))
    if source_fps is None or source_fps <= 0:
        return target_fps if target_fps > 0 else None

    # The selected value is a ceiling: Advanced must never display or encode
    # an FPS higher than the source.
    return min(source_fps, target_fps)


def _advanced_overhead_bps(job) -> int:
    total = target_total_bitrate_bps(job)
    if total is None or total <= 0:
        return 0
    # Reserve mux/metadata/filler drift so the final container stays near the
    # requested target instead of overshooting it.
    return max(4_000, int(total * 0.05))


def compute_video_bitrate_bps(job, output_ref: str | None = None) -> int | None:
    total = target_total_bitrate_bps(job)
    if total is None or total <= 0:
        return None
    audio = audio_bitrate_for_output(job, output_ref or getattr(job, "output_path", None))
    overhead = _advanced_overhead_bps(job)
    video = total - max(0, audio) - overhead
    return video if video > 0 else 1


def estimate_size_bytes(job) -> int | None:
    # The visible Advanced estimate must respect the requested target size.
    return target_size_bytes(job)


def estimate_total_bitrate_bps(job) -> int | None:
    return target_total_bitrate_bps(job)


def build_video_filters(job) -> list[str]:
    source = _source_resolution(job)
    target_width, target_height = output_resolution(job)
    filters: list[str] = []
    if source is not None and target_width is not None and target_height is not None:
        source_width, source_height = source
        if target_width != source_width or target_height != source_height:
            filters.append(f"scale={int(target_width)}:{int(target_height)}")

    policy = str(getattr(job, "advanced_fps_policy", "keep") or "keep").strip().lower()
    if _policy_reduces_to_value(policy):
        target_fps = float(_fps_reduction_value(job))
        fps = parse_fps(getattr(job, "fps", None))
        # O valor escolhido funciona como teto: nunca aumenta FPS original.
        if fps is not None and fps > target_fps:
            filters.append(f"fps={format_filter_value(target_fps)}")
    return filters


def build_ffmpeg_options(job, output_ext: str, audio_stream_options: list[str] | None = None) -> list[str]:
    audio_stream_options = list(audio_stream_options or [])
    video_bitrate = compute_video_bitrate_bps(job, output_ext)
    filter_args: list[str] = []
    filters = build_video_filters(job)
    if filters:
        filter_args = ["-vf", ",".join(filters)]

    if video_bitrate is None or video_bitrate <= 0:
        return audio_stream_options + filter_args + ["-c:v", "libx264", "-crf", "28", "-movflags", "+faststart"]

    audio_bitrate = audio_bitrate_for_output(job, output_ext)
    if output_ext == ".webm":
        args = audio_stream_options + filter_args + [
            "-c:v", "libvpx-vp9",
            "-b:v", str(video_bitrate),
            "-minrate", str(video_bitrate),
            "-maxrate", str(video_bitrate),
        ]
        if audio_bitrate > 0:
            args += ["-c:a", "libopus", "-b:a", str(audio_bitrate)]
        else:
            args += ["-an"]
        return args

    args = audio_stream_options + filter_args + [
        "-c:v", "libx264",
        "-b:v", str(video_bitrate),
        "-minrate", str(video_bitrate),
        "-maxrate", str(video_bitrate),
        "-bufsize", str(max(video_bitrate * 2, video_bitrate + 1)),
        "-x264-params", "nal-hrd=cbr:filler=1",
    ]
    if audio_bitrate > 0:
        args += ["-c:a", "aac", "-b:a", str(audio_bitrate)]
    else:
        args += ["-an"]
    return args + ["-movflags", "+faststart"]
