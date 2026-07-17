"""Loader for templates/connector-probes.yaml.

The /scout-setup wizard reads this registry and tries each connector's
``primary`` tool, falling through to ``fallbacks`` until one succeeds
(or all fail, in which case the connector is marked disabled).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

from scout.errors import ConfigError

# Legacy probe/config keys and their canonical replacements. The probe key is
# what gets written into scout-config.yaml `connectors.enabled`, and phase
# sections are gated on `requires:` matching it exactly — a stale key in an
# existing vault would silently drop every section it gates (#172: `gmail` vs
# the phase key `email`).
CONNECTOR_KEY_ALIASES: dict[str, str] = {
    "gmail": "email",
}


def normalize_connector_keys(keys: set[str]) -> set[str]:
    """Map legacy connector keys to their canonical names. Idempotent."""
    return {CONNECTOR_KEY_ALIASES.get(k, k) for k in keys}


class ProbeKind(Enum):
    MCP_TOOL = "mcp_tool"  # primary is an MCP tool name to call
    BASH = "bash"  # primary is "bash"; command is the shell command


@dataclass(frozen=True)
class Probe:
    name: str
    kind: ProbeKind
    tool_chain: list[str] = field(default_factory=list)  # MCP_TOOL only
    bash_command: str = ""  # BASH only
    needs_user_input: list[str] = field(default_factory=list)


def load_registry(path: Path) -> dict[str, Probe]:
    """Parse connector-probes.yaml into typed Probe objects."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"connector-probes.yaml must be a YAML mapping at the top level, got {type(raw).__name__}")
    out: dict[str, Probe] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"connector {name!r}: expected mapping, got {type(body).__name__}")
        if "primary" not in body:
            raise ValueError(f"connector {name!r}: missing 'primary'")
        primary = body["primary"]
        raw_needs = body.get("needs_user_input") or []
        if isinstance(raw_needs, str):
            raise ValueError(f"connector {name!r}: 'needs_user_input' must be a list, got string")
        needs = list(raw_needs)

        if primary == "bash":
            if "command" not in body:
                raise ValueError(f"connector {name!r}: bash probe requires 'command'")
            if not body["command"]:
                raise ValueError(f"connector {name!r}: bash probe 'command' must not be empty")
            out[name] = Probe(
                name=name,
                kind=ProbeKind.BASH,
                bash_command=body["command"],
                needs_user_input=needs,
            )
        else:
            raw_fallbacks = body.get("fallbacks") or []
            if isinstance(raw_fallbacks, str):
                raise ValueError(f"connector {name!r}: 'fallbacks' must be a list, got string")
            chain = [primary] + list(raw_fallbacks)
            out[name] = Probe(
                name=name,
                kind=ProbeKind.MCP_TOOL,
                tool_chain=chain,
                needs_user_input=needs,
            )
    return out


def _default_plugin_root() -> Path:
    """Plugin root = the dir that contains the engine venv and templates/.

    Derived from the running package location, mirroring
    install_schedule_plist.resolve_scoutctl_bin().
    """
    import scout

    return Path(scout.__file__).parent.parent.parent


def resolve_registry(
    *,
    plugin_root: Path | None = None,
    data_dir: Path | None = None,
) -> dict[str, Probe]:
    """Merge the shipped probe registry with the optional user overlay.

    Shipped: ``<plugin_root>/templates/connector-probes.yaml`` (required).
    Overlay: ``<data_dir>/connector-probes.local.yaml`` (optional).
    Union of the two; on a key collision the overlay entry wins, letting a
    user repoint a shipped probe or add new connectors that survive plugin
    updates (#97).

    Raises ConfigError (naming the offending file) if the shipped registry
    is missing/invalid or the overlay is invalid. The overlay being absent
    or empty is normal and leaves the shipped set unchanged.
    """
    if plugin_root is None:
        plugin_root = _default_plugin_root()
    if data_dir is None:
        from scout import paths

        data_dir = paths.data_dir()

    shipped_path = plugin_root / "templates" / "connector-probes.yaml"
    if not shipped_path.exists():
        raise ConfigError(f"shipped connector-probes.yaml not found at {shipped_path}")
    try:
        merged = dict(load_registry(shipped_path))
    except (ValueError, yaml.YAMLError) as e:
        raise ConfigError(f"shipped connector-probes.yaml is invalid: {e}") from e

    overlay_path = data_dir / "connector-probes.local.yaml"
    if overlay_path.exists():
        try:
            overlay = load_registry(overlay_path)
        except (ValueError, yaml.YAMLError) as e:
            raise ConfigError(f"{overlay_path.name} is invalid: {e}") from e
        merged.update(overlay)  # overlay wins on key collision

    return merged
