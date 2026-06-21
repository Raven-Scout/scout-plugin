"""Unit tests for scout.scripts.schedule_tick — the dispatcher brain."""

from __future__ import annotations

import json
import os
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
    _get_last_fire_index,
    _load_last_fire_cache,
    _network_ready,
    _read_last_fire_index,
    _spawn_runner,
    _write_last_fire_cache,
    candidates_by_key,
)
from scout.scripts.schedule_tick import (
    main as tick_main,
)
from scout.scripts.schedule_tick import (
    run as tick_run,
)

# Frozen clock for tick_run() tests: Mon 08:05 ET — well past a 00:01 slot, so
# the slot is unambiguously due. Tests that exercise tick_run() against the real
# clock flake when CI runs near the 00:00 boundary (the missed-window math is
# time-of-day-dependent), so they patch schedule_tick._now to this value.
_FROZEN_NOW = datetime(2026, 5, 11, 8, 5, tzinfo=ZoneInfo("America/New_York"))


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


# 5b. Cached last-fire index (perf fix for #73).
#
# The cache lives at .scout-state/last-fire.json and is keyed by the mtimes
# of both usage-tracker.jsonl and session-tokens.jsonl. The fast path returns
# the cached dict in O(slots); a miss/staleness falls through to the full
# JSONL scan via _read_last_fire_index and rewrites the cache.


def _make_tracker(tmp_path):
    log_dir = tmp_path / ".scout-logs"
    log_dir.mkdir(exist_ok=True)
    state_dir = tmp_path / ".scout-state"
    state_dir.mkdir(exist_ok=True)
    tracker = log_dir / "usage-tracker.jsonl"
    tracker.write_text(
        '{"ts":"2026-05-11T12:00:00Z","type":"briefing","scout_mode":"morning-briefing"}\n'
        '{"ts":"2026-05-11T17:00:00Z","type":"consolidation","scout_mode":"afternoon-consolidation"}\n'
    )
    return tracker, state_dir


def test_get_last_fire_index_writes_cache_on_first_call(tmp_path):
    tracker, state_dir = _make_tracker(tmp_path)
    assert not (state_dir / "last-fire.json").exists()

    index = _get_last_fire_index(state_dir, tracker)

    assert "morning-briefing" in index
    assert "afternoon-consolidation" in index
    # Cache was written so the next call doesn't scan again.
    assert (state_dir / "last-fire.json").exists()


def test_get_last_fire_index_uses_cache_when_mtimes_match(tmp_path):
    """Cache hit: source files unchanged since warm-up → cache is honoured."""
    tracker, state_dir = _make_tracker(tmp_path)

    # Warm the cache.
    _get_last_fire_index(state_dir, tracker)

    # If we corrupt the underlying JSONL but leave its mtime untouched,
    # the cache should still serve the previously-extracted index without
    # re-scanning — that's the whole point of the cache.
    original_mtime_ns = tracker.stat().st_mtime_ns
    tracker.write_bytes(b"not-json garbage that would raise on rescan\n")
    os.utime(tracker, ns=(original_mtime_ns, original_mtime_ns))

    session_tokens = tracker.parent / "session-tokens.jsonl"
    cached = _load_last_fire_cache(state_dir, tracker, session_tokens)
    assert cached is not None
    assert "morning-briefing" in cached
    assert "afternoon-consolidation" in cached


def test_get_last_fire_index_invalidates_cache_on_tracker_change(tmp_path):
    tracker, state_dir = _make_tracker(tmp_path)
    _get_last_fire_index(state_dir, tracker)  # warm

    # Append a new row → tracker mtime bumps → cache should be invalidated.
    with tracker.open("a", encoding="utf-8") as f:
        f.write('{"ts":"2026-05-12T08:00:00Z","type":"briefing","scout_mode":"morning-briefing"}\n')

    session_tokens = tracker.parent / "session-tokens.jsonl"
    assert _load_last_fire_cache(state_dir, tracker, session_tokens) is None

    # _get_last_fire_index detects the staleness and rebuilds.
    fresh = _get_last_fire_index(state_dir, tracker)
    # The newer 2026-05-12 row wins over the cached 2026-05-11 row.
    assert fresh["morning-briefing"].day == 12


def test_get_last_fire_index_invalidates_cache_on_session_tokens_change(tmp_path):
    tracker, state_dir = _make_tracker(tmp_path)
    _get_last_fire_index(state_dir, tracker)  # warm

    # Touching session-tokens.jsonl must also invalidate the cache, since
    # _read_last_fire_index reads both files.
    session_tokens = tracker.parent / "session-tokens.jsonl"
    session_tokens.write_text('{"ts":"2026-05-13T09:00:00Z","scout_mode":"morning-briefing"}\n')

    assert _load_last_fire_cache(state_dir, tracker, session_tokens) is None
    fresh = _get_last_fire_index(state_dir, tracker)
    assert fresh["morning-briefing"].day == 13


