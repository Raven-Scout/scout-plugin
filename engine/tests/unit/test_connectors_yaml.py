"""Unit tests for scout.connectors — YAML loader + typed lookups."""

from __future__ import annotations

import pytest

from scout.connectors import (
    Capability,
    Connector,
    ConnectorRegistry,
    Tier,
    load_registry,
)
from scout.schedule import SlotType


def test_load_registry_returns_official_tier_seed():
    reg = load_registry()
    assert isinstance(reg, ConnectorRegistry)
    keys = set(reg.keys())
    # The 9 official-tier connectors that ship in the seed YAML.
    assert keys >= {
        "mcp:claude_ai_Slack",
        "mcp:claude_ai_Linear",
        "mcp:claude_ai_Gmail",
        "mcp:claude_ai_Google_Calendar",
        "mcp:claude_ai_Granola",
        "mcp:claude_ai_Google_Drive",
        "github",
        "mcp:claude-in-chrome",
        "mcp:whatsapp-mcp",
        "notify:telegram",
    }


def test_connector_fields_typed():
    reg = load_registry()
    slack = reg["mcp:claude_ai_Slack"]
    assert isinstance(slack, Connector)
    assert slack.display_name == "Slack"
    assert slack.tier == Tier.OFFICIAL
    assert Capability.INBOUND in slack.capabilities
    assert Capability.OUTBOUND in slack.capabilities


def test_slack_required_in_briefing_and_consolidation_types():
    """Slack is required across the four operational slot types."""
    reg = load_registry()
    slack = reg["mcp:claude_ai_Slack"]
    assert slack.required_in_type(SlotType.BRIEFING)
    assert slack.required_in_type(SlotType.CONSOLIDATION)
    assert slack.required_in_type(SlotType.DREAMING)
    assert slack.required_in_type(SlotType.RESEARCH)
    # Manual is never required for any connector.
    assert not slack.required_in_type(SlotType.MANUAL)


def test_required_in_specific_types_granola_briefing_consolidation_only():
    """Granola: briefing + consolidation only — never dreaming/research."""
    reg = load_registry()
    granola = reg["mcp:claude_ai_Granola"]
    assert granola.required_in_type(SlotType.BRIEFING)
    assert granola.required_in_type(SlotType.CONSOLIDATION)
    assert not granola.required_in_type(SlotType.DREAMING)
    assert not granola.required_in_type(SlotType.RESEARCH)


def test_outbound_only_connector_required_in_no_type():
    """Telegram is outbound-only — never required for any slot type."""
    reg = load_registry()
    tg = reg["notify:telegram"]
    assert tg.required_in_types == ()
    for st in SlotType:
        assert not tg.required_in_type(st)


def test_remediation_fields_under_180_chars():
    """The first_fix string goes into Slack/Telegram DMs and gets truncated; pin the cap."""
    reg = load_registry()
    for key, c in reg.items():
        assert len(c.remediation.first_fix) <= 180, (
            f"{key}.remediation.first_fix too long ({len(c.remediation.first_fix)} chars)"
        )


def test_critical_connectors_filter_by_slot_type():
    reg = load_registry()
    critical = reg.critical_for_slot_type(SlotType.BRIEFING)
    assert "mcp:claude_ai_Slack" in critical
    assert "mcp:claude_ai_Granola" in critical
    assert "notify:telegram" not in critical  # outbound, never critical
    # Dreaming requires a smaller set: Slack + Linear only.
    dreaming_critical = reg.critical_for_slot_type(SlotType.DREAMING)
    assert "mcp:claude_ai_Slack" in dreaming_critical
    assert "mcp:claude_ai_Linear" in dreaming_critical
    assert "mcp:claude_ai_Granola" not in dreaming_critical
    assert "github" not in dreaming_critical  # gh used in research, not dreaming


def test_unknown_connector_raises():
    reg = load_registry()
    with pytest.raises(KeyError):
        reg["mcp:nonexistent"]


def test_overlay_path_layered_on_seed(tmp_path, monkeypatch):
    """If <data_dir>/.scout-state/connectors.local.yaml exists, it overlays the seed.

    v0.4 doesn't write to this file but the loader respects it so v0.8 can land
    the writer without touching the loader.
    """
    overlay = tmp_path / ".scout-state" / "connectors.local.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        """
schema_version: 1
connectors:
  mcp:custom-thing:
    display_name: Custom
    tier: community
    capabilities: [inbound]
    required_in: []
    remediation:
      first_fix: "Manual restart."
      detail: "User-authored."
"""
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    reg = load_registry()
    assert "mcp:custom-thing" in reg
    assert reg["mcp:custom-thing"].tier == Tier.COMMUNITY


def test_overlay_can_override_seed_remediation(tmp_path, monkeypatch):
    overlay = tmp_path / ".scout-state" / "connectors.local.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(
        """
schema_version: 1
connectors:
  mcp:claude_ai_Slack:
    remediation:
      first_fix: "User-customized fix instructions."
"""
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    reg = load_registry()
    # Overlay overrides only the field it specifies; other fields inherit from seed.
    assert reg["mcp:claude_ai_Slack"].remediation.first_fix == "User-customized fix instructions."
    assert reg["mcp:claude_ai_Slack"].display_name == "Slack"  # inherited
