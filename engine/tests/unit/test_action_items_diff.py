"""Unit tests for scout.action_items.diff."""

from __future__ import annotations

from scout.action_items.diff import diff
from scout.action_items.parser import ActionItem


def _item(
    *,
    title: str,
    short_prefix: str | None = None,
    status: str = "open",
    section: str = "In Progress",
    priority: str = "",
) -> ActionItem:
    checkbox = "x" if status == "done" else " "
    prefix_token = f"[#{short_prefix}] " if short_prefix else ""
    return ActionItem(
        priority=priority,
        title=title,
        status=status,
        section=section,
        context_links=[],
        notes=[],
        details=[],
        raw_line=f"- [{checkbox}] {prefix_token}{title}",
        short_prefix=short_prefix,
    )


def test_no_changes_yields_empty_list() -> None:
    items = [_item(title="task A", short_prefix="A3F7")]
    assert diff(prev=items, curr=items) == []


def test_added_item_emits_added_event() -> None:
    prev: list[ActionItem] = []
    curr = [_item(title="task A", short_prefix="A3F7")]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "added"
    assert events[0].item_id == "A3F7"
    assert events[0].title == "task A"


def test_removed_item_emits_removed_event() -> None:
    prev = [_item(title="task A", short_prefix="A3F7")]
    curr: list[ActionItem] = []
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "removed"
    assert events[0].item_id == "A3F7"


def test_status_open_to_done_emits_completed() -> None:
    prev = [_item(title="task A", short_prefix="A3F7", status="open")]
    curr = [_item(title="task A", short_prefix="A3F7", status="done")]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "completed"
    assert events[0].item_id == "A3F7"


def test_status_done_to_open_emits_reopened() -> None:
    prev = [_item(title="task A", short_prefix="A3F7", status="done")]
    curr = [_item(title="task A", short_prefix="A3F7", status="open")]
    events = diff(prev=prev, curr=curr)
    assert events[0].kind == "reopened"


def test_title_changed_emits_title_changed_event() -> None:
    prev = [_item(title="old title", short_prefix="A3F7")]
    curr = [_item(title="new title", short_prefix="A3F7")]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "title_changed"
    assert events[0].item_id == "A3F7"
    assert events[0].extras == {"old_title": "old title", "new_title": "new title"}


def test_match_falls_back_to_section_and_title_for_unprefixed_lines() -> None:
    """Legacy lines without [#XXXX] match by (section, title) tuple."""
    prev = [_item(title="legacy task", short_prefix=None, status="open")]
    curr = [_item(title="legacy task", short_prefix=None, status="done")]
    events = diff(prev=prev, curr=curr)
    assert events[0].kind == "completed"
    assert events[0].item_id == ""  # no prefix → empty id


def test_unprefixed_line_in_different_sections_treated_as_separate() -> None:
    prev = [_item(title="dup", short_prefix=None, section="In Progress")]
    curr = [_item(title="dup", short_prefix=None, section="To Do")]
    # Same title, different sections → first removed, second added.
    events = diff(prev=prev, curr=curr)
    kinds = {e.kind for e in events}
    assert kinds == {"removed", "added"}


def test_multiple_changes_emitted_in_input_order() -> None:
    prev = [
        _item(title="A", short_prefix="AAAA", status="open"),
        _item(title="B", short_prefix="BBBB", status="open"),
    ]
    curr = [
        _item(title="A", short_prefix="AAAA", status="done"),  # completed
        _item(title="B", short_prefix="BBBB", status="open"),  # unchanged
        _item(title="C", short_prefix="CCCC", status="open"),  # added
    ]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 2
    assert events[0].kind == "completed" and events[0].item_id == "AAAA"
    assert events[1].kind == "added" and events[1].item_id == "CCCC"


def test_prefix_match_wins_over_title_match() -> None:
    """If prefixes match but titles differ, that's a title_changed — not remove+add."""
    prev = [_item(title="original", short_prefix="A3F7")]
    curr = [_item(title="renamed", short_prefix="A3F7")]
    events = diff(prev=prev, curr=curr)
    assert len(events) == 1
    assert events[0].kind == "title_changed"


def test_duplicate_section_title_items_matched_pairwise() -> None:
    """#58: two items with identical (section, title) on both prev and curr are
    matched 1:1 (not collapsed/dropped). The diff engine must pair them up and
    emit the right events rather than silently discarding duplicates."""
    # Two items with the same (section, title) but different statuses in prev
    prev = [
        _item(title="dup task", section="In Progress", status="open"),
        _item(title="dup task", section="In Progress", status="in_progress"),
    ]
    curr = [
        _item(title="dup task", section="In Progress", status="done"),
        _item(title="dup task", section="In Progress", status="done"),
    ]
    events = diff(prev=prev, curr=curr)
    # Both items must be matched (no item silently dropped).
    # With 2 prev and 2 curr, we expect 2 "completed" events (open→done,
    # in_progress→done). The old last-wins dict collapses to 1 match.
    completed = [e for e in events if e.kind == "completed"]
    assert len(completed) == 2, f"Expected 2 'completed' events (one per paired dup); got {events}"


def test_duplicate_section_title_extra_curr_becomes_added() -> None:
    """#58: if curr has more duplicates than prev, extras are emitted as 'added'."""
    prev = [
        _item(title="dup task", section="In Progress", status="open"),
    ]
    curr = [
        _item(title="dup task", section="In Progress", status="open"),
        _item(title="dup task", section="In Progress", status="open"),
    ]
    events = diff(prev=prev, curr=curr)
    added = [e for e in events if e.kind == "added"]
    # The second curr item has no prev partner → "added"
    assert len(added) == 1


def test_change_event_has_section_for_display() -> None:
    """Renderers need the section for context — verify it survives diffing."""
    prev: list[ActionItem] = []
    curr = [_item(title="task", short_prefix="A3F7", section="To Do")]
    events = diff(prev=prev, curr=curr)
    assert events[0].section == "To Do"
