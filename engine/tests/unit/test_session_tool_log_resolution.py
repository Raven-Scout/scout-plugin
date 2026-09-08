"""Transcript discovery, row-parsing tolerance and error classification for
the `session-tool-log` Stop hook.

`test_session_tool_log.py` covers `extract_tool_calls`, `write_records` and
the `run()` happy path. This file covers the parts that decide *whether the
hook finds anything at all*:

* `_resolve_transcript_path`'s three-step fallback (explicit path → cwd-encoded
  project dir → full project scan). If it picks nothing, a whole session's
  tool-call accounting silently vanishes from `connector-health.md`.
* `_iter_rows`'s tolerance for a transcript being appended to as we read it.
* `_is_error`'s four independent error signals — a missed one under-reports
  connector failures, which is the exact metric the health report exists for.
* `main()`'s swallow-everything contract: a Stop hook that raises blocks the
  session.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from scout.hooks import session_tool_log as stl
from scout.hooks.session_tool_log import ToolCallRecord


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp HOME so `~/.claude/projects` lookups are hermetic."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _transcript(path: Path, *rows: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _use(tool_id: str, name: str = "Bash", **inp: Any) -> dict[str, Any]:
    return {
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": inp}],
        }
    }


def _result(tool_id: str, **fields: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_id}
    block.update(fields)
    return {"message": {"role": "user", "content": [block]}}


# ---------------------------------------------------------------------------
# _resolve_transcript_path
# ---------------------------------------------------------------------------


def test_an_explicit_transcript_path_wins(home: Path, tmp_path: Path) -> None:
    explicit = _transcript(tmp_path / "explicit.jsonl", _use("t1"))
    assert stl._resolve_transcript_path({"transcript_path": str(explicit)}) == explicit


def test_an_explicit_path_is_tilde_expanded(home: Path) -> None:
    target = _transcript(home / "sessions" / "abc.jsonl", _use("t1"))
    assert stl._resolve_transcript_path({"transcript_path": "~/sessions/abc.jsonl"}) == target


def test_a_nonexistent_explicit_path_falls_through_to_the_session_id(home: Path) -> None:
    """The Stop payload's transcript_path can point at a file that was already
    rotated away; the session_id lookup is the safety net."""
    encoded = "-Users-alex-Scout"
    target = _transcript(home / ".claude" / "projects" / encoded / "sess-1.jsonl", _use("t1"))

    resolved = stl._resolve_transcript_path(
        {"transcript_path": "/nope/gone.jsonl", "session_id": "sess-1", "cwd": "/Users/alex/Scout"}
    )
    assert resolved == target


def test_the_cwd_is_encoded_by_replacing_slashes_with_dashes(home: Path) -> None:
    target = _transcript(home / ".claude" / "projects" / "-Users-alex-Scout" / "sess-1.jsonl", _use("t1"))
    assert stl._resolve_transcript_path({"session_id": "sess-1", "cwd": "/Users/alex/Scout"}) == target


def test_a_missing_cwd_falls_back_to_scanning_every_project_dir(home: Path) -> None:
    projects = home / ".claude" / "projects"
    (projects / "-Users-alex-other").mkdir(parents=True)
    target = _transcript(projects / "-Users-alex-Scout" / "sess-1.jsonl", _use("t1"))
    assert stl._resolve_transcript_path({"session_id": "sess-1"}) == target


def test_a_wrong_cwd_still_finds_the_transcript_by_scanning(home: Path) -> None:
    target = _transcript(home / ".claude" / "projects" / "-Users-alex-Scout" / "sess-1.jsonl", _use("t1"))
    resolved = stl._resolve_transcript_path({"session_id": "sess-1", "cwd": "/somewhere/else"})
    assert resolved == target


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"transcript_path": ""},
        {"transcript_path": 123},
        {"session_id": ""},
        {"session_id": None},
        {"session_id": 42},
    ],
)
def test_resolution_returns_none_without_a_usable_identifier(home: Path, payload: dict) -> None:
    assert stl._resolve_transcript_path(payload) is None


def test_resolution_returns_none_when_no_project_dir_exists(home: Path) -> None:
    assert stl._resolve_transcript_path({"session_id": "sess-1"}) is None


def test_resolution_returns_none_when_the_session_is_in_no_project_dir(home: Path) -> None:
    (home / ".claude" / "projects" / "-Users-alex-Scout").mkdir(parents=True)
    assert stl._resolve_transcript_path({"session_id": "sess-missing"}) is None


# ---------------------------------------------------------------------------
# _iter_rows
# ---------------------------------------------------------------------------


def test_iter_rows_skips_blank_and_malformed_lines(tmp_path: Path) -> None:
    """The transcript is appended to live, so the hook can read a torn final
    line — that must not abort the whole walk."""
    path = tmp_path / "t.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"a": 1}),
                "",
                "   ",
                '{"partial": ',  # torn append
                '"a bare string"',  # valid JSON, not an object
                "[1, 2]",
                json.dumps({"b": 2}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert list(stl._iter_rows(path)) == [{"a": 1}, {"b": 2}]


def test_iter_rows_is_empty_for_a_missing_file(tmp_path: Path) -> None:
    assert list(stl._iter_rows(tmp_path / "nope.jsonl")) == []


def test_iter_rows_replaces_undecodable_bytes(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"tool": "caf\xe9"}\n' + json.dumps({"a": 1}).encode() + b"\n")
    rows = list(stl._iter_rows(path))
    # The mojibake row still decodes (errors="replace") and parses.
    assert {"a": 1} in rows


# ---------------------------------------------------------------------------
# _tool_response_from_result
# ---------------------------------------------------------------------------


def test_snake_case_is_error_is_normalized_to_the_posttooluse_camel_case() -> None:
    """The transcript writes `is_error`; `connector_log.classify`'s consumer
    reads `isError`. Dropping the translation makes every failure look OK."""
    assert stl._tool_response_from_result({"is_error": True})["isError"] is True
    assert "isError" not in stl._tool_response_from_result({"is_error": False})
    assert "isError" not in stl._tool_response_from_result({})


def test_a_string_content_is_wrapped_in_a_text_block() -> None:
    out = stl._tool_response_from_result({"content": "command not found"})
    assert out["content"] == [{"type": "text", "text": "command not found"}]


def test_a_list_content_passes_through() -> None:
    blocks = [{"type": "text", "text": "ok"}]
    assert stl._tool_response_from_result({"content": blocks})["content"] == blocks


def test_a_content_of_another_type_is_dropped() -> None:
    assert "content" not in stl._tool_response_from_result({"content": {"unexpected": "shape"}})


# ---------------------------------------------------------------------------
# _is_error — the four independent signals
# ---------------------------------------------------------------------------


def test_no_error_signals_reads_as_success() -> None:
    assert stl._is_error({}) == (False, "")
    assert stl._is_error({"returncode": 0, "content": [{"type": "text", "text": "ok"}]}) == (False, "")


def test_is_error_flag_marks_a_failure() -> None:
    assert stl._is_error({"isError": True}) == (True, "")


def test_a_nonzero_returncode_marks_a_failure() -> None:
    assert stl._is_error({"returncode": 127}) == (True, "")
    # A non-int returncode is not a signal.
    assert stl._is_error({"returncode": "127"}) == (False, "")


def test_an_error_field_marks_a_failure_and_supplies_the_snippet() -> None:
    is_err, snippet = stl._is_error({"error": "gh: not logged in"})
    assert is_err is True
    assert snippet == "gh: not logged in"


def test_an_error_snippet_is_truncated_to_160_chars() -> None:
    _is_err, snippet = stl._is_error({"error": "x" * 500})
    assert len(snippet) == 160


def test_a_content_block_flagged_is_error_marks_a_failure() -> None:
    is_err, snippet = stl._is_error({"content": [{"isError": True, "text": "connector unavailable"}]})
    assert is_err is True
    assert snippet == "connector unavailable"


def test_a_content_block_with_no_text_yields_an_empty_snippet() -> None:
    assert stl._is_error({"content": [{"isError": True}]}) == (True, "")


def test_the_first_snippet_wins_over_later_ones() -> None:
    _is_err, snippet = stl._is_error(
        {
            "error": "outer error",
            "content": [{"isError": True, "text": "inner error"}],
        }
    )
    assert snippet == "outer error"


def test_non_dict_content_items_are_ignored() -> None:
    assert stl._is_error({"content": ["a string", None, 42]}) == (False, "")


# ---------------------------------------------------------------------------
# write_records
# ---------------------------------------------------------------------------


def test_write_records_survives_an_unwritable_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Stop hook must not raise; a failed write returns 0 written."""
    log_dir = tmp_path / "logs"
    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.suffix == ".jsonl":
            raise OSError("read-only filesystem")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    written = stl.write_records(
        [ToolCallRecord(tool_name="Bash", tool_input={}, tool_response={})],
        mode="dreaming",
        session_id="s1",
        log_dir=log_dir,
    )
    assert written == 0


