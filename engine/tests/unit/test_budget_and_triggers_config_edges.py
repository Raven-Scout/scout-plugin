"""Tolerance and validation branches in `budget_check` and `triggers.config`.

Two modules, two opposite contracts, both under-covered at the edges:

* `budget_check` is deliberately *tolerant* — it gates whether a scheduled
  session runs at all, so a hand-edited config or a torn tracker append must
  fall back to defaults rather than block the run.
* `triggers.config` is deliberately *strict* — a trigger is a thing that fires
  automated actions, so every malformed field must raise a named `ConfigError`
  the operator can act on, never be silently defaulted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.errors import ConfigError
from scout.scripts import budget_check as bc
from scout.scripts.budget_check import (
    EXIT_BACKOFF,
    EXIT_PROCEED,
    EXIT_SKIP_OVER_BUDGET,
    BudgetConfig,
    BudgetDecision,
    decide,
    load_config,
)
from scout.triggers import config as tcfg
from scout.triggers.config import load_triggers

NOW = datetime(2026, 5, 28, 14, 0, tzinfo=UTC)


def _tracker(path: Path, *rows: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _row(minutes_ago: int, **fields) -> dict:
    ts = (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")
    return {"ts": ts, **fields}


# ---------------------------------------------------------------------------
# BudgetDecision / BudgetConfig
# ---------------------------------------------------------------------------


def test_should_proceed_is_true_only_for_the_proceed_code() -> None:
    assert BudgetDecision(EXIT_PROCEED, "ok").should_proceed is True
    assert BudgetDecision(EXIT_SKIP_OVER_BUDGET, "over").should_proceed is False
    assert BudgetDecision(EXIT_BACKOFF, "backoff").should_proceed is False


def test_window_and_threshold_are_derived_from_the_daily_budget() -> None:
    cfg = BudgetConfig(daily_budget_usd=48.0, window_hours=6, skip_threshold_pct=50.0)
    assert cfg.window_budget_usd == 12.0  # 48 * 6 / 24
    assert cfg.skip_threshold_usd == 6.0  # 12 * 50%


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_config_defaults_when_the_file_is_absent(tmp_path: Path) -> None:
    assert load_config(tmp_path / "nope.yaml") == BudgetConfig()


def test_config_defaults_when_the_file_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text("daily_budget_estimate_usd: 10\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", boom)
    assert load_config(cfg) == BudgetConfig()


def test_config_reads_all_four_keys(tmp_path: Path) -> None:
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text(
        "daily_budget_estimate_usd: 12.5\n"
        "rate_limit_window_hours: 3\n"
        "skip_threshold_pct: 60\n"
        "failure_backoff_minutes: 15\n",
        encoding="utf-8",
    )
    assert load_config(cfg) == BudgetConfig(
        daily_budget_usd=12.5, window_hours=3, skip_threshold_pct=60.0, failure_backoff_min=15
    )


def test_config_skips_unparseable_and_irrelevant_lines(tmp_path: Path) -> None:
    """A `scout-config.yaml` is hand-edited and holds far more than these four
    keys — every other shape must be ignored, not crash the gate."""
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text(
        "# a comment\n"
        "\n"
        "instance:\n"  # key with no scalar value
        "  name: Scout\n"  # nested, and not one of our keys
        "timezone: America/New_York\n"  # unknown key
        "daily_budget_estimate_usd: not-a-number\n"  # right key, bad cast
        "rate_limit_window_hours: 3  # trailing comment\n",
        encoding="utf-8",
    )
    cfgv = load_config(cfg)
    assert cfgv.window_hours == 3
    assert cfgv.daily_budget_usd == bc.DEFAULT_DAILY_BUDGET_USD


@pytest.mark.parametrize(
    ("key", "value", "field"),
    [
        ("skip_threshold_pct", "150", "skip_threshold_pct"),  # > 100%
        ("skip_threshold_pct", "-5", "skip_threshold_pct"),
        ("rate_limit_window_hours", "0", "window_hours"),  # a 0h window prices nothing
        ("daily_budget_estimate_usd", "-1", "daily_budget_usd"),
    ],
)
def test_an_out_of_range_override_falls_back_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str, value: str, field: str
) -> None:
    """scout-config.yaml is hand-editable and doubles as bootstrap state. A
    nonsense bound (a 0-hour window, a 150% threshold) would silently mis-gate
    every scheduled run, so it's refused *loudly* and the default stands."""
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text(f"{key}: {value}\n", encoding="utf-8")

    loaded = load_config(cfg)
    assert getattr(loaded, field) == getattr(BudgetConfig(), field)
    err = capsys.readouterr().err
    assert f"ignoring {field}=" in err
    assert "outside the usable range" in err


