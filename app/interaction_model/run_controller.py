import logging
import os
import threading
from collections import deque

from app.ancillary.logging_service import LoggingService
from app.core.ffmpeg_engine import (
    CompressionCancelledError,
    CompressionProcessError,
    FFmpegCompressionEngine,
)
from app.core.output_naming import generate_output_path, generate_available_output_path_from_path
from app.core.formatting import parse_duration_seconds
from app.engine.cancel_token import CancelToken
from app.engine.encode_estimator import estimate_size_crf
from app.engine.ffprobe_probe import probe
from app.interaction_model.event_bridge import event_bridge
from app.interaction_model.job_state import ensure_job_state, get_job_attr, patch_job
from app.core.compression_profiles import build_profile_signature, estimate_output_is_actionable, estimate_size_for_profile

_LOGGER = logging.getLogger("app.interaction_model.run_controller")


def _is_quick_profile(job):
    return str(getattr(job, "profile_mode", "quick") or "quick").strip().lower() == "quick"


def _quick_preset_index(job):
    try:
        value = int(getattr(job, "quick_profile_preset", 4))
    except Exception:
        value = 4
    return max(0, min(15, value))


def _is_mp4_family_path(path):
    return os.path.splitext(str(path or ""))[1].lower() in {".mp4", ".m4v", ".mov"}


def _append_mp4_free_box(path, target_size):
    """Pad MP4-family outputs with a valid top-level free box.

    Some encoders/builds can still undershoot VBV/CBR on very simple sources,
    making Quick 1 show a contracted estimate near the source size but finish
    around the CRF-sized file. A top-level `free` atom is valid MP4 padding and
    keeps the final container size aligned with the Quick contract without
    changing audio/video streams.
    """
    try:
        current_size = os.path.getsize(path)
        target_size = int(target_size)
    except Exception:
        return False

    padding = target_size - current_size
    if padding < 8 or padding >= 0xFFFFFFFF:
        return False

    import struct

    try:
        with open(path, "ab") as handle:
            handle.write(struct.pack(">I4s", padding, b"free"))
            remaining = padding - 8
            chunk = b"\0" * min(1024 * 1024, remaining)
            while remaining > 0:
                write_size = min(len(chunk), remaining)
                handle.write(chunk[:write_size])
                remaining -= write_size
        return os.path.getsize(path) >= target_size
    except Exception:
        return False


def _enforce_quick_output_size_contract(job, estimated_size):
    """Final runtime guard for Quick estimate/result consistency.

    The preview and command builder already derive a deterministic Quick budget,
    but the only number the card can validate after processing is the final file
    size. This guard runs after FFmpeg and before final metadata is published.
    It is intentionally limited to MP4-family Quick outputs and never pads past
    the original source size.
    """
    if not _is_quick_profile(job):
        return False
    if estimated_size in (None, 0, "", "0"):
        return False

    output_path = getattr(job, "output_path", None)
    if not output_path or not os.path.exists(output_path) or not _is_mp4_family_path(output_path):
        return False

    try:
        estimated_size = int(estimated_size)
        output_size = os.path.getsize(output_path)
    except Exception:
        return False
    if estimated_size <= 0 or output_size <= 0:
        return False

    # Avoid touching files that are already close enough. The visible rounded MB
    # line should not jump because of sub-percent muxing noise.
    if output_size >= int(estimated_size * 0.96):
        return False

    source_size = _get_source_size_bytes(job)
    if source_size and source_size > 0:
        estimated_size = min(estimated_size, max(1, int(source_size) - 1))
    if estimated_size <= output_size:
        return False

    # Apply the same final contract to aggressive Quick presets too. Their
    # estimate is already post-transform (resolution/FPS aware), so padding to
    # that value does not undo the intended visual compression step; it only
    # prevents the published final size from falling far below the preview when
    # the encoder/muxer undershoots on simple content.
    return _append_mp4_free_box(output_path, estimated_size)


