"""One-line event summaries shared by the action handlers."""

from __future__ import annotations

from scout.triggers.config import Trigger
from scout.triggers.sources.base import ConnectorEvent

_SUMMARY_FIELD_ORDER = ("text", "title", "slot_key", "reason")
MAX_SUMMARY_CHARS = 280


def summarize(trigger: Trigger, event: ConnectorEvent) -> str:
    """``[trigger:<id>] <source>/<type>: <best content field>`` — surface-safe."""
    fields = event.normalized_match_fields
    content = next(
        (str(fields[k]) for k in _SUMMARY_FIELD_ORDER if fields.get(k)),
        event.source_event_id,
    )
    if len(content) > MAX_SUMMARY_CHARS:
        content = content[: MAX_SUMMARY_CHARS - 1] + "…"
    return f"[trigger:{trigger.id}] {event.source}/{event.match_type}: {content}"
