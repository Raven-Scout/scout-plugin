"""Unit tests for scout.schedule — YAML loader + slot semantics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.errors import ConfigError
from scout.schedule import (
    OnMissPolicy,
    Slot,
    SlotPriority,
    SlotType,
    load_default_schedule,
    load_schedule,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_load_default_schedule_returns_jordan_default_slots():
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
