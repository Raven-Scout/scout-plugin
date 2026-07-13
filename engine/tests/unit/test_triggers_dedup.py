"""Dedup + cooldown + daily-cap state (.scout-cache/trigger-fires.json).

Day boundaries are ET (America/New_York) per the spec — caps reset at
00:00 ET, not 00:00 UTC.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from scout.triggers.dedup import DedupStore

T0 = dt.datetime(2026, 7, 1, 16, 0, 0, tzinfo=dt.UTC)  # 12:00 ET


def _store(tmp_path: Path, **kw) -> DedupStore:
    return DedupStore(tmp_path / "trigger-fires.json", **kw)


# ----- is_new ---------------------------------------------------------------


def test_unseen_event_is_new(tmp_path):
    store = _store(tmp_path)
    assert store.is_new("slack_mention_alex", "1700000000.000100")


def test_recorded_event_is_not_new(tmp_path):
    store = _store(tmp_path)
    store.record_fire("slack_mention_alex", "1700000000.000100", T0)
    assert not store.is_new("slack_mention_alex", "1700000000.000100")


def test_dedup_is_per_trigger(tmp_path):
    store = _store(tmp_path)
    store.record_fire("slack_mention_alex", "1700000000.000100", T0)
    assert store.is_new("other_trigger", "1700000000.000100")


def test_recent_ids_window_slides(tmp_path):
    """Beyond the sliding window, old event ids age out (non-monotonic id safety)."""
    store = _store(tmp_path, recent_window=3)
    for i in range(5):
        store.record_fire("t", f"ev-{i}", T0 + dt.timedelta(seconds=i))
    # ev-0 and ev-1 have aged out of the window of 3.
    assert store.is_new("t", "ev-0")
    assert not store.is_new("t", "ev-3")
    assert not store.is_new("t", "ev-4")


# ----- persistence ------------------------------------------------------------


def test_state_persists_across_instances(tmp_path):
    _store(tmp_path).record_fire("t", "ev-1", T0)
    reloaded = _store(tmp_path)
    assert not reloaded.is_new("t", "ev-1")
    assert reloaded.fires_today("t", T0) == 1


def test_file_shape_matches_spec(tmp_path):
    store = _store(tmp_path)
    store.record_fire("slack_mention_alex", "1700000000.000100", T0)
    data = json.loads((tmp_path / "trigger-fires.json").read_text())
    entry = data["slack_mention_alex"]
    assert entry["last_fire_ts"] == "2026-07-01T16:00:00Z"
    assert entry["last_seen_event_id"] == "1700000000.000100"
    assert entry["fires_today"] == 1
    assert entry["fires_today_date"] == "2026-07-01"  # ET date


def test_corrupt_cache_file_starts_fresh(tmp_path):
    p = tmp_path / "trigger-fires.json"
    p.write_text("{not json", encoding="utf-8")
    store = DedupStore(p)
    assert store.is_new("t", "ev-1")
    store.record_fire("t", "ev-1", T0)  # must not raise
    assert not store.is_new("t", "ev-1")


# ----- cooldown ----------------------------------------------------------------


def test_cooldown_blocks_within_gap(tmp_path):
    store = _store(tmp_path)
    store.record_fire("t", "ev-1", T0)
    assert store.in_cooldown("t", 1800, T0 + dt.timedelta(minutes=10))
    assert not store.in_cooldown("t", 1800, T0 + dt.timedelta(minutes=40))


def test_zero_cooldown_never_blocks(tmp_path):
    store = _store(tmp_path)
    store.record_fire("t", "ev-1", T0)
    assert not store.in_cooldown("t", 0, T0 + dt.timedelta(seconds=1))


def test_never_fired_trigger_is_not_in_cooldown(tmp_path):
    assert not _store(tmp_path).in_cooldown("t", 3600, T0)


# ----- daily cap (ET day boundary) ----------------------------------------------


def test_fires_today_counts_within_et_day(tmp_path):
    store = _store(tmp_path)
    store.record_fire("t", "ev-1", T0)
    store.record_fire("t", "ev-2", T0 + dt.timedelta(hours=1))
    assert store.fires_today("t", T0 + dt.timedelta(hours=2)) == 2


def test_fires_today_resets_at_midnight_et_not_utc(tmp_path):
    store = _store(tmp_path)
    # 2026-07-02T03:00Z is still 2026-07-01 23:00 ET (EDT, UTC-4).
    late_et = dt.datetime(2026, 7, 2, 3, 0, 0, tzinfo=dt.UTC)
    store.record_fire("t", "ev-1", late_et)
    assert store.fires_today("t", late_et) == 1
    # 2026-07-02T05:00Z crosses into 2026-07-02 ET (01:00 ET) → reset.
    next_et_day = dt.datetime(2026, 7, 2, 5, 0, 0, tzinfo=dt.UTC)
    assert store.fires_today("t", next_et_day) == 0


def test_cap_notified_flag_is_per_et_day(tmp_path):
    store = _store(tmp_path)
    assert not store.cap_notified_today("t", T0)
    store.mark_cap_notified("t", T0)
    assert store.cap_notified_today("t", T0)
    # Next ET day: flag resets so a new cap-hit notifies again.
    tomorrow = T0 + dt.timedelta(days=1)
    assert not store.cap_notified_today("t", tomorrow)
