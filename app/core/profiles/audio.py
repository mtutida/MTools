from __future__ import annotations

from typing import Any, Callable

from app.core.profiles import advanced as advanced_profile
from app.core.profiles.common import safe_duration_seconds

AUDIO_FORMAT_OPTIONS = ("mp3", "m4a", "aac", "opus", "ogg", "wma", "flac", "wav")
AUDIO_BITRATE_OPTIONS = (32, 48, 64, 96, 128, 160, 192)
AUDIO_SAMPLE_RATE_OPTIONS = (44100, 48000)
AUDIO_DEFAULT_OUTPUT_FORMAT = "mp3"
_AUDIO_FORMAT_METADATA = {
    "mp3": {
        "label": "MP3 (.mp3)",
        "extension": ".mp3",
        "codec": "libmp3lame",
        "default_bitrate_kbps": 128,
        "uses_target_bitrate": True,
    },
    "m4a": {
        "label": "M4A (.m4a)",
        "extension": ".m4a",
        "codec": "aac",
        "default_bitrate_kbps": 128,
        "uses_target_bitrate": True,
    },
    "aac": {
        "label": "AAC (.aac)",
        "extension": ".aac",
        "codec": "aac",
        "default_bitrate_kbps": 128,
        "uses_target_bitrate": True,
    },
    "opus": {
        "label": "Opus (.opus)",
        "extension": ".opus",
        "codec": "libopus",
        "default_bitrate_kbps": 96,
        "uses_target_bitrate": True,
    },
    "ogg": {
        "label": "OGG (.ogg)",
        "extension": ".ogg",
        "codec": "libvorbis",
        "default_bitrate_kbps": 128,
        "uses_target_bitrate": True,
    },
    "wma": {
        "label": "WMA (.wma)",
        "extension": ".wma",
        "codec": "wmav2",
        "default_bitrate_kbps": 128,
        "uses_target_bitrate": True,
    },
    "flac": {
        "label": "FLAC (.flac)",
        "extension": ".flac",
        "codec": "flac",
        "default_bitrate_kbps": 128,
        "uses_target_bitrate": False,
    },
    "wav": {
        "label": "WAV (.wav)",
        "extension": ".wav",
        "codec": "pcm_s16le",
        "default_bitrate_kbps": 128,
        "uses_target_bitrate": False,
    },
}
_AUDIO_EXT_BY_FORMAT = {key: value["extension"] for key, value in _AUDIO_FORMAT_METADATA.items()}
_AUDIO_CODEC_BY_FORMAT = {key: value["codec"] for key, value in _AUDIO_FORMAT_METADATA.items()}
_AUDIO_DEFAULT_BITRATE_KBPS = {key: int(value["default_bitrate_kbps"]) for key, value in _AUDIO_FORMAT_METADATA.items()}

LOSSY_SOURCE_EXTENSIONS = {".mp3", ".aac", ".m4a", ".ogg", ".opus", ".wma"}
LOSSLESS_OUTPUT_FORMATS = {"flac", "wav"}


def normalize_output_format(value: Any) -> str:
    normalized = str(value or AUDIO_DEFAULT_OUTPUT_FORMAT).strip().lower()
    return normalized if normalized in AUDIO_FORMAT_OPTIONS else AUDIO_DEFAULT_OUTPUT_FORMAT


def normalize_bitrate_kbps(value: Any, *, output_format: str | None = None) -> int:
    try:
        parsed = int(round(float(value))) if value not in (None, "") else None
    except Exception:
        parsed = None
    if parsed is None or parsed <= 0:
        parsed = _AUDIO_DEFAULT_BITRATE_KBPS.get(normalize_output_format(output_format), 128)
    # Do not snap to AUDIO_BITRATE_OPTIONS. The Audio profile must be able to
    # preserve probed source-track values such as 94 kbps exactly; snapping made
    # the UI/encode fall back to 64 or 96 even when "Trilhas de áudio" showed a
    # different detected bitrate.
    return max(1, int(parsed))


