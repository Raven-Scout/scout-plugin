"""Pure diff over ActionItem snapshots.

Returns a list of ChangeEvent records describing what changed between
two parses of the same action-items file. Designed to be the v0.5
event-store subscriber's projection target as well — same shape,
different source.

Match priority:
1. By short_prefix (the [#XXXX] surface form from §13.1) — only matches
   when *both* prev and curr items carry the same prefix.
2. By (section, title) tuple — fallback for legacy unprefixed lines.

This means a line that gains a prefix in curr is not matched to its
unprefixed prev — it appears as a `removed` (the unprefixed version
disappeared) plus an `added` (the prefixed version appeared). That's
intentional: the watcher's `prev_state` will be re-seeded on the next
diff cycle, and the v0.5 event store will see the assignment as a
distinct event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scout.action_items.parser import ActionItem


@dataclass(frozen=True)
class ChangeEvent:
    kind: str  # "added" | "removed" | "completed" | "reopened" | "title_changed"
    item_id: str  # short_prefix, or "" for unprefixed lines
    title: str  # display title (curr title for title_changed)
    section: str  # display section
    extras: dict[str, Any] = field(default_factory=dict)


def _index_by_prefix(items: list[ActionItem]) -> dict[str, ActionItem]:
    return {i.short_prefix: i for i in items if i.short_prefix}


def _index_by_section_title(items: list[ActionItem]) -> dict[tuple[str, str], list[ActionItem]]:
    # Skip items that have a prefix — those match by prefix path only.
    # Maps each (section, title) key to a list so that N duplicates on each
    # side can be paired 1:1 (consume one from the list per curr match).
    index: dict[tuple[str, str], list[ActionItem]] = {}
    for i in items:
        if not i.short_prefix:
            key = (i.section, i.title)
            index.setdefault(key, []).append(i)
    return index


def diff(*, prev: list[ActionItem], curr: list[ActionItem]) -> list[ChangeEvent]:
    """Return the list of changes from `prev` to `curr`."""
    events: list[ChangeEvent] = []

    prev_by_prefix = _index_by_prefix(prev)
    # Each key maps to a list of prev items; we pop (consume) one per curr match
    # so that N identical (section, title) items on each side pair up 1:1.
    prev_by_st = _index_by_section_title(prev)

    matched_prev_prefix: set[str] = set()

    # Walk curr in input order so the emitted events are in display order.
    for item in curr:
        if item.short_prefix:
            prev_match = prev_by_prefix.get(item.short_prefix)
            if prev_match is not None:
                matched_prev_prefix.add(item.short_prefix)
                events.extend(_compare(prev_match, item))
                continue
            # New prefix in curr that wasn't in prev → "added".
            events.append(
                ChangeEvent(
                    kind="added",
                    item_id=item.short_prefix,
                    title=item.title,
                    section=item.section,
                )
            )
            continue

        # Unprefixed line: match by (section, title), consuming one prev item.
        key = (item.section, item.title)
        bucket = prev_by_st.get(key)
        if bucket:
            prev_match = bucket.pop(0)  # consume earliest prev for this key
            events.extend(_compare(prev_match, item))
            continue

        events.append(
            ChangeEvent(
                kind="added",
                item_id="",
                title=item.title,
                section=item.section,
            )
        )

    # Anything in prev that wasn't matched is removed.
    for prefix, item in prev_by_prefix.items():
        if prefix not in matched_prev_prefix:
            events.append(
                ChangeEvent(
                    kind="removed",
                    item_id=prefix,
                    title=item.title,
                    section=item.section,
                )
            )
    # Any unconsumed prev items in the section-title index are "removed".
    for _key, remaining in prev_by_st.items():
        for item in remaining:
            events.append(
                ChangeEvent(
                    kind="removed",
                    item_id="",
                    title=item.title,
                    section=item.section,
                )
            )

    return events


def _compare(prev: ActionItem, curr: ActionItem) -> list[ChangeEvent]:
    """Compare two matched items; emit zero or more events."""
    out: list[ChangeEvent] = []
    item_id = curr.short_prefix or ""
    if prev.status != curr.status:
        kind = "completed" if curr.status == "done" else "reopened"
        out.append(
            ChangeEvent(
                kind=kind,
                item_id=item_id,
                title=curr.title,
                section=curr.section,
            )
        )
    if prev.title != curr.title:
        out.append(
            ChangeEvent(
                kind="title_changed",
                item_id=item_id,
                title=curr.title,
                section=curr.section,
                extras={"old_title": prev.title, "new_title": curr.title},
            )
        )
    return out
