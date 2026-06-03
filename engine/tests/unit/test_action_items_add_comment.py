"""Unit tests for scout.action_items.add_comment.

New contract (Plan 2 supplement, Task 20):
- add_comment(*, comment, by_id|by_subject, date=None, data_dir=None) -> Event
- Inserts a ``  - <comment>`` sub-bullet beneath the matched task line.
- Returns Event(kind="action_item.commented", source="cli:add_comment") with
  payload {item_id, via, title, comment}.
- Resolution by_id/by_subject is delegated to scout.action_items._common.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from scout.action_items.add_comment import add_comment
from scout.errors import ActionItemError
from scout.events import Event
from scout.id_map import IdMap, IdMapEntry


def test_add_comment_by_id_inserts_subbullet(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(
        IdMapEntry(
            "01HXAAA",
            "A3F7",
            "Submit Lever feedback",
            "action-items-2026-04-26.md",
            5,
        )
    )
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("## In Progress\n\n- [ ] [#A3F7] 🔴 Submit Lever feedback\n- [ ] 🟡 Other unrelated task\n")
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    event = add_comment(by_id="A3F7", comment="Hiring manager confirmed", data_dir=fake_data_dir)

    text = daily.read_text()
    # Default author is "scout"; the `<author>:` prefix is what binds the
    # line to the task as a comment (scout-plugin#100).
    assert "- [ ] [#A3F7] 🔴 Submit Lever feedback\n  - scout: Hiring manager confirmed" in text
    assert "Other unrelated task" in text  # untouched
    assert isinstance(event, Event)
    assert event.kind == "action_item.commented"
    assert event.source == "cli:add_comment"
    assert event.payload["item_id"] == "01HXAAA"
    assert event.payload["via"] == "id"
    assert event.payload["author"] == "scout"
    assert event.payload["comment"] == "Hiring manager confirmed"


def test_add_comment_custom_author_prefix(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A GUI-supplied author is written as the `- <author>: <text>` prefix."""
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("## To Do\n\n- [ ] 🔴 Followup with vendor on contract\n")
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    event = add_comment(by_subject="vendor", comment="test", author="Vaclav Nosek", data_dir=fake_data_dir)
    assert "  - Vaclav Nosek: test" in daily.read_text()
    assert event.payload["author"] == "Vaclav Nosek"


def test_add_comment_by_subject_fallback(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("## To Do\n\n- [ ] 🔴 Followup with vendor on contract\n")
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    event = add_comment(by_subject="vendor", comment="Email sent 4/26", data_dir=fake_data_dir)
    assert "  - scout: Email sent 4/26" in daily.read_text()
    assert event.payload["via"] == "subject"
    assert event.payload["comment"] == "Email sent 4/26"


def test_add_comment_by_id_unknown_prefix_raises(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] x\n")
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="prefix.*not found"):
        add_comment(by_id="ZZZZ", comment="x", data_dir=fake_data_dir)


def test_add_comment_event_id_and_ts_well_formed(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX", "A3F7", "task", "action-items-2026-04-26.md", 1))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] [#A3F7] task\n")
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    event = add_comment(by_id="A3F7", comment="x", data_dir=fake_data_dir)
    assert len(event.id) == 26
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event.ts)
