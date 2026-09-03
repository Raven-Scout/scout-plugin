"""Failure-path coverage for the installers and the bootstrap pipeline lock.

Each of these modules already has a happy-path test file; what's missing is
every branch that only fires when the host misbehaves — and those are the
branches that decide whether Scout degrades gracefully or wedges:

* `install_scoutctl_shim` must never raise and must never clobber a
  user-managed `scoutctl` (scout-plugin#99).
* `install_wake_schedule` must refuse rather than install a wrong wake time
  when the schedule has no weekday slot.
* `bootstrap_lock` guards the 8-stage pipeline against a concurrent dispatcher
  tick; a mis-classified stale lock lets two pipelines interleave (#36).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scout.scripts import bootstrap_lock as lock
from scout.scripts import install_scoutctl_shim as shim
from scout.scripts import install_wake_schedule as wake
from scout.scripts.bootstrap_lock import LockBusyError
from scout.scripts.install_scoutctl_shim import SHIM_MARKER, install_scoutctl_shim, shim_dir


class _Proc:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


# ---------------------------------------------------------------------------
# install_scoutctl_shim
# ---------------------------------------------------------------------------


@pytest.fixture
def real_bin(tmp_path: Path) -> Path:
    p = tmp_path / "plugin" / ".venv" / "bin" / "scoutctl"
    p.parent.mkdir(parents=True)
    p.write_text("#!/bin/sh\n", encoding="utf-8")
    p.chmod(0o755)
    return p


def test_shim_wraps_the_active_checkouts_scoutctl(tmp_path: Path, real_bin: Path) -> None:
    home = tmp_path / "home"
    written = install_scoutctl_shim(home=home, target_bin=real_bin)
    assert written == shim_dir(home) / "scoutctl"
    assert written is not None
    body = written.read_text()
    assert SHIM_MARKER in body
    assert f'exec "{real_bin}" "$@"' in body
    assert body.startswith("#!/bin/sh\n")
    assert os.access(written, os.X_OK)


def test_shim_is_skipped_when_the_target_scoutctl_is_missing(tmp_path: Path) -> None:
    assert install_scoutctl_shim(home=tmp_path / "home", target_bin=tmp_path / "nope") is None


def test_shim_is_skipped_when_the_bin_dir_cannot_be_created(
    tmp_path: Path, real_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only home degrades to manual prefix-minting, not a crash."""

    def boom(*_a: object, **_k: object):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", boom)
    assert install_scoutctl_shim(home=tmp_path / "home", target_bin=real_bin) is None


def test_shim_refuses_to_replace_a_symlinked_scoutctl(tmp_path: Path, real_bin: Path) -> None:
    """A symlink is presumed user-managed (pipx / a global venv); overwriting
    it would hijack their install."""
    home = tmp_path / "home"
    d = shim_dir(home)
    d.mkdir(parents=True)
    (d / "scoutctl").symlink_to(real_bin)

    assert install_scoutctl_shim(home=home, target_bin=real_bin) is None
    assert (d / "scoutctl").is_symlink()


def test_shim_refuses_to_replace_a_foreign_regular_file(tmp_path: Path, real_bin: Path) -> None:
    home = tmp_path / "home"
    d = shim_dir(home)
    d.mkdir(parents=True)
    foreign = d / "scoutctl"
    foreign.write_text("#!/bin/sh\n# someone else's scoutctl\n", encoding="utf-8")

    assert install_scoutctl_shim(home=home, target_bin=real_bin) is None
    assert "someone else's" in foreign.read_text()


def test_shim_refreshes_its_own_previous_shim(tmp_path: Path, real_bin: Path) -> None:
    """The shim must re-point at the current venv on every upgrade, or it
    dangles after a plugin update."""
    home = tmp_path / "home"
    d = shim_dir(home)
    d.mkdir(parents=True)
    (d / "scoutctl").write_text(f'#!/bin/sh\n{SHIM_MARKER}\nexec /old/path/scoutctl "$@"\n', encoding="utf-8")

    written = install_scoutctl_shim(home=home, target_bin=real_bin)
    assert written is not None
    assert "/old/path/scoutctl" not in written.read_text()
    assert str(real_bin) in written.read_text()


