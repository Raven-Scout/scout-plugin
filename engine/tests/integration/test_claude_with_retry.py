"""Integration test for templates/scripts/claude-with-retry.sh.tmpl.

Renders the template and drives it with a fake `claude` binary that fails on
the first attempt and succeeds on the second, asserting the wrapper's transient
classifier retries (or doesn't) the right error classes.

Regression context: an overnight Scout run slept mid-stream, woke to a dropped
socket reported as "API Error: Connection closed mid-response", which was NOT
in TRANSIENT_PATTERNS — so the wrapper hard-failed (exit 1) instead of retrying
a fresh session on wake. That exit-1 poisoned the next run's failure-backoff.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
TEMPLATE = REPO_ROOT / "templates" / "scripts" / "claude-with-retry.sh.tmpl"


@pytest.fixture(autouse=True)
def _silence_desktop_notifier(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Auth-path tests must never pop a real notification on the dev's machine.

    No-op `osascript` / `notify-send` go first on PATH; the notification tests
    put their recorders in front of these.
    """
    quiet = tmp_path_factory.mktemp("quiet-notifier")
    for name in ("osascript", "notify-send"):
        p = quiet / name
        p.write_text("#!/bin/bash\ncat > /dev/null\nexit 0\n", encoding="utf-8")
        p.chmod(0o755)
    monkeypatch.setenv("PATH", f"{quiet}{os.pathsep}{os.environ['PATH']}")


