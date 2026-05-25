"""Unit tests for scout.action_items.delete_comment."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scout.action_items.delete_comment import delete_comment
from scout.errors import ActionItemError
from scout.events import Event
from scout.id_map import IdMap, IdMapEntry


def _make_daily(fake_data_dir: Path, body: str) -> Path:
    daily = fake_data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text(body)
    return daily


def _register_prefix(fake_data_dir: Path, prefix: str = "A3F7") -> None:
    m = IdMap.load(fake_data_dir)
    m.register(IdMapEntry("01HXAAA", prefix, "task", "action-items-2026-04-26.md", 5))
    m.save()


def test_delete_comment_by_index(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    daily = _make_daily(
        fake_data_dir,
        "## In Progress\n\n"
        "- [ ] [#A3F7] task\n"
        "  - jordan: first\n"
        "  - jordan: second\n"
        "  - jordan: third\n"
        "- [ ] another task\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda: dt.date(2026, 4, 26))

    event = delete_comment(by_id="A3F7", index=2, data_dir=fake_data_dir)

    text = daily.read_text()
    assert "first" in text
    assert "second" not in text
    assert "third" in text
    assert "another task" in text
    assert isinstance(event, Event)
    assert event.kind == "action_item.comment_deleted"
    assert event.source == "cli:delete_comment"
    assert event.payload["comment_text"] == "second"


def test_delete_comment_by_text_substring(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(
        fake_data_dir,
        "## In Progress\n\n- [ ] [#A3F7] task\n  - jordan: vendor confirmed\n  - jordan: legal review needed\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda: dt.date(2026, 4, 26))

    delete_comment(by_id="A3F7", text="legal", data_dir=fake_data_dir)

    text = (fake_data_dir / "action-items" / "action-items-2026-04-26.md").read_text()
    assert "vendor confirmed" in text
    assert "legal review" not in text


def test_delete_comment_ignores_snoozed_until_marker(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The snoozed-until sub-bullet shares the comment shape but is machine
    metadata. `--index 1` must point at the first real comment, not the marker."""
    _register_prefix(fake_data_dir)
    daily = _make_daily(
        fake_data_dir,
        "## In Progress\n\n- [ ] [#A3F7] task\n  - snoozed-until: 2026-05-01\n  - jordan: real comment\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda: dt.date(2026, 4, 26))

    delete_comment(by_id="A3F7", index=1, data_dir=fake_data_dir)

    text = daily.read_text()
    assert "snoozed-until: 2026-05-01" in text
    assert "real comment" not in text


def test_delete_comment_index_out_of_range(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(
        fake_data_dir,
        "- [ ] [#A3F7] task\n  - jordan: only one\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="--index 5 out of range"):
        delete_comment(by_id="A3F7", index=5, data_dir=fake_data_dir)


def test_delete_comment_ambiguous_text(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(
        fake_data_dir,
        "- [ ] [#A3F7] task\n  - jordan: vendor ping\n  - jordan: vendor confirmed\n",
    )
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="ambiguous"):
        delete_comment(by_id="A3F7", text="vendor", data_dir=fake_data_dir)


def test_delete_comment_requires_exactly_one_selector(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(fake_data_dir, "- [ ] [#A3F7] task\n  - jordan: hi\n")
    monkeypatch.setattr("scout.action_items.delete_comment._today", lambda: dt.date(2026, 4, 26))

    with pytest.raises(ActionItemError, match="exactly one of --index or --text"):
        delete_comment(by_id="A3F7", data_dir=fake_data_dir)

    with pytest.raises(ActionItemError, match="exactly one of --index or --text"):
        delete_comment(by_id="A3F7", index=1, text="hi", data_dir=fake_data_dir)