def test_write_records_appends_to_an_existing_day_file(tmp_path: Path) -> None:
    """Two hook invocations on the same ET day accumulate rather than
    truncate — a Scout day can run several sessions."""
    rec = [ToolCallRecord(tool_name="Bash", tool_input={"command": "ls"}, tool_response={})]
    assert stl.write_records(rec, mode="dreaming", session_id="s1", log_dir=tmp_path) == 1
    assert stl.write_records(rec, mode="briefing", session_id="s2", log_dir=tmp_path) == 1

    out = next(tmp_path.glob("connector-calls-*.jsonl"))
    rows = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert [r["session_id"] for r in rows] == ["s1", "s2"]
    assert [r["mode"] for r in rows] == ["dreaming", "briefing"]


def test_write_records_creates_the_log_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / ".scout-logs"
    stl.write_records(
        [ToolCallRecord(tool_name="Bash", tool_input={}, tool_response={})],
        mode="dreaming",
        session_id="s1",
        log_dir=nested,
    )
    assert nested.is_dir()


# ---------------------------------------------------------------------------
# run() / main()
# ---------------------------------------------------------------------------


def test_run_returns_none_for_a_non_object_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    assert stl.run(stdin=io.StringIO('"just a string"')) is None
    assert stl.run(stdin=io.StringIO("[1, 2, 3]")) is None


