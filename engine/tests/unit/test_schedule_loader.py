"""Unit tests for scout.schedule — YAML loader + slot semantics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.errors import ConfigError
from scout.schedule import (
    OnMissPolicy,
    Schedule,
    Slot,
    SlotPriority,
    SlotRuntime,
    SlotType,
    load_default_schedule,
    load_schedule,
    next_fires,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_load_default_schedule_returns_default_slots():
    sched = load_default_schedule()
    keys = set(sched.keys())
    # The 10 slot keys shipped in engine/scout/defaults/schedule.yaml.
    assert keys >= {
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


def test_slot_dataclass_has_typed_fields():
    sched = load_default_schedule()
    morning = sched["morning-briefing"]
    assert isinstance(morning, Slot)
    assert morning.type == SlotType.BRIEFING
    assert morning.fires_at_local == "08:00"
    assert "Mon" in morning.weekdays
    assert morning.on_miss == OnMissPolicy.FIRE
    assert morning.cooldown_minutes == 60
    assert morning.runner == "run-scout.sh"
    assert morning.budget_usd is None  # optional; absent in default
    assert morning.tz is None  # absent → system local


def test_slot_priority_order_is_briefing_consolidation_dreaming_research_manual():
    assert SlotPriority.BRIEFING.value > SlotPriority.CONSOLIDATION.value
    assert SlotPriority.CONSOLIDATION.value > SlotPriority.DREAMING.value
    assert SlotPriority.DREAMING.value > SlotPriority.RESEARCH.value
    assert SlotPriority.RESEARCH.value > SlotPriority.MANUAL.value


def test_load_schedule_from_explicit_path():
    sched = load_schedule(FIXTURES / "schedule-default.yaml")
    assert "morning-briefing" in sched


def test_unknown_slot_type_raises():
    overlay = FIXTURES / "schedule-edge-cases.yaml"
    # File has slot with `type: not-real-type`.
    with pytest.raises(ConfigError, match="not-real-type"):
        load_schedule(overlay)


def test_invalid_fires_at_local_format_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '25:99'\n"  # invalid hour
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="fires_at_local"):
        load_schedule(bad)


def test_invalid_weekday_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Funday]\n"  # not a valid weekday
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="weekday"):
        load_schedule(bad)


def test_empty_weekdays_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: []\n"  # empty
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="weekday"):
        load_schedule(bad)


def test_slot_target_today_in_system_local_tz_when_no_override():
    sched = load_default_schedule()
    morning = sched["morning-briefing"]
    # 'Now' set to a specific datetime in local tz; target_today should match.
    fake_now = datetime(2026, 5, 11, 6, 0, tzinfo=ZoneInfo("America/New_York"))  # Mon 6am EDT
    target = morning.target_today(now=fake_now)
    assert target.tzinfo is not None
    assert target.hour == 8
    assert target.minute == 0
    assert target.weekday() == 0  # Monday


def test_slot_target_today_honors_per_slot_tz_override(tmp_path):
    bad_or_explicit = tmp_path / "with-tz.yaml"
    bad_or_explicit.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  pacific-standup:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
        "    tz: America/Los_Angeles\n"
    )
    sched = load_schedule(bad_or_explicit)
    pac = sched["pacific-standup"]
    fake_now = datetime(2026, 5, 11, 14, 0, tzinfo=ZoneInfo("Europe/Prague"))  # 2pm Prague
    target = pac.target_today(now=fake_now)
    # Same wall-clock day; 8am Pacific = different absolute time than 8am Prague.
    assert target.tzinfo == ZoneInfo("America/Los_Angeles")
    assert target.hour == 8


def test_slot_target_today_returns_none_when_weekday_doesnt_match():
    sched = load_default_schedule()
    weekend = sched["weekend-briefing"]
    fake_now = datetime(2026, 5, 11, 6, 0, tzinfo=ZoneInfo("America/New_York"))  # Monday
    assert weekend.target_today(now=fake_now) is None


def test_schedule_keys_iter_lookup_contains():
    sched = load_default_schedule()
    keys = list(sched.keys())
    assert "morning-briefing" in keys
    assert "morning-briefing" in sched
    assert sched["morning-briefing"].type == SlotType.BRIEFING


def test_schedule_get_priority_for_slot_type():
    sched = load_default_schedule()
    morning = sched["morning-briefing"]
    consolidation = sched["morning-consolidation"]
    assert morning.priority > consolidation.priority


def test_by_type_filters_to_matching_slot_type():
    sched = load_default_schedule()
    briefings = sched.by_type(SlotType.BRIEFING)
    assert len(briefings) == 2  # morning-briefing + weekend-briefing
    assert all(s.type == SlotType.BRIEFING for s in briefings)
    consolidations = sched.by_type(SlotType.CONSOLIDATION)
    assert len(consolidations) == 4  # morning/midday/afternoon/evening
    assert sched.by_type(SlotType.MANUAL) == []


def test_missed_window_hours_must_be_positive(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 0\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="missed_window_hours"):
        load_schedule(bad)


def test_negative_cooldown_minutes_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: -5\n"
    )
    with pytest.raises(ConfigError, match="cooldown_minutes"):
        load_schedule(bad)


def test_unknown_schema_version_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 999\n"
        "slots:\n"
        "  any-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="schema_version"):
        load_schedule(bad)


def test_empty_runner_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  bad-slot:\n"
        "    type: briefing\n"
        "    runner: ''\n"
        "    fires_at_local: '08:00'\n"
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    with pytest.raises(ConfigError, match="runner"):
        load_schedule(bad)


def test_overlay_path_layered_on_seed_when_present(tmp_path, monkeypatch):
    """If <vault>/.scout-state/schedule.local.yaml exists, layer on top of the canonical."""
    canonical = tmp_path / "schedule.yaml"
    canonical.write_text(
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
    overlay = tmp_path / "schedule.local.yaml"
    overlay.write_text(
        "slots:\n  morning-briefing:\n    fires_at_local: '07:00'\n"  # override only this field
    )
    sched = load_schedule(canonical, overlay=overlay)
    assert sched["morning-briefing"].fires_at_local == "07:00"
    assert sched["morning-briefing"].on_miss == OnMissPolicy.FIRE  # inherited


# ---------------------------------------------------------------------------
# next_fires() — forward-looking next-fire computation
# ---------------------------------------------------------------------------

_ET = ZoneInfo("America/New_York")


def _make_schedule(slots: dict[str, Slot]) -> Schedule:
    return Schedule(slots)


def _make_slot(
    key: str,
    fires_at: str,
    weekdays: list[str],
    slot_type: SlotType = SlotType.BRIEFING,
    tz: str | None = None,
) -> Slot:
    return Slot(
        key=key,
        type=slot_type,
        runner="run-scout.sh",
        fires_at_local=fires_at,
        weekdays=tuple(weekdays),
        missed_window_hours=4,
        on_miss=OnMissPolicy.FIRE,
        cooldown_minutes=60,
        budget_usd=None,
        tz=tz,
    )


def test_next_fires_today_slot_still_ahead():
    """A slot that fires later today should appear in the results."""
    # Mon 2026-05-04 06:00 ET — morning-briefing fires at 08:00
    now = datetime(2026, 5, 4, 6, 0, tzinfo=_ET)
    slot = _make_slot("morning-briefing", "08:00", ["Mon", "Tue", "Wed", "Thu", "Fri"])
    sched = _make_schedule({"morning-briefing": slot})
    results = next_fires(sched, now=now, window_hours=24)
    assert len(results) == 1
    key, fire_dt = results[0]
    assert key == "morning-briefing"
    assert fire_dt.hour == 8
    assert fire_dt.minute == 0
    assert fire_dt > now


def test_next_fires_today_slot_already_past_gives_tomorrow():
    """A slot whose today-target has already passed rolls to tomorrow."""
    # Mon 2026-05-04 10:00 ET — morning-briefing was at 08:00 (already past)
    now = datetime(2026, 5, 4, 10, 0, tzinfo=_ET)
    slot = _make_slot("morning-briefing", "08:00", ["Mon", "Tue", "Wed", "Thu", "Fri"])
    sched = _make_schedule({"morning-briefing": slot})
    # 24-hour window: includes tomorrow Tue 08:00 (24h from Mon 10:00 is Tue 10:00)
    results = next_fires(sched, now=now, window_hours=24)
    assert len(results) == 1
    key, fire_dt = results[0]
    assert key == "morning-briefing"
    # Tomorrow is Tuesday 08:00 — within the 24h window
    assert fire_dt.weekday() == 1  # Tuesday
    assert fire_dt.hour == 8


def test_next_fires_weekday_filter_skips_non_matching_days():
    """Slots with weekday exclusions should skip excluded days."""
    # Saturday 2026-05-09 06:00 ET — morning-briefing only fires Mon-Fri
    now = datetime(2026, 5, 9, 6, 0, tzinfo=_ET)
    slot = _make_slot("morning-briefing", "08:00", ["Mon", "Tue", "Wed", "Thu", "Fri"])
    sched = _make_schedule({"morning-briefing": slot})
    # Next Mon is 2026-05-11 08:00; that's ~50h away — beyond a 24h window
    results = next_fires(sched, now=now, window_hours=24)
    assert results == []


def test_next_fires_multi_day_skip_finds_next_matching_weekday():
    """If next matching weekday is 2 days out, the result reflects that."""
    # Friday 2026-05-08 20:00 ET — weekend-briefing fires Sat/Sun at 08:30
    now = datetime(2026, 5, 8, 20, 0, tzinfo=_ET)
    slot = _make_slot("weekend-briefing", "08:30", ["Sat", "Sun"])
    sched = _make_schedule({"weekend-briefing": slot})
    # Sat 08:30 is ~12.5h ahead — within a 24h window
    results = next_fires(sched, now=now, window_hours=24)
    assert len(results) == 1
    key, fire_dt = results[0]
    assert key == "weekend-briefing"
    assert fire_dt.weekday() == 5  # Saturday
    assert fire_dt.hour == 8
    assert fire_dt.minute == 30


def test_next_fires_window_cutoff_excludes_slots_beyond_window():
    """Slots whose next fire is beyond the window should be excluded."""
    # Mon 2026-05-04 09:00 ET — slot fires at 08:00 weekdays (already past)
    # Next fire is Tue 08:00 = 23h ahead — within 24h window
    # But if window is 12h it falls outside.
    now = datetime(2026, 5, 4, 9, 0, tzinfo=_ET)
    slot = _make_slot("morning-briefing", "08:00", ["Mon", "Tue", "Wed", "Thu", "Fri"])
    sched = _make_schedule({"morning-briefing": slot})
    # 12h window: now + 12h = Mon 21:00; Tue 08:00 is beyond that
    results = next_fires(sched, now=now, window_hours=12)
    assert results == []


def test_next_fires_slot_tz_override_honored():
    """Per-slot tz override should be used when computing next fire."""
    # now is Mon 2026-05-04 07:00 ET (= 04:00 PT)
    # Slot fires at 08:00 PT (= 11:00 ET) on weekdays
    now = datetime(2026, 5, 4, 7, 0, tzinfo=_ET)
    slot = _make_slot(
        "pacific-standup",
        "08:00",
        ["Mon", "Tue", "Wed", "Thu", "Fri"],
        tz="America/Los_Angeles",
    )
    sched = _make_schedule({"pacific-standup": slot})
    results = next_fires(sched, now=now, window_hours=24)
    assert len(results) == 1
    key, fire_dt = results[0]
    assert key == "pacific-standup"
    # Should be 08:00 PT on Mon
    assert fire_dt.tzinfo == ZoneInfo("America/Los_Angeles")
    assert fire_dt.hour == 8
    # In ET terms: 08:00 PT = 11:00 ET, which is ahead of 07:00 ET
    et_equiv = fire_dt.astimezone(_ET)
    assert et_equiv.hour == 11
    assert fire_dt > now


def test_next_fires_mixed_tz_schedule_returns_both_in_order():
    """Two slots with different tz overrides should both appear, sorted by fire time."""
    # now Mon 2026-05-04 05:00 ET
    now = datetime(2026, 5, 4, 5, 0, tzinfo=_ET)
    et_slot = _make_slot("et-slot", "08:00", ["Mon", "Tue", "Wed", "Thu", "Fri"])
    pt_slot = _make_slot(
        "pt-slot",
        "08:00",
        ["Mon", "Tue", "Wed", "Thu", "Fri"],
        tz="America/Los_Angeles",
    )
    sched = _make_schedule({"et-slot": et_slot, "pt-slot": pt_slot})
    results = next_fires(sched, now=now, window_hours=24)
    assert len(results) == 2
    keys = [k for k, _ in results]
    # et-slot fires at 08:00 ET, pt-slot at 08:00 PT = 11:00 ET → et-slot is first
    assert keys[0] == "et-slot"
    assert keys[1] == "pt-slot"
    # Check chronological order
    assert results[0][1] < results[1][1]


def test_next_fires_empty_schedule_returns_empty_list():
    """Empty schedule should return an empty list, not raise."""
    sched = _make_schedule({})
    now = datetime(2026, 5, 4, 9, 0, tzinfo=_ET)
    results = next_fires(sched, now=now, window_hours=24)
    assert results == []


def test_next_fires_single_slot_precision():
    """Verify exact datetime values are correct for a known slot/now pair."""
    # Mon 2026-05-04 07:30:00 ET — slot fires 08:00 ET
    now = datetime(2026, 5, 4, 7, 30, 0, tzinfo=_ET)
    slot = _make_slot("morning-briefing", "08:00", ["Mon", "Tue", "Wed", "Thu", "Fri"])
    sched = _make_schedule({"morning-briefing": slot})
    results = next_fires(sched, now=now, window_hours=24)
    assert len(results) == 1
    _, fire_dt = results[0]
    expected = datetime(2026, 5, 4, 8, 0, 0, tzinfo=_ET)
    assert fire_dt == expected


def test_next_fires_default_schedule_returns_slots_within_24h():
    """Smoke test against the real default schedule — should return >=1 slot for most times."""
    # Mon 2026-05-04 07:00 ET — several slots should be in the next 24 hours
    now = datetime(2026, 5, 4, 7, 0, tzinfo=_ET)
    sched = load_default_schedule()
    results = next_fires(sched, now=now, window_hours=24)
    # Morning briefing (08:00), consolidations (11, 13, 17, 19), research (14),
    # dreaming-evening (18:30), dreaming-nightly (22) are all ahead of 07:00
    assert len(results) >= 5
    # All returned fire times should be strictly in the future
    for _, fire_dt in results:
        assert fire_dt > now
    # Results should be in chronological order
    fire_times = [fire_dt for _, fire_dt in results]
    assert fire_times == sorted(fire_times)


# ---------------------------------------------------------------------------
# SlotRuntime — Plan 6 Task 1: runtime enum field on Slot
# ---------------------------------------------------------------------------


def test_fires_at_local_single_digit_components_normalized(tmp_path):
    """#69: fires_at_local: '7:5' must be stored as '07:05' (zero-padded HH:MM)."""
    yaml_file = tmp_path / "schedule.yaml"
    yaml_file.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  early-slot:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '7:5'\n"  # single-digit hour and minute
        "    weekdays: [Mon, Tue, Wed, Thu, Fri]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: fire\n"
        "    cooldown_minutes: 60\n"
    )
    sched = load_schedule(yaml_file)
    assert sched["early-slot"].fires_at_local == "07:05", (
        f"Expected '07:05' but got {sched['early-slot'].fires_at_local!r}"
    )


