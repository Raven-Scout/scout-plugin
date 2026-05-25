"""Unit tests for scout.action_items.snooze.

New contract (Plan 2 supplement, Task 19):
- snooze(*, until, by_id|by_subject, date=None, data_dir=None) -> Event
- Inserts a ``  - snoozed-until: YYYY-MM-DD`` sub-bullet beneath the
  matched task line. The task itself stays in place.
- Returns Event(kind="action_item.snoozed", source="cli:snooze") with
  payload {item_id, via, title, until}.
- Resolution by_id/by_subject is delegated to scout.action_items._common.

The earlier "move to future-dated daily file with carry-in annotation"
contract was dropped in this rewrite (see task spec).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from scout.action_items.snooze import snooze
from scout.errors import ActionItemError
from scout.events import Event
from scout.id_map import IdMap, IdMapEntry


def test_snooze_by_id_inserts_until_subbullet(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    event = snooze(by_id="A3F7", until=dt.date(2026, 5, 1), data_dir=fake_data_dir)

    text = daily.read_text()
    assert "- [ ] [#A3F7] 🔴 Submit Lever feedback\n  - snoozed-until: 2026-05-01" in text
    assert "Other unrelated task" in text  # untouched
    assert isinstance(event, Event)
    assert event.kind == "action_item.snoozed"
    assert event.source == "cli:snooze"
    assert event.payload["item_id"] == "01HXAAA"
    assert event.payload["via"] == "id"
    assert event.payload["until"] == "2026-05-01"


def test_snooze_by_subject_fallback(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("## To Do\n\n- [ ] 🔴 Followup with vendor on contract\n")
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    event = snooze(by_subject="vendor", until=dt.date(2026, 5, 1), data_dir=fake_data_dir)
    assert "- snoozed-until: 2026-05-01" in daily.read_text()
    assert event.payload["via"] == "subject"
    assert event.payload["until"] == "2026-05-01"


def test_snooze_by_id_unknown_prefix_raises(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] x\n")
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="prefix.*not found"):
        snooze(by_id="ZZZZ", until=dt.date(2026, 5, 1), data_dir=fake_data_dir)


def test_snooze_event_id_and_ts_well_formed(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HX", "A3F7", "task", "action-items-2026-04-26.md", 1))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] [#A3F7] task\n")
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    event = snooze(by_id="A3F7", until=dt.date(2026, 5, 1), data_dir=fake_data_dir)
    assert len(event.id) == 26
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event.ts)


def test_snooze_records_from_kind_in_marker(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Optional `from_kind` is embedded in the snooze marker as a `(from-kind: …)`
    suffix so a later carry-forward can restore the source section's priority on
    the target day."""
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "task", "action-items-2026-04-26.md", 1))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("## 🔴 Urgent\n\n- [ ] [#A3F7] 🔴 task\n")
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    event = snooze(
        by_id="A3F7",
        until=dt.date(2026, 5, 1),
        from_kind="urgent",
        data_dir=fake_data_dir,
    )

    assert "- snoozed-until: 2026-05-01 (from-kind: urgent)" in daily.read_text()
    assert event.payload["from_kind"] == "urgent"


def test_snooze_omits_from_kind_when_not_provided(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing callers that don't pass from_kind keep the original marker shape."""
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", "A3F7", "task", "action-items-2026-04-26.md", 1))
    m.save()
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("- [ ] [#A3F7] task\n")
    monkeypatch.setattr("scout.action_items.snooze._today", lambda: dt.date(2026, 4, 26))

    event = snooze(by_id="A3F7", until=dt.date(2026, 5, 1), data_dir=fake_data_dir)

    text = daily.read_text()
    assert "- snoozed-until: 2026-05-01\n" in text
    assert "from-kind" not in text
    assert "from_kind" not in event.payload


def test_snooze_rejects_non_date_until(fake_data_dir: Path) -> None:
    """A string accidentally passed for `until` must fail loudly at the boundary,
    not silently AttributeError mid-mutation."""
    from scout.action_items.snooze import snooze

    with pytest.raises(ActionItemError, match="until must be a date"):
        snooze(by_subject="x", until="2026-05-01", data_dir=fake_data_dir)  # type: ignore[arg-type]