def display_label_for_format(output_format: str | None) -> str:
    normalized = normalize_output_format(output_format)
    return str(_AUDIO_FORMAT_METADATA.get(normalized, {}).get("label") or "MP3 (.mp3)")


def extension_for_format(output_format: str | None) -> str:
    return _AUDIO_EXT_BY_FORMAT.get(normalize_output_format(output_format), ".mp3")


def codec_for_format(output_format: str | None) -> str:
    return _AUDIO_CODEC_BY_FORMAT.get(normalize_output_format(output_format), "libmp3lame")


def uses_target_bitrate(output_format: str | None) -> bool:
    normalized = normalize_output_format(output_format)
    return bool(_AUDIO_FORMAT_METADATA.get(normalized, {}).get("uses_target_bitrate", True))


def is_lossless_output_format(output_format: str | None) -> bool:
    return normalize_output_format(output_format) in LOSSLESS_OUTPUT_FORMATS


def is_lossy_source_path(source_path: str | None) -> bool:
    text = str(source_path or "").strip().lower()
    return any(text.endswith(ext) for ext in LOSSY_SOURCE_EXTENSIONS)


def lossy_to_lossless_warning(source_path: str | None, output_format: str | None) -> str:
    if not is_lossy_source_path(source_path) or not is_lossless_output_format(output_format):
        return ""
    label = display_label_for_format(output_format).split(" ", 1)[0]
    return (
        f"Aviso: converter uma fonte lossy para {label} pode aumentar bastante o tamanho "
        "e não recupera a qualidade já perdida."
    )


def default_profile() -> dict[str, Any]:
    output_format = AUDIO_DEFAULT_OUTPUT_FORMAT
    bitrate_kbps = normalize_bitrate_kbps(None, output_format=output_format)
    return {
        "profile_mode": "audio",
        "profile_label": f"Áudio/{display_label_for_format(output_format)} {bitrate_kbps} kbps",
        "control_mode": "audio",
        "audio_output_format": output_format,
        "audio_output_extension": extension_for_format(output_format),
        "audio_bitrate_kbps": bitrate_kbps,
        "audio_track_policy": "KEEP_DEFAULT_ONLY",
        "selected_track_id": None,
        "selected_track_ids": None,
        "audio_channel_policy": "KEEP_ORIGINAL",
        "audio_sample_rate": "ORIGINAL",
        "audio_quality_mode": "BALANCED",
        "audio_volume_normalization": "OFF",
        "audio_metadata_policy": "PRESERVE",
        "extract_subtitle": False,
    }


