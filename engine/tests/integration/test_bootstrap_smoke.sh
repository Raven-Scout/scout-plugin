#!/bin/bash
# Integration smoke test for scoutctl bootstrap install + upgrade.
# Runs against a temp vault — no host pollution.
#
# Usage: bash ~/scout-plugin/engine/tests/integration/test_bootstrap_smoke.sh

set -euo pipefail

TEST_VAULT=$(mktemp -d -t scout-smoke-XXXXXX)
trap 'rm -rf "$TEST_VAULT"' EXIT

SCOUTCTL="${SCOUTCTL:-$HOME/scout-plugin-plan-8/.venv/bin/scoutctl}"

if [ ! -x "$SCOUTCTL" ]; then
    echo "FAIL: scoutctl not found at $SCOUTCTL" >&2
    exit 1
fi

echo "=== install ==="
SCOUT_DATA_DIR="$TEST_VAULT" "$SCOUTCTL" bootstrap install \
    --no-jobs \
    --skip-claude \
    --instance-name "TestScout" \
    --user-name "Test User" \
    --user-email "test@example.com" \
    --timezone "America/New_York" \
    --platform "macos" \
    || true   # doctor may report yellow on no-jobs

# Required directory tree
test -d "$TEST_VAULT/knowledge-base" || { echo "FAIL: knowledge-base"; exit 1; }
test -d "$TEST_VAULT/action-items" || { echo "FAIL: action-items"; exit 1; }
test -d "$TEST_VAULT/.scout-state" || { echo "FAIL: .scout-state"; exit 1; }

# Cat-1 files
test -s "$TEST_VAULT/scripts/heartbeat.sh" || { echo "FAIL: heartbeat.sh empty/missing"; exit 1; }
test -s "$TEST_VAULT/knowledge-base/ontology/parser.py" || { echo "FAIL: parser.py"; exit 1; }
test -s "$TEST_VAULT/action-items/render.py" || { echo "FAIL: render.py"; exit 1; }

# Cat-4 assembled + snapshots
for kind in SKILL DREAMING RESEARCH; do
    test -s "$TEST_VAULT/$kind.md" || { echo "FAIL: $kind.md"; exit 1; }
    test -s "$TEST_VAULT/.scout-state/last-assembled/$kind.md" || { echo "FAIL: snapshot $kind.md"; exit 1; }
done

# Schedule
test -s "$TEST_VAULT/.scout-state/schedule.yaml" || { echo "FAIL: schedule.yaml"; exit 1; }
SCOUT_DATA_DIR="$TEST_VAULT" "$SCOUTCTL" schedule list >/dev/null || { echo "FAIL: scoutctl schedule list"; exit 1; }

# Version stamp
grep -q "version_at_last_setup" "$TEST_VAULT/scout-config.yaml" || { echo "FAIL: version_at_last_setup"; exit 1; }

echo ""
echo "=== upgrade (idempotent) ==="
SCOUT_DATA_DIR="$TEST_VAULT" "$SCOUTCTL" bootstrap upgrade --no-jobs --skip-claude || true

# Should still pass all checks
for kind in SKILL DREAMING RESEARCH; do
    test -s "$TEST_VAULT/$kind.md" || { echo "FAIL: post-upgrade $kind.md"; exit 1; }
done

echo ""
echo "=== doctor ==="
SCOUT_DATA_DIR="$TEST_VAULT" "$SCOUTCTL" bootstrap doctor --no-jobs

echo ""
echo "PASS: bootstrap smoke test"
