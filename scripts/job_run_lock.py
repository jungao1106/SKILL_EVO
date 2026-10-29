#!/usr/bin/env python
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO


LOCK_FILENAME = ".skills-evo-run.lock"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lock_path(job_dir: Path) -> Path:
    return job_dir / LOCK_FILENAME


def job_is_running(job_dir: Path) -> bool:
    """Return whether another local process holds this job's run lock."""
    lock_path = _lock_path(job_dir)
    if not lock_path.exists():
        return False

    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


@contextmanager
def exclusive_job_run(job_dir: Path) -> Iterator[TextIO]:
    """Hold an advisory lock for a Harbor job for the duration of a run."""
    job_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(job_dir)
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "unknown owner"
            raise RuntimeError(
                f"Job is already running: {job_dir} ({owner})"
            ) from exc

        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "pid": os.getpid(),
                "started_at": _utc_now(),
                "job_dir": str(job_dir.resolve()),
            },
            handle,
            sort_keys=True,
        )
        handle.write("\n")
        handle.flush()
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
