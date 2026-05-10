"""Unit tests for engine/scout/scripts/bootstrap_lock.py."""

from __future__ import annotations

import os

import pytest

from scout.scripts.bootstrap_lock import (
    LockBusyError,
    acquire_lock,
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
    fake_pid = 999999
    lock.write_text(str(fake_pid))
    assert is_lock_held_by_live_pid(lock) is False


def test_is_lock_not_held_when_file_missing(tmp_path):
    lock = tmp_path / ".scout-session.lock"
    assert is_lock_held_by_live_pid(lock) is False


def test_remove_stale_lock_removes_dead_pid(tmp_path):
    lock = tmp_path / ".scout-session.lock"
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