def normalize_payload(payload: dict[str, Any], normalize_audio_profile_fields: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    profile = default_profile()
    output_format = normalize_output_format(payload.get("audio_output_format"))
    bitrate_kbps = normalize_bitrate_kbps(payload.get("audio_bitrate_kbps"), output_format=output_format)
    profile.update({
        "profile_mode": "audio",
        "profile_label": f"Áudio/{display_label_for_format(output_format)} {bitrate_kbps} kbps",
        "control_mode": "audio",
        "audio_output_format": output_format,
        "audio_output_extension": extension_for_format(output_format),
        "audio_bitrate_kbps": bitrate_kbps,
    })
    profile.update(normalize_audio_profile_fields(payload))
    return profile


def build_signature(job, source_path_value: Any, signature_value: Callable[[Any], Any]) -> tuple:
    return (
        "audio",
        source_path_value,
        signature_value(getattr(job, "audio_output_format", None)),
        signature_value(getattr(job, "audio_bitrate_kbps", None)),
        signature_value(getattr(job, "audio_track_policy", None)),
        signature_value(getattr(job, "selected_track_id", None)),
        signature_value(getattr(job, "selected_track_ids", None)),
        signature_value(getattr(job, "audio_channel_policy", None)),
        signature_value(getattr(job, "audio_sample_rate", None)),
        signature_value(getattr(job, "audio_quality_mode", None)),
        signature_value(getattr(job, "audio_volume_normalization", None)),
        signature_value(getattr(job, "audio_metadata_policy", None)),
        signature_value(getattr(job, "extract_subtitle", None)),
    )


def build_button_text(job) -> str:
    return "Áudio"


def build_tooltip(job) -> str:
    output_format = normalize_output_format(getattr(job, "audio_output_format", None))
    bitrate = normalize_bitrate_kbps(getattr(job, "audio_bitrate_kbps", None), output_format=output_format)
    return f"Áudio/{display_label_for_format(output_format)} {bitrate} kbps"


def selected_output_audio_stream_count(job) -> int:
    source_path = str(getattr(job, "source_path", None) or "").strip()
    streams = advanced_profile._probe_audio_stream_details(source_path) if source_path else tuple()
    if not streams:
        return 0
    policy = str(getattr(job, "audio_track_policy", "KEEP_DEFAULT_ONLY") or "KEEP_DEFAULT_ONLY").strip().upper()
    if policy == "KEEP_ALL":
        return max(1, len(streams))
    if policy == "SELECTED_ONLY":
        raw_ids = getattr(job, "selected_track_ids", None)
        selected_ids = []
        if isinstance(raw_ids, (list, tuple, set)):
            for value in raw_ids:
                try:
                    track_index = int(value)
                except Exception:
                    continue
                if 0 <= track_index < len(streams):
                    selected_ids.append(track_index)
        if not selected_ids:
            try:
                track_index = int(getattr(job, "selected_track_id", None))
            except Exception:
                return 0
            selected_ids = [track_index] if 0 <= track_index < len(streams) else []
        return len(tuple(dict.fromkeys(selected_ids)))
    return 1


def _resolved_sample_rate_hz(job) -> int:
    requested = str(getattr(job, "audio_sample_rate", "ORIGINAL") or "ORIGINAL").strip().upper()
    if requested in {"32000", "44100", "48000"}:
        return int(requested)
    source_path = str(getattr(job, "source_path", None) or "").strip()
    streams = advanced_profile._probe_audio_stream_details(source_path) if source_path else tuple()
    primary = streams[0] if streams else {}
    try:
        sample_rate = int(primary.get("sample_rate") or 0)
    except Exception:
        sample_rate = 0
    return sample_rate if sample_rate > 0 else 44100


def _resolved_channel_count(job) -> int:
    channel_policy = str(getattr(job, "audio_channel_policy", "KEEP_ORIGINAL") or "KEEP_ORIGINAL").strip().upper()
    if channel_policy == "DOWNMIX_TO_MONO":
        return 1
    if channel_policy == "DOWNMIX_TO_STEREO":
        return 2
    try:
        source_channels = int(getattr(job, "primary_audio_channels", 2) or 2)
    except Exception:
        source_channels = 2
    return max(1, source_channels)


def _estimated_lossless_bitrate_bps(job, output_format: str | None) -> int:
    normalized = normalize_output_format(output_format)
    pcm_bitrate = max(1, _resolved_sample_rate_hz(job) * _resolved_channel_count(job) * 16)
    if normalized == "flac":
        return max(384_000, int(pcm_bitrate * 0.55))
    return pcm_bitrate


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(float(value)) if value not in (None, "") else None
    except Exception:
        return None
    return parsed if parsed is not None and parsed > 0 else None


def _source_total_bitrate_bps(job) -> int | None:
    for attr in ("input_bitrate", "source_bitrate", "total_bitrate", "bitrate"):
        value = _safe_positive_int(getattr(job, attr, None))
        if value is not None:
            # Most imported/probed bitrate attributes are stored as bps. If an
            # old payload stores a compact kbps value, normalize it defensively.
            return value * 1000 if value < 10_000 else value
    source_path = str(getattr(job, "source_path", None) or getattr(job, "input_path", None) or "").strip()
    if source_path:
        try:
            duration = safe_duration_seconds(getattr(job, "duration", None))
            if duration and duration > 0:
                import os

                size_bytes = os.path.getsize(source_path)
                if size_bytes > 0:
                    return max(1, int(round((float(size_bytes) * 8.0) / float(duration))))
        except Exception:
            pass
    return None


def _source_audio_bitrate_bps(job) -> int | None:
    # Prefer the probed audio stream bitrate because it is the same data shown
    # in the "Trilhas de áudio" selector. Job-level cached attributes can be
    # stale or rounded after previous estimates, which made the suggested Audio
    # profile bitrate show 96 kbps while the detected track showed 128 kbps.
    source_path = str(getattr(job, "source_path", None) or getattr(job, "input_path", None) or "").strip()
    if source_path:
        try:
            streams = advanced_profile._probe_audio_stream_details(source_path)
        except Exception:
            streams = tuple()
        bitrates: list[int] = []
        for stream in streams or ():
            if not isinstance(stream, dict):
                continue
            value = _safe_positive_int(stream.get("bit_rate"))
            if value is not None:
                bitrates.append(value * 1000 if value < 10_000 else value)
        if bitrates:
            return max(1, int(round(sum(bitrates) / len(bitrates))))

    for attr in (
        "primary_audio_bitrate",
        "source_audio_bitrate",
        "input_audio_bitrate",
        "audio_bitrate",
    ):
        value = _safe_positive_int(getattr(job, attr, None))
        if value is not None:
            return value * 1000 if value < 10_000 else value
    return None




def _growth_safe_bitrate_cap_kbps(job, stream_count: int = 1) -> int | None:
    """Return a safe per-stream lossy bitrate cap for the Audio profile.

    For video -> audio extraction/conversion, the old behavior could encode M4A
    at 128 kbps even when the entire input file averaged ~106 kbps. With the
    same duration, that necessarily creates a larger file. The cap below keeps
    the selected target bitrate below the source budget whenever reliable source
    bitrate information exists.
    """
    source_audio_bps = _source_audio_bitrate_bps(job)
    if source_audio_bps is not None:
        # Match the same rounded kbps value displayed in "Trilhas de áudio".
        # ffprobe often reports MP3/AAC tracks as e.g. 1279xx bps; the selector
        # displays that as 128 kbps, but flooring here produced a 127 kbps cap.
        # The nearest-supported-not-above step then fell back to 96 kbps. Rounding
        # keeps the effective cap aligned with the detected/displayed track value.
        return max(1, int(round(float(source_audio_bps) / 1000.0)))

    source_total_bps = _source_total_bitrate_bps(job)
    if source_total_bps is not None:
        # Fallback only when stream bitrate is unavailable.
        divisor = max(1, int(stream_count or 1))
        overhead_safe_kbps = int((float(source_total_bps) * 0.94) / 1000.0 / divisor)
        return max(1, overhead_safe_kbps)
    return None


def effective_audio_bitrate_kbps(job) -> int:
    """Return the effective per-stream bitrate used by the Audio profile.

    This is the single source of truth for the visible Audio profile bitrate and
    for FFmpeg options. It may be lower than the raw UI selection when the
    growth-safe cap is active.
    """
    output_format = normalize_output_format(getattr(job, "audio_output_format", None))
    if not uses_target_bitrate(output_format):
        return max(1, int(round(_estimated_lossless_bitrate_bps(job, output_format) / 1000.0)))
    bitrate_kbps = normalize_bitrate_kbps(
        getattr(job, "audio_bitrate_kbps", None),
        output_format=output_format,
    )
    channels_policy = str(getattr(job, "audio_channel_policy", "KEEP_ORIGINAL") or "KEEP_ORIGINAL").strip().upper()
    if channels_policy == "DOWNMIX_TO_MONO":
        bitrate_kbps = min(bitrate_kbps, 96)
    safe_cap_kbps = _growth_safe_bitrate_cap_kbps(job, selected_output_audio_stream_count(job) or 1)
    if safe_cap_kbps is not None:
        # Cap to the exact detected/source budget. Do not round down to the
        # nearest predefined combo option, otherwise a detected 94 kbps track
        # becomes 64 kbps.
        bitrate_kbps = min(bitrate_kbps, int(round(float(safe_cap_kbps))))
    return max(1, int(bitrate_kbps))


def _effective_audio_bitrate_bps(job) -> int:
    return max(1, effective_audio_bitrate_kbps(job) * 1000)


def estimate_size_bytes(job) -> int | None:
    duration_seconds = safe_duration_seconds(getattr(job, "duration", None))
    if duration_seconds is None or duration_seconds <= 0:
        return None
    stream_count = selected_output_audio_stream_count(job)
    if stream_count <= 0:
        return None
    total_bitrate_bps = estimate_output_bitrate_bps(job)
    if total_bitrate_bps is None or total_bitrate_bps <= 0:
        return None
    container_overhead_ratio = 1.02
    estimated = int((float(total_bitrate_bps) * float(duration_seconds) / 8.0) * container_overhead_ratio)
    return max(1, estimated)


def estimate_output_bitrate_bps(job) -> int | None:
    stream_count = selected_output_audio_stream_count(job)
    if stream_count <= 0:
        return None
    return max(1, _effective_audio_bitrate_bps(job) * stream_count)


def output_video_traits(job) -> tuple[str | None, str | None]:
    return ("Somente áudio", None)


def build_ffmpeg_options(job, output_ext: str) -> list[str]:
    codec = codec_for_format(getattr(job, "audio_output_format", None) or output_ext)
    bitrate_bps = _effective_audio_bitrate_bps(job)
    track_policy = str(getattr(job, "audio_track_policy", "KEEP_DEFAULT_ONLY") or "KEEP_DEFAULT_ONLY").strip().upper()
    channel_policy = str(getattr(job, "audio_channel_policy", "KEEP_ORIGINAL") or "KEEP_ORIGINAL").strip().upper()
    sample_rate = str(getattr(job, "audio_sample_rate", "ORIGINAL") or "ORIGINAL").strip().upper()
    volume_norm = str(getattr(job, "audio_volume_normalization", "OFF") or "OFF").strip().upper()
    metadata_policy = str(getattr(job, "audio_metadata_policy", "PRESERVE") or "PRESERVE").strip().upper()

    options: list[str] = ["-vn"]
    if track_policy == "KEEP_ALL":
        options += ["-map", "0:a?"]
    elif track_policy == "SELECTED_ONLY":
        raw_ids = getattr(job, "selected_track_ids", None)
        selected_ids: list[int] = []
        if isinstance(raw_ids, (list, tuple, set)):
            for value in raw_ids:
                try:
                    selected_ids.append(max(0, int(value)))
                except Exception:
                    continue
        if not selected_ids:
            try:
                selected_ids = [max(0, int(getattr(job, "selected_track_id", None)))]
            except Exception:
                selected_ids = [0]
        for track_index in tuple(dict.fromkeys(selected_ids)):
            options += ["-map", f"0:a:{track_index}?"]
    else:
        options += ["-map", "0:a:0?"]

    if metadata_policy == "REMOVE":
        options += ["-map_metadata", "-1"]

    options += ["-c:a", codec]
    if codec != "copy" and uses_target_bitrate(getattr(job, "audio_output_format", None) or output_ext):
        options += ["-b:a", str(bitrate_bps)]
    if channel_policy == "DOWNMIX_TO_MONO":
        options += ["-ac", "1"]
    elif channel_policy == "DOWNMIX_TO_STEREO":
        options += ["-ac", "2"]
    if sample_rate in {"32000", "44100", "48000"}:
        options += ["-ar", sample_rate]
    if volume_norm == "LIGHT":
        options += ["-af", "loudnorm=I=-18:TP=-2:LRA=11"]
    elif volume_norm == "STANDARD":
        options += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    if output_ext == ".m4a":
        options += ["-movflags", "+faststart"]
    return options
