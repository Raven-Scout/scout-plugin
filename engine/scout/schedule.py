"""Schedule registry: vault-canonical schedule.yaml loader + slot semantics.

Schedule definition lives at `~/Scout/.scout-state/schedule.yaml`; the engine
ships defaults at `engine/scout/defaults/schedule.yaml` that `scoutctl schedule
init` copies on first run. The vault file is the single source of truth at
runtime.

Slot wall-clock times are interpreted in the system's current local timezone
by default (TZ-aware by construction — travel ET → CEST and the schedule moves
with you). Optional per-slot `tz: <iana-zone>` field pins a slot to a fixed
zone if needed.

See ~/scout-app/docs/superpowers/specs/2026-05-04-schedule-v2-design.md.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from scout.errors import ConfigError


class SlotType(enum.Enum):
    BRIEFING = "briefing"
    CONSOLIDATION = "consolidation"
    DREAMING = "dreaming"
    RESEARCH = "research"
    MANUAL = "manual"


class OnMissPolicy(enum.Enum):
    FIRE = "fire"
    SKIP = "skip"
    COLLAPSE = "collapse"


class SlotRuntime(enum.Enum):
    LOCAL = "local"
    REMOTE = "remote"  # Reserved for a future plan (remote routine integration via Anthropic routines API); not yet wired. Loader accepts; dispatcher rejects.


class SlotPriority(enum.IntEnum):
    """Priority order for single-fire-per-tick selection.

    Higher integer = fires first when multiple slots are eligible at the
    same tick. Hardcoded; not user-configurable. See design doc §4 step 6.
    """

    BRIEFING = 50
    CONSOLIDATION = 40
    DREAMING = 30
    RESEARCH = 20
    MANUAL = 10


_VALID_WEEKDAYS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


@dataclass(frozen=True)
class Slot:
    """One scheduled slot. Frozen — load_schedule rebuilds; never mutated."""

    key: str  # user-chosen kebab-case identifier
    type: SlotType  # fixed plugin vocabulary
    runner: str  # script name relative to vault root
    fires_at_local: str  # "HH:MM" 24-hour
    weekdays: tuple[str, ...]  # subset of _VALID_WEEKDAYS
    missed_window_hours: int
    on_miss: OnMissPolicy
    cooldown_minutes: int
    budget_usd: float | None = None  # optional; not load-bearing in v0.5
    tz: str | None = None  # optional IANA zone; absent → system local
    runtime: SlotRuntime = SlotRuntime.LOCAL

    @property
    def priority(self) -> SlotPriority:
        """Map slot type to its priority for single-fire-per-tick selection."""
        return _PRIORITY_BY_TYPE[self.type]

    def target_today(self, *, now: datetime) -> datetime | None:
        """Return today's target datetime for this slot, or None if today's weekday is excluded.

        `now` must be tz-aware. Slot's tz override (or system local) is honored.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        slot_tz = ZoneInfo(self.tz) if self.tz else now.tzinfo
        local_today = now.astimezone(slot_tz)
        weekday_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][local_today.weekday()]
        if weekday_name not in self.weekdays:
            return None
        hh, mm = self.fires_at_local.split(":")
        return local_today.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)


_PRIORITY_BY_TYPE: dict[SlotType, SlotPriority] = {
    SlotType.BRIEFING: SlotPriority.BRIEFING,
    SlotType.CONSOLIDATION: SlotPriority.CONSOLIDATION,
    SlotType.DREAMING: SlotPriority.DREAMING,
    SlotType.RESEARCH: SlotPriority.RESEARCH,
    SlotType.MANUAL: SlotPriority.MANUAL,
}


class Schedule:
    """Indexed view over loaded slots. Use load_schedule() / load_default_schedule()."""

    def __init__(self, slots: dict[str, Slot]) -> None:
        self._slots = slots

    def __contains__(self, key: str) -> bool:
        return key in self._slots

    def __getitem__(self, key: str) -> Slot:
        return self._slots[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._slots)

    def keys(self):
        return self._slots.keys()

    def values(self):
        return self._slots.values()

    def items(self):
        return self._slots.items()

    def by_type(self, slot_type: SlotType) -> list[Slot]:
        return [s for s in self._slots.values() if s.type == slot_type]


def load_default_schedule() -> Schedule:
    """Load the plugin-shipped default schedule.

    Used by tests, `scoutctl schedule init` (to seed the vault), and
    fallback loaders when the vault file is absent.
    """
    return load_schedule(Path(__file__).parent / "defaults" / "schedule.yaml")


