"""Tolerance branches in the CC-session cache.

`test_cc_session_cache.py` covers discovery, the mtime cache and the markdown
render. What's left is every path that must degrade quietly: a session JSONL
that vanishes or can't be read mid-walk, a corrupt cache file, a failed atomic
write, the "no user message found" sentinels, and `main()`'s
never-break-the-preamble contract.

This module runs in the preamble of every scheduled session over
`~/.claude/projects`, a directory another process is actively writing — so
"tolerant" is the whole design, and an untested tolerance branch is one that
first executes at 03:00.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scout.scripts import cc_session_cache as ccc
from scout.scripts.cc_session_cache import SessionEntry


@pytest.fixture
def projects(tmp_path: Path) -> Path:
    d = tmp_path / "projects"
    d.mkdir()
    return d


def _session(projects: Path, project_dir: str, session_id: str, *rows: dict) -> Path:
    d = projects / project_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def _entry(path: str = "/p/s.jsonl", **overrides) -> SessionEntry:
    base = {
        "jsonl_path": path,
        "project_path": "/Users/alex/work",
        "session_id": "s",
        "mtime_ns": 1,
        "size_bytes": 2,
        "first_msg": "hello",
        "files_touched": ["~/work/a.py"],
    }
    base.update(overrides)
    return SessionEntry(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# iter_session_jsonls
# ---------------------------------------------------------------------------


def test_discovery_yields_nothing_when_the_projects_dir_is_absent(tmp_path: Path) -> None:
    assert list(ccc.iter_session_jsonls(tmp_path / "nope", cutoff_ts=0, exclude_suffixes=())) == []


def test_discovery_skips_loose_files_in_the_projects_dir(projects: Path) -> None:
    (projects / "stray.jsonl").write_text("{}\n", encoding="utf-8")
    _session(projects, "-Users-alex-work", "s1", {"type": "user", "content": "hi"})
    found = [p.name for p, _st in ccc.iter_session_jsonls(projects, cutoff_ts=0, exclude_suffixes=())]
    assert found == ["s1.jsonl"]


def test_discovery_skips_a_file_it_cannot_stat(projects: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Code rotates these files while we walk; a vanished one must be
    skipped, not abort the whole scan."""
    gone = _session(projects, "-Users-alex-work", "gone", {"type": "user", "content": "hi"})
    _session(projects, "-Users-alex-work", "kept", {"type": "user", "content": "hi"})

    real_stat = Path.stat

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == gone:
            raise OSError("vanished mid-walk")
        return real_stat(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", maybe_boom)
    found = [p.stem for p, _st in ccc.iter_session_jsonls(projects, cutoff_ts=0, exclude_suffixes=())]
    assert found == ["kept"]


def test_discovery_excludes_scouts_own_project_dirs(projects: Path) -> None:
    _session(projects, "-Users-alex-Scout", "own", {"type": "user", "content": "hi"})
    _session(projects, "-Users-alex-work", "other", {"type": "user", "content": "hi"})
    found = [
        p.stem for p, _st in ccc.iter_session_jsonls(projects, cutoff_ts=0, exclude_suffixes=ccc._excluded_suffixes())
    ]
    assert found == ["other"]


def test_discovery_honours_the_cutoff(projects: Path) -> None:
    old = _session(projects, "-Users-alex-work", "old", {"type": "user", "content": "hi"})
    os.utime(old, (0, 0))
    _session(projects, "-Users-alex-work", "new", {"type": "user", "content": "hi"})

    found = [p.stem for p, _st in ccc.iter_session_jsonls(projects, cutoff_ts=1, exclude_suffixes=())]
    assert found == ["new"]


def test_project_path_decoding() -> None:
    assert ccc._project_path_from_dirname("-Users-alex-work") == "/Users/alex/work"
    # A name that isn't dash-encoded passes through untouched.
    assert ccc._project_path_from_dirname("plain-name") == "plain-name"


# ---------------------------------------------------------------------------
# extract_first_message
# ---------------------------------------------------------------------------


def test_first_message_reads_a_nested_text_block(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "review PROJ-1234"}]}}) + "\n",
        encoding="utf-8",
    )
    assert ccc.extract_first_message(p) == "review PROJ-1234"


def test_first_message_reads_a_top_level_string_content(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"role": "human", "content": "hello there"}) + "\n", encoding="utf-8")
    assert ccc.extract_first_message(p) == "hello there"


