from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sys
import statistics
import tempfile
from typing import Any, Mapping, Sequence

from app.core.ffmpeg_binaries import resolve_ffmpeg
from app.core.user_data_migration import resolve_legacy_environment
from app.core.subprocess_utils import run_no_window

from app.core.formatting import parse_fps, parse_resolution

AUDIO_ONLY_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wma", ".flac", ".wav", ".aiff", ".aif", ".alac"
}
MUSIC_AUDIO_EXTENSIONS = {".flac", ".wav", ".aiff", ".aif", ".alac"}
LOSSY_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".opus", ".ogg", ".wma"}
FRAME_SAMPLING_TEMP_PREFIX = "compactme_frames_"


@dataclass(frozen=True)
class AutomaticMediaAnalysis:
    media_kind: str
    detected_label: str
    strategy_label: str
    confidence: float
    width: int | None
    height: int | None
    fps: float | None
    duration_seconds: float | None
    video_bitrate_bps: int | None
    audio_bitrate_bps: int | None
    suggested_width: int | None
    suggested_height: int | None
    suggested_fps: float | None
    suggested_video_bitrate_bps: int | None
    suggested_audio_bitrate_bps: int | None
    output_format: str
    estimated_output_bytes: int | None
    visual_variation_score: float | None = None
    static_frame_ratio: float | None = None
    visual_motion_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def estimated_output_mb(self) -> float | None:
        if self.estimated_output_bytes is None:
            return None
        return self.estimated_output_bytes / (1024 * 1024)


def build_automatic_recommendation(media_info: Any, file_size_bytes: int) -> AutomaticMediaAnalysis:
    """Build a metadata-only compression recommendation.

    This phase intentionally avoids UI imports, Qt objects, frame sampling and
    filesystem probing. It accepts the tuple returned by ``ffprobe_probe.probe``
    or a dict/object with equivalent fields, then returns a stable data contract
    that later UI/encoding phases can consume.
    """
    source_size = _positive_int(file_size_bytes)
    info = _normalize_media_info(media_info)

    width, height = info["resolution"]
    fps = info["fps"]
    duration = info["duration_seconds"]
    total_bitrate = info["input_bitrate_bps"]
    audio_bitrate = _resolve_audio_bitrate(info, total_bitrate, duration, source_size)
    has_video = bool(width and height)
    extension = str(info.get("extension") or "").lower()

    if not has_video:
        return _build_audio_recommendation(
            extension=extension,
            duration_seconds=duration,
            input_bitrate_bps=total_bitrate,
            audio_bitrate_bps=audio_bitrate,
            file_size_bytes=source_size,
        )

    video_bitrate = _resolve_video_bitrate(total_bitrate, audio_bitrate, duration, source_size)
    pixels = int(width or 0) * int(height or 0)
    source_bpp = _bits_per_pixel_frame(video_bitrate, width, height, fps)
    already_compressed = _is_already_compressed(video_bitrate, width, height, fps, source_bpp)
    frame_metrics = _analyze_light_frame_staticity(
        source_path=str(info.get("source_path") or ""),
        duration_seconds=duration,
        width=width,
        height=height,
    )
    content_class = _classify_video(
        width,
        height,
        fps,
        video_bitrate,
        source_bpp,
        already_compressed,
        source_path=str(info.get("source_path") or ""),
        duration_seconds=duration,
        frame_metrics=frame_metrics,
    )

    target_width, target_height = _suggest_video_resolution(width, height, content_class)
    target_fps = _suggest_video_fps(fps, content_class)
    target_video_bitrate = _suggest_video_bitrate(
        source_width=width,
        source_height=height,
        source_fps=fps,
        source_video_bitrate=video_bitrate,
        content_class=content_class,
        already_compressed=already_compressed,
    )
    target_audio_bitrate = _suggest_audio_bitrate(audio_bitrate, media_kind="video", content_class=content_class)
    estimated = _estimate_output_size(duration, target_video_bitrate, target_audio_bitrate)
    already_compressed_for_safety = already_compressed and content_class not in {"karaoke_text", "center_text_static_background", "text_overlay_low_motion", "compressed_static_or_text", "low_visual_change"}
    estimated = _cap_estimate_for_safe_gain(estimated, source_size, already_compressed=already_compressed_for_safety)

    detected_label, strategy_label, confidence = _video_labels(content_class, already_compressed_for_safety, pixels, fps, source_bpp)
    return AutomaticMediaAnalysis(
        media_kind="video",
        detected_label=detected_label,
        strategy_label=strategy_label,
        confidence=confidence,
        width=width,
        height=height,
        fps=fps,
        duration_seconds=duration,
        video_bitrate_bps=video_bitrate,
        audio_bitrate_bps=audio_bitrate,
        suggested_width=target_width,
        suggested_height=target_height,
        suggested_fps=target_fps,
        suggested_video_bitrate_bps=target_video_bitrate,
        suggested_audio_bitrate_bps=target_audio_bitrate,
        output_format="mp4",
        estimated_output_bytes=estimated,
        visual_variation_score=frame_metrics.get("mean_delta") if frame_metrics else None,
        static_frame_ratio=frame_metrics.get("static_ratio") if frame_metrics else None,
        visual_motion_label=frame_metrics.get("label") if frame_metrics else None,
    )


