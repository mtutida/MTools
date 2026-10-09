"""Verified candidate resolver shared by the CompactMe application and tools."""
from __future__ import annotations
import hashlib, json, os, re, subprocess, atexit, secrets, time, ctypes
from dataclasses import dataclass
from pathlib import Path

RUNTIME_ROOT = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "MTools" / "Shared" / "media"
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")

def _diag(message: str):
    try:
        root = Path(os.environ.get("COMPACTME_MEDIA_STATE_ROOT", os.environ.get("PROGRAMDATA", str(Path.home())))) / "MTools" / "Shared" / "media"
        root.mkdir(parents=True, exist_ok=True)
        with (root / "resolver-diagnostic.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} pid={os.getpid()} {message}\n")
    except Exception:
        pass

class ResolutionError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}"); self.code = code

@dataclass(frozen=True)
class RuntimeHandle:
    runtime_id: str
    manifest_sha256: str
    ffmpeg: Path
    ffprobe: Path
    capabilities: dict
    lease_path: Path | None = None

    def release(self):
        if self.lease_path:
            _diag(f"LEASE_RELEASE_BEGIN runtimeId={self.runtime_id} lease={self.lease_path}")
            lock = self.lease_path.parents[2] / "locks" / f"{self.runtime_id}.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            try: _acquire_lock(lock)
            except RuntimeError: return
            try: self.lease_path.unlink()
            except FileNotFoundError: pass
            leases = self.lease_path.parent
            try:
                if not any(leases.glob("*.lease")):
                    marker = leases.parents[1] / "inuse" / self.runtime_id
                    marker.unlink(missing_ok=True)
            except OSError: pass
            try: lock.unlink()
            except FileNotFoundError: pass
            _diag(f"LEASE_RELEASE_END runtimeId={self.runtime_id}")

def acquire_lease(handle: RuntimeHandle) -> RuntimeHandle:
    _diag(f"LEASE_ACQUIRE_BEGIN runtimeId={handle.runtime_id}")
    runtime = handle.ffmpeg.parents[1]
    state_base = Path(os.environ.get("COMPACTME_MEDIA_STATE_ROOT", "")) if os.environ.get("COMPACTME_MEDIA_STATE_ROOT") else Path(os.environ.get("PROGRAMDATA", str(runtime.parent.parent))) / "MTools" / "Shared" / "media"
    leases = state_base / "leases" / handle.runtime_id; leases.mkdir(parents=True, exist_ok=True)
    lock = state_base / "locks" / f"{handle.runtime_id}.lock"; lock.parent.mkdir(parents=True, exist_ok=True)
    _acquire_lock(lock)
    try:
        for stale in leases.glob("*.lease"):
            try:
                data = _load_json(stale); status = _process_status(int(data["pid"]), data.get("creationTime"))
                if status is False: stale.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, json.JSONDecodeError): continue
        lease = leases / f"{os.getpid()}-{secrets.token_hex(6)}.lease"
        fd = os.open(str(lease), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "created": time.time(), "creationTime": _process_creation_time(os.getpid()), "runtimeId": handle.runtime_id}, stream)
        (state_base / "inuse" / handle.runtime_id).parent.mkdir(parents=True, exist_ok=True)
        (state_base / "inuse" / handle.runtime_id).touch(exist_ok=True)
    finally:
        _release_lock(lock)
    result = RuntimeHandle(handle.runtime_id, handle.manifest_sha256, handle.ffmpeg, handle.ffprobe, handle.capabilities, lease)
    atexit.register(result.release)
    _diag(f"LEASE_ACQUIRE_END runtimeId={handle.runtime_id} lease={lease}")
    return result

def _acquire_lock(lock: Path):
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "creationTime": _process_creation_time(os.getpid())}, stream)
    except FileExistsError:
        try:
            owner = _load_json(lock)
            expected = owner.get("creationTime")
            if "creationTimeLow" in owner and "creationTimeHigh" in owner:
                expected = ((int(owner["creationTimeHigh"]) & 0xffffffff) << 32) | (int(owner["creationTimeLow"]) & 0xffffffff)
            status = _process_status(int(owner["pid"]), expected)
        except (OSError, ValueError, KeyError, json.JSONDecodeError): status = None
        if status is False:
            lock.unlink(missing_ok=True); return _acquire_lock(lock)
        raise RuntimeError("state lock is held or indeterminate")

