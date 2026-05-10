"""Unit tests for engine/scout/scripts/phase_assembly.py."""

from __future__ import annotations

from pathlib import Path

from scout.scripts.phase_assembly import (
    parse_phase_file,
    render_template,
    select_sections,
)

FIXTURES = Path(__file__).parent / "fixtures" / "phases"


def test_parse_single_section_file():
    sections = parse_phase_file(FIXTURES / "core" / "dummy-core.md")
    assert len(sections) == 1
    s = sections[0]
    assert s.phase == "core"
    assert s.name == "dummy-core"
    assert s.slot == "setup"
    assert s.mode == ["briefing"]
    assert s.requires is None
    assert "Hello {{USER_NAME}}" in s.body


def test_parse_multi_section_file():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    assert len(sections) == 2
    assert sections[0].slot == "query"
    assert sections[1].slot == "outbound-scan"
    assert sections[0].requires == "slack"


def test_select_filters_by_enabled_connectors():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    selected = select_sections(sections, enabled_connectors={"slack"})
    assert len(selected) == 2
    selected_disabled = select_sections(sections, enabled_connectors=set())
    assert selected_disabled == []


def test_select_keeps_requires_null():
    sections = parse_phase_file(FIXTURES / "core" / "dummy-core.md")
    selected = select_sections(sections, enabled_connectors=set())
    assert len(selected) == 1


def test_render_template_substitutes_variables():
    out = render_template(
        "Hello {{USER_NAME}} at {{SCOUT_DIR}}",
        {"USER_NAME": "Alice", "SCOUT_DIR": "/tmp/x"},
    )
    assert out == "Hello Alice at /tmp/x"


def test_render_template_empty_for_unknown_var():
    out = render_template("X {{UNKNOWN_VAR}} Y", {})
    assert out == "X  Y"


def test_select_filters_by_slot():
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-slack.md")
    selected = select_sections(
        sections,
        enabled_connectors={"slack"},
        slot="outbound-scan",
    )
    assert len(selected) == 1
    assert selected[0].slot == "outbound-scan"