def automatic_recommendation_to_profile_payload(analysis: AutomaticMediaAnalysis) -> dict[str, Any]:
    """Convert the recommendation to the existing profile payload vocabulary.

    This remains UI-free and does not change any encoder behavior by itself.
    It is provided so the next phase can wire Automático into the existing
    Advanced/Audio contracts without inventing a parallel compression path.
    """
    if analysis.media_kind == "audio":
        output_format = str(analysis.output_format or "mp3").lower()
        bitrate = _kbps(analysis.suggested_audio_bitrate_bps) or 96
        return {
            "compression_mode": "audio",
            "audio_output_format": output_format,
            "audio_bitrate_kbps": bitrate,
            "audio_track_policy": "KEEP_DEFAULT_ONLY",
            "audio_channel_policy": "KEEP_ORIGINAL",
            "audio_sample_rate": "ORIGINAL",
            "audio_quality_mode": "BALANCED",
            "audio_volume_normalization": "OFF",
            "audio_metadata_policy": "PRESERVE",
            "extract_subtitle": False,
        }

    payload: dict[str, Any] = {
        "compression_mode": "advanced",
        "manual_control_type": "DIRECT_BITRATE",
        "advanced_target_mode": "bitrate",
        "advanced_target_bitrate_kbps": _kbps(analysis.suggested_video_bitrate_bps) or 1200,
        "advanced_resolution_bitrate_mode": "explicit",
        "advanced_resolution_bitrate_kbps": _kbps(analysis.suggested_video_bitrate_bps) or 1200,
        "advanced_fps_policy": "keep",
        "advanced_fps_value": None,
        "advanced_audio_policy": "reduce_to_value" if analysis.suggested_audio_bitrate_bps else "keep",
        "advanced_audio_bitrate_kbps": _kbps(analysis.suggested_audio_bitrate_bps),
        "allow_audio_quality_reduction": bool(analysis.suggested_audio_bitrate_bps),
        "audio_track_policy": "KEEP_ALL",
        "selected_track_id": None,
        "audio_channel_policy": "KEEP_ORIGINAL",
        "video_output_format": str(analysis.output_format or "MP4").upper(),
        "video_output_extension": ".mp4",
    }

    if analysis.suggested_width and analysis.suggested_height:
        payload.update({
            "manual_control_type": "RESOLUTION_DRIVEN",
            "advanced_target_mode": "resolution",
            "advanced_width": analysis.suggested_width,
            "advanced_height": analysis.suggested_height,
        })
    if analysis.suggested_fps and analysis.fps and analysis.suggested_fps < analysis.fps:
        payload.update({
            "advanced_fps_policy": "reduce_to_value",
            "advanced_fps_value": int(round(analysis.suggested_fps)),
        })
    return payload


def _normalize_media_info(media_info: Any) -> dict[str, Any]:
    raw = _as_mapping(media_info)
    if raw is None and isinstance(media_info, Sequence) and not isinstance(media_info, (str, bytes, bytearray)):
        values = list(media_info)
        raw = {
            "codec": _sequence_get(values, 0),
            "resolution": _sequence_get(values, 1),
            "fps": _sequence_get(values, 2),
            "duration": _sequence_get(values, 3),
            "container": _sequence_get(values, 4),
            "input_bitrate_bps": _sequence_get(values, 5),
            "audio_streams": _sequence_get(values, 6),
            "audio_track_count": _sequence_get(values, 7),
            "primary_audio_channels": _sequence_get(values, 8),
            "primary_audio_sample_rate": _sequence_get(values, 9),
        }
    if raw is None:
        raw = {}

    resolution = _pick(raw, "resolution", "video_resolution")
    width = _positive_int(_pick(raw, "width", "video_width"))
    height = _positive_int(_pick(raw, "height", "video_height"))
    if (width is None or height is None) and resolution not in (None, "", "?"):
        parsed = parse_resolution(resolution)
        if parsed:
            width, height = parsed

    duration = _duration_seconds(_pick(raw, "duration_seconds", "duration", "input_duration", "source_duration"))
    fps = _metadata_fps(_pick(raw, "fps", "frame_rate", "avg_frame_rate"))
    input_bitrate = _positive_int(_pick(raw, "input_bitrate_bps", "input_bitrate", "bitrate", "bit_rate"))
    source_path = str(_pick(raw, "source_path", "input_path", "path") or "").strip()
    extension = str(_pick(raw, "extension", "ext") or Path(source_path).suffix or "").lower()
    return {
        "codec": _pick(raw, "codec", "video_codec"),
        "resolution": (width, height),
        "fps": fps,
        "duration_seconds": duration,
        "container": _pick(raw, "container", "format", "format_name"),
        "input_bitrate_bps": input_bitrate,
        "audio_streams": _pick(raw, "audio_streams", "audio_tracks") or [],
        "audio_track_count": _positive_int(_pick(raw, "audio_track_count", "audio_tracks_count")),
        "primary_audio_channels": _positive_int(_pick(raw, "primary_audio_channels", "audio_channels", "channels")),
        "primary_audio_sample_rate": _positive_int(_pick(raw, "primary_audio_sample_rate", "audio_sample_rate", "sample_rate")),
        "extension": extension,
        "source_path": source_path,
    }


def _metadata_fps(value: Any) -> float | None:
    parsed = parse_fps(value)
    if parsed is not None:
        return parsed
    if value in (None, "", "?"):
        return None
    text = str(value).strip().lower().replace(",", ".")
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    if not match:
        return None
    try:
        fps = float(match.group(1))
    except Exception:
        return None
    return fps if fps > 0 else None


def _path_hint_match(source_path: str, hints: Sequence[str]) -> bool:
    text = str(source_path or "").lower()
    if not text:
        return False
    compact = re.sub(r"[^a-z0-9áàâãéêíóôõúç]+", " ", text)
    compact_dotted = text.replace("_", ".").replace("-", ".")
    return any(hint in compact or hint in compact_dotted for hint in hints)


def _lesson_filename_hint(source_path: str) -> bool:
    hints = (
        "aula",
        "video aula",
        "video.aula",
        "vídeo aula",
        "slide",
        "slides",
        "curso",
        "treinamento",
        "lesson",
        "lecture",
        "class",
        "screen recording",
        "gravacao de tela",
        "gravação de tela",
        "tela gravada",
        "screencast",
        "webinar",
    )
    return _path_hint_match(source_path, hints)


