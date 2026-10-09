from __future__ import annotations

from typing import Any, Callable

from app.core.formatting import parse_resolution

SMART_STRATEGIES = ("Economia Máxima", "Equilíbrio", "Qualidade Prioritária")
SMART_STRATEGY_DISPLAY_LABELS = {
    "Economia Máxima": "Reduzir escala",
    "Equilíbrio": "Otimizar arquivo",
    "Qualidade Prioritária": "Preservar qualidade",
}

# Strategic uses a continuous 0-10 intensity scale for each strategy.
# The previous curve was too shallow: the lightest Strategic setting could
# reduce only 3% and the strongest Economy setting topped out near 24%.
# These ranges make the endpoints useful while preserving the intent of each
# strategy: Quality remains conservative, Balanced becomes a real middle path,
# and Economy reaches a clearly smaller output.
SMART_STRATEGY_GAIN_RANGES = {
    "Qualidade Prioritária": (0.100, 0.250),
    "Equilíbrio": (0.180, 0.400),
    "Economia Máxima": (0.280, 0.600),
}
SMART_STRATEGY_BASE_GAIN = {strategy: limits[0] for strategy, limits in SMART_STRATEGY_GAIN_RANGES.items()}
SMART_STRATEGY_CRF_RANGES = {
    "Qualidade Prioritária": (24.0, 30.0),
    "Equilíbrio": (27.0, 35.0),
    "Economia Máxima": (30.0, 42.0),
}


def default_profile() -> dict[str, Any]:
    return {
        "profile_mode": "smart", "profile_label": "Estratégico/Otimizar arquivo", "control_mode": "STRATEGY",
        "strategy_type": "Equilíbrio", "slider_value": 5.0, "smart_strategy": "Equilíbrio", "smart_intensity": 5,
        "audio_track_policy": "KEEP_ALL", "selected_track_id": None, "audio_channel_policy": "KEEP_ORIGINAL",
        "allow_audio_quality_reduction": False,
    }


