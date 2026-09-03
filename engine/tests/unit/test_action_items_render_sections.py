"""Per-section-kind coverage of the action-items HTML renderer.

`test_action_items_render.py` covers the happy path over the shared fixture
plus comment binding. The renderer's other half — one specialized emitter per
section emoji (`💡` focus, `📅` meetings, `✅` completed, `📋` digest), the
deep-link scanner, the markdown-aware subject splitter and the inline
formatter — is only reachable from richer markdown, so this file drives each
of those from a purpose-built document.

Fixture content is anonymized per CLAUDE.md: `PROJ-`/`OPS-` Linear prefixes,
`example-org` repos, `acme-co.slack.com` links, Alex/Priya/Sam.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.render import (
    SECTION_STYLES,
    _plain_subject,
    _render_task_links,
    _section_kind,
    _split_subject,
    parse,
    render,
)

# One document exercising every section kind and inline construct.
FULL_DOC = """# Action Items — Wednesday, Apr 15, 2026
**Morning briefing** — Last updated 08:12 ET

Preamble paragraph one.
Preamble paragraph two.

---

## 💡 Today's Focus

- Ship the parser fix
- Reply to Priya about the rollout

## 🔴 Urgent / Time-sensitive

- [ ] [#A3F7] **Land PROJ-1234** — blocked on review, see PROJ-1234 again
  - Source: standup
  > Alex (2026-04-15 09:00 ET): taking a look
- [ ] Review https://github.com/example-org/widgets/pull/42 — and the same PR https://github.com/example-org/widgets/pull/42
- [ ] Follow the thread https://acme-co.slack.com/archives/C0123456789/p1700000000000000
- [x] [#B5K2] Already handled — nothing to do

## 🟡 To Do

- [ ] Draft the `scoutctl` migration note — uses ~~old~~ new flags and *emphasis*
- [ ] Read [[knowledge-base/rollout-plan|the rollout plan]] and [[bare-wikilink]]
- [ ] Check [the changelog](https://example.com/changelog)

## 🟢 Watching

- [ ] Watch for the vendor reply

## 🏡 Personal errands

- [ ] Book the dentist

## 📅 Today's Meetings

| Time | Meeting | Attendees |
| :--- | :------ | --------: |
| 09:00 | Standup | Alex, Priya |
| 14:00 | Review | Sam |

### Tentative

| Time | Meeting |
| --- | --- |
| 16:00 | Coffee |

## ✅ Completed Today

- [x] **Sent the weekly status** — to the whole team
- [x] Filed OPS-77
- [ ] Not actually done

## 📋 Scout Digest

**Rollout**

The rollout is on track.
Second paragraph of the same block.

**Risks**

One risk remains.

## Uncategorized bucket

- [ ] A task in a section with no emoji
"""


@pytest.fixture
def doc(tmp_path: Path) -> Path:
    p = tmp_path / "action-items-2026-04-15.md"
    p.write_text(FULL_DOC, encoding="utf-8")
    return p


@pytest.fixture
def out(doc: Path) -> str:
    return render(doc)


# ---------------------------------------------------------------------------
# parse()
# ---------------------------------------------------------------------------


def test_parse_captures_title_preamble_and_every_section(doc: Path) -> None:
    title, preamble, sections = parse(doc)
    assert title == "Action Items — Wednesday, Apr 15, 2026"
    # The bold "**Morning briefing** …" line and both paragraphs precede the
    # first `##`, so all three are preamble.
    assert "Preamble paragraph one." in preamble
    assert "Preamble paragraph two." in preamble
    assert [s.title for s in sections] == [
        "Today's Focus",
        "Urgent / Time-sensitive",
        "To Do",
        "Watching",
        "Personal errands",
        "Today's Meetings",
        "Completed Today",
        "Scout Digest",
        "Uncategorized bucket",
    ]


def test_parse_collects_focus_bullets_not_tasks(doc: Path) -> None:
    _, _, sections = parse(doc)
    focus = next(s for s in sections if s.title == "Today's Focus")
    assert focus.tasks == []
    assert [b.text for b in focus.bullets] == [
        "Ship the parser fix",
        "Reply to Priya about the rollout",
    ]


def test_parse_reads_tables_and_subheads(doc: Path) -> None:
    _, _, sections = parse(doc)
    meetings = next(s for s in sections if s.title == "Today's Meetings")
    assert len(meetings.tables) == 2
    assert meetings.tables[0].headers == ["Time", "Meeting", "Attendees"]
    # The `| :--- | :---: |` alignment row is dropped, not read as data.
    assert meetings.tables[0].rows == [["09:00", "Standup", "Alex, Priya"], ["14:00", "Review", "Sam"]]
    assert meetings.subheads == ["Tentative"]
    assert meetings.tables[1].rows == [["16:00", "Coffee"]]


def test_parse_treats_bare_paragraphs_inside_a_section_as_bullets(doc: Path) -> None:
    """The digest's prose lines aren't bullets in the markdown; the parser
    still collects them so `_render_digest` can wrap them in <p>."""
    _, _, sections = parse(doc)
    digest = next(s for s in sections if s.title == "Scout Digest")
    texts = [b.text for b in digest.bullets]
    assert "The rollout is on track." in texts
    assert "**Rollout**" in texts


def test_parse_of_a_file_with_no_h1_starts_at_line_zero(tmp_path: Path) -> None:
    p = tmp_path / "no-title.md"
    p.write_text("## 🔴 Urgent\n\n- [ ] a task\n", encoding="utf-8")
    title, preamble, sections = parse(p)
    assert title == ""
    assert preamble == []
    assert [s.title for s in sections] == ["Urgent"]


def test_parse_section_whose_first_token_is_not_an_emoji_keeps_the_full_title(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text("# T\n\n## Q2 budget review\n\n- [ ] a task\n", encoding="utf-8")
    _, _, sections = parse(p)
    assert sections[0].emoji == ""
    assert sections[0].title == "Q2 budget review"


def test_parse_ignores_subheads_and_hrules_before_the_first_section(tmp_path: Path) -> None:
    p = tmp_path / "d.md"
    p.write_text("# T\n\n***\n\n### orphan subhead\n\n## 🔴 Urgent\n\n- [ ] a task\n", encoding="utf-8")
    _, preamble, sections = parse(p)
    assert len(sections) == 1
    assert sections[0].subheads == []
    # "### orphan subhead" arrives with current=None, so it lands in preamble.
    assert any("orphan subhead" in p_ for p_ in preamble)


# ---------------------------------------------------------------------------
# _section_kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("emoji", "expected"), [(e, v[0]) for e, v in SECTION_STYLES.items()])
def test_section_kind_maps_every_known_emoji(emoji: str, expected: str) -> None:
    from scout.action_items.render import Section

    assert _section_kind(Section(emoji=emoji, title="whatever")) == expected


def test_section_kind_falls_back_to_personal_by_title() -> None:
    from scout.action_items.render import Section

    assert _section_kind(Section(emoji="🏡", title="Personal errands")) == "personal"
    assert _section_kind(Section(emoji="", title="PERSONAL stuff")) == "personal"


def test_section_kind_defaults_to_neutral() -> None:
    from scout.action_items.render import Section

    assert _section_kind(Section(emoji="", title="Uncategorized bucket")) == "neutral"


# ---------------------------------------------------------------------------
# _split_subject — the separator must not land inside a markdown token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "subject", "body"),
    [
        ("**Land it** — blocked on review", "**Land it**", "blocked on review"),
        ("Plain subject – en dash body", "Plain subject", "en dash body"),
        ("Plain subject - hyphen body", "Plain subject", "hyphen body"),
        # A dash inside each token type must be skipped over.
        ("`a - b` — real body", "`a - b`", "real body"),
        ("~~a - b~~ — real body", "~~a - b~~", "real body"),
        ("**a - b** — real body", "**a - b**", "real body"),
        ("[[note - draft]] — real body", "[[note - draft]]", "real body"),
        ("[label - x](https://example.com/a-b) — real body", "[label - x](https://example.com/a-b)", "real body"),
        # No separator at all: the whole line is the subject.
        ("No separator here", "No separator here", ""),
    ],
)
def test_split_subject_respects_markdown_tokens(line: str, subject: str, body: str) -> None:
    assert _split_subject(line) == (subject, body)


def test_split_subject_uses_the_first_separator_outside_tokens() -> None:
    assert _split_subject("subject — body — trailing") == ("subject", "body — trailing")


@pytest.mark.parametrize(
    ("line", "subject", "body"),
    [
        # With no dash separator, a ": " outside tokens splits instead.
        ("Budget review: reply to Priya", "Budget review", "reply to Priya"),
        # ...and the same token-awareness applies to that fallback.
        ("`ratio: 3` is fine", "`ratio: 3` is fine", ""),
        ("**stage: two** shipped", "**stage: two** shipped", ""),
        ("~~stage: two~~ dropped", "~~stage: two~~ dropped", ""),
        ("**Bold head**: the body", "**Bold head**", "the body"),
        # A colon with no following space is not a separator.
        ("ratio:3 unchanged", "ratio:3 unchanged", ""),
    ],
)
def test_split_subject_colon_fallback(line: str, subject: str, body: str) -> None:
    assert _split_subject(line) == (subject, body)


@pytest.mark.parametrize(
    "line",
    [
        # A bare `[` ... `]` with no `(` after it: bracket_depth must reset so a
        # later dash is still found.
        "[tag] — real body",
        # Unbalanced parens must not swallow the rest of the line.
        "[label](https://example.com/x) — real body",
    ],
)
def test_split_subject_handles_bracket_forms_without_swallowing_the_body(line: str) -> None:
    subject, body = _split_subject(line)
    assert body == "real body"
    assert subject == line.split(" — ")[0]


def test_plain_subject_strips_markdown_for_the_cli_needle() -> None:
    """The rendered mark-done command must carry the same stripped needle the
    CLI compares against."""
    assert _plain_subject("**Land** `PROJ-1234` ~~old~~ [[note]] [text](https://example.com)") == (
        "Land PROJ-1234 old note text"
    )


# ---------------------------------------------------------------------------
# Deep links
# ---------------------------------------------------------------------------


def _task(subject: str, body: str = ""):
    from scout.action_items.render import Task

    return Task(done=False, subject=subject, body=body, raw=subject)


def test_task_links_deduplicate_repeated_linear_ids() -> None:
    html_out = _render_task_links(_task("Land PROJ-1234", "still PROJ-1234 and PROJ-1234"))
    assert html_out.count("Linear PROJ-1234") == 1
    assert "linear.app/" in html_out


def test_task_links_uses_the_configured_linear_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workspace slug is env-driven so no real workspace is baked in."""
    import importlib

    monkeypatch.setenv("SCOUT_LINEAR_WORKSPACE", "acme-co")
    mod = importlib.reload(importlib.import_module("scout.action_items.render"))
    try:
        assert "linear.app/acme-co/issue/PROJ-1234" in mod._render_task_links(_task("Land PROJ-1234"))
    finally:
        monkeypatch.delenv("SCOUT_LINEAR_WORKSPACE", raising=False)
        importlib.reload(mod)


def test_task_links_deduplicate_github_prs_and_slack_threads() -> None:
    pr = "https://github.com/example-org/widgets/pull/42"
    slack = "https://acme-co.slack.com/archives/C0123456789/p1700000000000000"
    html_out = _render_task_links(_task(f"Review {pr}", f"{pr} and {slack} and {slack}"))
    assert html_out.count("PR example-org/widgets#42") == 1
    assert html_out.count("Slack thread") == 1


def test_task_links_are_empty_when_nothing_matches() -> None:
    assert _render_task_links(_task("Book the dentist")) == ""


def test_task_links_open_safely_in_a_new_tab() -> None:
    html_out = _render_task_links(_task("Land PROJ-1234"))
    assert 'target="_blank"' in html_out
    assert 'rel="noopener noreferrer"' in html_out


# ---------------------------------------------------------------------------
# render() — the per-kind emitters
# ---------------------------------------------------------------------------


def test_render_emits_a_focus_box(out: str) -> None:
    assert 'class="focus-box"' in out
    assert "<li>Ship the parser fix</li>" in out


def test_render_emits_the_meetings_table_with_its_subhead(out: str) -> None:
    assert 'class="section section-meetings"' in out
    assert '<table class="mtg">' in out
    assert "<th>Time</th>" in out
    assert "<td>Standup</td>" in out
    assert "<h3>Tentative</h3>" in out
    assert "<td>Coffee</td>" in out


def test_render_completed_section_counts_only_done_tasks(out: str) -> None:
    assert 'class="completed"' in out
    assert "Sent the weekly status" in out
    assert "Filed OPS-77" in out
    # The open task inside ✅ is excluded, so the count is 2, not 3.
    assert '<span class="count">(2)</span>' in out
    assert "Not actually done" not in out


def test_render_digest_splits_blocks_on_bold_subheads(out: str) -> None:
    assert 'class="digest"' in out
    assert "<h3><strong>Rollout</strong></h3>" in out
    assert "<h3><strong>Risks</strong></h3>" in out
    assert out.count('class="digest-block"') == 2
    assert "<p>The rollout is on track.</p>" in out


def test_render_emoji_less_section_falls_back_to_cards(out: str) -> None:
    assert "A task in a section with no emoji" in out


def test_render_summary_counts_open_tasks_per_kind(out: str) -> None:
    """The stat row is the first thing a reader looks at; a mis-bucketed count
    is worse than no count. Urgent has 3 open + 1 done."""
    summary = out.split('<section class="summary">', 1)[1].split("</section>", 1)[0]
    assert '<div class="stat urgent"><div class="num">3</div>' in summary
    assert '<div class="stat warn"><div class="num">3</div>' in summary
    assert '<div class="stat info"><div class="num">1</div>' in summary
    assert '<div class="stat muted"><div class="num">1</div>' in summary
    assert '<div class="stat ok"><div class="num">2</div>' in summary


def test_render_names_the_source_file_in_the_footer(out: str) -> None:
    assert "<code>action-items-2026-04-15.md</code>" in out


def test_render_carries_preamble_paragraphs_into_the_header(out: str) -> None:
    assert "<p>Preamble paragraph one.</p>" in out


# ---------------------------------------------------------------------------
# _inline
# ---------------------------------------------------------------------------


def test_inline_renders_every_markdown_token(out: str) -> None:
    assert "<code>scoutctl</code>" in out
    assert "<s>old</s>" in out
    assert "<em>emphasis</em>" in out
    assert "<strong>Land PROJ-1234</strong>" in out


def test_inline_renders_wikilinks_as_pills_using_the_label(out: str) -> None:
    assert '<span class="wiki">the rollout plan</span>' in out
    # A bare wikilink falls back to the last path segment.
    assert '<span class="wiki">bare-wikilink</span>' in out


def test_inline_renders_markdown_links_as_anchors(out: str) -> None:
    assert '<a href="https://example.com/changelog" target="_blank" rel="noopener">the changelog</a>' in out


def test_render_escapes_html_in_task_text(tmp_path: Path) -> None:
    p = tmp_path / "action-items-2026-04-15.md"
    p.write_text(
        '# T\n\n## 🔴 Urgent\n\n- [ ] Fix <script>alert("x")</script> in the parser\n',
        encoding="utf-8",
    )
    out = render(p)
    assert "<script>alert" not in out
    assert "&lt;script&gt;" in out


def test_render_escapes_html_in_the_title(tmp_path: Path) -> None:
    p = tmp_path / "action-items-2026-04-15.md"
    p.write_text("# Action <b>Items</b>\n\n## 🔴 Urgent\n\n- [ ] a task\n", encoding="utf-8")
    out = render(p)
    assert "<title>Action &lt;b&gt;Items&lt;/b&gt;</title>" in out


def test_render_task_actions_truncate_a_long_needle(tmp_path: Path) -> None:
    """The copy-to-clipboard command embeds a subject substring; over ~40 chars
    it is trimmed so the command stays readable."""
    long_subject = "A very long action item subject that keeps going well past forty characters"
    p = tmp_path / "action-items-2026-04-15.md"
    p.write_text(f"# T\n\n## 🔴 Urgent\n\n- [ ] {long_subject}\n", encoding="utf-8")
    out = render(p)
    # 40 chars then rstrip: "…that kee" (the 41st char is the 'p' of "keeps").
    assert "--subject &quot;A very long action item subject that kee&quot;" in out
    # The date is threaded in from the filename stem.
    assert "mark_done.py 2026-04-15 --subject" in out
    # ...and the untruncated subject appears only in the card body, never in a
    # copy-command payload.
    for chunk in out.split('data-cmd="')[1:]:
        assert long_subject not in chunk.split('"', 1)[0]


def test_render_offers_no_actions_for_completed_tasks(tmp_path: Path) -> None:
    p = tmp_path / "action-items-2026-04-15.md"
    p.write_text("# T\n\n## 🔴 Urgent\n\n- [x] done already\n", encoding="utf-8")
    out = render(p)
    assert 'class="task-actions"' not in out


def test_render_escapes_quotes_in_the_copy_command(tmp_path: Path) -> None:
    p = tmp_path / "action-items-2026-04-15.md"
    p.write_text('# T\n\n## 🔴 Urgent\n\n- [ ] Reply to the "budget" thread\n', encoding="utf-8")
    out = render(p)
    # The needle is embedded inside a double-quoted shell argument, so an inner
    # quote must be backslash-escaped before HTML escaping.
    assert "&quot;budget&quot;" in out or "\\&quot;budget\\&quot;" in out
