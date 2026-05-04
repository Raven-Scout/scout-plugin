"""Unit tests for scout.scripts.schedule_tick — the dispatcher brain."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scout.events import Event
from scout.schedule import (
    OnMissPolicy,
    Slot,
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