def test_run_falls_back_to_the_transcript_stem_for_the_session_id(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Stop payload can carry transcript_path without session_id; the stem is
    the id, so the row still attributes to the right session."""
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    (tmp_path / "Scout" / ".scout-logs").mkdir(parents=True)

    transcript = _transcript(tmp_path / "sess-xyz.jsonl", _use("t1", command="ls"), _result("t1"))
    event = stl.run(stdin=io.StringIO(json.dumps({"transcript_path": str(transcript)})))

    assert event is not None
    assert event.payload["session_id"] == "sess-xyz"
    assert event.payload["calls_written"] == 1
    assert event.kind == "session.tool_log.written"


def test_run_reads_real_stdin_when_none_is_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert stl.run() is None


def test_main_returns_zero_even_when_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Stop hook's exit code gates the session; it must always be 0."""

    def boom(**_k: object):
        raise RuntimeError("unreachable state")

    monkeypatch.setattr(stl, "run", boom)
    assert stl.main() == 0


def test_main_returns_zero_on_the_happy_path(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "Scout"))
    (tmp_path / "Scout" / ".scout-logs").mkdir(parents=True)
    transcript = _transcript(tmp_path / "sess-1.jsonl", _use("t1", command="ls"), _result("t1"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": str(transcript)})))
    assert stl.main() == 0
    assert list((tmp_path / "Scout" / ".scout-logs").glob("connector-calls-*.jsonl"))


# ---------------------------------------------------------------------------
# extract_tool_calls — the remaining skip branches
# ---------------------------------------------------------------------------


def test_extract_skips_blocks_and_ids_it_cannot_use() -> None:
    rows = [
        {"message": "not a dict"},
        {"message": {"role": "assistant", "content": "not a list"}},
        {"message": {"role": "assistant", "content": ["a bare string", None]}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "thinking"}]}},
        # tool_use with a non-string id
        {"message": {"role": "assistant", "content": [{"type": "tool_use", "id": 7, "name": "Bash"}]}},
        {"message": {"role": "user", "content": ["a bare string"]}},
        {"message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},
        # tool_result with a non-string id, and one for an unknown call
        {"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": 7}]}},
        {"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "never-used"}]}},
        {"message": {"role": "system", "content": [{"type": "tool_use", "id": "sys", "name": "Bash"}]}},
        _use("t1", command="ls"),
        _result("t1"),
    ]
    calls = stl.extract_tool_calls(rows)
    assert [c.tool_name for c in calls] == ["Bash"]
    assert calls[0].tool_input == {"command": "ls"}


def test_extract_defaults_a_missing_tool_name_to_unknown() -> None:
    rows = [{"message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1"}]}}]
    calls = stl.extract_tool_calls(rows)
    assert [c.tool_name for c in calls] == ["unknown"]
    assert calls[0].tool_input == {}