def _karaoke_filename_hint(source_path: str) -> bool:
    hints = (
        "karaoke",
        "karaokê",
        "karaoke",
        "karaok",
        "lyrics",
        "lyric",
        "legenda",
        "legendas",
        "subtitle",
        "subtitles",
        "sing along",
        "sing-along",
        "instrumental",
        "playback",
    )
    return _path_hint_match(source_path, hints)


def _build_audio_recommendation(*, extension: str, duration_seconds: float | None, input_bitrate_bps: int | None, audio_bitrate_bps: int | None, file_size_bytes: int | None) -> AutomaticMediaAnalysis:
    source_bitrate = audio_bitrate_bps or input_bitrate_bps
    voice_like = _is_voice_like_audio(extension, source_bitrate)
    already_compressed = bool(extension in LOSSY_AUDIO_EXTENSIONS and source_bitrate and source_bitrate <= 128_000)

    if voice_like:
        target_audio = 64_000 if already_compressed else 80_000
        detected = "Áudio de voz/fala provável"
        strategy = "otimizar fala clara com bitrate baixo"
        confidence = 0.66 if already_compressed else 0.58
        output_format = "mp3"
    else:
        target_audio = 160_000 if extension in MUSIC_AUDIO_EXTENSIONS else 128_000
        detected = "Áudio musical ou conteúdo tonal provável"
        strategy = "preservar qualidade tonal com compressão moderada"
        confidence = 0.54
        output_format = "mp3"

    if source_bitrate:
        floor = 48_000 if voice_like else 96_000
        target_audio = min(target_audio, max(floor, int(source_bitrate * (0.92 if already_compressed else 0.75))))
    target_audio = _round_bitrate(target_audio, minimum=48_000, maximum=192_000)
    estimated = _estimate_output_size(duration_seconds, None, target_audio)
    estimated = _cap_estimate_for_safe_gain(estimated, file_size_bytes, already_compressed=already_compressed)

    return AutomaticMediaAnalysis(
        media_kind="audio",
        detected_label=detected,
        strategy_label=strategy,
        confidence=confidence,
        width=None,
        height=None,
        fps=None,
        duration_seconds=duration_seconds,
        video_bitrate_bps=None,
        audio_bitrate_bps=source_bitrate,
        suggested_width=None,
        suggested_height=None,
        suggested_fps=None,
        suggested_video_bitrate_bps=None,
        suggested_audio_bitrate_bps=target_audio,
        output_format=output_format,
        estimated_output_bytes=estimated,
    )


def _classify_video(
    width: int | None,
    height: int | None,
    fps: float | None,
    video_bitrate: int | None,
    source_bpp: float | None,
    already_compressed: bool,
    *,
    source_path: str = "",
    duration_seconds: float | None = None,
    frame_metrics: Mapping[str, Any] | None = None,
) -> str:
    fps_value = fps or 0.0
    bpp = source_bpp or 0.0
    lesson_hint = _lesson_filename_hint(source_path)
    karaoke_hint = _karaoke_filename_hint(source_path)
    visual_class = _classify_frame_metrics(frame_metrics)

    if _frame_metrics_suggest_center_text_static(frame_metrics) and height and height <= 1080:
        # Lyric/karaokê videos often place large text in the middle of the
        # frame, not in the subtitle area. Let this signal win before the
        # generic already-compressed guard so a 1080p lyric video is not
        # treated as having only 2–15% safe gain.
        return "center_text_static_background"

    if visual_class == "text_overlay" and height and height <= 1080:
        if karaoke_hint:
            return "karaoke_text"
        return "text_overlay_low_motion"

    if visual_class == "very_static" and height and height <= 1080:
        if karaoke_hint:
            return "karaoke_text"
        if lesson_hint or (duration_seconds and duration_seconds >= 180):
            return "slides_screen"
        return "low_visual_change"
    if visual_class == "low_motion" and height and height <= 1080:
        if karaoke_hint:
            return "karaoke_text"
        if lesson_hint and (not duration_seconds or duration_seconds >= 90):
            return "slides_screen"
        if already_compressed:
            return "low_visual_change"

    if _frame_metrics_suggest_text_overlay(frame_metrics) and height and height <= 1080:
        return "text_overlay_low_motion"

    if visual_class == "moderate" and already_compressed and _frame_metrics_suggest_static_background(frame_metrics):
        return "compressed_static_or_text"

    # Explicit karaoke/lyrics hints must win before the generic
    # already-compressed guard. Karaoke files often arrive as very small
    # 720p/1080p MP4s, but the user still expects a text/audio-preserving
    # recommendation instead of the generic “compressão leve” path.
    if karaoke_hint and height and height <= 1080:
        fps_ok = not fps_value or fps_value <= 31.5
        bitrate_density_ok = not bpp or bpp <= 0.180
        source_bitrate_ok = not video_bitrate or video_bitrate <= 5_000_000
        if fps_ok and bitrate_density_ok and source_bitrate_ok:
            return "karaoke_text"

    if already_compressed:
        return "already_compressed"

    # Explicit aula/slides/tela hints from the file name/path are useful when
    # no frame sampling is allowed. Keep the guard tied to common teaching/screen
    # metadata to avoid relabeling arbitrary movies only because of a folder name.
    if lesson_hint and height and height <= 1080:
        fps_ok = not fps_value or fps_value <= 31.0
        bitrate_density_ok = not bpp or bpp <= 0.115
        duration_ok = not duration_seconds or duration_seconds >= 180
        source_bitrate_ok = not video_bitrate or video_bitrate <= 3_000_000
        if fps_ok and bitrate_density_ok and duration_ok and source_bitrate_ok:
            return "slides_screen"

    if fps_value and fps_value <= 24 and bpp and bpp <= 0.10:
        return "slides_screen"
    # Metadata-only low-motion hint: many aulas/slides/screen recordings are
    # exported at 25/30 fps, 720p/1080p and already have a low bitrate density.
    # Accept 30.3/30.303 fps readings because FFprobe often reports nominal
    # 30 fps content with fractional values.
    if fps_value and fps_value <= 31.0 and bpp and bpp <= 0.055 and height and height <= 1080:
        return "slides_screen"
    if fps_value and fps_value <= 20:
        return "slides_screen"
    if fps_value >= 50 or bpp >= 0.145:
        return "high_motion"
    if height and height >= 1440:
        return "high_resolution"
    return "moderate_motion"


