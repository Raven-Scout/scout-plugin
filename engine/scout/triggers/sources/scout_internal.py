"""scout_internal trigger source — the engine's own event stream.

v1 reads the JSONL event logs the schedule dispatcher already writes
(``.scout-logs/schedule-events-YYYY-MM-DD.jsonl``); when the v0.5 SQLite
event store lands, ``scan_since`` becomes a SELECT against it (spec
§Event-store integration).
"""

from __future__ import annotations

import json
from pathlib import Path

from scout.triggers.sources.base import ConnectorEvent

# Engine event kinds the dispatcher emits today (schedule_tick + triggers +
# notify). A trigger's match.type must name one of these.
SUPPORTED_MATCH_TYPES: list[str] = [
    "notification.sent",
    "schedule.tick.completed",
    "schedule.tick.skipped",
    "slot.fire_failed",
    "slot.fired",
    "slot.skipped",
    "trigger.fired",
]

EVENT_LOG_GLOB = "schedule-events-*.jsonl"


class ScoutInternalSource:
    """Scan the engine's JSONL event logs for rows newer than ``ts``."""

    name = "scout_internal"
    SUPPORTED_MATCH_TYPES = SUPPORTED_MATCH_TYPES

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    def scan_since(self, ts: str) -> list[ConnectorEvent]:
        if not self._log_dir.is_dir():
            return []
        since_date = ts[:10]
        events: list[ConnectorEvent] = []
        for log_path in sorted(self._log_dir.glob(EVENT_LOG_GLOB)):
            # File names are UTC-dated; skip files that can't contain rows > ts.
            file_date = log_path.stem.removeprefix("schedule-events-")
            if file_date < since_date:
                continue
            events.extend(self._scan_file(log_path, ts))
        events.sort(key=lambda e: e.ts)
        return events

    def _scan_file(self, log_path: Path, since_ts: str) -> list[ConnectorEvent]:
        out: list[ConnectorEvent] = []
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return out
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            row_id, row_ts, kind = row.get("id"), row.get("ts"), row.get("kind")
            if not (isinstance(row_id, str) and isinstance(row_ts, str) and isinstance(kind, str)):
                continue
            # Both timestamps are ISO-8601 UTC "Z" strings → lexicographic
            # comparison is chronological.
            if row_ts <= since_ts:
                continue
            raw_payload = row.get("payload")
            payload: dict = raw_payload if isinstance(raw_payload, dict) else {}
            fields = {k: v for k, v in payload.items() if k != "type"}
            fields["type"] = kind
            fields["event_source"] = row.get("source", "")
            out.append(
                ConnectorEvent(
                    source=self.name,
                    source_event_id=row_id,
                    ts=row_ts,
                    raw_payload=dict(row),
                    normalized_match_fields=fields,
                )
            )
        return out

    def health_check(self) -> tuple[bool, str]:
        if not self._log_dir.is_dir():
            return False, f"log dir does not exist: {self._log_dir}"
        return True, "ok"

    def supports_webhook(self) -> bool:
        return False
