"""Durations and block windows on the planning minute grid.

Durations are written the way people say them: ``15m``, ``45m``, ``1h``,
``1h15m``. Everything /scout-plan writes sits on ``planning.increment_minutes``
(15 by default), so an estimate, the calendar block it became and the actual
time recorded afterwards are always comparable.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass

_DURATION_RE = re.compile(r"^(?:(?P<h>\d+)\s*h)?\s*(?:(?P<m>\d+)\s*m)?$", re.IGNORECASE)
_HHMM_RE = re.compile(r"^(?P<h>\d{2}):(?P<m>\d{2})$")


def parse_duration(text: str) -> int:
    """``"1h15m"`` -> 75. Raises ValueError on anything that is not a positive h/m duration."""
    candidate = text.strip()
    m = _DURATION_RE.match(candidate)
    if not candidate or m is None or (m.group("h") is None and m.group("m") is None):
        raise ValueError(f"not a duration: {text!r} (use e.g. 15m, 45m, 1h, 1h15m)")
    minutes = int(m.group("h") or 0) * 60 + int(m.group("m") or 0)
    if minutes <= 0:
        raise ValueError(f"duration must be positive: {text!r}")
    return minutes


def format_duration(minutes: int) -> str:
    """75 -> ``"1h15m"``, 60 -> ``"1h"``, 45 -> ``"45m"``."""
    if minutes <= 0:
        raise ValueError(f"duration must be positive, got {minutes}")
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return f"{hours}h{rest}m"
    if hours:
        return f"{hours}h"
    return f"{rest}m"


def round_up(minutes: int, increment: int) -> int:
    """Round up to the next multiple of ``increment`` (never below one increment)."""
    if increment <= 0:
        raise ValueError(f"increment must be positive, got {increment}")
    return max(increment, math.ceil(minutes / increment) * increment)


def require_on_grid(minutes: int, increment: int) -> None:
    """Raise ValueError unless ``minutes`` is a positive multiple of ``increment``."""
    if minutes <= 0 or minutes % increment:
        raise ValueError(f"{minutes} minutes is not on the {increment}-minute grid")


def _parse_hhmm(text: str) -> dt.time:
    m = _HHMM_RE.match(text.strip())
    if m is None:
        raise ValueError(f"not an HH:MM time: {text!r}")
    return dt.time(int(m.group("h")), int(m.group("m")))


@dataclass(frozen=True)
class BlockWindow:
    day: dt.date
    start: dt.time
    end: dt.time

    @property
    def minutes(self) -> int:
        return (self.end.hour * 60 + self.end.minute) - (self.start.hour * 60 + self.start.minute)

    def render(self) -> str:
        return f"{self.day.isoformat()} {self.start:%H:%M}-{self.end:%H:%M}"


def parse_block_window(day: str, start: str, end: str, *, increment: int) -> BlockWindow:
    """Validate a same-day block whose edges and length sit on the grid."""
    try:
        parsed_day = dt.date.fromisoformat(day.strip())
    except ValueError as e:
        raise ValueError(f"not a YYYY-MM-DD date: {day!r}") from e
    window = BlockWindow(parsed_day, _parse_hhmm(start), _parse_hhmm(end))
    if window.minutes <= 0:
        raise ValueError(f"block must end after it starts: {start}-{end}")
    for edge in (window.start, window.end):
        if (edge.hour * 60 + edge.minute) % increment:
            raise ValueError(f"block edge {edge:%H:%M} is not on the {increment}-minute grid")
    return window


__all__ = [
    "BlockWindow",
    "format_duration",
    "parse_block_window",
    "parse_duration",
    "require_on_grid",
    "round_up",
]
