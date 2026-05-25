"""Shared helpers for action-item mutators.

Factored out of mark_done/snooze/add_comment so each mutator's public
function is a thin wrapper around resolution + the actual mutation +
Event construction.
"""

from __future__ import annotations

import re
from pathlib import Path

from scout.action_items.parser import ActionItem
from scout.errors import ActionItemError
from scout.id_map import IdMap

# Matches the comment shape that `add-comment` writes:
#   `  - <author>: <text>`
# Author allows letters/digits/`._-` to mirror the parser's tolerance.
# The snooze marker `  - snoozed-until: YYYY-MM-DD` also fits this shape; we
# strip those when listing comments since they are not user-authored notes.
_COMMENT_SUB_BULLET_RE = re.compile(r"^(?P<indent>\s+)-\s+(?P<author>[A-Za-z][A-Za-z0-9._-]*)\s*:\s*(?P<text>.+?)\s*$")
_SNOOZE_MARKER_AUTHORS = {"snoozed-until"}


def list_comment_lines(path: Path, *, task_line_number: int) -> list[tuple[int, str, str]]:
    """Walk the indented sub-bullets directly under `task_line_number`.

    Returns a list of `(line_number, author, text)` tuples for each comment
    `  - <author>: <text>` sub-bullet attached to the task, in file order.
    The `snoozed-until` marker is filtered out — it's machine metadata, not
    a user-authored comment.

    Stops at the first non-indented line, empty line, or a new task line.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    idx = task_line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(f"list_comment_lines: task line {task_line_number} out of range (1..{len(lines)})")
    comments: list[tuple[int, str, str]] = []
    j = idx + 1
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            break
        # Stop when we drop back to a top-level (unindented) bullet.
        if not (line.startswith(" ") or line.startswith("\t")):
            break
        m = _COMMENT_SUB_BULLET_RE.match(line)
        if m is None:
            # Non-comment indented line (e.g. a sub-task or detail bullet).
            # Skip it but keep scanning; comments may appear after.
            j += 1
            continue
        author = m.group("author")
        if author.lower() in _SNOOZE_MARKER_AUTHORS:
            j += 1
            continue
        comments.append((j + 1, author, m.group("text")))
        j += 1
    return comments


def select_comment(
    *,
    candidates: list[tuple[int, str, str]],
    index: int | None,
    text: str | None,
) -> tuple[int, str, str]:
    """Pick a single comment from `candidates` by 1-based `index` or substring `text`.

    Exactly one of `index` / `text` must be provided. Substring matching is
    case-insensitive against the comment body and must resolve to exactly
    one row — ambiguous matches raise.
    """
    if (index is None) == (text is None):
        raise ActionItemError("comment selector requires exactly one of --index or --text")
    if not candidates:
        raise ActionItemError("no comments found on this task")
    if index is not None:
        if index < 1 or index > len(candidates):
            raise ActionItemError(f"--index {index} out of range; task has {len(candidates)} comment(s)")
        return candidates[index - 1]
    assert text is not None
    needle = text.lower()
    matches = [c for c in candidates if needle in c[2].lower()]
    if not matches:
        raise ActionItemError(f"no comment matched text: {text!r}")
    if len(matches) > 1:
        raise ActionItemError(
            f"ambiguous comment text {text!r}; matched {len(matches)}:\n"
            + "\n".join(f"  {i + 1}. {c[2]}" for i, c in enumerate(matches))
        )
    return matches[0]


def resolve_target(
    *,
    items: list[ActionItem],
    data_dir: Path,
    by_id: str | None,
    by_subject: str | None,
) -> tuple[ActionItem, str, str]:
    """Resolve which `ActionItem` a mutator should act on.

    Returns `(target, item_ulid, via)` where `via` is `"id"` or
    `"subject"`. `item_ulid` may be empty string if a `--by-subject` lookup
    matched a legacy unprefixed line and no IdMap entry exists for it.

    Raises `ActionItemError` on bad arguments, unknown prefix, no match,
    or ambiguous match.
    """
    if (by_id is None) == (by_subject is None):
        raise ActionItemError("resolve_target requires exactly one of by_id or by_subject")

    id_map = IdMap.load(data_dir)

    if by_id is not None:
        entry = id_map.lookup_by_prefix(by_id)
        if entry is None:
            raise ActionItemError(
                f"prefix [#{by_id}] not found in id-map; if this is a legacy line, retry with --by-subject"
            )
        match = next((i for i in items if i.short_prefix == by_id), None)
        if match is None:
            raise ActionItemError(f"prefix [#{by_id}] is in id-map but not present in this file")
        return match, entry.ulid, "id"

    # by_subject path
    assert by_subject is not None  # enforced by the exactly-one-of check above
    # Match against the cleaned title, NOT the raw_line. raw_line includes
    # the `[#XXXX]` prefix marker and priority emoji, which would cause a
    # user search for e.g. "A3F7" to silently match the prefix token of an
    # unrelated task. Users wanting to find by prefix should use --by-id.
    needle = by_subject.lower()
    matches = [i for i in items if i.status == "open" and needle in i.title.lower()]
    if len(matches) == 0:
        raise ActionItemError(f"no open task matched subject: {by_subject!r}")
    if len(matches) > 1:
        raise ActionItemError(
            f"ambiguous subject {by_subject!r}; matched:\n" + "\n".join(f"  - {m.title}" for m in matches)
        )
    match = matches[0]
    item_ulid = ""
    if match.short_prefix:
        sub_entry = id_map.lookup_by_prefix(match.short_prefix)
        if sub_entry is not None:
            item_ulid = sub_entry.ulid
    return match, item_ulid, "subject"
