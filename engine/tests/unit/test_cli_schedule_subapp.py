"""CLI smoke tests for `scoutctl schedule {list,show,validate,init,reload,list-upcoming}`."""

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


# ---------------------------------------------------------------------------
# list-upcoming tests
# ---------------------------------------------------------------------------


def test_list_upcoming_exits_zero():
    result = runner.invoke(app, ["schedule", "list-upcoming"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def test_list_upcoming_json_is_array():
    result = runner.invoke(app, ["schedule", "list-upcoming", "--json"])
    assert result.exit_code == 0, result.stdout
    records = json.loads(result.stdout)
    assert isinstance(records, list)


def test_list_upcoming_json_has_required_fields():
    result = runner.invoke(app, ["schedule", "list-upcoming", "--json"])
    assert result.exit_code == 0
    records = json.loads(result.stdout)
    # With a 24h window there should be at least some slots (defaults fire daily)
    # We just need at least one to validate shape; but if zero, still assert schema.
    for rec in records:
        assert "slot_key" in rec
        assert "slot_type" in rec
        assert "scheduled_at_local" in rec
        assert "scheduled_at_utc" in rec


def test_list_upcoming_json_slot_type_values_are_valid():
    result = runner.invoke(app, ["schedule", "list-upcoming", "--json"])
    assert result.exit_code == 0
    records = json.loads(result.stdout)
    valid_types = {"briefing", "consolidation", "dreaming", "research", "manual"}
    for rec in records:
        assert rec["slot_type"] in valid_types


def test_list_upcoming_json_sorted_alphabetically_by_slot_key():
    result = runner.invoke(app, ["schedule", "list-upcoming", "--json"])
    assert result.exit_code == 0
    records = json.loads(result.stdout)
    keys = [r["slot_key"] for r in records]
    assert keys == sorted(keys), f"Expected alphabetical order, got: {keys}"


def test_list_upcoming_window_zero_returns_empty_array():
    """A zero-hour window should return no upcoming slots."""
    result = runner.invoke(app, ["schedule", "list-upcoming", "--window", "0"])
    assert result.exit_code == 0
    records = json.loads(result.stdout)
    assert records == []


def test_list_upcoming_large_window_returns_all_slots():
    """With a 200h window (~8 days), all 10 default slots should appear."""
    result = runner.invoke(app, ["schedule", "list-upcoming", "--window", "200"])
    assert result.exit_code == 0
    records = json.loads(result.stdout)
    keys = {r["slot_key"] for r in records}
    expected_keys = {
        "morning-briefing",
        "weekend-briefing",
        "morning-consolidation",
        "midday-consolidation",
        "afternoon-consolidation",
        "evening-consolidation",
        "dreaming-evening",
        "dreaming-nightly",
        "dreaming-weekend-morning",
        "research",
    }
    assert keys == expected_keys


def test_list_upcoming_no_json_emits_tab_separated_lines():
    """--no-json should emit tab-separated lines (not JSON)."""
    result = runner.invoke(app, ["schedule", "list-upcoming", "--window", "200", "--no-json"])
    assert result.exit_code == 0
    # Should NOT be parseable as JSON array
    output = result.stdout.strip()
    if output:
        # At least one line; each should have tab separators
        first_line = output.splitlines()[0]
        parts = first_line.split("\t")
        assert len(parts) == 3, f"Expected 3 tab-separated fields, got: {parts}"
        slot_key, slot_type, scheduled_at_local = parts
        assert slot_key  # non-empty
        assert slot_type in {"briefing", "consolidation", "dreaming", "research", "manual"}
        # scheduled_at_local should look like an ISO datetime
        assert "T" in scheduled_at_local or ":" in scheduled_at_local
