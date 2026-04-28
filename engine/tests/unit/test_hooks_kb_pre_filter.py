"""Unit tests for scout.hooks.kb_pre_filter.

Mirrors the bash original at ~/Scout/hooks/kb-pre-filter.sh. Behavior is the
contract: discover knowledge-base/*.md, classify each as STALE / NO_DATE / FRESH
based on a per-file freshness budget, write a markdown summary to
$SCOUT_DATA_DIR/.scout-cache/kb-filter.md.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scout.events import Event
from scout.hooks.kb_pre_filter import (
    classify,
    discover_kb_files,
    extract_date_string,
    freshness_hours_for,
    main,
    parse_date,
    run,
)

# -- freshness_hours_for -----------------------------------------------------


def test_freshness_hours_special_files(tmp_path):
    assert freshness_hours_for(tmp_path / "linear-issues.md") == 6
    assert freshness_hours_for(tmp_path / "knowledge-base.md") == 6
    assert freshness_hours_for(tmp_path / "people.md") == 168
    assert freshness_hours_for(tmp_path / "channels.md") == 336
    assert freshness_hours_for(tmp_path / "ai-costs.md") == 168
    assert freshness_hours_for(tmp_path / "ai-landscape.md") == 168


def test_freshness_hours_priority_red(tmp_path):
    f = tmp_path / "proj.md"
    f.write_text("---\npriority: 🔴 Urgent\n---\n# Project\n")
    assert freshness_hours_for(f) == 72


def test_freshness_hours_priority_yellow(tmp_path):
    f = tmp_path / "proj.md"
    f.write_text("---\npriority: 🟡\n---\n# Project\n")
    assert freshness_hours_for(f) == 168


def test_freshness_hours_priority_green(tmp_path):
    f = tmp_path / "proj.md"
    f.write_text("---\npriority: 🟢 Watching\n---\n# Project\n")
    assert freshness_hours_for(f) == 336


def test_freshness_hours_default_no_priority(tmp_path):
    f = tmp_path / "proj.md"
    f.write_text("# Project\n\nNo priority frontmatter here.\n")
    assert freshness_hours_for(f) == 168


def test_freshness_hours_priority_only_in_first_25_lines(tmp_path):
    """Bash uses head -25 to find priority — anything beyond is ignored."""
    f = tmp_path / "proj.md"
    body = ["# Project", ""] + ["filler line"] * 30 + ["priority: 🔴"]
    f.write_text("\n".join(body) + "\n")
    # Should fall back to default since 🔴 is past line 25
    assert freshness_hours_for(f) == 168


# -- extract_date_string ------------------------------------------------------


def test_extract_date_string_strips_bold_markers(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("# Foo\n\n**Last Updated:** April 22, 2026 12:34 PM\n")
    assert extract_date_string(f) == "April 22, 2026 12:34 PM"


def test_extract_date_string_strips_label_prefix(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("Last Verified: 2026-04-15\n")
    assert extract_date_string(f) == "2026-04-15"


def test_extract_date_string_strips_source_suffix(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("**Last Updated:** April 22, 2026. Source: Slack DM with @kuba\n")
    assert extract_date_string(f) == "April 22, 2026"


def test_extract_date_string_strips_verified_suffix(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("Last Updated: April 22, 2026. Verified by jb\n")
    assert extract_date_string(f) == "April 22, 2026"


def test_extract_date_string_strips_parenthetical(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("**Last Updated:** April 22, 2026 12:34 PM (auto-snooze)\n")
    assert extract_date_string(f) == "April 22, 2026 12:34 PM"


def test_extract_date_string_case_insensitive(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("LAST UPDATED: 2026-04-15\n")
    assert extract_date_string(f) == "2026-04-15"


def test_extract_date_string_only_first_25_lines(tmp_path):
    """Bash only scans head -25 lines for the date marker."""
    f = tmp_path / "x.md"
    body = ["# Title"] + ["filler"] * 30 + ["Last Updated: 2026-04-15"]
    f.write_text("\n".join(body) + "\n")
    assert extract_date_string(f) == ""


def test_extract_date_string_returns_empty_on_missing(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("# Foo\n\nNo date marker here.\n")
    assert extract_date_string(f) == ""


def test_extract_date_string_first_match_only(tmp_path):
    """Multiple Last Updated lines — bash takes the first."""
    f = tmp_path / "x.md"
    f.write_text("# Title\n\nLast Updated: 2026-04-15\nLast Updated: 2026-01-01\n")
    assert extract_date_string(f) == "2026-04-15"


# -- parse_date ---------------------------------------------------------------


def test_parse_date_full_with_12h_time():
    dt = parse_date("April 22, 2026 12:34 PM")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 4 and dt.day == 22
    assert dt.hour == 12 and dt.minute == 34


def test_parse_date_full_with_24h_time():
    dt = parse_date("April 22, 2026 14:30")
    assert dt is not None
    assert dt.hour == 14 and dt.minute == 30


def test_parse_date_full_no_time():
    dt = parse_date("April 22, 2026")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 4 and dt.day == 22


def test_parse_date_iso_with_time():
    dt = parse_date("2026-04-15 09:00")
    assert dt is not None
    assert dt.hour == 9 and dt.minute == 0


def test_parse_date_iso_no_time():
    dt = parse_date("2026-04-15")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 4 and dt.day == 15


def test_parse_date_unparseable_returns_none():
    assert parse_date("not a date") is None
    assert parse_date("") is None
    assert parse_date("yesterday") is None


# -- discover_kb_files --------------------------------------------------------


def _make_kb(root: Path) -> Path:
    """Build a synthetic knowledge-base/ tree under root and return its path."""
    kb = root / "knowledge-base"
    kb.mkdir(parents=True)
    return kb


def test_discover_excludes_ontology(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "knowledge-base.md").write_text("x")
    (kb / "ontology").mkdir()
    (kb / "ontology" / "schema.md").write_text("x")
    (kb / "ontology" / "entities").mkdir()
    (kb / "ontology" / "entities" / "foo.md").write_text("x")

    files = discover_kb_files(tmp_path)
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in files)
    assert "knowledge-base/knowledge-base.md" in rels
    assert not any("ontology" in r for r in rels)


def test_discover_excludes_archive_paths(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "knowledge-base.md").write_text("x")
    (kb / "projects").mkdir()
    (kb / "projects" / "active.md").write_text("x")
    (kb / "projects" / "archived").mkdir()
    (kb / "projects" / "archived" / "old-thing.md").write_text("x")
    (kb / "archive-thoughts.md").write_text("x")

    files = discover_kb_files(tmp_path)
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in files)
    assert "knowledge-base/projects/active.md" in rels
    assert not any("archived" in r for r in rels)
    assert not any("archive" in r for r in rels)


def test_discover_excludes_personal(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "knowledge-base.md").write_text("x")
    (kb / "personal").mkdir()
    (kb / "personal" / "jordan.md").write_text("x")

    files = discover_kb_files(tmp_path)
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in files)
    assert "knowledge-base/knowledge-base.md" in rels
    assert not any("personal" in r for r in rels)


def test_discover_skips_review_queue_and_archived_basenames(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "knowledge-base.md").write_text("x")
    (kb / "review-queue.md").write_text("x")
    (kb / "archived.md").write_text("x")

    files = discover_kb_files(tmp_path)
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in files)
    assert "knowledge-base/review-queue.md" not in rels
    assert "knowledge-base/archived.md" not in rels
    assert "knowledge-base/knowledge-base.md" in rels


def test_discover_skips_archive_draft_prompt_glob_basenames(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "knowledge-base.md").write_text("x")
    (kb / "pre-archive-notes.md").write_text("x")
    (kb / "scout-draft-foo.md").write_text("x")
    (kb / "system-prompt-v2.md").write_text("x")

    files = discover_kb_files(tmp_path)
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in files)
    # The find-level *archive* exclusion already catches pre-archive-notes.md.
    # Per-file basename glob also catches *-draft* and *-prompt*.
    assert "knowledge-base/pre-archive-notes.md" not in rels
    assert "knowledge-base/scout-draft-foo.md" not in rels
    assert "knowledge-base/system-prompt-v2.md" not in rels


def test_discover_skips_people_subdir(tmp_path):
    """Per-file rel-path skip for */people/*.md (entity files)."""
    kb = _make_kb(tmp_path)
    (kb / "knowledge-base.md").write_text("x")
    (kb / "people.md").write_text("x")  # top-level people.md is fine
    (kb / "people").mkdir()
    (kb / "people" / "andrea.md").write_text("x")

    files = discover_kb_files(tmp_path)
    rels = sorted(p.relative_to(tmp_path).as_posix() for p in files)
    assert "knowledge-base/people.md" in rels  # top-level kept
    assert "knowledge-base/people/andrea.md" not in rels


def test_discover_returns_sorted_files(tmp_path):
    kb = _make_kb(tmp_path)
    (kb / "z-last.md").write_text("x")
    (kb / "a-first.md").write_text("x")
    (kb / "m-middle.md").write_text("x")

    files = discover_kb_files(tmp_path)
    rels = [p.relative_to(tmp_path).as_posix() for p in files]
    assert rels == sorted(rels)


# -- classify -----------------------------------------------------------------


def test_classify_stale_when_age_exceeds_budget(tmp_path):
    kb = _make_kb(tmp_path)
    f = kb / "linear-issues.md"  # budget: 6h
    f.write_text("# Linear\n\nLast Updated: 2026-04-20 12:00\n")
    now = datetime(2026, 4, 28, 12, 0)  # 8 days = 192h > 6h
    label, details = classify(f, now, tmp_path)
    assert label == "STALE"
    assert details["age_hours"] >= 6
    assert details["budget_hours"] == 6


def test_classify_fresh_when_age_within_budget(tmp_path):
    kb = _make_kb(tmp_path)
    f = kb / "people.md"  # budget: 168h
    f.write_text("# People\n\nLast Updated: 2026-04-26 12:00\n")
    now = datetime(2026, 4, 28, 12, 0)  # 48h < 168h
    label, details = classify(f, now, tmp_path)
    assert label == "FRESH"
    assert details["age_hours"] == 48


def test_classify_no_date_when_missing_marker(tmp_path):
    kb = _make_kb(tmp_path)
    f = kb / "proj.md"
    f.write_text("# Project\n\nNo Last Updated marker here.\n")
    now = datetime(2026, 4, 28, 12, 0)
    label, _ = classify(f, now, tmp_path)
    assert label == "NO_DATE"


def test_classify_no_date_when_unparseable(tmp_path):
    kb = _make_kb(tmp_path)
    f = kb / "proj.md"
    f.write_text("# Project\n\nLast Updated: never\n")
    now = datetime(2026, 4, 28, 12, 0)
    label, _ = classify(f, now, tmp_path)
    assert label == "NO_DATE"


def test_classify_age_is_dst_correct_across_spring_forward(tmp_path):
    """Bash uses UTC-elapsed seconds via `date -j -f ... +%s` — Python must too.

    Concrete scenario: file dated Jan 1, 2026 12:00 PM (EST, UTC-5),
    "now" = Apr 28, 2026 12:00 PM (EDT, UTC-4). Spring-forward in March 2026
    makes this case sensitive:

      - Wall-clock delta: 117 days × 24h = 2808h  (NAIVE Python — wrong)
      - UTC-elapsed delta: 117*24 - 1 = 2807h     (BASH — correct)

    Same-zone tz-aware subtraction in Python *also* returns wall-clock delta
    (Python docs: "If both are aware and have different tzinfo attributes,
    a-b acts as if a and b were first converted to naive UTC datetimes" —
    so SAME tzinfo means no conversion). The fix must convert via
    `.timestamp()` (or via UTC) to match bash.
    """
    et = ZoneInfo("America/New_York")
    f = tmp_path / "thing.md"
    f.write_text("---\npriority: 🔴 Urgent\n---\n**Last Updated:** January 1, 2026 12:00 PM\n")
    now = datetime(2026, 4, 28, 12, 0, tzinfo=et)
    label, details = classify(f, now=now, scout_dir=tmp_path)
    # Bash gives 2807h for this case (verified with `date -j -f`).
    # Naive Python or same-zone aware subtraction would give 2808h.
    assert details["age_hours"] == 2807, (
        f"Expected 2807h (UTC-elapsed, matches bash) but got {details['age_hours']}h. "
        f"2808h indicates wall-clock subtraction (DST drift bug)."
    )
    # 2807h > 72h budget for a 🔴 file → STALE
    assert label == "STALE"


def test_classify_age_naive_now_treated_as_et(tmp_path):
    """When `now` is passed in naive, classify() should interpret it as ET
    (matching the behavior bash gets implicitly via system TZ)."""
    f = tmp_path / "thing.md"
    f.write_text("---\npriority: 🔴 Urgent\n---\n**Last Updated:** January 1, 2026 12:00 PM\n")
    now_naive = datetime(2026, 4, 28, 12, 0)  # naive
    _, details = classify(f, now=now_naive, scout_dir=tmp_path)
    # Same expectation as DST test: 2807h via UTC-epoch arithmetic.
    assert details["age_hours"] == 2807


# -- run ----------------------------------------------------------------------


def test_run_writes_output_with_all_three_sections(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    kb = _make_kb(tmp_path)

    # STALE: linear-issues with 6h budget, 8d old
    (kb / "linear-issues.md").write_text("# Linear\n\nLast Updated: 2026-04-20 12:00\n")
    # FRESH: people.md with 168h budget, 1d old
    (kb / "people.md").write_text("# People\n\nLast Updated: 2026-04-27 12:00\n")
    # NO_DATE: project with no marker
    (kb / "projects").mkdir()
    (kb / "projects" / "noisy.md").write_text("# Noisy\n\nNo date here.\n")

    now = datetime(2026, 4, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    event = run(session_type="dreaming", now=now)

    assert isinstance(event, Event)
    assert event.kind == "kb_pre_filter.scored"
    assert event.source == "hook:kb-pre-filter"
    assert event.payload["session_type"] == "dreaming"
    assert event.payload["stale"] == 1
    assert event.payload["fresh"] == 1
    assert event.payload["no_date"] == 1

    out_path = tmp_path / ".scout-cache" / "kb-filter.md"
    assert out_path.is_file()
    content = out_path.read_text()
    assert "# KB Pre-Filter" in content
    assert "(dreaming)" in content
    assert "## STALE — Need reading/audit" in content
    assert "## NO DATE — Need checking" in content
    assert "## FRESH — Skip unless feedback signals" in content
    assert "**knowledge-base/linear-issues.md**" in content
    assert "knowledge-base/people.md" in content
    assert "knowledge-base/projects/noisy.md" in content
    assert "Stale: 1 | No date: 1 | Fresh: 1" in content


def test_run_omits_empty_stale_and_no_date_sections(tmp_path, monkeypatch):
    """Header sections for stale and no-date should be omitted when those
    lists are empty (mirrors bash if [ ${#STALE[@]} -gt 0 ] guard)."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    kb = _make_kb(tmp_path)
    # All FRESH
    (kb / "people.md").write_text("# People\n\nLast Updated: 2026-04-27 12:00\n")

    now = datetime(2026, 4, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    run(session_type="briefing", now=now)

    out_path = tmp_path / ".scout-cache" / "kb-filter.md"
    content = out_path.read_text()
    assert "## STALE" not in content
    assert "## NO DATE" not in content
    assert "## FRESH — Skip unless feedback signals" in content
    assert "Stale: 0 | No date: 0 | Fresh: 1" in content


def test_run_handles_empty_kb(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    _make_kb(tmp_path)  # empty

    now = datetime(2026, 4, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    event = run(session_type="dreaming", now=now)

    assert isinstance(event, Event)
    assert event.payload == {
        "stale": 0,
        "no_date": 0,
        "fresh": 0,
        "session_type": "dreaming",
        "output_path": str(tmp_path / ".scout-cache" / "kb-filter.md"),
    }
    out_path = tmp_path / ".scout-cache" / "kb-filter.md"
    assert out_path.is_file()
    assert "Stale: 0 | No date: 0 | Fresh: 0" in out_path.read_text()


def test_run_returns_none_when_kb_dir_missing(tmp_path, monkeypatch):
    """SCOUT_DATA_DIR exists but knowledge-base/ does not — run() returns None."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    # No knowledge-base/ created.
    now = datetime(2026, 4, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    result = run(session_type="dreaming", now=now)
    assert result is None


def test_run_default_session_type_is_dreaming(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    _make_kb(tmp_path)
    now = datetime(2026, 4, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    event = run(now=now)
    assert event is not None
    assert event.payload["session_type"] == "dreaming"


def test_run_stale_entry_includes_age_budget_and_lastdate(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    kb = _make_kb(tmp_path)
    (kb / "linear-issues.md").write_text("# Linear\n\n**Last Updated:** April 20, 2026 12:00 PM\n")
    now = datetime(2026, 4, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    run(now=now)
    content = (tmp_path / ".scout-cache" / "kb-filter.md").read_text()
    # Format: - **{rel}** — {age}h old (standard: {budget}h) — last: {datestr}
    assert "**knowledge-base/linear-issues.md**" in content
    assert "(standard: 6h)" in content
    assert "April 20, 2026 12:00 PM" in content


def test_run_fresh_entry_includes_age(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    kb = _make_kb(tmp_path)
    (kb / "people.md").write_text("# People\n\nLast Updated: 2026-04-27 12:00\n")
    now = datetime(2026, 4, 28, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    run(now=now)
    content = (tmp_path / ".scout-cache" / "kb-filter.md").read_text()
    # Format: - {rel} ({age}h old)
    assert "knowledge-base/people.md (24h old)" in content


# -- main / contract ----------------------------------------------------------


def test_main_returns_zero_on_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    _make_kb(tmp_path)
    rc = main(["briefing"])
    assert rc == 0


def test_main_returns_zero_when_kb_missing(tmp_path, monkeypatch):
    """No knowledge-base/ dir — main() must still return 0 (hooks never block)."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    rc = main(["dreaming"])
    assert rc == 0


def test_main_swallows_exceptions(tmp_path, monkeypatch):
    """Force a failure in run() and confirm main() returns 0."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))

    import scout.hooks.kb_pre_filter as m

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(m, "run", boom)
    rc = main(["dreaming"])
    assert rc == 0


def test_main_default_session_type(tmp_path, monkeypatch):
    """No CLI arg — defaults to 'dreaming'."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    _make_kb(tmp_path)
    rc = main([])
    assert rc == 0
    out_path = tmp_path / ".scout-cache" / "kb-filter.md"
    assert "(dreaming)" in out_path.read_text()
