"""Parser and rendering branches in the per-file migration.

`test_migrate_perfile.py` covers the idempotent driver end-to-end.
`migrate_perfile` is a **destructive one-shot** — it unlinks the single-file
wishlists after splitting them — so every parsing branch it depends on is one
where a bug means real data is silently dropped or mis-filed. This file drives
the parsers directly against the legacy shapes a mature vault actually
contains: `[in progress]` / `[done]` prefixes, `HIGH`/`MEDIUM`/`LOW` markers,
`(2026-04-15 — source)` parentheticals, priority glyphs, `START IMMEDIATELY`
leads, and `##`/`###` area headings.

Fixture content is anonymized per CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.migrate_perfile import (
    RUN_LOG_HEADER,
    Item,
    _heading_area,
    _last_verified_body,
    _research_queue_has_items,
    _unique_path,
    _yq,
    filename_for,
    main,
    migrate_perfile,
    migrate_research_file,
    migrate_wishlist_file,
    needs_migration,
    parse_research_item,
    parse_wishlist_item,
    render_item,
    slugify,
    split_bullets,
    split_research_items,
)

# ---------------------------------------------------------------------------
# parse_wishlist_item
# ---------------------------------------------------------------------------


def test_a_plain_bold_wishlist_bullet() -> None:
    item = parse_wishlist_item("**Add a status widget** Some longer body text.")
    assert item == Item(
        title="Add a status widget",
        status="open",
        priority="medium",
        date=None,
        source=None,
        body="Some longer body text.",
    )


@pytest.mark.parametrize(
    ("marker", "expected"),
    [("[in progress]", "in-progress"), ("[In Progress]", "in-progress"), ("[done]", "done"), ("[DONE]", "done")],
)
def test_a_status_marker_is_stripped_from_the_title(marker: str, expected: str) -> None:
    item = parse_wishlist_item(f"**{marker} Add a status widget**")
    assert item.status == expected
    assert item.title == "Add a status widget"


@pytest.mark.parametrize(
    ("marker", "expected"),
    [("HIGH", "high"), ("MEDIUM", "medium"), ("LOW", "low")],
)
def test_a_priority_marker_is_stripped_from_the_title(marker: str, expected: str) -> None:
    item = parse_wishlist_item(f"**{marker} — Add a status widget**")
    assert item.priority == expected
    assert item.title == "Add a status widget"


def test_status_and_priority_markers_combine() -> None:
    item = parse_wishlist_item("**[in progress] HIGH - Add a status widget**")
    assert (item.status, item.priority, item.title) == ("in-progress", "high", "Add a status widget")


def test_the_done_file_forces_done_regardless_of_the_marker() -> None:
    """`Wishlist-done.md` is the archive; its rows are complete even when the
    inline marker says otherwise."""
    item = parse_wishlist_item("**[in progress] Add a status widget**", in_done_file=True)
    assert item.status == "done"


def test_a_parenthetical_splits_into_date_and_source() -> None:
    item = parse_wishlist_item("**Add a status widget** (2026-04-15 — Priya in standup). The body.")
    assert item.date == "2026-04-15"
    assert item.source == "Priya in standup"
    assert item.body == "The body."


def test_a_parenthetical_with_only_a_date_has_no_source() -> None:
    item = parse_wishlist_item("**Add a status widget** (2026-04-15)")
    assert item.date == "2026-04-15"
    assert item.source is None


def test_a_parenthetical_with_only_a_source_has_no_date() -> None:
    item = parse_wishlist_item("**Add a status widget** (Priya in standup)")
    assert item.date is None
    assert item.source == "Priya in standup"


def test_a_bullet_with_no_bold_lead_uses_the_whole_line_as_the_title() -> None:
    item = parse_wishlist_item("Add a status widget")
    assert item.title == "Add a status widget"
    assert item.body == ""


def test_a_multiline_wishlist_bullet_keeps_its_body() -> None:
    item = parse_wishlist_item("**Add a status widget**\n  More detail.\n  Even more.")
    assert item.title == "Add a status widget"
    assert "More detail." in item.body


# ---------------------------------------------------------------------------
# parse_research_item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [("- [ ] Investigate the vendor API", "open"), ("- [x] Investigate", "done"), ("- [X] Investigate", "done")],
)
def test_a_research_checkbox_sets_the_status(line: str, expected: str) -> None:
    assert parse_research_item(line).status == expected


@pytest.mark.parametrize(
    ("glyph", "expected"),
    [("🔴", "urgent"), ("🟡", "medium"), ("🟢", "low"), ("🔵", "medium")],
)
def test_a_priority_glyph_is_read_and_stripped(glyph: str, expected: str) -> None:
    item = parse_research_item(f"- [ ] {glyph} Investigate the vendor API")
    assert item.priority == expected
    assert item.title == "Investigate the vendor API"
    assert glyph not in item.title


def test_a_research_line_without_a_checkbox_defaults_to_open() -> None:
    item = parse_research_item("Investigate the vendor API")
    assert item.status == "open"
    assert item.title == "Investigate the vendor API"


def test_a_start_immediately_lead_becomes_urgent_and_is_stripped() -> None:
    item = parse_research_item("- [ ] **START IMMEDIATELY — Investigate the outage**")
    assert item.priority == "urgent"
    assert item.title == "Investigate the outage"


def test_a_start_immediately_lead_is_matched_case_insensitively() -> None:
    item = parse_research_item("- [ ] **Start immediately - Investigate the outage**")
    assert item.priority == "urgent"
    assert item.title == "Investigate the outage"


def test_a_date_in_the_research_body_is_captured() -> None:
    item = parse_research_item("- [ ] **Investigate the vendor API** — raised 2026-04-15")
    assert item.date == "2026-04-15"
    assert item.body == "— raised 2026-04-15"


def test_the_area_is_carried_onto_the_item() -> None:
    assert parse_research_item("- [ ] Investigate", area="platform").area == "platform"


# ---------------------------------------------------------------------------
# slugify / filename_for / _unique_path / _yq / render_item
# ---------------------------------------------------------------------------


def test_slugify_drops_punctuation_and_caps_word_count() -> None:
    assert slugify("Add a Status Widget!") == "add-a-status-widget"
    assert slugify("one two three four five six seven eight nine ten") == ("one-two-three-four-five-six-seven-eight")
    assert slugify("PROJ-1234: the fix") == "proj-1234-the-fix"
    assert slugify("!!!") == ""


def test_filename_prefers_the_items_own_date() -> None:
    item = parse_wishlist_item("**Add a widget** (2026-04-15)")
    assert filename_for(item, "2026-06-01") == "2026-04-15-add-a-widget.md"


def test_filename_falls_back_to_the_default_date() -> None:
    item = parse_wishlist_item("**Add a widget**")
    assert filename_for(item, "2026-06-01") == "2026-06-01-add-a-widget.md"


def test_unique_path_suffixes_on_collision(tmp_path: Path) -> None:
    """Migration is destructive: two items sharing a date+slug must both
    survive rather than one silently overwriting the other."""
    assert _unique_path(tmp_path, "a.md") == tmp_path / "a.md"

    (tmp_path / "a.md").write_text("first", encoding="utf-8")
    assert _unique_path(tmp_path, "a.md") == tmp_path / "a-2.md"

    (tmp_path / "a-2.md").write_text("second", encoding="utf-8")
    (tmp_path / "a-3.md").write_text("third", encoding="utf-8")
    assert _unique_path(tmp_path, "a.md") == tmp_path / "a-4.md"


def test_yaml_quoting_escapes_backslashes_and_quotes() -> None:
    assert _yq('say "hi"') == '"say \\"hi\\""'
    assert _yq("path\\to\\thing") == '"path\\\\to\\\\thing"'


def test_render_item_emits_every_populated_frontmatter_key() -> None:
    item = Item(
        title="Investigate the vendor API",
        status="open",
        priority="urgent",
        date="2026-04-15",
        source="Priya in standup",
        body="Some detail.",
        area="platform",
    )
    out = render_item(item)
    assert out.startswith("---\n")
    assert 'title: "Investigate the vendor API"' in out
    assert "status: open" in out
    assert "priority: urgent" in out
    assert "date: 2026-04-15" in out
    assert 'source: "Priya in standup"' in out
    assert 'area: "platform"' in out
    assert out.endswith("# Investigate the vendor API\n\nSome detail.\n")


def test_render_item_omits_empty_optional_keys() -> None:
    out = render_item(Item(title="t", status="open", priority="medium", date=None, source=None, body=""))
    assert "date:" not in out
    assert "source:" not in out
    assert "area:" not in out


# ---------------------------------------------------------------------------
# split_bullets
# ---------------------------------------------------------------------------


def test_split_bullets_handles_both_markers_and_indented_continuations() -> None:
    text = (
        "# Wishlist\n"
        "\n"
        "- **First item**\n"
        "  continuation of first\n"
        "\n"
        "* **Second item**\n"
        "\tanother continuation\n"
        "- **Third item**\n"
    )
    bullets = split_bullets(text)
    assert len(bullets) == 3
    assert "continuation of first" in bullets[0]
    assert "another continuation" in bullets[1]


def test_a_flush_left_non_bullet_line_closes_the_current_bullet() -> None:
    """CommonMark continuation rules: an unindented paragraph is not part of
    the bullet above it, so it must not be swept into that item's body."""
    text = "- **First item**\nA flush-left paragraph.\n- **Second item**\n"
    bullets = split_bullets(text)
    assert len(bullets) == 2
    assert "flush-left" not in bullets[0]
    assert "flush-left" not in bullets[1]


