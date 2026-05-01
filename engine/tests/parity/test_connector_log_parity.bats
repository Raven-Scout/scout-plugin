#!/usr/bin/env bats

setup() {
    BASH_HOOK="$HOME/Scout/hooks/connector-log.sh"
    PYTHON_HOOK="$BATS_TEST_DIRNAME/../../.venv/bin/scoutctl"
    SCOUT_MODE="morning-briefing"
    export SCOUT_MODE
    if [ ! -x "$BASH_HOOK" ]; then
        skip "bash hook not present at $BASH_HOOK (already migrated?)"
    fi
}

@test "mcp tool: bash + python emit identical connector classification" {
    payload='{"session_id":"p1","tool_name":"mcp__plugin_slack_slack__slack_send_message","tool_response":{"isError":false}}'

    BASH_HOME=$(mktemp -d)
    PYTHON_HOME=$(mktemp -d)
    mkdir -p "$BASH_HOME/Scout"
    mkdir -p "$PYTHON_HOME/Scout"

    # Bash hook: HOME-redirected so its hard-coded ~/Scout/.scout-logs lands in tmp.
    env HOME="$BASH_HOME" SCOUT_MODE="$SCOUT_MODE" "$BASH_HOOK" <<<"$payload"

    # Python hook: SCOUT_DATA_DIR explicitly points at the python tmp Scout dir.
    env SCOUT_DATA_DIR="$PYTHON_HOME/Scout" SCOUT_MODE="$SCOUT_MODE" "$PYTHON_HOOK" hook connector-log <<<"$payload"

    bash_row=$(cat "$BASH_HOME"/Scout/.scout-logs/connector-calls-*.jsonl)
    python_row=$(cat "$PYTHON_HOME"/Scout/.scout-logs/connector-calls-*.jsonl)
    bash_connector=$(echo "$bash_row" | jq -r '.connector')
    python_connector=$(echo "$python_row" | jq -r '.connector')
    [ "$bash_connector" = "$python_connector" ]
    [ "$bash_connector" = "mcp:plugin_slack_slack" ]

    rm -rf "$BASH_HOME" "$PYTHON_HOME"
}

@test "bash tool with gh command: both emit github" {
    payload='{"session_id":"p2","tool_name":"Bash","tool_input":{"command":"gh pr list"},"tool_response":{"returncode":0}}'

    BASH_HOME=$(mktemp -d)
    PYTHON_HOME=$(mktemp -d)
    mkdir -p "$BASH_HOME/Scout"
    mkdir -p "$PYTHON_HOME/Scout"

    env HOME="$BASH_HOME" SCOUT_MODE="$SCOUT_MODE" "$BASH_HOOK" <<<"$payload"
    env SCOUT_DATA_DIR="$PYTHON_HOME/Scout" SCOUT_MODE="$SCOUT_MODE" "$PYTHON_HOOK" hook connector-log <<<"$payload"

    bash_connector=$(jq -r '.connector' "$BASH_HOME"/Scout/.scout-logs/connector-calls-*.jsonl)
    python_connector=$(jq -r '.connector' "$PYTHON_HOME"/Scout/.scout-logs/connector-calls-*.jsonl)
    [ "$bash_connector" = "$python_connector" ]
    [ "$bash_connector" = "github" ]

    rm -rf "$BASH_HOME" "$PYTHON_HOME"
}

@test "error tool_response: both record error=true with truncated snippet" {
    payload='{"session_id":"p3","tool_name":"mcp__claude_ai_Gmail__search","tool_response":{"isError":true,"error":"auth expired"}}'

    BASH_HOME=$(mktemp -d)
    PYTHON_HOME=$(mktemp -d)
    mkdir -p "$BASH_HOME/Scout"
    mkdir -p "$PYTHON_HOME/Scout"

    env HOME="$BASH_HOME" SCOUT_MODE="$SCOUT_MODE" "$BASH_HOOK" <<<"$payload"
    env SCOUT_DATA_DIR="$PYTHON_HOME/Scout" SCOUT_MODE="$SCOUT_MODE" "$PYTHON_HOOK" hook connector-log <<<"$payload"

    bash_err=$(jq -r '.error' "$BASH_HOME"/Scout/.scout-logs/connector-calls-*.jsonl)
    python_err=$(jq -r '.error' "$PYTHON_HOME"/Scout/.scout-logs/connector-calls-*.jsonl)
    [ "$bash_err" = "$python_err" ]
    [ "$bash_err" = "true" ]

    rm -rf "$BASH_HOME" "$PYTHON_HOME"
}
