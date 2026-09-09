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
from scout.scripts.budget_config import show_payload, warn_if_legacy_dotfile

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
