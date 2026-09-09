"""CLI + payload tests for `scoutctl budget {show,set}`.

The JSON shape asserted here is the contract scout-app's BudgetSettingsService
decodes. Changing a key name here is a breaking change for the app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts.budget_config import (
    BudgetWriteError,
    apply_updates,
    show_payload,
    validate_updates,
    warn_if_legacy_dotfile,
    write_budget,
)

runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal vault with SCOUT_DATA_DIR pointed at it."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-logs").mkdir()
    return tmp_path


# ----- show_payload --------------------------------------------------------


def test_show_payload_reports_defaults_for_a_bare_vault(vault: Path) -> None:
    payload = show_payload(vault)
    assert payload["source"] == "defaults"
    assert payload["daily_usd"] == 50.0
    assert payload["window_hours"] == 5
    assert payload["skip_at_pct"] == 80.0
    assert payload["failure_backoff_minutes"] == 60
    assert payload["config_path"] == str(vault / "scout-config.yaml")


def test_show_payload_derives_the_gate_with_two_step_rounding(vault: Path) -> None:
    """The engine rounds the window budget to cents BEFORE applying the skip
    percentage. At the defaults the two forms disagree — 8.34 vs a collapsed
    8.33 — and scout-app mirrors this arithmetic, so the intermediate rounding
    is part of the contract, not an implementation detail.
    """
    payload = show_payload(vault)
    assert payload["window_budget_usd"] == 10.42
    assert payload["skip_threshold_usd"] == 8.34


def test_show_payload_reads_a_canonical_block(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        "budget:\n  daily_usd: 200\n  window_hours: 3\n  skip_at_pct: 90\n  failure_backoff_minutes: 30\n"
    )
    payload = show_payload(vault)
    assert payload["source"] == "vault"
    assert payload["daily_usd"] == 200
    assert payload["window_budget_usd"] == 25.0
    assert payload["skip_threshold_usd"] == 22.5


def test_show_payload_reports_legacy_source(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text("plan:\n  daily_budget_estimate_usd: 120\n  rate_limit_window_hours: 4\n")
    payload = show_payload(vault)
    assert payload["source"] == "legacy"
    assert payload["daily_usd"] == 120


# ----- the dead dotfile ----------------------------------------------------


def test_warns_when_the_dotted_config_carries_budget_keys(vault: Path) -> None:
    (vault / ".scout-config.yaml").write_text("plan:\n  daily_budget_estimate_usd: 200\n  rate_limit_window_hours: 3\n")
    warning = warn_if_legacy_dotfile(vault)
    assert warning is not None
    assert ".scout-config.yaml" in warning


def test_no_warning_when_the_dotted_config_is_absent(vault: Path) -> None:
    assert warn_if_legacy_dotfile(vault) is None


def test_no_warning_when_the_dotted_config_has_no_budget_keys(vault: Path) -> None:
    (vault / ".scout-config.yaml").write_text("schedule:\n  off_peak_start: 23\n")
    assert warn_if_legacy_dotfile(vault) is None


# ----- CLI -----------------------------------------------------------------


def test_budget_show_json_emits_the_payload(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "show", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert set(data) == {
        "config_path",
        "source",
        "daily_usd",
        "window_hours",
        "skip_at_pct",
        "failure_backoff_minutes",
        "window_budget_usd",
        "skip_threshold_usd",
    }


def test_budget_show_json_stays_parseable_with_the_dotfile_warning(vault: Path) -> None:
    """The warning goes to stderr so --json stdout is machine-readable."""
    (vault / ".scout-config.yaml").write_text("plan:\n  daily_budget_estimate_usd: 200\n")
    result = runner.invoke(app, ["budget", "show", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["source"] == "defaults"
    assert ".scout-config.yaml" in result.stderr


def test_budget_show_human_output_names_the_gate(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "show"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "8.34" in result.stdout
    assert "defaults" in result.stdout


# ----- apply_updates (pure) ------------------------------------------------

_MULTI_PRODUCER_CONFIG = """\
# Scout vault config — hand-edited and bootstrap-written.
user:
  name: Alex
  email: alex@example.com

budget:
  # Calibrated from tracked sessions.
  daily_usd: 150
  window_hours: 5
  skip_at_pct: 80
  failure_backoff_minutes: 60

plugin:
  version_at_last_setup: 0.9.0
  applied_migrations: []