def _release_lock(lock: Path):
    try: lock.unlink()
    except FileNotFoundError: pass

def _process_creation_time(pid: int):
    if os.name != "nt": return None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True); k32.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]; k32.OpenProcess.restype=ctypes.c_void_p
    handle = k32.OpenProcess(0x1000, False, pid)
    if not handle: return None if ctypes.get_last_error() == 5 else False
    creation = ctypes.c_ulonglong(); exit_t=ctypes.c_ulonglong(); kernel=ctypes.c_ulonglong(); user=ctypes.c_ulonglong()
    k32.GetProcessTimes.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulonglong),ctypes.POINTER(ctypes.c_ulonglong),ctypes.POINTER(ctypes.c_ulonglong),ctypes.POINTER(ctypes.c_ulonglong)]
    ok = k32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t), ctypes.byref(kernel), ctypes.byref(user)); k32.CloseHandle(handle)
    return int(creation.value) if ok else None

def _process_status(pid: int, expected):
    actual = _process_creation_time(pid)
    if actual is None: return None
    return expected is None or int(expected) == actual

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()

def _load_json(path: Path):
    raw = path.read_bytes()
    # NSIS Unicode builds write lock metadata as UTF-16LE; application
    # leases remain UTF-8. Accept both encodings while preserving strict JSON.
    if raw.startswith(b"\xff\xfe") or (b"\x00" in raw and raw.count(b"\x00") >= len(raw) // 4):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    return json.loads(text)

def _safe_child(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ResolutionError("INTEGRITY_FAILED", f"unsafe relative path: {relative}")
    candidate = (root / relative).resolve(); base = root.resolve()
    if candidate != base and base not in candidate.parents: raise ResolutionError("INTEGRITY_FAILED", f"path escapes runtime root: {relative}")
    return candidate

def _version(value: str, field: str) -> tuple[int, int, int]:
    match = SEMVER.match(str(value or ""))
    if not match: raise ResolutionError("VERSION_INCOMPATIBLE", f"invalid {field}")
    return tuple(int(part) for part in match.groups())

def _in_range(value: tuple[int, int, int], spec: dict | None) -> bool:
    if not spec: return True
    if spec.get("minInclusive") and value < _version(spec["minInclusive"], "minimum"): return False
    if spec.get("maxExclusive") and value >= _version(spec["maxExclusive"], "maximum"): return False
    return True

def _verify_candidate(root: Path, item: dict, requirements: dict) -> RuntimeHandle:
    root = root.resolve()
    if root.parent.name == "runtimes" and root.name != str(item.get("runtimeId")): raise ResolutionError("PROVENANCE_UNTRUSTED", "runtime identity/path mismatch")
    manifest_path = _safe_child(root, "manifest.json")
    if not manifest_path.is_file(): raise ResolutionError("INTEGRITY_FAILED", "manifest missing")
    manifest_sha = _sha256(manifest_path)
    if item.get("manifestSha256") != manifest_sha: raise ResolutionError("INTEGRITY_FAILED", "manifest digest mismatch")
    manifest = _load_json(manifest_path)
    if manifest.get("schemaVersion") != 1 or manifest.get("runtimeId") != item.get("runtimeId"): raise ResolutionError("SCHEMA_UNSUPPORTED", "manifest schema or identity")
    platform_info = manifest.get("platform", {})
    if platform_info.get("os") != "windows" or platform_info.get("arch") != "x64": raise ResolutionError("PLATFORM_UNSUPPORTED", "candidate is not Windows x64")
    if not _in_range(_version(manifest.get("packageVersion"), "packageVersion"), requirements.get("packageRange")): raise ResolutionError("VERSION_INCOMPATIBLE", "package range")
    if manifest.get("runtimeContractMajor", 1) != requirements.get("runtimeContractMajor", 1): raise ResolutionError("SCHEMA_UNSUPPORTED", "runtime contract mismatch")
    entries = manifest.get("files", [])
    if not isinstance(entries, list) or not entries: raise ResolutionError("INTEGRITY_FAILED", "manifest file list missing")
    paths = [entry.get("path") for entry in entries]
    if len(paths) != len(set(paths)) or set(paths) != {"bin/ffmpeg.exe", "bin/ffprobe.exe"}:
        raise ResolutionError("INTEGRITY_FAILED", "manifest must contain exactly one FFmpeg and FFprobe entry")
    for entry in entries:
        path = _safe_child(root, entry["path"])
        if not path.is_file() or path.stat().st_size != int(entry.get("size", -1)) or _sha256(path) != entry.get("sha256"): raise ResolutionError("INTEGRITY_FAILED", entry["path"])
    required = requirements.get("requiredCapabilities", {}); caps = manifest.get("capabilities", {})
    for key, values in required.items():
        if not set(values).issubset(set(caps.get(key, []))): raise ResolutionError("CAPABILITY_MISSING", key)
    ffmpeg, ffprobe = _safe_child(root, "bin/ffmpeg.exe"), _safe_child(root, "bin/ffprobe.exe")
    if not ffmpeg.is_file() or not ffprobe.is_file(): raise ResolutionError("INTEGRITY_FAILED", "ffmpeg/ffprobe pair missing")
    return RuntimeHandle(item["runtimeId"], manifest_sha, ffmpeg, ffprobe, caps)

def resolve(requirements: dict, *, runtime_root: Path | None = None, bundled_root: Path | None = None) -> RuntimeHandle:
    _diag(f"RESOLVE_BEGIN runtime_root={runtime_root} bundled_root={bundled_root} policy={requirements.get('policy')}")
    root = (runtime_root or RUNTIME_ROOT).resolve(); rejected = []; certified = requirements.get("certifiedRuntimeIds")
    catalog_path = _safe_child(root, "catalog.json")
    if catalog_path.is_file():
        try:
            catalog = _load_json(catalog_path); candidates = catalog.get("runtimes", [])
            if certified is not None: candidates = [item for item in candidates if item.get("runtimeId") in certified]
            candidates.sort(key=lambda item: (certified.index(item["runtimeId"]) if certified else 10**9, item.get("runtimeId", "")))
            for item in candidates:
                if item.get("state") != "eligible": continue
                try: return acquire_lease(_verify_candidate(_safe_child(root, "runtimes/" + item["runtimeId"]), item, requirements))
                except ResolutionError as exc:
                    _diag(f"RESOLVE_SHARED_REJECT runtimeId={item.get('runtimeId')} code={exc.code} detail={exc}")
                    rejected.append(f"{item.get('runtimeId')}: {exc.code}")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _diag(f"RESOLVE_CATALOG_REJECT detail={exc}")
            rejected.append(f"catalog: {exc}")
    if bundled_root is not None and requirements.get("policy", "shared-then-bundled") != "shared-only":
        try:
            manifest_path = _safe_child(bundled_root, "manifest.json"); manifest = _load_json(manifest_path)
            item = {"runtimeId": manifest["runtimeId"], "manifestSha256": _sha256(manifest_path)}
            return acquire_lease(_verify_candidate(bundled_root, item, requirements))
        except (ResolutionError, OSError, ValueError, KeyError) as exc:
            _diag(f"RESOLVE_BUNDLED_REJECT code={getattr(exc, 'code', type(exc).__name__)} detail={exc}")
            rejected.append(f"bundled: {getattr(exc, 'code', str(exc))}")
    raise ResolutionError("RUNTIME_MISSING" if not rejected else "VERSION_INCOMPATIBLE", "; ".join(rejected) or "no eligible runtime")

def probe(handle: RuntimeHandle) -> dict:
    result = {}
    for name, executable in (("ffmpeg", handle.ffmpeg), ("ffprobe", handle.ffprobe)):
        completed = subprocess.run([str(executable), "-version"], capture_output=True, text=True, timeout=20, check=False, cwd=str(executable.parent))
        if completed.returncode != 0: raise ResolutionError("PROBE_FAILED", name)
        result[name] = {"returncode": completed.returncode, "version": completed.stdout.splitlines()[0] if completed.stdout else ""}
    return result
