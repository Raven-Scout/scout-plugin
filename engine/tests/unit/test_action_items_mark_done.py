"""Unit tests for scout.action_items.mark_done."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from scout.action_items.mark_done import mark_done
from scout.errors import ActionItemError
from scout.events import Event
from scout.id_map import IdMap, IdMapEntry

# -----------------------------------------------------------------------
# Plan 2 legacy contract — adapted to the new by_subject= kwarg.
# `mark_done(path, subject=..., undo=...)` no longer exists. The new API
# resolves the daily file via `data_dir` + `_today()`, so each test seeds
# a daily file at the expected path and pins `_today()` via monkeypatch.
# The Plan 2 `undo` flag is dropped (no caller exercises it post-Task 18).
# -----------------------------------------------------------------------


def _seed_daily(data_dir: Path, body: str, *, date: dt.date) -> Path:
    items_dir = data_dir / "action-items"
    items_dir.mkdir(parents=True, exist_ok=True)
    f = items_dir / f"action-items-{date.isoformat()}.md"
    f.write_text(body)
    return f


def test_marks_open_task_done_by_subject(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    f = _seed_daily(
        fake_data_dir,
        "- [ ] Submit Lever feedback\n- [ ] Other task\n",
        date=today,
    )
    mark_done(by_subject="Lever feedback", data_dir=fake_data_dir)
    assert "- [x] Submit Lever feedback" in f.read_text()
    assert "- [ ] Other task" in f.read_text()  # unchanged


def test_no_match_raises(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    _seed_daily(fake_data_dir, "- [ ] Existing task\n", date=today)
    with pytest.raises(ActionItemError, match="no open task matched"):
        mark_done(by_subject="missing keyword", data_dir=fake_data_dir)


def test_ambiguous_match_raises_listing_candidates(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    _seed_daily(
        fake_data_dir,
        "- [ ] Lever feedback A\n- [ ] Lever feedback B\n",
        date=today,
    )
    with pytest.raises(ActionItemError, match="ambiguous|multiple") as exc:
        mark_done(by_subject="lever feedback", data_dir=fake_data_dir)
    msg = str(exc.value)
    assert "Lever feedback A" in msg
    assert "Lever feedback B" in msg


def test_resolves_today_when_data_dir_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `data_dir` argument resolves via SCOUT_DATA_DIR env var."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    f = _seed_daily(tmp_path, "- [ ] task X\n", date=today)
    mark_done(by_subject="task X")
    assert "- [x] task X" in f.read_text()


# -----------------------------------------------------------------------
# Task 18 new contract — by_id + by_subject + Event return.
# -----------------------------------------------------------------------


def test_mark_done_by_id_flips_correct_line(fake_data_dir, monkeypatch):
    # Set up: register prefix↔ULID in the id-map, write a markdown file with that prefix.
    m = IdMap.load(fake_data_dir)
    m.register(
        IdMapEntry(
            "01HXAAA0000000000000000000",
            "A3F7",
            "Submit Lever feedback",
            "action-items-2026-04-26.md",
            5,
        )
    )
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "# Action Items — 2026-04-26\n\n"
        "## In Progress\n\n"
        "- [ ] [#A3F7] 🔴 Submit Lever feedback\n"
        "- [ ] 🟡 Other unrelated task\n"
    )
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.mark_done import mark_done

    event = mark_done(by_id="A3F7", data_dir=fake_data_dir)

    assert "- [x] [#A3F7]" in daily.read_text()
    assert "- [ ] 🟡 Other" in daily.read_text()  # unrelated line untouched
    assert isinstance(event, Event)
    assert event.kind == "action_item.completed"
    assert event.source == "cli:mark_done"
    assert event.payload["item_id"] == "01HXAAA0000000000000000000"
    assert event.payload["via"] == "id"


def test_mark_done_by_subject_fallback_for_unprefixed_line(fake_data_dir, monkeypatch):
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("## In Progress\n\n- [ ] 🔴 Followup with vendor on contract\n")
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.mark_done import mark_done

    event = mark_done(by_subject="vendor", data_dir=fake_data_dir)
    assert "- [x] 🔴 Followup with vendor" in daily.read_text()
    assert event.payload["via"] == "subject"
    # No prefix on the line means no entity ULID — payload uses the event's own ULID derivation.
    assert "item_id" in event.payload  # may be None or empty; assert key present


def test_mark_done_by_id_unknown_prefix_raises(fake_data_dir, monkeypatch):
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: dt.date(2026, 4, 26))
    from scout.action_items.mark_done import mark_done
    from scout.errors import ActionItemError

    with pytest.raises(ActionItemError, match="prefix.*not found"):
        mark_done(by_id="ZZZZ", data_dir=fake_data_dir)


def test_mark_done_event_id_and_ts_well_formed(fake_data_dir, monkeypatch):
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX", "A3F7", "task", "action-items-2026-04-26.md", 1))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] [#A3F7] task\n")
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: dt.date(2026, 4, 26))

    from scout.action_items.mark_done import mark_done

    event = mark_done(by_id="A3F7", data_dir=fake_data_dir)
    assert len(event.id) == 26
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event.ts)


# Regression: mark_done must mutate the line indicated by the parser's
# item.line_number, not re-scan the file for the first matching raw_line.
# The old behavior performed a second read and matched first-occurrence,
# which under any TOCTOU race could target an unrelated line. Issue #32.


def test_mark_done_uses_parser_line_number_even_under_external_edit(fake_data_dir, monkeypatch):
    """The writer must use the parser's item.line_number — not a rescan
    for the first matching raw_line. If the file is externally edited
    between parse and write, the writer should fail loudly (line_number
    no longer valid) rather than silently flipping a different line
    that happens to match by content (the old find_line_number behavior)."""
    today = dt.date(2026, 4, 26)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)

    # Initial file: target task is at line 3.
    daily = fake_data_dir / "action-items" / f"action-items-{today.isoformat()}.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(
        "## Section\n"
        "\n"
        "- [ ] target task\n"  # line 3
        "- [ ] other task\n"
    )

    # Hook parse_file: after it returns the items (target at line 3), simulate
    # an external edit that prepends a duplicate raw_line at line 1 and pushes
    # the original content down. The old find_line_number-based code would
    # rescan and return line 1 → silently flip the wrong line.
    import scout.action_items.parser as parser_mod

    real_parse = parser_mod.parse_file

    def parse_then_external_edit(path):
        items = real_parse(path)
        path.write_text(
            "- [ ] target task\n"  # NEW duplicate at line 1
            "## Section\n"  # line 2
            "\n"  # line 3 — now empty (was the target)
            "- [ ] target task\n"  # line 4 — original target shifted down
            "- [ ] other task\n"  # line 5
        )
        return items

    monkeypatch.setattr("scout.action_items.mark_done.parse_file", parse_then_external_edit)

    # New contract: the writer uses line_number=3 (from the parser). After the
    # external edit, line 3 is empty — flip_checkbox raises. This is BETTER
    # than the old behavior, which would have silently flipped the duplicate
    # at line 1.
    with pytest.raises(ActionItemError):
        mark_done(by_subject="target", data_dir=fake_data_dir)

    # And critically: the injected duplicate at line 1 was NOT silently flipped.
    result = daily.read_text().splitlines()
    assert result[0] == "- [ ] target task", (
        f"injected duplicate at line 1 must NOT have been flipped; got: {result[0]!r}"
    )


def test_undo_reopens_done_task_by_subject(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    f = _seed_daily(
        fake_data_dir,
        "- [x] Submit Lever feedback\n- [ ] Other task\n",
        date=today,
    )
    event = mark_done(by_subject="Lever feedback", data_dir=fake_data_dir, undo=True)
    assert "- [ ] Submit Lever feedback" in f.read_text()
    assert event.kind == "action_item.reopened"


def test_undo_reopens_uppercase_done_task(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#56: a task completed as `[X]` must still be reopenable."""
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    f = _seed_daily(fake_data_dir, "- [X] Shipped thing\n", date=today)
    mark_done(by_subject="Shipped thing", data_dir=fake_data_dir, undo=True)
    assert "- [ ] Shipped thing" in f.read_text()


def test_undo_by_subject_does_not_match_open_tasks(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    _seed_daily(fake_data_dir, "- [ ] Still open task\n", date=today)
    with pytest.raises(ActionItemError, match="no done task matched"):
        mark_done(by_subject="Still open", data_dir=fake_data_dir, undo=True)


def test_undo_reopens_by_id(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#116 acceptance: `mark-done --by-id XXXX --undo` reopens a done task."""
    today = dt.date(2026, 4, 15)
    monkeypatch.setattr("scout.action_items.mark_done._today", lambda: today)
    f = _seed_daily(fake_data_dir, "- [x] [#AB30] Ship the fix\n", date=today)
    event = mark_done(by_id="AB30", data_dir=fake_data_dir, undo=True)
    assert "- [ ] [#AB30] Ship the fix" in f.read_text()
    assert event.kind == "action_item.reopened"
