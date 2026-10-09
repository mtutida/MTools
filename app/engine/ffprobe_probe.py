import json
import logging
import re
from typing import Any

from app.core.ffmpeg_binaries import resolve_ffmpeg, resolve_ffprobe
from app.core.formatting import parse_duration_seconds
from app.core.subprocess_utils import run_no_window


_LOGGER = logging.getLogger("app.engine.ffprobe_probe")
PROBE_TIMEOUT = 30
_PROBE_COMMANDS = (
    [],
    ["-analyzeduration", "100M", "-probesize", "100M"],
    ["-fflags", "+genpts", "-err_detect", "ignore_err", "-analyzeduration", "100M", "-probesize", "100M"],
)


def _run_probe(path: str) -> dict[str, Any] | None:
    ffprobe = resolve_ffprobe()
    if not ffprobe:
        return None

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
            result = run_no_window(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=PROBE_TIMEOUT,
            )
            data = json.loads(result.stdout or "{}")
            if data.get("streams") or data.get("format"):
                return data
        except Exception:
            _LOGGER.debug("ffprobe command failed for %s with args %s", path, extra, exc_info=True)
            continue

    return None


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        number = float(value)
        return number if number >= 0 else None
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return None


def _parse_duration_value(raw: Any) -> float | None:
    return parse_duration_seconds(raw)


def _fallback_duration_from_ffprobe(path: str) -> float | None:
    ffprobe = resolve_ffprobe()
    if not ffprobe:
        return None

    command_variants = (
        ["-v", "error", "-show_entries", "format=duration", "-of", "default=nokey=1:noprint_wrappers=1", path],
        ["-v", "error", "-show_entries", "stream=duration", "-select_streams", "v:0", "-of", "default=nokey=1:noprint_wrappers=1", path],
        ["-v", "error", "-analyzeduration", "100M", "-probesize", "100M", "-show_entries", "format=duration", "-of", "default=nokey=1:noprint_wrappers=1", path],
        ["-v", "error", "-analyzeduration", "100M", "-probesize", "100M", "-show_entries", "stream=duration", "-select_streams", "v:0", "-of", "default=nokey=1:noprint_wrappers=1", path],
    )

    for args in command_variants:
        try:
            result = run_no_window(
                [ffprobe, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=PROBE_TIMEOUT,
            )
            for line in (result.stdout or "").splitlines():
                value = _parse_duration_value(line.strip())
                if value is not None:
                    return value
        except Exception:
            _LOGGER.debug("fallback ffprobe duration command failed for %s", path, exc_info=True)
            continue

    return None


def _fallback_duration_from_ffmpeg(path: str) -> float | None:
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return None

    cmd = [ffmpeg, "-hide_banner", "-i", path, "-f", "null", "-"]
    try:
        result = run_no_window(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=max(PROBE_TIMEOUT, 45),
        )
    except Exception:
        _LOGGER.debug("fallback ffmpeg duration probe failed for %s", path, exc_info=True)
        return None

    stderr = result.stderr or ""

    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:[\.,]\d+)?)", stderr)
    if match:
        h, m, s = match.groups()
        try:
            value = int(h) * 3600 + int(m) * 60 + float(s.replace(",", "."))
            if value > 0:
                return value
        except Exception:
            _LOGGER.debug("Could not parse ffmpeg Duration line for %s", path, exc_info=True)

    times = re.findall(r"time=(\d+):(\d+):(\d+(?:[\.,]\d+)?)", stderr)
    if times:
        h, m, s = times[-1]
        try:
            value = int(h) * 3600 + int(m) * 60 + float(s.replace(",", "."))
            if value > 0:
                return value
        except Exception:
            _LOGGER.debug("Could not parse ffmpeg progress time for %s", path, exc_info=True)

    return None


def _pick_duration_seconds(data: dict[str, Any], video_stream: dict[str, Any] | None, path: str) -> float | None:
    format_block = data.get("format") or {}
    streams = data.get("streams") or []
    format_tags = format_block.get("tags") or {}

    candidates = [
        format_block.get("duration"),
        format_tags.get("DURATION"),
        format_tags.get("duration"),
        format_tags.get("DURATION-eng"),
        format_tags.get("TIMELENGTH"),
        (video_stream or {}).get("duration"),
        ((video_stream or {}).get("tags") or {}).get("DURATION"),
        ((video_stream or {}).get("tags") or {}).get("duration"),
        ((video_stream or {}).get("tags") or {}).get("DURATION-eng"),
        ((video_stream or {}).get("tags") or {}).get("TIMELENGTH"),
    ]

    for stream in streams:
        tags = stream.get("tags") or {}
        candidates.extend([
            stream.get("duration"),
            tags.get("DURATION"),
            tags.get("duration"),
            tags.get("DURATION-eng"),
            tags.get("TIMELENGTH"),
        ])

    for raw in candidates:
        value = _parse_duration_value(raw)
        if value is not None:
            return value

    value = _fallback_duration_from_ffprobe(path)
    if value is not None:
        return value

    return _fallback_duration_from_ffmpeg(path)


