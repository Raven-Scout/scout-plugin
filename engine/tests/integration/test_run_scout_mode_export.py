"""Integration test: the rendered runners hand SCOUT_MODE to the telemetry hooks.

Renders each runner template (run-scout / run-dreaming / run-research) with a
stubbed ``scripts/claude-with-retry.sh`` that stands in for the ``claude`` child
process. The stub records the ``SCOUT_MODE`` it inherited and then drives the
real hooks exactly as Claude Code would from inside the session:

  - ``session-tool-log`` — the Stop hook registered in hooks/hooks.json — against
    a two-row session transcript, and
  - ``connector-log`` — the PostToolUse-shaped hook it replaced (#72), still
    exposed as ``scoutctl hook connector-log`` and the repro used in #192 —
    against one synthetic tool call.

Both gate on ``SCOUT_MODE``. The assertions are on the telemetry rows the hooks
write, not on the shell text.

Regression context (#192, #121): the runners set a *local* ``MODE`` and exported
only ``SCOUT_DATA_DIR``. The hooks short-circuit when ``SCOUT_MODE`` is unset
(interactive sessions must stay silent), so every scheduled run since the Plan
4/5 port looked interactive: no ``connector-calls-*.jsonl`` rows, therefore no
``connector-health-report`` data, therefore the outage alerter could never fire.
A connector was dark for 20 consecutive runs with exit code 0 and an empty
failures.log before anyone noticed. On the pre-fix templates the "mode seen" file
below reads ``<unset>`` and zero rows are written.

The static counterpart (export line ordering) lives in
``tests/unit/test_runner_exports_scout_mode.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
ENGINE_ROOT = REPO_ROOT / "engine"
TEMPLATES = REPO_ROOT / "templates"

RUN_TIMEOUT_S = 60

# (template, a dispatcher slot key that routes to this runner, default MODE when
#  SCOUT_FORCE_MODE is unset — i.e. an operator firing the runner by hand)
RUNNERS = [
    ("run-scout.sh.tmpl", "morning-briefing", "manual"),
    ("run-dreaming.sh.tmpl", "dreaming-nightly", "dreaming-manual"),
    ("run-research.sh.tmpl", "research", "research-manual"),
]

SESSION_ID = "sess-0001"

# One synthetic PostToolUse payload for connector-log — shape matches what Claude
# Code hands the hook. Anonymized per CLAUDE.md (stand-in Slack channel id).
MCP_TOOL = "mcp__plugin_slack_slack__slack_send_message"
TOOL_CALL = json.dumps(
    {
        "session_id": SESSION_ID,
        "tool_name": MCP_TOOL,
        "tool_input": {"channel": "C0123456789", "text": "hello"},
        "tool_response": {"isError": False},
    }
)

# A minimal session transcript for session-tool-log: one Bash tool_use paired
# with its tool_result. `gh` is a connector binary, so the row classifies as
# ``github`` — exercising the same labeller the health alerter reads.
TRANSCRIPT_ROWS = [
    {
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "toolu_01", "name": "Bash", "input": {"command": "gh pr list"}}],
        }
    },
    {
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_01", "content": "no open pull requests"}],
        }
    },
]

EXPECTED_ROWS = {MCP_TOOL: "mcp:plugin_slack_slack", "Bash": "github"}  # tool -> connector key

UNSET_SENTINEL = "<unset>"


def _render(tmpl: Path, scout_dir: Path) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    for placeholder, value in {
        "{{SCOUT_DIR}}": str(scout_dir),
        "{{CLAUDE_BIN}}": "/usr/bin/true",  # never reached — the retry wrapper is stubbed
        "{{INSTANCE_NAME_LOWER}}": "scout",
        "{{INSTANCE_NAME}}": "Scout",
        "{{MAX_BUDGET}}": "25",
        "{{USER_NAME}}": "Alex",
        "{{USER_SLACK_ID}}": "U0123456789",
    }.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text, f"unrendered placeholder left in {tmpl.name}"
    out = scout_dir / tmpl.name.removesuffix(".tmpl")
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    return out


def _stub_claude(scout_dir: Path) -> Path:
    """Stand-in for scripts/claude-with-retry.sh (and thereby the claude child).

    Writes the inherited SCOUT_MODE (or a sentinel) to ``scout-mode.seen``, then
    drives both real hooks: connector-log with one tool call, session-tool-log
    with a Stop payload pointing at a transcript. Runs them with the test's own
    interpreter and the in-tree engine on PYTHONPATH so the package under test
    is what gets exercised.
    """
    seen = scout_dir / "scout-mode.seen"
    transcript = scout_dir / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(row) + "\n" for row in TRANSCRIPT_ROWS), encoding="utf-8")
    stop_payload = json.dumps({"transcript_path": str(transcript), "session_id": SESSION_ID})

    py = sys.executable
    post_tool_use_hook = "import sys; from scout.hooks.connector_log import run; run(stdin=sys.stdin)"
    stop_hook = "import sys; from scout.hooks.session_tool_log import run; run(stdin=sys.stdin)"

    stub = scout_dir / "scripts" / "claude-with-retry.sh"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.write_text(
        "#!/bin/bash\n"
        'LOG_FILE="$1"\n'
        f'printf \'%s\\n\' "${{SCOUT_MODE-{UNSET_SENTINEL}}}" > "{seen}"\n'
        f'export PYTHONPATH="{ENGINE_ROOT}${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        f'printf \'%s\' \'{TOOL_CALL}\' | "{py}" -c "{post_tool_use_hook}" >> "$LOG_FILE" 2>&1\n'
        f'printf \'%s\' \'{stop_payload}\' | "{py}" -c "{stop_hook}" >> "$LOG_FILE" 2>&1\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return seen


def _run(script: Path, *, force_mode: str | None) -> subprocess.CompletedProcess:
    # Start from a clean slate for the three variables under test: the test
    # process itself may be running inside a Scout session.
    env = {k: v for k, v in os.environ.items() if k not in ("SCOUT_MODE", "SCOUT_FORCE_MODE", "SCOUT_DATA_DIR")}
    if force_mode is not None:
        env["SCOUT_FORCE_MODE"] = force_mode  # what schedule_tick / run_skill set
    return subprocess.run(
        [str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT_S,
    )


def _run_log(scout_dir: Path) -> str:
    logs = sorted((scout_dir / ".scout-logs").glob("*.log"))
    return "\n".join(log.read_text(encoding="utf-8") for log in logs)


def _telemetry_rows(scout_dir: Path) -> list[dict]:
    files = sorted((scout_dir / ".scout-logs").glob("connector-calls-*.jsonl"))
    return [json.loads(line) for f in files for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def _assert_both_hooks_wrote_rows(scout_dir: Path, *, mode: str) -> None:
    rows = _telemetry_rows(scout_dir)
    by_tool = {row["tool"]: row for row in rows}
    assert set(by_tool) == set(EXPECTED_ROWS), (
        f"expected one row from each hook (0 rows == they short-circuited); got {rows!r}; log:\n{_run_log(scout_dir)}"
    )
    assert len(rows) == len(EXPECTED_ROWS), f"each hook writes exactly one row; got {rows!r}"
    for tool, connector in EXPECTED_ROWS.items():
        row = by_tool[tool]
        assert row["mode"] == mode, f"{tool}: row must carry {mode!r}, got {row['mode']!r}"
        assert row["connector"] == connector
        assert row["session_id"] == SESSION_ID
        assert row["error"] is False


@pytest.mark.parametrize(("name", "slot_key", "_default_mode"), RUNNERS)
def test_dispatched_run_tags_telemetry_with_slot_key(
    tmp_path: Path, name: str, slot_key: str, _default_mode: str
) -> None:
    """Scheduler path: SCOUT_FORCE_MODE=<slot key> must surface to the hooks as SCOUT_MODE."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATES / name, scout_dir)
    seen = _stub_claude(scout_dir)

    result = _run(script, force_mode=slot_key)

    assert result.returncode == 0, result.stderr + _run_log(scout_dir)
    assert seen.read_text(encoding="utf-8").strip() == slot_key, "claude child must inherit SCOUT_MODE"
    _assert_both_hooks_wrote_rows(scout_dir, mode=slot_key)


