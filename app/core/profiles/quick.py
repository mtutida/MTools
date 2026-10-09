from __future__ import annotations

from typing import Any, Callable

from app.ancillary.configuration import ConfigurationService
from app.engine.encode_estimator import estimate_bitrate_from_size, estimate_size_crf, quick_preset_to_crf

QUICK_PRESET_TITLES = (
    "Compressão Muito Baixa", "Compressão Baixa", "Compressão Baixa+", "Compressão Moderada",
    "Compressão Moderada+", "Compressão Equilibrada", "Compressão Alta", "Compressão Alta+",
    "Compressão Muito Alta", "Compressão Máxima", "Compressão Extrema 1", "Compressão Extrema 2",
    "Compressão Extrema 3", "Compressão Extrema 4", "Compressão Extrema 5", "Compressão Extrema 6",
)
QUICK_SCALE_START_INDEX = 8
QUICK_FPS_START_INDEX = 12
QUICK_AGGRESSIVE_START_INDEX = QUICK_SCALE_START_INDEX
# Quick now uses four visual blocks of four levels each:
# 1-4 preserve quality, 5-8 reduce bitrate, 9-12 reduce resolution,
# and 13-16 reduce resolution plus FPS. The aggressive estimate factors
# start when the profile is allowed to reduce resolution.
QUICK_CONTRACT_ESTIMATE_START_INDEX = 4
QUICK_AGGRESSIVE_ESTIMATE_FACTORS = {
    8: 0.90, 9: 0.82, 10: 0.74, 11: 0.66,
    12: 0.56, 13: 0.48, 14: 0.40, 15: 0.34,
}
# Deterministic Quick size contract per preset. The curve stays monotonic
# across the four blocks and becomes progressively stronger only after the
# user enters the resolution/FPS blocks.
QUICK_MIN_GAIN_RATIOS = {
    0: 0.080, 1: 0.100, 2: 0.120, 3: 0.140,
    4: 0.160, 5: 0.180, 6: 0.205, 7: 0.230,
    8: 0.260, 9: 0.300, 10: 0.340, 11: 0.380,
    12: 0.430, 13: 0.480, 14: 0.540, 15: 0.600,
}


def preset_title(index: int | None) -> str:
    try:
        index = int(index) if index is not None else 4
    except Exception:
        index = 4
    index = max(0, min(len(QUICK_PRESET_TITLES) - 1, index))
    return QUICK_PRESET_TITLES[index]


def is_aggressive_preset(index: int | None) -> bool:
    try:
        value = int(index) if index is not None else -1
    except Exception:
        value = -1
    return value >= QUICK_AGGRESSIVE_START_INDEX


def aggressive_estimate_factor(index: int | None) -> float:
    if not is_aggressive_preset(index):
        return 1.0
    try:
        return float(QUICK_AGGRESSIVE_ESTIMATE_FACTORS.get(int(index), 1.0))
    except Exception:
        return 1.0


def default_profile() -> dict[str, Any]:
    try:
        cfg = ConfigurationService.instance().get()
        quick_preset = int(getattr(cfg, "quick_profile_preset", 4))
    except Exception:
        quick_preset = 4
    quick_preset = max(0, min(len(QUICK_PRESET_TITLES) - 1, quick_preset))
    return {"profile_mode": "quick", "profile_label": f"Rápido/{preset_title(quick_preset)}", "quick_profile_preset": quick_preset, "allow_audio_quality_reduction": False}


def preset_index(job) -> int:
    try:
        quick_preset = int(getattr(job, "quick_profile_preset", None))
    except Exception:
        quick_preset = None
    if quick_preset is None:
        quick_preset = default_profile()["quick_profile_preset"]
    return max(0, min(len(QUICK_PRESET_TITLES) - 1, quick_preset))


def build_signature(job, source_path_value: Any, signature_value: Callable[[Any], Any]) -> tuple:
    return ("quick", source_path_value, signature_value(getattr(job, "quick_profile_preset", None)))


def build_button_text(job) -> str:
    return f"Rápido {preset_index(job) + 1}"


def build_tooltip(job) -> str:
    quick_preset = preset_index(job)
    suffix = " • reduz resolução/FPS" if quick_preset >= QUICK_FPS_START_INDEX else (" • reduz resolução" if is_aggressive_preset(quick_preset) else "")
    return f"Rápido/{preset_title(quick_preset)}{suffix}"


def compute_crf(job) -> float:
    return quick_preset_to_crf(preset_index(job))


def minimum_gain_ratio(job) -> float:
    return QUICK_MIN_GAIN_RATIOS.get(preset_index(job), QUICK_MIN_GAIN_RATIOS[4])


def estimate_size_bytes(job, estimate_size_crf_func, *, cap_estimated_size_to_source: Callable[[Any, int | None], int | None]) -> int | None:
    source_path = getattr(job, "source_path", None)
    if not source_path:
        return None
    estimated = estimate_size_crf_func(source_path, crf=compute_crf(job))
    quick_preset = preset_index(job)
    if estimated is not None and is_aggressive_preset(quick_preset):
        estimated = max(1, int(estimated * aggressive_estimate_factor(quick_preset)))
    return cap_estimated_size_to_source(job, estimated)


