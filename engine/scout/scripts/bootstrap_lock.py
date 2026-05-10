"""Global pipeline lock for `scoutctl bootstrap install|upgrade`.

Holds ``.scout-logs/.scout-session.lock`` for the entire 8-stage pipeline.
Runner scripts and the dispatcher already check this lock and skip when
held — so holding it for the pipeline closes every interleaving window
between bootstrap stages and dispatcher ticks.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockBusyError(Exception):
    """Raised when the lock is already held by a live PID."""

    def __init__(self, lock_path: Path, pid: int) -> None:
        self.lock_path = lock_path
        self.pid = pid
        super().__init__(f"lock {lock_path} held by live PID {pid}")


def is_lock_held_by_live_pid(lock_path: Path) -> bool:
    """Return True iff the lock file exists and its PID is alive."""
    if not lock_path.exists():
        return False
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, 0)  # signal 0 — existence probe, no actual signal sent
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Different uid — process exists but we can't signal it. Treat as live.
        return True


def acquire_lock(lock_path: Path) -> None:
    """Take the lock by writing our PID. Raise if already held by a live PID."""
    if is_lock_held_by_live_pid(lock_path):
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = -1
        raise LockBusyError(lock_path, pid)
    if lock_path.exists():
        # Stale (dead PID). Remove and continue.
        lock_path.unlink()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()), encoding="utf-8")


def release_lock(lock_path: Path) -> None:
    """Release the lock if we still hold it."""
    if not lock_path.exists():
        return
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        lock_path.unlink()
        return
    if pid == os.getpid():
        lock_path.unlink()


def remove_stale_lock(lock_path: Path) -> None:
    """Remove the lock file iff its PID is dead. No-op otherwise."""
    if lock_path.exists() and not is_lock_held_by_live_pid(lock_path):
        lock_path.unlink()


def acquire_lock_with_wait(
    lock_path: Path, *, timeout_s: int = 300, poll_s: int = 10
) -> None:
    """Acquire with up to ``timeout_s`` of polling. Raise LockBusyError on timeout."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            acquire_lock(lock_path)
            return
        except LockBusyError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(poll_s)
