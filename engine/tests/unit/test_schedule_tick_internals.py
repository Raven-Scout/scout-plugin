"""Internal helpers of the schedule dispatcher: timezone resolution, the
last-fire index and its cache, the candidate/decision helpers, the network
probe and `main()`'s error handling.

`test_schedule_tick.py` covers the tick's decision matrix end-to-end.
This file drives the plumbing under it — the parts where a bug is silent
rather than loud:

* `_local_tz_name` decides what "08:30 local" means. A wrong answer shifts
  every slot by hours and the dispatcher still reports success, which is why
  each fallback logs to stderr instead of returning "UTC" quietly (#50).
* `_read_last_fire_index` / the `last-fire.json` cache decide whether a slot
  already ran. A stale-cache read means a double fire; a mis-parsed row means
  a missed one (#73).
* `_network_ready` gates every fire on a post-wake network being up.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import socket
from pathlib import Path

import pytest

from scout.schedule import OnMissPolicy, Schedule, SlotType, load_default_schedule
from scout.scripts import schedule_tick as st
from scout.scripts.schedule_tick import Decision, SlotCandidate

NOW = _dt.datetime(2026, 5, 28, 14, 0, tzinfo=_dt.UTC)  # a Thursday


@pytest.fixture(autouse=True)
def _clear_schedule_cache() -> None:
    """`_load_or_default` memoizes on mtime in a module global; reset it so
    tests can't leak a schedule into each other."""
    st._SCHEDULE_CACHE = None


def _slot(key: str, **overrides):
    import dataclasses

    template = next(iter(load_default_schedule().values()))
    return dataclasses.replace(template, key=key, **overrides)


def _candidate(key: str, *, target: _dt.datetime, last_fire: _dt.datetime | None = None, **slot_kwargs):
    return SlotCandidate(slot_key=key, slot=_slot(key, **slot_kwargs), target=target, last_fire=last_fire)


# ---------------------------------------------------------------------------
# _parse_iso_z / _weekday_name
# ---------------------------------------------------------------------------


def test_parse_iso_z_accepts_both_offset_forms() -> None:
    assert st._parse_iso_z("2026-05-28T14:00:00Z") == st._parse_iso_z("2026-05-28T14:00:00+00:00")


def test_parse_iso_z_rejects_a_non_date() -> None:
    with pytest.raises(ValueError):
        st._parse_iso_z("not-a-date")


@pytest.mark.parametrize(
    ("day", "name"),
    [(25, "Mon"), (26, "Tue"), (27, "Wed"), (28, "Thu"), (29, "Fri"), (30, "Sat"), (31, "Sun")],
)
def test_weekday_name_matches_the_schedule_vocabulary(day: int, name: str) -> None:
    """These strings are compared against `Slot.weekdays`; an off-by-one here
    silently moves every weekday-gated slot."""
    assert st._weekday_name(_dt.datetime(2026, 5, day, tzinfo=_dt.UTC)) == name


# ---------------------------------------------------------------------------
# _local_tz_name
# ---------------------------------------------------------------------------


def test_a_valid_tz_env_var_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TZ", "Europe/Prague")
    assert st._local_tz_name(localtime=tmp_path / "localtime") == "Europe/Prague"


def test_an_invalid_tz_env_var_is_reported_and_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TZ", "Mars/Olympus_Mons")
    assert st._local_tz_name(localtime=tmp_path / "not-a-symlink") == "UTC"
    err = capsys.readouterr().err
    assert "$TZ='Mars/Olympus_Mons' is not a valid IANA zone" in err


def test_the_localtime_symlink_target_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """macOS resolves /etc/localtime through several zoneinfo layouts, so the
    zone name is whatever follows the *deepest* zoneinfo* component."""
    monkeypatch.delenv("TZ", raising=False)
    zoneinfo_dir = tmp_path / "var" / "db" / "timezone" / "tz" / "2026a" / "zoneinfo" / "Europe"
    zoneinfo_dir.mkdir(parents=True)
    (zoneinfo_dir / "Prague").write_bytes(b"")

    link = tmp_path / "localtime"
    link.symlink_to(zoneinfo_dir / "Prague")
    assert st._local_tz_name(localtime=link) == "Europe/Prague"


