"""Unit tests for scout.scripts.schedule_tick — the dispatcher brain."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from scout.errors import ConfigError
from scout.events import Event
from scout.schedule import (
    OnMissPolicy,
    Slot,
    SlotRuntime,
    SlotType,
    load_default_schedule,
)
from scout.scripts.schedule_tick import (
    Decision,
    SlotCandidate,
    _apply_miss_rules,
    _compute_due_slots,
    _filter_winner_by_priority,
    _network_ready,
    _read_last_fire_index,
    _spawn_runner,
    candidates_by_key,
)
from scout.scripts.schedule_tick import (
    run as tick_run,
)


# Helper for synthesizing slots in tests.
def _slot(
    key: str,
    *,
    type_: SlotType = SlotType.CONSOLIDATION,
    fires_at: str = "11:00",
    weekdays: tuple = ("Mon", "Tue", "Wed", "Thu", "Fri"),
    missed_window_hours: int = 2,
    on_miss: OnMissPolicy = OnMissPolicy.COLLAPSE,
    cooldown_minutes: int = 90,
) -> Slot:
    return Slot(
        key=key,
        type=type_,
        runner="run-scout.sh",
        fires_at_local=fires_at,
        weekdays=weekdays,
        missed_window_hours=missed_window_hours,
        on_miss=on_miss,
        cooldown_minutes=cooldown_minutes,
    )


# 0. candidates_by_key: indexer used by callers to look up candidate metadata.


def test_candidates_by_key_indexes_by_slot_key():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    target = datetime(2026, 5, 11, 8, 0, tzinfo=et)
    cand = SlotCandidate(
        slot_key="morning-briefing",
        slot=sched["morning-briefing"],
        target=target,
        last_fire=None,
    )
    indexed = candidates_by_key([cand])
    assert indexed["morning-briefing"].target == target


# 1. compute_due_slots: only slots whose target time has passed and last fire is older than today's target.


def test_compute_due_slots_includes_slot_past_target_with_no_prior_fire():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 11, 30, tzinfo=et)
    last_fire = {}
    candidates = _compute_due_slots(sched, last_fire, now)
    keys = {c.slot_key for c in candidates}
    assert "morning-briefing" in keys
    assert "morning-consolidation" in keys
    assert "midday-consolidation" not in keys


def test_compute_due_slots_skips_slot_within_cooldown():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 11, 30, tzinfo=et)
    last_fire = {"morning-consolidation": now - timedelta(minutes=30)}
    candidates = _compute_due_slots(sched, last_fire, now)
    assert "morning-consolidation" not in {c.slot_key for c in candidates}


def test_compute_due_slots_excludes_slot_with_today_fire():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 11, 30, tzinfo=et)
    last_fire = {"morning-consolidation": datetime(2026, 5, 11, 11, 5, tzinfo=et)}
    candidates = _compute_due_slots(sched, last_fire, now)
    assert "morning-consolidation" not in {c.slot_key for c in candidates}


# 2. apply_miss_rules: on_miss policy + missed_window + collapse-within-type semantics.


def test_apply_miss_rules_fire_within_window():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 11, 30, tzinfo=et)
    candidate = SlotCandidate(
        slot_key="morning-briefing",
        slot=sched["morning-briefing"],
        target=datetime(2026, 5, 11, 8, 0, tzinfo=et),
        last_fire=None,
    )
    decisions = _apply_miss_rules([candidate], now=now)
    assert decisions["morning-briefing"].action == "fire"


def test_apply_miss_rules_fire_outside_window_skips():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 14, 0, tzinfo=et)
    candidate = SlotCandidate(
        slot_key="morning-briefing",
        slot=sched["morning-briefing"],
        target=datetime(2026, 5, 11, 8, 0, tzinfo=et),
        last_fire=None,
    )
    decisions = _apply_miss_rules([candidate], now=now)
    assert decisions["morning-briefing"].action == "skip"
    assert "stale" in decisions["morning-briefing"].reason


def test_apply_miss_rules_skip_policy_always_skips():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 14, 30, tzinfo=et)
    candidate = SlotCandidate(
        slot_key="research",
        slot=sched["research"],
        target=datetime(2026, 5, 11, 14, 0, tzinfo=et),
        last_fire=None,
    )
    decisions = _apply_miss_rules([candidate], now=now)
    assert decisions["research"].action == "skip"
    assert "on_miss=skip" in decisions["research"].reason


def test_apply_miss_rules_collapse_within_type_fires_only_latest():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 17, 30, tzinfo=et)
    morning = SlotCandidate(
        "morning-consolidation",
        sched["morning-consolidation"],
        target=datetime(2026, 5, 11, 11, 0, tzinfo=et),
        last_fire=None,
    )
    midday = SlotCandidate(
        "midday-consolidation",
        sched["midday-consolidation"],
        target=datetime(2026, 5, 11, 13, 0, tzinfo=et),
        last_fire=None,
    )
    afternoon = SlotCandidate(
        "afternoon-consolidation",
        sched["afternoon-consolidation"],
        target=datetime(2026, 5, 11, 17, 0, tzinfo=et),
        last_fire=None,
    )
    decisions = _apply_miss_rules([morning, midday, afternoon], now=now)
    assert decisions["afternoon-consolidation"].action == "fire"
    assert decisions["morning-consolidation"].action == "skip"
    assert "collapsed-into=afternoon-consolidation" in decisions["morning-consolidation"].reason
    assert decisions["midday-consolidation"].action == "skip"


def test_apply_miss_rules_collapse_respects_window_for_oldest():
    sched = load_default_schedule()
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 11, 19, 30, tzinfo=et)
    morning = SlotCandidate(
        "morning-consolidation",
        sched["morning-consolidation"],
        target=datetime(2026, 5, 11, 11, 0, tzinfo=et),
        last_fire=None,
    )
    evening = SlotCandidate(
        "evening-consolidation",
        sched["evening-consolidation"],
        target=datetime(2026, 5, 11, 19, 0, tzinfo=et),
        last_fire=None,
    )
    decisions = _apply_miss_rules([morning, evening], now=now)
    assert decisions["evening-consolidation"].action == "fire"
    assert decisions["morning-consolidation"].action == "skip"


# 3. priority filter: at most one fire per tick.


def test_filter_winner_by_priority_picks_briefing_over_consolidation():
    sched = load_default_schedule()
    decisions = {
        "morning-briefing": Decision(action="fire"),
        "afternoon-consolidation": Decision(action="fire"),
    }
    winner = _filter_winner_by_priority(sched, decisions)
    assert winner == "morning-briefing"


def test_filter_winner_by_priority_picks_consolidation_when_no_briefing():
    sched = load_default_schedule()
    decisions = {
        "afternoon-consolidation": Decision(action="fire"),
        "dreaming-evening": Decision(action="fire"),
    }
    winner = _filter_winner_by_priority(sched, decisions)
    assert winner == "afternoon-consolidation"


def test_filter_winner_returns_none_when_no_fire_decisions():
    sched = load_default_schedule()
    decisions = {
        "morning-briefing": Decision(action="skip", reason="stale"),
    }
    assert _filter_winner_by_priority(sched, decisions) is None


# 4. network probe.


def test_network_ready_returns_true_when_probe_succeeds():
    with patch("scout.scripts.schedule_tick.socket.create_connection") as mock_conn:
        mock_conn.return_value.__enter__ = lambda s: None
        mock_conn.return_value.__exit__ = lambda *args: None
        assert _network_ready(retries=1, sleep_seconds=0) is True


def test_network_ready_returns_false_after_exhausting_retries():
    with patch("scout.scripts.schedule_tick.socket.create_connection", side_effect=OSError("dns")):
        assert _network_ready(retries=2, sleep_seconds=0) is False


# 5. tracker reading.


def test_read_last_fire_index_extracts_per_slot_last_ts(tmp_path):
    log_dir = tmp_path / ".scout-logs"
    log_dir.mkdir()
    tracker = log_dir / "usage-tracker.jsonl"
    tracker.write_text(
        '{"ts":"2026-05-11T12:00:00Z","type":"briefing","scout_mode":"morning-briefing"}\n'
        '{"ts":"2026-05-11T15:00:00Z","type":"consolidation","scout_mode":"morning-consolidation"}\n'
        '{"ts":"2026-05-11T17:00:00Z","type":"consolidation","scout_mode":"afternoon-consolidation"}\n'
    )
    index = _read_last_fire_index(tracker)
    assert "morning-briefing" in index
    assert "afternoon-consolidation" in index
    assert index["morning-briefing"] < index["afternoon-consolidation"]


def test_read_last_fire_index_is_empty_when_tracker_missing(tmp_path):
    assert _read_last_fire_index(tmp_path / "no.jsonl") == {}


def test_read_last_fire_index_applies_legacy_mode_rename(tmp_path):
    """Legacy session-tokens.jsonl with old mode names → renamed to new slot keys."""
    log_dir = tmp_path / ".scout-logs"
    log_dir.mkdir()
    session_tokens = log_dir / "session-tokens.jsonl"
    session_tokens.write_text(
        '{"ts":"2026-05-11T15:00:00Z","scout_mode":"consolidation-11am"}\n'
        '{"ts":"2026-05-11T17:00:00Z","scout_mode":"consolidation-1pm"}\n'
        '{"ts":"2026-05-11T21:00:00Z","scout_mode":"morning-briefing"}\n'
    )
    tracker = log_dir / "usage-tracker.jsonl"
    tracker.write_text("")
    index = _read_last_fire_index(tracker)
    # Old names mapped to new keys.
    assert "morning-consolidation" in index
    assert "midday-consolidation" in index
    assert "morning-briefing" in index
    assert "consolidation-11am" not in index  # not present under old name


def test_read_last_fire_index_falls_through_legacy_usage_tracker_rows(tmp_path):
    """Legacy usage-tracker rows lacking scout_mode are silently skipped."""
    log_dir = tmp_path / ".scout-logs"
    log_dir.mkdir()
    tracker = log_dir / "usage-tracker.jsonl"
    tracker.write_text(
        # Pre-Plan-5 rows from write-session-cost.sh / heartbeat.sh (no scout_mode):
        '{"ts":"2026-05-01T12:00:00Z","type":"briefing","budget_cap":150}\n'
        '{"ts":"2026-05-01T13:00:00Z","type":"heartbeat","source":"heartbeat"}\n'
    )
    index = _read_last_fire_index(tracker)
    assert index == {}  # silently skipped; no error


# 6. End-to-end: run() emits Event and writes JSONL row.


def test_run_emits_schedule_tick_completed_event(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched_path = tmp_path / ".scout-state" / "schedule.yaml"
    sched_path.write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: manual\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: skip\n    cooldown_minutes: 5\n"
    )
    with (
        patch("scout.scripts.schedule_tick._network_ready", return_value=True),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 99999
        ev = tick_run()
    assert isinstance(ev, Event)
    assert ev.kind == "schedule.tick.completed"


def test_run_skips_when_network_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched_path = tmp_path / ".scout-state" / "schedule.yaml"
    sched_path.write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: fire\n    cooldown_minutes: 5\n"
    )
    with (
        patch("scout.scripts.schedule_tick._network_ready", return_value=False),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen,
    ):
        ev = tick_run()
    mock_popen.assert_not_called()
    assert ev.kind == "schedule.tick.completed"
    skipped_log = next((tmp_path / ".scout-logs").glob("schedule-events-*.jsonl"), None)
    assert skipped_log is not None
    events = [json.loads(line) for line in skipped_log.read_text().splitlines()]
    skip_kinds = [e for e in events if e["kind"] == "slot.skipped"]
    assert any("network-offline" in (e.get("payload") or {}).get("reason", "") for e in skip_kinds)


def test_run_lock_held_returns_quickly(tmp_path, monkeypatch):
    """Concurrency guard: a held flock causes the second tick to early-exit."""
    import fcntl

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    lock_path = tmp_path / ".scout-state" / ".schedule-tick.lock"
    lock_path.touch()
    with open(lock_path, "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ev = tick_run()
    assert ev.kind == "schedule.tick.skipped"
    assert ev.payload.get("reason") == "lock_held"


# 7. v0.5+ event-taxonomy compliance: payload fields per spec §9.


def test_slot_fired_event_has_full_payload_per_v0_5_spec(tmp_path, monkeypatch):
    """slot.fired payload must contain every v0.5+ spec field."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched_path = tmp_path / ".scout-state" / "schedule.yaml"
    sched_path.write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: fire\n    cooldown_minutes: 5\n"
    )
    with (
        patch("scout.scripts.schedule_tick._network_ready", return_value=True),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 42
        tick_run()
    log = next((tmp_path / ".scout-logs").glob("schedule-events-*.jsonl"))
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    fired_events = [r for r in rows if r["kind"] == "slot.fired"]
    assert len(fired_events) == 1
    payload = fired_events[0]["payload"]
    # All v0.5+ spec fields present.
    for f in ("slot_key", "slot_type", "target_local", "target_utc", "runner", "pid_spawned"):
        assert f in payload, f"{f} missing from slot.fired payload: {payload}"
    assert fired_events[0]["source"] == "cli:schedule_tick"


def test_slot_skipped_event_has_slot_type_and_target_local(tmp_path, monkeypatch):
    """slot.skipped payload must contain slot_type and target_local per v0.5+ spec."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched_path = tmp_path / ".scout-state" / "schedule.yaml"
    sched_path.write_text(
        "schema_version: 1\nslots:\n  research-slot:\n"
        "    type: research\n    runner: run-research.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: skip\n    cooldown_minutes: 5\n"
    )
    with patch("scout.scripts.schedule_tick._network_ready", return_value=True):
        tick_run()
    log = next((tmp_path / ".scout-logs").glob("schedule-events-*.jsonl"))
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    skipped = [r for r in rows if r["kind"] == "slot.skipped"]
    assert len(skipped) >= 1
    payload = skipped[0]["payload"]
    for f in ("slot_key", "slot_type", "target_local", "reason"):
        assert f in payload


# 8. fire_now() coverage.


def test_fire_now_unknown_slot_emits_fire_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched = tmp_path / ".scout-state" / "schedule.yaml"
    sched.write_text(
        "schema_version: 1\nslots:\n  any-slot:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n    missed_window_hours: 4\n    on_miss: fire\n    cooldown_minutes: 60\n"
    )
    from scout.scripts.schedule_tick import fire_now

    ev = fire_now("no-such-slot")
    assert ev.kind == "slot.fire_failed"
    assert "unknown" in (ev.payload.get("error") or "").lower()


def test_fire_now_lock_held_emits_fire_failed(tmp_path, monkeypatch):
    import fcntl

    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched = tmp_path / ".scout-state" / "schedule.yaml"
    sched.write_text(
        "schema_version: 1\nslots:\n  any-slot:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n    missed_window_hours: 4\n    on_miss: fire\n    cooldown_minutes: 60\n"
    )
    lock_path = tmp_path / ".scout-state" / ".schedule-tick.lock"
    lock_path.touch()
    from scout.scripts.schedule_tick import fire_now

    with open(lock_path, "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ev = fire_now("any-slot")
    assert ev.kind == "slot.fire_failed"
    assert "lock" in (ev.payload.get("error") or "").lower()


def test_fire_now_happy_path_emits_slot_fired(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched = tmp_path / ".scout-state" / "schedule.yaml"
    sched.write_text(
        "schema_version: 1\nslots:\n  any-slot:\n"
        "    type: briefing\n    runner: run-scout.sh\n    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n    missed_window_hours: 4\n    on_miss: fire\n    cooldown_minutes: 60\n"
    )
    from scout.scripts.schedule_tick import fire_now

    with patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen:
        mock_popen.return_value.pid = 7777
        ev = fire_now("any-slot")
    assert ev.kind == "slot.fired"
    assert ev.payload.get("manual") is True
    assert ev.payload.get("pid_spawned") == 7777


def test_fire_now_runner_missing_emits_fire_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    sched = tmp_path / ".scout-state" / "schedule.yaml"
    sched.write_text(
        "schema_version: 1\nslots:\n  any-slot:\n"
        "    type: briefing\n    runner: nonexistent-runner.sh\n    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n    missed_window_hours: 4\n    on_miss: fire\n    cooldown_minutes: 60\n"
    )
    from scout.scripts.schedule_tick import fire_now

    with patch("scout.scripts.schedule_tick.subprocess.Popen", side_effect=FileNotFoundError("nope")):
        ev = fire_now("any-slot")
    assert ev.kind == "slot.fire_failed"


# 9. runtime guard: dispatcher rejects remote slots until Plan 7.


def test_spawn_runner_rejects_remote_runtime(tmp_path):
    """Plan 7 forward-compat: dispatcher refuses to spawn `runtime: remote` slots
    until the routines API integration ships. Until then, save attempts in the
    Schedules tab UI render Remote as disabled, so this guard catches manual
    YAML edits that set runtime: remote."""
    slot = Slot(
        key="research",
        type=SlotType.RESEARCH,
        runner="run-research.sh",
        fires_at_local="14:00",
        weekdays=("Mon",),
        missed_window_hours=4,
        on_miss=OnMissPolicy.SKIP,
        cooldown_minutes=240,
        runtime=SlotRuntime.REMOTE,
    )
    with pytest.raises(ConfigError, match="runtime: remote.*Plan 7"):
        _spawn_runner(vault=tmp_path, slot_key="research", slot=slot)
