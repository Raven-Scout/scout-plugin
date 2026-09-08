"""Coverage of heartbeat's I/O layer: the subprocess probes, the detached
launch, the log writer, and the `run()`/`main()` driver.

`test_heartbeat.py` covers the pure pieces — `load_config`,
`read_tracker_stats`, `in_off_peak`, `decide`, and one no-tracker `run()`.
What is left is everything that shells out or writes: `scout_session_running`
(pgrep), `vault_has_uncommitted_changes` (git), `run_budget_check`
(scoutctl), `launch_runner` (detached Popen), `_log_line`, and the driver's
launch / dry-run / launch-failure branches.

These matter because heartbeat runs unattended every 30 minutes: a probe that
raises instead of returning a bool turns a routine skip into a launchd
"configuration error", and a swallowed launch failure means Scout silently
stops running sessions.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scout.scripts import heartbeat as hb


def _now() -> datetime:
    return datetime(2026, 5, 28, 14, 0, tzinfo=UTC)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    (v / ".scout-logs").mkdir(parents=True)
    return v


class _Proc:
    """Stand-in for a CompletedProcess."""

    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


# ---------------------------------------------------------------------------
# scout_session_running
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, "4711\n", True),
        (1, "", False),  # pgrep's "no match"
        (0, "   \n", False),  # exit 0 but nothing named — treat as not running
    ],
)
def test_scout_session_running_reads_pgrep(
    monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, expected: bool
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(returncode, stdout))
    assert hb.scout_session_running() is expected


@pytest.mark.parametrize("exc", [OSError("no pgrep"), subprocess.TimeoutExpired("pgrep", 2)])
def test_scout_session_running_treats_a_broken_probe_as_not_running(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """A missing or hung pgrep must not block the heartbeat forever — "unknown"
    resolves to "not running" so a session can still fire."""

    def boom(*_a: object, **_k: object):
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)
    assert hb.scout_session_running() is False


def test_scout_session_running_passes_the_pattern_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.append(argv) or _Proc(1))
    hb.scout_session_running("claude.*custom-")
    assert seen == [["pgrep", "-f", "claude.*custom-"]]


# ---------------------------------------------------------------------------
# vault_has_uncommitted_changes
# ---------------------------------------------------------------------------


def test_vault_without_a_git_dir_reports_no_changes(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No .git means git is never invoked — this is the common case for a vault
    the user hasn't put under version control."""

    def fail(*_a: object, **_k: object):
        raise AssertionError("git must not run without a .git dir")

    monkeypatch.setattr(subprocess, "run", fail)
    assert hb.vault_has_uncommitted_changes(vault) is False


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, " M knowledge-base/people.md\n", True),
        (0, "", False),
        (128, "", False),  # git error (e.g. corrupt repo) -> no signal
    ],
)
def test_vault_has_uncommitted_changes_reads_git_status(
    vault: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str, expected: bool
) -> None:
    (vault / ".git").mkdir()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(returncode, stdout))
    assert hb.vault_has_uncommitted_changes(vault) is expected


@pytest.mark.parametrize("exc", [OSError("no git"), subprocess.TimeoutExpired("git", 5)])
def test_vault_has_uncommitted_changes_swallows_probe_failures(
    vault: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    (vault / ".git").mkdir()

    def boom(*_a: object, **_k: object):
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)
    assert hb.vault_has_uncommitted_changes(vault) is False


# ---------------------------------------------------------------------------
# load_config / read_tracker_stats tolerance
#
# Both parsers are deliberately forgiving: heartbeat must never fail a tick
# over a hand-edited config or a torn tracker append.
# ---------------------------------------------------------------------------


