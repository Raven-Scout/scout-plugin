#!/usr/bin/env bash
# Scout one-command installer.
#   curl -fsSL https://raw.githubusercontent.com/Raven-Scout/scout-plugin/main/install.sh | bash
# Sets up the PLUGIN + ENGINE. The interactive vault is then created with /scout-setup.
#
# Flags: --check  (verify preconditions only; make no changes)
set -euo pipefail

MARKETPLACE="Raven-Scout/scout-plugin"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

have() { command -v "$1" >/dev/null 2>&1; }
fail() { echo "error: $*" >&2; exit 1; }

# --- preconditions ---
have claude || fail "Claude Code CLI not found. Install it first: https://docs.claude.com/claude-code (then re-run this)."
have git    || fail "git is required."
if ! have uv; then
  echo "uv not found — installing (https://docs.astral.sh/uv)…"
  [ "$CHECK_ONLY" = 1 ] || curl -fsSL https://astral.sh/uv/install.sh | sh
fi

if [ "$CHECK_ONLY" = 1 ]; then
  echo "preconditions OK (claude, git present; uv $(have uv && echo present || echo 'will-install'))"
  exit 0
fi

# --- plugin + engine ---
echo "Adding the Scout marketplace…"
claude plugin marketplace add "$MARKETPLACE" 2>/dev/null || claude plugin marketplace update scout-plugin
echo "Installing the Scout plugin…"
claude plugin install scout@scout-plugin

# Resolve the installed plugin root and build its engine venv.
ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c "import sys,json;print(next(p['installPath'] for m in json.load(sys.stdin).get('plugins',{}).values() for p in m if 'scout-plugin' in p['installPath']))" 2>/dev/null || true)"
if [ -n "$ROOT" ] && [ -f "$ROOT/scripts/install-venv.sh" ]; then
  echo "Setting up the engine venv…"
  bash "$ROOT/scripts/install-venv.sh"
fi

cat <<'DONE'

✅ Scout plugin + engine installed.

Next step — create your vault (interactive: detects your connectors, collects
your details, sets the schedule):

    Open Claude Code and run:  /scout-setup

DONE
