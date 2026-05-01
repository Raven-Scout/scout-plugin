#!/usr/bin/env bats

# Parity test: bash connector-health-report.sh vs Python scoutctl connector-health-report.
# Both run against an identical synthetic .scout-logs/ tree under tmp HOMEs;
# the resulting connector-health.md files must agree on the connector rows that
# both implementations cover (bash hardcodes 8 connectors; Python's YAML adds
# 2 more — WhatsApp + Telegram — plus an outbound-only filter, so the parity
# check is the *intersection* of the connector roster).

setup() {
    BASH_SCRIPT="$HOME/Scout/scripts/connector-health-report.sh"
    PYTHON_CLI="$BATS_TEST_DIRNAME/../../.venv/bin/scoutctl"
    FIXTURE="$BATS_TEST_DIRNAME/../fixtures/connector-calls-2026-04-22-fixed.jsonl"
    if [ ! -x "$BASH_SCRIPT" ]; then
        skip "bash script not present at $BASH_SCRIPT (already migrated?)"
    fi
    if [ ! -f "$FIXTURE" ]; then
        skip "fixture missing at $FIXTURE"
    fi
}

# Build a synthetic Scout tree under $1 with the fixture JSONL copied in.
build_scout_tree() {
    local home_root="$1"
    local scout="$home_root/Scout"
    mkdir -p "$scout/.scout-logs" "$scout/.scout-cache" "$scout/knowledge-base"
    cp "$FIXTURE" "$scout/.scout-logs/connector-calls-2026-04-22.jsonl"
}

# Strip lines that legitimately differ between the two implementations:
#   - timestamp lines (Last updated)
#   - rows for connectors only present in one (WhatsApp, Telegram, etc.)
#   - the prose "How this works" / "Alert rules" section (rewritten in Python port)
# Keeps the alert section + the matrix rows for the 8 shared connectors.
strip_volatile() {
    sed -E \
        -e '/\*\*Last updated:\*\*/d' \
        -e '/\*\*Window:\*\*/d' \
        -e '/^\| WhatsApp /d' \
        -e '/^\| Telegram /d' \
        -e '/^## How this works/,$d'
}

@test "parity: shared-connector matrix rows agree on 3-session fixture" {
    BASH_HOME=$(mktemp -d)
    PY_HOME=$(mktemp -d)

    build_scout_tree "$BASH_HOME"
    build_scout_tree "$PY_HOME"

    # Bash: HOME-redirected so SCOUT_DIR=$HOME/Scout points at tmp.
    env HOME="$BASH_HOME" TZ="America/New_York" "$BASH_SCRIPT" >/dev/null

    # Python: SCOUT_DATA_DIR points at the equivalent tmp Scout dir.
    env SCOUT_DATA_DIR="$PY_HOME/Scout" TZ="America/New_York" "$PYTHON_CLI" connector-health-report >/dev/null

    bash_md="$BASH_HOME/Scout/knowledge-base/connector-health.md"
    py_md="$PY_HOME/Scout/knowledge-base/connector-health.md"

    [ -f "$bash_md" ]
    [ -f "$py_md" ]

    bash_filtered=$(strip_volatile < "$bash_md")
    py_filtered=$(strip_volatile < "$py_md")

    diff <(echo "$bash_filtered") <(echo "$py_filtered")

    rm -rf "$BASH_HOME" "$PY_HOME"
}

@test "parity: Slack row identical (5 ok across 3 sessions)" {
    BASH_HOME=$(mktemp -d)
    PY_HOME=$(mktemp -d)
    build_scout_tree "$BASH_HOME"
    build_scout_tree "$PY_HOME"

    env HOME="$BASH_HOME" TZ="America/New_York" "$BASH_SCRIPT" >/dev/null
    env SCOUT_DATA_DIR="$PY_HOME/Scout" TZ="America/New_York" "$PYTHON_CLI" connector-health-report >/dev/null

    bash_slack=$(grep -E '^\| Slack \|' "$BASH_HOME/Scout/knowledge-base/connector-health.md")
    py_slack=$(grep -E '^\| Slack \|' "$PY_HOME/Scout/knowledge-base/connector-health.md")
    [ "$bash_slack" = "$py_slack" ]

    rm -rf "$BASH_HOME" "$PY_HOME"
}

@test "parity: no alerts on healthy fixture (both produce no Active Alerts section)" {
    BASH_HOME=$(mktemp -d)
    PY_HOME=$(mktemp -d)
    build_scout_tree "$BASH_HOME"
    build_scout_tree "$PY_HOME"

    env HOME="$BASH_HOME" TZ="America/New_York" "$BASH_SCRIPT" >/dev/null
    env SCOUT_DATA_DIR="$PY_HOME/Scout" TZ="America/New_York" "$PYTHON_CLI" connector-health-report >/dev/null

    if grep -q "Active Alerts" "$BASH_HOME/Scout/knowledge-base/connector-health.md"; then
        echo "bash unexpectedly produced Active Alerts on healthy fixture" >&2
        return 1
    fi
    if grep -q "Active Alerts" "$PY_HOME/Scout/knowledge-base/connector-health.md"; then
        echo "python unexpectedly produced Active Alerts on healthy fixture" >&2
        return 1
    fi

    rm -rf "$BASH_HOME" "$PY_HOME"
}
