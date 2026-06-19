"""Heartbeat decision — fires every 30 min via launchd to maybe launch a session.

Port of ``~/Scout/scripts/heartbeat.sh`` (#74 + #79). The bash version ran
three separate ``python3 -c`` invocations against ``usage-tracker.jsonl``
just to extract minutes-since-last-session, hours-since-dreaming, and
hours-since-research — paying ~150–300 ms of cold start each, every
30 minutes, forever. Folded here into one walk of the tracker that emits
all three derived values plus the pgrep / git-status side checks.

Gating order (preserved from bash):
  1. Another ``claude .*scout-`` process already running → skip
  2. Off-peak detection from ``.scout-config.yaml`` (used by gate 5)
  3. Budget check (delegates to ``scoutctl budget check`` so its tracker
     parse is shared with #87's optimization)
  4. Minimum gap since last session (default 120 min)
  5. Off-peak conservatism (skip if off-peak AND <240 min since last)
  6. Work signals — fire only when:
       * >=4 h since last dreaming run OR
       * uncommitted changes in the vault git repo
  7. Pick runner:
       * research, if >=24 h since last research AND research-queue has
         an open item AND ``run-research.sh`` exists
       * otherwise dreaming
  8. Launch the chosen runner detached, log the PID
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scout import paths

# Defaults mirror heartbeat.sh constants so behavior is preserved when no
# .scout-config.yaml is present.
DEFAULT_OFF_PEAK_START = 23
DEFAULT_OFF_PEAK_END = 6
DEFAULT_MIN_GAP_MINUTES = 120
DEFAULT_OFF_PEAK_MIN_GAP_MINUTES = 240
DEFAULT_DREAMING_SIGNAL_HOURS = 4
DEFAULT_RESEARCH_MIN_GAP_HOURS = 24

EXIT_LAUNCHED = 0
EXIT_SKIPPED = 0  # bash returns 0 on intentional skip too
EXIT_ERROR = 1

_TRACKER_FILENAME = "usage-tracker.jsonl"
_CONFIG_FILENAME = ".scout-config.yaml"
_RESEARCH_QUEUE_REL = "knowledge-base/research-queue.md"
_RESEARCH_QUEUE_DIR_REL = "knowledge-base/research-queue"

_CONFIG_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^#\s][^#]*?)\s*(?:#.*)?$")

_CONFIG_KEYS = {
    "off_peak_start": "off_peak_start",
    "off_peak_end": "off_peak_end",
}


# ----- config -------------------------------------------------------------


@dataclass(frozen=True)
class HeartbeatConfig:
    off_peak_start: int = DEFAULT_OFF_PEAK_START
    off_peak_end: int = DEFAULT_OFF_PEAK_END
    min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES
    off_peak_min_gap_minutes: int = DEFAULT_OFF_PEAK_MIN_GAP_MINUTES
    dreaming_signal_hours: int = DEFAULT_DREAMING_SIGNAL_HOURS
    research_min_gap_hours: int = DEFAULT_RESEARCH_MIN_GAP_HOURS


def load_config(config_path: Path) -> HeartbeatConfig:
    """Parse only the two scalar keys heartbeat cares about from .scout-config.yaml.

    Missing file or unparseable values silently fall back to defaults, matching
    the bash original's tolerant ``grep | awk`` pattern.
    """
    if not config_path.exists():
        return HeartbeatConfig()
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return HeartbeatConfig()
    overrides: dict[str, int] = {}
    for line in text.splitlines():
        m = _CONFIG_LINE_RE.match(line)
        if not m:
            continue
        yaml_key, raw_value = m.group(1), m.group(2).strip().strip("\"'")
        field_name = _CONFIG_KEYS.get(yaml_key)
        if field_name is None:
            continue
        try:
            overrides[field_name] = int(raw_value)
        except (TypeError, ValueError):
            continue
    return HeartbeatConfig(**overrides)


# ----- state collection ---------------------------------------------------


@dataclass(frozen=True)
class TrackerStats:
    """Derived recency stats from one walk of usage-tracker.jsonl."""

    minutes_since_last_session: int
    hours_since_dreaming: int
    hours_since_research: int

    @classmethod
    def empty(cls) -> TrackerStats:
        return cls(
            minutes_since_last_session=9999,
            hours_since_dreaming=99,
            hours_since_research=999,
        )


def read_tracker_stats(tracker_path: Path, *, now: datetime | None = None) -> TrackerStats:
    """Compute all three "time since" stats from a single pass of the tracker.

    Replaces the three separate ``python3 -c`` calls in heartbeat.sh — each of
    which opened the same JSONL and iterated every line. Malformed rows are
    silently skipped, same as bash.
    """
    if not tracker_path.exists():
        return TrackerStats.empty()
    n = now or datetime.now(UTC)
    last_any: datetime | None = None
    last_dreaming: datetime | None = None
    last_research: datetime | None = None
    try:
        with tracker_path.open("r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                ts_str = row.get("ts")
                if not isinstance(ts_str, str):
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if last_any is None or ts > last_any:
                    last_any = ts
                row_type = row.get("type")
                if row_type == "dreaming" and (last_dreaming is None or ts > last_dreaming):
                    last_dreaming = ts
                elif row_type == "research" and (last_research is None or ts > last_research):
                    last_research = ts
    except OSError:
        return TrackerStats.empty()
    return TrackerStats(
        minutes_since_last_session=(int((n - last_any).total_seconds() / 60) if last_any else 9999),
        hours_since_dreaming=(int((n - last_dreaming).total_seconds() / 3600) if last_dreaming else 99),
        hours_since_research=(int((n - last_research).total_seconds() / 3600) if last_research else 999),
    )


def scout_session_running(pgrep_pattern: str = "claude.*scout-") -> bool:
    """True iff a ``claude .*scout-`` process is currently alive.

    Wrapped so tests can monkey-patch the subprocess away.
    """
    try:
        proc = subprocess.run(
            ["pgrep", "-f", pgrep_pattern],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() != ""


def vault_has_uncommitted_changes(vault: Path) -> bool:
    """True iff ``git status --porcelain`` in *vault* shows any modifications."""
    if not (vault / ".git").is_dir():
        return False
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and proc.stdout.strip() != ""


def research_queue_has_unchecked(queue_path: Path) -> bool:
    """True iff *queue_path* exists and contains at least one ``- [ ]`` line."""
    if not queue_path.exists():
        return False
    try:
        with queue_path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("- [ ]"):
                    return True
    except OSError:
        return False
    return False


def _item_status(path: Path) -> str | None:
    """Read the frontmatter ``status:`` of a per-file queue item (lowercased), or None."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    # scan the leading frontmatter block (between the first two '---' fences)
    lines = text.splitlines()
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"\s*status\s*:\s*(\S+)", line)
        if m:
            return m.group(1).strip().lower()
    else:
        return None  # no closing fence → malformed frontmatter, treat as no status
    return None  # closing fence found, no status key