def load_schedule(
    canonical_path: Path,
    *,
    overlay: Path | None = None,
) -> Schedule:
    """Load a schedule.yaml. Optionally layer an overlay file on top.

    The overlay is shallow-merged into each slot key (matches Plan 4's
    connectors overlay pattern). Validation runs after the merge.
    """
    seed = _load_yaml(canonical_path)
    version = seed.get("schema_version", 1)
    if version != 1:
        raise ConfigError(f"schedule.yaml at {canonical_path} has schema_version {version}; engine supports 1")
    merged: dict[str, Any] = dict(seed.get("slots", {}))
    if overlay is not None and overlay.exists():
        ov = _load_yaml(overlay)
        ov_version = ov.get("schema_version", 1)
        if ov_version != 1:
            raise ConfigError(f"schedule.yaml at {overlay} has schema_version {ov_version}; engine supports 1")
        for key, override in ov.get("slots", {}).items():
            if key in merged:
                merged[key] = {**merged[key], **override}
            else:
                merged[key] = override
    slots: dict[str, Slot] = {}
    for key, raw in merged.items():
        slots[key] = _build_slot(key, raw)
    return Schedule(slots)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"schedule yaml at {path} not found") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"schedule yaml at {path} is malformed: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"schedule yaml at {path} is not a mapping")
    return data


def _build_slot(key: str, raw: dict[str, Any]) -> Slot:
    try:
        slot_type = SlotType(raw["type"])
        weekdays_raw = raw.get("weekdays", [])
        if not weekdays_raw:
            raise ConfigError(f"slot {key}: weekdays must be a non-empty list")
        for wd in weekdays_raw:
            if wd not in _VALID_WEEKDAYS:
                raise ConfigError(f"slot {key}: invalid weekday {wd!r}; must be one of {sorted(_VALID_WEEKDAYS)}")
        fires_at_raw = str(raw.get("fires_at_local", ""))
        try:
            hh, mm = fires_at_raw.split(":")
            time(int(hh), int(mm))  # validate via stdlib
        except (ValueError, AttributeError) as e:
            raise ConfigError(f"slot {key}: fires_at_local {fires_at_raw!r} is not HH:MM 24-hour") from e
        on_miss = OnMissPolicy(raw["on_miss"])
        tz = raw.get("tz")
        if tz is not None:
            try:
                ZoneInfo(tz)
            except ZoneInfoNotFoundError as e:
                raise ConfigError(f"slot {key}: unknown tz {tz!r}") from e
        runner = raw["runner"]
        if not isinstance(runner, str) or not runner.strip():
            raise ConfigError(f"slot {key}: runner must be a non-empty string")
        mw = int(raw["missed_window_hours"])
        if mw <= 0:
            raise ConfigError(f"slot {key}: missed_window_hours must be > 0, got {mw}")
        cd = int(raw["cooldown_minutes"])
        if cd < 0:
            raise ConfigError(f"slot {key}: cooldown_minutes must be >= 0, got {cd}")
        runtime_raw = raw.get("runtime", "local")
        try:
            runtime = SlotRuntime(runtime_raw)
        except ValueError as e:
            raise ConfigError(
                f"slot {key!r}: runtime {runtime_raw!r} is not one of {[r.value for r in SlotRuntime]}"
            ) from e
        return Slot(
            key=key,
            type=slot_type,
            runner=runner,
            fires_at_local=fires_at_raw,
            weekdays=tuple(weekdays_raw),
            missed_window_hours=mw,
            on_miss=on_miss,
            cooldown_minutes=cd,
            budget_usd=raw.get("budget_usd"),
            tz=tz,
            runtime=runtime,
        )
    except (KeyError, ValueError) as e:
        raise ConfigError(f"slot {key}: malformed entry: {e}") from e


_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def next_fires(
    schedule: Schedule,
    *,
    now: datetime,
    window_hours: float = 24.0,
) -> list[tuple[str, datetime]]:
    """Return (slot_key, next_fire_datetime) pairs for slots firing within window.

    For each slot, finds the soonest fire time strictly in the future that
    falls within ``now + window_hours``. Results are sorted chronologically.
    Does NOT consult the tracker — this is pure forward-looking schema math.

    DST note: on spring-forward day, a ``fires_at_local`` that lands in the
    missing hour (e.g. '02:30' on 2026-03-08 in America/New_York) silently
    uses the pre-shift UTC offset via ``datetime.replace``. Acceptable for
    Scout's defaults (no slot fires in the gap); revisit if a slot ever
    schedules in that window.

    Args:
        schedule: The loaded Schedule to scan.
        now: Timezone-aware datetime representing the current moment.
        window_hours: How many hours ahead to look (default 24).

    Returns:
        List of (slot_key, fire_datetime) tuples, sorted by fire_datetime.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    cutoff = now + timedelta(hours=window_hours)
    results: list[tuple[str, datetime]] = []

    for key, slot in schedule.items():
        slot_tz = ZoneInfo(slot.tz) if slot.tz else now.tzinfo
        hh, mm = slot.fires_at_local.split(":")
        hour = int(hh)
        minute = int(mm)

        for day_offset in range(8):
            candidate_day = (now + timedelta(days=day_offset)).astimezone(slot_tz)
            weekday_name = _WEEKDAY_NAMES[candidate_day.weekday()]
            if weekday_name not in slot.weekdays:
                continue
            # Build the target datetime on that calendar day in the slot's tz
            target = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                continue
            # Found the next eligible fire for this slot
            if target <= cutoff:
                results.append((key, target))
            break  # whether inside or outside window, this slot is done

    results.sort(key=lambda x: x[1])
    return results