def test_split_bullets_ignores_leading_prose_and_empty_results() -> None:
    assert split_bullets("# Wishlist\n\nJust prose, no bullets.\n") == []
    assert split_bullets("") == []


def test_split_bullets_requires_content_after_the_marker() -> None:
    # "- " with nothing after it is not a bullet start.
    assert split_bullets("-\n- **Real item**\n") == ["**Real item**"]


# ---------------------------------------------------------------------------
# _heading_area / split_research_items
# ---------------------------------------------------------------------------


def test_heading_area_slugifies_and_strips_a_leading_glyph() -> None:
    assert _heading_area("🔴 Platform Reliability") == "platform-reliability"


def test_heading_area_drops_a_trailing_status_segment() -> None:
    """`## Platform — done` names the area "platform", not "platform-done"."""
    for suffix in ("done", "WIP", "in progress", "in-progress", "✅", "🎯"):
        assert _heading_area(f"Platform — {suffix}") == "platform"


def test_heading_area_keeps_a_non_status_trailing_segment() -> None:
    assert _heading_area("Platform — Q2 goals") == "platform-q2-goals"


def test_the_queue_heading_is_not_an_area() -> None:
    """`## Queue` is the container heading, not a topic — items under it get
    no area rather than `area: "queue"`."""
    assert _heading_area("Queue") is None
    assert _heading_area("queue") is None


