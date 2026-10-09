import logging
import os
from dataclasses import dataclass

from PySide6.QtGui import QColor, QPalette

from app.core.formatting import format_bitrate, format_bytes as _shared_format_bytes
from app.core.compression_profiles import build_profile_tooltip, estimate_output_bitrate_for_profile, estimate_output_is_actionable, estimate_output_video_traits, profile_output_matches_current_settings
from app.core.profiles import audio as audio_profile
from app.core.profiles.common import safe_duration_seconds
from app.engine.encode_estimator import estimate_bitrate_from_size
from app.ui.theme_tokens import resolve_status_color

_LOGGER = logging.getLogger(__name__)

STATUS_TEXT_MAP = {
    "READY": "Comprimir",
    "NO_GAIN": "Pronto!",
    "QUEUED": "Sair da fila",
    "RUNNING": "PROCESSANDO",
    "PROCESSING": "PROCESSANDO",
    "COMPLETED": "Concluído",
    "DONE": "Concluído",
    "FAILED": "FALHA",
    "ERROR": "FALHA",
    "CANCELLED": "Cancelado",
}


@dataclass(frozen=True)
class FileCardViewData:
    source: str | None
    output_path: str | None
    name: str
    status_text: str
    raw_status: str
    status_color: QColor
    resolution: str
    fps: str
    output_resolution: str | None
    output_fps: str | None
    duration: str
    source_size_text: str | None
    estimated_size_text: str | None
    size_text: str | None
    input_size_text: str | None
    output_size_text: str | None
    input_bitrate_text: str | None
    output_bitrate_text: str | None
    destination_dir: str
    profile_button_text: str
    profile_tooltip: str
    thumbnail: str | None
    progress: int
    is_audio: bool
    input_channels_text: str | None
    output_channels_text: str | None
    output_format_text: str | None
    input_sample_rate_text: str | None
    output_sample_rate_text: str | None


def normalize_status(status) -> tuple[str, str]:
    text = status
    if not isinstance(text, str):
        text = str(text)
    if "." in text:
        text = text.split(".")[-1]
    return STATUS_TEXT_MAP.get(text, text), text


def format_bytes(size_bytes: int | float | None) -> str | None:
    return _shared_format_bytes(size_bytes, allow_non_positive=True)


def _is_quick_estimate_pending(job) -> bool:
    return bool(getattr(job, "quick_estimate_pending", False))


def build_display_name(job) -> str:
    source = getattr(job, "input_path", None) or getattr(job, "source_path", None)
    output_path = getattr(job, "output_path", None)
    fallback_name = getattr(job, "file_name", "unknown")

    try:
        if source and output_path:
            src_base = os.path.basename(source)
            src_name, src_ext = os.path.splitext(src_base)
            out_base = os.path.basename(output_path)
            out_name, out_ext = os.path.splitext(out_base)

            if out_name.startswith(src_name):
                suffix = out_name[len(src_name) :]
                if suffix:
                    return f"{src_name} [{suffix}]{out_ext}"
                return out_base
            return out_base
    except Exception:
        return fallback_name

    return fallback_name


def _resolve_source_size(job, source: str | None) -> int | None:
    source_size = getattr(job, "source_size", None)
    if source_size is not None:
        try:
            value = int(source_size)
            if value > 0:
                return value
        except Exception:
            _LOGGER.debug("Invalid source_size value while preparing file card data.", exc_info=True)

    if not source or not os.path.exists(source):
        return None

    try:
        value = int(os.path.getsize(source))
        return value if value > 0 else None
    except Exception:
        return None


def resolve_playable_output_path(job) -> str | None:
    """Return the generated output path when the completed card can play it."""

    if job is None:
        return None
    status = str(getattr(job, "status", "") or "").strip().upper()
    if status not in {"DONE", "COMPLETED", "SUCCESS", "FINISHED"}:
        return None
    output_path = getattr(job, "output_path", None)
    if not output_path:
        return None
    try:
        output_path = os.path.normpath(str(output_path))
    except Exception:
        return None
    if not output_path or not os.path.isfile(output_path):
        return None
    try:
        if not profile_output_matches_current_settings(job):
            return None
    except Exception:
        return None
    return output_path


