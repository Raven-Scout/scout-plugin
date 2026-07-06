"""Connector contract for trigger sources (spec §Connector contract).

Every ``sources/*.py`` implements :class:`TriggerSource`: a ``scan_since(ts)``
poller returning normalized :class:`ConnectorEvent` rows, plus a per-tick
``health_check``. The webhook half of the protocol is v2-only; v1 pollers
return ``False`` / ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ConnectorEvent:
    """One normalized event from a trigger source.

    ``normalized_match_fields`` is what the matcher reads — each source
    flattens its native event shape into a stable set of keys, always
    including ``type`` (the source-scoped match type, e.g. ``mention``).
    ``source_event_id`` is connector-specific (Slack message TS, GitHub
    notification thread id, engine event ULID) and feeds dedup.
    """

    source: str
    source_event_id: str
    ts: str  # ISO 8601 UTC
    raw_payload: dict[str, Any] = field(default_factory=dict)
    normalized_match_fields: dict[str, Any] = field(default_factory=dict)

    @property
    def match_type(self) -> str:
        return str(self.normalized_match_fields.get("type", ""))


@runtime_checkable
class TriggerSource(Protocol):
    """What every trigger source must implement."""

    name: str
    SUPPORTED_MATCH_TYPES: list[str]

    def scan_since(self, ts: str) -> list[ConnectorEvent]:
        """Return all events from this source since ``ts``. Idempotent."""
        ...

    def health_check(self) -> tuple[bool, str]:
        """Return (is_healthy, reason). Called per tick."""
        ...

    def supports_webhook(self) -> bool:
        """v2 only — does this source emit signed webhooks?"""
        ...