def normalize_payload(payload: dict[str, Any], normalize_audio_profile_fields: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    strategy = str(payload.get("strategy_type") or payload.get("smart_strategy") or "Equilíbrio").strip()
    if strategy not in SMART_STRATEGIES:
        strategy = "Equilíbrio"
    try:
        slider_value = float(payload.get("slider_value", payload.get("smart_intensity", 5)))
    except Exception:
        slider_value = 5.0
    slider_value = max(0.0, min(10.0, slider_value))
    intensity = max(0, min(10, int(round(slider_value))))
    profile = default_profile()
    profile.update({"control_mode": "STRATEGY", "strategy_type": strategy, "slider_value": slider_value, "smart_strategy": strategy, "smart_intensity": intensity})
    profile.update(normalize_audio_profile_fields(payload))
    return profile


def build_signature(job, source_path_value: Any, signature_value: Callable[[Any], Any]) -> tuple:
    return ("smart", source_path_value, signature_value(getattr(job, "smart_strategy", None) or getattr(job, "strategy_type", None)), signature_value(getattr(job, "smart_intensity", None) or getattr(job, "slider_value", None)), signature_value(getattr(job, "audio_track_policy", None)), signature_value(getattr(job, "selected_track_id", None)), signature_value(getattr(job, "audio_channel_policy", None)))


def build_button_text(job) -> str:
    return "Estratégico"


def build_tooltip(job) -> str:
    strategy = str(getattr(job, "smart_strategy", "Equilíbrio") or "Equilíbrio").strip()
    try:
        intensity = int(getattr(job, "smart_intensity", 5))
    except Exception:
        intensity = 5
    intensity = max(0, min(10, intensity))
    display_strategy = SMART_STRATEGY_DISPLAY_LABELS.get(strategy, strategy)
    return f"Estratégico/{display_strategy} • intensidade {intensity}"


def compute_crf(job) -> float:
    strategy = str(getattr(job, "smart_strategy", "Equilíbrio") or "Equilíbrio").strip()
    try:
        intensity = float(getattr(job, "slider_value", getattr(job, "smart_intensity", 5)))
    except Exception:
        intensity = 5.0
    intensity = max(0.0, min(10.0, intensity))
    min_crf, max_crf = SMART_STRATEGY_CRF_RANGES.get(strategy, SMART_STRATEGY_CRF_RANGES["Equilíbrio"])
    ratio = intensity / 10.0
    return max(18.0, min(42.0, min_crf + (max_crf - min_crf) * ratio))


def minimum_gain_ratio(job) -> float:
    strategy = str(getattr(job, "smart_strategy", "Equilíbrio") or "Equilíbrio").strip()
    try:
        intensity = float(getattr(job, "slider_value", getattr(job, "smart_intensity", 5)))
    except Exception:
        intensity = 5.0
    intensity = max(0.0, min(10.0, intensity))
    min_ratio, max_ratio = SMART_STRATEGY_GAIN_RANGES.get(strategy, SMART_STRATEGY_GAIN_RANGES["Equilíbrio"])
    ratio = intensity / 10.0
    return min(0.70, max(0.0, min_ratio + (max_ratio - min_ratio) * ratio))


def estimate_size_bytes(job, *, safe_duration_seconds, smart_total_bitrate_budget_bps, cap_estimated_size_to_source) -> int | None:
    duration_seconds = safe_duration_seconds(getattr(job, "duration", None))
    total_bitrate = smart_total_bitrate_budget_bps(job)
    if duration_seconds is None or duration_seconds <= 0 or total_bitrate is None or total_bitrate <= 0:
        return None
    estimated = int((total_bitrate * duration_seconds) / 8)
    if estimated <= 0:
        return None
    return cap_estimated_size_to_source(job, estimated)


def estimate_output_bitrate_bps(job, *, smart_total_bitrate_budget_bps) -> int | None:
    return smart_total_bitrate_budget_bps(job)


def _source_resolution(job) -> tuple[int, int] | None:
    return parse_resolution(getattr(job, "resolution", None))


def _strategy_key(job) -> str:
    return str(getattr(job, "smart_strategy", None) or getattr(job, "strategy_type", None) or "Equilíbrio").strip()


def _intensity_value(job) -> int:
    try:
        value = float(getattr(job, "slider_value", getattr(job, "smart_intensity", 5)))
    except Exception:
        value = 5.0
    return max(0, min(10, int(round(value))))


def _target_height_for_economy(job) -> int | None:
    """Return the Strategic/Reduzir escala target height.

    This card is the explicit authorization to reduce frame dimensions. Its
    goal is to avoid making small files only by starving bitrate, because too
    few bits per pixel tends to create grain, blocks and blur. The policy scales
    progressively with intensity, preserves aspect ratio through ffmpeg, and
    never goes below a safe 240p floor for horizontal content. Conservative and
    balanced strategies keep the validated preserve-resolution behavior.
    """
    if _strategy_key(job) != "Economia Máxima":
        return None
    intensity = _intensity_value(job)
    if intensity <= 0:
        return None
    resolution = _source_resolution(job)
    if resolution is None:
        return None
    _width, source_height = resolution
    if source_height <= 240:
        return None

    # Intensity bands: light scale reduction starts only for larger sources;
    # medium/high intensity allows one-step downscale for already-small videos.
    if intensity <= 3:
        candidates = [2160, 1440, 1080, 720]
    elif intensity <= 7:
        candidates = [1440, 1080, 720, 480, 360, 240]
    else:
        candidates = [1080, 720, 480, 360, 240]

    target_height: int | None = None
    for candidate in candidates:
        if source_height > candidate:
            target_height = candidate
            break

    if target_height is None or target_height >= source_height:
        return None
    target_height = max(240, int(target_height))
    if target_height >= source_height:
        return None
    return max(2, int(round(float(target_height) / 2.0) * 2))


def _target_resolution_for_economy(job) -> tuple[int, int] | None:
    source = _source_resolution(job)
    target_height = _target_height_for_economy(job)
    if source is None or target_height is None:
        return None
    source_width, source_height = source
    if target_height <= 0 or target_height >= source_height:
        return None
    proportional_width = source_width * (float(target_height) / float(source_height))
    target_width = max(2, int(round(proportional_width / 2.0) * 2))
    return target_width, int(target_height)


def output_video_traits(job) -> tuple[str | None, str | None]:
    input_resolution = getattr(job, "resolution", None)
    input_fps = getattr(job, "fps", None)
    output_resolution = str(input_resolution) if input_resolution not in (None, "") else None
    target_resolution = _target_resolution_for_economy(job)
    if target_resolution is not None:
        output_resolution = f"{target_resolution[0]}x{target_resolution[1]}"
    return (output_resolution, str(input_fps) if input_fps not in (None, "") else None)


def build_ffmpeg_options(job, output_ext: str, audio_stream_options: list[str], *, resolve_smart_audio_plan_for_gain, safe_int_value, smart_video_bitrate_budget_bps) -> list[str]:
    smart_audio_plan = resolve_smart_audio_plan_for_gain(job, output_ext)
    smart_audio_options: list[str] = ["-c:a", str(smart_audio_plan.get("codec") or ("libopus" if output_ext == ".webm" else "aac"))]
    smart_audio_bitrate = safe_int_value(smart_audio_plan.get("bitrate"))
    if smart_audio_bitrate is not None and str(smart_audio_plan.get("codec")) != "copy":
        smart_audio_options += ["-b:a", str(smart_audio_bitrate)]
    smart_audio_channels = safe_int_value(smart_audio_plan.get("channels"))
    if smart_audio_channels is not None and str(smart_audio_plan.get("codec")) != "copy":
        smart_audio_options += ["-ac", str(smart_audio_channels)]
    smart_video_budget, _resolved_audio_plan, _effective_audio_bitrate = smart_video_bitrate_budget_bps(job, output_ext)
    filter_args: list[str] = []
    target_height = _target_height_for_economy(job)
    if target_height is not None:
        filter_args = ["-vf", f"scale=-2:{int(target_height)}"]
    if output_ext == ".webm":
        video_args = ["-c:v", "libvpx-vp9"]
        if smart_video_budget is not None and smart_video_budget > 0:
            video_args += ["-b:v", str(smart_video_budget), "-minrate", str(smart_video_budget), "-maxrate", str(smart_video_budget)]
        else:
            video_args += ["-crf", str(max(20, min(42, compute_crf(job) + 4))), "-b:v", "0"]
        return audio_stream_options + filter_args + video_args + smart_audio_options
    video_args = ["-c:v", "libx264"]
    if smart_video_budget is not None and smart_video_budget > 0:
        video_args += ["-b:v", str(smart_video_budget), "-minrate", str(smart_video_budget), "-maxrate", str(smart_video_budget), "-bufsize", str(smart_video_budget * 2), "-x264-params", "nal-hrd=cbr:filler=1"]
    else:
        video_args += ["-crf", str(compute_crf(job))]
    return audio_stream_options + filter_args + video_args + smart_audio_options + ["-movflags", "+faststart"]
