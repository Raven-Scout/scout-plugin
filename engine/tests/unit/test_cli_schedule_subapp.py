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


def test_schedule_list_json_emits_full_slot_records():
    """Plan 6's ScheduleEditService consumes this exact JSON shape to populate
    the in-app editor. Each record must have all 11 Slot fields, sorted by
    slot_key alphabetically.
    """
    result = runner.invoke(app, ["schedule", "list", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr

    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 10  # default schedule.yaml ships 10 slots

    # Sorted alphabetically by slot_key.
    keys = [s["key"] for s in data]
    assert keys == sorted(keys)

    # morning-briefing field-shape spot-check (all 11 fields present).
    morning = next(s for s in data if s["key"] == "morning-briefing")
    assert morning["type"] == "briefing"
    assert morning["runner"] == "run-scout.sh"
    assert morning["fires_at_local"] == "08:00"
    assert morning["weekdays"] == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert morning["missed_window_hours"] == 4
    assert morning["on_miss"] == "fire"
    assert morning["cooldown_minutes"] == 60
    assert morning["budget_usd"] is None
    assert morning["tz"] is None
    assert morning["runtime"] == "local"


def test_schedule_list_default_emits_tab_separated():
    """Backward compat: --no-json (default) preserves the original
    tab-separated output for existing terminal users."""
    result = runner.invoke(app, ["schedule", "list"])
    assert result.exit_code == 0
    # Tab-separated, not JSON — first non-empty line should NOT start with `[`.
    first_line = next((line for line in result.stdout.splitlines() if line.strip()), "")
    assert not first_line.startswith("[")
    assert "\t" in first_line


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


# ---------------------------------------------------------------------------
# validate --target tests (Plan 6 Task 3)
# ---------------------------------------------------------------------------


def test_schedule_validate_target_flag_passes_for_valid_yaml(tmp_path):
    """The --target flag points validate at an arbitrary path so the Schedules
    tab editor can validate a candidate before committing via atomic-rename."""
    target = tmp_path / "candidate.yaml"
    target.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  morning-briefing:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    runner = CliRunner()
    result = runner.invoke(app, ["schedule", "validate", "--target", str(target)])
    assert result.exit_code == 0
    assert "schedule OK" in result.output


def test_schedule_validate_target_flag_fails_for_invalid_yaml(tmp_path):
    target = tmp_path / "broken.yaml"
    target.write_text("schema_version: 99\nslots: {}\n")
    runner = CliRunner()
    result = runner.invoke(app, ["schedule", "validate", "--target", str(target)])
    assert result.exit_code == 1
    # ConfigError message is surfaced — both stdout and stderr captures may have it.
    combined = (result.stderr or "") + (result.output or "")
    assert "schema_version" in combined


def test_schedule_validate_target_flag_fails_for_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    runner = CliRunner()
    result = runner.invoke(app, ["schedule", "validate", "--target", str(missing)])
    assert result.exit_code == 1


def test_schedule_validate_no_flag_keeps_default_behavior(tmp_path, monkeypatch):
    """Without --target, validate reads from the vault path (or engine defaults).
    Backward-compat with pre-Plan-6 callers."""
    # Point the vault at an empty tmp_path so the defaults fallback kicks in.
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["schedule", "validate"])
    assert result.exit_code == 0
    assert "schedule OK" in result.output
