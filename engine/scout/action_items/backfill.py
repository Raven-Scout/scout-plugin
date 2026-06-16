"""Add `[#XXXX]` short prefixes to action-items lines that lack them.

Lets vaults that predate the prefix convention migrate without hand-editing.
Reads the file once, parses via `parse_lines` against the same bytes, picks
every open-status item with `short_prefix is None`, mints a fresh
non-colliding prefix per line, writes them bottom-up, and registers each
prefix in id-map.json immediately after its write succeeds.

Idempotent: re-running the command on a file that already has prefixes is a
no-op (returns an empty list).
"""

from __future__ import annotations

import re
from pathlib import Path

from scout.action_items.parser import parse_lines
from scout.action_items.writer import add_prefix_to_line
from scout.id_map import IdMap, IdMapEntry
from scout.ids import new_short_prefix, new_ulid

# `add_prefix_to_line` only operates on lines that actually carry a checkbox
# marker. The parser is more permissive — under sections like "Files Touched"
# it can surface plain `- **name** — body` bullets as `status == "open"`
# items, which then crash the writer mid-backfill. Pre-filter against the raw
# source line to skip anything that isn't a real task.
_CHECKBOX_RE = re.compile(r"^\s*- \[[ xX]\] ")


def backfill_prefixes(
    *,
    target: Path,
    data_dir: Path,
    dry_run: bool = False,
) -> list[tuple[int, str, str]]:
    """Mint and (optionally) write prefixes for unprefixed open items in
    `target`.

    Returns a list of `(line_number, new_prefix, title)` tuples for every
    line we'd touch. When `dry_run` is True, no file or id-map writes happen
    — the return value is the same so callers can show a preview.
    """
    if not target.exists():
        return []
    raw_lines = target.read_text(encoding="utf-8").splitlines()
    items = parse_lines(raw_lines)

    def _has_checkbox(line_number: int) -> bool:
        idx = line_number - 1
        if not 0 <= idx < len(raw_lines):
            return False
        return _CHECKBOX_RE.match(raw_lines[idx]) is not None

    candidates = [i for i in items if i.status == "open" and i.short_prefix is None and _has_checkbox(i.line_number)]
    if not candidates:
        return []

    id_map = IdMap.load(data_dir)
    in_use = id_map.in_use_prefixes()

    plan: list[tuple[int, str, str]] = []
    for item in candidates:
        prefix = new_short_prefix(exclude=in_use)
        in_use.add(prefix)
        plan.append((item.line_number, prefix, item.title))

    if dry_run:
        return plan

    # Apply line edits from the bottom up so earlier line numbers don't shift
    # under us. Register each prefix in the id-map immediately after its write
    # succeeds, and save() in a finally — so a mid-loop failure still leaves the
    # id-map consistent with whatever reached disk (#42). Without this, a
    # partial write desyncs the map and a retry re-mints live prefixes.
    try:
        for line_no, prefix, title in sorted(plan, key=lambda p: p[0], reverse=True):
            add_prefix_to_line(target, line_number=line_no, prefix=prefix)
            id_map.register(
                IdMapEntry(
                    ulid=new_ulid(),
                    short_prefix=prefix,
                    last_title=title,
                    last_file=target.name,
                    last_line=line_no,
                )
            )
    finally:
        id_map.save()
    return plan
