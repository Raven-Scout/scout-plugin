"""Engine capability manifest.

The manifest is the contract between scout-plugin (engine) and scout-app.
It declares which features and CLI subcommands this engine version
supports. Scout-app reads it at launch and refuses (with a helpful
banner) to use features the engine cannot provide.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scout import __version__

ENGINE_DIR = Path(__file__).parent.parent


@dataclass
class EngineManifest:
    """Serializable capability manifest."""

    version: str
    schema_version: int
    features: dict[str, bool] = field(default_factory=dict)
    subcommands: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _list_subcommands() -> list[str]:
    """Enumerate top-level subcommand names from the live Typer app.

    Local imports keep this off scoutctl's hot path: scout.cli does
    not import scout.manifest at module top, so the click-graph build
    happens only when someone explicitly invokes
    `scoutctl manifest build/show`.

    Returns names sorted for stable JSON output.
    """
    import typer.main

    from scout.cli import app as cli_app

    click_group = typer.main.get_command(cli_app)
    commands = getattr(click_group, "commands", {})
    return sorted(commands.keys())


def build_manifest() -> EngineManifest:
    """Construct the manifest from package state.

    Feature flags reflect what this version of the engine promises.
    Plans 2 and 4 flip individual flags to True as subsystems land.
    Subcommands are derived from the live Typer app — adding a
    command in scout.cli automatically updates the manifest.
    """
    return EngineManifest(
        version=__version__,
        schema_version=1,
        features={
            "session_tokens_v1": True,  # Plan 4
            "connector_health_v1": True,  # Plan 4
            "action_items_cli_v1": True,  # Plan 2
            "kb_ontology_v1": True,  # Plan 2
            "tui_v1": True,  # Plan 2
            "schedule_v2": True,  # Plan 5
        },
        subcommands=_list_subcommands(),
    )


def write_manifest(path: Path | None = None) -> Path:
    """Write the manifest to disk. Returns the path written."""
    target = path or (ENGINE_DIR / "manifest.json")
    target.write_text(build_manifest().to_json() + "\n")
    return target
