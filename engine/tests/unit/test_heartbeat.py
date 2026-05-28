"""Unit tests for scout.scripts.heartbeat.

Closes #79: heartbeat now does one tracker walk instead of three, and folds
all gates into a pure decide() function for testability.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts.heartbeat import (
    DEFAULT_MIN_GAP_MINUTES,
    DEFAULT_OFF_PEAK_END,
    DEFAULT_OFF_PEAK_MIN_GAP_MINUTES,
    DEFAULT_OFF_PEAK_START,
    Decision,
    HeartbeatConfig,
    TrackerStats,
    decide,
    in_off_peak,
    load_config,
    read_tracker_stats,
    research_queue_has_unchecked,
)


# ----- helpers ------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 28, 14, 0, tzinfo=UTC)


def _row(ts: datetime, **fields: object) -> str:
    return json.dumps({"ts": ts.isoformat().replace("+00:00", "Z"), **fields}) + "\n"


def _executable_runner(tmp_path: Path, name: str) -> Path:
    runner = tmp_path / name
    runner.write_text("#!/bin/sh\nexit 0\n")
    runner.chmod(0o755)
    return runner


# ----- config -------------------------------------------------------------


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.off_peak_start == DEFAULT_OFF_PEAK_START
    assert cfg.off_peak_end == DEFAULT_OFF_PEAK_END


def test_load_config_overrides_off_peak_window(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("off_peak_start: 22\noff_peak_end: 8\n")
    cfg = load_config(path)
    assert cfg.off_peak_start == 22
    assert cfg.off_peak_end == 8


def test_load_config_ignores_malformed_values(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("off_peak_start: not-a-number\n")
    cfg = load_config(path)
    assert cfg.off_peak_start == DEFAULT_OFF_PEAK_START


# ----- tracker stats -----------------------------------------------------


def test_read_tracker_stats_empty_when_missing(tmp_path: Path) -> None:
    stats = read_tracker_stats(tmp_path / "absent.jsonl", now=_now())
    assert stats == TrackerStats.empty()


def test_read_tracker_stats_picks_latest_per_type(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    tracker.write_text(
        _row(_now() - timedelta(hours=10), type="dreaming")
        + _row(_now() - timedelta(hours=5), type="dreaming")  # newer
        + _row(_now() - timedelta(hours=30), type="research")
        + _row(_now() - timedelta(minutes=45), type="briefing")  # last-any
    )
    stats = read_tracker_stats(tracker, now=_now())
    assert stats.minutes_since_last_session == 45
    assert stats.hours_since_dreaming == 5
    assert stats.hours_since_research == 30


def test_read_tracker_stats_skips_malformed_rows(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    tracker.write_text(
        "not json\n"
        + _row(_now() - timedelta(minutes=10), type="dreaming")
        + '{"ts": "bad-date", "type": "dreaming"}\n'
    )
    stats = read_tracker_stats(tracker, now=_now())
    assert stats.minutes_since_last_session == 10
    assert stats.hours_since_dreaming == 0


def test_read_tracker_stats_walks_file_exactly_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Regression guard: the previous bash version opened tracker 3x per tick."""
    tracker = tmp_path / "tracker.jsonl"
    tracker.write_text(_row(_now() - timedelta(minutes=5), type="dreaming"))

    open_calls = {"count": 0}
    original_open = Path.open

    def counting_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == tracker:
            open_calls["count"] += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    read_tracker_stats(tracker, now=_now())
    assert open_calls["count"] == 1


# ----- off-peak detection ------------------------------------------------


def test_in_off_peak_default_window() -> None:
    cfg = HeartbeatConfig()  # default 23..6
    assert in_off_peak(23, cfg)
    assert in_off_peak(0, cfg)
    assert in_off_peak(5, cfg)
    assert not in_off_peak(6, cfg)
    assert not in_off_peak(12, cfg)
    assert not in_off_peak(22, cfg)


# ----- research queue ---------------------------------------------------


def test_research_queue_has_unchecked_true(tmp_path: Path) -> None:
    q = tmp_path / "research-queue.md"
    q.write_text("# queue\n- [x] done\n- [ ] todo\n")
    assert research_queue_has_unchecked(q)


def test_research_queue_has_unchecked_false(tmp_path: Path) -> None:
    q = tmp_path / "research-queue.md"
    q.write_text("# queue\n- [x] done only\n")
    assert not research_queue_has_unchecked(q)


