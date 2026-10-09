from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from app.core.ffmpeg_binaries import resolve_ffmpeg, resolve_ffprobe
from app.core.subprocess_utils import run_no_window
from app.core.formatting import format_mb_value as _format_mb_value, parse_fps, parse_resolution
from app.core.profiles import advanced as advanced_profile
from app.core.profiles import audio as audio_profile
from app.core.profiles import quick as quick_profile
from app.core.profiles import strategic as strategic_profile
from app.core.automatic_profile import (
    AutomaticMediaAnalysis,
    automatic_recommendation_to_profile_payload,
    build_automatic_recommendation,
)

VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v", ".wmv", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".vob"
)

AUDIO_BITRATE_BY_EXT = {
    ".webm": 96_000,
}
DEFAULT_AUDIO_BITRATE = 128_000
QUICK_MIN_AUDIO_BITRATE = 48_000
QUICK_MIN_VIDEO_BITRATE = 160_000
QUICK_MIN_FALLBACK_VIDEO_BITRATE = 56_000


def _allow_audio_quality_reduction(job) -> bool:
    return bool(getattr(job, "allow_audio_quality_reduction", False))


def _safe_audio_floor_for_output(output_ref: str | None, *, allow_reduction: bool) -> int:
    if allow_reduction:
        return QUICK_MIN_AUDIO_BITRATE
    text = str(output_ref or "").strip().lower()
    dot = text.rfind(".")
    ext = text[dot:] if dot >= 0 else text
    return 48_000 if ext == ".webm" else 64_000

# Public compatibility exports kept for UI/importers while implementation lives in isolated modules.
SMART_STRATEGIES = strategic_profile.SMART_STRATEGIES
SMART_STRATEGY_BASE_GAIN = strategic_profile.SMART_STRATEGY_BASE_GAIN
QUICK_PRESET_TITLES = quick_profile.QUICK_PRESET_TITLES
QUICK_MIN_GAIN_RATIOS = quick_profile.QUICK_MIN_GAIN_RATIOS

__all__ = (
    "VIDEO_EXTENSIONS",
    "SMART_STRATEGIES",
    "SMART_STRATEGY_BASE_GAIN",
    "QUICK_PRESET_TITLES",
    "QUICK_MIN_GAIN_RATIOS",
    "is_aggressive_quick_preset",
    "aggressive_quick_estimate_factor",
    "resolve_job_source_size_bytes",
    "estimated_output_has_gain",
    "estimated_output_is_no_gain",
    "estimated_output_is_actionable",
    "estimate_output_is_actionable",
    "is_video_path",
    "quick_preset_title",
    "default_quick_profile",
    "default_smart_profile",
    "default_audio_profile",
    "default_advanced_bitrate_profile",
    "default_advanced_size_profile",
    "default_advanced_resolution_profile",
    "build_automatic_profile_from_media_info",
    "derive_job_profile_from_payload",
    "build_profile_signature",
    "profile_output_matches_current_settings",
    "build_profile_button_text",
    "build_profile_tooltip",
    "compute_advanced_video_bitrate_bps",
    "compute_job_crf",
    "estimate_size_for_profile",
    "estimate_output_bitrate_for_profile",
    "estimate_output_video_traits",
    "build_ffmpeg_profile_options",
)


QUICK_AUDIO_CONDITIONAL_THRESHOLDS = {
    6: 160_000,
    7: 144_000,
    8: 128_000,
    9: 112_000,
}
QUICK_AUDIO_AGGRESSIVE_TARGETS = {
    10: 112_000,
    11: 96_000,
    12: 96_000,
    13: 80_000,
    14: 64_000,
    15: 64_000,
}

QUICK_AGGRESSIVE_START_INDEX = quick_profile.QUICK_SCALE_START_INDEX
QUICK_FPS_START_INDEX = quick_profile.QUICK_FPS_START_INDEX
QUICK_STANDARD_HEIGHTS = (1080, 720, 640, 480, 432, 360, 240, 144)
# Four-block Quick profile. Levels 9-12 reduce only resolution;
# levels 13-16 keep reducing resolution and also cap FPS.
QUICK_AGGRESSIVE_HEIGHT_RATIOS = (0.90, 0.85, 0.80, 0.75, 0.72, 0.68, 0.64, 0.60)
QUICK_FPS_CAPS = (22.0, 20.0, 18.0, 15.0)
MIN_ESTIMATED_GAIN_BYTES = 128 * 1024
ADVANCED_MIN_GAIN_RATIO = 0.020


def is_aggressive_quick_preset(index: int | None) -> bool:
    return quick_profile.is_aggressive_preset(index)

def aggressive_quick_estimate_factor(index: int | None) -> float:
    return quick_profile.aggressive_estimate_factor(index)

def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(float(value)) if value is not None else None
    except Exception:
        return None
    if parsed is None or parsed <= 0:
        return None
    return parsed


def resolve_job_source_size_bytes(job) -> int | None:
    # Prefer explicit metadata when available. Some UI/import paths populate
    # ``input_size_bytes`` instead of ``source_size``; using both keeps the
    # Quick bitrate contract active even before the run controller normalizes
    # the job.
    for attr in ("source_size", "input_size_bytes", "size_bytes"):
        source_size = _coerce_positive_int(getattr(job, attr, None))
        if source_size is not None:
            return source_size

    source_path = getattr(job, "source_path", None)
    if not source_path:
        return None

    try:
        source_size = int(os.path.getsize(source_path))
    except Exception:
        return None
    return source_size if source_size > 0 else None


