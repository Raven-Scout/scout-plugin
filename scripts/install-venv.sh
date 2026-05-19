#!/bin/bash
# Build the scout-engine venv at $PLUGIN_ROOT/.venv.
#
# Invoked by /scout-setup (with a 5-minute timeout) and as a manual fallback
# from /scout-update when the venv is missing or pinned to the wrong tree.
#
# Usage: bash <plugin-root>/scripts/install-venv.sh
#
# The plugin root is derived from this script's own location, so it works
# whether the script lives under ~/.claude/plugins/cache/...,
# ~/.claude/plugins/marketplaces/..., or a hand-cloned ~/scout-plugin.

set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PLUGIN_ROOT/.venv"

if [ ! -d "$PLUGIN_ROOT/engine" ]; then
    echo "error: engine directory not found at $PLUGIN_ROOT/engine" >&2
    exit 1
fi

# Pick a Python interpreter that satisfies engine[requires-python] = ">=3.11".
# Apple's bundled /usr/bin/python3 is 3.9.x on every macOS we support, so
# `python3` alone is unreliable — try the explicit minors first.
PYTHON=""
for candidate in python3.13 python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done
if [ -z "$PYTHON" ] && command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        PYTHON="python3"
    fi
fi
if [ -z "$PYTHON" ]; then
    cat >&2 <<EOF
error: no Python >= 3.11 found on PATH.
scout-engine requires Python 3.11 or newer. Install one of:
  macOS:   brew install python@3.13
  Debian:  sudo apt install python3.13 python3.13-venv
  pyenv:   pyenv install 3.13 && pyenv shell 3.13
then re-run: bash $PLUGIN_ROOT/scripts/install-venv.sh
EOF
    exit 1
fi

PYTHON_VERSION="$("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
echo "using $PYTHON ($PYTHON_VERSION)"

if [ -d "$VENV" ]; then
    echo "venv already exists at $VENV — recreating..."
    rm -rf "$VENV"
fi

echo "creating venv at $VENV..."
"$PYTHON" -m venv "$VENV"

echo "installing scout-engine in editable mode (this may take 30-60s)..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$PLUGIN_ROOT/engine[dev]"

if [ ! -x "$VENV/bin/scoutctl" ]; then
    echo "error: scoutctl not found at $VENV/bin/scoutctl after install" >&2
    exit 1
fi

echo "ok: venv ready at $VENV"
echo "verify: $VENV/bin/scoutctl version"
