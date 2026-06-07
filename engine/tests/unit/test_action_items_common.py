"""Unit tests for scout.action_items._common — shared mutator helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items._common import resolve_target
from scout.action_items.parser import ActionItem
from scout.errors import ActionItemError
from scout.id_map import IdMap, IdMapEntry


def test_resolve_target_by_id_returns_entry_and_match(fake_data_dir: Path) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "task X", "today.md", 5))
    m.save()
    items = [
        ActionItem(
            priority="🔴",
            title="task X",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#A3F7] 🔴 task X",
            short_prefix="A3F7",
        ),
        ActionItem(
            priority="",
            title="other",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] other",
            short_prefix=None,
        ),
    ]
    target, ulid, via = resolve_target(items=items, data_dir=fake_data_dir, by_id="A3F7", by_subject=None)
    assert target.title == "task X"
    assert ulid == "01HXAAA"
    assert via == "id"


def test_resolve_target_by_subject_substring(fake_data_dir: Path) -> None:
    items = [
        ActionItem(
            priority="🔴",
            title="Reply to vendor on contract",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] 🔴 Reply to vendor on contract",
            short_prefix=None,
        ),
    ]
    target, ulid, via = resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="vendor")
    assert target.title == "Reply to vendor on contract"
    assert ulid == ""
    assert via == "subject"


def test_resolve_target_rejects_both_args_unset(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="exactly one"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id=None, by_subject=None)


def test_resolve_target_rejects_both_args_set(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="exactly one"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id="A3F7", by_subject="x")


def test_resolve_target_unknown_id_raises(fake_data_dir: Path) -> None:
    with pytest.raises(ActionItemError, match="prefix.*not found"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id="ZZZZ", by_subject=None)


def test_resolve_target_auto_registers_prefix_in_file_but_not_idmap(
    fake_data_dir: Path,
) -> None:
    """The briefing/consolidation skill writes fresh `[#XXXX]` lines directly
    into the markdown without registering them in the id-map. When a mutator
    sees the prefix on a parsed item but the id-map is empty, it should
    register on-the-fly so `mark-done --by-id` keeps working without a manual
    `backfill-prefixes` pass."""
    items = [
        ActionItem(
            priority="🔥",
            title="Rotate Kai Pricing GitHub token",
            status="open",
            section="Urgent",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#KPTK] 🔥 Rotate Kai Pricing GitHub token",
            line_number=12,
            short_prefix="KPTK",
        ),
    ]
    target, ulid, via = resolve_target(items=items, data_dir=fake_data_dir, by_id="KPTK", by_subject=None)
    assert target.short_prefix == "KPTK"
    assert via == "id"
    assert ulid  # a new ULID was minted

    # Persisted: a follow-up call sees the registered entry rather than
    # auto-registering again.
    m2 = IdMap.load(fake_data_dir)
    e2 = m2.lookup_by_prefix("KPTK")
    assert e2 is not None
    assert e2.ulid == ulid
    assert e2.last_title == "Rotate Kai Pricing GitHub token"


def test_resolve_target_ambiguous_subject_raises(fake_data_dir: Path) -> None:
    items = [
        ActionItem(
            priority="",
            title="Reply to alice",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] Reply to alice",
            short_prefix=None,
        ),
        ActionItem(
            priority="",
            title="Reply to bob",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] Reply to bob",
            short_prefix=None,
        ),
    ]
    with pytest.raises(ActionItemError, match="ambiguous"):
        resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="reply")


def test_resolve_target_prefix_in_idmap_but_missing_from_items_raises(
    fake_data_dir: Path,
) -> None:
    """If the IdMap knows a prefix but the parsed items list doesn't include it
    (e.g., user passed `--by-id A3F7` while looking at the wrong day's file),
    raise a clear error rather than silently no-op."""
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "task X", "today.md", 5))
    m.save()
    # Items list is empty — simulates the wrong-file case.
    with pytest.raises(ActionItemError, match="is in id-map but not present"):
        resolve_target(items=[], data_dir=fake_data_dir, by_id="A3F7", by_subject=None)


# Regression: by_subject must match against item.title (cleaned), not
# raw_line (which includes the [#XXXX] prefix marker and the priority
# emoji). Otherwise a search for "A3F7" silently matches the prefix
# token of an unrelated task. Issue #32.


def test_resolve_target_by_subject_does_not_match_prefix_token(fake_data_dir: Path) -> None:
    """A subject substring that only appears inside the [#XXXX] prefix
    marker (and not in the cleaned title) must NOT match — users searching
    for a prefix should use --by-id, not --by-subject."""
    items = [
        ActionItem(
            priority="🔴",
            title="task X",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#A3F7] 🔴 task X",
            line_number=5,
            short_prefix="A3F7",
        ),
    ]
    with pytest.raises(ActionItemError, match="no open task matched"):
        resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="A3F7")


def test_resolve_target_by_subject_matches_title_substring(fake_data_dir: Path) -> None:
    """Sanity: a substring that appears in the cleaned title still matches."""
    items = [
        ActionItem(
            priority="🔴",
            title="task X",
            status="open",
            section="In Progress",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#A3F7] 🔴 task X",
            line_number=5,
            short_prefix="A3F7",
        ),
    ]
    target, _, via = resolve_target(items=items, data_dir=fake_data_dir, by_id=None, by_subject="task x")
    assert target.title == "task X"
    assert via == "subject"


def test_resolve_target_ambiguous_id_raises(fake_data_dir: Path) -> None:
    """Two open tasks sharing a [#TAG] is ambiguous for --by-id; raise rather
    than silently acting on the first (reusable human tags can collide)."""
    items = [
        ActionItem(
            priority="🔴",
            title="Miro 1:1 follow-through",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#MIRO] Miro 1:1 follow-through",
            line_number=5,
            short_prefix="MIRO",
        ),
        ActionItem(
            priority="🟡",
            title="Miro design doc review",
            status="open",
            section="To Do",
            context_links=[],
            notes=[],
            details=[],
            raw_line="- [ ] [#MIRO] Miro design doc review",
            line_number=9,
            short_prefix="MIRO",
        ),
    ]
    with pytest.raises(ActionItemError, match="ambiguous id"):
        resolve_target(items=items, data_dir=fake_data_dir, by_id="MIRO", by_subject=None)