def test_overlay_new_key_is_a_copy_not_alias(tmp_path, monkeypatch):
    """#55: merged[key] = dict(override) must not alias the overlay's raw dict.

    The bug: `merged[key] = override` stores the same object reference.
    If _build_slot (or any caller) adds/removes keys from `raw`, the caller's
    dict is mutated in place. Fix: `merged[key] = dict(override)`.

    We prove this by intercepting the `merged` dict at merge time:
    capture the id() of the dict stored under the new-slot key and compare it
    to the id() of the dict that came directly from `_load_yaml(overlay)`.
    They must differ (copy), not be the same object (alias).
    """
    from scout import schedule as sched_mod

    canonical = tmp_path / "schedule.yaml"
    canonical.write_text(
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
    overlay = tmp_path / "schedule.local.yaml"
    overlay.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  new-slot:\n"
        "    type: manual\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '10:00'\n"
        "    weekdays: [Mon]\n"
        "    missed_window_hours: 4\n"
        "    on_miss: skip\n"
        "    cooldown_minutes: 0\n"
    )

    # Capture the raw dict object passed to _build_slot for "new-slot"
    real_build_slot = sched_mod._build_slot
    captured: dict[str, dict] = {}

    def spy_build_slot(key, raw):
        captured[key] = raw
        return real_build_slot(key, raw)

    monkeypatch.setattr(sched_mod, "_build_slot", spy_build_slot)

    # Also capture the dict from _load_yaml for the overlay
    real_load_yaml = sched_mod._load_yaml
    overlay_raws: list[dict] = []

    def spy_load_yaml(path):
        result = real_load_yaml(path)
        if path == overlay:
            overlay_raws.append(result)
        return result

    monkeypatch.setattr(sched_mod, "_load_yaml", spy_load_yaml)

    sched = load_schedule(canonical, overlay=overlay)
    assert "new-slot" in sched
    assert len(overlay_raws) == 1
    overlay_new_slot_dict = overlay_raws[0]["slots"]["new-slot"]
    built_new_slot_dict = captured["new-slot"]

    # The fix: these must be different objects (a copy was made)
    assert built_new_slot_dict is not overlay_new_slot_dict, (
        "merged[key] = override aliases the overlay dict; must be dict(override)"
    )


