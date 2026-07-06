"""Match trigger filter rules against normalized connector events.

The match language is deliberately small (enumerated types + typed attribute
filters — no regex, per the spec's prior-art survey):

- ``type`` must equal the event's normalized type. Mandatory.
- Any other key is an attribute filter against ``normalized_match_fields``:
  scalar → equality; list → membership; the literal ``"any"`` (or a list
  containing it) → wildcard.
- ``exclude_<field>: <value|list>`` inverts: matching values REJECT the event.
- ``exclude_self: true`` rejects events the source flagged ``is_self`` (the
  Scout user's own activity).

A filter naming a field the event doesn't carry does not match (strict).
"""

from __future__ import annotations

from typing import Any

from scout.triggers.sources.base import ConnectorEvent

_EXCLUDE_PREFIX = "exclude_"


def _is_wildcard(expected: Any) -> bool:
    return expected == "any" or (isinstance(expected, list) and "any" in expected)


def matches(match: dict[str, Any], event: ConnectorEvent) -> bool:
    """Return True iff ``event`` satisfies every rule in ``match``."""
    fields = event.normalized_match_fields
    if match.get("type") != fields.get("type"):
        return False

    for key, expected in match.items():
        if key == "type":
            continue
        if key == "exclude_self":
            if expected and fields.get("is_self"):
                return False
            continue
        if key.startswith(_EXCLUDE_PREFIX):
            field_name = key[len(_EXCLUDE_PREFIX) :]
            rejected = expected if isinstance(expected, list) else [expected]
            if fields.get(field_name) in rejected:
                return False
            continue
        if _is_wildcard(expected):
            continue
        if key not in fields:
            return False
        actual = fields[key]
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True