def test_a_heading_that_slugifies_to_nothing_is_not_an_area() -> None:
    assert _heading_area("🔴") is None
    assert _heading_area("!!!") is None


def test_split_research_items_attributes_the_nearest_heading() -> None:
    text = (
        "# Research Queue\n"
        "\n"
        "## Platform\n"
        "\n"
        "- [ ] Investigate the vendor API\n"
        "\n"
        "### Storage\n"
        "\n"
        "- [ ] Benchmark the new engine\n"
        "\n"
        "## People\n"
        "\n"
        "- [x] Read the postmortem\n"
    )
    pairs = list(split_research_items(text))
    assert [(line.split("] ")[1], area) for line, area in pairs] == [
        ("Investigate the vendor API", "platform"),
        ("Benchmark the new engine", "storage"),
        ("Read the postmortem", "people"),
    ]


def test_a_new_h2_clears_the_previous_h3_area() -> None:
    text = "## Platform\n### Storage\n## People\n- [ ] Read the postmortem\n"
    assert list(split_research_items(text)) == [("- [ ] Read the postmortem", "people")]


def test_split_research_items_skips_non_checklist_lines() -> None:
    text = "## Platform\n\nSome prose.\n- a plain bullet\n- [ ] A real item\n"
    assert [line for line, _area in split_research_items(text)] == ["- [ ] A real item"]


# ---------------------------------------------------------------------------
# migrate_wishlist_file / migrate_research_file
# ---------------------------------------------------------------------------


def test_migrate_wishlist_file_writes_one_file_per_item(tmp_path: Path) -> None:
    src = tmp_path / "Wishlist.md"
    src.write_text(
        "# Wishlist\n\n- **HIGH — Add a status widget** (2026-04-15 — Priya)\n- **Ship the docs**\n",
        encoding="utf-8",
    )
    out = tmp_path / "wishlist"
    assert migrate_wishlist_file(src, out, False, "2026-06-01") == 2
    assert (out / "2026-04-15-add-a-status-widget.md").is_file()
    assert (out / "2026-06-01-ship-the-docs.md").is_file()


def test_migrate_wishlist_file_skips_a_titleless_bullet(tmp_path: Path) -> None:
    """A bullet that is only markers (`- **HIGH —**`) has no title and no
    filename; skip it rather than writing `2026-06-01-.md`."""
    src = tmp_path / "Wishlist.md"
    src.write_text("- **HIGH —**\n- **Ship the docs**\n", encoding="utf-8")
    out = tmp_path / "wishlist"
    assert migrate_wishlist_file(src, out, False, "2026-06-01") == 1
    assert [p.name for p in out.iterdir()] == ["2026-06-01-ship-the-docs.md"]


def test_migrate_research_file_writes_one_file_per_item_with_areas(tmp_path: Path) -> None:
    src = tmp_path / "research-queue.md"
    src.write_text(
        "# Research Queue\n\n## Platform\n\n- [ ] 🔴 Investigate the vendor API\n- [x] Benchmark the engine\n",
        encoding="utf-8",
    )
    out = tmp_path / "research-queue"
    assert migrate_research_file(src, out, "2026-06-01") == 2
    written = (out / "2026-06-01-investigate-the-vendor-api.md").read_text()
    assert "priority: urgent" in written
    assert 'area: "platform"' in written


