from pathlib import Path

APP_NAME = "MTools CompactMe"
WINDOWS_APP_USER_MODEL_ID = "MTools.CompactMe"
APP_VERSION = "1.1.0-rc395"
APP_PHASE = "output_directory_native_separator_fix"

# Build counter remains available for diagnostics/logging without driving the
# user-facing release version displayed in the title bar.
_counter_path = Path(__file__).resolve().parent.parent / ".build_counter"

try:
    BUILD_COUNTER = _counter_path.read_text().strip()
except Exception:
    BUILD_COUNTER = "0"

BUILD_ID = f"BUILD_{BUILD_COUNTER}"

