"""Unit tests for scout.action_items.writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.writer import (
    atomic_write_lines,
    flip_checkbox,
    insert_below,
)


def test_atomic_write_replaces_file_contents(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("old\n")
    atomic_write_lines(target, ["new line 1", "new line 2"])
    assert target.read_text() == "new line 1\nnew line 2\n"


def test_atomic_write_uses_temp_then_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure between tmp write and replace must leave original intact."""
    target = tmp_path / "f.md"
    target.write_text("original\n")
    real_replace = __import__("os").replace

    def boom(_src: str, _dst: str) -> None:
        raise OSError("simulated rename failure")

    import os

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_lines(target, ["new"])
    assert target.read_text() == "original\n"  # untouched
    monkeypatch.setattr(os, "replace", real_replace)


def test_flip_checkbox_open_to_done(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] task A\n- [ ] task B\n")
    flip_checkbox(target, line_number=1, to_done=True)
    assert target.read_text() == "- [x] task A\n- [ ] task B\n"


def test_flip_checkbox_done_to_open(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [x] task A\n")
    flip_checkbox(target, line_number=1, to_done=False)
    assert target.read_text() == "- [ ] task A\n"


def test_insert_below_appends_after_target_line(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("line 1\nline 2\nline 3\n")
    insert_below(target, line_number=2, text="  - inserted note")
    assert target.read_text() == "line 1\nline 2\n  - inserted note\nline 3\n"


def test_flip_checkbox_out_of_range_raises(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] task\n")
    from scout.errors import ActionItemError

    with pytest.raises(ActionItemError, match="line"):
        flip_checkbox(target, line_number=99, to_done=True)


def test_add_prefix_to_unprefixed_line(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] 🔴 task title\n- [ ] [#X9Y2] other\n")
    from scout.action_items.writer import add_prefix_to_line

    add_prefix_to_line(target, line_number=1, prefix="A3F7")
    assert target.read_text() == "- [ ] [#A3F7] 🔴 task title\n- [ ] [#X9Y2] other\n"


def test_add_prefix_handles_no_priority_emoji(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] just a plain task\n")
    from scout.action_items.writer import add_prefix_to_line

    add_prefix_to_line(target, line_number=1, prefix="A3F7")
    assert target.read_text() == "- [ ] [#A3F7] just a plain task\n"


def test_add_prefix_refuses_if_line_already_prefixed(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] [#X9Y2] already prefixed\n")
    from scout.action_items.writer import add_prefix_to_line
    from scout.errors import ActionItemError

    with pytest.raises(ActionItemError, match="already has prefix"):
        add_prefix_to_line(target, line_number=1, prefix="A3F7")


def test_flip_checkbox_preserves_existing_prefix(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] [#A3F7] task\n")
    from scout.action_items.writer import flip_checkbox

    flip_checkbox(target, line_number=1, to_done=True)
    assert target.read_text() == "- [x] [#A3F7] task\n"


def test_add_prefix_refuses_when_line_number_out_of_range(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("- [ ] only line\n")
    from scout.action_items.writer import add_prefix_to_line
    from scout.errors import ActionItemError

    with pytest.raises(ActionItemError, match="out of range"):
        add_prefix_to_line(target, line_number=99, prefix="A3F7")


def test_add_prefix_refuses_when_line_is_not_a_checkbox(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text("plain text without checkbox\n")
    from scout.action_items.writer import add_prefix_to_line
    from scout.errors import ActionItemError

    with pytest.raises(ActionItemError, match="doesn't start with a checkbox marker"):
        add_prefix_to_line(target, line_number=1, prefix="A3F7")


def test_add_prefix_to_specific_line_in_multi_line_file(tmp_path: Path) -> None:
    """Pin 1-indexed semantics — line 3 in a multi-line file mutates only line 3."""
    target = tmp_path / "f.md"
    target.write_text("# Header\n\n- [ ] 🔴 first task\n- [ ] 🟡 second task\n- [ ] 🟢 third task\n")
    from scout.action_items.writer import add_prefix_to_line

    add_prefix_to_line(target, line_number=4, prefix="B5K2")

    assert target.read_text() == (
        "# Header\n\n- [ ] 🔴 first task\n- [ ] [#B5K2] 🟡 second task\n- [ ] 🟢 third task\n"
    )


# Regression: add_prefix_to_line must accept indented checkbox lines.
# The parser and backfill candidate regex both accept `^\s*- \[[ xX]\] `,
# but the writer used to require zero-indent. An indented item would crash
# the writer mid-backfill, leaving the file partially modified and the
# id-map out of sync with the file. Issue #31.


def test_add_prefix_to_indented_checkbox_preserves_indent(tmp_path: Path) -> None:
    """A single-space-indented checkbox is a valid top-level item per the
    parser. The writer must insert the prefix without losing the indent."""
    target = tmp_path / "f.md"
    target.write_text(" - [ ] indented one space\n")
    from scout.action_items.writer import add_prefix_to_line

    add_prefix_to_line(target, line_number=1, prefix="A3F7")

    assert target.read_text() == " - [ ] [#A3F7] indented one space\n"


def test_add_prefix_to_done_indented_checkbox_preserves_indent(tmp_path: Path) -> None:
    target = tmp_path / "f.md"
    target.write_text(" - [x] indented and done\n")
    from scout.action_items.writer import add_prefix_to_line

    add_prefix_to_line(target, line_number=1, prefix="B5K2")

    assert target.read_text() == " - [x] [#B5K2] indented and done\n"


def test_add_prefix_handles_uppercase_X(tmp_path: Path) -> None:
    """The backfill candidate regex accepts `[X]` (uppercase). The writer
    must too — otherwise a file with any externally-completed item using
    `[X]` crashes mid-backfill (id-map state torn). See Issue #31."""
    target = tmp_path / "f.md"
    target.write_text("- [X] done with uppercase X\n")
    from scout.action_items.writer import add_prefix_to_line

    add_prefix_to_line(target, line_number=1, prefix="C7M9")

    assert target.read_text() == "- [X] [#C7M9] done with uppercase X\n"


def test_backfill_prefixes_handles_indented_checkbox_end_to_end(
    fake_data_dir: Path,
) -> None:
    """End-to-end regression for Issue #31: a file that mixes an
    indented checkbox with a regular one must be backfilled without the
    writer crashing mid-loop and leaving the file partially modified."""
    target = fake_data_dir / "action-items" / "sample.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "# Day\n"
        "\n"
        " - [ ] indented task\n"  # indent=1 — top-level item per parser
        "- [ ] regular task\n"
    )

    from scout.action_items.backfill import backfill_prefixes

    plan = backfill_prefixes(target=target, data_dir=fake_data_dir, dry_run=False)

    assert len(plan) == 2, f"expected to backfill both items, got: {plan}"
    out = target.read_text()
    # Both lines must now carry a prefix, and the indent on line 3 must be preserved.
    assert " - [ ] [#" in out, f"indent of indented line not preserved: {out!r}"
    assert "\n- [ ] [#" in out, f"non-indented line not prefixed: {out!r}"


# Line-ending / trailing-newline preservation across the edit round-trip (#34).


def test_flip_checkbox_preserves_crlf(tmp_path):
    from scout.action_items.writer import flip_checkbox

    target = tmp_path / "ai.md"
    target.write_bytes(b"## To Do\r\n- [ ] task one\r\n- [ ] task two\r\n")
    flip_checkbox(target, line_number=2, to_done=True)
    data = target.read_bytes()
    assert data == b"## To Do\r\n- [x] task one\r\n- [ ] task two\r\n"
    # No lone LF introduced.
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_insert_below_preserves_absent_trailing_newline(tmp_path):
    from scout.action_items.writer import insert_below

    target = tmp_path / "ai.md"
    target.write_bytes(b"## To Do\n- [ ] task")  # no final newline
    insert_below(target, line_number=2, text="  - note")
    data = target.read_bytes()
    assert data == b"## To Do\n- [ ] task\n  - note"
    assert not data.endswith(b"\n")


def test_lf_file_round_trips_unchanged(tmp_path):
    from scout.action_items.writer import replace_line

    target = tmp_path / "ai.md"
    target.write_bytes(b"## To Do\n- [ ] a\n- [ ] b\n")
    replace_line(target, line_number=2, text="- [ ] a edited")
    assert target.read_bytes() == b"## To Do\n- [ ] a edited\n- [ ] b\n"


def test_flip_checkbox_reopens_uppercase_x(tmp_path: Path) -> None:
    """Reopen accepts either completion casing and writes `[ ]` back (#56)."""
    f = tmp_path / "f.md"
    f.write_text("- [X] Shipped thing\n")
    flip_checkbox(f, line_number=1, to_done=False)
    assert f.read_text() == "- [ ] Shipped thing\n"


def test_flip_checkbox_reopens_lowercase_x(tmp_path: Path) -> None:
    f = tmp_path / "f.md"
    f.write_text("- [x] Shipped thing\n")
    flip_checkbox(f, line_number=1, to_done=False)
    assert f.read_text() == "- [ ] Shipped thing\n"
