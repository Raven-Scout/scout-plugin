"""Atomic write-back for action-items markdown files.

POSIX `os.replace` is atomic: readers see either the old complete
file or the new complete file, never a torn state. We write to a
sibling temp file in the same directory, fsync it, then rename.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from scout.errors import ActionItemError
from scout.ids import short_prefix_pattern

# Top-level checkbox line, with any leading whitespace. Captures the indent
# and the marker so the prefix can be inserted while preserving the indent.
# Case-insensitive `[x]` matches `[X]` too, mirroring the parser/backfill
# candidate regex.
_CHECKBOX_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<marker>- \[[ xX]\] )")


def atomic_write_lines(target: Path, lines: list[str]) -> None:
    """Replace `target`'s contents with `lines` (one per line, trailing newline)."""
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(parent))
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
            if lines:
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        # Cleanup on any failure (including the simulated OSError in tests).
        if tmp.exists():
            tmp.unlink()
        raise


def _read_lines(target: Path) -> list[str]:
    return target.read_text(encoding="utf-8").splitlines()


def flip_checkbox(target: Path, *, line_number: int, to_done: bool) -> None:
    """Toggle `[ ]` ⇄ `[x]` on the 1-indexed line. Preserves all other bytes."""
    lines = _read_lines(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(f"flip_checkbox: line {line_number} out of range (1..{len(lines)})")
    old = "[ ]" if to_done else "[x]"
    new = "[x]" if to_done else "[ ]"
    if old not in lines[idx]:
        raise ActionItemError(f"flip_checkbox: line {line_number} does not contain `{old}`")
    lines[idx] = lines[idx].replace(old, new, 1)
    atomic_write_lines(target, lines)


def insert_below(target: Path, *, line_number: int, text: str) -> None:
    """Insert `text` as a new line directly below the 1-indexed line."""
    lines = _read_lines(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(f"insert_below: line {line_number} out of range (1..{len(lines)})")
    lines.insert(idx + 1, text)
    atomic_write_lines(target, lines)


def replace_line(target: Path, *, line_number: int, text: str) -> None:
    """Replace the 1-indexed line wholesale with `text`."""
    lines = _read_lines(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(f"replace_line: line {line_number} out of range (1..{len(lines)})")
    lines[idx] = text
    atomic_write_lines(target, lines)


def delete_line(target: Path, *, line_number: int) -> None:
    """Remove the 1-indexed line entirely."""
    lines = _read_lines(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(f"delete_line: line {line_number} out of range (1..{len(lines)})")
    del lines[idx]
    atomic_write_lines(target, lines)


def add_prefix_to_line(target: Path, *, line_number: int, prefix: str) -> None:
    """Insert `[#PREFIX] ` after the checkbox marker on the 1-indexed line.

    Preserves leading whitespace (the parser and backfill candidate regex
    both accept indented top-level items, so the writer must too).
    Accepts both `[x]` and `[X]` checkbox completion markers.

    Refuses if the line already carries a `[#XXXX]` prefix — the caller
    should not be asking to add one if scout.id_map already has a record.
    """
    lines = _read_lines(target)
    idx = line_number - 1
    if not 0 <= idx < len(lines):
        raise ActionItemError(f"add_prefix_to_line: line {line_number} out of range (1..{len(lines)})")
    line = lines[idx]
    if short_prefix_pattern().search(line):
        raise ActionItemError(f"add_prefix_to_line: line {line_number} already has prefix")
    m = _CHECKBOX_LINE_RE.match(line)
    if m is None:
        raise ActionItemError(f"add_prefix_to_line: line {line_number} doesn't start with a checkbox marker")
    head_end = m.end()
    lines[idx] = line[:head_end] + f"[#{prefix}] " + line[head_end:]
    atomic_write_lines(target, lines)