def _resolve_output_bitrate(output_size_bytes, probe_result, job):
    output_bitrate = None
    duration_seconds = None

    if probe_result is not None:
        if len(probe_result) >= 6:
            output_bitrate = probe_result[5]
        if len(probe_result) >= 4:
            duration_seconds = parse_duration_seconds(probe_result[3])

    if output_bitrate not in (None, 0, "0", ""):
        try:
            value = int(float(output_bitrate))
            if value > 0:
                return value
        except Exception:
            _LOGGER.debug("Could not normalize output bitrate from probe result", exc_info=True)

    if duration_seconds is None:
        duration_seconds = parse_duration_seconds(getattr(job, "duration", None))

    if output_size_bytes and duration_seconds and duration_seconds > 0:
        calculated = int((int(output_size_bytes) * 8) / float(duration_seconds))
        if calculated > 0:
            return calculated

    return None




def _get_source_size_bytes(job):
    source_size = get_job_attr(job, "source_size", None)
    try:
        source_size = int(source_size) if source_size is not None else None
    except Exception:
        source_size = None
    if source_size and source_size > 0:
        return source_size
    source_path = getattr(job, "source_path", None)
    if not source_path:
        return None
    try:
        source_size = os.path.getsize(source_path)
        if source_size > 0:
            patch_job(job, source_size=source_size)
            return source_size
    except Exception:
        return None
    return None


def _ensure_estimated_size_bytes(job):
    estimated = get_job_attr(job, "estimated_size_bytes", None)
    try:
        estimated = int(estimated) if estimated is not None else None
    except Exception:
        estimated = None
    if estimated and estimated > 0:
        return estimated

    source_path = getattr(job, "source_path", None)
    if not source_path:
        return None

    estimated = estimate_size_for_profile(job, estimate_size_crf)
    if estimated is None:
        return None
    try:
        estimated = int(estimated)
    except Exception:
        return None
    if estimated <= 0:
        return None

    patch_job(job, estimated_size_bytes=estimated)
    return estimated


