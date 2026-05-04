"""Integration test: full tick against a tmp_path vault + fake clock."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scout.scripts.schedule_tick import run as tick_run


def test_e2e_tick_fires_briefing_on_monday_morning(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    state = tmp_path / ".scout-state"
    state.mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched = state / "schedule.yaml"
    sched.write_text(
        "schema_version: 1\nslots:\n  morning-briefing:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '08:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 4\n    on_miss: fire\n    cooldown_minutes: 60\n"
    )
    et = ZoneInfo("America/New_York")
    fake_now = datetime(2026, 5, 11, 8, 5, tzinfo=et)
    with (
        patch("scout.scripts.schedule_tick._now", return_value=fake_now),
        patch("scout.scripts.schedule_tick._network_ready", return_value=True),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 12345
        ev = tick_run()
    args, kwargs = mock_popen.call_args
    env = kwargs["env"]
    assert env["SCOUT_FORCE_MODE"] == "morning-briefing"
    assert ev.kind == "schedule.tick.completed"
    assert "morning-briefing" in (ev.payload or {}).get("fired", [])


def test_e2e_tick_handles_wake_from_sleep_with_priority_winner(tmp_path, monkeypatch):
    """Wake at 3pm Mon after closed-laptop morning. Briefing wins on priority."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    state = tmp_path / ".scout-state"
    state.mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched = state / "schedule.yaml"
    sched.write_text(
        "schema_version: 1\nslots:\n"
        "  morning-briefing:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '08:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 8\n    on_miss: fire\n    cooldown_minutes: 60\n"
        "  morning-consolidation:\n"
        "    type: consolidation\n    runner: run-scout.sh\n    fires_at_local: '11:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 8\n    on_miss: collapse\n    cooldown_minutes: 90\n"
    )
    et = ZoneInfo("America/New_York")
    fake_now = datetime(2026, 5, 11, 15, 0, tzinfo=et)
    with (
        patch("scout.scripts.schedule_tick._now", return_value=fake_now),
        patch("scout.scripts.schedule_tick._network_ready", return_value=True),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 99
        ev = tick_run()
    assert mock_popen.call_count == 1
    args, kwargs = mock_popen.call_args
    assert kwargs["env"]["SCOUT_FORCE_MODE"] == "morning-briefing"
    assert "morning-briefing" in (ev.payload or {}).get("fired", [])
    assert "morning-consolidation" not in (ev.payload or {}).get("fired", [])
    assert "morning-consolidation" not in (ev.payload or {}).get("skipped", [])