def test_load_last_fire_cache_rejects_wrong_schema_version(tmp_path):
    state_dir = tmp_path / ".scout-state"
    state_dir.mkdir()
    tracker = tmp_path / "usage-tracker.jsonl"
    tracker.write_text("")
    session_tokens = tracker.parent / "session-tokens.jsonl"

    # Hand-write a cache file with a bad schema version.
    (state_dir / "last-fire.json").write_text(
        json.dumps(
            {
                "schema_version": 999,
                "tracker_mtime_ns": tracker.stat().st_mtime_ns,
                "session_tokens_mtime_ns": None,
                "last_fire": {"morning-briefing": "2026-05-11T12:00:00Z"},
            }
        )
    )
    assert _load_last_fire_cache(state_dir, tracker, session_tokens) is None


def test_load_last_fire_cache_handles_corrupt_json(tmp_path):
    state_dir = tmp_path / ".scout-state"
    state_dir.mkdir()
    tracker = tmp_path / "usage-tracker.jsonl"
    tracker.write_text("")
    session_tokens = tracker.parent / "session-tokens.jsonl"

    (state_dir / "last-fire.json").write_text("{not valid json")
    # Corrupt cache must not raise — it returns None and the caller rebuilds.
    assert _load_last_fire_cache(state_dir, tracker, session_tokens) is None


def test_write_last_fire_cache_round_trip(tmp_path):
    state_dir = tmp_path / ".scout-state"
    state_dir.mkdir()
    tracker = tmp_path / "usage-tracker.jsonl"
    tracker.write_text("")
    session_tokens = tracker.parent / "session-tokens.jsonl"

    et = ZoneInfo("America/New_York")
    original = {
        "morning-briefing": datetime(2026, 5, 11, 8, 0, tzinfo=et),
        "afternoon-consolidation": datetime(2026, 5, 11, 17, 0, tzinfo=et),
    }
    _write_last_fire_cache(state_dir, original, tracker, session_tokens)

    round_tripped = _load_last_fire_cache(state_dir, tracker, session_tokens)
    assert round_tripped is not None
    # Timestamps are normalised to UTC on write — compare as moments.
    assert round_tripped["morning-briefing"] == original["morning-briefing"]
    assert round_tripped["afternoon-consolidation"] == original["afternoon-consolidation"]


# Schedule.yaml mtime cache (#82). Reuses the parsed Schedule across ticks
# when the file hasn't changed — the dispatcher fires every 5 min and the
# schedule rarely moves, so the YAML parse was pure overhead.


def test_load_or_default_caches_parsed_schedule(tmp_path, monkeypatch):
    from scout.scripts import schedule_tick as st

    state = tmp_path / ".scout-state"
    state.mkdir()
    sched_path = state / "schedule.yaml"
    sched_path.write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: manual\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: skip\n    cooldown_minutes: 5\n"
    )
    # Clear cache from any prior test runs in this process.
    monkeypatch.setattr(st, "_SCHEDULE_CACHE", None)

    calls = {"n": 0}
    real_load = st.load_schedule

    def counting_load_schedule(path):
        calls["n"] += 1
        return real_load(path)

    monkeypatch.setattr(st, "load_schedule", counting_load_schedule)

    s1 = st._load_or_default(tmp_path)
    s2 = st._load_or_default(tmp_path)
    assert s1 is s2  # same parsed object, returned from cache
    assert calls["n"] == 1


def test_load_or_default_invalidates_cache_on_mtime_change(tmp_path, monkeypatch):
    from scout.scripts import schedule_tick as st

    state = tmp_path / ".scout-state"
    state.mkdir()
    sched_path = state / "schedule.yaml"
    sched_path.write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: manual\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: skip\n    cooldown_minutes: 5\n"
    )
    monkeypatch.setattr(st, "_SCHEDULE_CACHE", None)

    calls = {"n": 0}
    real_load = st.load_schedule

    def counting_load_schedule(path):
        calls["n"] += 1
        return real_load(path)

    monkeypatch.setattr(st, "load_schedule", counting_load_schedule)
    st._load_or_default(tmp_path)

    # Rewrite with a bumped mtime → cache miss → re-parse.
    import os

    new_ts = sched_path.stat().st_mtime_ns + 1_000_000_000
    sched_path.write_text(sched_path.read_text())  # touch contents
    os.utime(sched_path, ns=(new_ts, new_ts))

    st._load_or_default(tmp_path)
    assert calls["n"] == 2


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
        patch("scout.scripts.schedule_tick._now", return_value=_FROZEN_NOW),
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
        patch("scout.scripts.schedule_tick._now", return_value=_FROZEN_NOW),
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
    with (
        patch("scout.scripts.schedule_tick._now", return_value=_FROZEN_NOW),
        patch("scout.scripts.schedule_tick._network_ready", return_value=True),
    ):
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


# 9. runtime guard: dispatcher rejects remote slots until the routines API ships.


