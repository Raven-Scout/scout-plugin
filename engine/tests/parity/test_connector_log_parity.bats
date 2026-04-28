#!/usr/bin/env bats

setup() {
    SCOUT_DATA_DIR_BASH=$(mktemp -d)
    SCOUT_DATA_DIR_PYTHON=$(mktemp -d)
    BASH_HOOK="$HOME/Scout/hooks/connector-log.sh"
    PYTHON_HOOK="$BATS_TEST_DIRNAME/../../.venv/bin/scoutctl"
    SCOUT_MODE="morning-briefing"
    export SCOUT_DATA_DIR_BASH SCOUT_DATA_DIR_PYTHON SCOUT_MODE
    if [ ! -x "$BASH_HOOK" ]; then
        skip "bash hook not present at $BASH_HOOK (already migrated?)"
    fi
}

teardown() {
    rm -rf "$SCOUT_DATA_DIR_BASH" "$SCOUT_DATA_DIR_PYTHON"
}

@test "mcp tool: bash + python emit identical connector classification" {
    payload='{"session_id":"p1","tool_name":"mcp__plugin_slack_slack__slack_send_message","tool_response":{"isError":false}}'

    # Bash side
    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_BASH" \
        echo "$payload" | "$BASH_HOOK"

    # Python side
    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_PYTHON" \
        echo "$payload" | "$PYTHON_HOOK" hook connector-log

    bash_row=$(cat "$SCOUT_DATA_DIR_BASH"/.scout-logs/connector-calls-*.jsonl)
    python_row=$(cat "$SCOUT_DATA_DIR_PYTHON"/.scout-logs/connector-calls-*.jsonl)

    bash_connector=$(echo "$bash_row" | jq -r '.connector')
    python_connector=$(echo "$python_row" | jq -r '.connector')
    [ "$bash_connector" = "$python_connector" ]
    [ "$bash_connector" = "mcp:plugin_slack_slack" ]
}

@test "bash tool with gh command: both emit github" {
    payload='{"session_id":"p2","tool_name":"Bash","tool_input":{"command":"gh pr list"},"tool_response":{"returncode":0}}'

    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_BASH" echo "$payload" | "$BASH_HOOK"
    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_PYTHON" echo "$payload" | "$PYTHON_HOOK" hook connector-log

    bash_connector=$(jq -r '.connector' "$SCOUT_DATA_DIR_BASH"/.scout-logs/connector-calls-*.jsonl)
    python_connector=$(jq -r '.connector' "$SCOUT_DATA_DIR_PYTHON"/.scout-logs/connector-calls-*.jsonl)
    [ "$bash_connector" = "$python_connector" ]
    [ "$bash_connector" = "github" ]
}

@test "error tool_response: both record error=true with truncated snippet" {
    payload='{"session_id":"p3","tool_name":"mcp__claude_ai_Gmail__search","tool_response":{"isError":true,"error":"auth expired"}}'

    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_BASH" echo "$payload" | "$BASH_HOOK"
    SCOUT_DATA_DIR="$SCOUT_DATA_DIR_PYTHON" echo "$payload" | "$PYTHON_HOOK" hook connector-log

    bash_err=$(jq -r '.error' "$SCOUT_DATA_DIR_BASH"/.scout-logs/connector-calls-*.jsonl)
    python_err=$(jq -r '.error' "$SCOUT_DATA_DIR_PYTHON"/.scout-logs/connector-calls-*.jsonl)
    [ "$bash_err" = "$python_err" ]
    [ "$bash_err" = "true" ]
}