def _pick_video_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") == "video":
            return stream
    return streams[0] if streams else None


def _pick_audio_streams(streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [stream for stream in streams if stream.get("codec_type") == "audio"]


def _build_audio_stream_metadata(streams: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int, int | None]:
    audio_streams = _pick_audio_streams(streams)
    rendered: list[dict[str, Any]] = []
    primary_channels = 2
    primary_sample_rate: int | None = None

    for logical_index, stream in enumerate(audio_streams):
        tags = stream.get("tags") or {}
        title = str(tags.get("title") or tags.get("handler_name") or "").strip()
        language = str(tags.get("language") or "").strip()
        codec = str(stream.get("codec_name") or "?").upper()
        channels = _safe_int(stream.get("channels")) or 2
        sample_rate = _safe_int(stream.get("sample_rate"))
        disposition = stream.get("disposition") or {}
        is_default = bool(disposition.get("default"))
        if logical_index == 0 or is_default:
            primary_channels = channels
            if sample_rate and sample_rate > 0:
                primary_sample_rate = sample_rate
        label_bits = [f"Trilha {logical_index + 1}"]
        if title:
            label_bits.append(title)
        if language:
            label_bits.append(language.upper())
        label_bits.append(f"{channels} ch")
        label_bits.append(codec)
        rendered.append({
            "id": logical_index,
            "label": " • ".join(label_bits),
            "channels": channels,
            "sample_rate": sample_rate,
            "is_default": is_default,
        })

    return rendered, len(rendered), primary_channels, primary_sample_rate


def _pick_bitrate_bps(data: dict[str, Any], video_stream: dict[str, Any] | None, duration_seconds: float | None) -> int | None:
    format_block = data.get("format") or {}
    format_tags = format_block.get("tags") or {}
    stream_tags = (video_stream or {}).get("tags") or {}

    candidates = [
        format_block.get("bit_rate"),
        (video_stream or {}).get("bit_rate"),
        format_tags.get("BPS"),
        format_tags.get("BPS-eng"),
        format_tags.get("bit_rate"),
        stream_tags.get("BPS"),
        stream_tags.get("BPS-eng"),
        stream_tags.get("bit_rate"),
    ]

    for raw in candidates:
        value = _safe_int(raw)
        if value is not None and value > 0:
            return value

    size_bytes = _safe_int(format_block.get("size"))
    if size_bytes and duration_seconds and duration_seconds > 0:
        calculated = int((size_bytes * 8) / duration_seconds)
        if calculated > 0:
            return calculated

    return None


def probe(path):
    try:
        data = _run_probe(path)
        if not data:
            return None

        streams = data.get("streams", [])
        fmt = data.get("format", {})
        video = _pick_video_stream(streams)

        codec = "?"
        width = "?"
        height = "?"
        fps = "?"
        duration = "?"
        container = "?"
        input_bitrate = None

        if video:
            codec = video.get("codec_name", "?")
            width = _safe_int(video.get("width")) or "?"
            height = _safe_int(video.get("height")) or "?"

            rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
            try:
                num, den = rate.split("/")
                den_v = float(den)
                if den_v != 0:
                    fps_value = float(num) / den_v
                    if fps_value > 0:
                        fps = str(round(fps_value, 2))
            except Exception:
                fps = "?"

        total_seconds = _pick_duration_seconds(data, video, path)
        if total_seconds and total_seconds > 0:
            minutes = int(total_seconds // 60)
            seconds = int(total_seconds % 60)
            duration = f"{minutes:02d}:{seconds:02d}"

        format_name = fmt.get("format_name")
        if format_name:
            container = str(format_name).split(",")[0]

        input_bitrate = _pick_bitrate_bps(data, video, total_seconds)
        audio_streams, audio_track_count, primary_audio_channels, primary_audio_sample_rate = _build_audio_stream_metadata(streams)
        resolution = f"{width}x{height}" if width != "?" and height != "?" else "?"
        return (
            codec,
            resolution,
            fps,
            duration,
            container,
            input_bitrate,
            audio_streams,
            audio_track_count,
            primary_audio_channels,
            primary_audio_sample_rate,
        )

    except Exception:
        return None