def _video_labels(content_class: str, already_compressed: bool, pixels: int, fps: float | None, source_bpp: float | None) -> tuple[str, str, float]:
    if already_compressed:
        return "Vídeo já muito comprimido", "aplicar compressão leve e evitar perda visível", 0.72
    if content_class == "slides_screen":
        return "Aula/slides/tela gravada provável", "manter resolução e reduzir bitrate", 0.68
    if content_class == "karaoke_text":
        return "Karaokê/legendas/texto em vídeo provável", "preservar texto e áudio, reduzir vídeo com cuidado", 0.66
    if content_class == "center_text_static_background":
        return "Lyric/karaokê com texto central e fundo simples provável", "manter resolução, reduzir vídeo e preservar áudio", 0.66
    if content_class == "text_overlay_low_motion":
        return "Vídeo com texto/legendas ou fundo estático provável", "preservar áudio e reduzir vídeo com cuidado", 0.63
    if content_class == "compressed_static_or_text":
        return "Vídeo já comprimido com margem para reduzir vídeo", "preservar áudio e reduzir vídeo com cuidado", 0.60
    if content_class == "low_visual_change":
        return "Vídeo com baixa variação visual", "reduzir vídeo com mais força e preservar áudio", 0.64
    if content_class == "high_motion":
        return "Vídeo com muito movimento provável", "evitar bitrate muito baixo e preservar fluidez", 0.62
    if content_class == "high_resolution":
        return "Vídeo em alta resolução", "reduzir escala com cuidado e equilibrar bitrate", 0.60
    confidence = 0.52
    if pixels > 0 and fps:
        confidence += 0.04
    if source_bpp is not None:
        confidence += 0.04
    return "Vídeo comum com movimento moderado", "equilibrar bitrate, resolução e FPS", min(0.62, confidence)


def _suggest_video_resolution(width: int | None, height: int | None, content_class: str) -> tuple[int | None, int | None]:
    if not width or not height:
        return None, None
    if content_class in {"slides_screen", "karaoke_text", "center_text_static_background", "text_overlay_low_motion", "compressed_static_or_text", "low_visual_change", "already_compressed"}:
        return None, None
    if content_class == "high_resolution" and height > 1080:
        return _scale_to_height(width, height, 1080)
    if content_class == "high_motion" and height > 1080:
        return _scale_to_height(width, height, 1080)
    if content_class == "moderate_motion" and height > 1440:
        return _scale_to_height(width, height, 1440)
    return None, None


def _suggest_video_fps(fps: float | None, content_class: str) -> float | None:
    if not fps or fps <= 0:
        return None
    if content_class in {"slides_screen", "karaoke_text", "center_text_static_background", "text_overlay_low_motion", "compressed_static_or_text", "low_visual_change", "already_compressed"}:
        return None
    if fps > 60:
        return 60.0
    if content_class == "moderate_motion" and fps > 30:
        return 30.0
    if content_class == "high_resolution" and fps > 30:
        return 30.0
    return None


