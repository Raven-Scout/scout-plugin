"""Integration test for the concurrency-lock guard in templates/run-scout.sh.tmpl.

Renders the runner template with a stubbed claude-with-retry.sh and drives the
lock-guard branches with real holder processes.

Regression context: the guard was liveness-only (`kill -0 $PID`), so a session
that was alive but not progressing — host slept mid-run, wedged network call —
held the lock indefinitely and every subsequent scheduled slot silently exited
0 (41h and 21h outages observed, invisible to every audit surface because the
skip path is exit 0 and last-fire.json still records the fire). The guard now
bounds the lock by age: a live holder past SCOUT_LOCK_MAX_AGE_SECS is reaped
(TERM, grace, KILL), the reap is recorded in failures.log, and the new session
takes over the lock.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
TEMPLATE = REPO_ROOT / "templates" / "run-scout.sh.tmpl"

# The reap path is TERM → 5s grace → KILL, so give runs comfortable headroom.
RUN_TIMEOUT_S = 30


def _render(tmpl: Path, scout_dir: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",  # never reached — the retry wrapper is stubbed
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    out = scout_dir / "run-scout.sh"
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    return out


def _stub_retry_wrapper(scout_dir: Path) -> Path:
    """Stand-in for scripts/claude-with-retry.sh: records that the session
    body was reached (i.e. the lock was acquired), then succeeds."""
    marker = scout_dir / "session-ran.marker"
    stub = scout_dir / "scripts" / "claude-with-retry.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(f'#!/bin/bash\ntouch "{marker}"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    return marker


def _run(script: Path, *, max_age_s: int) -> subprocess.CompletedProcess:
    env = {**os.environ, "SCOUT_LOCK_MAX_AGE_SECS": str(max_age_s)}
    return subprocess.run(
        [str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _run_log(scout_dir: Path) -> str:
    logs = sorted((scout_dir / ".scout-logs").glob("scout-*.log"))
    assert logs, "runner should always create its per-run log"
    return "\n".join(log.read_text(encoding="utf-8") for log in logs)


def test_fresh_alive_holder_still_skips(tmp_path: Path) -> None:
    """A live holder under the age ceiling keeps the original skip behavior."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    marker = _stub_retry_wrapper(scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    holder = subprocess.Popen(["sleep", "300"])
    try:
        lock = log_dir / ".scout-session.lock"
        lock.write_text(f"{holder.pid}\n", encoding="utf-8")  # mtime = now → fresh

        result = _run(script, max_age_s=3600)

        assert result.returncode == 0, result.stderr
        assert "Another Scout session running" in _run_log(scout_dir)
        assert not marker.exists(), "a fresh live holder must not be taken over"
        assert holder.poll() is None, "a fresh live holder must not be killed"
        assert lock.read_text(encoding="utf-8").strip() == str(holder.pid)
    finally:
        holder.kill()
        holder.wait()


def test_wedged_holder_is_reaped_and_lock_taken_over(tmp_path: Path) -> None:
    """A live holder past the age ceiling is killed, logged, and replaced."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    marker = _stub_retry_wrapper(scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    holder = subprocess.Popen(["sleep", "300"])
    try:
        lock = log_dir / ".scout-session.lock"
        lock.write_text(f"{holder.pid}\n", encoding="utf-8")
        held_since = time.time() - 120
        os.utime(lock, (held_since, held_since))  # lock written 120s ago

        result = _run(script, max_age_s=30)

        assert result.returncode == 0, result.stderr
        log = _run_log(scout_dir)
        assert "Reaping wedged Scout session" in log
        failures = (log_dir / "failures.log").read_text(encoding="utf-8")
        assert f"Reaped wedged session PID {holder.pid}" in failures, (
            "the reap must be visible to audit surfaces, not just the per-run log"
        )
        assert marker.exists(), "the new session must proceed after the takeover"
        assert holder.wait(timeout=10) != 0, "the wedged holder must be killed"
        assert not lock.exists(), "the takeover session must clean up its own lock on exit"
    finally:
        holder.kill()
        holder.wait()


def test_dead_holder_stale_lock_still_removed(tmp_path: Path) -> None:
    """Guard: the pre-existing crashed-holder branch keeps working."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    marker = _stub_retry_wrapper(scout_dir)
    log_dir = scout_dir / ".scout-logs"
    log_dir.mkdir()
    dead = subprocess.Popen(["sleep", "0"])
    dead.wait()  # reaped → kill -0 fails for this PID
    lock = log_dir / ".scout-session.lock"
    lock.write_text(f"{dead.pid}\n", encoding="utf-8")

    result = _run(script, max_age_s=3600)

    assert result.returncode == 0, result.stderr
    assert marker.exists(), "a crashed holder's lock must not block the run"
    assert not lock.exists()
