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
from datetime import datetime, time
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
    merged: dict[str, Any] = dict(seed.get("slots", {}))
    if overlay is not None and overlay.exists():
        ov = _load_yaml(overlay)
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
        return Slot(
            key=key,
            type=slot_type,
            runner=raw["runner"],
            fires_at_local=fires_at_raw,
            weekdays=tuple(weekdays_raw),
            missed_window_hours=int(raw["missed_window_hours"]),
            on_miss=on_miss,
            cooldown_minutes=int(raw["cooldown_minutes"]),
            budget_usd=raw.get("budget_usd"),
            tz=tz,
        )
    except (KeyError, ValueError) as e:
        raise ConfigError(f"slot {key}: malformed entry: {e}") from e
