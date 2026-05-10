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


class ProbeKind(Enum):
    MCP_TOOL = "mcp_tool"   # primary is an MCP tool name to call
    BASH = "bash"           # primary is "bash"; command is the shell command


@dataclass(frozen=True)
class Probe:
    name: str
    kind: ProbeKind
    tool_chain: list[str] = field(default_factory=list)  # MCP_TOOL only
    bash_command: str = ""                                # BASH only
    needs_user_input: list[str] = field(default_factory=list)


def load_registry(path: Path) -> dict[str, Probe]:
    """Parse connector-probes.yaml into typed Probe objects."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"connector-probes.yaml must be a YAML mapping at the top level, "
            f"got {type(raw).__name__}"
        )
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