def _resolve_display_target_size(job, source_size: int | None) -> tuple[int | None, bool]:
    actual_output_valid = profile_output_matches_current_settings(job)
    output_size = getattr(job, "output_size_bytes", None) if actual_output_valid else None
    if output_size is not None:
        try:
            value = int(output_size)
            if value > 0:
                return value, False
        except Exception:
            _LOGGER.debug("Invalid output_size value while preparing file card data.", exc_info=True)

    estimated_size = getattr(job, "estimated_size_bytes", None)
    if estimated_size is None:
        return None, True

    try:
        value = int(estimated_size)
    except Exception:
        return None, True

    if value <= 0:
        return None, True

    return value, True


def _minimum_display_gain_ratio(job) -> float:
    profile_mode = str(getattr(job, "profile_mode", "quick") or "quick").strip().lower()
    if profile_mode == "advanced":
        return 0.02
    if profile_mode == "audio":
        return 0.01
    if profile_mode == "smart":
        return 0.05
    try:
        quick_preset = int(getattr(job, "quick_profile_preset", 4))
    except Exception:
        quick_preset = 4
    quick_preset = max(0, min(15, quick_preset))
    quick_min_gain = {
        0: 0.020, 1: 0.035, 2: 0.050, 3: 0.065,
        4: 0.080, 5: 0.100, 6: 0.125, 7: 0.150,
        8: 0.190, 9: 0.240, 10: 0.300, 11: 0.360,
        12: 0.430, 13: 0.500, 14: 0.570, 15: 0.640,
    }
    return quick_min_gain.get(quick_preset, 0.080)


def _fallback_estimated_size_from_source(job, source_size: int | None) -> int | None:
    if source_size is None:
        return None
    try:
        source_value = int(source_size)
    except Exception:
        return None
    if source_value <= 1:
        return None
    gain_ratio = max(0.005, min(0.90, _minimum_display_gain_ratio(job)))
    required_reduction = max(1, int(round(source_value * gain_ratio)))
    return max(1, min(source_value - 1, source_value - required_reduction))


def _resolve_estimated_size_for_display(job, source_size: int | None) -> int | None:
    estimated_size = getattr(job, "estimated_size_bytes", None)
    try:
        estimated_value = int(estimated_size) if estimated_size not in (None, "", 0, "0") else None
    except Exception:
        estimated_value = None
    if estimated_value is not None and estimated_value > 0:
        return estimated_value

    # Do not run sample encodes from the card presenter: this function can be
    # called during paint/model refresh and must remain lightweight. If the
    # asynchronous estimator has not populated the job yet, use the profile's
    # conservative minimum gain so the card still shows a complete preview.
    return _fallback_estimated_size_from_source(job, source_size)


def _resolve_estimated_bitrate_for_display(job, estimated_size: int | None) -> int | None:
    estimated_bitrate = getattr(job, "estimated_output_bitrate", None)
    try:
        estimated_bitrate = int(estimated_bitrate) if estimated_bitrate not in (None, "", 0, "0") else None
    except Exception:
        estimated_bitrate = None
    if estimated_bitrate is not None and estimated_bitrate > 0:
        return estimated_bitrate

    try:
        estimated_bitrate = estimate_output_bitrate_for_profile(job, estimated_size)
    except Exception:
        estimated_bitrate = None
    try:
        estimated_bitrate = int(estimated_bitrate) if estimated_bitrate not in (None, "", 0, "0") else None
    except Exception:
        estimated_bitrate = None
    if estimated_bitrate is not None and estimated_bitrate > 0:
        return estimated_bitrate

    try:
        estimated_bitrate = estimate_bitrate_from_size(estimated_size, getattr(job, "duration", None))
    except Exception:
        estimated_bitrate = None
    try:
        estimated_bitrate = int(estimated_bitrate) if estimated_bitrate not in (None, "", 0, "0") else None
    except Exception:
        estimated_bitrate = None
    return estimated_bitrate if estimated_bitrate is not None and estimated_bitrate > 0 else None


def build_size_text(job, source: str | None) -> tuple[str | None, str | None, str | None]:
    source_size = _resolve_source_size(job, source)
    if source_size is None:
        return None, None, None

    original_text = format_bytes(source_size)
    if _is_quick_estimate_pending(job):
        return original_text, "--", f"{original_text} → --" if original_text else "--"
    target_size, is_estimate = _resolve_display_target_size(job, source_size)
    if target_size is None:
        target_size = _resolve_estimated_size_for_display(job, source_size)
        is_estimate = True
    target_text = format_bytes(target_size)

    if original_text and target_text:
        prefix = "≈" if is_estimate else ""
        return original_text, target_text, f"{original_text} → {prefix}{target_text}"

    return original_text, target_text, original_text