@pytest.mark.parametrize(("name", "_slot_key", "default_mode"), RUNNERS)
def test_manual_run_tags_telemetry_with_runner_default(
    tmp_path: Path, name: str, _slot_key: str, default_mode: str
) -> None:
    """Operator path (no SCOUT_FORCE_MODE): the runner's own default mode is exported.

    Manual runs are still scheduled-style sessions — the skills launch them with
    ``nohup bash ~/Scout/run-*.sh`` — so they must be accounted for too, under the
    per-runner default the prompt and cost tracker already use.
    """
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATES / name, scout_dir)
    seen = _stub_claude(scout_dir)

    result = _run(script, force_mode=None)

    assert result.returncode == 0, result.stderr + _run_log(scout_dir)
    assert seen.read_text(encoding="utf-8").strip() == default_mode
    _assert_both_hooks_wrote_rows(scout_dir, mode=default_mode)


@pytest.mark.parametrize(("name", "slot_key", "_default_mode"), RUNNERS)
def test_data_dir_export_points_hooks_at_the_vault(
    tmp_path: Path, name: str, slot_key: str, _default_mode: str
) -> None:
    """Rows land under <vault>/.scout-logs — the runner's SCOUT_DATA_DIR export, not ~/Scout."""
    scout_dir = tmp_path / "Scout"
    scout_dir.mkdir()
    script = _render(TEMPLATES / name, scout_dir)
    _stub_claude(scout_dir)

    result = _run(script, force_mode=slot_key)

    assert result.returncode == 0, result.stderr + _run_log(scout_dir)
    files = list((scout_dir / ".scout-logs").glob("connector-calls-*.jsonl"))
    assert len(files) == 1, "both hooks must write into the rendered vault's day-file, not the default data dir"