def test_a_localtime_target_naming_an_unloadable_zone_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    zoneinfo_dir = tmp_path / "usr" / "share" / "zoneinfo" / "Mars"
    zoneinfo_dir.mkdir(parents=True)
    (zoneinfo_dir / "Olympus_Mons").write_bytes(b"")

    link = tmp_path / "localtime"
    link.symlink_to(zoneinfo_dir / "Olympus_Mons")

    assert st._local_tz_name(localtime=link) == "UTC"
    assert "is not loadable; falling back to UTC" in capsys.readouterr().err


def test_a_localtime_target_with_no_zoneinfo_component_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    target = tmp_path / "somewhere" / "else"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")
    link = tmp_path / "localtime"
    link.symlink_to(target)

    assert st._local_tz_name(localtime=link) == "UTC"
    err = capsys.readouterr().err
    assert "has no zoneinfo directory component" in err
    assert "set $TZ to your IANA zone" in err


def test_a_localtime_that_is_not_a_symlink_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    plain = tmp_path / "localtime"
    plain.write_bytes(b"")
    assert st._local_tz_name(localtime=plain) == "UTC"
    assert "is not a symlink; falling back to UTC" in capsys.readouterr().err


def test_now_returns_a_tz_aware_datetime_in_the_resolved_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/Prague")
    now = st._now()
    assert now.tzinfo is not None
    assert str(now.tzinfo) == "Europe/Prague"