def test_slot_runtime_defaults_to_local_when_absent(tmp_path):
    """YAML without a runtime key should produce Slot.runtime == LOCAL."""
    yaml_file = tmp_path / "schedule.yaml"
    yaml_file.write_text(
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
        # runtime key intentionally absent
    )
    sched = load_schedule(yaml_file)
    assert sched["morning-briefing"].runtime == SlotRuntime.LOCAL


def test_slot_runtime_parses_local_explicitly(tmp_path):
    """YAML with runtime: local should parse to SlotRuntime.LOCAL."""
    yaml_file = tmp_path / "schedule.yaml"
    yaml_file.write_text(
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
        "    runtime: local\n"
    )
    sched = load_schedule(yaml_file)
    assert sched["morning-briefing"].runtime == SlotRuntime.LOCAL


def test_slot_runtime_parses_remote(tmp_path):
    """YAML with runtime: remote should parse to SlotRuntime.REMOTE.

    Note: the dispatcher guard (Task 2) is what rejects REMOTE at fire-time.
    The loader accepts it without error.
    """
    yaml_file = tmp_path / "schedule.yaml"
    yaml_file.write_text(
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
        "    runtime: remote\n"
    )
    sched = load_schedule(yaml_file)
    assert sched["morning-briefing"].runtime == SlotRuntime.REMOTE


def test_slot_runtime_invalid_value_raises_config_error(tmp_path):
    """YAML with an unrecognized runtime value should raise ConfigError mentioning 'runtime'."""
    yaml_file = tmp_path / "schedule.yaml"
    yaml_file.write_text(
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
        "    runtime: cloud\n"  # not a valid SlotRuntime value
    )
    with pytest.raises(ConfigError, match="runtime"):
        load_schedule(yaml_file)
