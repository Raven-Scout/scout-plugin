"""Unit tests for scout.scripts.pre_session_data.

Closes #74 (the pre-session-data slice) and #76 (kb_pre_filter double-open
fix gets the mtime-cached extraction it needed).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scout.scripts.pre_session_data import (
    KB_DATES_CACHE_FILENAME,
    SessionContext,
    extract_last_updated,
    gather,
    gather_kb_file_dates,
    write_context,
)

# ----- last-updated extraction --------------------------------------------


def test_extract_last_updated_with_colon(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# Title\n**Last updated:** May 27, 2026 2:30 PM ET\nbody\n")
    assert extract_last_updated(md) == "May 27, 2026 2:30 PM ET"


def test_extract_last_updated_case_insensitive(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# Title\nLast VERIFIED: 2026-05-25\n")
    assert extract_last_updated(md) == "2026-05-25"


def test_extract_last_updated_returns_empty_when_absent(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# Title\nno date line here\n")
    assert extract_last_updated(md) == ""


def test_extract_last_updated_only_scans_first_five_lines(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("line1\nline2\nline3\nline4\nline5\nLast updated: should be ignored\n")
    assert extract_last_updated(md) == ""


# ----- KB walking + cache --------------------------------------------------


def test_gather_kb_file_dates_walks_and_caches(tmp_path: Path) -> None:
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    kb.mkdir(parents=True)
    (kb / "foo.md").write_text("# foo\nLast updated: May 1, 2026\n")
    (kb / "bar.md").write_text("# bar\nNo date here\n")

    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME
    out = gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)

    assert "knowledge-base/foo.md" in out
    assert out["knowledge-base/foo.md"] == "May 1, 2026"
    # bar.md has no date — it's still cached (negative entry) but absent from
    # the returned dict to match bash's "only emit non-empty" behavior.
    assert "knowledge-base/bar.md" not in out
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text())
    assert "knowledge-base/bar.md" in cached  # negative cache entry stored


def test_gather_kb_file_dates_reuses_cache_when_mtime_unchanged(tmp_path: Path) -> None:
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    kb.mkdir(parents=True)
    md = kb / "foo.md"
    md.write_text("# foo\nLast updated: May 1, 2026\n")

    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME
    gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)

    # Corrupt the file but preserve mtime — cache should still serve original.
    original_mtime_ns = md.stat().st_mtime_ns
    md.write_text("# foo\nLast updated: TOTALLY DIFFERENT\n")
    os.utime(md, ns=(original_mtime_ns, original_mtime_ns))

    out = gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)
    assert out["knowledge-base/foo.md"] == "May 1, 2026"


def test_gather_kb_file_dates_re_extracts_on_mtime_bump(tmp_path: Path) -> None:
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    kb.mkdir(parents=True)
    md = kb / "foo.md"
    md.write_text("# foo\nLast updated: May 1, 2026\n")
    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME
    gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)

    md.write_text("# foo\nLast updated: May 28, 2026\n")
    out = gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)
    assert out["knowledge-base/foo.md"] == "May 28, 2026"


def test_gather_kb_file_dates_excludes_ontology_and_archive(tmp_path: Path) -> None:
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    (kb / "ontology").mkdir(parents=True)
    (kb / "archive").mkdir(parents=True)
    (kb / "ontology" / "schema.md").write_text("Last updated: May 1, 2026\n")
    (kb / "archive" / "old.md").write_text("Last updated: May 1, 2026\n")
    (kb / "current.md").write_text("Last updated: May 1, 2026\n")
    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME
    out = gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)
    assert set(out) == {"knowledge-base/current.md"}


def test_gather_kb_file_dates_missing_root(tmp_path: Path) -> None:
    out = gather_kb_file_dates(
        tmp_path / "nope",
        scout_dir=tmp_path,
        cache_path=tmp_path / "cache.json",
    )
    assert out == {}


def test_gather_kb_file_dates_opens_each_file_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: bash version effectively opened each file 4-5 times
    (head + grep + head + sed + sed). The cached-Python path opens at most once,
    and zero times when cache hits."""
    scout_dir = tmp_path / "vault"
    kb = scout_dir / "knowledge-base"
    kb.mkdir(parents=True)
    md = kb / "foo.md"
    md.write_text("# foo\nLast updated: May 1, 2026\n")
    cache_path = tmp_path / ".scout-cache" / KB_DATES_CACHE_FILENAME

    opens = {"count": 0}
    original_open = Path.open

    def counting_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == md:
            opens["count"] += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    gather_kb_file_dates(kb, scout_dir=scout_dir, cache_path=cache_path)
    assert opens["count"] == 1


# ----- gh / git helpers --------------------------------------------------


def test_gather_returns_empty_when_no_git_no_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)

    monkeypatch.setenv("SCOUT_DATA_DIR", str(scout_dir))
    monkeypatch.setattr(psd, "get_pr_authored", lambda: [])
    monkeypatch.setattr(psd, "get_pr_review_requested", lambda: [])

    ctx = gather("briefing", scout_dir=scout_dir)
    assert ctx.session_type == "briefing"
    assert ctx.git_recent == ""
    assert ctx.pr_authored == []
    assert ctx.pr_review_requested == []
    assert ctx.personal_tasks == ""


def test_gather_calls_gh_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts import pre_session_data as psd

    scout_dir = tmp_path / "vault"
    (scout_dir / "knowledge-base").mkdir(parents=True)
    monkeypatch.setattr(psd, "get_pr_authored", lambda: [{"number": 42, "title": "x"}])
    monkeypatch.setattr(psd, "get_pr_review_requested", lambda: [{"number": 43, "title": "y"}])

    ctx = gather("research", scout_dir=scout_dir)
    assert ctx.pr_authored == [{"number": 42, "title": "x"}]
    assert ctx.pr_review_requested == [{"number": 43, "title": "y"}]


# ----- write_context ----------------------------------------------------


def test_write_context_round_trip(tmp_path: Path) -> None:
    ctx = SessionContext(
        generated_at="2026-05-28T12:00:00",
        session_type="briefing",
        git_recent="abc 123\n",
        kb_file_dates={"knowledge-base/foo.md": "May 1"},
        pr_authored=[{"number": 1}],
        pr_review_requested=[{"number": 2}],
        personal_tasks="task list",
    )
    out = tmp_path / "session-context.json"
    write_context(ctx, out)
    parsed = json.loads(out.read_text())
    assert parsed["session_type"] == "briefing"
    assert parsed["pr_authored"] == [{"number": 1}]
    assert parsed["kb_file_dates"]["knowledge-base/foo.md"] == "May 1"
