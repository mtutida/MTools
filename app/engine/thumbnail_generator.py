import logging
import os
import subprocess
from app.core.subprocess_utils import run_no_window

from PIL import Image, ImageDraw, ImageFont

from app.core.ffmpeg_binaries import resolve_ffmpeg

_AUDIO_EXTENSIONS = (".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus", ".alac")
_LOGGER = logging.getLogger("app.engine.thumbnail_generator")


def _create_audio_placeholder(out_path, input_path=None):
    try:
        image = Image.new("RGB", (320, 180), (240, 245, 250))
        draw = ImageDraw.Draw(image)

        bg_fill = (240, 245, 250)
        border = (210, 220, 230)
        icon_fill = (103, 126, 158)

        draw.rounded_rectangle((0, 0, 319, 179), radius=18, fill=bg_fill, outline=border, width=2)

        # Use a ready Unicode music-note glyph from a system font instead of hand-drawing shapes.
        note_char = "♪"
        font_candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/seguisym.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
        font = None
        for candidate in font_candidates:
            if os.path.exists(candidate):
                try:
                    font = ImageFont.truetype(candidate, 118)
                    break
                except Exception:
                    _LOGGER.debug("Audio placeholder font candidate failed: %s", candidate, exc_info=True)
                    continue
        if font is None:
            try:
                font = ImageFont.load_default()
            except Exception:
                _LOGGER.debug("Audio placeholder default font failed", exc_info=True)
                font = None

        if font is not None:
            bbox = draw.textbbox((0, 0), note_char, font=font)
            glyph_w = bbox[2] - bbox[0]
            glyph_h = bbox[3] - bbox[1]
            x = int((320 - glyph_w) / 2 - bbox[0])
            y = int((180 - glyph_h) / 2 - bbox[1])
            draw.text((x, y), note_char, font=font, fill=icon_fill)
        else:
            draw.text((140, 44), note_char, fill=icon_fill)

        image.save(out_path, format="JPEG", quality=92)
        return out_path if os.path.exists(out_path) else None
    except Exception:
        _LOGGER.debug("Audio placeholder creation failed", exc_info=True)
        return None


def _generate_audio_thumbnail(input_path, out_path, ffmpeg):
    try:
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception:
            _LOGGER.debug("Could not remove previous audio thumbnail: %s", out_path, exc_info=True)
        cmd = [
            ffmpeg,
            "-y",
            "-i", input_path,
            "-an",
            "-vf", "scale=320:-1",
            "-frames:v", "1",
            out_path,
        ]
        run_no_window(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if os.path.exists(out_path):
            return out_path
    except Exception:
        _LOGGER.debug("Audio thumbnail generation failed; using placeholder", exc_info=True)
    return _create_audio_placeholder(out_path, input_path)


def generate_thumbnail(video_path, out_path):
    try:
        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            return None

        ext = os.path.splitext(str(video_path or ""))[1].lower()
        if ext in _AUDIO_EXTENSIONS:
            return _generate_audio_thumbnail(video_path, out_path, ffmpeg)

        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception:
            _LOGGER.debug("Could not remove previous video thumbnail: %s", out_path, exc_info=True)
        cmd = [
            ffmpeg,
            "-y",
            "-i", video_path,
            "-vf", "thumbnail=200,eq=brightness=0.05:saturation=1.15,scale=320:-1",
            "-frames:v", "1",
            out_path,
        ]
        run_no_window(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return out_path if os.path.exists(out_path) else None
    except Exception:
        _LOGGER.debug("Video thumbnail generation failed", exc_info=True)
        return None