def test_research_queue_has_unchecked_missing_file(tmp_path: Path) -> None:
    assert not research_queue_has_unchecked(tmp_path / "absent.md")


# ----- decide() ----------------------------------------------------------


def _base_decide_args(tmp_path: Path) -> dict:
    return {
        "stats": TrackerStats(
            minutes_since_last_session=300,
            hours_since_dreaming=5,
            hours_since_research=2,
        ),
        "config": HeartbeatConfig(),
        "now_hour": 14,  # mid-afternoon, not off-peak
        "budget_ok": True,
        "session_already_running": False,
        "uncommitted_vault_changes": False,
        "research_queue_open": False,
        "research_runner": _executable_runner(tmp_path, "run-research.sh"),
        "dreaming_runner": _executable_runner(tmp_path, "run-dreaming.sh"),
    }


def test_decide_skips_when_session_already_running(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    args["session_already_running"] = True
    decision = decide(**args)
    assert decision.action == "skip"
    assert decision.reason == "session_already_running"


def test_decide_skips_when_budget_exhausted(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    args["budget_ok"] = False
    decision = decide(**args)
    assert decision.action == "skip"
    assert decision.reason == "budget_exhausted"


def test_decide_skips_when_under_min_gap(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    args["stats"] = TrackerStats(
        minutes_since_last_session=60,
        hours_since_dreaming=5,
        hours_since_research=2,
    )
    decision = decide(**args)
    assert decision.action == "skip"
    assert "last_session_60m_ago" in decision.reason


def test_decide_off_peak_conservatism_blocks_recent_session(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    args["now_hour"] = 2  # off-peak
    args["stats"] = TrackerStats(
        minutes_since_last_session=180,  # under off-peak 240 min gap
        hours_since_dreaming=5,
        hours_since_research=2,
    )
    decision = decide(**args)
    assert decision.action == "skip"
    assert "off_peak_conservatism" in decision.reason


def test_decide_skips_when_no_work_signals(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    args["stats"] = TrackerStats(
        minutes_since_last_session=300,
        hours_since_dreaming=2,  # under signal threshold
        hours_since_research=2,
    )
    args["uncommitted_vault_changes"] = False
    decision = decide(**args)
    assert decision.action == "skip"
    assert decision.reason == "no_pending_work_signals"


def test_decide_fires_dreaming_when_signals_present(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    decision = decide(**args)  # hours_since_dreaming=5 >= 4 trigger
    assert decision.action == "launch"
    assert decision.session_type == "dreaming"
    assert decision.runner == args["dreaming_runner"]


def test_decide_picks_research_when_eligible(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    args["stats"] = TrackerStats(
        minutes_since_last_session=300,
        hours_since_dreaming=5,
        hours_since_research=30,  # >= 24 trigger
    )
    args["research_queue_open"] = True
    decision = decide(**args)
    assert decision.action == "launch"
    assert decision.session_type == "research"


def test_decide_skips_when_runners_missing(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    # Remove both runners.
    args["research_runner"].unlink()
    args["dreaming_runner"].unlink()
    decision = decide(**args)
    assert decision.action == "skip"
    assert decision.reason == "no_runner_executable"


def test_decide_fires_dreaming_on_uncommitted_changes_alone(tmp_path: Path) -> None:
    args = _base_decide_args(tmp_path)
    args["stats"] = TrackerStats(
        minutes_since_last_session=300,
        hours_since_dreaming=1,  # under signal threshold
        hours_since_research=2,
    )
    args["uncommitted_vault_changes"] = True
    decision = decide(**args)
    assert decision.action == "launch"
    assert decision.session_type == "dreaming"


# ----- end-to-end via run() ---------------------------------------------


def test_run_skips_cleanly_with_no_tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh vault has no tracker, no runners — decide() returns skip and we exit 0."""
    from scout.scripts import heartbeat as hb

    vault = tmp_path / "vault"
    (vault / ".scout-logs").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    # Stub side-effect helpers so the test stays hermetic.
    monkeypatch.setattr(hb, "scout_session_running", lambda *_, **__: False)
    monkeypatch.setattr(hb, "vault_has_uncommitted_changes", lambda *_: False)
    monkeypatch.setattr(hb, "run_budget_check", lambda *_, **__: 0)
    assert hb.run() == 0
