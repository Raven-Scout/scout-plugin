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
    out: dict[str, Probe] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            raise ValueError(f"connector {name!r}: expected mapping, got {type(body).__name__}")
        if "primary" not in body:
            raise ValueError(f"connector {name!r}: missing 'primary'")
        primary = body["primary"]
        needs = list(body.get("needs_user_input") or [])

        if primary == "bash":
            if "command" not in body:
                raise ValueError(f"connector {name!r}: bash probe requires 'command'")
            out[name] = Probe(
                name=name,
                kind=ProbeKind.BASH,
                bash_command=body["command"],
                needs_user_input=needs,
            )
        else:
            chain = [primary] + list(body.get("fallbacks") or [])
            out[name] = Probe(
                name=name,
                kind=ProbeKind.MCP_TOOL,
                tool_chain=chain,
                needs_user_input=needs,
            )
    return out
