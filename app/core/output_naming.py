import os
import datetime

DEFAULT_SOURCE_OUTPUT_SUBDIR = "CompactMe"


def _resolve_source_output_directory(base_dir):
    """Return the dynamic default output directory for source-based output.

    The default is a CompactMe subfolder next to the source file, avoiding
    writes beside the original file while keeping outputs easy to find.
    If the source is already inside a CompactMe folder, reuse that folder
    instead of nesting CompactMe/CompactMe.
    """
    base_dir = base_dir or "."
    if os.path.basename(os.path.normpath(base_dir)).lower() == DEFAULT_SOURCE_OUTPUT_SUBDIR.lower():
        return base_dir
    return os.path.join(base_dir, DEFAULT_SOURCE_OUTPUT_SUBDIR)


from app.ancillary.configuration import ConfigurationService


def _normalize_naming_mode(value):
    if isinstance(value, str):
        normalized = value.strip().lower()
        mapping = {
            "original": 0,
            "suffix": 1,
            "pattern": 1,
            "timestamp": 2,
            "date": 2,
        }
        return mapping.get(normalized, 0)

    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0

    return value if value in (0, 1, 2) else 0


def _resolve_output_extension(input_ext, job=None):
    ext = (input_ext or "").lower()

    profile_mode = str(getattr(job, "profile_mode", "") or "").strip().lower() if job is not None else ""
    if profile_mode == "audio":
        requested = str(getattr(job, "audio_output_extension", None) or getattr(job, "audio_output_format", None) or "").strip().lower()
        if requested and not requested.startswith("."):
            requested = "." + requested
        if requested:
            return requested

    requested_video = str(getattr(job, "video_output_extension", None) or getattr(job, "video_output_format", None) or "").strip().lower()
    if requested_video:
        if not requested_video.startswith("."):
            requested_video = "." + requested_video
        allowed_video = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".m4v"}
        if requested_video in allowed_video:
            return requested_video

    # 3GP input is transcoded with H.264/AAC in this application.
    # MP4 is a significantly safer output container for that profile than 3GP.
    if ext == ".3gp":
        return ".mp4"

    return input_ext


def generate_output_path(input_path, job=None):
    cfg = ConfigurationService.instance().get()

    base_dir = os.path.dirname(input_path)
    name, ext = os.path.splitext(os.path.basename(input_path))
    ext = _resolve_output_extension(ext, job=job)

    # directory
    if getattr(cfg, "use_source_directory", True):
        directory = _resolve_source_output_directory(base_dir)
    else:
        directory = cfg.output_directory or _resolve_source_output_directory(base_dir)

    # naming mode
    mode = _normalize_naming_mode(getattr(cfg, "output_naming_mode", 0))

    if mode == 0:
        output_name = name + ext

    elif mode == 1:
        suffix = getattr(cfg, "output_suffix", "").strip()

        if "|" in suffix:
            suffix = suffix.split("|")[0].strip()

        # fallback if user left suffix empty
        if not suffix:
            suffix = "_compactado"

        if not suffix.startswith("_"):
            suffix = "_" + suffix

        output_name = name + suffix + ext

    else:  # mode == 2
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        output_name = f"{name}_{ts}{ext}"

    output = os.path.join(directory, output_name)

    # collision policy
    policy = getattr(cfg, "collision_policy", 0)

    if policy == 0:  # sequential numbering
        counter = 1
        base_output = output
        while os.path.exists(output):
            name2, ext2 = os.path.splitext(base_output)
            output = f"{name2}_{counter}{ext2}"
            counter += 1

    elif policy == 1:  # overwrite
        pass

    elif policy == 2:  # cancel
        if os.path.exists(output):
            raise Exception("Arquivo de saída já existe")

    return output


def generate_available_output_path_from_path(output_path):
    """Return a non-existing path derived from an already resolved output path.

    This ignores overwrite/cancel preferences because it is a final safety
    guard used immediately before FFmpeg starts.
    """
    if not output_path:
        return output_path
    if not os.path.exists(output_path):
        return output_path
    directory = os.path.dirname(output_path)
    stem, ext = os.path.splitext(os.path.basename(output_path))
    counter = 1
    while True:
        candidate = os.path.join(directory, f"{stem}_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1
