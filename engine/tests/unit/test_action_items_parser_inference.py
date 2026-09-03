"""Status/priority inference and helper coverage for the action-items parser.

`test_action_items_parser.py` and the corpus contract test
(`test_parser_contract.py`) cover the checkbox/prefix/priority happy paths.
What's left is the inference cascade in `_infer_status` — the legacy formats a
mature vault still contains (`✅`/`🔄` prefixes, `~~strike~~`, `— Done`
suffixes, and section/subsection defaults) — plus `### ` subsection handling,
TUI note attachment, and the two public grouping helpers.

Those matter because a mis-inferred status hides an open item from
`filter_actionable`, which is what the briefing prompt reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.parser import (
    ActionItem,
    filter_actionable,
    items_by_priority,
    parse_action_items,
    parse_lines,
)


def _lines(text: str) -> list[str]:
    """splitlines() with an explicit list[str] type (parse_lines is invariant)."""
    return list(text.splitlines())


def _items(text: str) -> list[ActionItem]:
    return parse_lines(_lines(text))


def _one(text: str) -> ActionItem:
    items = _items(text)
    assert len(items) == 1, items
    return items[0]


# ---------------------------------------------------------------------------
# parse_action_items — file-level
# ---------------------------------------------------------------------------


def test_parse_action_items_returns_empty_for_a_missing_file(tmp_path: Path) -> None:
    """The daily file legitimately doesn't exist before the first briefing;
    callers read the result as "no items", not an error."""
    assert parse_action_items(tmp_path / "action-items-2026-04-15.md") == []


def test_parse_action_items_reads_a_real_file(tmp_path: Path) -> None:
    p = tmp_path / "action-items-2026-04-15.md"
    p.write_text("## To Do\n\n- [ ] 🔴 a task\n", encoding="utf-8")
    items = parse_action_items(p)
    assert [i.title for i in items] == ["a task"]


# ---------------------------------------------------------------------------
# _infer_status cascade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("- [x] explicit done", "done"),
        ("- [X] explicit done upper", "done"),
        ("- [ ] explicit open", "open"),
        # Legacy status-emoji prefixes, before checkboxes were adopted.
        ("- ✅ finished item", "done"),
        ("- 🔄 in flight", "in_progress"),
        ("- ~~dropped item~~", "done"),
        ("- Ship the thing — Done", "done"),
        ("- Ship the thing — Completed", "done"),
        ("- Ship the thing ✅ Done", "done"),
        # Nothing to infer from -> open.
        ("- a plain bullet", "open"),
    ],
)
def test_infer_status_from_the_line_itself(line: str, expected: str) -> None:
    assert _one(f"## Notes\n\n{line}\n").status == expected


def test_a_checkbox_beats_a_done_looking_section() -> None:
    """An explicit `- [ ]` under "## Completed Today" is still open — the
    checkbox is the author's intent and must win over the section default."""
    items = _items("## Completed Today\n\n- [ ] actually still open\n- [x] really done\n")
    by_title = {i.title: i.status for i in items}
    assert by_title == {"actually still open": "open", "really done": "done"}


@pytest.mark.parametrize(
    ("section", "expected"),
    [
        ("Completed Today", "done"),
        ("Completed", "done"),
        ("Done", "done"),
        ("In Progress", "in_progress"),
        ("To Do", "open"),
        ("Todo", "open"),
        ("Watching", "watching"),
        ("Upcoming", "open"),
        ("Something Else", "open"),  # no mapping -> open
    ],
)
def test_infer_status_from_the_section_header(section: str, expected: str) -> None:
    assert _one(f"## {section}\n\n- a plain bullet\n").status == expected


def test_infer_status_from_the_section_is_case_insensitive() -> None:
    assert _one("## COMPLETED TODAY\n\n- a plain bullet\n").status == "done"


@pytest.mark.parametrize("subsection", ["✅ Security Plugin", "Rollout — Done", "done work"])
def test_infer_status_from_a_done_marking_subsection(subsection: str) -> None:
    """`### ✅ …` / `### … — Done` are status markers: bullets under them are
    complete even without a checkbox."""
    assert _one(f"## To Do\n\n### {subsection}\n\n- a plain bullet\n").status == "done"


def test_a_subsection_beats_the_parent_section_for_status() -> None:
    item = _one("## Watching\n\n### ✅ Shipped\n\n- a plain bullet\n")
    assert item.status == "done"


# ---------------------------------------------------------------------------
# Priority inference
# ---------------------------------------------------------------------------


def test_priority_comes_from_the_line_when_present() -> None:
    assert _one("## 🟢 Watching\n\n- [ ] 🔴 urgent despite the section\n").priority == "🔴"


def test_priority_falls_back_to_the_subsection() -> None:
    item = _one("## To Do\n\n### 🟡 Medium bucket\n\n- [ ] no glyph on the line\n")
    assert item.priority == "🟡"


def test_priority_falls_back_to_the_section() -> None:
    assert _one("## 🔴 Urgent\n\n- [ ] no glyph on the line\n").priority == "🔴"


def test_the_subsection_beats_the_section_for_priority() -> None:
    item = _one("## 🔴 Urgent\n\n### 🟢 Low bucket\n\n- [ ] no glyph on the line\n")
    assert item.priority == "🟢"


def test_priority_is_empty_when_nothing_declares_one() -> None:
    assert _one("## To Do\n\n- [ ] no glyph anywhere\n").priority == ""


