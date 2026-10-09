import os
import re
import subprocess
from collections import deque

from app.core.ffmpeg_binaries import resolve_ffmpeg, resolve_ffprobe
from app.core.subprocess_utils import popen_no_window, run_no_window
from app.core.compression_profiles import build_ffmpeg_profile_options


class CompressionProcessError(Exception):
    def __init__(self, user_message, technical_details=None, failure_kind="processing_failed"):
        super().__init__(user_message)
        self.user_message = user_message
        self.technical_details = technical_details
        self.failure_kind = failure_kind


class CompressionCancelledError(Exception):
    pass


class FFmpegCompressionEngine:
    """FFmpeg compression engine with real-time progress parsing."""

    TIME_REGEX = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    _BANNER_PREFIXES = (
        "ffmpeg version",
        "built with ",
        "configuration:",
        "libavutil",
        "libavcodec",
        "libavformat",
        "libavdevice",
        "libavfilter",
        "libswscale",
        "libswresample",
        "libpostproc",
    )

    def _get_duration_seconds(self, input_path):
        ffprobe = resolve_ffprobe()
        if not ffprobe:
            return None

        cmd = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            input_path,
        ]

        try:
            result = run_no_window(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return float(result.stdout.strip())
        except Exception:
            return None

    def _parse_time_to_seconds(self, line):
        match = self.TIME_REGEX.search(line)
        if not match:
            return None

        hours = int(match.group(1))
        minutes = int(match.group(2))
        seconds = float(match.group(3))
        return hours * 3600 + minutes * 60 + seconds

    def _prepare_contract_metadata(self, job, input_path, duration):
        """Populate normalized source metadata before profile option resolution.

        The Quick profile encoder contract depends on source size and duration
        to derive the real CBR budget. Some UI paths already have these fields,
        but execution jobs can arrive with only the input path populated. In that
        case the estimate panel can show the contracted size while the encoder
        silently falls back to CRF because the budget cannot be resolved.
        """
        if job is None:
            return
        try:
            size_bytes = os.path.getsize(input_path)
        except Exception:
            size_bytes = None

        if size_bytes and size_bytes > 0:
            for attr in ("input_size_bytes", "size_bytes"):
                try:
                    current = getattr(job, attr, None)
                    if current in (None, 0, "", "0"):
                        setattr(job, attr, int(size_bytes))
                except Exception:
                    pass

        if duration and duration > 0:
            for attr in ("duration", "input_duration", "duration_seconds"):
                try:
                    current = getattr(job, attr, None)
                    if current in (None, 0, "", "0"):
                        setattr(job, attr, float(duration))
                except Exception:
                    pass
            if size_bytes and size_bytes > 0:
                try:
                    current_bitrate = getattr(job, "input_bitrate", None)
                    if current_bitrate in (None, 0, "", "0"):
                        setattr(job, "input_bitrate", int((int(size_bytes) * 8) / float(duration)))
                except Exception:
                    pass

        try:
            if not getattr(job, "source_path", None):
                setattr(job, "source_path", input_path)
        except Exception:
            pass

    def _build_command(self, ffmpeg, input_path, output_path, job=None, duration=None):
        ext = os.path.splitext(output_path)[1].lower()

        self._prepare_contract_metadata(job, input_path, duration)

        cmd = [ffmpeg, "-y", "-i", input_path]
        profile_options = build_ffmpeg_profile_options(job, ext) if job is not None else []
        if profile_options:
            cmd += profile_options
        elif ext == ".webm":
            cmd += [
                "-c:v",
                "libvpx-vp9",
                "-crf",
                "32",
                "-b:v",
                "0",
                "-c:a",
                "libopus",
                "-b:a",
                "96k",
            ]
        else:
            cmd += [
                "-c:v",
                "libx264",
                "-crf",
                "28",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
            ]
            if ext == ".mp4":
                cmd += ["-movflags", "+faststart"]

        cmd.append(output_path)
        return cmd

    def _is_banner_line(self, line):
        stripped = line.strip()
        if not stripped:
            return True

        lowered = stripped.lower()
        if lowered.startswith(self._BANNER_PREFIXES):
            return True
        if re.match(r"^lib[a-z0-9]+\s+\d", stripped):
            return True
        return False

    def _extract_useful_stderr_lines(self, stderr_tail):
        cleaned = [line.strip() for line in stderr_tail if line and line.strip()]
        non_banner = [line for line in cleaned if not self._is_banner_line(line)]
        useful = [
            line for line in non_banner if "error" in line.lower() or "invalid" in line.lower()
        ]
        if useful:
            return useful[-6:]
        return non_banner[-6:]

    def _compact_failure_reason(self, useful_lines):
        for line in reversed(useful_lines or []):
            reason = str(line or "").strip()
            if not reason:
                continue
            if reason.lower() in {"conversion failed!", "conversion failed"}:
                continue
            reason = re.sub(r"^\[[^\]]+\]\s*", "", reason).strip()
            reason = re.sub(r"\s+", " ", reason).strip()
            if len(reason) > 220:
                reason = reason[:219].rstrip() + "…"
            return reason
        return None

    def _classify_failure(self, useful_lines):
        text = "\n".join(useful_lines).lower()
        compact_reason = self._compact_failure_reason(useful_lines)
        if "no space left on device" in text:
            return "disk_full", "Espaço em disco insuficiente para gerar o arquivo"
        if "error opening input" in text or "no such file or directory" in text:
            return "input_not_found", "Arquivo de entrada não encontrado"
        if "permission denied" in text:
            return "permission_denied", "Sem permissão para acessar o arquivo ou a pasta de saída"
        if "moov atom not found" in text:
            return "invalid_media_data", "Arquivo MP4 incompleto ou corrompido"
        if "invalid data found" in text:
            return "invalid_media_data", "Arquivo inválido, incompleto ou corrompido"
        if "unknown encoder" in text or "encoder not found" in text:
            return "encoder_unavailable", "Encoder necessário não está disponível no FFmpeg"
        if "height not divisible by 2" in text or "width not divisible by 2" in text or "not divisible by 2" in text:
            return "invalid_dimensions", "Resolução gerada incompatível com o encoder"
        if "error opening output" in text or "could not write header" in text:
            return "output_write_failed", "Não foi possível criar o arquivo de saída"
        if compact_reason:
            return "processing_failed", f"Falha ao processar o arquivo: {compact_reason}"
        return "processing_failed", "Falha ao processar o arquivo"

    def _normalize_return_codes(self, return_code):
        raw_code = int(return_code)
        signed_code = raw_code
        if raw_code > 0x7FFFFFFF:
            signed_code = raw_code - 0x100000000
        return raw_code, signed_code

    def _build_technical_details(self, input_path, output_path, return_code, useful_lines):
        raw_code, signed_code = self._normalize_return_codes(return_code)
        detail_lines = [
            f"input={input_path}",
            f"output={output_path}",
            f"exit_code_raw={raw_code}",
            f"exit_code_signed={signed_code}",
        ]
        if useful_lines:
            for index, line in enumerate(useful_lines, start=1):
                detail_lines.append(f"stderr_summary_{index}={line}")
        return "\n".join(detail_lines)

    def process(self, job, cancel_token=None):
        input_path = job.source_path
        output_path = job.output_path

        if not input_path:
            raise Exception("Job sem source_path")

        if not output_path:
            raise Exception("Job sem output_path")

        duration = self._get_duration_seconds(input_path)

        ffmpeg = resolve_ffmpeg()
        if not ffmpeg:
            raise Exception("FFmpeg não encontrado")

        cmd = self._build_command(ffmpeg, input_path, output_path, job=job, duration=duration)

        process = popen_no_window(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        last_progress = 0
        stderr_tail = deque(maxlen=80)

        for line in process.stderr:
            stderr_tail.append(line.rstrip())

            if cancel_token and cancel_token.is_cancelled():
                process.kill()
                process.wait()
                raise CompressionCancelledError()

            seconds = self._parse_time_to_seconds(line)
            if seconds is not None and duration:
                progress = int((seconds / duration) * 100)
                progress = max(0, min(progress, 100))

                if progress != last_progress:
                    if hasattr(job, "set_progress"):
                        job.set_progress(progress)
                    last_progress = progress

        process.wait()

        if process.returncode != 0:
            useful_lines = self._extract_useful_stderr_lines(stderr_tail)
            failure_kind, user_message = self._classify_failure(useful_lines)
            technical_details = self._build_technical_details(
                input_path=input_path,
                output_path=output_path,
                return_code=process.returncode,
                useful_lines=useful_lines,
            )
            raise CompressionProcessError(
                user_message,
                technical_details=technical_details,
                failure_kind=failure_kind,
            )

        if hasattr(job, "set_progress"):
            job.set_progress(100)
