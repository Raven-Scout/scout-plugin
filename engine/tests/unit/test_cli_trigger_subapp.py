"""CLI smoke tests for `scoutctl trigger {list,show,validate,reload,test,fire-now,stats}`.

Mirrors test_cli_schedule_subapp.py. Fixtures are synthetic/anonymized per
CLAUDE.md.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from scout.cli import app

runner = CliRunner()


def _write_triggers(vault: Path, triggers: list[dict]) -> Path:
    state = vault / ".scout-state"
    state.mkdir(parents=True, exist_ok=True)
    p = state / "triggers.yaml"
    p.write_text(yaml.safe_dump({"schema_version": 1, "triggers": triggers}), encoding="utf-8")
    return p


def _notify_trigger(**overrides) -> dict:
    t = {
        "id": "slack_mention_alex",
        "source": "slack",
        "match": {"type": "mention", "user": "U0123456789"},
        "action": {"kind": "notify", "via": ["telegram"]},
        "cooldown_seconds": 0,
        "daily_fire_cap": 200,
    }
    t.update(overrides)
    return t


def _internal_trigger(**overrides) -> dict:
    t = {
        "id": "slot_failures",
        "source": "scout_internal",
        "match": {"type": "slot.fire_failed"},
        "action": {"kind": "interactive"},
        "cooldown_seconds": 0,
        "daily_fire_cap": 10,
    }
    t.update(overrides)
    return t


# ----- list -------------------------------------------------------------------


def test_trigger_list_without_file_reports_none(fake_data_dir: Path):
    result = runner.invoke(app, ["trigger", "list"])
    assert result.exit_code == 0, result.output
    assert "no triggers" in result.output.lower()


def test_trigger_list_tab_separated(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger(), _internal_trigger()])
    result = runner.invoke(app, ["trigger", "list"])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0].startswith("slack_mention_alex\tslack\tmention\tnotify")
    assert "\t" in lines[0]


def test_trigger_list_json_full_records(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    result = runner.invoke(app, ["trigger", "list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["id"] == "slack_mention_alex"
    assert data[0]["source"] == "slack"
    assert data[0]["match"] == {"type": "mention", "user": "U0123456789"}
    assert data[0]["action"]["kind"] == "notify"
    assert data[0]["daily_fire_cap"] == 200
    assert data[0]["enabled"] is True


# ----- show -------------------------------------------------------------------


def test_trigger_show_includes_fire_state(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    result = runner.invoke(app, ["trigger", "show", "slack_mention_alex"])
    assert result.exit_code == 0, result.output
    record = json.loads(result.output)
    assert record["id"] == "slack_mention_alex"
    assert record["cooldown_seconds"] == 0
    assert "fire_state" in record  # dedup-store view (empty before first fire)


def test_trigger_show_unknown_id_exits_nonzero(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    result = runner.invoke(app, ["trigger", "show", "no-such-trigger"])
    assert result.exit_code != 0
    assert "no-such-trigger" in result.output


# ----- validate / reload ---------------------------------------------------------


def test_trigger_validate_ok(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    result = runner.invoke(app, ["trigger", "validate"])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_trigger_validate_without_file_is_ok(fake_data_dir: Path):
    result = runner.invoke(app, ["trigger", "validate"])
    assert result.exit_code == 0, result.output


def test_trigger_validate_rejects_missing_cap(fake_data_dir: Path, tmp_path: Path):
    t = _notify_trigger()
    del t["daily_fire_cap"]
    target = tmp_path / "candidate.yaml"
    target.write_text(yaml.safe_dump({"schema_version": 1, "triggers": [t]}), encoding="utf-8")
    result = runner.invoke(app, ["trigger", "validate", "--target", str(target)])
    assert result.exit_code != 0
    assert "daily_fire_cap" in result.output


def test_trigger_reload_succeeds(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    result = runner.invoke(app, ["trigger", "reload"])
    assert result.exit_code == 0
    assert "reloaded" in result.output.lower()


# ----- test (dry-run matching) -----------------------------------------------------


def test_trigger_test_simulates_against_recent_events(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_internal_trigger()])
    # A fresh slot.fire_failed row in the engine event stream (< 1h old).
    now = dt.datetime.now(tz=dt.UTC)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    log = fake_data_dir / ".scout-logs" / f"schedule-events-{ts[:10]}.jsonl"
    row = {
        "id": "01TESTEVENT00000000000000",
        "ts": ts,
        "kind": "slot.fire_failed",
        "source": "cli:schedule_tick",
        "payload": {"slot_key": "research", "error": "FileNotFoundError"},
    }
    log.write_text(json.dumps(row) + "\n", encoding="utf-8")

    result = runner.invoke(app, ["trigger", "test", "slot_failures"])
    assert result.exit_code == 0, result.output
    assert "01TESTEVENT00000000000000" in result.output
    # Dry-run: nothing dispatched, no fire recorded.
    assert not (fake_data_dir / ".scout-cache" / "trigger-fires.json").exists()
    assert not (fake_data_dir / "needs-attention.md").exists()


def test_trigger_test_unknown_id_exits_nonzero(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    result = runner.invoke(app, ["trigger", "test", "nope"])
    assert result.exit_code != 0


# ----- fire-now ----------------------------------------------------------------------


def test_trigger_fire_now_dispatches_synthetic_event(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_internal_trigger()])
    result = runner.invoke(app, ["trigger", "fire-now", "slot_failures"])
    assert result.exit_code == 0, result.output
    # Interactive action wrote the artifact; the fire was recorded + logged.
    assert (fake_data_dir / "needs-attention.md").exists()
    assert (fake_data_dir / ".scout-cache" / "trigger-fires.json").exists()
    assert list((fake_data_dir / ".scout-logs").glob("trigger-fires-*.jsonl"))


def test_trigger_fire_now_unknown_id_exits_nonzero(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    result = runner.invoke(app, ["trigger", "fire-now", "nope"])
    assert result.exit_code != 0


# ----- stats -------------------------------------------------------------------------


def test_trigger_stats_rolls_up_fire_logs(fake_data_dir: Path):
    log_dir = fake_data_dir / ".scout-logs"
    today = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")

    def row(trigger_id: str, status: str = "ok") -> str:
        return json.dumps(
            {
                "trigger_id": trigger_id,
                "event_id": "x",
                "action_kind": "notify",
                "status": status,
                "detail": {},
                "ts": f"{today}T10:00:00.000Z",
            }
        )

    (log_dir / f"trigger-fires-{today}.jsonl").write_text(
        "\n".join([row("slack_mention_alex"), row("slack_mention_alex", "error"), row("gh_reviews")]) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["trigger", "stats", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["by_trigger"]["slack_mention_alex"]["total"] == 2
    assert data["by_trigger"]["slack_mention_alex"]["error"] == 1
    assert data["by_trigger"]["gh_reviews"]["total"] == 1
    assert data["by_day"][today] == 3
