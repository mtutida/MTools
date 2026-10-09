import os
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal, Slot

from app.engine.ffprobe_probe import probe

VIDEO_EXT = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v", ".ts", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".vob")
AUDIO_EXT = (".mp3", ".aac", ".wav", ".flac", ".ogg", ".m4a", ".wma", ".opus", ".alac")
SUPPORTED_EXT = VIDEO_EXT + AUDIO_EXT


@dataclass
class ImportBatchRequest:
    paths: list[str] | None = None
    folder: str | None = None


class ImportBatchWorker(QObject):
    progress = Signal(int, int, int, int)  # current, total, valid, invalid
    valid_found = Signal(str, object, object)  # path, probe_result, source_size
    finished = Signal(dict)

    def __init__(self, request: ImportBatchRequest):
        super().__init__()
        self._request = request
        self._cancelled = False

    @Slot()
    def run(self):
        valid_count = 0
        invalid_count = 0
        invalid_names: list[str] = []
        discovered_paths = self._collect_paths()
        total = len(discovered_paths)

        if total == 0:
            self.progress.emit(0, 0, 0, 0)
            self.finished.emit(
                {
                    "total": 0,
                    "valid": 0,
                    "invalid": 0,
                    "invalid_names": [],
                }
            )
            return

        for current, path in enumerate(discovered_paths, start=1):
            if self._cancelled:
                break

            try:
                result = probe(path)
                if result is None:
                    invalid_count += 1
                    invalid_names.append(os.path.basename(path))
                    self.progress.emit(current, total, valid_count, invalid_count)
                    continue

                try:
                    source_size = os.path.getsize(path)
                except Exception:
                    source_size = None

                valid_count += 1
                self.valid_found.emit(path, result, source_size)
            except Exception:
                invalid_count += 1
                invalid_names.append(os.path.basename(path))

            self.progress.emit(current, total, valid_count, invalid_count)

        self.finished.emit(
            {
                "total": total,
                "valid": valid_count,
                "invalid": invalid_count,
                "invalid_names": invalid_names,
            }
        )

    def cancel(self):
        self._cancelled = True

    def _collect_paths(self) -> list[str]:
        if self._request.folder:
            collected: list[str] = []
            for root, _, files in os.walk(self._request.folder):
                for name in files:
                    if name.lower().endswith(SUPPORTED_EXT):
                        collected.append(os.path.join(root, name))
            return collected

        paths = self._request.paths or []
        return [path for path in paths if isinstance(path, str) and path]