def test_an_in_range_override_is_honoured(tmp_path: Path) -> None:
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text("skip_threshold_pct: 100\nrate_limit_window_hours: 1\n", encoding="utf-8")
    loaded = load_config(cfg)
    assert loaded.skip_threshold_pct == 100.0
    assert loaded.window_hours == 1


def test_a_nested_override_is_honoured_under_its_own_section(tmp_path: Path) -> None:
    """Keys are matched on (section, key) so an unrelated subtree can't set the
    budget — scout-config.yaml has several producers, and a bare-key scan would
    let a stale duplicate quietly win."""
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text("plan:\n  rate_limit_window_hours: 3\nthresholds:\n  skip_threshold_pct: 60\n", encoding="utf-8")
    loaded = load_config(cfg)
    assert loaded.window_hours == 3
    assert loaded.skip_threshold_pct == 60.0


def test_config_strips_quotes_around_a_value(tmp_path: Path) -> None:
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text('daily_budget_estimate_usd: "7.50"\n', encoding="utf-8")
    assert load_config(cfg).daily_budget_usd == 7.5


# ---------------------------------------------------------------------------
# decide
# ---------------------------------------------------------------------------


def test_decide_proceeds_on_a_first_run(tmp_path: Path) -> None:
    d = decide(tmp_path / "usage-tracker.jsonl", BudgetConfig(), now=NOW)
    assert d.exit_code == EXIT_PROCEED
    assert "first run" in d.reason


def test_decide_backs_off_after_a_recent_rate_limit(tmp_path: Path) -> None:
    """The rate-limit gate uses double the failure-backoff window — a 429 means
    the account is throttled, not just that one session failed."""
    cfg = BudgetConfig(failure_backoff_min=30)
    tracker = _tracker(tmp_path / "usage-tracker.jsonl", _row(50, type="rate_limit"))
    d = decide(tracker, cfg, now=NOW)
    assert d.exit_code == EXIT_BACKOFF
    assert "rate_limit event in last 60m" in d.reason


def test_decide_ignores_a_rate_limit_outside_the_doubled_window(tmp_path: Path) -> None:
    cfg = BudgetConfig(failure_backoff_min=30)
    tracker = _tracker(tmp_path / "usage-tracker.jsonl", _row(90, type="rate_limit"))
    assert decide(tracker, cfg, now=NOW).exit_code == EXIT_PROCEED


def test_decide_backs_off_after_a_recent_nonzero_exit(tmp_path: Path) -> None:
    cfg = BudgetConfig(failure_backoff_min=60)
    tracker = _tracker(tmp_path / "usage-tracker.jsonl", _row(10, exit_code=1, budget_spent=0.1))
    d = decide(tracker, cfg, now=NOW)
    assert d.exit_code == EXIT_BACKOFF
    assert "recent failure 10m ago" in d.reason


def test_decide_proceeds_once_the_failure_backoff_has_elapsed(tmp_path: Path) -> None:
    cfg = BudgetConfig(failure_backoff_min=30)
    tracker = _tracker(tmp_path / "usage-tracker.jsonl", _row(45, exit_code=1, budget_spent=0.1))
    assert decide(tracker, cfg, now=NOW).exit_code == EXIT_PROCEED


def test_decide_skips_when_the_window_cost_reaches_the_threshold(tmp_path: Path) -> None:
    cfg = BudgetConfig(daily_budget_usd=24.0, window_hours=6, skip_threshold_pct=50.0)
    # window budget = 6.00, threshold = 3.00
    tracker = _tracker(
        tmp_path / "usage-tracker.jsonl",
        _row(30, exit_code=0, budget_spent=1.5),
        _row(20, exit_code=0, budget_spent=1.5),
    )
    d = decide(tracker, cfg, now=NOW)
    assert d.exit_code == EXIT_SKIP_OVER_BUDGET
    assert "$3.00 >= skip threshold $3.00" in d.reason


def test_decide_excludes_spend_from_before_the_window(tmp_path: Path) -> None:
    cfg = BudgetConfig(daily_budget_usd=24.0, window_hours=1, skip_threshold_pct=50.0)
    tracker = _tracker(
        tmp_path / "usage-tracker.jsonl",
        _row(600, exit_code=0, budget_spent=99.0),  # long outside the window
        _row(10, exit_code=0, budget_spent=0.1),
    )
    d = decide(tracker, cfg, now=NOW)
    assert d.exit_code == EXIT_PROCEED
    assert "$0.10 spent" in d.reason