def test_first_message_skips_blank_malformed_and_non_user_rows(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        "\n".join(
            [
                "",
                "   ",
                "{torn",
                '"a bare string"',
                json.dumps({"type": "assistant", "content": "not the user"}),
                json.dumps({"type": "user", "content": "   "}),  # blank -> keep looking
                json.dumps({"type": "user", "message": {"content": [{"type": "image"}]}}),  # no text
                json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": ""}]}}),
                json.dumps({"type": "user", "content": "the real first message"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert ccc.extract_first_message(p) == "the real first message"


def test_first_message_only_scans_the_head_of_the_file(tmp_path: Path) -> None:
    """Bash used `head -50`; a session's first prompt is always near the top,
    and scanning a 100 MB transcript per file would blow the preamble budget."""
    p = tmp_path / "s.jsonl"
    filler = [json.dumps({"type": "assistant", "content": "x"})] * 60
    p.write_text("\n".join([*filler, json.dumps({"type": "user", "content": "too late"})]) + "\n", encoding="utf-8")
    assert ccc.extract_first_message(p) == "(could not extract first message)"


def test_first_message_is_truncated(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "user", "content": "x" * 900}) + "\n", encoding="utf-8")
    assert len(ccc.extract_first_message(p)) == ccc._FIRST_MSG_MAX_CHARS


def test_first_message_reports_a_parse_error_for_an_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "user", "content": "hi"}) + "\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert ccc.extract_first_message(p) == "(parse error)"


def test_first_message_sentinel_for_a_session_with_no_user_row(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "assistant", "content": "hello"}) + "\n", encoding="utf-8")
    assert ccc.extract_first_message(p) == "(could not extract first message)"


# ---------------------------------------------------------------------------
# extract_files_touched
# ---------------------------------------------------------------------------


def test_files_touched_collapses_home_and_dedupes(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps({"tool_input": {"file_path": "/Users/alex/work/a.py"}}),
                json.dumps({"tool_input": {"file_path": "/Users/alex/work/a.py"}}),
                json.dumps({"tool_input": {"file_path": "/etc/hosts"}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert ccc.extract_files_touched(p, home=Path("/Users/alex")) == ["/etc/hosts", "~/work/a.py"]


@pytest.mark.parametrize(
    "noisy",
    [
        "/Users/alex/.claude/projects/-x/tool-results/r.json",
        "/Users/alex/.claude/projects/-x/tasks/t.json",
        "/Users/alex/.claude/plugins/cache/p.js",
        "/Users/alex/work/node_modules/dep/index.js",
        "/private/tmp/claude-501/scratch.txt",
        "/Users/alex/.claude/projects/-x/memory/m.md",
    ],
)
def test_files_touched_drops_agent_internal_noise(tmp_path: Path, noisy: str) -> None:
    """These are the agent's own scratch files; surfacing them buries the
    user-meaningful edits the briefing is supposed to notice."""
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"tool_input": {"file_path": noisy}}) + "\n", encoding="utf-8")
    assert ccc.extract_files_touched(p, home=Path("/Users/alex")) == []


def test_files_touched_is_capped(tmp_path: Path) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(
        "\n".join(json.dumps({"tool_input": {"file_path": f"/w/f{n:03d}.py"}}) for n in range(25)) + "\n",
        encoding="utf-8",
    )
    assert len(ccc.extract_files_touched(p, home=Path("/Users/alex"))) == ccc._MAX_FILES_TOUCHED


def test_files_touched_is_empty_for_an_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"tool_input": {"file_path": "/w/a.py"}}) + "\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert ccc.extract_files_touched(p) == []


# ---------------------------------------------------------------------------
# cache load / write
# ---------------------------------------------------------------------------


def test_cache_load_is_empty_when_absent(tmp_path: Path) -> None:
    assert ccc._load_cache(tmp_path / "nope.json") == {}


@pytest.mark.parametrize("body", ["{torn", '["a", "list"]', "null", "42"])
def test_cache_load_rejects_a_corrupt_file(tmp_path: Path, body: str) -> None:
    """A corrupt cache costs one slow run, not a crashed preamble."""
    cache = tmp_path / "cc-sessions.cache.json"
    cache.write_text(body, encoding="utf-8")
    assert ccc._load_cache(cache) == {}


def test_cache_load_skips_individual_bad_entries(tmp_path: Path) -> None:
    cache = tmp_path / "cc-sessions.cache.json"
    from dataclasses import asdict

    cache.write_text(
        json.dumps(
            {
                "/p/good.jsonl": asdict(_entry("/p/good.jsonl")),
                "/p/not-a-dict.jsonl": "oops",
                "/p/missing-keys.jsonl": {"jsonl_path": "/p/missing-keys.jsonl"},
                "/p/bad-mtime.jsonl": {**asdict(_entry("/p/bad-mtime.jsonl")), "mtime_ns": "soon"},
                "/p/null-files.jsonl": {**asdict(_entry("/p/null-files.jsonl")), "files_touched": None},
            }
        ),
        encoding="utf-8",
    )
    loaded = ccc._load_cache(cache)
    assert set(loaded) == {"/p/good.jsonl", "/p/null-files.jsonl"}
    assert loaded["/p/null-files.jsonl"].files_touched == []


