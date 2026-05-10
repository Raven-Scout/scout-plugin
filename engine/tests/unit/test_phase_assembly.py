"""Unit tests for engine/scout/scripts/phase_assembly.py."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_select_filters_mixed_connectors_within_file(tmp_path):
    """Two sections in the same file with different `requires` are filtered independently."""
    sections = parse_phase_file(FIXTURES / "connectors" / "dummy-mixed.md")
    assert len(sections) == 2
    only_slack = select_sections(sections, enabled_connectors={"slack"})
    assert len(only_slack) == 1
    assert only_slack[0].requires == "slack"
    only_gmail = select_sections(sections, enabled_connectors={"gmail"})
    assert len(only_gmail) == 1
    assert only_gmail[0].requires == "gmail"
    both = select_sections(sections, enabled_connectors={"slack", "gmail"})
    assert len(both) == 2


def test_parse_skips_trailing_fence_junk_section(tmp_path):
    """A file ending with '---' should not yield an empty junk section."""
    p = tmp_path / "trailing.md"
    p.write_text("---\nphase: core\nname: x\nslot: setup\nmode: []\nrequires: null\n---\n\nbody\n\n---\n")
    sections = parse_phase_file(p)
    assert len(sections) == 1
    assert sections[0].name == "x"


def test_parse_raises_on_missing_phase(tmp_path):
    """A section with empty 'phase' field is rejected."""
    p = tmp_path / "no_phase.md"
    p.write_text("---\nname: x\nslot: setup\nmode: []\nrequires: null\n---\n\nbody\n")
    with pytest.raises(ValueError, match="'phase' field is required"):
        parse_phase_file(p)


def test_parse_raises_on_string_mode(tmp_path):
    """`mode: briefing` (string) instead of `mode: [briefing]` (list) is rejected."""
    p = tmp_path / "bad_mode.md"
    p.write_text("---\nphase: core\nname: x\nslot: setup\nmode: briefing\nrequires: null\n---\n\nbody\n")
    with pytest.raises(ValueError, match="'mode' must be a YAML list"):
        parse_phase_file(p)


def test_parse_raises_on_list_requires(tmp_path):
    """`requires: [slack, gmail]` (list) instead of `requires: slack` (string) is rejected."""
    p = tmp_path / "bad_requires.md"
    p.write_text("---\nphase: connector\nname: x\nslot: setup\nmode: []\nrequires: [slack, gmail]\n---\n\nbody\n")
    with pytest.raises(ValueError, match="'requires'"):
        parse_phase_file(p)