def test_decide_tolerates_a_naive_timestamp(tmp_path: Path) -> None:
    """Older tracker rows were written without an offset; reading them as local
    time would shift the whole window."""
    naive = (NOW - timedelta(minutes=10)).replace(tzinfo=None).isoformat()
    tracker = _tracker(tmp_path / "usage-tracker.jsonl", {"ts": naive, "exit_code": 0, "budget_spent": 0.25})
    d = decide(tracker, BudgetConfig(), now=NOW)
    assert "$0.25 spent" in d.reason


def test_decide_skips_rows_with_no_usable_timestamp(tmp_path: Path) -> None:
    tracker = _tracker(
        tmp_path / "usage-tracker.jsonl",
        {"budget_spent": 99.0},  # no ts
        {"ts": "", "budget_spent": 99.0},
        {"ts": 1700000000, "budget_spent": 99.0},  # not a string
        {"ts": "not-a-date", "budget_spent": 99.0},
        _row(10, exit_code=0, budget_spent=0.25),
    )
    d = decide(tracker, BudgetConfig(), now=NOW)
    assert "$0.25 spent" in d.reason


def test_decide_tolerates_uncastable_cost_and_exit_fields(tmp_path: Path) -> None:
    tracker = _tracker(
        tmp_path / "usage-tracker.jsonl",
        _row(10, exit_code="fine", budget_spent="a lot"),
        _row(5, exit_code=0, budget_spent=None),
    )
    d = decide(tracker, BudgetConfig(), now=NOW)
    assert d.exit_code == EXIT_PROCEED
    assert "$0.00 spent" in d.reason