def _suggest_video_bitrate(*, source_width: int | None, source_height: int | None, source_fps: float | None, source_video_bitrate: int | None, content_class: str, already_compressed: bool) -> int:
    height = source_height or 720
    fps = source_fps or 30.0
    if content_class == "slides_screen":
        base = _slides_bitrate_for_height(height)
        if source_video_bitrate:
            # Baseline calibrated against real Format Factory output supplied by
            # the owner for aula/slides: 1280x720, ~30 fps, video near 208 kbps
            # and audio near 123 kbps. Keep a small safety margin above that
            # for readability while still allowing 80%+ reductions on static
            # teaching/screen content.
            base = min(base, max(180_000, int(source_video_bitrate * 0.22)))
        return _round_bitrate(base, minimum=180_000, maximum=900_000)
    if content_class == "center_text_static_background":
        # Modo equilibrado para lyric/karaokê com texto central: preservar a
        # resolução original, como no caso de aula/slides, e reduzir apenas o
        # bitrate de vídeo. A redução de resolução fica restrita à variante
        # explícita "alta redução" no popup.
        base = _center_text_static_bitrate_for_height(height)
        if source_video_bitrate:
            base = min(base, max(220_000, int(source_video_bitrate * 0.52)))
        return _round_bitrate(base, minimum=208_000, maximum=650_000)
    if content_class == "karaoke_text":
        base = _karaoke_bitrate_for_height(height)
        if source_video_bitrate and source_video_bitrate <= 350_000:
            # Karaoke já muito comprimido precisa de uma recomendação útil.
            # Manter áudio aceitável e baixar o vídeo para uma faixa de alta
            # redução, assumindo letras/fundo simples e pouca movimentação.
            base = min(base, max(96_000, int(source_video_bitrate * 0.40)))
            return _round_bitrate(base, minimum=80_000, maximum=220_000)
        if height >= 1080:
            # Para karaokê 1080p não tão comprimido, preservar leitura das letras
            # com redução moderada, sem cair no caminho conservador genérico.
            base = min(base, 650_000)
        elif height >= 720:
            base = min(base, 420_000)
        if source_video_bitrate:
            base = min(base, max(260_000, int(source_video_bitrate * 0.72)))
        return _round_bitrate(base, minimum=240_000, maximum=1_200_000)
    if content_class == "text_overlay_low_motion":
        base = _text_overlay_bitrate_for_height(height)
        if source_video_bitrate:
            base = min(base, max(160_000, int(source_video_bitrate * 0.58)))
        return _round_bitrate(base, minimum=144_000, maximum=700_000)
    if content_class == "compressed_static_or_text":
        base = _text_overlay_bitrate_for_height(height)
        if source_video_bitrate:
            base = min(base, max(144_000, int(source_video_bitrate * 0.62)))
        return _round_bitrate(base, minimum=128_000, maximum=650_000)
    if content_class == "low_visual_change":
        base = _low_visual_change_bitrate_for_height(height)
        if source_video_bitrate:
            base = min(base, max(160_000, int(source_video_bitrate * 0.58)))
        return _round_bitrate(base, minimum=144_000, maximum=900_000)
    if already_compressed:
        if source_video_bitrate:
            return _round_bitrate(max(250_000, int(source_video_bitrate * 0.88)), minimum=250_000, maximum=max(250_000, source_video_bitrate))
        return _round_bitrate(_moderate_bitrate_for_height(height), minimum=400_000, maximum=2_000_000)
    if content_class == "high_motion":
        base = _motion_bitrate_for_height(height, fps)
        if source_video_bitrate:
            base = min(base, max(700_000, int(source_video_bitrate * 0.72)))
        return _round_bitrate(base, minimum=700_000, maximum=6_000_000)
    if content_class == "high_resolution":
        base = _moderate_bitrate_for_height(min(height, 1080))
        if source_video_bitrate:
            base = min(base, max(900_000, int(source_video_bitrate * 0.62)))
        return _round_bitrate(base, minimum=900_000, maximum=3_500_000)
    base = _moderate_bitrate_for_height(height)
    if source_video_bitrate:
        base = min(base, max(500_000, int(source_video_bitrate * 0.65)))
    return _round_bitrate(base, minimum=500_000, maximum=3_000_000)


def _suggest_audio_bitrate(source_audio_bitrate: int | None, *, media_kind: str, content_class: str) -> int:
    if media_kind == "audio":
        return 96_000
    if content_class == "slides_screen":
        # Aula/slides geralmente aceita vídeo muito menor, mas o áudio deve
        # permanecer claro. A referência real do Format Factory ficou em
        # ~123 kbps, então o alvo seguro passa a ser 128 kbps.
        target = 128_000
        minimum = 80_000
    elif content_class == "center_text_static_background":
        # Modo equilibrado: preservar melhor o áudio musical/karaokê. A redução
        # agressiva de áudio fica somente na variante explícita de alta redução.
        target = 128_000
        minimum = 96_000
    elif content_class == "karaoke_text":
        target = 160_000 if source_audio_bitrate and source_audio_bitrate >= 160_000 else 128_000
        minimum = 96_000
    elif content_class in {"text_overlay_low_motion", "compressed_static_or_text"}:
        target = 128_000
        minimum = 96_000
    elif content_class == "low_visual_change":
        target = 128_000
        minimum = 96_000
    else:
        target = 128_000
        minimum = 64_000
    if source_audio_bitrate:
        if content_class == "center_text_static_background":
            source_factor = 1.00
        elif content_class in {"karaoke_text", "text_overlay_low_motion", "compressed_static_or_text"}:
            source_factor = 1.00
        elif content_class == "slides_screen":
            source_factor = 0.96
        else:
            source_factor = 0.90
        target = min(target, max(minimum, int(source_audio_bitrate * source_factor)))
    return _round_bitrate(target, minimum=minimum, maximum=192_000)


def _resolve_audio_bitrate(info: Mapping[str, Any], total_bitrate: int | None, duration: float | None, source_size: int | None) -> int | None:
    streams = info.get("audio_streams") or []
    bitrates: list[int] = []
    if isinstance(streams, Sequence) and not isinstance(streams, (str, bytes, bytearray)):
        for stream in streams:
            stream_map = _as_mapping(stream) or {}
            value = _positive_int(_pick(stream_map, "bit_rate", "bitrate", "bitrate_bps", "BPS"))
            if value:
                bitrates.append(value)
    if bitrates:
        return bitrates[0]
    channels = _positive_int(info.get("primary_audio_channels"))
    if channels:
        return 96_000 if channels <= 1 else 128_000
    if total_bitrate and not (info.get("resolution") or (None, None))[0]:
        return total_bitrate
    return None


def _resolve_video_bitrate(total_bitrate: int | None, audio_bitrate: int | None, duration: float | None, source_size: int | None) -> int | None:
    if total_bitrate:
        if audio_bitrate and total_bitrate > audio_bitrate:
            return max(1, total_bitrate - audio_bitrate)
        return total_bitrate
    if source_size and duration and duration > 0:
        estimated_total = int((source_size * 8) / duration)
        if audio_bitrate and estimated_total > audio_bitrate:
            return max(1, estimated_total - audio_bitrate)
        return estimated_total
    return None


def _is_already_compressed(video_bitrate: int | None, width: int | None, height: int | None, fps: float | None, source_bpp: float | None) -> bool:
    if not video_bitrate or not width or not height:
        return False
    if source_bpp is not None and source_bpp < 0.045:
        return True
    if height >= 1080 and video_bitrate < 1_000_000:
        return True
    if height >= 720 and video_bitrate < 600_000:
        return True
    return False