def test_load_config_falls_back_when_the_file_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "scout-config.yaml"
    config.write_text("off_peak_start: 1\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    assert hb.load_config(config) == hb.HeartbeatConfig()


def test_load_config_ignores_non_scalar_and_unknown_lines(tmp_path: Path) -> None:
    config = tmp_path / "scout-config.yaml"
    config.write_text(
        "# a comment\n"
        "\n"
        "instance:\n"  # key with no value -> no match
        "  name: Scout\n"  # known-shaped but not a heartbeat key
        "timezone: America/New_York\n"  # unknown key
        "off_peak_start: 22  # trailing comment\n",
        encoding="utf-8",
    )
    cfg = hb.load_config(config)
    assert cfg.off_peak_start == 22
    assert cfg.off_peak_end == hb.DEFAULT_OFF_PEAK_END


def test_read_tracker_stats_tolerates_blank_and_non_object_rows(tmp_path: Path) -> None:
    tracker = tmp_path / "usage-tracker.jsonl"
    good = _now().replace(hour=12)
    tracker.write_text(
        "\n".join(
            [
                "",
                "   ",
                '"just a string"',  # valid JSON, not a dict
                "[1, 2, 3]",  # valid JSON, not a dict
                json.dumps({"type": "dreaming"}),  # no ts
                json.dumps({"ts": 1700000000, "type": "dreaming"}),  # ts not a string
                json.dumps({"ts": good.isoformat().replace("+00:00", "Z"), "type": "dreaming"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stats = hb.read_tracker_stats(tracker, now=_now())
    assert stats.minutes_since_last_session == 120
    assert stats.hours_since_dreaming == 2


def test_read_tracker_stats_assumes_utc_for_a_naive_timestamp(tmp_path: Path) -> None:
    """Older tracker rows were written without an offset; reading them as local
    time would shift every gap by hours and mis-gate the heartbeat."""
    tracker = tmp_path / "usage-tracker.jsonl"
    tracker.write_text(json.dumps({"ts": "2026-05-28T12:00:00", "type": "dreaming"}) + "\n", encoding="utf-8")
    stats = hb.read_tracker_stats(tracker, now=_now())
    assert stats.minutes_since_last_session == 120


def test_read_tracker_stats_falls_back_when_the_tracker_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracker = tmp_path / "usage-tracker.jsonl"
    tracker.write_text(json.dumps({"ts": "2026-05-28T12:00:00Z"}) + "\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert hb.read_tracker_stats(tracker, now=_now()) == hb.TrackerStats.empty()


# ---------------------------------------------------------------------------
# research_queue_has_unchecked / _item_status edge cases
# ---------------------------------------------------------------------------


def test_research_queue_has_unchecked_survives_an_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = tmp_path / "research-queue.md"
    queue.write_text("- [ ] item\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert hb.research_queue_has_unchecked(queue) is False


def test_item_status_returns_none_for_an_unreadable_item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = tmp_path / "item.md"
    item.write_text("---\nstatus: open\n---\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    assert hb._item_status(item) is None


def test_item_status_returns_none_without_frontmatter(tmp_path: Path) -> None:
    item = tmp_path / "item.md"
    item.write_text("# Just a heading\n\nstatus: open\n", encoding="utf-8")
    assert hb._item_status(item) is None


def test_item_status_returns_none_for_unterminated_frontmatter(tmp_path: Path) -> None:
    """No closing fence means the block is malformed; a `status:` inside it is
    not trustworthy, so the item is not counted as open."""
    item = tmp_path / "item.md"
    item.write_text("---\ntitle: no closing fence\n", encoding="utf-8")
    assert hb._item_status(item) is None


def test_item_status_returns_none_when_frontmatter_has_no_status(tmp_path: Path) -> None:
    item = tmp_path / "item.md"
    item.write_text("---\ntitle: nothing here\n---\n\nbody\n", encoding="utf-8")
    assert hb._item_status(item) is None


def test_item_status_lowercases_the_value(tmp_path: Path) -> None:
    item = tmp_path / "item.md"
    item.write_text("---\nstatus: In-Progress\n---\n", encoding="utf-8")
    assert hb._item_status(item) == "in-progress"


def test_research_queue_has_open_survives_an_unlistable_queue_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_dir = tmp_path / "knowledge-base" / "research-queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "topic.md").write_text("---\nstatus: open\n---\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "glob", boom)
    assert hb.research_queue_has_open(tmp_path) is False


# ---------------------------------------------------------------------------
# run_budget_check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rc", [0, 1, 2])
def test_run_budget_check_forwards_the_exit_code(monkeypatch: pytest.MonkeyPatch, rc: int) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(rc))
    assert hb.run_budget_check("scoutctl") == rc


def test_run_budget_check_resolves_the_binary_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setenv("SCOUTCTL_BIN", "/opt/scout/bin/scoutctl")
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.append(argv) or _Proc(0))
    hb.run_budget_check()
    assert seen == [["/opt/scout/bin/scoutctl", "budget", "check"]]


def test_run_budget_check_defaults_to_scoutctl_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.delenv("SCOUTCTL_BIN", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.append(argv) or _Proc(0))
    hb.run_budget_check()
    assert seen[0][0] == "scoutctl"


@pytest.mark.parametrize("exc", [OSError("not found"), subprocess.TimeoutExpired("scoutctl", 10)])
def test_run_budget_check_does_not_gate_on_a_missing_scoutctl(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """An unreachable budget check must read as "proceed" (0), not "exhausted" —
    otherwise a PATH problem silently stops all sessions."""

    def boom(*_a: object, **_k: object):
        raise exc

    monkeypatch.setattr(subprocess, "run", boom)
    assert hb.run_budget_check("scoutctl") == 0


# ---------------------------------------------------------------------------
# launch_runner
# ---------------------------------------------------------------------------


def test_launch_runner_detaches_and_redirects_into_the_log(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = vault / ".scout-logs" / "heartbeat.log"
    runner = vault / "run-dreaming.sh"
    runner.write_text("#!/bin/sh\nexit 0\n")
    runner.chmod(0o755)

    seen: dict[str, object] = {}

    class FakePopen:
        pid = 4711

        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            seen.update(kwargs)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    assert hb.launch_runner(runner, vault=vault, log_path=log_path) == 4711
    assert seen["argv"] == [str(runner)]
    assert seen["cwd"] == str(vault)
    # Detached: a new session so launchd reaping the heartbeat doesn't kill the
    # child, and no inherited stdin.
    assert seen["start_new_session"] is True
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["stdout"] is seen["stderr"]


def test_launch_runner_creates_the_log_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "nested" / "dirs" / "heartbeat.log"
    runner = tmp_path / "run.sh"
    runner.write_text("#!/bin/sh\n")

    class FakePopen:
        pid = 1

        def __init__(self, *a, **k):
            pass

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    hb.launch_runner(runner, vault=tmp_path, log_path=log_path)
    assert log_path.parent.is_dir()


def test_launch_runner_really_runs_a_script(vault: Path) -> None:
    """One unmocked launch, to prove the Popen wiring works end-to-end."""
    log_path = vault / ".scout-logs" / "heartbeat.log"
    runner = vault / "run-dreaming.sh"
    runner.write_text("#!/bin/sh\necho hello-from-runner\n")
    runner.chmod(0o755)

    pid = hb.launch_runner(runner, vault=vault, log_path=log_path)
    assert pid > 0
    # Reap it so the test doesn't leave a zombie, then read what it wrote.
    try:
        import os

        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass
    assert "hello-from-runner" in log_path.read_text()


# ---------------------------------------------------------------------------
# _log_line
# ---------------------------------------------------------------------------


def test_log_line_appends_a_timestamped_reason(vault: Path) -> None:
    log_path = vault / ".scout-logs" / "heartbeat.log"
    hb._log_line(log_path, "skipped: budget_exhausted")
    hb._log_line(log_path, "launched dreaming PID=1")
    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("[") and "skipped: budget_exhausted" in lines[0]
    assert "launched dreaming PID=1" in lines[1]


def test_log_line_falls_back_when_the_timezone_is_unknown(vault: Path) -> None:
    log_path = vault / ".scout-logs" / "heartbeat.log"
    hb._log_line(log_path, "skipped: x", tz_name="Not/AZone")
    assert "skipped: x" in log_path.read_text()


def test_log_line_falls_back_when_zone_resolution_raises(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The stamp is decoration; the *reason* is the signal. A config layer that
    blows up mid-resolution must still leave a readable log line."""
    import scout.config as scout_config

    def boom(*_a: object, **_k: object):
        raise RuntimeError("config layer exploded")

    monkeypatch.setattr(scout_config, "resolve_timezone", boom)

    log_path = vault / ".scout-logs" / "heartbeat.log"
    hb._log_line(log_path, "skipped: budget_exhausted")
    assert "skipped: budget_exhausted" in log_path.read_text()


def test_log_line_never_raises_on_an_unwritable_log(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Logging is best-effort: an unwritable log must not turn a normal skip
    into a launchd configuration error."""
    log_path = vault / ".scout-logs" / "heartbeat.log"
    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == log_path:
            raise OSError("read-only filesystem")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    hb._log_line(log_path, "skipped: x")  # must not raise


# ---------------------------------------------------------------------------
# run() / main()
# ---------------------------------------------------------------------------


@pytest.fixture
def quiet_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutral probe results: no session running, budget fine, no git changes."""
    monkeypatch.setattr(hb, "scout_session_running", lambda *a, **k: False)
    monkeypatch.setattr(hb, "run_budget_check", lambda *a, **k: 0)
    monkeypatch.setattr(hb, "vault_has_uncommitted_changes", lambda *a, **k: False)


def _seed_runner(vault: Path, name: str) -> Path:
    runner = vault / name
    runner.write_text("#!/bin/sh\nexit 0\n")
    runner.chmod(0o755)
    return runner


def test_run_launches_dreaming_and_logs_the_pid(
    vault: Path, quiet_probes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_runner(vault, "run-dreaming.sh")
    monkeypatch.setattr(hb, "launch_runner", lambda runner, *, vault, log_path: 4711)

    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_LAUNCHED
    log = (vault / ".scout-logs" / "heartbeat.log").read_text()
    assert "launched dreaming PID=4711" in log


def test_run_dry_run_reports_without_launching(
    vault: Path, quiet_probes: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _seed_runner(vault, "run-dreaming.sh")

    def fail(*_a: object, **_k: object):
        raise AssertionError("dry_run must not launch")

    monkeypatch.setattr(hb, "launch_runner", fail)

    assert hb.run(data_dir=vault, dry_run=True, now=_now()) == hb.EXIT_LAUNCHED
    assert f"would_launch {runner}" in capsys.readouterr().out
    assert "dry_run: would launch dreaming" in (vault / ".scout-logs" / "heartbeat.log").read_text()


def test_run_returns_error_when_the_launch_fails(
    vault: Path, quiet_probes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_runner(vault, "run-dreaming.sh")

    def boom(*_a: object, **_k: object):
        raise OSError("exec format error")

    monkeypatch.setattr(hb, "launch_runner", boom)

    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_ERROR
    assert "launch_failed: exec format error" in (vault / ".scout-logs" / "heartbeat.log").read_text()


def test_run_skips_and_logs_when_a_session_is_already_running(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_runner(vault, "run-dreaming.sh")
    monkeypatch.setattr(hb, "scout_session_running", lambda *a, **k: True)
    monkeypatch.setattr(hb, "run_budget_check", lambda *a, **k: 0)
    monkeypatch.setattr(hb, "vault_has_uncommitted_changes", lambda *a, **k: False)

    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_SKIPPED
    assert "skipped: session_already_running" in (vault / ".scout-logs" / "heartbeat.log").read_text()


def test_run_skips_when_the_budget_check_says_no(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_runner(vault, "run-dreaming.sh")
    monkeypatch.setattr(hb, "scout_session_running", lambda *a, **k: False)
    monkeypatch.setattr(hb, "run_budget_check", lambda *a, **k: 1)
    monkeypatch.setattr(hb, "vault_has_uncommitted_changes", lambda *a, **k: False)

    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_SKIPPED
    assert "skipped: budget_exhausted" in (vault / ".scout-logs" / "heartbeat.log").read_text()


def test_run_picks_research_when_the_queue_has_an_open_item(
    vault: Path, quiet_probes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both runners present and both gaps wide: research wins over dreaming."""
    research = _seed_runner(vault, "run-research.sh")
    _seed_runner(vault, "run-dreaming.sh")
    queue_dir = vault / "knowledge-base" / "research-queue"
    queue_dir.mkdir(parents=True)
    (queue_dir / "topic.md").write_text("---\nstatus: open\n---\n", encoding="utf-8")

    launched: list[Path] = []
    monkeypatch.setattr(hb, "launch_runner", lambda runner, *, vault, log_path: launched.append(runner) or 99)

    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_LAUNCHED
    assert launched == [research]
    assert "launched research PID=99" in (vault / ".scout-logs" / "heartbeat.log").read_text()


def test_run_honours_the_vault_off_peak_window(vault: Path, quiet_probes: None) -> None:
    """A vault scout-config.yaml widening the off-peak window must be picked up
    by run() (not just load_config in isolation)."""
    _seed_runner(vault, "run-dreaming.sh")
    (vault / "scout-config.yaml").write_text("off_peak_start: 0\noff_peak_end: 24\n", encoding="utf-8")

    tracker = vault / ".scout-logs" / "usage-tracker.jsonl"
    recent = _now().replace(hour=11)  # 180 min before _now(): past min_gap, under off-peak gap
    tracker.write_text(
        json.dumps({"ts": recent.isoformat().replace("+00:00", "Z"), "type": "dreaming"}) + "\n",
        encoding="utf-8",
    )

    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_SKIPPED
    assert "off_peak_conservatism" in (vault / ".scout-logs" / "heartbeat.log").read_text()


def test_run_skips_when_no_runner_is_executable(vault: Path, quiet_probes: None) -> None:
    (vault / "run-dreaming.sh").write_text("#!/bin/sh\n")  # present but not +x
    assert hb.run(data_dir=vault, now=_now()) == hb.EXIT_SKIPPED
    assert "skipped: no_runner_executable" in (vault / ".scout-logs" / "heartbeat.log").read_text()


def test_run_resolves_the_data_dir_from_the_environment(
    vault: Path, quiet_probes: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    _seed_runner(vault, "run-dreaming.sh")
    monkeypatch.setattr(hb, "launch_runner", lambda runner, *, vault, log_path: 1)
    assert hb.run(now=_now()) == hb.EXIT_LAUNCHED


def test_main_forwards_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    monkeypatch.setattr(hb, "run", lambda *, dry_run: seen.append(dry_run) or hb.EXIT_LAUNCHED)
    assert hb.main(dry_run=True) == hb.EXIT_LAUNCHED
    assert seen == [True]


def test_main_converts_an_unexpected_exception_into_exit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """launchd reads non-zero as a config error, so an unexpected crash must
    still come back as a clean EXIT_ERROR rather than a traceback."""

    def boom(**_k: object):
        raise RuntimeError("unreachable state")

    monkeypatch.setattr(hb, "run", boom)
    assert hb.main() == hb.EXIT_ERROR