# ---------------------------------------------------------------------------
# Sections, subsections and attachment
# ---------------------------------------------------------------------------


def test_the_subsection_becomes_the_reported_section_label() -> None:
    assert _one("## To Do\n\n### Q2 budget\n\n- [ ] a task\n").section == "Q2 budget"


def test_the_section_label_falls_back_to_the_h2() -> None:
    assert _one("## To Do\n\n- [ ] a task\n").section == "To Do"


def test_a_new_subsection_detaches_the_previous_item() -> None:
    """A sub-bullet after a `###` must not be appended to the item above the
    heading — it belongs to whatever comes next."""
    items = _items("## To Do\n\n- [ ] first task\n\n### Later\n\n  - orphan sub-bullet\n- [ ] second task\n")
    first = next(i for i in items if i.title == "first task")
    assert first.details == []


def test_a_new_section_detaches_the_previous_item() -> None:
    items = _items("## To Do\n\n- [ ] first task\n\n## Watching\n\n  - orphan sub-bullet\n")
    assert next(i for i in items if i.title == "first task").details == []


def test_tui_notes_attach_to_the_preceding_item() -> None:
    item = _one("## To Do\n\n- [ ] a task\n  - **[TUI note 2026-04-15]** looked into it\n")
    assert item.notes == ["- **[TUI note 2026-04-15]** looked into it"]
    # A note is not a detail sub-bullet.
    assert item.details == []


def test_sub_bullets_collect_details_and_context_links() -> None:
    item = _one(
        "## To Do\n\n- [ ] a task\n  - Source: https://example.com/thread\n  - See [[knowledge-base/rollout-plan]]\n"
    )
    assert item.context_links == ["https://example.com/thread", "kb://knowledge-base/rollout-plan"]
    assert len(item.details) == 2


@pytest.mark.parametrize(
    "line",
    [
        "| Time | Meeting |",  # a table row
        "> a blockquote",
        "",
        "   ",
        "Just a paragraph of prose.",
    ],
)
def test_non_content_lines_never_become_items(line: str) -> None:
    assert _items(f"## To Do\n\n{line}\n") == []


@pytest.mark.parametrize("fence", ["```", "~~~", "```markdown"])
def test_fenced_blocks_are_not_parsed(fence: str) -> None:
    """Documentation examples inside a fence must not be mistaken for real
    items — backfill would otherwise rewrite the docs (#40)."""
    text = f"## To Do\n\n{fence}\n- [ ] example task from the docs\n## Not a heading\n```\n\n- [ ] real task\n"
    items = parse_lines(_lines(text))
    assert [i.title for i in items] == ["real task"]
    # The `## Not a heading` inside the fence must not have changed the section.
    assert items[0].section == "To Do"


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


def test_items_sort_by_status_then_priority() -> None:
    # An explicit `- [ ]` forces "open" regardless of section (see
    # test_a_checkbox_beats_a_done_looking_section), so the in_progress rows
    # here are bare bullets under "## In Progress".
    items = _items(
        "## In Progress\n\n- 🟢 wip low\n- 🔴 wip urgent\n"
        "## To Do\n\n- [ ] 🔴 open urgent\n- [ ] 🟡 open medium\n"
        "## Watching\n\n- 🟢 watching low\n"
        "## Completed Today\n\n- [x] 🔴 done urgent\n"
    )
    assert [i.title for i in items] == [
        "wip urgent",
        "wip low",
        "open urgent",
        "open medium",
        "watching low",
        "done urgent",
    ]


# ---------------------------------------------------------------------------
# filter_actionable / items_by_priority
# ---------------------------------------------------------------------------


def test_filter_actionable_drops_done_items() -> None:
    items = _items("## To Do\n\n- [ ] open task\n- [x] done task\n")
    assert [i.title for i in filter_actionable(items)] == ["open task"]


def test_filter_actionable_drops_calendar_sections() -> None:
    """Calendar rows are informational; surfacing them as actionable made the
    briefing's "what to do" list unreadable."""
    items = _items("## To Do\n\n- [ ] open task\n## Calendar\n\n- [ ] 09:00 standup\n")
    assert [i.title for i in filter_actionable(items)] == ["open task"]


def test_filter_actionable_matches_calendar_case_insensitively() -> None:
    items = _items("## Today's CALENDAR\n\n- [ ] 09:00 standup\n")
    assert filter_actionable(items) == []


def test_items_by_priority_always_returns_the_four_known_buckets() -> None:
    items = _items("## To Do\n\n- [ ] 🔴 urgent\n- [ ] no glyph\n")
    groups = items_by_priority(items)
    assert set(groups) == {"🔴", "🟡", "🟢", ""}
    assert [i.title for i in groups["🔴"]] == ["urgent"]
    assert [i.title for i in groups[""]] == ["no glyph"]
    assert groups["🟡"] == [] and groups["🟢"] == []


def test_items_by_priority_adds_a_bucket_for_an_unexpected_glyph() -> None:
    """A future priority glyph must land in its own bucket rather than being
    dropped on the floor."""
    groups = items_by_priority([ActionItem(priority="🔵", title="new glyph", status="open", section="To Do")])
    assert [i.title for i in groups["🔵"]] == ["new glyph"]


def test_items_by_priority_on_an_empty_list() -> None:
    assert items_by_priority([]) == {"🔴": [], "🟡": [], "🟢": [], "": []}