def _is_voice_like_audio(extension: str, bitrate: int | None) -> bool:
    if extension in MUSIC_AUDIO_EXTENSIONS:
        return False
    if extension in LOSSY_AUDIO_EXTENSIONS and bitrate and bitrate <= 112_000:
        return True
    return bool(bitrate and bitrate <= 96_000)


def _bits_per_pixel_frame(video_bitrate: int | None, width: int | None, height: int | None, fps: float | None) -> float | None:
    if not video_bitrate or not width or not height or not fps or fps <= 0:
        return None
    denominator = float(width) * float(height) * float(fps)
    return float(video_bitrate) / denominator if denominator > 0 else None


def _slides_bitrate_for_height(height: int) -> int:
    # Slides/aula/tela gravada costumam ter pouco movimento real. A nova
    # referência é o arquivo gerado no Format Factory informado pelo owner:
    # 1280x720, ~30 fps, vídeo em 208 kbps e áudio em 123 kbps. Aqui usamos
    # uma margem de segurança no vídeo, sem reduzir resolução nem FPS.
    if height >= 1440:
        return 600_000
    if height >= 1080:
        return 384_000
    if height >= 720:
        return 240_000
    if height >= 480:
        return 192_000
    return 160_000


def _center_text_static_bitrate_for_height(height: int) -> int:
    # Modo equilibrado para lyric/karaokê com texto central e fundo simples.
    # Mantém a resolução original, então precisa de mais margem visual do que a
    # variante de alta redução em 360p.
    if height >= 1440:
        return 420_000
    if height >= 1080:
        return 320_000
    if height >= 720:
        return 256_000
    if height >= 480:
        return 192_000
    return 144_000


def _karaoke_bitrate_for_height(height: int) -> int:
    # Karaokê/lyrics também costuma ter baixo movimento, mas precisa proteger
    # contornos de texto e preservar áudio musical melhor que aula/voz.
    if height >= 1440:
        return 1_400_000
    if height >= 1080:
        return 1_000_000
    if height >= 720:
        return 700_000
    if height >= 480:
        return 500_000
    return 380_000


def _text_overlay_bitrate_for_height(height: int) -> int:
    # Text/lyrics/legenda content needs more care than aula/slides, but when
    # region analysis shows localized change or a stable background it can use
    # much less video bitrate than a generic 1080p clip. Audio is preserved in
    # _suggest_audio_bitrate.
    if height >= 1440:
        return 650_000
    if height >= 1080:
        return 320_000
    if height >= 720:
        return 256_000
    if height >= 480:
        return 208_000
    return 160_000


def _low_visual_change_bitrate_for_height(height: int) -> int:
    if height >= 1440:
        return 700_000
    if height >= 1080:
        return 384_000
    if height >= 720:
        return 288_000
    if height >= 480:
        return 224_000
    return 160_000


def _moderate_bitrate_for_height(height: int) -> int:
    if height >= 2160:
        return 4_000_000
    if height >= 1440:
        return 2_800_000
    if height >= 1080:
        return 1_800_000
    if height >= 720:
        return 1_100_000
    if height >= 480:
        return 750_000
    return 450_000


def _motion_bitrate_for_height(height: int, fps: float) -> int:
    base = _moderate_bitrate_for_height(height)
    multiplier = 1.45 if fps >= 50 else 1.25
    return int(base * multiplier)


def _scale_to_height(width: int, height: int, target_height: int) -> tuple[int, int]:
    target_height = min(height, max(2, int(round(target_height / 2) * 2)))
    target_width = max(2, int(round((width * (target_height / height)) / 2) * 2))
    return target_width, target_height