def test_now_falls_back_to_utc_for_an_unloadable_resolved_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_local_tz_name` validates before returning, but keep the belt-and-braces
    fallback honest — a bare `ZoneInfo(name)` raise must not crash the tick."""
    monkeypatch.setattr(st, "_local_tz_name", lambda: "Mars/Olympus_Mons")
    assert st._now().tzinfo is _dt.UTC


# ---------------------------------------------------------------------------
# _read_last_fire_index
# ---------------------------------------------------------------------------


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    d = tmp_path / ".scout-logs"
    d.mkdir()
    return d


def _tracker(logs: Path, *rows: str) -> Path:
    p = logs / "usage-tracker.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_the_index_takes_the_latest_timestamp_per_slot(logs: Path) -> None:
    tracker = _tracker(
        logs,
        json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "morning-briefing"}),
        json.dumps({"ts": "2026-05-28T12:30:00Z", "slot_key": "morning-briefing"}),
        json.dumps({"ts": "2026-05-28T09:00:00Z", "slot_key": "midday-consolidation"}),
    )
    index = st._read_last_fire_index(tracker)
    assert index["morning-briefing"] == st._parse_iso_z("2026-05-28T12:30:00Z")
    assert index["midday-consolidation"] == st._parse_iso_z("2026-05-28T09:00:00Z")


def test_the_index_keeps_the_newest_when_rows_arrive_out_of_order(logs: Path) -> None:
    """Several writers append to this log, so rows are not guaranteed to be in
    timestamp order — the index must keep the max, not the last seen."""
    tracker = _tracker(
        logs,
        json.dumps({"ts": "2026-05-28T12:30:00Z", "slot_key": "morning-briefing"}),
        json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "morning-briefing"}),
    )
    assert st._read_last_fire_index(tracker)["morning-briefing"] == st._parse_iso_z("2026-05-28T12:30:00Z")


def test_the_index_merges_the_session_tokens_log(logs: Path) -> None:
    """`session-tokens.jsonl` (the Stop hook) carries slot identity as
    `scout_mode`; both files must feed one index."""
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "morning-briefing"}))
    (logs / st.SESSION_TOKENS_FILENAME).write_text(
        json.dumps({"ts": "2026-05-28T13:00:00Z", "scout_mode": "morning-briefing"}) + "\n",
        encoding="utf-8",
    )
    assert st._read_last_fire_index(tracker)["morning-briefing"] == st._parse_iso_z("2026-05-28T13:00:00Z")


def test_the_index_renames_legacy_mode_names(logs: Path) -> None:
    legacy, canonical = next(iter(st._LEGACY_MODE_RENAME.items()))
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "scout_mode": legacy}))
    index = st._read_last_fire_index(tracker)
    assert canonical in index
    assert legacy not in index


def test_the_index_skips_unusable_rows(logs: Path) -> None:
    """The tracker is appended by several writers (a shell script among them);
    a torn or slot-less row must be dropped, not abort the scan."""
    tracker = _tracker(
        logs,
        "",
        "   ",
        "{torn",
        '"a bare string"',
        "[1, 2]",
        json.dumps({"ts": "2026-05-28T08:00:00Z"}),  # no slot identity (legacy budget row)
        json.dumps({"ts": "2026-05-28T08:00:00Z", "slot_key": ""}),
        json.dumps({"ts": "2026-05-28T08:00:00Z", "slot_key": 42}),
        json.dumps({"slot_key": "morning-briefing"}),  # no ts
        json.dumps({"ts": "", "slot_key": "morning-briefing"}),
        json.dumps({"ts": 1700000000, "slot_key": "morning-briefing"}),
        json.dumps({"ts": "not-a-date", "slot_key": "morning-briefing"}),
        json.dumps({"ts": "2026-05-28T12:30:00Z", "slot_key": "morning-briefing"}),
    )
    assert st._read_last_fire_index(tracker) == {"morning-briefing": st._parse_iso_z("2026-05-28T12:30:00Z")}


def test_the_index_is_empty_when_no_log_exists(logs: Path) -> None:
    assert st._read_last_fire_index(logs / "usage-tracker.jsonl") == {}


def test_the_index_skips_an_unreadable_log(logs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "morning-briefing"}))

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert st._read_last_fire_index(tracker) == {}


# ---------------------------------------------------------------------------
# last-fire cache
# ---------------------------------------------------------------------------


def test_the_cache_round_trips_and_is_reused(logs: Path, tmp_path: Path) -> None:
    state = tmp_path / ".scout-state"
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "morning-briefing"}))

    first = st._get_last_fire_index(state, tracker)
    assert (state / st.LAST_FIRE_CACHE_FILENAME).is_file()
    assert st._get_last_fire_index(state, tracker) == first


def test_a_tracker_write_invalidates_the_cache(logs: Path, tmp_path: Path) -> None:
    """The cache is mtime-keyed; a new fire must be picked up on the next tick
    or the dispatcher double-fires."""
    state = tmp_path / ".scout-state"
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "morning-briefing"}))
    st._get_last_fire_index(state, tracker)

    tracker.write_text(
        json.dumps({"ts": "2026-05-28T13:00:00Z", "slot_key": "morning-briefing"}) + "\n", encoding="utf-8"
    )
    bumped = tracker.stat().st_mtime_ns + 1_000_000_000
    os.utime(tracker, ns=(bumped, bumped))

    assert st._get_last_fire_index(state, tracker)["morning-briefing"] == st._parse_iso_z("2026-05-28T13:00:00Z")


def test_a_session_tokens_write_invalidates_the_cache(logs: Path, tmp_path: Path) -> None:
    state = tmp_path / ".scout-state"
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "morning-briefing"}))
    st._get_last_fire_index(state, tracker)

    (logs / st.SESSION_TOKENS_FILENAME).write_text(
        json.dumps({"ts": "2026-05-28T13:00:00Z", "scout_mode": "morning-briefing"}) + "\n",
        encoding="utf-8",
    )
    assert st._get_last_fire_index(state, tracker)["morning-briefing"] == st._parse_iso_z("2026-05-28T13:00:00Z")


def test_a_missing_cache_file_is_a_miss(tmp_path: Path, logs: Path) -> None:
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "b"}))
    assert st._load_last_fire_cache(tmp_path, tracker, logs / st.SESSION_TOKENS_FILENAME) is None


@pytest.mark.parametrize("body", ["{torn", '["a", "list"]', "null", "42"])
def test_a_corrupt_cache_file_is_a_miss(tmp_path: Path, logs: Path, body: str) -> None:
    (tmp_path / st.LAST_FIRE_CACHE_FILENAME).write_text(body, encoding="utf-8")
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "b"}))
    assert st._load_last_fire_cache(tmp_path, tracker, logs / st.SESSION_TOKENS_FILENAME) is None


def test_a_cache_from_a_different_schema_version_is_a_miss(tmp_path: Path, logs: Path) -> None:
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "b"}))
    (tmp_path / st.LAST_FIRE_CACHE_FILENAME).write_text(
        json.dumps({"schema_version": 999, "last_fire": {}}), encoding="utf-8"
    )
    assert st._load_last_fire_cache(tmp_path, tracker, logs / st.SESSION_TOKENS_FILENAME) is None


def test_a_cache_with_a_non_mapping_index_is_a_miss(tmp_path: Path, logs: Path) -> None:
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "b"}))
    tokens = logs / st.SESSION_TOKENS_FILENAME
    (tmp_path / st.LAST_FIRE_CACHE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": st._LAST_FIRE_CACHE_SCHEMA_VERSION,
                "tracker_mtime_ns": st._file_mtime_ns(tracker),
                "session_tokens_mtime_ns": st._file_mtime_ns(tokens),
                "last_fire": ["not", "a", "mapping"],
            }
        ),
        encoding="utf-8",
    )
    assert st._load_last_fire_cache(tmp_path, tracker, tokens) is None


def test_individual_bad_cache_rows_are_skipped(tmp_path: Path, logs: Path) -> None:
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "b"}))
    tokens = logs / st.SESSION_TOKENS_FILENAME
    (tmp_path / st.LAST_FIRE_CACHE_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": st._LAST_FIRE_CACHE_SCHEMA_VERSION,
                "tracker_mtime_ns": st._file_mtime_ns(tracker),
                "session_tokens_mtime_ns": st._file_mtime_ns(tokens),
                "last_fire": {
                    "morning-briefing": "2026-05-28T08:30:00Z",
                    "bad-ts": "not-a-date",
                    "bad-value": 42,
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = st._load_last_fire_cache(tmp_path, tracker, tokens)
    assert loaded is not None
    assert set(loaded) == {"morning-briefing"}


def test_file_mtime_ns_is_none_for_a_missing_file(tmp_path: Path) -> None:
    assert st._file_mtime_ns(tmp_path / "nope") is None


def test_a_failed_cache_write_leaves_no_tempfile(tmp_path: Path, logs: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / ".scout-state"
    state.mkdir()
    tracker = _tracker(logs, json.dumps({"ts": "2026-05-28T08:30:00Z", "slot_key": "b"}))
    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.name.endswith(".json.tmp"):
            raise OSError("read-only filesystem")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    st._write_last_fire_cache(state, {"b": NOW}, tracker, logs / st.SESSION_TOKENS_FILENAME)

    assert not (state / st.LAST_FIRE_CACHE_FILENAME).exists()
    assert list(state.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# _compute_due_slots
# ---------------------------------------------------------------------------


def test_a_slot_already_fired_after_todays_target_is_not_a_candidate() -> None:
    sched = Schedule({"b": _slot("b", fires_at_local="08:30", cooldown_minutes=0)})
    target = sched["b"].target_today(now=NOW)
    assert target is not None
    fired = {"b": target + _dt.timedelta(minutes=1)}
    assert st._compute_due_slots(sched, fired, NOW) == []


def test_a_slot_inside_its_cooldown_is_not_a_candidate() -> None:
    sched = Schedule({"b": _slot("b", fires_at_local="08:30", cooldown_minutes=600)})
    target = sched["b"].target_today(now=NOW)
    assert target is not None
    # Fired before today's target (so not "already fired") but recently enough
    # to still be inside the 10h cooldown.
    fired = {"b": target - _dt.timedelta(minutes=30)}
    assert st._compute_due_slots(sched, fired, NOW) == []


def test_a_slot_whose_cooldown_has_elapsed_is_a_candidate() -> None:
    """Cooldown suppresses a re-fire right after a run; once it lapses the slot
    becomes eligible again."""
    sched = Schedule({"b": _slot("b", fires_at_local="08:30", cooldown_minutes=30)})
    target = sched["b"].target_today(now=NOW)
    assert target is not None
    fired = {"b": target - _dt.timedelta(hours=6)}
    candidates = st._compute_due_slots(sched, fired, NOW)
    assert [c.slot_key for c in candidates] == ["b"]
    assert candidates[0].last_fire == fired["b"]


def test_a_never_fired_due_slot_is_a_candidate() -> None:
    sched = Schedule({"b": _slot("b", fires_at_local="08:30", cooldown_minutes=0)})
    candidates = st._compute_due_slots(sched, {}, NOW)
    assert [c.slot_key for c in candidates] == ["b"]
    assert candidates[0].last_fire is None


def test_a_future_target_is_not_a_candidate() -> None:
    sched = Schedule({"b": _slot("b", fires_at_local="23:59", cooldown_minutes=0)})
    assert st._compute_due_slots(sched, {}, NOW) == []


def test_a_weekday_excluded_slot_is_not_a_candidate() -> None:
    # NOW is a Thursday.
    sched = Schedule({"b": _slot("b", fires_at_local="08:30", weekdays=("Sat", "Sun"))})
    assert st._compute_due_slots(sched, {}, NOW) == []


def test_candidates_by_key_indexes_by_slot_key() -> None:
    a = _candidate("a", target=NOW - _dt.timedelta(hours=1))
    b = _candidate("b", target=NOW - _dt.timedelta(hours=2))
    assert st.candidates_by_key([a, b]) == {"a": a, "b": b}


# ---------------------------------------------------------------------------
# _apply_miss_rules
# ---------------------------------------------------------------------------


def test_no_candidates_yields_no_decisions() -> None:
    assert st._apply_miss_rules([], now=NOW) == {}


def test_on_miss_skip_fires_inside_the_window() -> None:
    """#193: `skip` means "don't fire *late*", not "never fire". Every candidate
    reaching the policy check is already past its target (`_compute_due_slots`
    drops `target > now`), so skipping unconditionally left no path by which a
    skip-policy slot could ever fire — silently disabling all dreaming and
    research sessions."""
    c = _candidate("b", target=NOW - _dt.timedelta(minutes=5), on_miss=OnMissPolicy.SKIP, missed_window_hours=4)
    assert st._apply_miss_rules([c], now=NOW)["b"] == Decision(action="fire")


def test_on_miss_skip_goes_stale_past_its_window() -> None:
    """`missed_window_hours` is what bounds "late" — beyond it a skip-policy
    slot goes stale exactly as it does under `fire`."""
    c = _candidate("b", target=NOW - _dt.timedelta(hours=9), on_miss=OnMissPolicy.SKIP, missed_window_hours=4)
    assert st._apply_miss_rules([c], now=NOW)["b"] == Decision(action="skip", reason="stale-after-window")


def test_on_miss_fire_inside_the_window_fires() -> None:
    c = _candidate("b", target=NOW - _dt.timedelta(hours=1), on_miss=OnMissPolicy.FIRE, missed_window_hours=4)
    assert st._apply_miss_rules([c], now=NOW)["b"] == Decision(action="fire")


def test_on_miss_fire_past_the_window_is_stale() -> None:
    """A briefing that missed its whole window is worse than no briefing — it
    would report yesterday's world as today's."""
    c = _candidate("b", target=NOW - _dt.timedelta(hours=9), on_miss=OnMissPolicy.FIRE, missed_window_hours=4)
    assert st._apply_miss_rules([c], now=NOW)["b"] == Decision(action="skip", reason="stale-after-window")


def test_collapse_keeps_the_latest_target_within_a_type() -> None:
    early = _candidate(
        "early",
        target=NOW - _dt.timedelta(hours=3),
        on_miss=OnMissPolicy.COLLAPSE,
        type=SlotType.CONSOLIDATION,
        missed_window_hours=8,
    )
    late = _candidate(
        "late",
        target=NOW - _dt.timedelta(hours=1),
        on_miss=OnMissPolicy.COLLAPSE,
        type=SlotType.CONSOLIDATION,
        missed_window_hours=8,
    )
    decisions = st._apply_miss_rules([early, late], now=NOW)
    assert decisions["late"] == Decision(action="fire")
    assert decisions["early"] == Decision(action="skip", reason="collapsed-into=late")


def test_a_collapse_winner_past_its_window_is_still_stale() -> None:
    late = _candidate(
        "late",
        target=NOW - _dt.timedelta(hours=9),
        on_miss=OnMissPolicy.COLLAPSE,
        type=SlotType.CONSOLIDATION,
        missed_window_hours=4,
    )
    assert st._apply_miss_rules([late], now=NOW)["late"] == Decision(action="skip", reason="stale-after-window")


def test_collapse_groups_are_per_slot_type() -> None:
    """Two different types both collapsing must each keep their own winner —
    a consolidation must not swallow a dreaming slot."""
    cons = _candidate(
        "cons",
        target=NOW - _dt.timedelta(hours=1),
        on_miss=OnMissPolicy.COLLAPSE,
        type=SlotType.CONSOLIDATION,
        missed_window_hours=8,
    )
    dream = _candidate(
        "dream",
        target=NOW - _dt.timedelta(hours=2),
        on_miss=OnMissPolicy.COLLAPSE,
        type=SlotType.DREAMING,
        missed_window_hours=8,
    )
    decisions = st._apply_miss_rules([cons, dream], now=NOW)
    assert decisions["cons"] == Decision(action="fire")
    assert decisions["dream"] == Decision(action="fire")


# ---------------------------------------------------------------------------
# _filter_winner_by_priority
# ---------------------------------------------------------------------------


def test_no_fire_decisions_yields_no_winner() -> None:
    sched = Schedule({"b": _slot("b")})
    assert st._filter_winner_by_priority(sched, {"b": Decision(action="skip", reason="x")}) is None
    assert st._filter_winner_by_priority(sched, {}) is None


def test_the_highest_priority_slot_type_wins() -> None:
    sched = Schedule(
        {
            "b": _slot("b", type=SlotType.BRIEFING),
            "d": _slot("d", type=SlotType.DREAMING),
        }
    )
    decisions = {"b": Decision(action="fire"), "d": Decision(action="fire")}
    assert st._filter_winner_by_priority(sched, decisions) == "b"


def test_a_priority_tie_breaks_deterministically_by_slot_key() -> None:
    sched = Schedule(
        {
            "zebra": _slot("zebra", type=SlotType.BRIEFING),
            "alpha": _slot("alpha", type=SlotType.BRIEFING),
        }
    )
    decisions = {"zebra": Decision(action="fire"), "alpha": Decision(action="fire")}
    assert st._filter_winner_by_priority(sched, decisions) == "alpha"


# ---------------------------------------------------------------------------
# _network_ready
# ---------------------------------------------------------------------------


def test_the_network_probe_can_be_short_circuited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE", "1")

    def fail(*_a: object, **_k: object):
        raise AssertionError("the probe must not open a socket when short-circuited")

    monkeypatch.setattr(socket, "create_connection", fail)
    assert st._network_ready() is True


def test_the_probe_succeeds_on_the_first_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE", raising=False)

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    calls: list[tuple] = []
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout=None: calls.append(addr) or FakeSocket())
    assert st._network_ready() is True
    assert calls == [(st.NETWORK_PROBE_HOST, st.NETWORK_PROBE_PORT)]


def test_the_probe_retries_then_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dispatcher fires right after a scheduled wake; DNS is often not up
    for a second or two, which is why there are retries at all."""
    monkeypatch.delenv("SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE", raising=False)
    attempts: list[int] = []
    slept: list[float] = []

    def boom(*_a: object, **_k: object):
        attempts.append(1)
        raise OSError("network is unreachable")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(st._time, "sleep", lambda s: slept.append(s))

    assert st._network_ready(retries=3, sleep_seconds=0.5) is False
    assert len(attempts) == 3
    # Sleeps between attempts only — not after the last one.
    assert slept == [0.5, 0.5]


def test_the_probe_recovers_on_a_later_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE", raising=False)

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    calls = {"n": 0}

    def flaky(*_a: object, **_k: object):
        calls["n"] += 1
        if calls["n"] < 2:
            raise OSError("network is unreachable")
        return FakeSocket()

    monkeypatch.setattr(socket, "create_connection", flaky)
    monkeypatch.setattr(st._time, "sleep", lambda _s: None)
    assert st._network_ready(retries=3, sleep_seconds=0.1) is True
    assert calls["n"] == 2


def test_a_retries_value_below_one_still_makes_one_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE", raising=False)
    attempts: list[int] = []

    def boom(*_a: object, **_k: object):
        attempts.append(1)
        raise OSError("network is unreachable")

    monkeypatch.setattr(socket, "create_connection", boom)
    assert st._network_ready(retries=0, sleep_seconds=0) is False
    assert len(attempts) == 1


def test_the_probe_skips_sleeping_when_the_interval_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_SCHEDULE_TICK_SKIP_NETWORK_PROBE", raising=False)

    def boom(*_a: object, **_k: object):
        raise OSError("network is unreachable")

    def fail_sleep(_s: float) -> None:
        raise AssertionError("must not sleep when sleep_seconds is 0")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(st._time, "sleep", fail_sleep)
    assert st._network_ready(retries=2, sleep_seconds=0) is False


# ---------------------------------------------------------------------------
# _load_or_default
# ---------------------------------------------------------------------------


def test_load_or_default_falls_back_to_the_plugin_defaults(tmp_path: Path) -> None:
    assert st._load_or_default(tmp_path).keys() == load_default_schedule().keys()


def test_load_or_default_reads_the_vault_schedule_and_caches_it(tmp_path: Path) -> None:
    state = tmp_path / ".scout-state"
    state.mkdir()
    import shutil

    from scout import cli

    shutil.copy2(Path(cli.__file__).parent / "defaults" / "schedule.yaml", state / "schedule.yaml")

    first = st._load_or_default(tmp_path)
    assert st._SCHEDULE_CACHE is not None
    # Second call with an unchanged mtime returns the SAME object (cache hit).
    assert st._load_or_default(tmp_path) is first


def test_load_or_default_bypasses_the_cache_when_the_mtime_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / ".scout-state"
    state.mkdir()
    import shutil

    from scout import cli

    sched_path = state / "schedule.yaml"
    shutil.copy2(Path(cli.__file__).parent / "defaults" / "schedule.yaml", sched_path)

    real_stat = Path.stat
    seen: list[int] = []

    def maybe_boom(self: Path, *a: object, **k: object):
        # `exists()` stats first; fail only the explicit mtime read after it.
        if self == sched_path:
            seen.append(1)
            if len(seen) > 1:
                raise OSError("stat failed")
        return real_stat(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", maybe_boom)
    assert st._load_or_default(tmp_path).keys()
    # The mtime is the cache key; without it the schedule is re-parsed every
    # tick rather than cached under a bogus key.
    assert st._SCHEDULE_CACHE is None


# ---------------------------------------------------------------------------
# payload helpers
# ---------------------------------------------------------------------------


def test_candidate_target_iso_finds_the_named_candidate() -> None:
    target = NOW - _dt.timedelta(hours=1)
    candidates = [_candidate("a", target=target)]
    assert st._candidate_target_iso(candidates, "a") == target.isoformat()
    assert st._candidate_target_iso(candidates, "missing") is None
    assert st._candidate_target_iso([], "a") is None


def test_target_utc_iso_normalizes_to_a_z_suffix() -> None:
    from zoneinfo import ZoneInfo

    local = _dt.datetime(2026, 5, 28, 8, 30, tzinfo=ZoneInfo("America/New_York"))
    assert st._target_utc_iso(local) == "2026-05-28T12:30:00Z"


def test_the_skipped_payload_enriches_from_the_candidate_index() -> None:
    target = NOW - _dt.timedelta(hours=1)
    index = st.candidates_by_key([_candidate("a", target=target, type=SlotType.BRIEFING)])

    enriched = st._skipped_payload(index, "a", "on_miss=skip")
    assert enriched == {
        "slot_key": "a",
        "reason": "on_miss=skip",
        "slot_type": "briefing",
        "target_local": target.isoformat(),
    }


def test_the_skipped_payload_degrades_for_an_unknown_slot() -> None:
    """A slot can be skipped before it ever became a candidate (e.g. the lock
    was held), so the enrichment must be optional."""
    assert st._skipped_payload({}, "a", "lock-held") == {"slot_key": "a", "reason": "lock-held"}


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_a_successful_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(st, "run", lambda: None)
    assert st.main() == 0


def test_main_prints_the_traceback_and_returns_one_on_an_unhandled_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A silent exit-1 leaves no signal for why the tick stopped firing, so the
    traceback goes to stderr where launchd/cron captures it."""

    def boom() -> None:
        raise RuntimeError("unreachable state")

    monkeypatch.setattr(st, "run", boom)
    assert st.main() == 1
    err = capsys.readouterr().err
    assert "RuntimeError: unreachable state" in err
    assert "Traceback" in err