"""


def test_apply_updates_rewrites_only_the_named_lines() -> None:
    out = apply_updates(_MULTI_PRODUCER_CONFIG, {"daily_budget_usd": 200})
    assert "  daily_usd: 200\n" in out
    # Everything else survives byte-for-byte.
    assert "  window_hours: 5\n" in out
    assert "  # Calibrated from tracked sessions.\n" in out
    assert "  version_at_last_setup: 0.9.0\n" in out
    assert "# Scout vault config — hand-edited and bootstrap-written.\n" in out
    assert out.count("budget:") == 1


def test_apply_updates_leaves_the_file_untouched_when_nothing_changes() -> None:
    assert apply_updates(_MULTI_PRODUCER_CONFIG, {"daily_budget_usd": 150}) == _MULTI_PRODUCER_CONFIG


def test_apply_updates_writes_integers_without_a_trailing_zero() -> None:
    out = apply_updates(_MULTI_PRODUCER_CONFIG, {"skip_threshold_pct": 90.0})
    assert "  skip_at_pct: 90\n" in out
    assert "90.0" not in out


def test_apply_updates_keeps_a_genuine_fraction() -> None:
    out = apply_updates(_MULTI_PRODUCER_CONFIG, {"daily_budget_usd": 12.5})
    assert "  daily_usd: 12.5\n" in out


def test_apply_updates_inserts_a_key_the_block_lacks() -> None:
    partial = "budget:\n  daily_usd: 150\n\nplugin:\n  version_at_last_setup: 0.9.0\n"
    out = apply_updates(partial, {"window_hours": 3})
    assert "  daily_usd: 150\n  window_hours: 3\n" in out
    assert "plugin:\n" in out


def test_apply_updates_appends_a_complete_block_when_absent() -> None:
    out = apply_updates("user:\n  name: Alex\n", {"daily_budget_usd": 200})
    assert "user:\n  name: Alex\n" in out
    assert "budget:\n" in out
    assert "  daily_usd: 200\n" in out
    # Unspecified knobs are written at their defaults so the block is complete.
    assert "  window_hours: 5\n" in out
    assert "  skip_at_pct: 80\n" in out
    assert "  failure_backoff_minutes: 60\n" in out


def test_apply_updates_appends_to_an_empty_file() -> None:
    out = apply_updates("", {"daily_budget_usd": 200})
    assert "budget:\n" in out
    assert "  daily_usd: 200\n" in out


def test_apply_updates_ignores_a_budget_key_under_another_parent() -> None:
    """`daily_usd` nested under connectors must not be mistaken for the block."""
    text = "connectors:\n  inputs:\n    daily_usd: 1\n"
    out = apply_updates(text, {"daily_budget_usd": 200})
    assert "    daily_usd: 1\n" in out
    assert "budget:\n" in out
    assert "  daily_usd: 200\n" in out


# ----- validate_updates ----------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("window_hours", 0),
        ("window_hours", -1),
        ("skip_threshold_pct", 101),
        ("skip_threshold_pct", -1),
        ("daily_budget_usd", -5),
        ("failure_backoff_min", -1),
    ],
)
def test_validate_updates_rejects_out_of_range(field: str, value: float) -> None:
    """The read path tolerates a bad value so a mangled vault can't block a run.
    A `set` is a deliberate action, so the same value is an error — silently
    storing something else would show the user a number the gate ignores.
    """
    with pytest.raises(BudgetWriteError) as excinfo:
        validate_updates({field: value})
    assert str(value) in str(excinfo.value)


def test_validate_updates_accepts_the_boundaries() -> None:
    validate_updates({"daily_budget_usd": 0, "skip_threshold_pct": 100, "window_hours": 1})


# ----- write_budget --------------------------------------------------------


def test_write_budget_persists_and_returns_the_new_payload(vault: Path) -> None:
    payload = write_budget(
        {
            "daily_budget_usd": 200,
            "window_hours": 3,
            "skip_threshold_pct": 90,
            "failure_backoff_min": 30,
        },
        data_dir=vault,
    )
    assert payload["source"] == "vault"
    assert payload["daily_usd"] == 200
    assert payload["skip_threshold_usd"] == 22.5
    assert "daily_usd: 200" in (vault / "scout-config.yaml").read_text()


def test_write_budget_is_a_partial_update(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        "budget:\n  daily_usd: 150\n  window_hours: 5\n  skip_at_pct: 80\n  failure_backoff_minutes: 60\n"
    )
    write_budget({"daily_budget_usd": 200}, data_dir=vault)
    text = (vault / "scout-config.yaml").read_text()
    assert "daily_usd: 200" in text
    assert "window_hours: 5" in text
    assert "skip_at_pct: 80" in text


def test_write_budget_rejects_out_of_range_before_touching_the_file(vault: Path) -> None:
    original = "budget:\n  daily_usd: 150\n"
    (vault / "scout-config.yaml").write_text(original)
    with pytest.raises(BudgetWriteError):
        write_budget({"skip_threshold_pct": 500}, data_dir=vault)
    assert (vault / "scout-config.yaml").read_text() == original


def test_write_budget_leaves_no_tmp_files(vault: Path) -> None:
    write_budget({"daily_budget_usd": 200}, data_dir=vault)
    assert [p.name for p in vault.iterdir() if p.name.endswith(".tmp")] == []


def test_write_budget_retries_when_a_concurrent_writer_lands(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """scout-config.yaml has several producers — bootstrap writes version stamps
    and connectors into it. When the file changes between our read and our
    replace, the writer must re-read and re-apply rather than clobber.

    Driven through `_mtime_ns` rather than by mutating the file from a patched
    `read_text`: write_budget samples the mtime *after* reading, so a mutation
    staged at read time is already reflected in the sample and the guard would
    correctly see no change. Forcing the second call (the pre-replace check) to
    disagree is what actually exercises the retry branch.
    """
    from scout.scripts import budget_config

    config_path = vault / "scout-config.yaml"
    config_path.write_text("budget:\n  daily_usd: 150\n")

    calls = {"n": 0}
    real_mtime_ns = budget_config._mtime_ns

    def mtime_with_one_collision(path: Path) -> int | None:
        # Two calls per attempt: the read-time sample, then the pre-replace
        # check. On call 2 report a change and land the concurrent write, so the
        # retry reads merged content.
        calls["n"] += 1
        if calls["n"] == 2:
            config_path.write_text(config_path.read_text() + "plugin:\n  version_at_last_setup: 0.9.0\n")
            return -1
        return real_mtime_ns(path)

    monkeypatch.setattr(budget_config, "_mtime_ns", mtime_with_one_collision)
    write_budget({"daily_budget_usd": 200}, data_dir=vault)

    final = config_path.read_text()
    assert "daily_usd: 200" in final
    assert "version_at_last_setup: 0.9.0" in final  # the concurrent write survived
    assert calls["n"] >= 3  # proves a second attempt happened


# ----- CLI set -------------------------------------------------------------


def test_budget_set_writes_and_reports(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "set", "--daily-usd", "200", "--window-hours", "3", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["daily_usd"] == 200
    assert data["window_hours"] == 3
    assert data["source"] == "vault"


def test_budget_set_with_no_flags_exits_2(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "set"])
    assert result.exit_code == 2
    assert "at least one" in result.stderr


def test_budget_set_out_of_range_exits_1_and_names_the_knob(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "set", "--skip-at-pct", "500"])
    assert result.exit_code == 1
    assert "skip_at_pct" in result.stderr
    assert not (vault / "scout-config.yaml").exists()


def test_budget_set_then_show_round_trips(vault: Path) -> None:
    """A partial `set` on a bare vault appends a COMPLETE block, so the knobs it
    did not name keep the engine defaults — skip_at_pct stays 80, making the
    gate round(25.00 * 0.80, 2) = 20.00 rather than the 22.50 a 90% cut gives.
    That completeness is the point: the file states the whole gate instead of a
    partial override plus invisible defaults.
    """
    assert runner.invoke(app, ["budget", "set", "--daily-usd", "200", "--window-hours", "3"]).exit_code == 0
    result = runner.invoke(app, ["budget", "show", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert (data["daily_usd"], data["window_hours"]) == (200, 3)
    assert data["skip_at_pct"] == 80
    assert data["failure_backoff_minutes"] == 60
    assert data["window_budget_usd"] == 25.0
    assert data["skip_threshold_usd"] == 20.0


def test_budget_set_all_four_reaches_the_calibrated_gate(vault: Path) -> None:
    result = runner.invoke(
        app,
        [
            "budget",
            "set",
            "--daily-usd",
            "200",
            "--window-hours",
            "3",
            "--skip-at-pct",
            "90",
            "--failure-backoff-minutes",
            "30",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["skip_threshold_usd"] == 22.5
