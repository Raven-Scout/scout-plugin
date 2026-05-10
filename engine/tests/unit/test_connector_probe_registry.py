"""Unit tests for engine/scout/scripts/connector_probes.py."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from scout.scripts.connector_probes import (
    Probe,
    ProbeKind,
    load_registry,
)


def _registry(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "connector-probes.yaml"
    p.write_text(dedent(body))
    return p


def test_load_basic_mcp_probe(tmp_path):
    path = _registry(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks:
            - mcp__claude_ai_Slack__slack_read_user_profile
        """,
    )
    reg = load_registry(path)
    assert "slack" in reg
    probe = reg["slack"]
    assert probe.kind is ProbeKind.MCP_TOOL
    assert probe.tool_chain == [
        "mcp__plugin_slack_slack__slack_read_user_profile",
        "mcp__claude_ai_Slack__slack_read_user_profile",
    ]


def test_load_bash_probe(tmp_path):
    path = _registry(
        tmp_path,
        """
        github:
          primary: bash
          command: "gh auth status"
        """,
    )
    reg = load_registry(path)
    probe = reg["github"]
    assert probe.kind is ProbeKind.BASH
    assert probe.bash_command == "gh auth status"


def test_load_with_user_input_fields(tmp_path):
    path = _registry(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks: []
          needs_user_input:
            - user_slack_id
        """,
    )
    reg = load_registry(path)
    assert reg["slack"].needs_user_input == ["user_slack_id"]


def test_missing_primary_raises(tmp_path):
    path = _registry(
        tmp_path,
        """
        slack:
          fallbacks: []
        """,
    )
    with pytest.raises(ValueError, match="missing 'primary'"):
        load_registry(path)


def test_bash_probe_without_command_raises(tmp_path):
    path = _registry(
        tmp_path,
        """
        github:
          primary: bash
        """,
    )
    with pytest.raises(ValueError, match="bash probe.*requires 'command'"):
        load_registry(path)


def test_probe_emits_user_input_default_empty(tmp_path):
    path = _registry(
        tmp_path,
        """
        calendar:
          primary: mcp__claude_ai_Google_Calendar__list_calendars
          fallbacks: []
        """,
    )
    reg = load_registry(path)
    assert reg["calendar"].needs_user_input == []
