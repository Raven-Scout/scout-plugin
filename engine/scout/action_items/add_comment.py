"""Append a comment beneath an action item.

Inserts a ``  - <comment>`` sub-bullet directly beneath the matched task
line. The task itself stays in place — the sub-bullet is the comment.

Returns an `Event` describing the mutation. v0.4 mutators emit Events
but nothing persists them yet; v0.5 will add the SQLite event store.
See v0.4 spec §13.2.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from scout import paths
from scout.action_items._common import resolve_target
from scout.action_items.parser import parse_file
from scout.action_items.writer import insert_below
from scout.events import Event, now_iso
from scout.ids import new_ulid


def _today() -> dt.date:
    """Indirection so tests can monkeypatch the date without freezing time."""
    return dt.date.today()


def add_comment(
    *,
    comment: str,
    by_id: str | None = None,
    by_subject: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Append `  - <comment>` beneath today's (or `date`'s) action item.

    Exactly one of `by_id` or `by_subject` must be provided. `by_id` is
    a 4-char Crockford prefix; `by_subject` is a case-insensitive
    substring match against open-status raw lines (legacy fallback for
    lines that haven't been prefixed yet).
    """
    target_path = paths.action_items_daily_path(data=data_dir, date=date or _today())

    # Parse if file exists; otherwise pass empty items list and let
    # resolve_target produce the right error (unknown prefix for by_id,
    # no-match for by_subject). This preserves the by_id-unknown-prefix
    # contract: that error fires before any file existence check.
    items = parse_file(target_path) if target_path.exists() else []
    match, item_ulid, via = resolve_target(
        items=items,
        data_dir=data_dir if data_dir is not None else paths.data_dir(),
        by_id=by_id,
        by_subject=by_subject,
    )

    insert_below(target_path, line_number=match.line_number, text=f"  - {comment}")

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.commented",
        source="cli:add_comment",
        payload={
            "item_id": item_ulid,
            "via": via,
            "title": match.title,
            "comment": comment,
        },
    )
