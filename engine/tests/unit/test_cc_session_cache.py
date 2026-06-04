"""Unit tests for scout.scripts.cc_session_cache.

Closes #74 (folds the per-file python3 cold starts into one process) and #75
(adds the mtime-keyed cache so unchanged JSONLs are reused).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts.cc_session_cache import (
    CACHE_FILENAME,
    OUTPUT_FILENAME,
    SessionEntry,
    _project_path_from_dirname,
    build_session_entry,
    extract_files_touched,
    extract_first_message,
    iter_session_jsonls,
    render_markdown,
    run,
)

# ----- helpers ------------------------------------------------------------


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")


def _make_cc_project(
    cc_projects: Path,
    dirname: str,
    session_id: str,
    rows: list[dict],
    *,
    mtime_ago_hours: float = 1.0,
) -> Path:
    jsonl = cc_projects / dirname / f"{session_id}.jsonl"
    _write_jsonl(jsonl, rows)
    target_ts = (datetime.now(tz=UTC) - timedelta(hours=mtime_ago_hours)).timestamp()
    os.utime(jsonl, (target_ts, target_ts))
    return jsonl


# ----- name decoding ------------------------------------------------------


def test_project_path_from_dirname_round_trips_root_dash() -> None:
    assert _project_path_from_dirname("-Users-foo-bar-repo") == "/Users/foo/bar/repo"


def test_project_path_from_dirname_keeps_non_root_names_intact() -> None:
    assert _project_path_from_dirname("opaque-name") == "opaque-name"


# ----- discovery ---------------------------------------------------------


def test_iter_session_jsonls_excludes_scout_dirs(tmp_path: Path) -> None:
    cc = tmp_path / "projects"
    _make_cc_project(cc, "-Users-foo-Scout", "s1", [{"type": "user", "message": {"content": "hi"}}])
    _make_cc_project(cc, "-Users-foo-other", "s2", [{"type": "user", "message": {"content": "ok"}}])

    cutoff_ts = (datetime.now(tz=UTC) - timedelta(hours=24)).timestamp()
    found = [
        p.parent.name for p, _ in iter_session_jsonls(cc, cutoff_ts=cutoff_ts, exclude_suffixes=("-Scout", "-scout"))
    ]
    assert found == ["-Users-foo-other"]


def test_iter_session_jsonls_skips_stale_files(tmp_path: Path) -> None:
    cc = tmp_path / "projects"
    _make_cc_project(cc, "-Users-foo-bar", "fresh", [{"type": "user", "message": {"content": "hi"}}], mtime_ago_hours=1)
    _make_cc_project(
        cc, "-Users-foo-bar", "stale", [{"type": "user", "message": {"content": "old"}}], mtime_ago_hours=48
    )

    cutoff_ts = (datetime.now(tz=UTC) - timedelta(hours=24)).timestamp()
    ids = sorted(p.stem for p, _ in iter_session_jsonls(cc, cutoff_ts=cutoff_ts, exclude_suffixes=()))
    assert ids == ["fresh"]


# ----- first message extraction ------------------------------------------


def test_extract_first_message_handles_content_list(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(
        jsonl,
        [
            {"type": "assistant", "message": {"content": "irrelevant"}},
            {
                "type": "user",
                "message": {"content": [{"type": "text", "text": "build me a thing"}]},
            },
        ],
    )
    assert extract_first_message(jsonl) == "build me a thing"


def test_extract_first_message_handles_string_content(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [{"type": "user", "content": "hi there"}])
    assert extract_first_message(jsonl) == "hi there"


def test_extract_first_message_falls_back_when_no_match(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    _write_jsonl(jsonl, [{"type": "assistant", "message": {"content": "no users here"}}])
    assert "could not extract" in extract_first_message(jsonl)


def test_extract_first_message_handles_malformed_lines(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        "not json\n" + json.dumps({"type": "user", "message": {"content": "found it"}}) + "\n",
        encoding="utf-8",
    )
    assert extract_first_message(jsonl) == "found it"


# ----- files-touched extraction ------------------------------------------


def test_extract_files_touched_filters_noise_and_collapses_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    jsonl = tmp_path / "s.jsonl"
    contents = (
        '{"file_path":"/Users/me/.claude/projects/x/tool-results/y"}\n'
        '{"file_path":"/Users/me/.claude/plugins/cache/abc"}\n'
        '{"file_path":"/Users/me/repo/src/main.py"}\n'
        f'{{"file_path":"{home}/work/project/notes.md"}}\n'
        '{"file_path":"/private/tmp/claude-temp"}\n'
        '{"file_path":"/Users/me/node_modules/some/lib.js"}\n'
    )
    jsonl.write_text(contents, encoding="utf-8")
    touched = extract_files_touched(jsonl, home=home)
    # Two legitimate paths survive; the rest are noise-filtered.
    assert "/Users/me/repo/src/main.py" in touched
    assert "~/work/project/notes.md" in touched
    assert len(touched) == 2


def test_extract_files_touched_caps_at_ten(tmp_path: Path) -> None:
    jsonl = tmp_path / "s.jsonl"
    jsonl.write_text(
        "\n".join(f'{{"file_path":"/p/file-{i:02d}.md"}}' for i in range(20)) + "\n",
        encoding="utf-8",
    )
    assert len(extract_files_touched(jsonl)) == 10


# ----- caching ------------------------------------------------------------


def test_run_reuses_cached_entry_when_mtime_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "vault"))
    cc = tmp_path / "projects"
    jsonl = _make_cc_project(
        cc,
        "-Users-foo-bar",
        "abc",
        [{"type": "user", "message": {"content": "warm one"}}],
    )

    # First run — populates the cache.
    out1, count1 = run(cc_projects_dir=cc, instance_name="Scout")
    assert count1 == 1
    text1 = out1.read_text()
    assert "warm one" in text1

    # Corrupt the JSONL but keep its mtime constant. On a non-cached path
    # this would change the extracted first_msg; cached path returns the
    # original.
    original_mtime_ns = jsonl.stat().st_mtime_ns
    jsonl.write_bytes(b"garbage that would otherwise produce a parse error sentinel\n")
    os.utime(jsonl, ns=(original_mtime_ns, original_mtime_ns))

    out2, count2 = run(cc_projects_dir=cc, instance_name="Scout")
    assert count2 == 1
    text2 = out2.read_text()
    assert "warm one" in text2, "cache should serve original first_msg even after corruption"


def test_run_re_extracts_when_mtime_bumps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "vault"))
    cc = tmp_path / "projects"
    jsonl = _make_cc_project(
        cc,
        "-Users-foo-bar",
        "abc",
        [{"type": "user", "message": {"content": "first version"}}],
    )

    run(cc_projects_dir=cc, instance_name="Scout")

    # Rewrite with new content AND a fresher mtime.
    _write_jsonl(jsonl, [{"type": "user", "message": {"content": "second version"}}])
    fresh_ts = datetime.now(tz=UTC).timestamp()
    os.utime(jsonl, (fresh_ts, fresh_ts))

    out, _ = run(cc_projects_dir=cc, instance_name="Scout")
    text = out.read_text()
    assert "second version" in text


def test_run_writes_cache_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "vault"))
    cc = tmp_path / "projects"
    _make_cc_project(
        cc,
        "-Users-foo-bar",
        "abc",
        [{"type": "user", "message": {"content": "x"}}],
    )

    run(cc_projects_dir=cc, instance_name="Scout")
    cache_path = tmp_path / "vault" / ".scout-cache" / CACHE_FILENAME
    assert cache_path.exists()
    cached = json.loads(cache_path.read_text())
    assert any(entry["session_id"] == "abc" for entry in cached.values())


# ----- markdown rendering ------------------------------------------------


def test_render_markdown_lists_sessions_with_header(tmp_path: Path) -> None:
    entry = SessionEntry(
        jsonl_path="/p/abc.jsonl",
        project_path="/repo",
        session_id="abc",
        mtime_ns=1_700_000_000_000_000_000,
        size_bytes=4096,
        first_msg="do the thing",
        files_touched=["~/repo/src/main.py"],
    )
    from zoneinfo import ZoneInfo

    out = render_markdown(
        [entry],
        hours=24,
        instance_name="Scout",
        now_local_str="2026-05-28 12:00 EDT",
        tz=ZoneInfo("America/New_York"),
    )
    assert "# Claude Code Sessions — Last 24h" in out
    assert "do the thing" in out
    assert "~/repo/src/main.py" in out
    assert "**Total:** 1 session(s) found." in out


def test_render_markdown_empty_state_message(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    out = render_markdown(
        [],
        hours=12,
        instance_name="Scout",
        now_local_str="2026-05-28 12:00 EDT",
        tz=ZoneInfo("America/New_York"),
    )
    assert "*No non-Scout CC sessions found in the last 12 hours.*" in out


# ----- build_session_entry full path ------------------------------------


def test_build_session_entry_populates_all_fields(tmp_path: Path) -> None:
    cc = tmp_path / "projects"
    jsonl = _make_cc_project(
        cc,
        "-Users-foo-bar-repo",
        "session-42",
        [
            {"type": "user", "message": {"content": "kick it off"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}},
            {"file_path": "/Users/foo/bar/repo/src/main.py"},
        ],
    )
    entry = build_session_entry(jsonl, jsonl.stat())
    assert entry.project_path == "/Users/foo/bar/repo"
    assert entry.session_id == "session-42"
    assert entry.first_msg == "kick it off"
    assert "/Users/foo/bar/repo/src/main.py" in entry.files_touched
    assert entry.size_bytes > 0


# ----- end-to-end output path ------------------------------------------


def test_run_writes_output_at_known_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "vault"))
    cc = tmp_path / "projects"
    out_path, count = run(cc_projects_dir=cc, instance_name="Scout")
    assert out_path == tmp_path / "vault" / ".scout-cache" / OUTPUT_FILENAME
    assert count == 0
    assert out_path.exists()
    assert "0 session(s) found" in out_path.read_text()
