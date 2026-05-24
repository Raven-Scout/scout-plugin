"""Unit tests for scout.action_items.parser.

Drives all assertions off engine/tests/fixtures/action-items-sample.md
so behavior remains anchored to a real, version-controlled document.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.parser import ActionItem, parse_file

FIXTURE = Path(__file__).parent.parent / "fixtures" / "action-items-sample.md"


@pytest.fixture
def items() -> list[ActionItem]:
    return parse_file(FIXTURE)


def test_parses_all_items(items: list[ActionItem]) -> None:
    assert len(items) == 7  # 3 in progress + 2 to do + 1 watching + 1 completed


def test_open_vs_done_status(items: list[ActionItem]) -> None:
    open_titles = [i.title for i in items if i.status == "open"]
    done_titles = [i.title for i in items if i.status == "done"]
    assert "Submit Lever feedback to recruiting" in open_titles
    assert "Read incident postmortem" in done_titles


def test_priority_extraction(items: list[ActionItem]) -> None:
    by_title = {i.title: i for i in items}
    assert by_title["Submit Lever feedback to recruiting"].priority == "🔴"
    assert by_title["Send Scout plugin announcement"].priority == "🟡"
    assert by_title["Read incident postmortem"].priority == "🟢"
    assert by_title["Followup with vendor on contract redlines"].priority == ""


def test_section_attribution(items: list[ActionItem]) -> None:
    by_title = {i.title: i for i in items}
    assert by_title["Submit Lever feedback to recruiting"].section == "In Progress"
    assert by_title["Reply to Q2 budget thread"].section == "To Do"
    assert by_title["Vendor SLA renegotiation (no action yet)"].section == "Watching"
    assert by_title["Submit weekly status"].section == "Completed Today"


def test_sub_bullets_collected(items: list[ActionItem]) -> None:
    by_title = {i.title: i for i in items}
    lever = by_title["Submit Lever feedback to recruiting"]
    # context_links comes from "Context: <url>" sub-bullet
    assert any("example.com/lever" in link for link in lever.context_links)
    # details from all sub-bullets (including "Notes: ..." sub-bullet)
    assert any("hiring manager" in detail for detail in lever.details)


def test_raw_line_preserved_for_substring_lookup(items: list[ActionItem]) -> None:
    """Writer modules locate items by full-line substring match;
    `raw_line` must be the exact original source line."""
    by_title = {i.title: i for i in items}
    raw = by_title["Reply to Q2 budget thread"].raw_line
    assert "[ ]" in raw
    assert "🔴" in raw
    assert "Reply to Q2 budget thread" in raw


PREFIX_FIXTURE = Path(__file__).parent.parent / "fixtures" / "action-items-with-prefixes.md"


def test_parser_extracts_short_prefix_when_present() -> None:
    items = parse_file(PREFIX_FIXTURE)
    by_title = {i.title: i for i in items}
    assert by_title["Submit Lever feedback to recruiting"].short_prefix == "A3F7"
    assert by_title["Read incident postmortem"].short_prefix == "B5K2"
    assert by_title["Reply to Q2 budget thread"].short_prefix == "C9N4"


def test_parser_short_prefix_is_none_for_unprefixed_line() -> None:
    items = parse_file(PREFIX_FIXTURE)
    by_title = {i.title: i for i in items}
    assert by_title["Send Scout plugin announcement"].short_prefix is None
    assert by_title["Followup with vendor on contract redlines"].short_prefix is None


def test_parser_strips_prefix_from_title() -> None:
    """Title field should not include `[#XXXX]` — that's what short_prefix is for."""
    items = parse_file(PREFIX_FIXTURE)
    titles = [i.title for i in items]
    assert all("[#" not in t for t in titles)


def test_parser_raw_line_preserves_prefix() -> None:
    """raw_line is the unmodified source line; substring fallback uses it."""
    items = parse_file(PREFIX_FIXTURE)
    by_title = {i.title: i for i in items}
    assert "[#A3F7]" in by_title["Submit Lever feedback to recruiting"].raw_line


def test_parser_handles_prefix_and_priority_emoji_together() -> None:
    """A prefixed line with a priority emoji must produce all three fields correctly:
    short_prefix from [#XXXX], priority from the emoji, title with neither.
    Pins the interaction Tasks 18-20 will rely on.
    """
    items = parse_file(PREFIX_FIXTURE)
    by_title = {i.title: i for i in items}
    item = by_title["Submit Lever feedback to recruiting"]
    assert item.short_prefix == "A3F7"
    assert item.priority == "🔴"
    assert item.title == "Submit Lever feedback to recruiting"
    assert "[#A3F7]" not in item.title
    assert "🔴" not in item.title


def test_parser_handles_prefix_without_priority_emoji() -> None:
    """A prefixed line with no priority emoji must produce short_prefix populated,
    priority empty, title clean of any prefix marker.
    """
    items = parse_file(PREFIX_FIXTURE)
    by_title = {i.title: i for i in items}
    item = by_title["Plain prefixed task without priority"]
    assert item.short_prefix == "E1Q2"
    assert item.priority == ""
    assert item.title == "Plain prefixed task without priority"


# Regression: parse_file must specify encoding="utf-8" so non-UTF-8 locales
# (e.g. LANG=C in CI) don't silently corrupt emoji/wikilink/Unicode content.
# Issue #33.


def test_parse_file_reads_with_explicit_utf8_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """parse_file must pass encoding='utf-8' to read_text — otherwise the
    platform default applies and non-UTF-8 locales silently re-encode the
    file on the round-trip parse→write."""
    md = tmp_path / "scout.md"
    md.write_text("# X\n\n## Section\n\n- [ ] 🔴 Important task\n", encoding="utf-8")

    captured: list[str | None] = []
    real_read_text = Path.read_text

    def spy(self: Path, *args: object, **kwargs: object) -> str:
        captured.append(kwargs.get("encoding"))  # type: ignore[arg-type]
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", spy)
    items = parse_file(md)

    assert "utf-8" in captured, f"read_text was called without encoding=utf-8: {captured}"
    assert any(i.priority == "🔴" for i in items)