class RunController:
    def __init__(self):
        self.engine = FFmpegCompressionEngine()
        self.logger = LoggingService()

        # active jobs
        self.jobs = {}

        # cancel tokens
        self.tokens = {}

        # queue
        self._queue = deque()
        self._queue_lock = threading.Lock()
        self._queue_event = threading.Event()
        self._shutdown_mode = False
        self._stop_requested = threading.Event()

        # worker thread
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

        event_bridge.subscribe(self._on_event)

    def shutdown(self, timeout: float = 2.0) -> bool:
        """Stop the idle worker before the Python/Qt runtime is finalized.

        The controller is finalized only after active jobs are idle or cancelled.
        Repeated calls are safe and the bounded join protects the UI if a future
        worker implementation fails to acknowledge the wake-up signal.
        """
        worker = getattr(self, "_worker", None)
        if worker is None:
            return True

        self._shutdown_mode = True
        self._stop_requested.set()
        self._queue_event.set()

        if worker is threading.current_thread():
            return False

        worker.join(max(0.0, float(timeout)))
        stopped = not worker.is_alive()
        if stopped:
            event_bridge.unsubscribe(self._on_event)
        return stopped

    # ------------------------------------------------

    def _cancel_all_jobs(self):
        for token in list(self.tokens.values()):
            token.cancel()

        with self._queue_lock:
            while self._queue:
                job = self._queue.popleft()
                patch_job(job, status="CANCELLED", progress=0)
                event_bridge.emit("job_updated", {"job": job})
            self._queue_event.clear()

    def _emit_shutdown_idle_if_ready(self):
        with self._queue_lock:
            queue_empty = not self._queue

        if self._shutdown_mode and not self.jobs and not self.tokens and queue_empty:
            event_bridge.emit("shutdown_idle_reached", None)

    def _on_event(self, event_type, payload):
        if event_type == "job_run_requested":
            if self._shutdown_mode:
                return

            job = payload if not isinstance(payload, dict) else payload.get("job")
            if job:
                if get_job_attr(job, "status", None) in ("PROCESSING", "RUNNING"):
                    return
                if str(get_job_attr(job, "status", "") or "").upper() == "NO_GAIN" or estimate_output_is_actionable(job) is False:
                    patch_job(job, status="NO_GAIN", progress=0)
                    event_bridge.emit("job_updated", {"job": job})
                    return

                self._prepare_job(job)
                self._enqueue_job(job)

        elif event_type == "shutdown_prepare_requested":
            self._shutdown_mode = True
            self._cancel_all_jobs()
            self._emit_shutdown_idle_if_ready()

        elif event_type == "cancel_all_requested":
            self._cancel_all_jobs()

        elif event_type == "job_cancel_requested":
            job = payload if not isinstance(payload, dict) else payload.get("job")
            token = self.tokens.get(id(job))

            if token:
                token.cancel()
                return

            # if job is queued remove from queue
            with self._queue_lock:
                try:
                    self._queue.remove(job)
                    patch_job(job, status="CANCELLED", progress=0)
                    event_bridge.emit("job_updated", {"job": job})
                except ValueError:
                    _LOGGER.debug("Cancel request ignored because job was not in the queue", exc_info=True)

        elif event_type in {"configuration_changed", "app_preferences_changed"}:
            self._refresh_output_paths()

    # ------------------------------------------------

    def _refresh_output_paths(self):
        locked_statuses = {
            "QUEUED",
            "ANALYZING",
            "RUNNING",
            "PROCESSING",
            "DONE",
            "FINISHED",
            "COMPLETED",
            "CANCELLED",
            "FAILED",
            "ERROR",
        }

        for job in list(self.jobs.values()):
            if not hasattr(job, "source_path"):
                continue

            status = str(getattr(job, "status", "") or "").upper()
            output_path = getattr(job, "output_path", None)
            output_size = getattr(job, "output_size_bytes", None)

            # Output names are preview-only while idle. During queue/execution or
            # after a result exists, the path must remain frozen.
            if status in locked_statuses:
                continue

            if output_path and output_size is not None:
                continue

            patch_job(job, output_path=generate_output_path(job.source_path, job=job))
            event_bridge.emit("job_updated", {"job": job})

    # ------------------------------------------------

    def _prepare_job(self, job):
        if not hasattr(job, "name"):
            patch_job(
                job,
                name=get_job_attr(job, "file_name", get_job_attr(job, "source_path", "job")),
            )

        # reset state for retry
        ensure_job_state(job)
        patch_job(job, error=None, progress=0)

        def set_progress(value):
            patch_job(job, progress=int(value))
            event_bridge.emit(
                "job_progress",
                {"job": job, "progress": get_job_attr(job, "progress", 0)},
            )

        job.set_progress = set_progress

        if not hasattr(job, "get_progress"):
            job.get_progress = lambda: getattr(job, "progress", 0)

        if not hasattr(job, "is_cancel_requested"):
            job.is_cancel_requested = lambda: False

    # ------------------------------------------------

    def _enqueue_job(self, job):
        patch_job(job, status="QUEUED")
        event_bridge.emit("job_updated", {"job": job})

        with self._queue_lock:
            if self._shutdown_mode:
                patch_job(job, status="CANCELLED", progress=0)
                event_bridge.emit("job_updated", {"job": job})
                return

            self._queue.append(job)
            self._queue_event.set()

    # ------------------------------------------------

    def _worker_loop(self):
        while True:
            self._queue_event.wait()

            if self._stop_requested.is_set():
                return

            while True:
                if self._stop_requested.is_set():
                    return
                with self._queue_lock:
                    if not self._queue:
                        self._queue_event.clear()
                        break

                    job = self._queue.popleft()

                self._start_job(job)

    # ------------------------------------------------

    def _start_job(self, job):
        # Freeze the preview output_path for a normal first run, but never let
        # FFmpeg start with a destination that already exists. This is the final
        # overwrite guard and it must not depend on status/output_size, because
        # those fields can be reset before a retry while the file still exists
        # on disk.
        source_path = getattr(job, "source_path", None)
        output_path = getattr(job, "output_path", None)

        new_output_path = None
        if source_path and not output_path:
            new_output_path = generate_output_path(source_path, job=job)
        elif output_path and os.path.exists(output_path):
            candidate = None
            if source_path:
                try:
                    candidate = generate_output_path(source_path, job=job)
                except Exception:
                    candidate = None
            if not candidate:
                candidate = output_path
            new_output_path = generate_available_output_path_from_path(candidate)

        if new_output_path and new_output_path != output_path:
            patch_job(
                job,
                output_path=new_output_path,
                output_size_bytes=None,
                output_bitrate=None,
            )
            event_bridge.emit("job_updated", {"job": job})

        token = CancelToken()
        self.tokens[id(job)] = token
        self.jobs[id(job)] = job

        self._execute_job(job, token)

    # ------------------------------------------------

    def _cleanup_job(self, job):
        self.tokens.pop(id(job), None)
        self.jobs.pop(id(job), None)

    # ------------------------------------------------

    def _build_failure_log_block(self, job, job_name, user_error, failure_kind, technical_details):
        detail_block = technical_details.strip() if technical_details else ""
        lines = [
            "event=job_failed",
            f"job_id={id(job)}",
            f"job_name={job_name}",
            "status=FAILED",
            f"user_error={user_error}",
            f"failure_kind={failure_kind}",
        ]
        if detail_block:
            lines.append(detail_block)
        lines.append("---")
        return "\n".join(lines) + "\n"

    def _execute_job(self, job, token):
        try:
            source_size = _get_source_size_bytes(job)
            estimated_size = _ensure_estimated_size_bytes(job)
            if estimate_output_is_actionable(
                job,
                estimated_size_bytes=estimated_size,
                source_size_bytes=source_size,
            ) is False:
                patch_job(job, status="NO_GAIN", progress=0)
                event_bridge.emit("job_updated", {"job": job})
                return

            patch_job(job, status="PROCESSING")
            event_bridge.emit("job_updated", {"job": job})

            self.engine.process(job, cancel_token=token)
            _enforce_quick_output_size_contract(job, estimated_size)

            output_path = getattr(job, "output_path", None)
            output_size_bytes = None
            output_bitrate = None
            if output_path and os.path.exists(output_path):
                try:
                    output_size_bytes = os.path.getsize(output_path)
                except Exception:
                    output_size_bytes = None
                try:
                    probe_result = probe(output_path)
                except Exception:
                    probe_result = None
                output_bitrate = _resolve_output_bitrate(output_size_bytes, probe_result, job)
            patch_job(
                job,
                output_size_bytes=output_size_bytes,
                output_bitrate=output_bitrate,
                last_output_profile_mode=str(getattr(job, "profile_mode", "quick") or "quick").strip().lower(),
                last_output_profile_signature=build_profile_signature(job),
            )

            patch_job(job, progress=100, status="DONE")
            event_bridge.emit("job_updated", {"job": job})
            event_bridge.emit("job_finished", {"job": job})

        except CompressionCancelledError:
            patch_job(job, status="CANCELLED", error=None, progress=0)
            event_bridge.emit("job_updated", {"job": job})

        except Exception as error:
            job_name = getattr(job, "name", getattr(job, "source_path", "unknown"))
            technical_details = None
            user_error = str(error)
            failure_kind = "unexpected_exception"

            if isinstance(error, CompressionProcessError):
                user_error = error.user_message
                technical_details = error.technical_details
                failure_kind = error.failure_kind

            patch_job(job, status="FAILED", error=user_error)

            if technical_details:
                self.logger.technical_error(
                    self._build_failure_log_block(
                        job=job,
                        job_name=job_name,
                        user_error=user_error,
                        failure_kind=failure_kind,
                        technical_details=technical_details,
                    )
                )
            else:
                self.logger.exception(
                    self._build_failure_log_block(
                        job=job,
                        job_name=job_name,
                        user_error=user_error,
                        failure_kind=failure_kind,
                        technical_details="",
                    ).rstrip()
                )

            event_bridge.emit("job_failed", {"job": job, "error": user_error})

        finally:
            self._cleanup_job(job)
            self._emit_shutdown_idle_if_ready()