def test_cache_write_round_trips(tmp_path: Path) -> None:
    cache = tmp_path / "nested" / "cc-sessions.cache.json"
    ccc._write_cache(cache, {"/p/s.jsonl": _entry()})
    assert ccc._load_cache(cache) == {"/p/s.jsonl": _entry()}


def test_cache_write_cleans_up_after_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cc-sessions.cache.json"
    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.name.endswith(".json.tmp"):
            raise OSError("disk full")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    ccc._write_cache(cache, {"/p/s.jsonl": _entry()})  # must not raise
    assert not cache.exists()
    assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# run() / main()
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    d = tmp_path / "Scout"
    (d / ".scout-cache").mkdir(parents=True)
    return d


def test_run_writes_an_empty_summary_when_there_are_no_sessions(vault: Path, tmp_path: Path) -> None:
    """The function is total: downstream consumers rely on the file existing."""
    out, count = ccc.run(data_dir=vault, cc_projects_dir=tmp_path / "no-projects")
    assert count == 0
    assert out == vault / ".scout-cache" / ccc.OUTPUT_FILENAME
    text = out.read_text()
    assert "No non-Scout CC sessions found" in text
    assert "**Total:** 0 session(s) found." in text


def test_run_reuses_a_cached_entry_when_the_mtime_is_unchanged(
    vault: Path, projects: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the cache: a second run must not re-open an unchanged
    transcript."""
    _session(projects, "-Users-alex-work", "s1", {"type": "user", "content": "first prompt"})
    ccc.run(data_dir=vault, cc_projects_dir=projects)

    def fail(*_a: object, **_k: object):
        raise AssertionError("an unchanged transcript must not be re-extracted")

    monkeypatch.setattr(ccc, "build_session_entry", fail)
    _out, count = ccc.run(data_dir=vault, cc_projects_dir=projects)
    assert count == 1


def test_run_re_extracts_after_an_mtime_bump(vault: Path, projects: Path) -> None:
    p = _session(projects, "-Users-alex-work", "s1", {"type": "user", "content": "first prompt"})
    out, _count = ccc.run(data_dir=vault, cc_projects_dir=projects)
    assert "first prompt" in out.read_text()

    p.write_text(json.dumps({"type": "user", "content": "second prompt"}) + "\n", encoding="utf-8")
    os.utime(p, ns=(p.stat().st_mtime_ns + 1_000_000_000, p.stat().st_mtime_ns + 1_000_000_000))
    out, _count = ccc.run(data_dir=vault, cc_projects_dir=projects)
    assert "second prompt" in out.read_text()


def test_run_sorts_sessions_newest_first(vault: Path, projects: Path) -> None:
    older = _session(projects, "-Users-alex-work", "older", {"type": "user", "content": "older prompt"})
    newer = _session(projects, "-Users-alex-work", "newer", {"type": "user", "content": "newer prompt"})
    base = newer.stat().st_mtime_ns
    os.utime(older, ns=(base - 60_000_000_000, base - 60_000_000_000))

    out, count = ccc.run(data_dir=vault, cc_projects_dir=projects)
    assert count == 2
    text = out.read_text()
    assert text.index("newer prompt") < text.index("older prompt")


def test_run_honours_extra_exclude_suffixes(vault: Path, projects: Path) -> None:
    _session(projects, "-Users-alex-sandbox", "sandboxed", {"type": "user", "content": "sandbox prompt"})
    _session(projects, "-Users-alex-work", "kept", {"type": "user", "content": "work prompt"})

    _out, count = ccc.run(data_dir=vault, cc_projects_dir=projects, extra_exclude_suffixes=("-sandbox",))
    assert count == 1


def test_run_excludes_a_custom_instance_name(vault: Path, projects: Path) -> None:
    _session(projects, "-Users-alex-Nightly", "own", {"type": "user", "content": "own prompt"})
    _session(projects, "-Users-alex-work", "other", {"type": "user", "content": "work prompt"})

    _out, count = ccc.run(data_dir=vault, cc_projects_dir=projects, instance_name="Nightly")
    assert count == 1


def test_main_prints_the_summary_and_returns_zero(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    assert ccc.main(hours=6) == 0
    out = capsys.readouterr().out
    assert "CC session cache written to" in out
    assert "6h lookback" in out


def test_main_returns_zero_even_when_run_blows_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs in every scheduled session's preamble; a crash here must not
    be the reason a session doesn't start."""

    def boom(**_k: object):
        raise RuntimeError("unreachable state")

    monkeypatch.setattr(ccc, "run", boom)
    assert ccc.main() == 0
