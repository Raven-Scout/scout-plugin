"""Static guard: every runner template exports SCOUT_MODE before launching claude.

Regression context (#192, #121): the runners resolved the dispatcher's slot key
into a *local* ``MODE`` shell variable and exported only ``SCOUT_DATA_DIR``. The
plugin's Stop hooks (hooks/hooks.json -> session-tool-log, session-tokens) read
``SCOUT_MODE`` from the claude child's environment and deliberately short-circuit
when it is unset (interactive sessions must stay silent) — so every scheduled run
looked interactive, no ``connector-calls-*.jsonl`` row was ever written, and the
whole outage-detection chain built on that telemetry (``connector-health-report``,
``connector-alerts.log``, the macOS notification, ``connector-health.md``) never
activated. The export was present in the pre-port bash runner and dropped in the
Plan 4/5 migration; this test pins it so it cannot be dropped silently again.

The end-to-end counterpart (rendered runner -> stubbed claude -> real hook) lives
in ``tests/integration/test_run_scout_mode_export.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
TEMPLATES = REPO_ROOT / "templates"

# (template, default MODE when the dispatcher's SCOUT_FORCE_MODE is unset)
RUNNERS = [
    ("run-scout.sh.tmpl", "manual"),
    ("run-dreaming.sh.tmpl", "dreaming-manual"),
    ("run-research.sh.tmpl", "research-manual"),
]

EXPORT_LINE = 'export SCOUT_MODE="$MODE"'
CLAUDE_LAUNCH = '"$SCOUT_DIR/scripts/claude-with-retry.sh"'


@pytest.mark.parametrize(("name", "default_mode"), RUNNERS)
def test_runner_exports_scout_mode_between_mode_resolution_and_claude_launch(name: str, default_mode: str) -> None:
    """MODE is resolved, then exported as SCOUT_MODE, then claude is launched — in that order.

    Order matters: an export before the assignment exports an empty string (which
    the hooks treat as unset), and an export after the launch never reaches the
    child at all.
    """
    text = (TEMPLATES / name).read_text(encoding="utf-8")

    mode_assign = text.index(f'MODE="${{SCOUT_FORCE_MODE:-{default_mode}}}"')
    export = text.index(EXPORT_LINE)
    launch = text.index(CLAUDE_LAUNCH)

    assert mode_assign < export, f"{name}: SCOUT_MODE must be exported after MODE is resolved"
    assert export < launch, f"{name}: SCOUT_MODE must be exported before claude is launched"
    assert text.count(EXPORT_LINE) == 1, f"{name}: exactly one SCOUT_MODE export expected"


@pytest.mark.parametrize(("name", "_default_mode"), RUNNERS)
def test_runner_still_exports_data_dir(name: str, _default_mode: str) -> None:
    """Guard: the engine-package vault resolution export (Plan 5+) stays in place."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    assert 'export SCOUT_DATA_DIR="$SCOUT_DIR"' in text
