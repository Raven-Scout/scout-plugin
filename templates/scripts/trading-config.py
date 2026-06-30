#!/usr/bin/env python3
"""Read/write the Agentic Trading config — single source of truth for the master switch.

Usage:
    trading-config.py get <dotted.key>
    trading-config.py set <dotted.key> <value>

Resolves the vault from $SCOUT_DATA_DIR (falls back to ~/Scout). NOTE: `set`
round-trips the YAML through PyYAML, which strips comments — field docs live in
the agentic-trading project charter, not inline.
"""
import os
import sys
from pathlib import Path

import yaml

VAULT = Path(os.environ.get("SCOUT_DATA_DIR", Path.home() / "Scout"))
CONFIG = VAULT / "knowledge-base" / "projects" / "agentic-trading" / "config.yaml"


def _load():
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def _coerce(v):
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: trading-config.py get|set <dotted.key> [value]")
    op, key = sys.argv[1], sys.argv[2]
    data = _load()
    parts = key.split(".")
    node = data
    for p in parts[:-1]:
        node = node[p]
    if op == "get":
        val = node[parts[-1]]
        print(str(val).lower() if isinstance(val, bool) else val)
    elif op == "set":
        if len(sys.argv) < 4:
            sys.exit("set requires a value")
        node[parts[-1]] = _coerce(sys.argv[3])
        with open(CONFIG, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    else:
        sys.exit(f"unknown op: {op}")


if __name__ == "__main__":
    main()
