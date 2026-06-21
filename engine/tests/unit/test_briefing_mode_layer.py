"""Tests for the briefing-mode layer (spec 2026-06-21-briefing-mode-layer-design).

Covers the three assembled behaviors and the runner mode-signal plumbing:
  - Run Modes dispatch table (phases/core/00-run-modes.md) renders into SKILL
    near the top, and is mode-scoped so it does not leak into DREAMING/RESEARCH.
  - Monday Preview (weekend-only, mode: [briefing]).
  - Scout Digest on the briefing side (mode: [briefing, consolidation]).
  - The runner prompt dispatches on the dispatcher's $MODE slot key instead of
    re-deriving mode from the clock.
"""

from __future__ import annotations

from pathlib import Path

from scout.scripts.bootstrap import BootstrapConfig, _assemble

PLUGIN_ROOT = Path(__file__).parent.parent.parent.parent
RUNNER_TMPL = PLUGIN_ROOT / "templates" / "run-scout.sh.tmpl"


def _config(*, enabled_connectors: set[str] | None = None) -> BootstrapConfig:
    return BootstrapConfig(
        vault=Path("/tmp/does-not-matter"),
        plugin_root=PLUGIN_ROOT,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=enabled_connectors or set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


# --- Run Modes dispatch table ------------------------------------------------


def test_skill_includes_run_modes_table():
    skill = _assemble(_config(), "SKILL")
    assert "## Run Modes" in skill
    # Keys on slot semantics (the dispatcher's SCOUT_FORCE_MODE), not the clock.
    assert "SCOUT_FORCE_MODE" in skill
    assert "weekend-briefing" in skill
    assert "morning-briefing" in skill
    assert "consolidation" in skill


def test_run_modes_table_renders_at_top_of_skill():
    """The dispatch table must precede the work sections so the model reads
    'which mode am I in' before executing connector/core phases."""
    skill = _assemble(_config(enabled_connectors={"slack"}), "SKILL")
    run_modes_idx = skill.index("## Run Modes")
    # action-items archiving is the previously-first core section.
    assert run_modes_idx < skill.index("Archive Old Action Items")
    # And it precedes every connector section.
    assert run_modes_idx < skill.index("Slack")


# --- Monday Preview (weekend-only) -------------------------------------------


def test_skill_includes_monday_preview():
    skill = _assemble(_config(), "SKILL")
    assert "Monday Preview" in skill


# --- Scout Digest on the briefing side ---------------------------------------


def test_skill_includes_briefing_scout_digest():
    skill = _assemble(_config(), "SKILL")
    assert "Scout Digest" in skill


# --- Mode-scope leak guards --------------------------------------------------


def test_dreaming_excludes_briefing_only_sections():
    dreaming = _assemble(_config(), "DREAMING")
    assert "## Run Modes" not in dreaming
    assert "Monday Preview" not in dreaming


def test_research_excludes_briefing_only_sections():
    research = _assemble(_config(), "RESEARCH")
    assert "## Run Modes" not in research
    assert "Monday Preview" not in research


# --- Runner mode-signal plumbing ---------------------------------------------


def test_runner_prompt_dispatches_on_mode_signal():
    tmpl = RUNNER_TMPL.read_text(encoding="utf-8")
    # The prompt references the resolved $MODE rather than telling the model
    # to read the clock.
    assert "$MODE" in tmpl
    assert "Determine your mode based on the current hour" not in tmpl
    assert "date +%H" not in tmpl


def test_runner_assigns_mode_before_prompt():
    """$MODE must be resolved before the PROMPT string interpolates it,
    otherwise the prompt expands to an empty mode at runtime."""
    tmpl = RUNNER_TMPL.read_text(encoding="utf-8")
    mode_assign = tmpl.index('MODE="${SCOUT_FORCE_MODE:-manual}"')
    prompt_def = tmpl.index('PROMPT="')
    assert mode_assign < prompt_def
