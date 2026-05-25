"""Unit tests for scout.action_items.edit_comment."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from scout.action_items.edit_comment import edit_comment
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


def test_edit_comment_by_index_replaces_body_only(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_prefix(fake_data_dir)
    daily = _make_daily(
        fake_data_dir,
        "- [ ] [#A3F7] task\n"
        "  - jordan: first draft\n"
        "  - jordan: second draft\n",
    )
    monkeypatch.setattr(
        "scout.action_items.edit_comment._today", lambda: dt.date(2026, 4, 26)
    )

    event = edit_comment(
        by_id="A3F7",
        index=1,
        new_text="first final",
        data_dir=fake_data_dir,
    )

    text = daily.read_text()
    assert "  - jordan: first final\n" in text
    assert "first draft" not in text
    assert "second draft" in text  # untouched
    assert isinstance(event, Event)
    assert event.kind == "action_item.comment_edited"
    assert event.payload["old_text"] == "first draft"
    assert event.payload["new_text"] == "first final"


def test_edit_comment_preserves_original_indent(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A comment indented with four spaces (e.g. under a nested sub-task) keeps
    its indent after the edit. Author prefix also preserved."""
    _register_prefix(fake_data_dir)
    daily = _make_daily(
        fake_data_dir,
        "- [ ] [#A3F7] task\n"
        "    - alice: nested note\n",
    )
    monkeypatch.setattr(
        "scout.action_items.edit_comment._today", lambda: dt.date(2026, 4, 26)
    )

    edit_comment(
        by_id="A3F7",
        index=1,
        new_text="nested edit",
        data_dir=fake_data_dir,
    )

    assert "    - alice: nested edit\n" in daily.read_text()


def test_edit_comment_rejects_empty_text(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_prefix(fake_data_dir)
    _make_daily(fake_data_dir, "- [ ] [#A3F7] task\n  - jordan: original\n")
    monkeypatch.setattr(
        "scout.action_items.edit_comment._today", lambda: dt.date(2026, 4, 26)
    )

    with pytest.raises(ActionItemError, match="new-text must not be empty"):
        edit_comment(
            by_id="A3F7", index=1, new_text="   ", data_dir=fake_data_dir
        )


def test_edit_comment_by_text_substring(
    fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_prefix(fake_data_dir)
    daily = _make_daily(
        fake_data_dir,
        "- [ ] [#A3F7] task\n"
        "  - jordan: ping vendor\n"
        "  - jordan: legal sign-off\n",
    )
    monkeypatch.setattr(
        "scout.action_items.edit_comment._today", lambda: dt.date(2026, 4, 26)
    )

    edit_comment(
        by_id="A3F7",
        text="legal",
        new_text="legal cleared 2026-04-26",
        data_dir=fake_data_dir,
    )

    text = daily.read_text()
    assert "  - jordan: legal cleared 2026-04-26\n" in text
    assert "ping vendor" in text
