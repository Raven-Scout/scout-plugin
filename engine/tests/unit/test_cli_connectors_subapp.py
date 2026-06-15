"""CLI tests for `scoutctl connectors probe-registry`."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from scout.cli import app

runner = CliRunner()


def _overlay(data_dir: Path, body: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "connector-probes.local.yaml").write_text(dedent(body))


def test_probe_registry_json_lists_shipped_connectors():
    result = runner.invoke(app, ["connectors", "probe-registry", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    # Shipped registry ships these (templates/connector-probes.yaml).
    assert "slack" in data
    assert "github" in data
    assert data["slack"]["kind"] == "mcp_tool"
    assert data["github"]["kind"] == "bash"


def test_probe_registry_json_includes_overlay(tmp_path, monkeypatch):
    """A vault overlay adds a connector the wizard will then probe (#97)."""
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        devin:
          primary: mcp__devin__devin_session_search
          fallbacks: []
        """,
    )
    # SCOUT_DATA_DIR steers resolve_registry's default data_dir at the
    # overlay; the shipped half comes from the real repo templates/.
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    result = runner.invoke(app, ["connectors", "probe-registry", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert "devin" in data
    assert data["devin"]["tool_chain"] == ["mcp__devin__devin_session_search"]
    assert "slack" in data  # shipped still present


def test_probe_registry_default_is_tab_separated():
    result = runner.invoke(app, ["connectors", "probe-registry"])
    assert result.exit_code == 0, result.stdout + result.stderr
    first = next(line for line in result.stdout.splitlines() if line.strip())
    assert not first.startswith("{")  # not JSON
    parts = first.split("\t")
    assert len(parts) == 3
    assert parts[1] in ("bash", "mcp_tool")
