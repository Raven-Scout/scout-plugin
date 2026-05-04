"""CLI smoke tests for `scoutctl schedule {list,show,validate,init,reload}`."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from scout.cli import app

runner = CliRunner()


def test_schedule_list_shows_all_default_slots():
    result = runner.invoke(app, ["schedule", "list"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "morning-briefing" in result.stdout
    assert "morning-consolidation" in result.stdout
    assert "dreaming-evening" in result.stdout
    assert "research" in result.stdout


def test_schedule_show_single_slot_returns_full_record():
    result = runner.invoke(app, ["schedule", "show", "morning-briefing"])
    assert result.exit_code == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)
    assert record["key"] == "morning-briefing"
    assert record["type"] == "briefing"
    assert record["fires_at_local"] == "08:00"
    assert record["on_miss"] == "fire"


def test_schedule_show_unknown_slot_exits_nonzero():
    result = runner.invoke(app, ["schedule", "show", "no-such-slot"])
    assert result.exit_code != 0
    assert "no-such-slot" in (result.stdout + result.stderr)


def test_schedule_validate_returns_zero_on_default():
    result = runner.invoke(app, ["schedule", "validate"])
    assert result.exit_code == 0, result.stdout + result.stderr


def test_schedule_init_writes_vault_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["schedule", "init"])
    assert result.exit_code == 0, result.stdout + result.stderr
    written = tmp_path / ".scout-state" / "schedule.yaml"
    assert written.exists()
    assert "morning-briefing" in written.read_text()


def test_schedule_init_refuses_to_overwrite_existing_without_force(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    target = tmp_path / ".scout-state" / "schedule.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# existing user content\n")
    result = runner.invoke(app, ["schedule", "init"])
    assert result.exit_code != 0
    assert "exists" in (result.stdout + result.stderr).lower()
    # Existing content preserved.
    assert target.read_text() == "# existing user content\n"


def test_schedule_init_force_overwrites_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    target = tmp_path / ".scout-state" / "schedule.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("# old content\n")
    result = runner.invoke(app, ["schedule", "init", "--force"])
    assert result.exit_code == 0
    assert "morning-briefing" in target.read_text()


def test_schedule_reload_succeeds():
    result = runner.invoke(app, ["schedule", "reload"])
    assert result.exit_code == 0
    assert "reloaded" in result.stdout.lower()
