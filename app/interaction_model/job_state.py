from __future__ import annotations

from threading import RLock
from types import SimpleNamespace
from typing import Any


_LOCK_ATTR = "_mc_state_lock"
_VERSION_ATTR = "_mc_state_version"


def ensure_job_state(job: object) -> object:
    if job is None:
        return job

    lock = getattr(job, _LOCK_ATTR, None)
    if lock is None:
        setattr(job, _LOCK_ATTR, RLock())
        setattr(job, _VERSION_ATTR, int(getattr(job, _VERSION_ATTR, 0) or 0))
    return job


def patch_job(job: object, **changes: Any) -> object:
    ensure_job_state(job)
    lock = getattr(job, _LOCK_ATTR)
    with lock:
        for key, value in changes.items():
            setattr(job, key, value)
        setattr(job, _VERSION_ATTR, int(getattr(job, _VERSION_ATTR, 0) or 0) + 1)
    return job



def get_job_attr(job: object, name: str, default: Any = None) -> Any:
    if job is None:
        return default
    ensure_job_state(job)
    lock = getattr(job, _LOCK_ATTR)
    with lock:
        return getattr(job, name, default)


def snapshot_job(job: object) -> object:
    if job is None:
        return None

    ensure_job_state(job)
    lock = getattr(job, _LOCK_ATTR)
    with lock:
        data: dict[str, Any] = {}
        for key, value in vars(job).items():
            if key.startswith('_mc_'):
                continue
            if callable(value):
                continue
            data[key] = value

    return SimpleNamespace(**data)
