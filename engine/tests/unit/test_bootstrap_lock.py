"""Unit tests for engine/scout/scripts/bootstrap_lock.py."""

from __future__ import annotations

import os

import pytest

from scout.scripts.bootstrap_lock import (
    LockBusyError,
    acquire_lock,
    acquire_lock_with_wait,
    is_lock_held_by_live_pid,
    release_lock,
    remove_stale_lock,
)


def test_acquire_lock_writes_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    acquire_lock(lock)
    assert lock.exists()
    assert lock.read_text().strip() == str(os.getpid())


def test_release_lock_removes_file(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    acquire_lock(lock)
    release_lock(lock)
    assert not lock.exists()


def test_is_lock_held_by_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    assert is_lock_held_by_live_pid(lock) is True


def test_is_lock_not_held_when_pid_dead(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    # macOS default max PID is 99998; 999999 is reliably unused.
    fake_pid = 999999
    lock.write_text(str(fake_pid))
    assert is_lock_held_by_live_pid(lock) is False


def test_is_lock_not_held_when_file_missing(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    assert is_lock_held_by_live_pid(lock) is False


def test_remove_stale_lock_removes_dead_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    # macOS default max PID is 99998; 999999 is reliably unused.
    lock.write_text("999999")
    remove_stale_lock(lock)
    assert not lock.exists()


def test_remove_stale_lock_preserves_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    remove_stale_lock(lock)
    assert lock.exists()


def test_acquire_raises_when_held_by_live_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))
    with pytest.raises(LockBusyError):
        acquire_lock(lock)


def test_acquire_lock_with_wait_times_out(tmp_path, monkeypatch):
    """acquire_lock_with_wait raises LockBusyError when deadline expires."""
    lock = tmp_path / ".scout-session.lock"
    lock.write_text(str(os.getpid()))  # held by live (this) PID

    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "scout.scripts.bootstrap_lock.time.sleep",
        lambda s: sleep_calls.append(s),
    )

    # Force monotonic to advance past the deadline immediately.
    times = iter([0.0, 1000.0, 2000.0])
    monkeypatch.setattr(
        "scout.scripts.bootstrap_lock.time.monotonic",
        lambda: next(times),
    )

    with pytest.raises(LockBusyError):
        acquire_lock_with_wait(lock, timeout_s=300, poll_s=10)


def test_acquire_lock_with_wait_succeeds_after_retry(tmp_path, monkeypatch):
    """acquire_lock_with_wait succeeds when a previously-held lock becomes free."""
    lock = tmp_path / ".scout-session.lock"
    lock.write_text("999999")  # held by dead PID — first call to acquire_lock will clean it

    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "scout.scripts.bootstrap_lock.time.sleep",
        lambda s: sleep_calls.append(s),
    )

    acquire_lock_with_wait(lock, timeout_s=300, poll_s=10)
    # Lock now held by us
    assert lock.read_text().strip() == str(os.getpid())
    # Should succeed on first try (dead PID cleared by acquire_lock)
    assert sleep_calls == []


def test_release_lock_preserves_other_owner(tmp_path):
    """release_lock must not unlink a lock owned by a different PID."""
    lock = tmp_path / ".scout-session.lock"
    other_pid = os.getpid() + 1  # almost certainly not us
    lock.write_text(str(other_pid))
    release_lock(lock)
    assert lock.exists()
    assert lock.read_text().strip() == str(other_pid)


def test_acquire_lock_clears_stale_dead_pid(tmp_path):
    """acquire_lock unlinks a stale dead-PID lock and writes our own."""
    lock = tmp_path / ".scout-session.lock"
    # macOS default max PID is 99998; 999999 is reliably unused.
    lock.write_text("999999")  # dead PID
    acquire_lock(lock)
    assert lock.read_text().strip() == str(os.getpid())
