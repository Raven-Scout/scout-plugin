"""Unit tests for scout.hooks.session_tool_log.

Closes #72: per-tool-call PostToolUse hook was replaced with a single
Stop-hook walk of the session transcript. The output JSONL must remain
wire-compatible with the old hook so `connector_health_report` keeps
working unchanged.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scout.hooks.session_tool_log import (
    ToolCallRecord,
    extract_tool_calls,
    run,
    write_records,
)

# ----- helpers ------------------------------------------------------------


def _user_msg(content) -> dict:
    return {"message": {"role": "user", "content": content}}


def _assistant_tool_use(tool_id: str, name: str, **input_kwargs) -> dict:
    return {
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": name, "input": input_kwargs},
            ],
        },
    }


def _user_tool_result(tool_id: str, content, *, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    if is_error:
        block["is_error"] = True
    return _user_msg([block])


# ----- extract_tool_calls ------------------------------------------------


def test_extract_pairs_tool_use_with_tool_result() -> None:
    rows = [
        _user_msg("kick it off"),
        _assistant_tool_use("toolu_1", "Bash", command="ls"),
        _user_tool_result("toolu_1", "file1\nfile2"),
    ]
    calls = extract_tool_calls(rows)
    assert len(calls) == 1
    assert calls[0].tool_name == "Bash"
    assert calls[0].tool_input == {"command": "ls"}
    assert calls[0].tool_response  # non-empty


def test_extract_emits_in_chronological_order() -> None:
    rows = [
        _assistant_tool_use("toolu_1", "Bash", command="first"),
        _assistant_tool_use("toolu_2", "Bash", command="second"),
        _user_tool_result("toolu_2", "second result"),  # arrives first
        _user_tool_result("toolu_1", "first result"),  # arrives second
    ]
    calls = extract_tool_calls(rows)
    # Output order matches tool_use issuance, not tool_result arrival.
    assert [c.tool_input["command"] for c in calls] == ["first", "second"]


def test_extract_emits_unmatched_tool_use_with_empty_response() -> None:
    """A tool_use without a tool_result (session crashed mid-call) is still
    recorded — we still know the tool fired."""
    rows = [
        _assistant_tool_use("toolu_1", "Bash", command="never finished"),
    ]
    calls = extract_tool_calls(rows)
    assert len(calls) == 1
    assert calls[0].tool_response == {}


def test_extract_skips_non_tool_messages() -> None:
    rows = [
        _user_msg("just chatting"),
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}},
        {"type": "permission-mode", "permissionMode": "auto"},  # non-message row
    ]
    assert extract_tool_calls(rows) == []


def test_extract_propagates_is_error_flag() -> None:
    rows = [
        _assistant_tool_use("toolu_1", "Bash", command="false"),
        _user_tool_result("toolu_1", "exit 1", is_error=True),
    ]
    calls = extract_tool_calls(rows)
    assert calls[0].tool_response.get("isError") is True


def test_extract_handles_content_as_string_or_list() -> None:
    rows_string = [
        _assistant_tool_use("toolu_1", "Read", file_path="/p"),
        _user_tool_result("toolu_1", "single string content"),
    ]
    rows_list = [
        _assistant_tool_use("toolu_2", "Read", file_path="/p"),
        _user_tool_result("toolu_2", [{"type": "text", "text": "block content"}]),
    ]
    assert len(extract_tool_calls(rows_string)) == 1
    assert len(extract_tool_calls(rows_list)) == 1


# ----- write_records ----------------------------------------------------


def test_write_records_emits_jsonl_with_classify_field(tmp_path: Path) -> None:
    log_dir = tmp_path / ".scout-logs"
    records = [
        ToolCallRecord(tool_name="Bash", tool_input={"command": "gh pr list"}, tool_response={}),
        ToolCallRecord(tool_name="Read", tool_input={"file_path": "/x"}, tool_response={}),
    ]
    count = write_records(records, mode="briefing", session_id="abc", log_dir=log_dir)
    assert count == 2
    files = list(log_dir.glob("connector-calls-*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert rows[0]["tool"] == "Bash"
    assert rows[0]["connector"] == "github"  # classify("Bash", {"command": "gh ..."}) → "github"
    assert rows[1]["tool"] == "Read"
    assert rows[1]["connector"] == "read"
    assert all(r["mode"] == "briefing" and r["session_id"] == "abc" for r in rows)


def test_write_records_no_op_on_empty(tmp_path: Path) -> None:
    log_dir = tmp_path / ".scout-logs"
    count = write_records([], mode="briefing", session_id="abc", log_dir=log_dir)
    assert count == 0
    assert not log_dir.exists() or not list(log_dir.glob("*.jsonl"))


def test_write_records_records_error_snippet(tmp_path: Path) -> None:
    log_dir = tmp_path / ".scout-logs"
    records = [
        ToolCallRecord(
            tool_name="Bash",
            tool_input={"command": "false"},
            tool_response={"error": "command failed", "isError": True},
        ),
    ]
    write_records(records, mode="dreaming", session_id="x", log_dir=log_dir)
    line = next(log_dir.glob("connector-calls-*.jsonl")).read_text().splitlines()[0]
    row = json.loads(line)
    assert row["error"] is True
    assert row["err"] == "command failed"


# ----- end-to-end via run() ---------------------------------------------


def test_run_short_circuits_when_scout_mode_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("")
    stdin = io.StringIO(json.dumps({"transcript_path": str(transcript)}))
    assert run(stdin=stdin) is None


def test_run_processes_transcript_and_writes_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_MODE", "briefing")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path / "vault"))

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                _assistant_tool_use("toolu_x", "Bash", command="ls"),
                _user_tool_result("toolu_x", "file1\nfile2"),
            ]
        )
        + "\n"
    )

    stdin = io.StringIO(json.dumps({"transcript_path": str(transcript), "session_id": "abc"}))
    ev = run(stdin=stdin)
    assert ev is not None
    assert ev.kind == "session.tool_log.written"
    assert ev.payload["calls_written"] == 1

    log_dir = tmp_path / "vault" / ".scout-logs"
    files = list(log_dir.glob("connector-calls-*.jsonl"))
    assert len(files) == 1
    assert "Bash" in files[0].read_text()


def test_run_no_op_when_transcript_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_MODE", "briefing")
    stdin = io.StringIO(json.dumps({"transcript_path": str(tmp_path / "absent.jsonl")}))
    assert run(stdin=stdin) is None


def test_run_no_op_on_malformed_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_MODE", "briefing")
    stdin = io.StringIO("not json at all")
    assert run(stdin=stdin) is None
