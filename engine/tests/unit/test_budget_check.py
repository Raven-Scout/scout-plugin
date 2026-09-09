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
    SOURCE_DEFAULTS,
    SOURCE_LEGACY,
    SOURCE_VAULT,
    BudgetConfig,
    decide,
    load_config,
    load_config_with_source,
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
    # window_budget = round(50*5/24, 2) = 10.42 ; skip_threshold = round(10.42*0.8, 2) = 8.34
    # (8.34, not 8.33 — the window budget is rounded to cents BEFORE the pct applies)
    tracker.write_text(_row(_now() - timedelta(hours=1), budget_spent=2.0))

    assert decide(tracker, cfg, now=_now()).exit_code == EXIT_PROCEED


def test_decide_skips_when_window_cost_meets_threshold(tmp_path: Path) -> None:
    tracker = tmp_path / "tracker.jsonl"
    cfg = BudgetConfig(daily_budget_usd=50, window_hours=5, skip_threshold_pct=80)
    # threshold $8.34 — push two rows summing to $9.
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


# ----- canonical `budget:` block ------------------------------------------


def test_load_config_reads_canonical_budget_block(tmp_path: Path) -> None:
    path = tmp_path / "scout-config.yaml"
    path.write_text("budget:\n  daily_usd: 200\n  window_hours: 3\n  skip_at_pct: 90\n  failure_backoff_minutes: 30\n")
    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == 200
    assert cfg.window_hours == 3
    assert cfg.skip_threshold_pct == 90
    assert cfg.failure_backoff_min == 30
    assert source == SOURCE_VAULT


def test_legacy_shape_still_works_and_is_reported_as_legacy(tmp_path: Path) -> None:
    path = tmp_path / "scout-config.yaml"
    path.write_text(
        "plan:\n"
        "  daily_budget_estimate_usd: 120\n"
        "  rate_limit_window_hours: 4\n"
        "thresholds:\n"
        "  skip_threshold_pct: 70\n"
        "  failure_backoff_minutes: 15\n"
    )
    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == 120
    assert cfg.window_hours == 4
    assert cfg.skip_threshold_pct == 70
    assert cfg.failure_backoff_min == 15
    assert source == SOURCE_LEGACY


@pytest.mark.parametrize("canonical_first", [True, False])
def test_canonical_beats_legacy_in_either_file_order(tmp_path: Path, canonical_first: bool) -> None:
    """scan_overrides is last-wins within one pass, so a single merged key map
    would let line order pick the winner. Both orderings must resolve canonical.
    """
    canonical = "budget:\n  daily_usd: 200\n  window_hours: 3\n"
    legacy = "plan:\n  daily_budget_estimate_usd: 120\n  rate_limit_window_hours: 4\n"
    path = tmp_path / "scout-config.yaml"
    path.write_text(canonical + legacy if canonical_first else legacy + canonical)

    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == 200
    assert cfg.window_hours == 3
    assert source == SOURCE_VAULT


def test_legacy_fills_knobs_the_canonical_block_omits(tmp_path: Path) -> None:
    """A half-migrated vault must not lose the knobs it hasn't migrated yet."""
    path = tmp_path / "scout-config.yaml"
    path.write_text("budget:\n  daily_usd: 200\nthresholds:\n  failure_backoff_minutes: 15\n")
    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == 200
    assert cfg.failure_backoff_min == 15
    assert cfg.window_hours == DEFAULT_WINDOW_HOURS
    assert source == SOURCE_VAULT


def test_no_budget_config_at_all_reports_defaults(tmp_path: Path) -> None:
    path = tmp_path / "scout-config.yaml"
    path.write_text("user:\n  email: alex@example.com\nplugin:\n  version_at_last_setup: 0.9.0\n")
    cfg, source = load_config_with_source(path)
    assert cfg == BudgetConfig()
    assert source == SOURCE_DEFAULTS


def test_missing_file_reports_defaults(tmp_path: Path) -> None:
    cfg, source = load_config_with_source(tmp_path / "nope.yaml")
    assert cfg == BudgetConfig()
    assert source == SOURCE_DEFAULTS


def test_out_of_range_canonical_value_falls_back_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same tolerance the legacy keys get: `window_hours: 0` yields a $0 window
    budget and would silently skip every session from then on."""
    path = tmp_path / "scout-config.yaml"
    path.write_text("budget:\n  window_hours: 0\n  daily_usd: 200\n")
    cfg, _ = load_config_with_source(path)
    assert cfg.window_hours == DEFAULT_WINDOW_HOURS
    assert cfg.daily_budget_usd == 200
    assert "window_hours" in capsys.readouterr().err


def test_budget_key_under_the_wrong_parent_is_ignored(tmp_path: Path) -> None:
    """The scanner is parent-aware on purpose — scout-config.yaml has many
    subtrees written by several producers, and none of them may set the budget.
    """
    path = tmp_path / "scout-config.yaml"
    path.write_text("connectors:\n  inputs:\n    daily_usd: 999\n")
    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == DEFAULT_DAILY_BUDGET_USD
    assert source == SOURCE_DEFAULTS


def test_load_config_keeps_its_single_return_signature(tmp_path: Path) -> None:
    """run() and the existing tests call load_config(path) -> BudgetConfig."""
    path = tmp_path / "scout-config.yaml"
    path.write_text("budget:\n  daily_usd: 77\n")
    assert load_config(path).daily_budget_usd == 77


def test_verbose_run_names_the_legacy_key_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A vault still on plan:/thresholds: must say so rather than looking canonical."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-logs").mkdir()
    (tmp_path / "scout-config.yaml").write_text("plan:\n  daily_budget_estimate_usd: 120\n")
    run(verbose=True)
    assert "legacy" in capsys.readouterr().out


def test_verbose_run_says_when_running_on_engine_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-logs").mkdir()
    (tmp_path / "scout-config.yaml").write_text("user:\n  email: alex@example.com\n")
    run(verbose=True)
    assert "engine defaults" in capsys.readouterr().out