def _analyze_light_frame_staticity(*, source_path: str, duration_seconds: float | None, width: int | None, height: int | None) -> dict[str, Any] | None:
    """Sample a few frames and estimate visual variation.

    This is intentionally best-effort: if FFmpeg/Pillow is missing, the file is
    inaccessible, or extraction is slow, the automatic profile falls back to the
    metadata-only path. The goal is to detect static/near-static content without
    decoding the whole video.
    """
    path = Path(str(source_path or "")).expanduser()
    if not source_path or not path.exists() or not path.is_file():
        return None
    if not width or not height:
        return None
    # The frame sampler is optional and exists only to refine classification.
    # In PyInstaller/windowed builds, a native failure inside bundled image/codec
    # libraries can terminate the whole process before Python can catch it. Keep
    # installed builds on the stable metadata-only path unless explicitly enabled
    # for diagnostics.
    sampling_flag = resolve_legacy_environment("COMPACTME_ENABLE_FROZEN_FRAME_SAMPLING")
    if getattr(sys, "frozen", False) and str(sampling_flag or "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return None

    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return None
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageStat
    except Exception:
        return None

    duration = float(duration_seconds or 0.0)
    if duration <= 0:
        sample_times = [0.5, 3.0, 7.0, 12.0]
    else:
        # Avoid the first/last seconds because splash screens and fade-outs often
        # distort the staticity signal. Six samples keep the operation light.
        fractions = (0.08, 0.22, 0.38, 0.54, 0.70, 0.86) if duration >= 60 else (0.15, 0.35, 0.55, 0.75)
        sample_times = [max(0.35, min(duration - 0.35, duration * f)) for f in fractions if duration * f > 0]
    if len(sample_times) < 2:
        return None

    frames = []
    with tempfile.TemporaryDirectory(prefix=FRAME_SAMPLING_TEMP_PREFIX) as tmpdir:
        tmp = Path(tmpdir)
        for idx, timestamp in enumerate(sample_times, start=1):
            output = tmp / f"frame_{idx:02d}.jpg"
            cmd = [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=160:-1:flags=fast_bilinear",
                "-q:v",
                "8",
                "-y",
                str(output),
            ]
            try:
                run_no_window(cmd, stdout=-1, stderr=-1, timeout=1.8)
            except Exception:
                continue
            if not output.exists() or output.stat().st_size <= 0:
                continue
            try:
                with Image.open(output) as img:
                    frames.append(img.convert("L").resize((160, 90)))
            except Exception:
                continue

    if len(frames) < 3:
        return None

    deltas: list[float] = []
    upper_deltas: list[float] = []
    center_deltas: list[float] = []
    lower_deltas: list[float] = []
    for previous, current in zip(frames, frames[1:]):
        try:
            diff = ImageChops.difference(previous, current)
            mean = float(ImageStat.Stat(diff).mean[0]) / 255.0
            deltas.append(mean)
            width_px, height_px = previous.size
            upper_box = (0, 0, width_px, int(height_px * 0.68))
            center_box = (0, int(height_px * 0.18), width_px, int(height_px * 0.82))
            lower_box = (0, int(height_px * 0.58), width_px, height_px)
            upper_diff = ImageChops.difference(previous.crop(upper_box), current.crop(upper_box))
            center_diff = ImageChops.difference(previous.crop(center_box), current.crop(center_box))
            lower_diff = ImageChops.difference(previous.crop(lower_box), current.crop(lower_box))
            upper_deltas.append(float(ImageStat.Stat(upper_diff).mean[0]) / 255.0)
            center_deltas.append(float(ImageStat.Stat(center_diff).mean[0]) / 255.0)
            lower_deltas.append(float(ImageStat.Stat(lower_diff).mean[0]) / 255.0)
        except Exception:
            continue
    if not deltas:
        return None

    edge_bottom_values: list[float] = []
    edge_center_values: list[float] = []
    edge_upper_values: list[float] = []
    for frame in frames:
        try:
            width_px, height_px = frame.size
            upper_box = (0, 0, width_px, int(height_px * 0.68))
            center_box = (0, int(height_px * 0.18), width_px, int(height_px * 0.82))
            lower_box = (0, int(height_px * 0.58), width_px, height_px)
            upper_edges = frame.crop(upper_box).filter(ImageFilter.FIND_EDGES)
            center_edges = frame.crop(center_box).filter(ImageFilter.FIND_EDGES)
            lower_edges = frame.crop(lower_box).filter(ImageFilter.FIND_EDGES)
            edge_upper_values.append(float(ImageStat.Stat(upper_edges).mean[0]) / 255.0)
            edge_center_values.append(float(ImageStat.Stat(center_edges).mean[0]) / 255.0)
            edge_bottom_values.append(float(ImageStat.Stat(lower_edges).mean[0]) / 255.0)
        except Exception:
            continue

    mean_delta = float(statistics.fmean(deltas))
    median_delta = float(statistics.median(deltas))
    median_upper_delta = float(statistics.median(upper_deltas)) if upper_deltas else median_delta
    median_center_delta = float(statistics.median(center_deltas)) if center_deltas else median_delta
    median_lower_delta = float(statistics.median(lower_deltas)) if lower_deltas else median_delta
    lower_motion_ratio = median_lower_delta / max(0.001, median_upper_delta)
    center_motion_ratio = median_center_delta / max(0.001, median_delta)
    lower_edge_density = float(statistics.fmean(edge_bottom_values)) if edge_bottom_values else 0.0
    center_edge_density = float(statistics.fmean(edge_center_values)) if edge_center_values else 0.0
    upper_edge_density = float(statistics.fmean(edge_upper_values)) if edge_upper_values else 0.0
    lower_edge_ratio = lower_edge_density / max(0.001, upper_edge_density)
    center_edge_ratio = center_edge_density / max(0.001, lower_edge_density)
    static_ratio = sum(1 for delta in deltas if delta <= 0.018) / float(len(deltas))

    text_overlay_score = 0
    if median_upper_delta <= 0.060 and median_lower_delta >= median_upper_delta * 1.08:
        text_overlay_score += 1
    if lower_motion_ratio >= 1.18 and median_lower_delta >= 0.025:
        text_overlay_score += 1
    if lower_edge_density >= 0.030 and lower_edge_ratio >= 1.04:
        text_overlay_score += 1
    if upper_edge_density <= 0.060 and lower_edge_density >= 0.028:
        text_overlay_score += 1

    center_text_score = 0
    if 0.025 <= median_delta <= 0.070:
        center_text_score += 1
    if center_edge_density >= 0.060:
        center_text_score += 1
    if center_edge_density >= lower_edge_density * 0.92 and upper_edge_density >= 0.055:
        center_text_score += 1
    if median_center_delta <= 0.075 and median_lower_delta <= 0.050:
        center_text_score += 1

    if static_ratio >= 0.75 and median_delta <= 0.022:
        label = "muito baixa"
    elif text_overlay_score >= 2 or center_text_score >= 3:
        label = "moderada localizada/texto provável"
    elif static_ratio >= 0.45 or median_delta <= 0.040:
        label = "baixa"
    elif median_delta >= 0.095:
        label = "alta"
    else:
        label = "moderada"
    return {
        "mean_delta": round(mean_delta, 5),
        "median_delta": round(median_delta, 5),
        "static_ratio": round(static_ratio, 3),
        "sampled_frames": len(frames),
        "label": label,
        "median_upper_delta": round(median_upper_delta, 5),
        "median_center_delta": round(median_center_delta, 5),
        "median_lower_delta": round(median_lower_delta, 5),
        "lower_motion_ratio": round(lower_motion_ratio, 3),
        "center_motion_ratio": round(center_motion_ratio, 3),
        "lower_edge_density": round(lower_edge_density, 5),
        "center_edge_density": round(center_edge_density, 5),
        "upper_edge_density": round(upper_edge_density, 5),
        "lower_edge_ratio": round(lower_edge_ratio, 3),
        "center_edge_ratio": round(center_edge_ratio, 3),
        "text_overlay_score": text_overlay_score,
        "center_text_score": center_text_score,
    }


def _classify_frame_metrics(frame_metrics: Mapping[str, Any] | None) -> str | None:
    if not frame_metrics:
        return None
    try:
        median_delta = float(frame_metrics.get("median_delta") or 0.0)
        static_ratio = float(frame_metrics.get("static_ratio") or 0.0)
    except Exception:
        return None
    if static_ratio >= 0.75 and median_delta <= 0.022:
        return "very_static"
    if _frame_metrics_suggest_center_text_static(frame_metrics):
        return "text_overlay"
    if _frame_metrics_suggest_text_overlay(frame_metrics):
        return "text_overlay"
    if static_ratio >= 0.45 or median_delta <= 0.040:
        return "low_motion"
    if median_delta >= 0.095:
        return "high_motion"
    return "moderate"


def _frame_metrics_suggest_center_text_static(frame_metrics: Mapping[str, Any] | None) -> bool:
    if not frame_metrics:
        return False
    try:
        center_score = int(frame_metrics.get("center_text_score") or 0)
        median_delta = float(frame_metrics.get("median_delta") or 0.0)
        center_delta = float(frame_metrics.get("median_center_delta") or median_delta)
        lower_delta = float(frame_metrics.get("median_lower_delta") or median_delta)
        center_edge_density = float(frame_metrics.get("center_edge_density") or 0.0)
        lower_edge_density = float(frame_metrics.get("lower_edge_density") or 0.0)
        upper_edge_density = float(frame_metrics.get("upper_edge_density") or 0.0)
    except Exception:
        return False
    if center_score >= 3:
        return True
    # Centered lyric videos often have moderate global variation because the
    # words change, while the background remains simple. Require high edge
    # density in the center and no strong lower-subtitle signature.
    if 0.028 <= median_delta <= 0.065 and center_edge_density >= 0.060 and lower_delta <= 0.055:
        return True
    if center_delta <= 0.075 and center_edge_density >= 0.070 and upper_edge_density >= lower_edge_density * 0.95:
        return True
    return False


def _frame_metrics_suggest_text_overlay(frame_metrics: Mapping[str, Any] | None) -> bool:
    if not frame_metrics:
        return False
    try:
        text_score = int(frame_metrics.get("text_overlay_score") or 0)
        median_delta = float(frame_metrics.get("median_delta") or 0.0)
        upper_delta = float(frame_metrics.get("median_upper_delta") or median_delta)
        lower_delta = float(frame_metrics.get("median_lower_delta") or median_delta)
        lower_motion_ratio = float(frame_metrics.get("lower_motion_ratio") or 0.0)
        lower_edge_density = float(frame_metrics.get("lower_edge_density") or 0.0)
        lower_edge_ratio = float(frame_metrics.get("lower_edge_ratio") or 0.0)
    except Exception:
        return False
    if text_score >= 2:
        return True
    if 0.035 <= median_delta <= 0.090 and upper_delta <= 0.060 and lower_motion_ratio >= 1.20:
        return True
    if lower_delta >= 0.030 and upper_delta <= 0.055 and lower_edge_density >= 0.030 and lower_edge_ratio >= 1.05:
        return True
    return False


def _frame_metrics_suggest_static_background(frame_metrics: Mapping[str, Any] | None) -> bool:
    if not frame_metrics:
        return False
    try:
        median_delta = float(frame_metrics.get("median_delta") or 0.0)
        upper_delta = float(frame_metrics.get("median_upper_delta") or median_delta)
        lower_motion_ratio = float(frame_metrics.get("lower_motion_ratio") or 0.0)
        static_ratio = float(frame_metrics.get("static_ratio") or 0.0)
    except Exception:
        return False
    if _frame_metrics_suggest_text_overlay(frame_metrics):
        return True
    return bool((median_delta <= 0.075 and upper_delta <= 0.055) or (static_ratio >= 0.25 and lower_motion_ratio >= 1.12))


def _estimate_output_size(duration: float | None, video_bitrate: int | None, audio_bitrate: int | None) -> int | None:
    if not duration or duration <= 0:
        return None
    total = int(video_bitrate or 0) + int(audio_bitrate or 0)
    if total <= 0:
        return None
    container_overhead = 1.03
    return max(1, int((total * duration / 8) * container_overhead))


def _cap_estimate_for_safe_gain(estimated: int | None, source_size: int | None, *, already_compressed: bool) -> int | None:
    if estimated is None or not source_size or source_size <= 0:
        return estimated
    max_ratio = 0.96 if already_compressed else 0.88
    return min(estimated, max(1, int(source_size * max_ratio)))


def _round_bitrate(value: int | float, *, minimum: int, maximum: int) -> int:
    clamped = max(minimum, min(maximum, int(round(float(value)))))
    step = 50_000 if clamped >= 500_000 else 16_000
    return max(minimum, min(maximum, int(round(clamped / step) * step)))


def _kbps(value: int | None) -> int | None:
    return int(round(value / 1000)) if value else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(float(value)) if value not in (None, "", "?") else None
    except Exception:
        return None
    return parsed if parsed and parsed > 0 else None


def _duration_seconds(value: Any) -> float | None:
    if value in (None, "", "?"):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = str(value).strip().replace(",", ".")
    try:
        parsed = float(text)
        return parsed if parsed > 0 else None
    except Exception:
        pass
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except Exception:
        return None
    return None


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "__dict__"):
        return vars(value)
    return None


def _pick(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            value = source[key]
            if value not in (None, ""):
                return value
    return None


def _sequence_get(values: Sequence[Any], index: int) -> Any:
    return values[index] if index < len(values) else None
