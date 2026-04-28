"""Unit tests for scout.hooks.connector_log."""

from __future__ import annotations

import io
import json

from scout.events import Event
from scout.hooks.connector_log import classify, run


# Mode is required; the hook short-circuits without it (interactive sessions).
def test_no_scout_mode_short_circuits(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOUT_MODE", raising=False)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    result = run(stdin=io.StringIO(payload))
    assert result is None
    # No JSONL file written.
    log_dir = tmp_path / ".scout-logs"
    if log_dir.exists():
        assert not list(log_dir.glob("connector-calls-*.jsonl"))


def test_classify_bash_uses_first_token(tmp_path):
    assert classify("Bash", {"command": "gh pr list"}) == "github"
    assert classify("Bash", {"command": "ls -la"}) == "bash:ls"
    assert classify("Bash", {"command": "  curl -s url"}) == "bash:curl"
    assert classify("Bash", {"command": ""}) == "bash"


def test_classify_mcp_extracts_server_segment():
    assert classify("mcp__plugin_slack_slack__slack_send_message", {}) == "mcp:plugin_slack_slack"
    assert classify("mcp__claude_ai_Gmail__search_threads", {}) == "mcp:claude_ai_Gmail"
    assert classify("mcp__claude-in-chrome__find", {}) == "mcp:claude-in-chrome"
    assert classify("mcp__whatsapp-mcp__list_messages", {}) == "mcp:whatsapp-mcp"


def test_classify_other_tool_lowercases():
    assert classify("Read", {}) == "read"
    assert classify("WebFetch", {}) == "webfetch"


def test_run_writes_one_jsonl_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "morning-briefing")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    payload_text = json.dumps(
        {
            "session_id": "abc-123",
            "tool_name": "mcp__plugin_slack_slack__slack_send_message",
            "tool_input": {"channel": "C", "text": "x"},
            "tool_response": {"isError": False},
        }
    )
    event = run(stdin=io.StringIO(payload_text))
    assert isinstance(event, Event)
    assert event.kind == "tool.call.logged"
    assert event.source == "hook:connector-log"
    assert event.payload["connector"] == "mcp:plugin_slack_slack"
    assert event.payload["session_id"] == "abc-123"
    assert event.payload["mode"] == "morning-briefing"
    assert event.payload["error"] is False

    # JSONL row landed.
    et_logs = list((tmp_path / ".scout-logs").glob("connector-calls-*.jsonl"))
    assert len(et_logs) == 1
    rows = [json.loads(line) for line in et_logs[0].read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["connector"] == "mcp:plugin_slack_slack"
    assert rows[0]["mode"] == "morning-briefing"
    assert rows[0]["error"] is False
    # Event ts is UTC ISO-8601 with Z suffix.
    assert rows[0]["ts"].endswith("Z")


def test_run_records_error_with_snippet(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "consolidation-1pm")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    payload_text = json.dumps(
        {
            "session_id": "err-1",
            "tool_name": "mcp__claude_ai_Gmail__search_threads",
            "tool_response": {"isError": True, "error": "auth expired token rotated"},
        }
    )
    event = run(stdin=io.StringIO(payload_text))
    assert event.payload["error"] is True
    assert event.payload["err"] == "auth expired token rotated"


def test_run_handles_malformed_payload_silently(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "manual")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    # Hooks must NEVER break the session — return None on malformed input.
    result = run(stdin=io.StringIO("{not json"))
    assert result is None


def test_run_truncates_err_snippet_at_160_chars(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_MODE", "manual")
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    long_err = "X" * 500
    payload = json.dumps(
        {
            "session_id": "err-trunc",
            "tool_name": "Bash",
            "tool_input": {"command": "false"},
            "tool_response": {"returncode": 1, "error": long_err},
        }
    )
    event = run(stdin=io.StringIO(payload))
    assert len(event.payload["err"]) == 160
    assert event.payload["err"] == "X" * 160
