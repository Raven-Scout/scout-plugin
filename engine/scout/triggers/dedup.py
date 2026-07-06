"""Per-trigger dedup + cooldown + daily-cap state.

Backing file: ``.scout-cache/trigger-fires.json``, keyed by trigger id::

    {
      "slack_mention_alex": {
        "last_fire_ts": "2026-05-19T14:32:11Z",
        "last_seen_event_id": "1747663931.001234",
        "recent_event_ids": ["...", ...],
        "fires_today": 7,
        "fires_today_date": "2026-05-19",
        "cap_notified_date": "2026-05-19"
      }
    }

``fires_today_date`` is the ET date (America/New_York) — daily caps reset at
00:00 ET, NOT 00:00 UTC, matching the rest of Scout's day-boundary semantics.

``is_new`` consults both ``last_seen_event_id`` and a sliding window of the
100 most recent fired event ids, so non-monotonic event ids can't re-fire.
Contract is at-least-once with dedup: a corrupt/deleted cache file starts
fresh rather than raising (the tick must never die on dedup state).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_RECENT_WINDOW = 100
DAY_BOUNDARY_ZONE = ZoneInfo("America/New_York")


def _parse_iso_z(ts: str) -> dt.datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return dt.datetime.fromisoformat(ts)


def _utc_z(now: dt.datetime) -> str:
    return now.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class DedupStore:
    """Read/write view over trigger-fires.json. One instance per tick."""

    def __init__(self, path: Path, *, recent_window: int = DEFAULT_RECENT_WINDOW) -> None:
        self._path = path
        self._recent_window = recent_window
        self._state: dict[str, dict[str, Any]] = {}
        try:
            with path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._state = {k: v for k, v in loaded.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError):
            # Missing or corrupt cache → start fresh (at-least-once contract).
            self._state = {}

    # ----- queries ---------------------------------------------------------

    def is_new(self, trigger_id: str, event_id: str) -> bool:
        entry = self._state.get(trigger_id)
        if entry is None:
            return True
        if event_id == entry.get("last_seen_event_id"):
            return False
        return event_id not in entry.get("recent_event_ids", [])

    def in_cooldown(self, trigger_id: str, cooldown_seconds: int, now: dt.datetime) -> bool:
        if cooldown_seconds <= 0:
            return False
        entry = self._state.get(trigger_id)
        if entry is None or not entry.get("last_fire_ts"):
            return False
        try:
            last_fire = _parse_iso_z(entry["last_fire_ts"])
        except ValueError:
            return False
        return (now - last_fire) < dt.timedelta(seconds=cooldown_seconds)

    def fires_today(self, trigger_id: str, now: dt.datetime) -> int:
        entry = self._state.get(trigger_id)
        if entry is None:
            return 0
        if entry.get("fires_today_date") != self._day(now):
            return 0
        return int(entry.get("fires_today", 0))

    def cap_notified_today(self, trigger_id: str, now: dt.datetime) -> bool:
        entry = self._state.get(trigger_id)
        return entry is not None and entry.get("cap_notified_date") == self._day(now)

    def state(self, trigger_id: str) -> dict[str, Any]:
        """A copy of one trigger's raw entry (for `scoutctl trigger show`)."""
        return dict(self._state.get(trigger_id, {}))

    # ----- mutations -------------------------------------------------------

    def record_fire(self, trigger_id: str, event_id: str, now: dt.datetime) -> None:
        entry = self._state.setdefault(trigger_id, {})
        today = self._day(now)
        fires = int(entry.get("fires_today", 0)) if entry.get("fires_today_date") == today else 0
        recent = [e for e in entry.get("recent_event_ids", []) if e != event_id]
        recent.append(event_id)
        entry.update(
            {
                "last_fire_ts": _utc_z(now),
                "last_seen_event_id": event_id,
                "recent_event_ids": recent[-self._recent_window :],
                "fires_today": fires + 1,
                "fires_today_date": today,
            }
        )
        self._save()

    def mark_cap_notified(self, trigger_id: str, now: dt.datetime) -> None:
        entry = self._state.setdefault(trigger_id, {})
        entry["cap_notified_date"] = self._day(now)
        self._save()

    # ----- internals -------------------------------------------------------

    def _day(self, now: dt.datetime) -> str:
        return now.astimezone(DAY_BOUNDARY_ZONE).date().isoformat()

    def _save(self) -> None:
        """Atomic write (tmp + rename). Best-effort — never raises."""
        tmp = self._path.with_suffix(".json.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=1)
            os.replace(tmp, self._path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