def test_shim_is_skipped_when_the_existing_file_cannot_be_read(
    tmp_path: Path, real_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    d = shim_dir(home)
    d.mkdir(parents=True)
    existing = d / "scoutctl"
    existing.write_text(f"{SHIM_MARKER}\n", encoding="utf-8")

    real_read_text = Path.read_text

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == existing:
            raise OSError("permission denied")
        return real_read_text(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", maybe_boom)
    assert install_scoutctl_shim(home=home, target_bin=real_bin) is None


def test_shim_is_skipped_when_the_write_fails(tmp_path: Path, real_bin: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"

    def boom(*_a: object, **_k: object):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    assert install_scoutctl_shim(home=home, target_bin=real_bin) is None


def test_shim_resolves_the_target_from_the_running_engine_by_default(
    tmp_path: Path, real_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shim, "resolve_scoutctl_bin", lambda: real_bin)
    written = install_scoutctl_shim(home=tmp_path / "home")
    assert written is not None
    assert str(real_bin) in written.read_text()


# ---------------------------------------------------------------------------
# install_wake_schedule
# ---------------------------------------------------------------------------


def _slot(key: str, fires_at: str, weekdays: tuple[str, ...]):
    from scout.schedule import load_default_schedule

    sched = load_default_schedule()
    template = next(iter(sched.values()))
    import dataclasses

    return dataclasses.replace(template, key=key, fires_at_local=fires_at, weekdays=weekdays)


def _sched(*slots):
    from scout.schedule import Schedule

    return Schedule({s.key: s for s in slots})


def test_earliest_weekday_slot_ignores_weekend_only_slots() -> None:
    weekend = _slot("weekend-briefing", "07:00", ("Sat", "Sun"))
    weekday = _slot("morning-briefing", "08:30", ("Mon", "Tue", "Wed", "Thu", "Fri"))
    earliest = wake.compute_earliest_weekday_slot(_sched(weekend, weekday))
    assert earliest is not None
    assert earliest.key == "morning-briefing"


def test_earliest_weekday_slot_is_none_when_every_slot_is_a_weekend_slot() -> None:
    assert wake.compute_earliest_weekday_slot(_sched(_slot("w", "07:00", ("Sat", "Sun")))) is None


def test_install_refuses_when_there_is_no_weekday_slot() -> None:
    """Better to fail loudly than to install a pmset rule for the wrong day —
    a silently wrong wake time means the briefing never fires."""
    with pytest.raises(ValueError, match="no weekday slot found"):
        wake.install_wake_schedule(_sched(_slot("w", "07:00", ("Sat",))))


def test_install_refuses_a_slot_whose_weekdays_map_to_no_day_letters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A defensive guard: every name in `_WEEKDAYS` also has a `_WEEKDAY_LETTER`
    entry, so the selector cannot currently hand `install` a slot that yields an
    empty day string. Pin the guard anyway — the two tables are edited
    independently, and a `_WEEKDAYS` entry added without its letter would
    otherwise run `pmset repeat wakeorpoweron "" HH:MM:SS`."""
    import dataclasses

    weird = dataclasses.replace(_slot("weird", "08:00", ("Mon",)), weekdays=("Funday",))
    monkeypatch.setattr(wake, "compute_earliest_weekday_slot", lambda _s: weird)

    with pytest.raises(ValueError, match="slot weird has no recognizable weekdays"):
        wake.install_wake_schedule(_sched(_slot("m", "08:00", ("Mon",))))


def test_install_dry_run_reports_the_pmset_command_without_running_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_a: object, **_k: object):
        raise AssertionError("dry_run must not shell out")

    monkeypatch.setattr(subprocess, "run", fail)
    out = wake.install_wake_schedule(
        _sched(_slot("morning-briefing", "08:30", ("Mon", "Tue", "Wed", "Thu", "Fri"))), dry_run=True
    )
    assert out == "[dry-run] would run: pmset repeat wakeorpoweron MTWRF 08:30:00"


def test_install_runs_pmset_and_reports_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or _Proc(0))
    out = wake.install_wake_schedule(_sched(_slot("morning-briefing", "08:30", ("Mon", "Wed"))))
    assert seen == [["pmset", "repeat", "wakeorpoweron", "MW", "08:30:00"]]
    assert out.startswith("installed: pmset repeat wakeorpoweron MW")


def test_install_surfaces_a_pmset_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, "pmset: must be root\n"))
    with pytest.raises(RuntimeError, match="pmset failed: pmset: must be root"):
        wake.install_wake_schedule(_sched(_slot("m", "08:30", ("Mon",))))


def test_uninstall_dry_run_reports_the_cancel_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a: object, **_k: object):
        raise AssertionError("dry_run must not shell out")

    monkeypatch.setattr(subprocess, "run", fail)
    assert wake.uninstall_wake_schedule(dry_run=True) == "[dry-run] would run: pmset repeat cancel"


def test_uninstall_runs_pmset_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(cmd) or _Proc(0))
    assert wake.uninstall_wake_schedule() == "uninstalled"
    assert seen == [["pmset", "repeat", "cancel"]]


def test_uninstall_surfaces_a_pmset_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(1, "pmset: no repeating events\n"))
    with pytest.raises(RuntimeError, match="pmset failed"):
        wake.uninstall_wake_schedule()


# ---------------------------------------------------------------------------
# bootstrap_lock
# ---------------------------------------------------------------------------


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    return tmp_path / ".scout-logs" / ".scout-session.lock"


@pytest.mark.parametrize("pid", [0, -1, -12345])
def test_a_nonpositive_pid_is_never_treated_as_alive(pid: int) -> None:
    """os.kill(0, 0) signals the whole process group and os.kill(-1, 0) every
    process the user owns — a corrupt lock must not reach either."""
    assert lock._pid_alive(pid) is False


def test_a_pid_we_cannot_signal_counts_as_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """A different-uid holder still holds the lock; treating EPERM as "dead"
    would let us steal it."""

    def boom(_pid: int, _sig: int) -> None:
        raise PermissionError("operation not permitted")

    monkeypatch.setattr(os, "kill", boom)
    assert lock._pid_alive(4711) is True


def test_a_dead_pid_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_pid: int, _sig: int) -> None:
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(os, "kill", boom)
    assert lock._pid_alive(4711) is False


def test_our_own_pid_is_alive() -> None:
    assert lock._pid_alive(os.getpid()) is True


def test_lock_is_not_held_when_the_file_is_absent(lock_path: Path) -> None:
    assert lock.is_lock_held_by_live_pid(lock_path) is False


def test_lock_is_not_held_when_the_pid_is_unparseable(lock_path: Path) -> None:
    """The empty file a racing winner leaves between its O_EXCL create and its
    PID write reads as "not held by a live PID" — but acquire() still treats it
    as busy (#36)."""
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("", encoding="utf-8")
    assert lock.is_lock_held_by_live_pid(lock_path) is False
    lock_path.write_text("not-a-pid", encoding="utf-8")
    assert lock.is_lock_held_by_live_pid(lock_path) is False


def test_acquire_writes_our_pid_and_release_removes_it(lock_path: Path) -> None:
    lock.acquire_lock(lock_path)
    assert lock_path.read_text().strip() == str(os.getpid())
    assert lock.is_lock_held_by_live_pid(lock_path) is True

    lock.release_lock(lock_path)
    assert not lock_path.exists()


def test_acquire_raises_lock_busy_for_a_live_holder(lock_path: Path) -> None:
    lock.acquire_lock(lock_path)
    with pytest.raises(LockBusyError) as exc:
        lock.acquire_lock(lock_path)
    assert exc.value.pid == os.getpid()
    assert exc.value.lock_path == lock_path


def test_acquire_reclaims_a_lock_left_by_a_dead_pid(lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("999999999", encoding="utf-8")  # a PID that cannot exist
    lock.acquire_lock(lock_path)
    assert lock_path.read_text().strip() == str(os.getpid())


def test_acquire_treats_an_unparseable_lock_as_busy(lock_path: Path) -> None:
    """This is the #36 window: an empty lock is a racing winner mid-write, not
    a stale lock, so it must never be unlinked."""
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("", encoding="utf-8")
    with pytest.raises(LockBusyError) as exc:
        lock.acquire_lock(lock_path)
    assert exc.value.pid == -1
    assert lock_path.exists()


def test_acquire_rolls_back_a_partial_claim_when_the_pid_write_fails(
    lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written lock would be unparseable forever, wedging every later
    pipeline run — so the claim is rolled back instead."""

    def boom(*_a: object, **_k: object):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", boom)
    with pytest.raises(OSError, match="disk full"):
        lock.acquire_lock(lock_path)
    assert not lock_path.exists()


def test_release_is_a_no_op_when_the_lock_is_absent(lock_path: Path) -> None:
    lock.release_lock(lock_path)  # must not raise


def test_release_clears_an_unparseable_lock(lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("garbage", encoding="utf-8")
    lock.release_lock(lock_path)
    assert not lock_path.exists()


def test_release_leaves_another_processes_lock_alone(lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(str(os.getpid() + 1), encoding="utf-8")
    lock.release_lock(lock_path)
    assert lock_path.exists()


def test_remove_stale_lock_only_removes_a_dead_holders_lock(lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("999999999", encoding="utf-8")
    lock.remove_stale_lock(lock_path)
    assert not lock_path.exists()

    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    lock.remove_stale_lock(lock_path)
    assert lock_path.exists()


def test_remove_stale_lock_is_a_no_op_when_absent(lock_path: Path) -> None:
    lock.remove_stale_lock(lock_path)  # must not raise


def test_acquire_with_wait_returns_immediately_when_free(lock_path: Path) -> None:
    lock.acquire_lock_with_wait(lock_path, timeout_s=1, poll_s=0)
    assert lock_path.read_text().strip() == str(os.getpid())


def test_acquire_with_wait_polls_then_succeeds(lock_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock.acquire_lock(lock_path)
    slept: list[int] = []

    def fake_sleep(seconds: int) -> None:
        slept.append(seconds)
        # The holder goes away after the first poll.
        lock_path.unlink()

    monkeypatch.setattr("time.sleep", fake_sleep)
    lock.acquire_lock_with_wait(lock_path, timeout_s=300, poll_s=10)
    assert slept == [10]
    assert lock_path.read_text().strip() == str(os.getpid())


def test_acquire_with_wait_raises_on_timeout(lock_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock.acquire_lock(lock_path)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(LockBusyError):
        lock.acquire_lock_with_wait(lock_path, timeout_s=0, poll_s=0)
