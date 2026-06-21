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

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
TEMPLATE = REPO_ROOT / "templates" / "scripts" / "claude-with-retry.sh.tmpl"


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
