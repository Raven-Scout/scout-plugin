"""Unit tests for engine/scout/scripts/phase_backport.py.

Covers the reverse-mapping logic that pushes vault brain-file edits back into
phase fragments: conservative re-templatizing, diff hunking, section location,
and the round-trip safety gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.phase_backport import (
    RenderedSection,
    apply_section_edits,
    apply_to_phase_text,
    build_rendered_sections,
    diff_hunks,
    insert_after_anchor,
    plan_backport,
    retemplatize,
)

VARS = {
    "USER_NAME": "Alex",
    "INSTANCE_NAME": "Scout",
    "USER_EMAIL": "alex@example.com",
    "GITHUB_USERNAME": "alexdev",
    "SCOUT_DIR": "/home/alex/vault",
    "TIMEZONE": "America/New_York",
}


# ---------- retemplatize ----------


def test_retemplatize_reverses_safe_vars():
    lines = ["Email alex@example.com and repo alexdev live here."]
    out, risky = retemplatize(lines, VARS)
    assert out == ["Email {{USER_EMAIL}} and repo {{GITHUB_USERNAME}} live here."]
    assert risky == []


def test_retemplatize_flags_risky_vars_but_leaves_them_literal():
    # "Alex" / "Scout" are short, common tokens — must NOT be auto-rewritten
    # (would corrupt prose), but must be reported so a human genericizes them.
    lines = ["Alex asked Scout to do the thing."]
    out, risky = retemplatize(lines, VARS)
    assert out == ["Alex asked Scout to do the thing."]  # unchanged
    assert set(risky) == {"USER_NAME", "INSTANCE_NAME"}


def test_retemplatize_clean_line_has_no_risky_hits():
    lines = ["A generic rule with no instance-specific tokens."]
    out, risky = retemplatize(lines, VARS)
    assert out == lines
    assert risky == []


# ---------- diff_hunks ----------


def test_diff_hunks_finds_added_block_with_anchor():
    snapshot = "## Heading\n\nfirst line\nsecond line\n"
    live = "## Heading\n\nfirst line\nINSERTED LINE\nsecond line\n"
    hunks = diff_hunks(snapshot, live)
    assert len(hunks) == 1
    assert hunks[0].added == ["INSERTED LINE"]
    assert hunks[0].anchor == "first line"


def test_diff_hunks_empty_when_identical():
    text = "## Heading\n\nbody\n"
    assert diff_hunks(text, text) == []


# ---------- plan_backport (locate + retemplatize + round-trip) ----------


def _section(raw_body: str) -> RenderedSection:
    from scout.scripts.phase_assembly import render_template

    return RenderedSection(
        phase_file=Path("phases/modes/kb-deep-work.md"),
        section_name="Step 2-pre",
        raw_body=raw_body,
        rendered_body=render_template(raw_body, VARS),
    )


def test_plan_backport_applies_clean_hunk():
    raw = "### Step 2-pre\n\nScan markers under {{SCOUT_DIR}}.\nThen score files."
    sec = _section(raw)
    snapshot = sec.rendered_body
    # insert a generic rule line after the anchor "Scan markers..." (rendered)
    live = sec.rendered_body.replace(
        "Scan markers under /home/alex/vault.",
        "Scan markers under /home/alex/vault.\nNew generic guard rule.",
    )
    results = plan_backport(snapshot, live, [sec], VARS)
    assert len(results) == 1
    r = results[0]
    assert r.status == "applied"
    assert r.section_name == "Step 2-pre"
    assert r.retemplatized == ["New generic guard rule."]


def test_plan_backport_needs_review_when_risky_value_present():
    # An added line carrying "Alex" can't be safely auto-written into a public
    # template (would leak instance data) -> needs-review, never applied.
    raw = "### Step 2-pre\n\nScan markers under {{SCOUT_DIR}}.\nThen score files."
    sec = _section(raw)
    snapshot = sec.rendered_body
    live = sec.rendered_body.replace(
        "Scan markers under /home/alex/vault.",
        "Scan markers under /home/alex/vault.\nRule that mentions Alex by name.",
    )
    results = plan_backport(snapshot, live, [sec], VARS)
    assert results[0].status == "needs-review"
    assert "USER_NAME" in results[0].risky_hits


def test_plan_backport_unmapped_when_anchor_in_no_section():
    raw = "### Step 2-pre\n\nbody line one\nbody line two"
    sec = _section(raw)
    snapshot = sec.rendered_body + "\n\n## Vault-only hard rule\n\norphan line\n"
    live = snapshot.replace("orphan line", "orphan line\nadded under vault-only content")
    results = plan_backport(snapshot, live, [sec], VARS)
    assert results[0].status == "unmapped"


def test_plan_backport_needs_review_when_anchor_ambiguous():
    raw1 = "### A\n\nshared anchor line\nunique a"
    raw2 = "### B\n\nshared anchor line\nunique b"
    s1, s2 = _section(raw1), _section(raw2)
    snapshot = s1.rendered_body + "\n\n" + s2.rendered_body
    # Insert after the line shared by both sections, forcing an ambiguous anchor:
    live2 = snapshot.replace(
        "shared anchor line\nunique a",
        "shared anchor line\nAMBIG ADD\nunique a",
        1,
    )
    results = plan_backport(snapshot, live2, [s1, s2], VARS)
    assert results[0].status == "needs-review"
    assert "ambiguous" in results[0].reason.lower()


# ---------- build_rendered_sections (assembly selection + provenance) ----------


def _write_phase(root: Path, rel: str, frontmatter: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n{body}\n", encoding="utf-8")
    return path


def test_build_rendered_sections_selects_renders_and_keeps_provenance(tmp_path):
    phases = tmp_path / "phases"
    _write_phase(
        phases,
        "core/a.md",
        "phase: core\nname: A\nslot: s\nmode: [briefing, consolidation]\nrequires: null",
        "Body for {{USER_NAME}}.",
    )
    _write_phase(
        phases,
        "connectors/slack.md",
        "phase: connector\nname: Slack\nslot: s\nmode: [briefing]\nrequires: slack",
        "Slack body.",
    )
    secs = build_rendered_sections(phases, "SKILL", VARS, {"slack"})
    by_name = {s.section_name: s for s in secs}
    assert set(by_name) == {"A", "Slack"}
    assert by_name["A"].rendered_body == "Body for Alex."
    assert by_name["A"].phase_file.name == "a.md"


def test_build_rendered_sections_excludes_disabled_connector(tmp_path):
    phases = tmp_path / "phases"
    _write_phase(phases, "core/a.md", "phase: core\nname: A\nslot: s\nmode: [briefing]\nrequires: null", "Core body.")
    _write_phase(
        phases,
        "connectors/slack.md",
        "phase: connector\nname: Slack\nslot: s\nmode: [briefing]\nrequires: slack",
        "Slack body.",
    )
    secs = build_rendered_sections(phases, "SKILL", VARS, set())  # slack NOT enabled
    assert {s.section_name for s in secs} == {"A"}


def test_build_rendered_sections_filters_by_mode(tmp_path):
    phases = tmp_path / "phases"
    # mode-less core section is cross-cutting (lands in every kind); a
    # briefing-only core section is excluded from DREAMING; a dreaming section
    # under modes/ only appears in DREAMING.
    _write_phase(phases, "core/shared.md", "phase: core\nname: Shared\nslot: s\nrequires: null", "x")
    _write_phase(phases, "core/brief.md", "phase: core\nname: Brief\nslot: s\nmode: [briefing]\nrequires: null", "b")
    _write_phase(phases, "modes/dream.md", "phase: mode\nname: Dream\nslot: s\nmode: [dreaming]\nrequires: null", "d")
    skill = build_rendered_sections(phases, "SKILL", VARS, set())
    assert {s.section_name for s in skill} == {"Shared", "Brief"}
    dreaming = build_rendered_sections(phases, "DREAMING", VARS, set())
    assert {s.section_name for s in dreaming} == {"Shared", "Dream"}


# ---------- apply_section_edits (multiple inserts in one section) ----------


def test_apply_section_edits_applies_multiple_inserts():
    raw = "h1\nbody a\nh2\nbody b"
    edits = [("h1", ["after one"]), ("h2", ["after two"])]
    out = apply_section_edits(raw, raw, edits)  # raw==rendered (no vars)
    assert out == "h1\nafter one\nbody a\nh2\nafter two\nbody b"


# ---------- apply_to_phase_text (write-back) ----------


def test_apply_to_phase_text_replaces_body_preserving_frontmatter():
    file_text = "---\nphase: core\nname: A\n---\nline one\nline two\n"
    new = apply_to_phase_text(file_text, "line one\nline two", "line one\nNEW\nline two")
    assert new == "---\nphase: core\nname: A\n---\nline one\nNEW\nline two\n"


def test_apply_to_phase_text_raises_when_body_absent():
    with pytest.raises(ValueError):
        apply_to_phase_text("---\nx\n---\nbody\n", "not present", "new")


# ---------- end-to-end round-trip ----------


def test_end_to_end_backport_roundtrip(tmp_path):
    phases = tmp_path / "phases"
    pf = _write_phase(
        phases,
        "core/kb.md",
        "phase: core\nname: KB\nslot: s\nmode: [briefing]\nrequires: null",
        "### Step\n\nScan under {{SCOUT_DIR}}.\nScore files.",
    )
    secs = build_rendered_sections(phases, "SKILL", VARS, set())
    assembled = "\n\n".join(s.rendered_body for s in secs)
    snapshot = assembled
    live = assembled.replace(
        "Scan under /home/alex/vault.",
        "Scan under /home/alex/vault.\nNew guard rule.",
    )

    results = plan_backport(snapshot, live, secs, VARS)
    applied = [r for r in results if r.status == "applied"]
    assert len(applied) == 1

    # write the edit back into the phase file
    sec = next(s for s in secs if s.section_name == applied[0].section_name)
    edited_body = insert_after_anchor(
        sec.raw_body, sec.rendered_body, "Scan under /home/alex/vault.", applied[0].retemplatized
    )
    pf.write_text(apply_to_phase_text(pf.read_text(encoding="utf-8"), sec.raw_body, edited_body), encoding="utf-8")

    # re-assembling now reproduces the hand-edited vault file
    reassembled = "\n\n".join(s.rendered_body for s in build_rendered_sections(phases, "SKILL", VARS, set()))
    assert reassembled == live
