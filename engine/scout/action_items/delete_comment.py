"""Delete a comment sub-bullet beneath an action item.

The inverse of `add_comment` — removes a single `  - <author>: <text>`
line attached to the matched task. Git history is the archive.

Selection rules:
- The task is resolved by `by_id` (preferred) or `by_subject` (legacy
  fallback), same contract as the other mutators.
- The comment within that task is selected by 1-based `index` (counts
  user-authored sub-bullets, skipping the `snoozed-until` marker) or by
  case-insensitive substring `text`. Exactly one must be provided.

Returns an `Event` describing the mutation.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from scout import paths
from scout.action_items._common import (
    list_comment_lines,
    resolve_target,
    select_comment,
)
from scout.action_items.parser import parse_file
from scout.action_items.writer import delete_line
from scout.events import Event, now_iso
from scout.ids import new_ulid


def _today() -> dt.date:
    return dt.date.today()


def delete_comment(
    *,
    by_id: str | None = None,
    by_subject: str | None = None,
    index: int | None = None,
    text: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    target_path = paths.action_items_daily_path(data=data_dir, date=date or _today())

    items = parse_file(target_path) if target_path.exists() else []
    match, item_ulid, via = resolve_target(
        items=items,
        data_dir=data_dir if data_dir is not None else paths.data_dir(),
        by_id=by_id,
        by_subject=by_subject,
    )

    task_line = match.line_number
    candidates = list_comment_lines(target_path, task_line_number=task_line)
    line_no, author, body = select_comment(candidates=candidates, index=index, text=text)
    delete_line(target_path, line_number=line_no)

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.comment_deleted",
        source="cli:delete_comment",
        payload={
            "item_id": item_ulid,
            "via": via,
            "title": match.title,
            "comment_author": author,
            "comment_text": body,
        },
    )