def estimate_output_bitrate_bps(job, *, quick_audio_estimated_bitrate: Callable[[Any, str | None], int], default_audio_bitrate: int) -> int | None:
    source_path = getattr(job, "source_path", None)
    if not source_path:
        return None
    try:
        crf_estimate = estimate_size_crf(source_path, crf=int(round(compute_crf(job))))
    except Exception:
        crf_estimate = None
    if not crf_estimate:
        return None
    quick_preset = preset_index(job)
    if is_aggressive_preset(quick_preset):
        crf_estimate = max(1, int(crf_estimate * aggressive_estimate_factor(quick_preset)))
    duration_bitrate = estimate_bitrate_from_size(crf_estimate, getattr(job, "duration", None))
    if duration_bitrate is None:
        return None
    audio_bitrate = quick_audio_estimated_bitrate(job, getattr(job, "output_path", None))
    return max(audio_bitrate, duration_bitrate - max(0, default_audio_bitrate - audio_bitrate))


def output_video_traits(job, *, safe_source_resolution, safe_source_fps, resolve_aggressive_targets, format_estimated_resolution, format_estimated_fps) -> tuple[str | None, str | None]:
    input_resolution = getattr(job, "resolution", None)
    input_fps = getattr(job, "fps", None)
    output_resolution = str(input_resolution) if input_resolution not in (None, "") else None
    output_fps = str(input_fps) if input_fps not in (None, "") else None
    quick_preset = preset_index(job)
    source_resolution = safe_source_resolution(job)
    source_fps = safe_source_fps(job)
    target_height, target_fps = resolve_aggressive_targets(job, quick_preset)
    if source_resolution is not None and target_height is not None:
        width, height = source_resolution
        if 0 < target_height < height:
            proportional_width = width * (target_height / height)
            est_width = max(2, int(round(proportional_width / 2.0) * 2))
            output_resolution = format_estimated_resolution(est_width, int(target_height), output_resolution)
        else:
            output_resolution = format_estimated_resolution(width, height, output_resolution)
    if source_fps is not None and target_fps is not None and 0 < target_fps < source_fps:
        output_fps = format_estimated_fps(target_fps, output_fps)
    return output_resolution, output_fps


def build_ffmpeg_options(job, output_ext: str, audio_stream_options: list[str], *, build_aggressive_video_filters, resolve_quick_audio_plan_for_gain, safe_int_value, estimated_audio_bitrate_for_plan, quick_video_bitrate_budget_bps) -> list[str]:
    crf = compute_crf(job)
    quick_preset = preset_index(job)
    filter_args: list[str] = []
    aggressive_filters = build_aggressive_video_filters(job, quick_preset)
    if aggressive_filters:
        filter_args = ["-vf", ",".join(aggressive_filters)]
    quick_audio_plan = resolve_quick_audio_plan_for_gain(job, output_ext)
    quick_audio_codec = str(quick_audio_plan.get("codec") or ("libopus" if output_ext == ".webm" else "aac")).strip().lower()
    quick_audio_options: list[str] = []
    if quick_audio_codec != "none":
        quick_audio_options = ["-c:a", quick_audio_codec]
        quick_audio_bitrate = safe_int_value(quick_audio_plan.get("bitrate"))
        if quick_audio_bitrate is not None and quick_audio_codec != "copy":
            quick_audio_options += ["-b:a", str(quick_audio_bitrate)]
        quick_audio_channels = safe_int_value(quick_audio_plan.get("channels"))
        if quick_audio_channels is not None and quick_audio_codec != "copy":
            quick_audio_options += ["-ac", str(quick_audio_channels)]
    effective_quick_audio_bitrate = estimated_audio_bitrate_for_plan(job, output_ext, quick_audio_plan)
    quick_video_budget = quick_video_bitrate_budget_bps(job, effective_quick_audio_bitrate)
    if output_ext == ".webm":
        video_args = ["-c:v", "libvpx-vp9", "-crf", str(max(20, min(42, crf + 4)))]
        if quick_video_budget is not None and quick_video_budget > 0:
            video_args += ["-b:v", str(quick_video_budget), "-minrate", str(quick_video_budget), "-maxrate", str(quick_video_budget)]
        else:
            video_args += ["-b:v", "0"]
        return audio_stream_options + filter_args + video_args + quick_audio_options
    video_args = ["-c:v", "libx264"]
    if quick_video_budget is not None and quick_video_budget > 0:
        video_args += ["-b:v", str(quick_video_budget), "-minrate", str(quick_video_budget), "-maxrate", str(quick_video_budget), "-bufsize", str(quick_video_budget * 2)]
        if 0 <= quick_preset <= len(QUICK_PRESET_TITLES) - 1:
            video_args += ["-x264-params", "nal-hrd=cbr:filler=1"]
    else:
        video_args += ["-crf", str(crf)]
    return audio_stream_options + filter_args + video_args + quick_audio_options + ["-movflags", "+faststart"]
