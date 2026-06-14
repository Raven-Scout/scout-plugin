"""Unit tests for engine/scout/scripts/connector_probes.py."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from scout.errors import ConfigError
from scout.scripts.connector_probes import (
    ProbeKind,
    load_registry,
    resolve_registry,
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


def test_fallbacks_as_string_raises(tmp_path):
    path = _registry(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks: mcp__claude_ai_Slack__slack_read_user_profile
        """,
    )
    with pytest.raises(ValueError, match="'fallbacks' must be a list"):
        load_registry(path)


def test_top_level_list_raises(tmp_path):
    path = tmp_path / "connector-probes.yaml"
    path.write_text("- foo\n- bar\n")
    with pytest.raises(ValueError, match="must be a YAML mapping at the top level"):
        load_registry(path)


def test_bash_probe_with_empty_command_raises(tmp_path):
    path = _registry(
        tmp_path,
        """
        github:
          primary: bash
          command: ""
        """,
    )
    with pytest.raises(ValueError, match="must not be empty"):
        load_registry(path)


def test_needs_user_input_as_string_raises(tmp_path):
    path = _registry(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          needs_user_input: user_slack_id
        """,
    )
    with pytest.raises(ValueError, match="'needs_user_input' must be a list"):
        load_registry(path)


# ---------------------------------------------------------------------------
# resolve_registry tests
# ---------------------------------------------------------------------------


def _shipped(tmp_path: Path, body: str) -> Path:
    """Write a fake shipped registry under <plugin_root>/templates/."""
    templates = tmp_path / "plugin" / "templates"
    templates.mkdir(parents=True)
    (templates / "connector-probes.yaml").write_text(dedent(body))
    return tmp_path / "plugin"  # plugin_root


def _overlay(data_dir: Path, body: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "connector-probes.local.yaml").write_text(dedent(body))


def test_resolve_shipped_only_when_no_overlay(tmp_path):
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks: []
        """,
    )
    reg = resolve_registry(plugin_root=plugin_root, data_dir=tmp_path / "Scout")
    assert set(reg) == {"slack"}


def test_resolve_overlay_adds_new_connector(tmp_path):
    """The #97 repro: a custom devin probe in the overlay is merged in."""
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks: []
        """,
    )
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        devin:
          primary: mcp__devin__devin_session_search
          fallbacks: []
          needs_user_input:
            - devin_org_token
        """,
    )
    reg = resolve_registry(plugin_root=plugin_root, data_dir=data_dir)
    assert set(reg) == {"slack", "devin"}
    assert reg["devin"].tool_chain == ["mcp__devin__devin_session_search"]
    assert reg["devin"].needs_user_input == ["devin_org_token"]


def test_resolve_overlay_overrides_shipped_key(tmp_path):
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: shipped_tool
          fallbacks: []
        """,
    )
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        slack:
          primary: overlay_tool
          fallbacks: []
        """,
    )
    reg = resolve_registry(plugin_root=plugin_root, data_dir=data_dir)
    assert reg["slack"].tool_chain == ["overlay_tool"]


def test_resolve_empty_overlay_is_noop(tmp_path):
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: t
          fallbacks: []
        """,
    )
    data_dir = tmp_path / "Scout"
    _overlay(data_dir, "")
    reg = resolve_registry(plugin_root=plugin_root, data_dir=data_dir)
    assert set(reg) == {"slack"}


def test_resolve_malformed_overlay_raises_configerror_naming_file(tmp_path):
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: t
          fallbacks: []
        """,
    )
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        broken:
          fallbacks: []
        """,  # missing 'primary'
    )
    with pytest.raises(ConfigError, match="connector-probes.local.yaml"):
        resolve_registry(plugin_root=plugin_root, data_dir=data_dir)


def test_resolve_missing_shipped_raises_configerror(tmp_path):
    plugin_root = tmp_path / "plugin"  # no templates/ dir
    with pytest.raises(ConfigError, match="connector-probes.yaml"):
        resolve_registry(plugin_root=plugin_root, data_dir=tmp_path / "Scout")


def test_resolve_syntactically_invalid_overlay_raises_configerror(tmp_path):
    plugin_root = _shipped(tmp_path, "slack:\n  primary: t\n  fallbacks: []\n")
    data_dir = tmp_path / "Scout"
    data_dir.mkdir()
    (data_dir / "connector-probes.local.yaml").write_text("key: [unclosed\n")
    with pytest.raises(ConfigError, match="connector-probes.local.yaml"):
        resolve_registry(plugin_root=plugin_root, data_dir=data_dir)