def test_decide_skips_blank_and_malformed_tracker_lines(tmp_path: Path) -> None:
    tracker = tmp_path / "usage-tracker.jsonl"
    tracker.write_text(
        "\n".join(
            [
                "",
                "   ",
                "{torn",
                '"a bare string"',
                "[1, 2]",
                json.dumps(_row(10, exit_code=0, budget_spent=0.25)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert "$0.25 spent" in decide(tracker, BudgetConfig(), now=NOW).reason


def test_decide_proceeds_when_the_tracker_becomes_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A permission problem must not read as "budget exhausted" — that would
    silently stop every session."""
    tracker = _tracker(tmp_path / "usage-tracker.jsonl", _row(10, exit_code=1, budget_spent=99.0))

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert decide(tracker, BudgetConfig(), now=NOW).exit_code == EXIT_PROCEED


def test_run_wires_the_vault_paths_and_prints_when_verbose(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = tmp_path / "Scout"
    (vault / ".scout-logs").mkdir(parents=True)
    (vault / "scout-config.yaml").write_text("rate_limit_window_hours: 3\n", encoding="utf-8")

    assert bc.run(verbose=True, data_dir=vault) == EXIT_PROCEED
    out = capsys.readouterr().out
    assert "[budget-check] no tracker — first run" in out
    assert "window: 3h" in out


def test_run_is_quiet_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault = tmp_path / "Scout"
    (vault / ".scout-logs").mkdir(parents=True)
    assert bc.run(data_dir=vault) == EXIT_PROCEED
    assert capsys.readouterr().out == ""


def test_run_resolves_the_vault_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "Scout"
    (vault / ".scout-logs").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    assert bc.run() == EXIT_PROCEED


# ---------------------------------------------------------------------------
# triggers.config — default_installed_skills
# ---------------------------------------------------------------------------


def test_installed_skills_lists_the_plugins_skill_dirs(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    (skills / "scout-work").mkdir(parents=True)
    (skills / "scout-status").mkdir()
    (skills / "README.md").write_text("not a skill\n", encoding="utf-8")

    assert tcfg.default_installed_skills(tmp_path) == {"scout-work", "scout-status"}


def test_installed_skills_is_empty_without_a_skills_dir(tmp_path: Path) -> None:
    assert tcfg.default_installed_skills(tmp_path) == set()


def test_installed_skills_defaults_to_this_checkout() -> None:
    """No plugin_root argument resolves to the running checkout — the roster a
    `run_skill` action is validated against."""
    assert tcfg.default_installed_skills()


# ---------------------------------------------------------------------------
# triggers.config — load_triggers file-level validation
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "triggers.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_triggers_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_triggers(tmp_path / "nope.yaml")


def test_load_triggers_reports_malformed_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="is malformed"):
        load_triggers(_write(tmp_path, "triggers: [unclosed\n"))


def test_an_empty_file_is_zero_triggers(tmp_path: Path) -> None:
    """An empty triggers.yaml is a legitimate "nothing configured" state, not
    an error — `scoutctl trigger validate` must pass on it."""
    assert load_triggers(_write(tmp_path, ""), installed_skills=set()) == []


@pytest.mark.parametrize("body", ["- a\n- list\n", "just a string\n", "42\n"])
def test_a_non_mapping_file_is_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError, match="is not a mapping"):
        load_triggers(_write(tmp_path, body), installed_skills=set())


def test_an_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="schema_version 99; engine supports 1"):
        load_triggers(_write(tmp_path, "schema_version: 99\ntriggers: []\n"), installed_skills=set())


@pytest.mark.parametrize("value", ["a string", "{a: mapping}", "42"])
def test_a_non_list_triggers_key_is_rejected(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="'triggers' must be a list"):
        load_triggers(_write(tmp_path, f"triggers: {value}\n"), installed_skills=set())


def test_a_non_mapping_trigger_entry_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"trigger\[0\]: expected a mapping, got str"):
        load_triggers(_write(tmp_path, "triggers:\n  - just a string\n"), installed_skills=set())


# ---------------------------------------------------------------------------
# triggers.config — per-trigger field validation
# ---------------------------------------------------------------------------

_VALID = """schema_version: 1
triggers:
  - id: t1
    source: scout_internal
    match: {type: slot.fire_failed}
    action: {kind: notify, tier: info, body: hi}
    daily_fire_cap: 3
"""


def test_a_valid_trigger_loads(tmp_path: Path) -> None:
    triggers = load_triggers(_write(tmp_path, _VALID), installed_skills=set())
    assert [t.id for t in triggers] == ["t1"]
    assert triggers[0].match_type == "slot.fire_failed"
    assert triggers[0].daily_fire_cap == 3
    assert triggers[0].cooldown_seconds == 0
    assert triggers[0].allow_cycle is False
    assert triggers[0].enabled is True


def test_an_action_with_no_kind_is_rejected(tmp_path: Path) -> None:
    body = _VALID.replace("action: {kind: notify, tier: info, body: hi}", "action: {tier: info}")
    with pytest.raises(ConfigError, match="action.kind is required"):
        load_triggers(_write(tmp_path, body), installed_skills=set())


def test_a_non_mapping_action_is_rejected(tmp_path: Path) -> None:
    body = _VALID.replace("action: {kind: notify, tier: info, body: hi}", "action: notify")
    with pytest.raises(ConfigError, match="action.kind is required"):
        load_triggers(_write(tmp_path, body), installed_skills=set())


@pytest.mark.parametrize("value", ["not-a-number", "[1, 2]", "{a: b}", "null"])
def test_a_non_integer_daily_fire_cap_is_rejected(tmp_path: Path, value: str) -> None:
    """The cap is the blast radius on an automated action; a silently-defaulted
    one is unlimited firing."""
    body = _VALID.replace("daily_fire_cap: 3", f"daily_fire_cap: {value}")
    with pytest.raises(ConfigError, match="daily_fire_cap must be an integer"):
        load_triggers(_write(tmp_path, body), installed_skills=set())


@pytest.mark.parametrize("value", ["not-a-number", "[1, 2]", "{a: b}", "null"])
def test_a_non_integer_cooldown_is_rejected(tmp_path: Path, value: str) -> None:
    body = _VALID.replace("daily_fire_cap: 3", f"daily_fire_cap: 3\n    cooldown_seconds: {value}")
    with pytest.raises(ConfigError, match="cooldown_seconds must be an integer"):
        load_triggers(_write(tmp_path, body), installed_skills=set())


def test_a_negative_cooldown_is_rejected(tmp_path: Path) -> None:
    body = _VALID.replace("daily_fire_cap: 3", "daily_fire_cap: 3\n    cooldown_seconds: -5")
    with pytest.raises(ConfigError, match="cooldown_seconds must be >= 0, got -5"):
        load_triggers(_write(tmp_path, body), installed_skills=set())


def test_a_float_cap_is_truncated_not_rejected(tmp_path: Path) -> None:
    body = _VALID.replace("daily_fire_cap: 3", "daily_fire_cap: 3.9")
    assert load_triggers(_write(tmp_path, body), installed_skills=set())[0].daily_fire_cap == 3
