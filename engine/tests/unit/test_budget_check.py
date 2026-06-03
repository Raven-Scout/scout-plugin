"""Unit tests for scout.scripts.budget_check — ports bash budget-check.sh behavior.

The bash script's exit-code contract is the canonical interface; these tests
exercise it: 0 == proceed, 1 == skip (over budget), 2 == backoff.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts.budget_check import (
    DEFAULT_DAILY_BUDGET_USD,
    DEFAULT_FAILURE_BACKOFF_MIN,
    DEFAULT_SKIP_THRESHOLD_PCT,
    DEFAULT_WINDOW_HOURS,
    EXIT_BACKOFF,
    EXIT_PROCEED,
    EXIT_SKIP_OVER_BUDGET,
    BudgetConfig,
    decide,
    load_config,
    run,
)

# ----- config parsing ------------------------------------------------------


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg.daily_budget_usd == DEFAULT_DAILY_BUDGET_USD
    assert cfg.window_hours == DEFAULT_WINDOW_HOURS
    assert cfg.skip_threshold_pct == DEFAULT_SKIP_THRESHOLD_PCT
    assert cfg.failure_backoff_min == DEFAULT_FAILURE_BACKOFF_MIN


def test_load_config_overrides_each_known_key(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "daily_budget_estimate_usd: 100\n"
        "rate_limit_window_hours: 8\n"
        "skip_threshold_pct: 90\n"
        "failure_backoff_minutes: 30\n"
        "unrelated_key: should_be_ignored\n"
    )
    cfg = load_config(path)
    assert cfg.daily_budget_usd == 100
    assert cfg.window_hours == 8
    assert cfg.skip_threshold_pct == 90
    assert cfg.failure_backoff_min == 30


def test_load_config_silently_skips_bad_values(tmp_path: Path) -> None:
    """Bash uses `grep | awk` and silently falls back on parse failures."""
    path = tmp_path / "config.yaml"
    path.write_text("daily_budget_estimate_usd: not-a-number\nrate_limit_window_hours: 8\n")
    cfg = load_config(path)
    assert cfg.daily_budget_usd == DEFAULT_DAILY_BUDGET_USD
    assert cfg.window_hours == 8


def test_budget_config_derives_window_and_threshold() -> None:
    cfg = BudgetConfig(daily_budget_usd=48, window_hours=6, skip_threshold_pct=50)
    assert cfg.window_budget_usd == pytest.approx(12.0)
    assert cfg.skip_threshold_usd == pytest.approx(6.0)


# ----- decide() ------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, tzinfo=UTC)


def _row(ts: datetime, **fields: object) -> str:
    import json

    return json.dumps({"ts": ts.isoformat().replace("+00:00", "Z"), **fields}) + "\n"


def test_decide_proceeds_when_tracker_missing(tmp_path: Path) -> None:
    decision = decide(tmp_path / "missing.jsonl", BudgetConfig(), now=_now())
    assert decision.exit_code == EXIT_PROCEED


def test_decide_proceeds_when_window_cost_under_threshold(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig(daily_budget_usd=50, window_hours=5, skip_threshold_pct=80)
    # window_budget = 50*5/24 ≈ 10.42 ; skip_threshold = 8.33
    tracker.write_text(_row(_now() - timedelta(hours=1), budget_spent=2.0))

    assert decide(tracker, cfg, now=_now()).exit_code == EXIT_PROCEED


def test_decide_skips_when_window_cost_meets_threshold(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig(daily_budget_usd=50, window_hours=5, skip_threshold_pct=80)
    # threshold ~$8.33 — push two rows summing to $9.
    tracker.write_text(
        _row(_now() - timedelta(hours=2), budget_spent=4.5) + _row(_now() - timedelta(hours=1), budget_spent=4.5)
    )

    decision = decide(tracker, cfg, now=_now())
    assert decision.exit_code == EXIT_SKIP_OVER_BUDGET
    assert "skip threshold" in decision.reason


def test_decide_ignores_rows_outside_window(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig(daily_budget_usd=50, window_hours=5, skip_threshold_pct=80)
    # Old row that would put us over budget if counted.
    tracker.write_text(_row(_now() - timedelta(hours=24), budget_spent=999.0))

    assert decide(tracker, cfg, now=_now()).exit_code == EXIT_PROCEED


def test_decide_backoffs_on_recent_rate_limit(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig(failure_backoff_min=60)
    # 90 min ago is inside the 2× backoff window (120 min).
    tracker.write_text(_row(_now() - timedelta(minutes=90), type="rate_limit"))

    decision = decide(tracker, cfg, now=_now())
    assert decision.exit_code == EXIT_BACKOFF
    assert "rate_limit" in decision.reason


def test_decide_does_not_backoff_on_old_rate_limit(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig(failure_backoff_min=60)
    # 200 min ago is OUTSIDE the 2× backoff window (120 min).
    tracker.write_text(_row(_now() - timedelta(minutes=200), type="rate_limit"))

    assert decide(tracker, cfg, now=_now()).exit_code == EXIT_PROCEED


def test_decide_backoffs_on_recent_failure(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig(failure_backoff_min=60)
    tracker.write_text(_row(_now() - timedelta(minutes=30), exit_code=1, budget_spent=0.0))

    decision = decide(tracker, cfg, now=_now())
    assert decision.exit_code == EXIT_BACKOFF
    assert "recent failure" in decision.reason


def test_decide_does_not_backoff_on_old_failure(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig(failure_backoff_min=60)
    tracker.write_text(_row(_now() - timedelta(minutes=120), exit_code=1, budget_spent=0.0))

    assert decide(tracker, cfg, now=_now()).exit_code == EXIT_PROCEED


def test_decide_skips_malformed_rows(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig()
    tracker.write_text(
        "this is not json\n"
        + _row(_now() - timedelta(hours=1), budget_spent=0.5)
        + "{not even an object: true}\n"
        + '{"ts": "not-a-date", "budget_spent": 99}\n'
    )

    # Single valid row ($0.50) is well under threshold.
    assert decide(tracker, cfg, now=_now()).exit_code == EXIT_PROCEED


# ----- run() — end-to-end through paths ----------------------------------


def test_run_returns_proceed_for_empty_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-logs").mkdir()
    assert run() == EXIT_PROCEED


def test_run_verbose_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-logs").mkdir()
    rc = run(verbose=True)
    captured = capsys.readouterr()
    assert rc == EXIT_PROCEED
    assert "[budget-check]" in captured.out
