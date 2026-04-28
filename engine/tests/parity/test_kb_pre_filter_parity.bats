#!/usr/bin/env bats

# Parity test: bash kb-pre-filter.sh vs Python scoutctl hook kb-pre-filter.
# Both run against an identical synthetic KB tree under a tmp SCOUT dir; the
# resulting .scout-cache/kb-filter.md files must agree on the summary line
# (same stale / no-date / fresh counts).

setup() {
    BASH_HOOK="$HOME/Scout/hooks/kb-pre-filter.sh"
    PYTHON_HOOK="$BATS_TEST_DIRNAME/../../.venv/bin/scoutctl"
    if [ ! -x "$BASH_HOOK" ]; then
        skip "bash hook not present at $BASH_HOOK (already migrated?)"
    fi
}

# Build a synthetic KB tree under $1.
build_kb() {
    local root="$1"
    local kb="$root/knowledge-base"
    mkdir -p "$kb/projects/active"

    # STALE: linear-issues.md, 6h budget, ancient
    cat > "$kb/linear-issues.md" <<'EOF'
# Linear

Last Updated: 2025-01-01 12:00
EOF

    # FRESH: people.md, 168h budget, today
    cat > "$kb/people.md" <<EOF
# People

Last Updated: $(date '+%Y-%m-%d %H:%M')
EOF

    # NO_DATE: project file with no Last Updated marker
    cat > "$kb/projects/active/foo.md" <<'EOF'
# Foo project

No date marker here.
EOF
}

@test "parity: bash and python emit same stale/no-date/fresh counts" {
    BASH_DIR=$(mktemp -d)
    PY_DIR=$(mktemp -d)

    build_kb "$BASH_DIR"
    build_kb "$PY_DIR"

    # Bash hook honors $SCOUT_DIR for both KB read AND .scout-cache write.
    env SCOUT_DIR="$BASH_DIR" "$BASH_HOOK" briefing >/dev/null

    # Python hook uses paths.data_dir() which honors $SCOUT_DATA_DIR.
    env SCOUT_DATA_DIR="$PY_DIR" "$PYTHON_HOOK" hook kb-pre-filter --session-type briefing >/dev/null

    bash_summary=$(grep -E '^Stale: ' "$BASH_DIR/.scout-cache/kb-filter.md")
    py_summary=$(grep -E '^Stale: ' "$PY_DIR/.scout-cache/kb-filter.md")

    [ "$bash_summary" = "$py_summary" ]
    [ "$bash_summary" = "Stale: 1 | No date: 1 | Fresh: 1" ]

    rm -rf "$BASH_DIR" "$PY_DIR"
}

@test "parity: minimal KB (one fresh file) → both summaries match" {
    # NOTE: A truly empty KB (no .md files at all) trips the bash original's
    # `set -u` on FRESH_FILES[@] (unbound array). Python is more defensive.
    # We use a minimal KB with one fresh file to verify zero stale / zero
    # no-date / nonzero fresh path under both implementations.
    BASH_DIR=$(mktemp -d)
    PY_DIR=$(mktemp -d)

    for root in "$BASH_DIR" "$PY_DIR"; do
        mkdir -p "$root/knowledge-base"
        cat > "$root/knowledge-base/people.md" <<EOF
# People

Last Updated: $(date '+%Y-%m-%d %H:%M')
EOF
    done

    env SCOUT_DIR="$BASH_DIR" "$BASH_HOOK" dreaming >/dev/null
    env SCOUT_DATA_DIR="$PY_DIR" "$PYTHON_HOOK" hook kb-pre-filter --session-type dreaming >/dev/null

    bash_summary=$(grep -E '^Stale: ' "$BASH_DIR/.scout-cache/kb-filter.md")
    py_summary=$(grep -E '^Stale: ' "$PY_DIR/.scout-cache/kb-filter.md")

    [ "$bash_summary" = "$py_summary" ]
    [ "$bash_summary" = "Stale: 0 | No date: 0 | Fresh: 1" ]

    rm -rf "$BASH_DIR" "$PY_DIR"
}

@test "parity: ontology/personal/archive paths excluded by both" {
    BASH_DIR=$(mktemp -d)
    PY_DIR=$(mktemp -d)

    for root in "$BASH_DIR" "$PY_DIR"; do
        kb="$root/knowledge-base"
        mkdir -p "$kb/ontology" "$kb/personal" "$kb/projects/archived"
        # These should ALL be excluded — we'd see them as no-date entries if they leaked in.
        cat > "$kb/ontology/schema.md" <<'EOF'
# Schema
EOF
        cat > "$kb/personal/jordan.md" <<'EOF'
# Jordan
EOF
        cat > "$kb/projects/archived/old.md" <<'EOF'
# Old
EOF
        # One legit fresh file so the run isn't trivially empty.
        cat > "$kb/people.md" <<EOF
# People

Last Updated: $(date '+%Y-%m-%d %H:%M')
EOF
    done

    env SCOUT_DIR="$BASH_DIR" "$BASH_HOOK" briefing >/dev/null
    env SCOUT_DATA_DIR="$PY_DIR" "$PYTHON_HOOK" hook kb-pre-filter --session-type briefing >/dev/null

    bash_summary=$(grep -E '^Stale: ' "$BASH_DIR/.scout-cache/kb-filter.md")
    py_summary=$(grep -E '^Stale: ' "$PY_DIR/.scout-cache/kb-filter.md")

    [ "$bash_summary" = "$py_summary" ]
    [ "$bash_summary" = "Stale: 0 | No date: 0 | Fresh: 1" ]

    rm -rf "$BASH_DIR" "$PY_DIR"
}
