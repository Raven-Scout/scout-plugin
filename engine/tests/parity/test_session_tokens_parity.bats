#!/usr/bin/env bats
# Parity tests for sum-session-tokens.sh ↔ scoutctl hook session-tokens.
# Verifies the JSONL row's numeric/string fields match (excluding ts/ts_et,
# which vary by wall-clock). cost_usd is compared within 1e-6 tolerance to
# absorb float arithmetic differences between jq and Python.

setup() {
    BASH_HOOK="$HOME/Scout/scripts/sum-session-tokens.sh"
    PYTHON_HOOK="$BATS_TEST_DIRNAME/../../.venv/bin/scoutctl"
    export SCOUT_MODE="morning-briefing"
    if [ ! -x "$BASH_HOOK" ]; then
        skip "bash hook not present at $BASH_HOOK (already migrated?)"
    fi
}

# Compare two JSONL files on every field except ts / ts_et. cost_usd uses
# a 1e-6 absolute tolerance; everything else must compare exactly.
assert_rows_equal() {
    local bash_row="$1"
    local py_row="$2"
    local fields=(session_id scout_mode cwd primary_model input_tokens output_tokens \
                  cache_read_input_tokens cache_creation_input_tokens \
                  num_turns duration_ms error)
    for f in "${fields[@]}"; do
        local b p
        b=$(echo "$bash_row" | jq -r ".$f")
        p=$(echo "$py_row"   | jq -r ".$f")
        [ "$b" = "$p" ] || { echo "field $f: bash=$b python=$p"; return 1; }
    done
    # cost_usd within tolerance.
    local bcost pcost
    bcost=$(echo "$bash_row" | jq -r '.cost_usd')
    pcost=$(echo "$py_row"   | jq -r '.cost_usd')
    awk -v b="$bcost" -v p="$pcost" 'BEGIN { d = b - p; if (d < 0) d = -d; if (d > 1e-6) exit 1 }' \
        || { echo "cost_usd: bash=$bcost python=$pcost (diff > 1e-6)"; return 1; }
}

@test "happy path: 3-turn mixed-model fixture (2 Opus + 1 Sonnet)" {
    BASH_TRACKER=$(mktemp)
    PY_TRACKER=$(mktemp)
    TRANSCRIPT=$(mktemp).jsonl

    # Deterministic 3-turn transcript: 2 Opus + 1 Sonnet, with cache fields.
    cat > "$TRANSCRIPT" <<'EOF'
{"type":"assistant","message":{"role":"assistant","model":"claude-opus-4-5-20251015","usage":{"input_tokens":1000,"output_tokens":500,"cache_read_input_tokens":100,"cache_creation_input_tokens":200}}}
{"type":"assistant","message":{"role":"assistant","model":"claude-opus-4-5-20251015","usage":{"input_tokens":2000,"output_tokens":1000,"cache_read_input_tokens":50,"cache_creation_input_tokens":100}}}
{"type":"assistant","message":{"role":"assistant","model":"claude-sonnet-4-5-20251022","usage":{"input_tokens":3000,"output_tokens":1500,"cache_read_input_tokens":200,"cache_creation_input_tokens":400}}}
EOF

    payload=$(jq -n --arg t "$TRANSCRIPT" \
        '{session_id:"s-happy", transcript_path:$t, cwd:"/tmp/work"}')

    env SESSION_TOKENS_TRACKER="$BASH_TRACKER" SCOUT_MODE="$SCOUT_MODE" \
        "$BASH_HOOK" <<<"$payload"
    env SESSION_TOKENS_TRACKER="$PY_TRACKER" SCOUT_MODE="$SCOUT_MODE" \
        "$PYTHON_HOOK" hook session-tokens <<<"$payload"

    bash_row=$(cat "$BASH_TRACKER")
    py_row=$(cat "$PY_TRACKER")
    assert_rows_equal "$bash_row" "$py_row"

    rm -f "$BASH_TRACKER" "$PY_TRACKER" "$TRANSCRIPT"
}

@test "missing transcript: both emit transcript_not_found zero-row" {
    BASH_TRACKER=$(mktemp)
    PY_TRACKER=$(mktemp)

    # Path that doesn't exist.
    payload='{"session_id":"s-missing","transcript_path":"/tmp/scout-parity-no-such-file-xyz.jsonl","cwd":"/work"}'

    env SESSION_TOKENS_TRACKER="$BASH_TRACKER" SCOUT_MODE="$SCOUT_MODE" \
        "$BASH_HOOK" <<<"$payload"
    env SESSION_TOKENS_TRACKER="$PY_TRACKER" SCOUT_MODE="$SCOUT_MODE" \
        "$PYTHON_HOOK" hook session-tokens <<<"$payload"

    bash_row=$(cat "$BASH_TRACKER")
    py_row=$(cat "$PY_TRACKER")
    [ "$(echo "$bash_row" | jq -r '.error')" = "transcript_not_found" ]
    [ "$(echo "$py_row"   | jq -r '.error')" = "transcript_not_found" ]
    assert_rows_equal "$bash_row" "$py_row"

    rm -f "$BASH_TRACKER" "$PY_TRACKER"
}

@test "no usage turns: both emit no_usage_turns zero-row" {
    BASH_TRACKER=$(mktemp)
    PY_TRACKER=$(mktemp)
    TRANSCRIPT=$(mktemp).jsonl

    # Transcript with only user turns / assistant turns lacking usage.
    cat > "$TRANSCRIPT" <<'EOF'
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"hi"}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"yo"}]}}
EOF

    payload=$(jq -n --arg t "$TRANSCRIPT" \
        '{session_id:"s-nousage", transcript_path:$t, cwd:"/work"}')

    # Bash polls 3s and Python polls 3s by default — both will hit the
    # no_usage_turns branch after the same delay. Ack: this test takes ~6s
    # total. Acceptable for a single bats case run on demand.
    env SESSION_TOKENS_TRACKER="$BASH_TRACKER" SCOUT_MODE="$SCOUT_MODE" \
        "$BASH_HOOK" <<<"$payload"
    env SESSION_TOKENS_TRACKER="$PY_TRACKER" SCOUT_MODE="$SCOUT_MODE" \
        "$PYTHON_HOOK" hook session-tokens <<<"$payload"

    bash_row=$(cat "$BASH_TRACKER")
    py_row=$(cat "$PY_TRACKER")
    [ "$(echo "$bash_row" | jq -r '.error')" = "no_usage_turns" ]
    [ "$(echo "$py_row"   | jq -r '.error')" = "no_usage_turns" ]
    assert_rows_equal "$bash_row" "$py_row"

    rm -f "$BASH_TRACKER" "$PY_TRACKER" "$TRANSCRIPT"
}
