#!/usr/bin/env bash
# Cut a release: bump the canonical version, propagate to all four manifests,
# promote the CHANGELOG, run the full check suite, then commit + tag + push.
#
# Usage: scripts/release.sh [patch|minor|major|X.Y.Z]   (default: patch)
set -euo pipefail

LEVEL="${1:-patch}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/engine/.venv/bin/python"
cd "$ROOT"

# --- preconditions ---
[ -x "$PY" ] || { echo "error: engine venv missing — run scripts/install-venv.sh" >&2; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "error: releases are cut from main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "error: working tree not clean" >&2; exit 1; }
git fetch -q origin
[ "$(git rev-list --count origin/main..HEAD)" = "0" ] && [ "$(git rev-list --count HEAD..origin/main)" = "0" ] \
    || { echo "error: local main not in sync with origin/main" >&2; exit 1; }

# --- bump + propagate + changelog ---
"$PY" -m scout.scripts.versioning check >/dev/null   # refuse if already drifted
NEW="$("$PY" -m scout.scripts.versioning bump "$LEVEL")"
TODAY="$(TZ=America/New_York date '+%Y-%m-%d')"
"$PY" - "$NEW" "$TODAY" <<'EOF'
import sys
from scout.scripts import versioning
versioning.promote_changelog(version=sys.argv[1], date=sys.argv[2])
EOF
echo "Releasing v$NEW"

# --- never tag a red tree ---
( cd engine && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests \
    && .venv/bin/mypy scout && .venv/bin/python -m pytest -q )

# --- commit + tag + push ---
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json \
        engine/pyproject.toml engine/scout/__init__.py CHANGELOG.md
git commit -m "release: v$NEW"
git tag "v$NEW"
git push origin main
git push origin "v$NEW"
echo "Pushed v$NEW + tag. Release workflow will publish the GitHub Release."
