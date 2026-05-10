#!/bin/bash
# Fallback installer for ~/scout-plugin/.venv — invoked manually if
# /scout-setup's automatic venv install times out.
#
# Usage: bash ~/scout-plugin/scripts/install-venv.sh
#
# After this completes, retry /scout-setup or run /scout-setup --skip-venv-install.

set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PLUGIN_ROOT/.venv"

if [ ! -d "$PLUGIN_ROOT/engine" ]; then
    echo "error: engine directory not found at $PLUGIN_ROOT/engine" >&2
    exit 1
fi

if [ -d "$VENV" ]; then
    echo "venv already exists at $VENV — recreating..."
    rm -rf "$VENV"
fi

echo "creating venv at $VENV..."
python3 -m venv "$VENV"

echo "installing scout-engine in editable mode (this may take 30-60s)..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$PLUGIN_ROOT/engine[dev]"

if [ ! -x "$VENV/bin/scoutctl" ]; then
    echo "error: scoutctl not found at $VENV/bin/scoutctl after install" >&2
    exit 1
fi

echo "ok: venv ready at $VENV"
echo "verify: $VENV/bin/scoutctl version"