AUDIO_EXTENSIONS = (".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus", ".alac")


def _estimate_output_size_text_from_bitrate(job, bitrate_bps: int | float | None) -> str | None:
    if bitrate_bps in (None, ""):
        return None
    duration_seconds = safe_duration_seconds(getattr(job, "duration", None))
    if duration_seconds is None or duration_seconds <= 0:
        return None
    try:
        estimated_size_bytes = int(round((float(bitrate_bps) * float(duration_seconds)) / 8.0))
    except Exception:
        return None
    return format_bytes(estimated_size_bytes)


def _resolve_audio_output_bitrate_value(job):
    """Return the visible Audio profile bitrate.

    For Audio jobs the user-facing bitrate must be the effective audio target
    selected by the profile (for example, the detected original track bitrate
    after the safety cap), not the container-total bitrate inferred from the
    estimated or actual output size. This keeps the top card aligned with the
    overlay combo, the lower summary and the FFmpeg target.
    """
    try:
        derived = int(audio_profile._effective_audio_bitrate_bps(job))
    except Exception:
        derived = None
    if derived not in (None, 0):
        return derived

    actual_output_valid = profile_output_matches_current_settings(job)
    output_bitrate_value = getattr(job, "output_bitrate", None) if actual_output_valid else None
    if output_bitrate_value not in (None, "", 0, "0"):
        return output_bitrate_value
    estimated_bitrate = getattr(job, "estimated_output_bitrate", None)
    if estimated_bitrate not in (None, "", 0, "0"):
        return estimated_bitrate
    return None


def _is_audio_job(job, source: str | None) -> bool:
    ext = os.path.splitext(str(source or ""))[1].lower()
    profile_mode = str(getattr(job, "profile_mode", "") or "").strip().lower()
    return ext in AUDIO_EXTENSIONS or profile_mode == "audio"


def _format_channel_count(value) -> str | None:
    try:
        channels = int(value) if value not in (None, "") else None
    except Exception:
        return None
    if channels is None or channels <= 0:
        return None
    if channels == 1:
        return "Mono"
    if channels == 2:
        return "Estéreo"
    return f"{channels} canais"


def _resolve_output_channel_count(job):
    policy = str(getattr(job, "audio_channel_policy", "KEEP_ORIGINAL") or "KEEP_ORIGINAL").strip().upper()
    if policy == "DOWNMIX_TO_MONO":
        return 1
    if policy == "DOWNMIX_TO_STEREO":
        return 2
    return getattr(job, "primary_audio_channels", None)




def format_sample_rate(sample_rate_hz: int | float | None) -> str | None:
    if sample_rate_hz in (None, ""):
        return None
    try:
        value = float(sample_rate_hz)
    except Exception:
        return None
    if value <= 0:
        return None
    khz = value / 1000.0
    if abs(khz - round(khz)) < 0.05:
        return f"{int(round(khz))} kHz"
    return f"{khz:.1f} kHz"


def _resolve_output_sample_rate_value(job):
    requested = str(getattr(job, "audio_sample_rate", "ORIGINAL") or "ORIGINAL").strip().upper()
    if requested not in {"", "ORIGINAL"}:
        try:
            value = int(requested)
            return value if value > 0 else None
        except Exception:
            return None
    original = getattr(job, "primary_audio_sample_rate", None)
    try:
        value = int(original)
        return value if value > 0 else None
    except Exception:
        return None