@lru_cache(maxsize=256)
def _probe_source_duration_seconds(source_path: str) -> float | None:
    ffprobe = resolve_ffprobe()
    if not ffprobe or not source_path:
        return None
    try:
        proc = run_no_window(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                source_path,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        value = float(str(proc.stdout or "").strip())
        return value if value > 0 else None
    except Exception:
        return None


def _resolve_job_duration_seconds(job) -> float | None:
    for attr in ("duration", "input_duration", "source_duration", "duration_seconds"):
        duration_seconds = _safe_duration_seconds(getattr(job, attr, None))
        if duration_seconds is not None and duration_seconds > 0:
            return duration_seconds

    source_size = resolve_job_source_size_bytes(job)
    input_bitrate = _coerce_positive_int(getattr(job, "input_bitrate", None))
    if source_size is not None and input_bitrate is not None and input_bitrate > 0:
        duration_seconds = (float(source_size) * 8.0) / float(input_bitrate)
        if duration_seconds > 0:
            return duration_seconds

    source_path = str(getattr(job, "source_path", None) or "").strip()
    if source_path:
        return _probe_source_duration_seconds(source_path)
    return None


def estimated_output_has_gain(job, estimated_size_bytes: int | None = None, source_size_bytes: int | None = None) -> bool | None:
    estimated = _coerce_positive_int(estimated_size_bytes)
    if estimated is None:
        estimated = _coerce_positive_int(getattr(job, "estimated_size_bytes", None))
    source_size = _coerce_positive_int(source_size_bytes)
    if source_size is None:
        source_size = resolve_job_source_size_bytes(job)

    if estimated is None or source_size is None:
        return None
    return estimated < source_size


def estimated_output_is_no_gain(job, estimated_size_bytes: int | None = None, source_size_bytes: int | None = None) -> bool:
    return estimated_output_is_actionable(
        job,
        estimated_size_bytes=estimated_size_bytes,
        source_size_bytes=source_size_bytes,
    ) is False


def estimated_output_is_actionable(job, estimated_size_bytes: int | None = None, source_size_bytes: int | None = None) -> bool | None:
    """Return whether the current estimate is worth processing.

    The global contract is that the app should not invite an encode when the
    preview cannot show a meaningful reduction. A strict ``estimated < source``
    check is not enough for the Advanced profile because muxing/CBR overhead can
    turn very small nominal gains into an equal or larger final file.
    """
    estimated = _coerce_positive_int(estimated_size_bytes)
    if estimated is None:
        estimated = _coerce_positive_int(getattr(job, "estimated_size_bytes", None))
    source_size = _coerce_positive_int(source_size_bytes)
    if source_size is None:
        source_size = resolve_job_source_size_bytes(job)

    if estimated is None or source_size is None:
        return None

    if estimated >= source_size:
        return False

    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    # Advanced and Audio need a visible/safe gain buffer. With unchanged or
    # near-preserving parameters a tiny technical reduction can round like the
    # original and may still finish equal or larger after container overhead.
    # Treat those near-original estimates as NO_GAIN so the card action reads
    # "Pronto!" instead of inviting a redundant encode.
    min_gain_ratio = 0.03 if profile_mode in {"advanced", "audio"} else 0.005
    min_gain_bytes = 1 if profile_mode not in {"advanced", "audio"} else min(128 * 1024, max(1, source_size - 1))
    required_reduction = max(1, int(round(source_size * min_gain_ratio)))
    required_reduction = max(required_reduction, min_gain_bytes)
    return estimated <= max(1, source_size - required_reduction)


def _safe_source_fps(job) -> float | None:
    return parse_fps(getattr(job, "fps", None))


def _format_filter_value(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _resolve_aggressive_quick_targets(job, quick_preset: int | None) -> tuple[int | None, float | None]:
    if not is_aggressive_quick_preset(quick_preset):
        return None, None

    source_resolution = _safe_source_resolution(job)
    if source_resolution is None:
        return None, None
    source_height = int(source_resolution[1])
    if source_height <= 0:
        return None, None

    try:
        preset_index = int(quick_preset)
    except Exception:
        return None, None
    step_index = max(0, preset_index - QUICK_AGGRESSIVE_START_INDEX)

    ratio = QUICK_AGGRESSIVE_HEIGHT_RATIOS[min(step_index, len(QUICK_AGGRESSIVE_HEIGHT_RATIOS) - 1)]
    target_height = max(2, int(round((float(source_height) * float(ratio)) / 2.0) * 2))
    # Do not upscale tiny sources; a same-height target keeps the estimator
    # stable while the FPS block can still reduce temporal density.
    target_height = min(source_height, target_height)

    if preset_index < QUICK_FPS_START_INDEX:
        return target_height, None

    source_fps = _safe_source_fps(job)
    if source_fps is None or source_fps <= 0:
        return target_height, None
    fps_step_index = max(0, preset_index - QUICK_FPS_START_INDEX)
    fps_cap = QUICK_FPS_CAPS[min(fps_step_index, len(QUICK_FPS_CAPS) - 1)]
    return target_height, min(source_fps, fps_cap)


def _build_aggressive_video_filters(job, quick_preset: int | None) -> list[str]:
    target_height, target_fps = _resolve_aggressive_quick_targets(job, quick_preset)
    if target_height is None and target_fps is None:
        return []

    filters: list[str] = []
    source_resolution = _safe_source_resolution(job)
    if source_resolution is not None:
        _, source_height = source_resolution
        if target_height is not None and target_height > 0 and target_height < source_height:
            filters.append(f"scale=-2:{int(target_height)}")

    source_fps = _safe_source_fps(job)
    if source_fps is not None and target_fps is not None and 0 < target_fps < source_fps:
        filters.append(f"fps={_format_filter_value(target_fps)}")

    return filters


def is_video_path(path: str | None) -> bool:
    return str(path or "").lower().endswith(VIDEO_EXTENSIONS)


def quick_preset_title(index: int | None) -> str:
    return quick_profile.preset_title(index)

def default_quick_profile() -> dict[str, Any]:
    return quick_profile.default_profile()

def default_smart_profile() -> dict[str, Any]:
    return strategic_profile.default_profile()

def default_audio_profile() -> dict[str, Any]:
    return audio_profile.default_profile()

def _default_advanced_audio_policy() -> dict[str, Any]:
    return {
        "audio_track_policy": "KEEP_ALL",
        "selected_track_id": None,
        "audio_channel_policy": "KEEP_ORIGINAL",
    }


def default_advanced_bitrate_profile(target_kbps: int = 1200) -> dict[str, Any]:
    target_kbps = max(100, int(target_kbps))
    profile = {
        "profile_mode": "advanced",
        "profile_label": f"Avançado/Bitrate {target_kbps} kbps",
        "control_mode": "MANUAL_LEVEL",
        "manual_control_type": "DIRECT_BITRATE",
        "advanced_target_mode": "bitrate",
        "advanced_target_bitrate_kbps": target_kbps,
        "advanced_target_size_mb": None,
        "advanced_width": None,
        "advanced_height": None,
        "advanced_resolution_lock": True,
        "advanced_resolution_bitrate_mode": "explicit",
        "advanced_resolution_bitrate_kbps": target_kbps,
        "advanced_fps_policy": "keep",
        "advanced_fps_value": None,
        "advanced_audio_policy": "keep",
        "advanced_audio_bitrate_kbps": None,
        "allow_audio_quality_reduction": False,
    }
    profile.update(_default_advanced_audio_policy())
    return profile


def default_advanced_size_profile(target_mb: float = 25) -> dict[str, Any]:
    try:
        target_mb = max(0.1, float(target_mb))
    except Exception:
        target_mb = 25.0
    profile = {
        "profile_mode": "advanced",
        "profile_label": f"Avançado/{_format_mb_value(target_mb)} MB",
        "control_mode": "MANUAL_LEVEL",
        "manual_control_type": "TARGET_SIZE",
        "advanced_target_mode": "size",
        "advanced_target_size_mb": target_mb,
        "advanced_target_bitrate_kbps": None,
        "advanced_width": None,
        "advanced_height": None,
        "advanced_resolution_lock": True,
        "advanced_resolution_bitrate_mode": "auto",
        "advanced_resolution_bitrate_kbps": None,
        "advanced_fps_policy": "keep",
        "advanced_fps_value": None,
        "advanced_audio_policy": "keep",
        "advanced_audio_bitrate_kbps": None,
    }
    profile.update(_default_advanced_audio_policy())
    return profile


def default_advanced_resolution_profile(
    width: int | None = None,
    height: int | None = None,
    *,
    bitrate_kbps: int | None = None,
    bitrate_mode: str = "auto",
) -> dict[str, Any]:
    try:
        width = max(1, int(width)) if width is not None else None
    except Exception:
        width = None
    try:
        height = max(1, int(height)) if height is not None else None
    except Exception:
        height = None
    normalized_mode = "explicit" if str(bitrate_mode or "auto").strip().lower() == "explicit" else "auto"
    normalized_bitrate = None
    if normalized_mode == "explicit":
        try:
            normalized_bitrate = max(100, int(float(bitrate_kbps))) if bitrate_kbps is not None else 1200
        except Exception:
            normalized_bitrate = 1200

    resolution_label = f"{width}x{height}" if width and height else "Original"
    profile = {
        "profile_mode": "advanced",
        "profile_label": f"Avançado/Resolução {resolution_label}",
        "control_mode": "MANUAL_LEVEL",
        "manual_control_type": "RESOLUTION_DRIVEN",
        "advanced_target_mode": "resolution",
        "advanced_target_size_mb": None,
        "advanced_target_bitrate_kbps": None,
        "advanced_width": width,
        "advanced_height": height,
        "advanced_resolution_lock": True,
        "advanced_resolution_bitrate_mode": normalized_mode,
        "advanced_resolution_bitrate_kbps": normalized_bitrate,
        "advanced_fps_policy": "keep",
        "advanced_fps_value": None,
        "advanced_audio_policy": "keep",
        "advanced_audio_bitrate_kbps": None,
    }
    profile.update(_default_advanced_audio_policy())
    return profile


def _normalize_manual_control_type(value: Any) -> str:
    manual_type = str(value or "").strip().upper()
    if manual_type in {"DIRECT_BITRATE", "TARGET_SIZE", "RESOLUTION_DRIVEN"}:
        return manual_type

    legacy = str(value or "").strip().lower()
    if legacy == "bitrate":
        return "DIRECT_BITRATE"
    if legacy == "size":
        return "TARGET_SIZE"
    if legacy == "resolution":
        return "RESOLUTION_DRIVEN"
    return "TARGET_SIZE"


def _normalize_audio_profile_fields(source: dict[str, Any]) -> dict[str, Any]:
    audio_track_policy = str(source.get("audio_track_policy") or "KEEP_ALL").strip().upper()
    if audio_track_policy not in {"KEEP_ALL", "KEEP_DEFAULT_ONLY", "SELECTED_ONLY"}:
        audio_track_policy = "KEEP_ALL"

    selected_track_id = source.get("selected_track_id")
    if selected_track_id in ("", "None"):
        selected_track_id = None
    elif selected_track_id is not None:
        try:
            selected_track_id = int(selected_track_id)
        except Exception:
            selected_track_id = None

    raw_track_ids = source.get("selected_track_ids")
    selected_track_ids = None
    if isinstance(raw_track_ids, (list, tuple, set)):
        selected_track_ids = []
        for value in raw_track_ids:
            try:
                selected_track_ids.append(int(value))
            except Exception:
                continue
        selected_track_ids = list(dict.fromkeys(selected_track_ids)) or None

    audio_channel_policy = str(source.get("audio_channel_policy") or "KEEP_ORIGINAL").strip().upper()
    if audio_channel_policy not in {"KEEP_ORIGINAL", "DOWNMIX_TO_STEREO", "DOWNMIX_TO_MONO"}:
        audio_channel_policy = "KEEP_ORIGINAL"

    audio_sample_rate = str(source.get("audio_sample_rate") or "ORIGINAL").strip().upper()
    if audio_sample_rate not in {"ORIGINAL", "44100", "48000"}:
        audio_sample_rate = "ORIGINAL"

    audio_quality_mode = str(source.get("audio_quality_mode") or "BALANCED").strip().upper()
    if audio_quality_mode not in {"QUALITY", "BALANCED", "SPACE"}:
        audio_quality_mode = "BALANCED"

    audio_volume_normalization = str(source.get("audio_volume_normalization") or "OFF").strip().upper()
    if audio_volume_normalization not in {"OFF", "LIGHT", "STANDARD"}:
        audio_volume_normalization = "OFF"

    audio_metadata_policy = str(source.get("audio_metadata_policy") or "PRESERVE").strip().upper()
    if audio_metadata_policy not in {"PRESERVE", "REMOVE"}:
        audio_metadata_policy = "PRESERVE"

    extract_subtitle = bool(source.get("extract_subtitle", False))

    return {
        "audio_track_policy": audio_track_policy,
        "selected_track_id": selected_track_id,
        "selected_track_ids": selected_track_ids,
        "audio_channel_policy": audio_channel_policy,
        "audio_sample_rate": audio_sample_rate,
        "audio_quality_mode": audio_quality_mode,
        "audio_volume_normalization": audio_volume_normalization,
        "audio_metadata_policy": audio_metadata_policy,
        "extract_subtitle": extract_subtitle,
    }




def build_automatic_profile_from_media_info(
    media_info: Any,
    file_size_bytes: int | None,
    *,
    source_path: str | None = None,
) -> dict[str, Any]:
    """Return an existing encoder profile derived from automatic analysis.

    Automático must not create a parallel encoding path. This bridge converts
    the metadata-only recommendation into the current Advanced/Audio profile
    dictionaries, then attaches read-only automatic metadata for the future UI.
    """
    enriched_media_info = _media_info_with_source_path(media_info, source_path)
    analysis = build_automatic_recommendation(enriched_media_info, file_size_bytes or 0)
    payload = automatic_recommendation_to_profile_payload(analysis)
    profile = derive_job_profile_from_payload(payload, source_path=source_path)
    profile.update(_automatic_profile_metadata(analysis))
    return profile


def _media_info_with_source_path(media_info: Any, source_path: str | None) -> Any:
    if not source_path:
        return media_info
    if isinstance(media_info, dict):
        enriched = dict(media_info)
        enriched.setdefault("source_path", source_path)
        enriched.setdefault("extension", os.path.splitext(str(source_path))[1].lower())
        return enriched
    if isinstance(media_info, (tuple, list)):
        return {
            "codec": media_info[0] if len(media_info) > 0 else None,
            "resolution": media_info[1] if len(media_info) > 1 else None,
            "fps": media_info[2] if len(media_info) > 2 else None,
            "duration": media_info[3] if len(media_info) > 3 else None,
            "container": media_info[4] if len(media_info) > 4 else None,
            "input_bitrate_bps": media_info[5] if len(media_info) > 5 else None,
            "audio_streams": media_info[6] if len(media_info) > 6 else [],
            "audio_track_count": media_info[7] if len(media_info) > 7 else None,
            "primary_audio_channels": media_info[8] if len(media_info) > 8 else None,
            "primary_audio_sample_rate": media_info[9] if len(media_info) > 9 else None,
            "source_path": source_path,
            "extension": os.path.splitext(str(source_path))[1].lower(),
        }
    return media_info


def _automatic_profile_metadata(analysis: AutomaticMediaAnalysis) -> dict[str, Any]:
    return {
        "profile_origin": "automatic",
        "automatic_media_kind": analysis.media_kind,
        "automatic_detected_label": analysis.detected_label,
        "automatic_strategy_label": analysis.strategy_label,
        "automatic_confidence": analysis.confidence,
        "automatic_estimated_output_bytes": analysis.estimated_output_bytes,
        "automatic_suggested_width": analysis.suggested_width,
        "automatic_suggested_height": analysis.suggested_height,
        "automatic_suggested_fps": analysis.suggested_fps,
        "automatic_suggested_video_bitrate_bps": analysis.suggested_video_bitrate_bps,
        "automatic_suggested_audio_bitrate_bps": analysis.suggested_audio_bitrate_bps,
        "automatic_output_format": analysis.output_format,
        "automatic_analysis": analysis.to_dict(),
    }

def derive_job_profile_from_payload(payload: dict[str, Any] | None, *, source_path: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return default_quick_profile()

    def _video_output_fields(source: dict[str, Any]) -> dict[str, Any]:
        raw_format = str(source.get("video_output_format") or source.get("video_format") or "").strip()
        raw_extension = str(source.get("video_output_extension") or raw_format or "").strip().lower()
        if not raw_extension:
            return {}
        if not raw_extension.startswith("."):
            raw_extension = "." + raw_extension
        allowed = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v"}
        if raw_extension not in allowed:
            return {}
        return {
            "video_output_format": raw_format.upper() if raw_format else raw_extension.lstrip(".").upper(),
            "video_output_extension": raw_extension,
        }

    video_output_fields = _video_output_fields(payload)

    requested_mode = str(payload.get("compression_mode") or "quick").strip().lower()
    if requested_mode == "automatic":
        media_info = payload.get("automatic_media_info") or payload.get("media_info") or payload.get("probe_result")
        file_size = _coerce_positive_int(
            payload.get("automatic_file_size_bytes")
            or payload.get("file_size_bytes")
            or payload.get("source_size")
        )
        if media_info is not None and file_size is not None:
            return build_automatic_profile_from_media_info(
                media_info,
                file_size,
                source_path=source_path or payload.get("source_path"),
            )
        return default_quick_profile()

    if requested_mode == "smart":
        profile = strategic_profile.normalize_payload(payload, _normalize_audio_profile_fields)
        profile.update(video_output_fields)
        return profile

    if requested_mode == "audio":
        return audio_profile.normalize_payload(payload, _normalize_audio_profile_fields)

    if requested_mode == "advanced":
        manual_control_type = _normalize_manual_control_type(
            payload.get("manual_control_type") or payload.get("advanced_target_mode")
        )
        if manual_control_type == "DIRECT_BITRATE":
            try:
                bitrate_kbps = int(float(payload.get("advanced_target_bitrate_kbps", payload.get("advanced_resolution_bitrate_kbps", 1200))))
            except Exception:
                bitrate_kbps = 1200
            profile = default_advanced_bitrate_profile(bitrate_kbps)
        elif manual_control_type == "RESOLUTION_DRIVEN":
            try:
                width = int(float(payload.get("advanced_width")))
            except Exception:
                width = None
            try:
                height = int(float(payload.get("advanced_height")))
            except Exception:
                height = None
            bitrate_mode = payload.get("advanced_resolution_bitrate_mode")
            if bitrate_mode is None:
                bitrate_mode = "explicit" if payload.get("advanced_resolution_bitrate_kbps") not in (None, "", "None") else "auto"
            profile = default_advanced_resolution_profile(
                width,
                height,
                bitrate_kbps=payload.get("advanced_resolution_bitrate_kbps"),
                bitrate_mode=str(bitrate_mode),
            )
        else:
            try:
                size_mb = float(payload.get("advanced_target_size_mb", 25))
            except Exception:
                size_mb = 25
            profile = default_advanced_size_profile(size_mb)

        try:
            width = int(float(payload.get("advanced_width")))
            profile["advanced_width"] = max(1, width)
        except Exception:
            pass
        try:
            height = int(float(payload.get("advanced_height")))
            profile["advanced_height"] = max(1, height)
        except Exception:
            pass
        profile["advanced_resolution_lock"] = bool(payload.get("advanced_resolution_lock", True))
        profile["allow_audio_quality_reduction"] = bool(payload.get("allow_audio_quality_reduction", False))
        fps_policy = str(payload.get("advanced_fps_policy") or "keep").strip().lower()
        if fps_policy not in {"keep", "preserve", "preservar", "reduce_to_value", "reduce", "reduce_if_needed"}:
            fps_policy = "keep"
        profile["advanced_fps_policy"] = "keep" if fps_policy in {"preserve", "preservar"} else fps_policy
        try:
            fps_value = int(round(float(payload.get("advanced_fps_value")))) if payload.get("advanced_fps_value") not in (None, "") else None
        except Exception:
            fps_value = None
        profile["advanced_fps_value"] = fps_value

        audio_policy = str(payload.get("advanced_audio_policy") or "keep").strip().lower()
        if audio_policy not in {"keep", "preserve", "preservar", "reduce_to_value", "reduce", "reduce_if_needed"}:
            audio_policy = "keep"
        profile["advanced_audio_policy"] = "keep" if audio_policy in {"preserve", "preservar"} else audio_policy
        try:
            audio_kbps = int(round(float(payload.get("advanced_audio_bitrate_kbps")))) if payload.get("advanced_audio_bitrate_kbps") not in (None, "") else None
        except Exception:
            audio_kbps = None
        profile["advanced_audio_bitrate_kbps"] = audio_kbps
        profile.update(_normalize_audio_profile_fields(payload))
        profile.update(video_output_fields)
        return profile

    return default_quick_profile()





def _signature_value(value):
    if value in (None, ""):
        return None
    try:
        if isinstance(value, float):
            return round(value, 4)
        text = str(value).strip()
        if text == "":
            return None
        return text
    except Exception:
        return value


def build_profile_signature(job) -> tuple:
    """Return a compact signature for the current profile configuration.

    Output metrics are stored globally on each job. This signature prevents a
    result produced by one profile/settings combination from being presented as
    the current output of another profile/settings preview.
    """
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    source_path = _signature_value(getattr(job, "source_path", None) or getattr(job, "input_path", None))

    if profile_mode == "audio":
        return audio_profile.build_signature(job, source_path, _signature_value)
    if profile_mode == "advanced":
        return (
            "advanced",
            source_path,
            _signature_value(getattr(job, "advanced_target_size_mb", None)),
            _signature_value(getattr(job, "manual_control_type", None) or getattr(job, "advanced_target_mode", None)),
            _signature_value(getattr(job, "advanced_size_strategy", None)),
            _signature_value(getattr(job, "advanced_width", None)),
            _signature_value(getattr(job, "advanced_height", None)),
            _signature_value(getattr(job, "advanced_fps_policy", None)),
            _signature_value(getattr(job, "advanced_fps_value", None)),
            _signature_value(getattr(job, "advanced_audio_policy", None)),
            _signature_value(getattr(job, "advanced_audio_bitrate_kbps", None)),
            _signature_value(getattr(job, "allow_audio_quality_reduction", False)),
            _signature_value(getattr(job, "audio_track_policy", None)),
            _signature_value(getattr(job, "selected_track_id", None)),
            _signature_value(getattr(job, "audio_channel_policy", None)),
            _signature_value(getattr(job, "video_output_extension", None) or getattr(job, "video_output_format", None)),
        )
    if profile_mode == "smart":
        return strategic_profile.build_signature(job, source_path, _signature_value) + (
            _signature_value(getattr(job, "allow_audio_quality_reduction", False)),
            _signature_value(getattr(job, "video_output_extension", None) or getattr(job, "video_output_format", None)),
        )
    return quick_profile.build_signature(job, source_path, _signature_value) + (
        _signature_value(getattr(job, "allow_audio_quality_reduction", False)),
        _signature_value(getattr(job, "video_output_extension", None) or getattr(job, "video_output_format", None)),
    )


def profile_output_matches_current_settings(job) -> bool:
    if job is None:
        return False
    last_mode = str(getattr(job, "last_output_profile_mode", "") or "").strip().lower()
    current_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if not last_mode or last_mode != current_mode:
        return False
    last_signature = getattr(job, "last_output_profile_signature", None)
    if last_signature in (None, ""):
        return False
    if isinstance(last_signature, list):
        last_signature = tuple(last_signature)
    return tuple(last_signature) == tuple(build_profile_signature(job))


def build_profile_button_text(job) -> str:
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode == "smart":
        return strategic_profile.build_button_text(job)
    if profile_mode == "advanced":
        return "Avançado"
    if profile_mode == "audio":
        return audio_profile.build_button_text(job)
    return quick_profile.build_button_text(job)

def build_profile_tooltip(job) -> str:
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode == "audio":
        return audio_profile.build_tooltip(job)
    if profile_mode == "smart":
        return strategic_profile.build_tooltip(job)
    if profile_mode == "advanced":
        manual_type = _normalize_manual_control_type(
            getattr(job, "manual_control_type", None) or getattr(job, "advanced_target_mode", None)
        )
        if manual_type == "DIRECT_BITRATE":
            try:
                kbps = int(float(getattr(job, "advanced_target_bitrate_kbps", 1200)))
            except Exception:
                kbps = 1200
            return f"Avançado/Bitrate {kbps} kbps"
        if manual_type == "RESOLUTION_DRIVEN":
            width = _coerce_positive_int(getattr(job, "advanced_width", None))
            height = _coerce_positive_int(getattr(job, "advanced_height", None))
            if width and height:
                return f"Avançado/Resolução {width}x{height}"
            return "Avançado/Resolução original"
        try:
            size_mb = float(getattr(job, "advanced_target_size_mb", 25))
        except Exception:
            size_mb = 25
        return f"Avançado/Tamanho {_format_mb_value(size_mb)} MB"
    return quick_profile.build_tooltip(job)


def _audio_bitrate_for_output(output_path: str | None) -> int:
    ext = ""
    if output_path:
        dot = output_path.rfind(".")
        ext = output_path[dot:].lower() if dot >= 0 else ""
    return AUDIO_BITRATE_BY_EXT.get(ext, DEFAULT_AUDIO_BITRATE)


def _safe_int_value(value: Any) -> int | None:
    try:
        parsed = int(float(value)) if value not in (None, "") else None
    except Exception:
        return None
    if parsed is None or parsed <= 0:
        return None
    return parsed
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


def _audio_copy_allowed_for_output(output_ext: str, codec_name: str) -> bool:
    ext = str(output_ext or "").strip().lower()
    codec = str(codec_name or "").strip().lower()
    if not codec:
        return False
    if ext == ".webm":
        return codec in {"opus", "vorbis"}
    if ext in {".mp4", ".m4v", ".mov"}:
        return codec in {"aac", "alac", "ac3", "eac3", "mp3"}
    if ext in {".mkv", ".avi", ".ts", ".m2ts", ".mpeg", ".mpg", ".vob", ".wmv"}:
        return True
    return codec in {"aac", "mp3", "opus", "vorbis"}


def _resolve_quick_audio_plan(job, output_ext: str) -> dict[str, Any]:
    try:
        preset = int(getattr(job, "quick_profile_preset", None))
    except Exception:
        preset = default_quick_profile()["quick_profile_preset"]
    preset = max(0, min(len(QUICK_PRESET_TITLES) - 1, preset))

    source_path = str(getattr(job, "source_path", None) or "").strip()
    streams = _probe_audio_stream_details(source_path) if source_path else tuple()
    # Important: when ffprobe confirms that the source has no audio stream,
    # Quick must not reserve the default AAC/Opus budget. The output cannot
    # contain that audio stream, so subtracting 96/128 kbps from the video CBR
    # budget makes a "6.8 MB / 290 kbps" estimate encode as roughly
    # "4.0 MB / 167 kbps" on video-only files.
    if source_path and not streams:
        return {"codec": "none", "bitrate": 0, "channels": None}
    primary_stream = streams[0] if streams else {}
    source_codec = str(primary_stream.get("codec_name") or "").strip().lower()
    source_audio_bitrate = _safe_int_value(primary_stream.get("bit_rate"))
    source_channels = _safe_int_value(primary_stream.get("channels")) or int(getattr(job, "primary_audio_channels", 2) or 2)
    copy_allowed = _audio_copy_allowed_for_output(output_ext, source_codec)
    allow_audio_reduction = _allow_audio_quality_reduction(job)
    minimum_audio_bitrate = _safe_audio_floor_for_output(output_ext or getattr(job, "output_path", None), allow_reduction=allow_audio_reduction)

    if preset <= 7:
        if copy_allowed:
            return {"codec": "copy", "bitrate": None, "channels": None}
        return {
            "codec": "libopus" if output_ext == ".webm" else "aac",
            "bitrate": _audio_bitrate_for_output(getattr(job, "output_path", None)),
            "channels": None,
        }

    if preset <= 11:
        threshold = QUICK_AUDIO_CONDITIONAL_THRESHOLDS.get(preset, DEFAULT_AUDIO_BITRATE)
        if copy_allowed and source_audio_bitrate is not None and source_audio_bitrate <= threshold:
            return {"codec": "copy", "bitrate": None, "channels": None}
        return {
            "codec": "libopus" if output_ext == ".webm" else "aac",
            "bitrate": threshold,
            "channels": None,
        }

    target = QUICK_AUDIO_AGGRESSIVE_TARGETS.get(preset, 64_000)
    if not allow_audio_reduction:
        target = max(target, minimum_audio_bitrate)
    target_channels = 2 if source_channels > 2 else None
    return {
        "codec": "libopus" if output_ext == ".webm" else "aac",
        "bitrate": target,
        "channels": target_channels,
    }


def _quick_audio_estimated_bitrate(job, output_path: str | None) -> int:
    ext = ""
    if output_path:
        dot = output_path.rfind(".")
        ext = output_path[dot:].lower() if dot >= 0 else ""
    plan = _resolve_quick_audio_plan(job, ext)
    if str(plan.get("codec") or "").strip().lower() == "none":
        return 0
    if plan.get("codec") == "copy":
        streams = _probe_audio_stream_details(str(getattr(job, "source_path", None) or ""))
        if streams:
            bitrates = [int(stream.get("bit_rate")) for stream in streams if _safe_int_value(stream.get("bit_rate")) is not None]
            if bitrates:
                return max(32_000, sum(bitrates))
        return DEFAULT_AUDIO_BITRATE
    bitrate = _safe_int_value(plan.get("bitrate"))
    return bitrate if bitrate is not None else _audio_bitrate_for_output(output_path)


def _estimated_audio_bitrate_for_plan(job, output_ext: str, plan: dict[str, Any]) -> int:
    codec = str(plan.get("codec") or "").strip().lower()
    if codec == "none":
        return 0
    if codec == "copy":
        streams = _probe_audio_stream_details(str(getattr(job, "source_path", None) or ""))
        if streams:
            bitrates = [int(stream.get("bit_rate")) for stream in streams if _safe_int_value(stream.get("bit_rate")) is not None]
            if bitrates:
                return max(32_000, sum(bitrates))
        return _audio_bitrate_for_output(getattr(job, "output_path", None) or output_ext)
    bitrate = _safe_int_value(plan.get("bitrate"))
    return bitrate if bitrate is not None else _audio_bitrate_for_output(getattr(job, "output_path", None) or output_ext)


def _resolve_quick_audio_plan_for_gain(job, output_ext: str) -> dict[str, Any]:
    plan = dict(_resolve_quick_audio_plan(job, output_ext))
    total_cap = _max_total_bitrate_with_gain(job)
    if total_cap is None or total_cap <= 0:
        return plan

    preferred_audio = _estimated_audio_bitrate_for_plan(job, output_ext, plan)
    codec = str(plan.get("codec") or "").strip().lower()
    minimum_audio_bitrate = _safe_audio_floor_for_output(output_ext or getattr(job, "output_path", None), allow_reduction=_allow_audio_quality_reduction(job))
    max_audio_budget = max(minimum_audio_bitrate, total_cap - QUICK_MIN_VIDEO_BITRATE)
    max_audio_budget = max(minimum_audio_bitrate, min(max_audio_budget, max(minimum_audio_bitrate, int(total_cap * 0.35))))

    if codec == "copy" and preferred_audio > max_audio_budget:
        source_channels = int(getattr(job, "primary_audio_channels", 2) or 2)
        bitrate = max(minimum_audio_bitrate, min(preferred_audio, max_audio_budget))
        return {
            "codec": "libopus" if output_ext == ".webm" else "aac",
            "bitrate": bitrate,
            "channels": 2 if source_channels > 2 and bitrate <= 96_000 else None,
        }

    bitrate = _safe_int_value(plan.get("bitrate"))
    if bitrate is not None and bitrate > max_audio_budget:
        plan["bitrate"] = max(minimum_audio_bitrate, max_audio_budget)
        source_channels = int(getattr(job, "primary_audio_channels", 2) or 2)
        if source_channels > 2 and int(plan["bitrate"]) <= 96_000:
            plan["channels"] = 2
    return plan


def _quick_minimum_total_bitrate_floor_bps(job) -> int | None:
    """Minimum total bitrate shown/used by Quick after tiny-budget fallback.

    Rápido 13-16 can reduce resolution/FPS so strongly that the deterministic
    size contract yields an impractically tiny total bitrate.  The runtime path
    already avoids impossible x264 CBR values; this floor keeps the preview
    aligned with the safe runtime floor instead of showing an impossible number
    such as 18 kbps.
    """
    try:
        quick_preset = quick_profile.preset_index(job)
    except Exception:
        return None
    if not quick_profile.is_aggressive_preset(quick_preset):
        return None

    audio_bitrate = _quick_audio_estimated_bitrate(job, getattr(job, "output_path", None))
    if _allow_audio_quality_reduction(job):
        # The checkbox explicitly authorizes an aggressive audio budget. In this
        # mode the visible total estimate may return to the compact fallback
        # target instead of preserving a safer audio floor.
        return QUICK_MIN_FALLBACK_VIDEO_BITRATE
    return max(QUICK_MIN_FALLBACK_VIDEO_BITRATE, max(0, int(audio_bitrate)) + QUICK_MIN_FALLBACK_VIDEO_BITRATE)


def _quick_minimum_estimated_size_floor_bytes(job) -> int | None:
    duration_seconds = _resolve_job_duration_seconds(job)
    if duration_seconds is None or duration_seconds <= 0:
        return None
    bitrate_floor = _quick_minimum_total_bitrate_floor_bps(job)
    if bitrate_floor is None or bitrate_floor <= 0:
        return None
    return max(1, int(round((float(bitrate_floor) * float(duration_seconds)) / 8.0)))


def quick_minimum_total_bitrate_floor_for_profile(job) -> int | None:
    """Public preview helper for Quick's minimum runtime bitrate floor."""
    return _quick_minimum_total_bitrate_floor_bps(job)


def _quick_total_bitrate_budget_bps(job) -> int | None:
    """Return the total bitrate budget used by Quick runtime encoding.

    The visible Quick estimate is capped by the minimum-gain contract and, for
    aggressive presets, reduced again by the exact resolution/FPS transform.
    The FFmpeg command must use that same post-transform target; otherwise the
    preview can say around 4.2 MB for Quick 11 while the runtime path still
    behaves like a different budget and the final card diverges.
    """
    source_size = resolve_job_source_size_bytes(job)
    duration_seconds = _resolve_job_duration_seconds(job)
    if source_size is None or source_size <= 0 or duration_seconds is None or duration_seconds <= 0:
        bitrate = _max_total_bitrate_with_gain(job)
    else:
        max_estimated = _max_estimated_size_with_gain(job, source_size)
        target_size = _quick_aggressive_size_from_transform_contract(job, max_estimated)
        bitrate = int((target_size * 8) / duration_seconds)

    if bitrate is None or bitrate <= 0:
        return None
    floor = _quick_minimum_total_bitrate_floor_bps(job)
    if floor is not None and floor > 0:
        bitrate = max(int(bitrate), int(floor))
    return bitrate if bitrate > 0 else None

def _quick_video_bitrate_budget_bps(job, audio_bitrate: int) -> int | None:
    total_cap = _quick_total_bitrate_budget_bps(job)
    if total_cap is None or total_cap <= 0:
        return None

    # All Quick levels are estimated through the same total-bitrate contract.
    # Keep the encoder on that contract too; reserve only a small mux overhead
    # margin because x264 filler makes the video stream obey the CBR budget
    # instead of undershooting heavily on simple sources.
    overhead_budget = max(2_000, int(total_cap * 0.02))

    video_budget = total_cap - max(0, audio_bitrate) - overhead_budget

    # In very aggressive Quick levels on already small/low-bitrate sources, the
    # transformed total budget can become lower than the minimum audio budget.
    # Returning 1 bps made FFmpeg receive an impossible x264 CBR target
    # (for example Rápido 16 after resolution+FPS reduction), which can fail
    # before producing an output.  Fall back to CRF for the video stream in that
    # edge case while preserving the resolution/FPS filters and audio plan.
    if video_budget < max(8_000, int(total_cap * 0.10)):
        return None

    return video_budget


def _smart_strategy_key(job) -> str:
    return str(
        getattr(job, "smart_strategy", None)
        or getattr(job, "strategy_type", None)
        or ""
    ).strip()


def _smart_audio_quality_reduction_delta_bps(job) -> int:
    """Return the total-budget reduction unlocked by the Strategic audio checkbox.

    Strategic estimates are total bitrate/size based. Changing only the audio
    plan reallocates bits between audio and video, so the visible total estimate
    would not change unless the audio permission also reduces the total budget by
    the newly allowed audio delta.
    """
    if not _allow_audio_quality_reduction(job):
        return 0
    if _smart_strategy_key(job) != "Economia Máxima":
        return 0

    output_ref = (
        getattr(job, "video_output_extension", None)
        or getattr(job, "video_output_format", None)
        or getattr(job, "output_path", None)
    )
    protected_floor = _safe_audio_floor_for_output(output_ref, allow_reduction=False)
    aggressive_floor = _safe_audio_floor_for_output(output_ref, allow_reduction=True)
    delta = int(protected_floor) - int(aggressive_floor)
    return max(0, delta)


def _smart_total_bitrate_budget_bps(job) -> int | None:
    """Return the total bitrate budget for Strategic encodes/estimates.

    Strategic must honor the same minimum-gain contract in both estimate and
    real encode. Using a deterministic total bitrate budget avoids x264
    undershooting a CRF target on simple content.
    """
    input_bitrate = _effective_input_bitrate_bps(job)
    if input_bitrate is None or input_bitrate <= 0:
        return None

    gain_ratio = _minimum_gain_ratio_for_job(job)
    target_total = int(round(input_bitrate * max(0.0, 1.0 - gain_ratio)))
    total_cap = _max_total_bitrate_with_gain(job)
    if total_cap is not None and total_cap > 0:
        target_total = min(target_total, total_cap)

    audio_delta = _smart_audio_quality_reduction_delta_bps(job)
    if audio_delta > 0:
        # With explicit audio-quality reduction, Strategic/Economia Máxima must
        # not remain clamped to the same preserved-audio floor used while the
        # checkbox is off.  At intensity 10 the target can already be near that
        # floor, so subtracting the audio delta and then clamping to the same
        # floor makes the checkbox visually ineffective.  Use the compact video
        # fallback floor as the lower bound for the visible total budget.
        minimum_total = QUICK_MIN_FALLBACK_VIDEO_BITRATE
        target_total = max(minimum_total, target_total - audio_delta)

    return target_total if target_total > 0 else None



def _resolve_smart_audio_plan_for_gain(job, output_ext: str) -> dict[str, Any]:
    plan = _resolve_quick_audio_plan_for_gain(job, output_ext)

    strategy = _smart_strategy_key(job)
    if strategy != "Economia Máxima":
        return plan

    if not _allow_audio_quality_reduction(job):
        return plan

    codec = str(plan.get("codec") or "").strip().lower()
    if codec in {"", "none"}:
        return plan

    aggressive_floor = _safe_audio_floor_for_output(
        output_ext or getattr(job, "output_path", None),
        allow_reduction=True,
    )
    current_bitrate = _safe_int_value(plan.get("bitrate"))
    if codec == "copy" or current_bitrate is None or current_bitrate > aggressive_floor:
        source_channels = int(getattr(job, "primary_audio_channels", 2) or 2)
        return {
            "codec": "libopus" if output_ext == ".webm" else "aac",
            "bitrate": aggressive_floor,
            "channels": 2 if source_channels > 2 and aggressive_floor <= 96_000 else None,
        }

    return plan



def _smart_video_bitrate_budget_bps(job, output_ext: str) -> tuple[int | None, dict[str, Any], int]:
    audio_plan = _resolve_smart_audio_plan_for_gain(job, output_ext)
    effective_audio_bitrate = _estimated_audio_bitrate_for_plan(job, output_ext, audio_plan)
    total_budget = _smart_total_bitrate_budget_bps(job)
    if total_budget is None or total_budget <= 0:
        return None, audio_plan, effective_audio_bitrate

    overhead_budget = max(4_000, int(total_budget * 0.05))
    video_budget = total_budget - max(0, effective_audio_bitrate) - overhead_budget
    return (video_budget if video_budget > 0 else 1), audio_plan, effective_audio_bitrate


def _safe_duration_seconds(raw_duration: Any) -> float | None:
    from app.engine.encode_estimator import parse_duration_seconds

    return parse_duration_seconds(raw_duration)


def _effective_input_bitrate_bps(job) -> int | None:
    duration_seconds = _resolve_job_duration_seconds(job)
    input_size = resolve_job_source_size_bytes(job)
    if input_size is not None and input_size > 0 and duration_seconds and duration_seconds > 0:
        calculated = int((input_size * 8) / duration_seconds)
        if calculated > 0:
            return calculated

    for attr in ("input_bitrate", "source_bitrate", "bitrate", "format_bitrate"):
        input_bitrate = _coerce_positive_int(getattr(job, attr, None))
        if input_bitrate is not None and input_bitrate > 0:
            return input_bitrate
    return None


def _max_total_bitrate_with_gain(job) -> int | None:
    source_size = resolve_job_source_size_bytes(job)
    duration_seconds = _resolve_job_duration_seconds(job)
    if source_size is None or duration_seconds is None or duration_seconds <= 0:
        return None
    max_estimated_size = _max_estimated_size_with_gain(job, source_size)
    bitrate = int((max_estimated_size * 8) / duration_seconds)
    return bitrate if bitrate > 0 else None



def compute_advanced_video_bitrate_bps(job) -> int | None:
    return advanced_profile.compute_video_bitrate_bps(job)

def compute_job_crf(job) -> float:
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode == "smart":
        return strategic_profile.compute_crf(job)
    return quick_profile.compute_crf(job)

def _minimum_gain_ratio_for_job(job) -> float:
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode == "audio":
        return ADVANCED_MIN_GAIN_RATIO
    if profile_mode == "advanced":
        return ADVANCED_MIN_GAIN_RATIO
    if profile_mode == "smart":
        return strategic_profile.minimum_gain_ratio(job)
    return quick_profile.minimum_gain_ratio(job)

def _max_estimated_size_with_gain(job, source_size: int) -> int:
    gain_ratio = _minimum_gain_ratio_for_job(job)
    required_reduction = max(1, int(round(source_size * gain_ratio)), min(MIN_ESTIMATED_GAIN_BYTES, max(1, source_size - 1)))
    max_estimated = source_size - required_reduction
    return max(1, min(max_estimated, source_size - 1))



def _quick_aggressive_size_from_transform_contract(job, max_estimated: int) -> int:
    """Estimate aggressive Quick presets after their resolution/FPS transform.

    Quick 11+ changes the media itself before encoding. The transition from
    preserve-only presets to aggressive presets must be progressive; the old
    standard-height ladder created cliffs from 432p to 360p, 240p and 144p.
    The target resolver now uses smooth height ratios for Rápido 9-16, and
    this estimator applies the same spatial/FPS ratio used by the real FFmpeg
    filter chain.
    """
    try:
        quick_preset = quick_profile.preset_index(job)
    except Exception:
        return max_estimated
    if not quick_profile.is_aggressive_preset(quick_preset):
        return max_estimated

    transform_factor = 1.0
    source_resolution = _safe_source_resolution(job)
    target_height, target_fps = _resolve_aggressive_quick_targets(job, quick_preset)

    if source_resolution is not None and target_height is not None:
        source_width, source_height = source_resolution
        if source_width > 0 and source_height > 0 and 0 < target_height < source_height:
            proportional_width = source_width * (float(target_height) / float(source_height))
            target_width = max(2, int(round(proportional_width / 2.0) * 2))
            source_area = float(source_width * source_height)
            target_area = float(target_width * int(target_height))
            if source_area > 0 and target_area > 0:
                transform_factor *= max(0.05, min(1.0, target_area / source_area))

    source_fps = _safe_source_fps(job)
    if source_fps is not None and target_fps is not None and source_fps > 0 and 0 < target_fps < source_fps:
        transform_factor *= max(0.05, min(1.0, float(target_fps) / float(source_fps)))

    # If metadata was incomplete and no transform ratio could be calculated,
    # fall back to the preset curve rather than the non-aggressive contract.
    if transform_factor >= 0.999:
        transform_factor = quick_profile.aggressive_estimate_factor(quick_preset)

    adjusted = int(round(float(max_estimated) * transform_factor))
    return max(1, min(max_estimated, adjusted))


def _cap_estimated_size_to_source(job, estimated_size: int | None) -> int | None:
    estimated = _coerce_positive_int(estimated_size)
    if estimated is None:
        return None

    source_size = resolve_job_source_size_bytes(job)
    if source_size is None:
        return estimated

    max_estimated = _max_estimated_size_with_gain(job, source_size)

    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode == "quick":
        # Quick uses the deterministic minimum-gain contract. Aggressive levels
        # also change resolution/FPS; the estimate must include that transform
        # and the same tiny-budget floor used by the runtime encoder.
        quick_estimated = _quick_aggressive_size_from_transform_contract(job, max_estimated)
        floor_size = _quick_minimum_estimated_size_floor_bytes(job)
        if floor_size is not None and floor_size > 0:
            quick_estimated = max(quick_estimated, floor_size)
        return max(1, min(quick_estimated, max_estimated))

    return min(estimated, max_estimated)


def estimate_size_for_profile(job, estimate_size_crf_func) -> int | None:
    source_path = getattr(job, "source_path", None)
    if not source_path:
        return None
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode == "smart":
        return strategic_profile.estimate_size_bytes(
            job,
            safe_duration_seconds=_safe_duration_seconds,
            smart_total_bitrate_budget_bps=_smart_total_bitrate_budget_bps,
            cap_estimated_size_to_source=_cap_estimated_size_to_source,
        )
    if profile_mode == "audio":
        return audio_profile.estimate_size_bytes(job)
    if profile_mode == "advanced":
        return advanced_profile.estimate_size_bytes(job)
    return quick_profile.estimate_size_bytes(
        job,
        estimate_size_crf_func,
        cap_estimated_size_to_source=_cap_estimated_size_to_source,
    )



def _advanced_requested_target_size_bytes(job) -> int | None:
    """Return the raw Advanced target-size request without auto gain capping."""
    try:
        target_mb = float(getattr(job, "advanced_target_size_mb", None))
    except Exception:
        return None
    if target_mb <= 0:
        return None
    return max(1, int(round(target_mb * 1024 * 1024)))


def _advanced_target_bitrate_bps(job) -> int | None:
    for attr in ("advanced_target_bitrate_kbps", "advanced_resolution_bitrate_kbps"):
        value = _coerce_positive_int(getattr(job, attr, None))
        if value is not None:
            return int(value) * 1000
    return None


def _advanced_has_transform_reduction_intent(job, source_size: int | None) -> bool:
    """Detect whether Advanced settings intentionally reduce some source trait.

    This blocks the no-op case where Advanced is selected and the user clicks
    Comprimir without changing target size, bitrate, resolution, FPS, or audio.
    """
    manual_type = _normalize_manual_control_type(
        getattr(job, "manual_control_type", None) or getattr(job, "advanced_target_mode", None)
    )

    input_bitrate = _coerce_positive_int(getattr(job, "input_bitrate", None))
    target_bitrate = _advanced_target_bitrate_bps(job)
    if manual_type == "DIRECT_BITRATE" and target_bitrate is not None:
        if input_bitrate is None or target_bitrate < int(input_bitrate * 0.99):
            return True

    requested_size = _advanced_requested_target_size_bytes(job)
    if requested_size is not None and source_size is not None:
        # Use the raw requested value, not the derived preview values.  Entering
        # the Advanced profile can recalculate bitrate/resolution from the same
        # source-sized budget; that automatic recalculation must not make the
        # card actionable.  Advanced only becomes compressible after the desired
        # size is materially lower than the input size.
        required_reduction = max(1, int(round(float(source_size) * 0.01)))
        if requested_size > max(1, source_size - required_reduction):
            return False
        return True

    source_resolution = _safe_source_resolution(job)
    try:
        target_width, target_height = advanced_profile.output_resolution(job)
    except Exception:
        target_width = target_height = None
    if source_resolution and target_width and target_height:
        source_width, source_height = source_resolution
        if int(target_width) < int(source_width) or int(target_height) < int(source_height):
            return True

    source_fps = _safe_source_fps(job)
    try:
        target_fps = advanced_profile.output_fps(job)
    except Exception:
        target_fps = None
    if source_fps is not None and target_fps is not None and float(target_fps) < float(source_fps) - 0.01:
        return True

    audio_policy = str(getattr(job, "advanced_audio_policy", "keep") or "keep").strip().lower()
    if audio_policy.startswith("reduce"):
        return True

    # Resolution-driven with an explicit bitrate lower than the source is also
    # an intentional reduction even if dimensions remain unchanged.
    resolution_bitrate_mode = str(getattr(job, "advanced_resolution_bitrate_mode", "") or "").strip().lower()
    if manual_type == "RESOLUTION_DRIVEN" and resolution_bitrate_mode == "explicit" and target_bitrate is not None:
        if input_bitrate is None or target_bitrate < int(input_bitrate * 0.99):
            return True

    return False


def estimate_output_is_actionable(job, estimated_size_bytes: int | None = None, source_size_bytes: int | None = None) -> bool | None:
    """Return whether the current profile estimate should allow the action button.

    Advanced must not process a no-op configuration: unchanged parameters can
    re-encode the same media and finish equal/larger due to mux/container drift.
    The guard therefore checks both reduction intent and useful estimated gain.
    Returning None preserves existing behavior for non-Advanced profiles.
    """
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode != "advanced":
        return None

    source_size = _coerce_positive_int(source_size_bytes)
    if source_size is None:
        source_size = resolve_job_source_size_bytes(job)

    if not _advanced_has_transform_reduction_intent(job, source_size):
        return False

    estimated = _coerce_positive_int(estimated_size_bytes)
    if estimated is None:
        estimated = _coerce_positive_int(getattr(job, "estimated_size_bytes", None))
    if estimated is None:
        try:
            estimated = estimate_size_for_profile(job, lambda *_args, **_kwargs: None)
        except Exception:
            estimated = None
    estimated = _coerce_positive_int(estimated)
    if estimated is None or source_size is None:
        return None

    # Require a small but real reduction. This avoids enabling a compression
    # action for equal/larger outputs and for visually identical rounded sizes.
    minimum_gain = max(1, int(round(float(source_size) * 0.01)))
    return estimated <= max(1, source_size - minimum_gain)


# Backward-compatible name used by UI and controllers.
estimated_output_is_actionable = estimate_output_is_actionable

def estimate_output_bitrate_for_profile(job, estimated_size_bytes: int | None) -> int | None:
    if estimated_size_bytes is not None:
        bitrate = _estimate_bitrate_from_size_for_job(job, estimated_size_bytes)
        if bitrate is not None:
            return bitrate
    profile_mode = str(getattr(job, "profile_mode", "") or "").lower()
    if profile_mode == "audio":
        return audio_profile.estimate_output_bitrate_bps(job)
    if profile_mode == "advanced":
        return advanced_profile.estimate_total_bitrate_bps(job)
    if profile_mode == "smart":
        return strategic_profile.estimate_output_bitrate_bps(
            job,
            smart_total_bitrate_budget_bps=_smart_total_bitrate_budget_bps,
        )
    if profile_mode == "quick":
        return quick_profile.estimate_output_bitrate_bps(
            job,
            quick_audio_estimated_bitrate=_quick_audio_estimated_bitrate,
            default_audio_bitrate=DEFAULT_AUDIO_BITRATE,
        )
    return None



def _estimate_bitrate_from_size_for_job(job, size_bytes: int | float | None) -> int | None:
    duration_seconds = _resolve_job_duration_seconds(job)
    if duration_seconds is None or duration_seconds <= 0:
        return None
    size_value = _coerce_positive_int(size_bytes)
    if size_value is None:
        return None
    bitrate = int((size_value * 8) / duration_seconds)
    return bitrate if bitrate > 0 else None


def _safe_source_resolution(job) -> tuple[int, int] | None:
    return parse_resolution(getattr(job, "resolution", None))


def _format_estimated_fps(value: float | None, fallback: str | None) -> str | None:
    if value is None or value <= 0:
        return fallback
    rounded = round(value, 3)
    if abs(rounded - round(rounded)) < 0.001:
        return str(int(round(rounded)))
    text = f"{rounded:.3f}".rstrip("0").rstrip(".")
    return text or fallback


def _format_estimated_resolution(width: int | None, height: int | None, fallback: str | None) -> str | None:
    if width is None or height is None or width <= 0 or height <= 0:
        return fallback
    return f"{width}x{height}"


def estimate_output_video_traits(job) -> tuple[str | None, str | None]:
    input_resolution = getattr(job, "resolution", None)
    input_fps = getattr(job, "fps", None)
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode == "audio":
        return audio_profile.output_video_traits(job)
    if profile_mode == "advanced":
        output_resolution = str(input_resolution) if input_resolution not in (None, "") else None
        width, height = advanced_profile.output_resolution(job)
        if width is not None and height is not None:
            output_resolution = _format_estimated_resolution(width, height, output_resolution)
        output_fps_value = advanced_profile.output_fps(job)
        output_fps = _format_estimated_fps(
            output_fps_value,
            str(input_fps) if input_fps not in (None, "") else None,
        )
        return (output_resolution, output_fps)
    if profile_mode == "smart":
        return strategic_profile.output_video_traits(job)
    return quick_profile.output_video_traits(
        job,
        safe_source_resolution=_safe_source_resolution,
        safe_source_fps=_safe_source_fps,
        resolve_aggressive_targets=_resolve_aggressive_quick_targets,
        format_estimated_resolution=_format_estimated_resolution,
        format_estimated_fps=_format_estimated_fps,
    )

def _quick_preserve_quality_cbr_fill_enabled(quick_preset: int | None) -> bool:
    try:
        preset = int(quick_preset) if quick_preset is not None else -1
    except Exception:
        preset = -1
    return 0 <= preset <= 5

def _build_audio_stream_options(job) -> list[str]:
    policy = str(getattr(job, "audio_track_policy", "KEEP_ALL") or "KEEP_ALL").strip().upper()
    selected_track_id = getattr(job, "selected_track_id", None)
    channel_policy = str(getattr(job, "audio_channel_policy", "KEEP_ORIGINAL") or "KEEP_ORIGINAL").strip().upper()

    options: list[str] = []
    if policy == "KEEP_DEFAULT_ONLY":
        options += ["-map", "0:v?", "-map", "0:a:0?"]
    elif policy == "SELECTED_ONLY":
        try:
            track_index = int(selected_track_id)
        except Exception:
            track_index = None
        if track_index is not None and track_index >= 0:
            options += ["-map", "0:v?", "-map", f"0:a:{track_index}?"]

    if channel_policy == "DOWNMIX_TO_MONO":
        options += ["-ac", "1"]
    elif channel_policy == "DOWNMIX_TO_STEREO":
        options += ["-ac", "2"]
    return options



def build_ffmpeg_profile_options(job, output_ext: str) -> list[str]:
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    audio_stream_options = _build_audio_stream_options(job)
    if profile_mode == "audio":
        return audio_profile.build_ffmpeg_options(job, output_ext)
    if profile_mode == "smart":
        return strategic_profile.build_ffmpeg_options(
            job,
            output_ext,
            audio_stream_options,
            resolve_smart_audio_plan_for_gain=_resolve_smart_audio_plan_for_gain,
            safe_int_value=_safe_int_value,
            smart_video_bitrate_budget_bps=_smart_video_bitrate_budget_bps,
        )
    if profile_mode == "advanced":
        return advanced_profile.build_ffmpeg_options(job, output_ext, audio_stream_options)
    return quick_profile.build_ffmpeg_options(
        job,
        output_ext,
        audio_stream_options,
        build_aggressive_video_filters=_build_aggressive_video_filters,
        resolve_quick_audio_plan_for_gain=_resolve_quick_audio_plan_for_gain,
        safe_int_value=_safe_int_value,
        estimated_audio_bitrate_for_plan=_estimated_audio_bitrate_for_plan,
        quick_video_bitrate_budget_bps=_quick_video_bitrate_budget_bps,
    )