def test_spawn_runner_rejects_remote_runtime(tmp_path):
    """Forward-compat: dispatcher refuses to spawn `runtime: remote` slots
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
    with pytest.raises(ConfigError, match="runtime: remote.*not yet implemented"):
        _spawn_runner(vault=tmp_path, slot_key="research", slot=slot)


# main(): unhandled exceptions must surface, not be silently swallowed.
# Regression test for issue #35 — cron/launchd had no way to diagnose failures.


def test_network_probe_called_with_reduced_retry_budget_in_do_tick(tmp_path, monkeypatch):
    """#60: the in-lock network probe must use a reduced retry budget (< 6) so
    worst-case lock hold is ~10s, not ~48s."""
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

    probe_kwargs: list[dict] = []

    def spy_network_ready(**kwargs):
        probe_kwargs.append(kwargs)
        return True  # succeed immediately

    with (
        patch("scout.scripts.schedule_tick._now", return_value=_FROZEN_NOW),
        patch("scout.scripts.schedule_tick._network_ready", side_effect=spy_network_ready),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 12345
        tick_run()

    assert len(probe_kwargs) == 1, "network probe should be called exactly once"
    retries_used = probe_kwargs[0].get("retries", 6)  # default 6 if not passed
    assert retries_used < 6, (
        f"In-lock network probe must use fewer than 6 retries to bound lock hold; got {retries_used}"
    )


def test_main_prints_traceback_to_stderr_when_run_raises(capsys):
    """Unhandled exceptions from run() must produce a traceback on stderr so
    cron/launchd logs capture the failure. Exit code stays 1."""
    boom = RuntimeError("synthetic failure for visibility test")
    with patch("scout.scripts.schedule_tick.run", side_effect=boom):
        rc = tick_main([])
    assert rc == 1
    captured = capsys.readouterr()
    assert "synthetic failure for visibility test" in captured.err
    assert "Traceback" in captured.err


# Event-log filename / ts agreement (#37). The UTC date in the file name must
# come from the same instant as the event ts — a second clock read could land
# on the far side of UTC midnight and file the event into the wrong day's log.


def test_emit_event_filename_matches_ts_date_across_midnight(tmp_path, monkeypatch):
    from scout.scripts import schedule_tick as st

    # ts is stamped at 23:59 on day N; a second (stale) clock read would be
    # day N+1. The filename must follow ts (day N), not the second read.
    monkeypatch.setattr(st, "now_iso", lambda: "2026-03-14T23:59:59.999Z")
    monkeypatch.setattr(st, "_now", lambda: datetime(2026, 3, 15, 0, 0, 1, tzinfo=ZoneInfo("UTC")))

    ev = st._emit_event(tmp_path, kind="slot.fired", source="test", payload={})

    logs = list(tmp_path.glob("schedule-events-*.jsonl"))
    assert len(logs) == 1
    assert logs[0].name == "schedule-events-2026-03-14.jsonl"
    assert ev.ts[:10] == "2026-03-14"


# Timezone resolution (#50): $TZ > /etc/localtime symlink > UTC, with a stderr
# warning on every fallback that can't produce a validated zone (a wrong tz
# silently shifts every fires_at_local).


def test_local_tz_name_env_tz_valid_wins(monkeypatch):
    from scout.scripts import schedule_tick as st

    monkeypatch.setenv("TZ", "America/New_York")
    assert st._local_tz_name() == "America/New_York"


def test_local_tz_name_env_tz_invalid_is_ignored_and_warns(monkeypatch, tmp_path, capsys):
    from scout.scripts import schedule_tick as st

    monkeypatch.setenv("TZ", "Totally/Bogus")
    not_a_link = tmp_path / "localtime"  # regular file → forces UTC
    not_a_link.write_text("x")
    assert st._local_tz_name(localtime=not_a_link) == "UTC"
    assert "not a valid IANA zone" in capsys.readouterr().err


def test_local_tz_name_resolves_symlink_zone(monkeypatch, tmp_path):
    from scout.scripts import schedule_tick as st

    monkeypatch.delenv("TZ", raising=False)
    link = tmp_path / "localtime"
    link.symlink_to("/usr/share/zoneinfo/America/New_York")
    assert st._local_tz_name(localtime=link) == "America/New_York"


def test_local_tz_name_resolves_relative_symlink(monkeypatch, tmp_path):
    from scout.scripts import schedule_tick as st

    monkeypatch.delenv("TZ", raising=False)
    link = tmp_path / "localtime"
    link.symlink_to("zoneinfo/Europe/Paris")  # relative target — resolve() handles it
    assert st._local_tz_name(localtime=link) == "Europe/Paris"


def test_local_tz_name_not_symlink_warns_and_falls_back_to_utc(monkeypatch, tmp_path, capsys):
    from scout.scripts import schedule_tick as st

    monkeypatch.delenv("TZ", raising=False)
    regular = tmp_path / "localtime"
    regular.write_text("not a symlink")
    assert st._local_tz_name(localtime=regular) == "UTC"
    assert "not a symlink" in capsys.readouterr().err
