"""Replace the body of an existing comment beneath an action item.

Same task/comment resolution as `delete_comment`. Preserves the
`  - <author>: ` prefix and the original indent — only the body text
changes.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from scout import paths
from scout.action_items._common import (
    list_comment_lines,
    resolve_target,
    select_comment,
)
from scout.action_items.parser import parse_file
from scout.action_items.writer import replace_line
from scout.errors import ActionItemError
from scout.events import Event, now_iso
from scout.ids import new_ulid

_AUTHOR_PREFIX_RE = re.compile(
    r"^(?P<head>\s+-\s+[A-Za-z][A-Za-z0-9._-]*\s*:\s+).*$"
)


def _today() -> dt.date:
    return dt.date.today()


def edit_comment(
    *,
    new_text: str,
    by_id: str | None = None,
    by_subject: str | None = None,
    index: int | None = None,
    text: str | None = None,
    date: dt.date | None = None,
    data_dir: Path | None = None,
) -> Event:
    if not new_text.strip():
        raise ActionItemError("edit-comment: --new-text must not be empty")

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
    line_no, author, old_text = select_comment(candidates=candidates, index=index, text=text)

    # Pull the existing line so we keep its original indent + author prefix
    # byte-for-byte. Only the body changes.
    lines = target_path.read_text(encoding="utf-8").splitlines()
    original = lines[line_no - 1]
    m = _AUTHOR_PREFIX_RE.match(original)
    if m is None:
        raise ActionItemError(
            f"edit-comment: could not parse author prefix on line {line_no}: {original!r}"
        )
    replacement = m.group("head") + new_text
    replace_line(target_path, line_number=line_no, text=replacement)

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="action_item.comment_edited",
        source="cli:edit_comment",
        payload={
            "item_id": item_ulid,
            "via": via,
            "title": match.title,
            "comment_author": author,
            "old_text": old_text,
            "new_text": new_text,
        },
    )
