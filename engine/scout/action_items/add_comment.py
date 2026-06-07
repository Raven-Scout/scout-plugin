"""Append a comment beneath an action item.

Inserts a ``  - <author>: <comment>`` sub-bullet directly beneath the
matched task line. The task itself stays in place — the sub-bullet is
the comment. The ``<author>:`` prefix is what binds the line to the
comment-thread reader (``render.py`` and ``_common.py``); without it the
line is indistinguishable from a plain detail bullet and renders as a
detached section note. See scout-plugin#100.

``author`` defaults to ``"scout"`` (the CLI/automation writer); the GUI
passes the signed-in user's display name via ``--author`` so the comment
is attributed to a person (e.g. ``- Vaclav Nosek: ...``).

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
    author: str = "scout",
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    """Append `  - <author>: <comment>` beneath today's (or `date`'s) item.

    Exactly one of `by_id` or `by_subject` must be provided. `by_id` is
    a stable `[#TAG]` id (2-8 [A-Z0-9], >=1 letter); `by_subject` is a case-insensitive
    substring match against open-status raw lines (legacy fallback for
    lines that haven't been prefixed yet).

    `author` is the attribution prefix written before the comment text.
    It defaults to ``"scout"``; pass a person's display name for
    GUI-authored comments. The reader (`render.py`, `_common.py`) keys on
    this ``<author>:`` prefix to bind the line to the task as a comment.
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

    insert_below(target_path, line_number=match.line_number, text=f"  - {author}: {comment}")

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.commented",
        source="cli:add_comment",
        payload={
            "item_id": item_ulid,
            "via": via,
            "title": match.title,
            "author": author,
            "comment": comment,
        },
    )