def _render(tmpl: Path, scout_dir: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8").replace("{{INSTANCE_NAME}}", "Scout")
    out = scout_dir / "scripts" / "claude-with-retry.sh"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    return out


def _fake_claude(scout_dir: Path, *, first_attempt_output: str, first_attempt_exit: int) -> Path:
    """A stand-in `claude` binary: emits given output + exit on attempt 1, then succeeds.

    Tracks attempts in a sibling counter file so the test can assert how many
    times the wrapper invoked it.
    """
    counter = scout_dir / "attempts.count"
    bin_path = scout_dir / "fake-claude.sh"
    bin_path.write_text(
        "#!/bin/bash\n"
        f'COUNTER="{counter}"\n'
        'n=$(cat "$COUNTER" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$COUNTER"\n'
        'if [ "$n" -eq 1 ]; then\n'
        f"  echo {first_attempt_output!r}\n"
        f"  exit {first_attempt_exit}\n"
        "fi\n"
        'echo "session complete"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    bin_path.chmod(0o755)
    return bin_path


def _run(script: Path, fake_claude: Path, log_file: Path) -> subprocess.CompletedProcess:
    # BACKOFF_S=0 → sleep 0 → no real delay between attempts in the test.
    env = {**os.environ, "SCOUT_RETRY_BACKOFF_S": "0", "SCOUT_RETRY_MAX": "2"}
    return subprocess.run(
        [str(script), str(log_file), str(fake_claude)],
        env=env,
        capture_output=True,
        text=True,
    )


def _attempts(scout_dir: Path) -> int:
    return int((scout_dir / "attempts.count").read_text().strip())


def test_retries_on_connection_closed_mid_response(tmp_path: Path) -> None:
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    fake = _fake_claude(
        scout_dir,
        first_attempt_output="API Error: Connection closed mid-response. The response above may be incomplete.",
        first_attempt_exit=1,
    )
    log_file = scout_dir / "run.log"

    result = _run(script, fake, log_file)

    assert result.returncode == 0, log_file.read_text()
    assert _attempts(scout_dir) == 2, "wrapper should have retried after the dropped connection"


def test_retries_on_sleep_mid_response(tmp_path: Path) -> None:
    """Lid-close/clamshell sleep must be retried like any other sleep death.

    Regression context: a lid-close mid-run makes the CLI emit "Your computer
    went to sleep mid-response" — a different string from the idle-sleep
    "Connection closed mid-response" already covered — so the wrapper
    hard-failed with zero retries and the whole session's work was discarded.
    """
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    fake = _fake_claude(
        scout_dir,
        first_attempt_output=(
            "API Error: Your computer went to sleep mid-response. The response above may be incomplete."
        ),
        first_attempt_exit=1,
    )
    log_file = scout_dir / "run.log"

    result = _run(script, fake, log_file)

    assert result.returncode == 0, log_file.read_text()
    assert _attempts(scout_dir) == 2, "wrapper should have retried after the lid-close sleep"


def test_existing_transient_pattern_still_retries(tmp_path: Path) -> None:
    """Guard: editing the regex alternation must not break a pre-existing pattern."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    fake = _fake_claude(
        scout_dir,
        first_attempt_output="Stream idle timeout - partial response received",
        first_attempt_exit=1,
    )
    log_file = scout_dir / "run.log"

    result = _run(script, fake, log_file)

    assert result.returncode == 0, log_file.read_text()
    assert _attempts(scout_dir) == 2


def test_does_not_retry_non_transient_error(tmp_path: Path) -> None:
    """Guard: the classifier stays narrow — auth failures are not retried."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    fake = _fake_claude(
        scout_dir,
        first_attempt_output="API Error: 401 Unauthorized",
        first_attempt_exit=1,
    )
    log_file = scout_dir / "run.log"

    result = _run(script, fake, log_file)

    assert result.returncode == 1
    assert _attempts(scout_dir) == 1, "non-transient failure must not be retried"


def test_auth_failure_emits_remediation(tmp_path: Path) -> None:
    """A 401 must not retry, but must log a clear re-authentication remediation.

    Regression context: engine 0.7.2 user hit "Failed to authenticate. API Error:
    401 Invalid authentication credentials" and the wrapper only logged the opaque
    "Failure not classified as transient" line — no pointer to re-auth. The auth
    class now gets its own remediation block (still no retry).
    """
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    fake = _fake_claude(
        scout_dir,
        first_attempt_output="Failed to authenticate. API Error: 401 Invalid authentication credentials",
        first_attempt_exit=1,
    )
    log_file = scout_dir / "run.log"

    result = _run(script, fake, log_file)

    assert result.returncode == 1
    assert _attempts(scout_dir) == 1, "auth failure must not be retried"
    log = log_file.read_text()
    assert "setup-token" in log, "auth remediation must point at `claude setup-token`"
    assert "Authentication failure" in log, "auth failure must be classified distinctly"


def test_non_auth_non_transient_keeps_generic_message(tmp_path: Path) -> None:
    """A non-auth, non-transient failure keeps the generic 'not classified' message
    and does NOT get the auth remediation block."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATE, scout_dir)
    fake = _fake_claude(
        scout_dir,
        first_attempt_output="API Error: 400 Bad Request — malformed prompt",
        first_attempt_exit=1,
    )
    log_file = scout_dir / "run.log"

    result = _run(script, fake, log_file)

    assert result.returncode == 1
    assert _attempts(scout_dir) == 1
    log = log_file.read_text()
    assert "not classified as transient" in log
    assert "setup-token" not in log, "non-auth failures must not get the auth remediation"


# --- Desktop notification on auth failure (#267) ------------------------------
#
# The auth banner above lands in the session log, which nobody reads while a
# scheduled Scout silently fails for weeks. The wrapper also raises a local
# desktop notification: osascript / notify-send don't need the rejected
# credential. Tests shadow both binaries with recorders on PATH, so they run
# the same on macOS and Linux and never pop a real notification.

AUTH_ERROR = "Failed to authenticate: OAuth session expired and could not be refreshed"


def _notifier_bin(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    """Fake `osascript` + `notify-send` that append their argv to a record file."""
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    record = tmp_path / "notifications.log"
    for name in ("osascript", "notify-send"):
        p = fake_bin / name
        p.write_text(
            f'#!/bin/bash\necho "{name} $*" >> "{record}"\ncat > /dev/null\nexit {exit_code}\n',
            encoding="utf-8",
        )
        p.chmod(0o755)
    return fake_bin, record


def _scripted_claude(scout_dir: Path, *, output: str, exit_code: int) -> Path:
    """A `claude` that emits ``output`` and exits ``exit_code`` on every call."""
    bin_path = scout_dir / "scripted-claude.sh"
    bin_path.write_text(f"#!/bin/bash\necho {output!r}\nexit {exit_code}\n", encoding="utf-8")
    bin_path.chmod(0o755)
    return bin_path


def _run_with_notifier(
    script: Path, claude: Path, log_file: Path, fake_bin: Path, **env_overrides: str
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SCOUT_RETRY_BACKOFF_S": "0",
        "SCOUT_RETRY_MAX": "2",
        **env_overrides,
    }
    return subprocess.run([str(script), str(log_file), str(claude)], env=env, capture_output=True, text=True)


def _notifications(record: Path) -> list[str]:
    return record.read_text().splitlines() if record.exists() else []


def _vault(tmp_path: Path) -> tuple[Path, Path, Path]:
    scout_dir = tmp_path / "Scout"
    logs = scout_dir / ".scout-logs"
    logs.mkdir(parents=True)
    return scout_dir, logs, _render(TEMPLATE, scout_dir)


def test_auth_failure_raises_desktop_notification(tmp_path: Path) -> None:
    scout_dir, logs, script = _vault(tmp_path)
    fake_bin, record = _notifier_bin(tmp_path)
    claude = _scripted_claude(scout_dir, output=AUTH_ERROR, exit_code=1)
    log_file = logs / "scout-1.log"

    result = _run_with_notifier(script, claude, log_file, fake_bin)

    assert result.returncode == 1
    sent = _notifications(record)
    assert len(sent) == 1, sent
    # The user-facing fix must be in the notification itself, not only the log.
    assert "run claude" in sent[0].lower() or "run `claude`" in sent[0].lower(), sent[0]
    assert "Desktop notification sent" in log_file.read_text()


def test_auth_notification_is_rate_limited(tmp_path: Path) -> None:
    """The scheduler ticks every few minutes; a dead credential must not spam."""
    scout_dir, logs, script = _vault(tmp_path)
    fake_bin, record = _notifier_bin(tmp_path)
    claude = _scripted_claude(scout_dir, output=AUTH_ERROR, exit_code=1)

    _run_with_notifier(script, claude, logs / "scout-1.log", fake_bin)
    _run_with_notifier(script, claude, logs / "scout-2.log", fake_bin)

    assert len(_notifications(record)) == 1
    assert "suppressed" in (logs / "scout-2.log").read_text()


def test_auth_notification_repeats_after_interval(tmp_path: Path) -> None:
    scout_dir, logs, script = _vault(tmp_path)
    fake_bin, record = _notifier_bin(tmp_path)
    claude = _scripted_claude(scout_dir, output=AUTH_ERROR, exit_code=1)

    _run_with_notifier(script, claude, logs / "scout-1.log", fake_bin, SCOUT_AUTH_ALERT_INTERVAL_S="0")
    _run_with_notifier(script, claude, logs / "scout-2.log", fake_bin, SCOUT_AUTH_ALERT_INTERVAL_S="0")

    assert len(_notifications(record)) == 2


def test_successful_run_rearms_auth_notification(tmp_path: Path) -> None:
    """Once the user re-authenticates, the next expiry must alert immediately."""
    scout_dir, logs, script = _vault(tmp_path)
    fake_bin, record = _notifier_bin(tmp_path)
    failing = _scripted_claude(scout_dir, output=AUTH_ERROR, exit_code=1)
    ok_bin = scout_dir / "ok-claude.sh"
    ok_bin.write_text("#!/bin/bash\necho done\nexit 0\n", encoding="utf-8")
    ok_bin.chmod(0o755)

    _run_with_notifier(script, failing, logs / "scout-1.log", fake_bin)
    _run_with_notifier(script, ok_bin, logs / "scout-2.log", fake_bin)
    _run_with_notifier(script, failing, logs / "scout-3.log", fake_bin)

    assert len(_notifications(record)) == 2


def test_non_auth_failure_does_not_notify(tmp_path: Path) -> None:
    scout_dir, logs, script = _vault(tmp_path)
    fake_bin, record = _notifier_bin(tmp_path)
    claude = _scripted_claude(scout_dir, output="API Error: 400 Bad Request", exit_code=1)

    _run_with_notifier(script, claude, logs / "scout-1.log", fake_bin)

    assert _notifications(record) == []


def test_auth_failure_classified_in_single_attempt_mode(tmp_path: Path) -> None:
    """SCOUT_RETRY_DISABLE=1 used to hit "Retry exhausted" before the auth check,
    so the run got neither the remediation banner nor (now) the notification."""
    scout_dir, logs, script = _vault(tmp_path)
    fake_bin, record = _notifier_bin(tmp_path)
    claude = _scripted_claude(scout_dir, output=AUTH_ERROR, exit_code=1)
    log_file = logs / "scout-1.log"

    result = _run_with_notifier(script, claude, log_file, fake_bin, SCOUT_RETRY_DISABLE="1")

    assert result.returncode == 1
    assert "Authentication failure" in log_file.read_text()
    assert len(_notifications(record)) == 1


def test_failing_notifier_keeps_claude_exit_code(tmp_path: Path) -> None:
    """The notification is best-effort: a broken notifier never masks the real exit."""
    scout_dir, logs, script = _vault(tmp_path)
    fake_bin, _record = _notifier_bin(tmp_path, exit_code=7)
    claude = _scripted_claude(scout_dir, output=AUTH_ERROR, exit_code=3)

    result = _run_with_notifier(script, claude, logs / "scout-1.log", fake_bin)

    assert result.returncode == 3