def research_queue_has_open(vault: Path) -> bool:
    """True iff the research queue has at least one open item.

    Current per-file format: any ``knowledge-base/research-queue/*.md`` with
    frontmatter ``status: open`` or ``in-progress``. Falls back to the legacy
    single-file ``research-queue.md`` (``- [ ]`` lines) for pre-migration vaults.
    """
    queue_dir = vault / _RESEARCH_QUEUE_DIR_REL
    if queue_dir.is_dir():
        try:
            items = sorted(queue_dir.glob("*.md"))
        except OSError:
            return False
        return any(_item_status(i) in ("open", "in-progress") for i in items)
    # Legacy single-file fallback (pre-migration vaults only):
    return research_queue_has_unchecked(vault / _RESEARCH_QUEUE_REL)


def run_budget_check(scoutctl_bin: str | None = None) -> int:
    """Invoke ``scoutctl budget check`` and return its exit code.

    Delegates so the budget check's tracker parse (#87) is shared. If scoutctl
    can't be found at all, returns 0 (don't gate on a missing optimization).
    """
    if scoutctl_bin is None:
        scoutctl_bin = os.environ.get("SCOUTCTL_BIN", "scoutctl")
    try:
        return subprocess.run(
            [scoutctl_bin, "budget", "check"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 0


# ----- decision -----------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    action: str  # "launch" | "skip"
    runner: Path | None = None
    session_type: str | None = None
    reason: str = ""


def in_off_peak(hour: int, config: HeartbeatConfig) -> bool:
    """Spans midnight: True if hour is at or after off_peak_start OR before off_peak_end."""
    return hour >= config.off_peak_start or hour < config.off_peak_end


def decide(
    *,
    stats: TrackerStats,
    config: HeartbeatConfig,
    now_hour: int,
    budget_ok: bool,
    session_already_running: bool,
    uncommitted_vault_changes: bool,
    research_queue_open: bool,
    research_runner: Path,
    dreaming_runner: Path,
) -> Decision:
    """Pure decision function — given collected state, pick launch / skip.

    Gates run in the same order as heartbeat.sh; the first one that fails
    short-circuits with a reason string the caller can write to the
    heartbeat log.
    """
    if session_already_running:
        return Decision("skip", reason="session_already_running")

    if not budget_ok:
        return Decision("skip", reason="budget_exhausted")

    if stats.minutes_since_last_session < config.min_gap_minutes:
        return Decision(
            "skip",
            reason=(f"last_session_{stats.minutes_since_last_session}m_ago_need_{config.min_gap_minutes}m"),
        )

    off_peak = in_off_peak(now_hour, config)
    if off_peak and stats.minutes_since_last_session < config.off_peak_min_gap_minutes:
        return Decision(
            "skip",
            reason=(
                f"off_peak_conservatism_{stats.minutes_since_last_session}m_ago_need_{config.off_peak_min_gap_minutes}m"
            ),
        )

    has_work = stats.hours_since_dreaming >= config.dreaming_signal_hours or uncommitted_vault_changes
    if not has_work:
        return Decision("skip", reason="no_pending_work_signals")

    pick_research = (
        stats.hours_since_research >= config.research_min_gap_hours
        and research_queue_open
        and research_runner.is_file()
        and os.access(research_runner, os.X_OK)
    )
    if pick_research:
        return Decision(
            "launch",
            runner=research_runner,
            session_type="research",
            reason=(
                f"research_{stats.hours_since_research}h_since_last_dreaming_{stats.hours_since_dreaming}h_since_last"
            ),
        )

    if dreaming_runner.is_file() and os.access(dreaming_runner, os.X_OK):
        return Decision(
            "launch",
            runner=dreaming_runner,
            session_type="dreaming",
            reason=f"dreaming_{stats.hours_since_dreaming}h_since_last",
        )

    return Decision("skip", reason="no_runner_executable")


# ----- launch -------------------------------------------------------------


def launch_runner(runner: Path, *, vault: Path, log_path: Path) -> int:
    """Spawn *runner* fully detached, appending stdout/stderr to *log_path*.

    Mirrors ``nohup "$RUNNER" >> "$HEARTBEAT_LOG" 2>&1 &`` plus the
    schedule_tick.py runner pattern (start_new_session + DEVNULL stdin).
    Returns the child's PID.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            [str(runner)],
            cwd=str(vault),
            env=os.environ.copy(),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
        )
    return proc.pid


# ----- driver -------------------------------------------------------------


def _log_line(log_path: Path, reason: str, tz_name: str = "America/New_York") -> None:
    """Append a timestamped reason line to the heartbeat log."""
    try:
        from zoneinfo import ZoneInfo

        ts = datetime.now(tz=ZoneInfo(tz_name))
        stamp = ts.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        stamp = datetime.now().isoformat(timespec="minutes")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {reason}\n")
    except OSError:
        pass


def run(
    *,
    dry_run: bool = False,
    data_dir: Path | None = None,
    scoutctl_bin: str | None = None,
    now: datetime | None = None,
) -> int:
    """Execute one heartbeat tick. Returns process exit code.

    Always returns 0 on intentional skip — the launchd plist treats non-zero
    as a configuration error, and skipping is normal/expected behavior.
    """
    target = data_dir or paths.data_dir()
    tracker_path = paths.logs_dir(target) / _TRACKER_FILENAME
    config_path = target / _CONFIG_FILENAME
    log_path = paths.logs_dir(target) / "heartbeat.log"

    config = load_config(config_path)
    n = now or datetime.now(UTC)
    stats = read_tracker_stats(tracker_path, now=n)

    research_runner = target / "run-research.sh"
    dreaming_runner = target / "run-dreaming.sh"

    decision = decide(
        stats=stats,
        config=config,
        now_hour=n.astimezone().hour,
        budget_ok=run_budget_check(scoutctl_bin) == 0,
        session_already_running=scout_session_running(),
        uncommitted_vault_changes=vault_has_uncommitted_changes(target),
        research_queue_open=research_queue_has_open(target),
        research_runner=research_runner,
        dreaming_runner=dreaming_runner,
    )

    if decision.action == "skip":
        _log_line(log_path, f"skipped: {decision.reason}")
        return EXIT_SKIPPED

    runner = decision.runner
    assert runner is not None  # "launch" always has a runner
    if dry_run:
        _log_line(log_path, f"dry_run: would launch {decision.session_type} ({decision.reason})")
        print(f"would_launch {runner}")
        return EXIT_LAUNCHED

    try:
        pid = launch_runner(runner, vault=target, log_path=log_path)
    except OSError as exc:
        _log_line(log_path, f"launch_failed: {exc}")
        return EXIT_ERROR
    _log_line(log_path, f"launched {decision.session_type} PID={pid} ({decision.reason})")
    return EXIT_LAUNCHED


def main(*, dry_run: bool = False) -> int:
    try:
        return run(dry_run=dry_run)
    except Exception:
        return EXIT_ERROR


__all__ = [
    "DEFAULT_DREAMING_SIGNAL_HOURS",
    "DEFAULT_MIN_GAP_MINUTES",
    "DEFAULT_OFF_PEAK_END",
    "DEFAULT_OFF_PEAK_MIN_GAP_MINUTES",
    "DEFAULT_OFF_PEAK_START",
    "DEFAULT_RESEARCH_MIN_GAP_HOURS",
    "Decision",
    "HeartbeatConfig",
    "TrackerStats",
    "decide",
    "in_off_peak",
    "launch_runner",
    "load_config",
    "main",
    "read_tracker_stats",
    "research_queue_has_open",
    "research_queue_has_unchecked",
    "run",
    "run_budget_check",
    "scout_session_running",
    "vault_has_uncommitted_changes",
]


# Iterable re-export kept for forward-compat — module-level imports already
# pulled it in via collections.abc.
_ = Iterable
