#!/usr/bin/env bash
# Scout one-command installer.
#   curl -fsSL https://raw.githubusercontent.com/Raven-Scout/scout-plugin/main/install.sh | bash
# Sets up the PLUGIN + ENGINE. The interactive vault is then created with /scout-setup.
#
# Flags: --check  (verify preconditions only; make no changes)
set -euo pipefail

MARKETPLACE="Raven-Scout/scout-plugin"
PLUGIN_ID="scout@scout-plugin"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

# The native Claude Code and uv installers both put binaries in ~/.local/bin,
# which a fresh login shell may not have on PATH yet.
export PATH="$HOME/.local/bin:$PATH"

have() { command -v "$1" >/dev/null 2>&1; }
fail() { echo "error: $*" >&2; exit 1; }

# --- preconditions ---
# On a fresh Mac, /usr/bin/git and /usr/bin/python3 exist but are stubs that pop
# the Command Line Tools installer when first run — so `command -v git` passes and
# the dialog would interrupt us mid-install. Check the real thing up front.
if [ "$(uname -s)" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
  [ "$CHECK_ONLY" = 1 ] || xcode-select --install >/dev/null 2>&1 || true
  fail "Apple's Command Line Tools are required (they provide git).
       A system dialog should have opened — click Install, wait for it to finish,
       then re-run this installer."
fi
have claude || fail "Claude Code not found. Install it first: https://docs.claude.com/claude-code
       then run \`claude\` once to sign in, and re-run this installer."
have git    || fail "git is required."
if ! have uv; then
  echo "uv not found — installing (https://docs.astral.sh/uv)…"
  [ "$CHECK_ONLY" = 1 ] || curl -fsSL https://astral.sh/uv/install.sh | sh
fi

if [ "$CHECK_ONLY" = 1 ]; then
  echo "preconditions OK (claude, git present; uv $(have uv && echo present || echo 'will-install'))"
  exit 0
fi
have uv || fail "uv install did not land on PATH (expected ~/.local/bin/uv). Open a new terminal and re-run."

# --- plugin + engine ---
echo "Adding the Scout marketplace…"
claude plugin marketplace add "$MARKETPLACE" 2>/dev/null || claude plugin marketplace update scout-plugin
echo "Installing the Scout plugin…"
claude plugin install "$PLUGIN_ID"
# On a re-run, install leaves the old version registered; update switches it.
claude plugin update "$PLUGIN_ID" >/dev/null 2>&1 || true

# Resolve the installed plugin root. `claude plugin list --json` has emitted both a
# top-level list and a {"plugins": {...}} map across versions — accept either.
# uv provides the interpreter, so this works with no system Python.
ROOT="$(claude plugin list --json 2>/dev/null | uv run --no-project --quiet python -c '
import json, sys
data = json.load(sys.stdin)
entries = data if isinstance(data, list) else [p for ps in data.get("plugins", {}).values() for p in ps]
paths = [p["installPath"] for p in entries if p.get("id") == sys.argv[1] or "/scout-plugin/" in p.get("installPath", "")]
print(paths[0] if paths else "")
' "$PLUGIN_ID" 2>/dev/null || true)"
# Fallback: the marketplace cache layout (~/.claude/plugins/cache/scout-plugin/scout/<version>).
if [ -z "$ROOT" ]; then
  ROOT="$(ls -d "$HOME"/.claude/plugins/cache/scout-plugin/scout/*/ 2>/dev/null | sort -V | tail -1 || true)"
  ROOT="${ROOT%/}"
fi
[ -n "$ROOT" ] && [ -f "$ROOT/scripts/install-venv.sh" ] \
  || fail "the Scout plugin installed, but its folder could not be located.
       Run \`claude plugin list\` to confirm it is installed, then re-run this installer."

echo "Setting up the engine (one-time, ~1 minute)…"
bash "$ROOT/scripts/install-venv.sh" \
  || fail "engine setup failed (see the output above). Retry with:
       bash \"$ROOT/scripts/install-venv.sh\""
"$ROOT/.venv/bin/scoutctl" --help >/dev/null 2>&1 \
  || fail "engine installed but scoutctl does not run. Retry with:
       bash \"$ROOT/scripts/install-venv.sh\""

cat <<'DONE'

✅ Scout plugin + engine installed.

Next step — create your vault (interactive: detects your connectors, collects
your details, sets the schedule):

    Open Claude Code and run:  /scout-setup

DONE