def build_view_data(job, palette: QPalette | None = None) -> FileCardViewData:
    source = getattr(job, "input_path", None) or getattr(job, "source_path", None)
    output_path = getattr(job, "output_path", None)
    status_text, raw_status = normalize_status(getattr(job, "status", "READY"))
    source_size = _resolve_source_size(job, source)
    source_size_text, estimated_size_text, size_text = build_size_text(job, source)

    if raw_status in ("READY", "IDLE", "PENDING"):
        estimated_value = _resolve_estimated_size_for_display(job, source_size)
        actionable = estimate_output_is_actionable(
            job,
            estimated_size_bytes=estimated_value,
            source_size_bytes=source_size,
        )
        if actionable is False:
            status_text = "Pronto!"
            raw_status = "NO_GAIN"

    destination_ref = output_path or source or ""
    destination_dir = os.path.dirname(os.path.normpath(destination_ref)) if destination_ref else ""
    input_bitrate_text = format_bitrate(getattr(job, "input_bitrate", None))
    actual_output_valid = profile_output_matches_current_settings(job)
    if raw_status in ("COMPLETED", "DONE") and not actual_output_valid:
        status_text = "Comprimir"
        raw_status = "READY"

    if _is_quick_estimate_pending(job):
        output_resolution, output_fps = "--", "--"
    else:
        output_resolution, output_fps = estimate_output_video_traits(job)
    is_audio = _is_audio_job(job, source)
    display_estimated_size = None
    if estimated_size_text not in (None, ""):
        display_estimated_size = _resolve_estimated_size_for_display(job, source_size)
    if is_audio:
        output_bitrate_value = _resolve_audio_output_bitrate_value(job)
    else:
        output_bitrate_value = getattr(job, "output_bitrate", None) if actual_output_valid else None
        if output_bitrate_value in (None, "", 0, "0"):
            output_bitrate_value = _resolve_estimated_bitrate_for_display(job, display_estimated_size)

        # Card preview compares the source bitrate shown in the same row with
        # the estimated target bitrate.  Some probes expose a stream bitrate as
        # input while the estimate comes from target container size/duration;
        # without this guard the card can show an apparent Advanced increase
        # (for example 2.39 → 2.61 Mbps) even when the target size is lower.
        # Keep the visible preview aligned with the no-growth contract and with
        # the configuration panel preview, without changing FFmpeg parameters.
        input_bitrate_value = getattr(job, "input_bitrate", None)
        try:
            input_bitrate_limit = int(round(float(input_bitrate_value)))
            output_bitrate_numeric = int(round(float(output_bitrate_value)))
        except Exception:
            input_bitrate_limit = None
            output_bitrate_numeric = None
        if (
            input_bitrate_limit is not None
            and input_bitrate_limit > 0
            and output_bitrate_numeric is not None
            and output_bitrate_numeric > input_bitrate_limit
        ):
            output_bitrate_value = input_bitrate_limit
    if _is_quick_estimate_pending(job):
        output_bitrate_text = "--"
    else:
        output_bitrate_text = format_bitrate(output_bitrate_value)
    input_channels_text = _format_channel_count(getattr(job, "primary_audio_channels", None))
    input_sample_rate_text = format_sample_rate(getattr(job, "primary_audio_sample_rate", None))
    output_channels_text = _format_channel_count(_resolve_output_channel_count(job)) if is_audio else None
    output_format_text = audio_profile.display_label_for_format(getattr(job, "audio_output_format", None)) if is_audio else None
    output_sample_rate_text = format_sample_rate(_resolve_output_sample_rate_value(job)) if is_audio else None
    if _is_quick_estimate_pending(job):
        output_size_text = "--"
    else:
        output_size_text = (format_bytes(getattr(job, "output_size_bytes", None)) if actual_output_valid else None) or estimated_size_text
        if output_size_text in (None, "") and output_bitrate_value not in (None, "", 0, "0"):
            output_size_text = _estimate_output_size_text_from_bitrate(job, output_bitrate_value)
    if is_audio:
        output_resolution = None
        output_fps = None

    return FileCardViewData(
        source=source,
        output_path=output_path,
        name=build_display_name(job),
        status_text=status_text,
        raw_status=raw_status,
        status_color=resolve_status_color(palette, raw_status) if palette is not None else QColor(),
        resolution=str(getattr(job, "resolution", "?")),
        fps=str(getattr(job, "fps", "?")),
        output_resolution=output_resolution,
        output_fps=output_fps,
        duration=str(getattr(job, "duration", "?")),
        source_size_text=source_size_text,
        estimated_size_text=estimated_size_text,
        size_text=size_text,
        input_size_text=source_size_text,
        output_size_text=output_size_text,
        input_bitrate_text=input_bitrate_text,
        output_bitrate_text=output_bitrate_text,
        destination_dir=destination_dir,
        profile_button_text="Configurar",
        profile_tooltip=build_profile_tooltip(job),
        thumbnail=getattr(job, "thumbnail", None),
        progress=int(getattr(job, "progress", 0) or 0),
        is_audio=is_audio,
        input_channels_text=input_channels_text,
        output_channels_text=output_channels_text,
        output_format_text=output_format_text,
        input_sample_rate_text=input_sample_rate_text if is_audio else None,
        output_sample_rate_text=output_sample_rate_text if is_audio else None,
    )