def test_migrate_research_file_skips_a_titleless_item(tmp_path: Path) -> None:
    src = tmp_path / "research-queue.md"
    src.write_text("- [ ] 🔴\n- [ ] A real item\n", encoding="utf-8")
    out = tmp_path / "research-queue"
    assert migrate_research_file(src, out, "2026-06-01") == 1


# ---------------------------------------------------------------------------
# _research_queue_has_items / _last_verified_body
# ---------------------------------------------------------------------------


def test_a_migrated_run_log_never_reads_as_needing_migration() -> None:
    """A preserved continuity note can itself contain checklist lines; the
    header short-circuit is what stops a re-migration on the next upgrade."""
    run_log = RUN_LOG_HEADER + "**Last verified 2026-04-15** — see:\n- [ ] a quoted checklist line\n"
    assert _research_queue_has_items(run_log) is False


@pytest.mark.parametrize(
    "body",
    [
        "# Research Queue\n\n## Queue\n\nsome prose\n",
        "# Research Queue\n\n- [ ] an open item\n",
        "# Research Queue\n\n* [x] a done item\n",
    ],
)
def test_a_legacy_research_queue_reads_as_needing_migration(body: str) -> None:
    assert _research_queue_has_items(body) is True


def test_a_research_queue_with_neither_marker_does_not_need_migration() -> None:
    assert _research_queue_has_items("# Research Queue\n\nJust prose.\n") is False


def test_last_verified_body_takes_the_final_matching_paragraph() -> None:
    text = (
        "# Research Queue\n"
        "\n"
        "**Last verified 2026-03-01** — first note.\n"
        "\n"
        "some other paragraph\n"
        "\n"
        "**Last verified 2026-04-15** — the latest note.\n"
        "Continued on a second line.\n"
    )
    body = _last_verified_body(text)
    assert body.startswith("**Last verified 2026-04-15**")
    assert "Continued on a second line." in body
    assert "2026-03-01" not in body


def test_last_verified_body_tolerates_indentation() -> None:
    """The match is on the lstripped paragraph, and the returned body is
    stripped — an indented note is still recognized."""
    assert _last_verified_body("   **Last verified 2026-04-15** — note.\n") == ("**Last verified 2026-04-15** — note.")


def test_last_verified_body_falls_back_when_there_is_no_note() -> None:
    assert _last_verified_body("# Research Queue\n\nJust prose.\n") == "_No runs yet._"
    assert _last_verified_body("") == "_No runs yet._"


def test_last_verified_body_collapses_runs_of_blank_lines() -> None:
    """Consecutive blank lines must not emit empty paragraphs — an empty one
    would `lstrip().startswith(...)` to False and is just noise."""
    text = "para one\n\n\n\n**Last verified 2026-04-15** — note.\n\n\n"
    assert _last_verified_body(text) == "**Last verified 2026-04-15** — note."


# ---------------------------------------------------------------------------
# needs_migration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["Wishlist.md", "Wishlist-in-progress.md", "Wishlist-done.md"])
def test_any_single_file_wishlist_means_the_vault_is_legacy(tmp_path: Path, name: str) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / name).write_text("- **Ship the docs**\n", encoding="utf-8")
    assert needs_migration(tmp_path) is True


def test_a_research_queue_still_holding_items_means_the_vault_is_legacy(tmp_path: Path) -> None:
    kb = tmp_path / "knowledge-base"
    kb.mkdir()
    (kb / "research-queue.md").write_text("# Research Queue\n\n- [ ] an open item\n", encoding="utf-8")
    assert needs_migration(tmp_path) is True


def test_a_vault_that_never_had_these_files_needs_no_migration(tmp_path: Path) -> None:
    assert needs_migration(tmp_path) is False


def test_a_fully_migrated_vault_needs_no_migration(tmp_path: Path) -> None:
    kb = tmp_path / "knowledge-base"
    kb.mkdir()
    (kb / "research-queue.md").write_text(RUN_LOG_HEADER + "_No runs yet._\n", encoding="utf-8")
    (tmp_path / "docs" / "wishlist").mkdir(parents=True)
    assert needs_migration(tmp_path) is False


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_without_a_vault_argument_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage: migrate_perfile.py <vault>" in capsys.readouterr().err


def test_main_migrates_the_named_vault(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "Wishlist.md").write_text("- **Ship the docs**\n", encoding="utf-8")

    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "'migrated': True" in out
    assert not (docs / "Wishlist.md").exists()
    assert list((docs / "wishlist").iterdir())


def test_main_reads_sys_argv_when_no_argv_is_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["migrate_perfile.py", str(tmp_path)])
    assert main() == 0
    assert "'migrated': False" in capsys.readouterr().out


def test_migrate_is_a_no_op_on_an_already_migrated_vault(tmp_path: Path) -> None:
    assert migrate_perfile(tmp_path) == {"migrated": False}
