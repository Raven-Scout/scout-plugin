"""Matcher semantics: trigger match rules vs normalized connector events.

Fixtures are synthetic/anonymized per CLAUDE.md.
"""

from __future__ import annotations

from typing import Any

from scout.triggers.matcher import matches
from scout.triggers.sources.base import ConnectorEvent


def _event(match_type: str = "mention", **fields: Any) -> ConnectorEvent:
    return ConnectorEvent(
        source="slack",
        source_event_id="1700000000.000100",
        ts="2026-07-01T12:00:00.000Z",
        raw_payload={},
        normalized_match_fields={"type": match_type, **fields},
    )


def test_type_must_match():
    assert matches({"type": "mention"}, _event("mention"))
    assert not matches({"type": "mention"}, _event("reaction"))


def test_scalar_filter_requires_equality():
    ev = _event(author="alex", channel="C0123456789")
    assert matches({"type": "mention", "author": "alex"}, ev)
    assert not matches({"type": "mention", "author": "priya"}, ev)


def test_list_filter_means_membership():
    ev = _event(channel="C0123456789")
    assert matches({"type": "mention", "channel": ["C0123456789", "C0000000001"]}, ev)
    assert not matches({"type": "mention", "channel": ["C0000000001"]}, ev)


def test_any_is_a_wildcard():
    ev = _event(channel="C0123456789")
    assert matches({"type": "mention", "channel": "any"}, ev)
    assert matches({"type": "mention", "channel": ["any"]}, ev)


def test_filter_key_missing_from_event_does_not_match():
    ev = _event(author="alex")
    assert not matches({"type": "mention", "channel": "C0123456789"}, ev)


def test_exclude_prefix_inverts_equality():
    """exclude_author: X → events authored by X never match."""
    by_alex = _event(author="alex")
    by_priya = _event(author="priya")
    match = {"type": "mention", "exclude_author": "alex"}
    assert not matches(match, by_alex)
    assert matches(match, by_priya)


def test_exclude_with_list_value():
    by_sam = _event(author="sam")
    match = {"type": "mention", "exclude_author": ["alex", "priya"]}
    assert matches(match, by_sam)
    assert not matches(match, _event(author="priya"))


def test_exclude_self_flag_reads_is_self_field():
    """Sources set is_self=True on events authored by the Scout user."""
    own = _event(is_self=True)
    other = _event(is_self=False)
    assert not matches({"type": "mention", "exclude_self": True}, own)
    assert matches({"type": "mention", "exclude_self": True}, other)
    # exclude_self: false is a no-op.
    assert matches({"type": "mention", "exclude_self": False}, own)


def test_extra_event_fields_are_ignored():
    ev = _event(author="alex", channel="C0123456789", text="ping <@U0123456789>")
    assert matches({"type": "mention"}, ev)


def test_boolean_and_numeric_filters():
    ev = _event(is_self=False, reply_count=3)
    assert matches({"type": "mention", "reply_count": 3}, ev)
    assert not matches({"type": "mention", "reply_count": 4}, ev)
