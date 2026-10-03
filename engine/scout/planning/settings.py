"""The ``planning:`` config block that /scout-plan reads via ``scoutctl planning show``.

Every value has a bound. A value outside it falls back to the default with a
one-line stderr warning, the same tolerance the budget and agent-session blocks
use: a hand-mangled vault file must never stop the user from planning a day.
"""

from __future__ import annotations

import dataclasses
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from scout import config as scout_config

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_ALLOWED_INCREMENTS = (5, 10, 15, 20, 30, 60)
_VISIBILITIES = ("private", "default", "public")
_AVAILABILITIES = ("busy", "free")

# name -> (low, high) inclusive
_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "capacity_pct": (10, 100),
    "deep_block_minutes": (15, 240),
    "batch_block_minutes": (15, 240),
    "meeting_gap_minutes": (0, 60),
    "calibration_min_samples": (1, 100),
    "calibration_window": (1, 500),
}
_FLOAT_BOUNDS: dict[str, tuple[float, float]] = {
    "buffer": (1.0, 3.0),
    "new_work_buffer": (1.0, 3.0),
    "calibration_min_factor": (0.25, 1.0),
    "calibration_max_factor": (1.0, 5.0),
}
# Lengths that must be whole steps of the grid.
_GRID_FIELDS = ("deep_block_minutes", "batch_block_minutes", "meeting_gap_minutes")


@dataclass(frozen=True)
class PlanningSettings:
    work_start: str = "09:00"
    work_end: str = "17:00"
    increment_minutes: int = 15
    capacity_pct: int = 65
    buffer: float = 1.25
    new_work_buffer: float = 1.5
    deep_block_minutes: int = 90
    batch_block_minutes: int = 45
    meeting_gap_minutes: int = 15
    calibration_min_samples: int = 5
    calibration_window: int = 20
    calibration_min_factor: float = 0.75
    calibration_max_factor: float = 2.5
    event_title_prefix: str = "[Scout]"
    event_visibility: str = "private"
    event_availability: str = "busy"

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> PlanningSettings:
        block = cfg.get("planning")
        if not isinstance(block, dict):
            return cls()
        default = cls()
        out = default

        for name in ("work_start", "work_end"):
            if name in block:
                raw = block[name]
                if isinstance(raw, str) and _HHMM_RE.match(raw.strip()):
                    out = replace(out, **cast(dict[str, Any], {name: raw.strip()}))
                else:
                    _warn(f"planning.{name}: expected HH:MM, got {raw!r}; using default")
        if out.work_end <= out.work_start:
            _warn(f"planning.work_end ({out.work_end}) must be after work_start ({out.work_start}); using defaults")
            out = replace(out, work_start=default.work_start, work_end=default.work_end)

        if "increment_minutes" in block:
            value = _as_int(block["increment_minutes"])
            if value in _ALLOWED_INCREMENTS:
                out = replace(out, increment_minutes=value)
            else:
                _warn(
                    f"planning.increment_minutes: expected one of {_ALLOWED_INCREMENTS}, "
                    f"got {block['increment_minutes']!r}; using default"
                )

        for name in ("work_start", "work_end"):
            hh, mm = getattr(out, name).split(":")
            if (int(hh) * 60 + int(mm)) % out.increment_minutes:
                _warn(
                    f"planning.{name} ({getattr(out, name)}) is not on the {out.increment_minutes}-minute grid; "
                    "using the default work hours"
                )
                out = replace(out, work_start=default.work_start, work_end=default.work_end)
                break

        for name, (low, high) in _INT_BOUNDS.items():
            if name not in block:
                continue
            value = _as_int(block[name])
            if value is None or not low <= value <= high:
                _warn(f"planning.{name}: expected an integer in {low}..{high}, got {block[name]!r}; using default")
                continue
            if name in _GRID_FIELDS and value % out.increment_minutes:
                _warn(f"planning.{name}: {value} is not on the {out.increment_minutes}-minute grid; using default")
                continue
            out = replace(out, **cast(dict[str, Any], {name: value}))

        for name, (flow, fhigh) in _FLOAT_BOUNDS.items():
            if name not in block:
                continue
            fvalue = _as_float(block[name])
            if fvalue is None or not flow <= fvalue <= fhigh:
                _warn(f"planning.{name}: expected a number in {flow}..{fhigh}, got {block[name]!r}; using default")
                continue
            out = replace(out, **cast(dict[str, Any], {name: fvalue}))
        # The two calibration bands meet at 1.0, so min <= max always holds.

        if "event_title_prefix" in block:
            raw = block["event_title_prefix"]
            if isinstance(raw, str) and raw.strip():
                out = replace(out, event_title_prefix=raw.strip())
            else:
                _warn(f"planning.event_title_prefix: expected a non-empty string, got {raw!r}; using default")
        for name, allowed in (("event_visibility", _VISIBILITIES), ("event_availability", _AVAILABILITIES)):
            if name in block:
                raw = block[name]
                if isinstance(raw, str) and raw.strip().lower() in allowed:
                    out = replace(out, **cast(dict[str, Any], {name: raw.strip().lower()}))
                else:
                    _warn(f"planning.{name}: expected one of {allowed}, got {raw!r}; using default")
        return out

    def to_json_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def load_settings(data_dir: Path | None = None) -> PlanningSettings:
    return PlanningSettings.from_config(scout_config.load_config(data_dir))


def _as_int(raw: object) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    return None


def _as_float(raw: object) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return None
    return None


def _warn(msg: str) -> None:
    print(f"scout-config: {msg}", file=sys.stderr)


__all__ = ["PlanningSettings", "load_settings"]
