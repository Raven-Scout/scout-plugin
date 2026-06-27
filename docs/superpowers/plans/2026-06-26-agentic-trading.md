# Agentic Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> ⚠️ **Experimental, real-money, opt-in.** This subsystem places **real brokerage orders** via the `robinhood-trading` MCP. It ships **disabled** and is **not financial advice**. See the [design spec](../specs/2026-06-26-agentic-trading-design.md).

**Goal:** Build the optional, default-disabled "Agentic Trading" subsystem — an autonomous Robinhood equity-trading loop wired into Scout — per the design spec.

**Architecture:** A single `config.yaml` master switch gates everything. Three integration points: RESEARCH hunts candidates → a market-hours `trading` session (`TRADING.md` brain + `run-trading.sh`) decides/executes within hard guardrails → the briefing surfaces NAV. Robinhood is the live source of truth; the vault owns intent.

**Tech Stack:** Bash run-scripts (mirroring `run-research.sh`), Python 3 + PyYAML (config I/O), the `robinhood-trading` MCP tools, launchd, Markdown prompt-brains. No pytest harness exists for prompt-brains — verification is shell-observable (`shellcheck`, `plutil -lint`, run + inspect state).

**Conventions:** `<vault>` = the operator's Scout data dir, resolved at runtime via `$SCOUT_DATA_DIR`. The account number and Slack notify ID are read from `config.yaml` — **never hard-code them**.

## Global Constraints

_Every task's requirements implicitly include this section. Values copied verbatim from the spec._

- **Account:** the operator-configured `agentic_allowed` Robinhood account (`config.yaml: account`). Never touch any other account.
- **Guardrails:** `capital_cap 50`, `max_position_pct 50`, `stop_loss_pct 18`, `daily_loss_cap_pct 12`, `circuit_breaker_nav 35`, `min_price 3`, `no_leveraged_inverse true`, `settled_cash_only true`.
- **Ships disabled:** `config.yaml: enabled` is `false` on first commit. Creating files starts nothing.
- **Autonomy:** `mode: auto`, with automatic **degrade to propose-and-approve** if `place_equity_order` is denied headless.
- **Robinhood is authoritative** for positions/cash/P&L — reconcile live every run.
- **All times Eastern.** Never bare `date` — always `TZ=America/New_York date ...`.
- **`[[wikilinks]]`** for internal references. Canonical path `knowledge-base/` (hyphen). Never `index.md`.
- Trading-session commit prefix: `trading [HH:MM]: ...`. Build-task commits: `scout: ...`.
- **System-test-first:** measured, capital-protecting; never gamble to force the target.

---

### Task 1: Config source-of-truth + reader/writer

**Files:**
- Create: `<vault>/knowledge-base/projects/agentic-trading/config.yaml`
- Create: `scripts/trading-config.py`

**Interfaces:**
- Produces: `trading-config.py get <dotted.key>` → prints scalar, exit 0; missing → stderr + exit 1. `trading-config.py set <dotted.key> <value>` → rewrites in place. Shell consumers (Tasks 2, 4) rely on these subcommands.

- [ ] **Step 1: Write `config.yaml` (master switch ships OFF)**

```yaml
# Agentic Trading — master config. See knowledge-base/projects/agentic-trading/agentic-trading.md
enabled: false          # ← MASTER SWITCH. Ships OFF. Nothing runs until true.
mode: auto              # auto = place orders; propose = draft + one-tap-approve DM
account: "<your-robinhood-account-number>"
notify_slack_id: "<your-slack-id>"
test:
  goal_nav: 100
  start_nav: 50
  deadline: "<YYYY-MM-DD>"
guardrails:
  capital_cap: 50
  max_position_pct: 50
  stop_loss_pct: 18
  daily_loss_cap_pct: 12
  circuit_breaker_nav: 35
  min_price: 3
  no_leveraged_inverse: true
  settled_cash_only: true
```

- [ ] **Step 2: Write `scripts/trading-config.py`**

```python
#!/usr/bin/env python3
"""Read/write Agentic Trading config. Single source of truth for the master switch.
Usage:
    trading-config.py get <dotted.key>
    trading-config.py set <dotted.key> <value>
"""
import os, sys, yaml
from pathlib import Path

VAULT = Path(os.environ.get("SCOUT_DATA_DIR", Path.home() / "Scout"))
CONFIG = VAULT / "knowledge-base" / "projects" / "agentic-trading" / "config.yaml"

def _load():
    with open(CONFIG) as f:
        return yaml.safe_load(f)

def _coerce(v):
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v

def main():
    if len(sys.argv) < 3:
        sys.exit("usage: trading-config.py get|set <dotted.key> [value]")
    op, key = sys.argv[1], sys.argv[2]
    data = _load()
    parts = key.split(".")
    node = data
    for p in parts[:-1]:
        node = node[p]
    if op == "get":
        val = node[parts[-1]]
        print(str(val).lower() if isinstance(val, bool) else val)
    elif op == "set":
        if len(sys.argv) < 4:
            sys.exit("set requires a value")
        node[parts[-1]] = _coerce(sys.argv[3])
        with open(CONFIG, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    else:
        sys.exit(f"unknown op: {op}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Make executable and verify reads**

```bash
chmod +x scripts/trading-config.py
python3 scripts/trading-config.py get enabled                  # expect: false
python3 scripts/trading-config.py get guardrails.capital_cap   # expect: 50
```

- [ ] **Step 4: Verify set round-trips without disturbing other keys**

```bash
python3 scripts/trading-config.py set enabled true
python3 scripts/trading-config.py get enabled                  # expect: true
python3 scripts/trading-config.py set enabled false            # restore shipped default
python3 scripts/trading-config.py get guardrails.stop_loss_pct # expect: 18 (untouched)
```

- [ ] **Step 5: Commit**

```bash
git add scripts/trading-config.py knowledge-base/projects/agentic-trading/config.yaml
git commit -m "scout: agentic-trading config + reader (ships disabled)"
```

---

### Task 2: `scripts/trading.sh` — the on/off/status CLI

**Files:**
- Create: `scripts/trading.sh`

**Interfaces:**
- Consumes: `trading-config.py get|set` (Task 1).
- Produces: `trading.sh on|off|status`. `status` is read-only and dependency-free (config + last NAV snapshot + days-left); it does NOT call the broker.

- [ ] **Step 1: Write `scripts/trading.sh`**

```bash
#!/bin/bash
# Agentic Trading master switch. See knowledge-base/projects/agentic-trading/agentic-trading.md
set -euo pipefail

VAULT="${SCOUT_DATA_DIR:-$HOME/Scout}"
CFG="$VAULT/scripts/trading-config.py"
NAV="$VAULT/knowledge-base/projects/agentic-trading/nav-history.md"

cmd="${1:-status}"
case "$cmd" in
  on)
    python3 "$CFG" set enabled true
    echo "Agentic Trading: ENABLED (mode=$(python3 "$CFG" get mode)). The loop runs on its market-hours schedule."
    ;;
  off)
    python3 "$CFG" set enabled false
    echo "Agentic Trading: DISABLED. No new orders or management. Open positions left untouched."
    ;;
  status)
    en=$(python3 "$CFG" get enabled)
    mode=$(python3 "$CFG" get mode)
    acct=$(python3 "$CFG" get account)
    deadline=$(python3 "$CFG" get test.deadline)
    today=$(TZ=America/New_York date '+%Y-%m-%d')
    days_left=$(( ( $(date -j -f '%Y-%m-%d' "$deadline" '+%s') - $(date -j -f '%Y-%m-%d' "$today" '+%s') ) / 86400 ))
    echo "Agentic Trading status"
    echo "  enabled:    $en"
    echo "  mode:       $mode"
    echo "  account:    ••••${acct: -4}"
    echo "  deadline:   $deadline ($days_left days left)"
    if [ -f "$NAV" ]; then
      echo "  last NAV:   $(grep -E '^\| [0-9]' "$NAV" | tail -1 || echo '(none yet)')"
    fi
    ;;
  *)
    echo "usage: trading.sh on|off|status" >&2
    exit 1
    ;;
esac
```

- [ ] **Step 2: Make executable, shellcheck, run status**

```bash
chmod +x scripts/trading.sh
shellcheck scripts/trading.sh        # expect: no warnings
scripts/trading.sh status            # expect: enabled false, mode auto, account ••••XXXX
```

- [ ] **Step 3: Verify on/off flip the master switch, then restore OFF**

```bash
scripts/trading.sh on  && python3 scripts/trading-config.py get enabled   # expect: true
scripts/trading.sh off && python3 scripts/trading-config.py get enabled   # expect: false
```

- [ ] **Step 4: Commit**

```bash
git add scripts/trading.sh
git commit -m "scout: trading.sh on/off/status master-switch CLI"
```

---

### Task 3: State-file scaffolds (watchlist, decision log, NAV history)

**Files:**
- Create: `<vault>/knowledge-base/projects/agentic-trading/watchlist.md`
- Create: `<vault>/knowledge-base/projects/agentic-trading/decision-log.md`
- Create: `<vault>/knowledge-base/projects/agentic-trading/nav-history.md`

**Interfaces:**
- Produces: files the loop appends to (Task 5) and `status` reads (Task 2). `nav-history.md` data rows start with `| YYYY-MM-DD` (the regex `trading.sh status` greps).

- [ ] **Step 1: Write `watchlist.md`**

```markdown
# Agentic Trading — Watchlist
Candidate names + theses. Written by [[RESEARCH]] sessions (toggle-gated), consumed by [[TRADING]].
Universe rule: liquid US equities/ETFs, price ≥ $3, real volume; no penny/OTC; no leveraged/inverse.

| Ticker | Added (ET) | Thesis (1 line) | Catalyst / timing | Source | Status |
|--------|-----------|-----------------|-------------------|--------|--------|
| _(none yet)_ | | | | | |
```

- [ ] **Step 2: Write `decision-log.md`**

```markdown
# Agentic Trading — Decision Log (append-only)
Every buy / sell / hold / skip, with reasoning and outcome. Newest at bottom.

| Timestamp (ET) | Action | Ticker | Qty/$ | Limit | Thesis / reason | Stop | Order ID | Result |
|----------------|--------|--------|-------|-------|-----------------|------|----------|--------|
```

- [ ] **Step 3: Write `nav-history.md` with the starting snapshot**

```markdown
# Agentic Trading — NAV History
One row per trading-session reconcile. Target: goal_nav by deadline. Start: start_nav.

| Date | Time (ET) | NAV | Cash (settled) | Positions value | Δ vs prior | Note |
|------|-----------|-----|----------------|-----------------|-----------|------|
| <YYYY-MM-DD> | seed | 50.00 | 0.00 | 0.00 | — | starting stake; subsystem disabled |
```

- [ ] **Step 4: Verify scaffolds exist and status reads NAV**

```bash
ls knowledge-base/projects/agentic-trading/{watchlist,decision-log,nav-history}.md
scripts/trading.sh status | grep "last NAV"   # expect: shows the seed row
```

- [ ] **Step 5: Commit**

```bash
git add knowledge-base/projects/agentic-trading/{watchlist,decision-log,nav-history}.md
git commit -m "scout: agentic-trading state scaffolds (watchlist, decision log, NAV history)"
```

---

### Task 4: `run-trading.sh` — entrypoint with disabled/market/deadline guards

**Files:**
- Create: `<vault>/run-trading.sh`

**Interfaces:**
- Consumes: `trading-config.py get enabled|test.deadline` (Task 1); `TRADING.md` (Task 5); mirrors `run-research.sh` infra (lock, budget-check, claude-with-retry, write-session-cost).
- Produces: the launchd entrypoint (Task 8). Exits `0` silently when disabled, on weekends, or with no work.

- [ ] **Step 1: Write `run-trading.sh` (guards FIRST, before any spend)**

```bash
#!/bin/bash
# Scout Agentic Trading runner — invoked by launchd on market-hours, or manually.
# GUARDS FIRST: exits before any cost if disabled / market closed / past deadline.
set -euo pipefail

VAULT="${SCOUT_DATA_DIR:-$HOME/Scout}"
export SCOUT_DATA_DIR="$VAULT"
LOG_DIR="$VAULT/.scout-logs"
CLAUDE_BIN="$(command -v claude || echo "$HOME/.local/bin/claude")"
CFG="$VAULT/scripts/trading-config.py"
TIMESTAMP=$(TZ=America/New_York date +%Y-%m-%d_%H-%M)
LOG_FILE="$LOG_DIR/trading-$TIMESTAMP.log"
LOCK_FILE="$LOG_DIR/.scout-session.lock"
mkdir -p "$LOG_DIR"
log() { echo "$1" >> "$LOG_FILE"; }

# --- GUARD 1: master switch ---
if [ "$(python3 "$CFG" get enabled)" != "true" ]; then
    log "=== Agentic Trading DISABLED — skipping $(TZ=America/New_York date) ==="
    exit 0
fi

# --- GUARD 2: weekday only ---
DOW=$(TZ=America/New_York date +%u)   # 1=Mon .. 7=Sun
if [ "$DOW" -ge 6 ]; then
    log "=== Weekend (dow=$DOW) — market closed, skipping ==="
    exit 0
fi

# --- GUARD 3: deadline → wind-down (report-only, no new entries) ---
TODAY=$(TZ=America/New_York date '+%Y-%m-%d')
DEADLINE=$(python3 "$CFG" get test.deadline)
WIND_DOWN=0
if [[ "$TODAY" > "$DEADLINE" ]]; then
    WIND_DOWN=1
    log "=== Past deadline $DEADLINE — WIND-DOWN mode (report-only) ==="
fi
export TRADING_WIND_DOWN="$WIND_DOWN"

# --- Concurrency guard (shared Scout lock) ---
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$LOCK_PID" 2>/dev/null; then
        log "=== Another Scout session running (PID $LOCK_PID) — skipping ==="
        exit 0
    else
        rm -f "$LOCK_FILE"
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# --- Budget check ---
BUDGET_CHECK="$VAULT/scripts/budget-check.sh"
if [ -x "$BUDGET_CHECK" ]; then
    if ! "$BUDGET_CHECK" --verbose >> "$LOG_FILE" 2>&1; then
        log "=== Budget check: skipping this run ==="
        exit 0
    fi
fi

PROMPT="You are Scout running in TRADING mode. Working directory $VAULT.
Step 1: Read $VAULT/TRADING.md in full and follow it exactly.
Step 2: Honor TRADING_WIND_DOWN=${TRADING_WIND_DOWN} (1 = report-only; manage/exit only).
Important:
- The robinhood-trading MCP tools are deferred — load them via ToolSearch before use.
- Robinhood is the source of truth: reconcile live before any decision.
- Enforce every guardrail in config.yaml as a HARD rule. System-test-first: never gamble to force the target.
- Commit with prefix: trading [HH:MM]: <summary>. Use TZ=America/New_York for timestamps."

log "=== Scout Trading run starting at $(TZ=America/New_York date) (wind_down=$WIND_DOWN) ==="
MODE="${SCOUT_FORCE_MODE:-trading}"
EXIT_CODE=0
cd "$VAULT" && "$VAULT/scripts/claude-with-retry.sh" \
    "$LOG_FILE" \
    "$CLAUDE_BIN" \
    --permission-mode auto \
    --model opus \
    --max-budget-usd 10.00 \
    --name "scout-${MODE}-$(TZ=America/New_York date +%Y%m%d-%H%M)" \
    -p "$PROMPT" \
    || EXIT_CODE=$?
log "=== Scout Trading run finished (exit $EXIT_CODE) at $(TZ=America/New_York date) ==="

COST_TRACKER="$VAULT/scripts/write-session-cost.sh"
if [ -x "$COST_TRACKER" ]; then
    "$COST_TRACKER" "trading" 10.00 0 "$EXIT_CODE" "runner" 2>/dev/null || true
fi
```

- [ ] **Step 2: shellcheck**

```bash
chmod +x run-trading.sh
shellcheck run-trading.sh    # expect: no warnings
```

- [ ] **Step 3: Verify the disabled guard exits with ZERO spend (no claude invoked)**

```bash
python3 scripts/trading-config.py get enabled    # confirm: false
bash run-trading.sh ; echo "exit=$?"             # expect: exit=0, instant
tail -1 .scout-logs/trading-*.log                # expect: "Agentic Trading DISABLED ... skipping"
```
Expected: instant `exit=0`; log confirms it skipped *before* the claude block. (Critical safety test — disabled means no money, no spend.)

- [ ] **Step 4: Verify guard ordering (guards run before any spend)**

```bash
grep -n "GUARD 1\|GUARD 2\|GUARD 3\|claude-with-retry" run-trading.sh
```
Expected: all three guards at lower line numbers than `claude-with-retry`.

- [ ] **Step 5: Commit**

```bash
git add run-trading.sh
git commit -m "scout: run-trading.sh entrypoint (disabled/weekend/deadline guards before any spend)"
```

---

### Task 5: `TRADING.md` — the loop brain

**Files:**
- Create: `<vault>/TRADING.md`

**Interfaces:**
- Consumes: `config.yaml` (Task 1), state files (Task 3), `robinhood-trading` MCP read+order tools, `TRADING_WIND_DOWN` env (Task 4).
- Produces: the 6-step loop. Appends to decision-log/nav-history; commits `trading [HH:MM]:`; notifies via `config.yaml: notify_slack_id`.

- [ ] **Step 1: Write `TRADING.md`** (read `account` and `notify_slack_id` from `config.yaml` — never hard-code)

````markdown
# TRADING

You are Scout in **TRADING** mode — the autonomous equity-trading loop for the optional [[agentic-trading]] subsystem. Read [[agentic-trading]] for full context. **System-test-first: protect capital, log everything, never gamble to force the target.**

## Step 0: Guards (re-confirm in-session)
- Load `config.yaml`. If `enabled` is not `true`, STOP. Read `account` and `notify_slack_id` for later steps.
- Honor `TRADING_WIND_DOWN`: if `1`, place **no new entries** — only manage/exit + report.
- Load `robinhood-trading` MCP tools via ToolSearch (deferred): `get_portfolio`, `get_equity_positions`, `get_equity_quotes`, `get_equity_orders`, `get_realized_pnl`, `review_equity_order`, `place_equity_order`, `get_equity_fundamentals`.

## Step 1: Reconcile (Robinhood is source of truth)
- `get_portfolio(account_number=<account>)` → NAV (`total_value`), `cash`, `pending_deposits`. **Settled cash = cash − pending_deposits.** If settled cash is 0 and no positions → log "awaiting settlement" and skip to Step 6.
- `get_equity_positions` → open positions (use `shares_available_for_sells`, `average_buy_price`).
- `get_equity_orders` → working/unfilled orders (don't double-submit).
- `get_realized_pnl` → realized P&L to date.
- Compute NAV, Δ vs last `nav-history.md` row, progress toward goal, trading days left.

## Step 2: Risk gate (HARD rules from config.yaml)
- **Circuit breaker:** NAV ≤ `circuit_breaker_nav` → HALT, notify operator that re-authorization is required, exit.
- **Daily loss cap:** NAV down > `daily_loss_cap_pct` vs first snapshot today → no new entries (exits still allowed).
- **Stop-loss:** flag any position ≥ `stop_loss_pct` below `average_buy_price` for exit in Step 3.

## Step 3: Manage open positions
- For each holding: live quote (`get_equity_quotes`), re-test thesis (from `decision-log.md`).
- **Exit** (sell, limit) if stop breached / thesis broken / target reached. Record the reason. Otherwise hold, note the re-eval.

## Step 4: Consider entries (skip if WIND_DOWN or daily-loss-cap hit)
- Read `watchlist.md`. Pick the single best thesis fitting ALL guardrails: price ≥ `min_price`; not leveraged/inverse; liquid; size ≤ `max_position_pct` of NAV; settled cash only; total ≤ `capital_cap`.
- High-conviction catalyst may size to the cap; absent a real edge, **skip and log the reason.** Skipping is valid and common.

## Step 5: Execute
- `review_equity_order(...)` (dry-run) → `place_equity_order(...)` as a **limit** order with a recorded stop intent.
- **Degrade path:** if `place_equity_order` is denied/blocked, append the fully-specified intended order to `decision-log.md` marked `PENDING-APPROVAL` and include a one-tap-approve summary in the Step 6 notification. (`mode: auto` → effective `propose` for that order.)

## Step 6: Log + report
- Append every decision **including skips and holds** to `decision-log.md`.
- Append a NAV row to `nav-history.md`.
- `git add -A && git commit -m "trading [HH:MM]: <NAV $X (Δ), actions>"` (TZ=America/New_York).
- Notify the operator (`slack_send_message`, ID `<notify_slack_id>`), ≤8 lines: NAV + Δ + progress + days left, what you did and why (incl. notable skips), any `PENDING-APPROVAL` orders. If the circuit breaker tripped, lead with that.

## Hard rules recap
Only the configured account. Never exceed `capital_cap`. Every position has a stop. Settled cash only. No penny/OTC/leveraged/inverse. Reconcile live. Protect capital over chasing the target.
````

- [ ] **Step 2: Verify structural integrity**

```bash
grep -c "^## Step" TRADING.md     # expect: 7 (Steps 0-6)
grep -E "circuit_breaker|stop_loss_pct|settled|PENDING-APPROVAL|notify_slack_id" TRADING.md | wc -l   # expect: ≥5
```

- [ ] **Step 3: Commit**

```bash
git add TRADING.md
git commit -m "scout: TRADING.md loop brain (reconcile→risk→manage→enter→execute→log, headless degrade)"
```

---

### Task 6: RESEARCH integration — toggle-gated candidate hunt

**Files:**
- Create: `<vault>/knowledge-base/research-queue/agentic-trading-watchlist.md`
- Modify: `<vault>/RESEARCH.md` (Step 1a area — add a short toggle-gated pointer)

- [ ] **Step 1: Write the research-queue item**

```markdown
---
title: Agentic Trading — candidate watchlist refresh
status: open
priority: low
date: <YYYY-MM-DD>
area: agentic-trading
recurring: true
gated_by: "knowledge-base/projects/agentic-trading/config.yaml: enabled"
---
# Agentic Trading watchlist refresh

**Gate:** Only run when `config.yaml: enabled` is `true`. If disabled, skip and note "trading disabled".

**Brief:** Find 3-6 liquid US equities/ETFs with near-term (≤2 week) catalysts fitting the guardrails (price ≥ $3, real volume, no penny/OTC, no leveraged/inverse). For each: one-line thesis, catalyst + timing, source. Write/refresh rows in `[[watchlist]]`. Prefer quality over quantity; remove stale names. Do NOT place trades — that's [[TRADING]]'s job.
```

- [ ] **Step 2: Add the toggle-gated pointer to `RESEARCH.md`** (after the Step 1a research-queue read)

```markdown
> **Agentic Trading gate:** The `agentic-trading-watchlist` queue item is gated — run ONLY if `config.yaml` has `enabled: true` (`python3 scripts/trading-config.py get enabled`). If `false`, skip silently. See [[agentic-trading]].
```

- [ ] **Step 3: Verify**

```bash
ls knowledge-base/research-queue/agentic-trading-watchlist.md
grep -q "Agentic Trading gate" RESEARCH.md && echo "pointer present"
```

- [ ] **Step 4: Commit**

```bash
git add knowledge-base/research-queue/agentic-trading-watchlist.md RESEARCH.md
git commit -m "scout: RESEARCH candidate-hunt for agentic-trading (toggle-gated)"
```

---

### Task 7: Briefing one-liner (toggle-gated)

**Files:**
- Modify: the briefing-assembly phase (engine `phases/`) and/or vault `SKILL.md`.

**Note:** In the engine, briefing content is assembled from `phases/`. Add the gated block to the appropriate briefing phase so it renders into `SKILL.md` on assembly (verify via the engine's `_assemble` step). Keep the change minimal and clearly gated.

- [ ] **Step 1: Add the gated briefing block**

```markdown
### Agentic Trading line (optional subsystem — gated)
If `config.yaml` has `enabled: true` (`python3 scripts/trading-config.py get enabled`), add ONE line under a "💸 Trading" mini-heading: latest NAV from `[[nav-history]]`, Δ vs prior, progress toward goal, days left. If `enabled: false`, output nothing. See [[agentic-trading]].
```

- [ ] **Step 2: Verify (gate respected both ways)**

```bash
grep -q "Agentic Trading line" <briefing phase or SKILL.md> && echo "block present"
python3 scripts/trading-config.py get enabled   # false → briefing omits the line
```

- [ ] **Step 3: Commit**

```bash
git add phases/ <SKILL.md if mirrored>
git commit -m "scout: briefing surfaces agentic-trading NAV (gated)"
```

---

### Task 8: launchd plist (market-hours, ships dormant)

**Files:**
- Create: `launchd/com.scout.trading.plist` (in-repo template; the bootstrap installs it to `~/Library/LaunchAgents/` on enable)

**Interfaces:**
- Consumes: `run-trading.sh` (Task 4), which self-guards — so firing while disabled is harmless.
- Produces: a market-hours schedule (~10:00 and ~15:30 ET, weekdays). NOT loaded during build.

- [ ] **Step 1: Write the plist template** (the bootstrap substitutes the real vault path for `__VAULT__`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.scout.trading</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>__VAULT__/run-trading.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>15</integer><key>Minute</key><integer>30</integer></dict>
    </array>
    <key>EnvironmentVariables</key>
    <dict><key>TZ</key><string>America/New_York</string></dict>
    <key>StandardOutPath</key>
    <string>__VAULT__/.scout-logs/trading-launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>__VAULT__/.scout-logs/trading-launchd.err.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Lint** (substitute a real path first when testing)

```bash
sed 's#__VAULT__#'"$HOME"'/Scout#g' launchd/com.scout.trading.plist | plutil -lint -   # expect: OK
```

- [ ] **Step 3: Commit (do NOT load it yet)**

```bash
git add launchd/com.scout.trading.plist
git commit -m "scout: launchd plist for market-hours trading session (dormant; load on enable)"
```

---

### Task 9: End-to-end dry run + supervised first-live checklist

**Files:** none (operational verification + doc update).

- [ ] **Step 1: Full disabled-state dry run (spends nothing, trades nothing)**

```bash
python3 scripts/trading-config.py get enabled    # confirm: false
bash run-trading.sh ; echo "exit=$?"             # expect: exit=0, instant
scripts/trading.sh status
```

- [ ] **Step 2: Confirm settlement before going live**

In an interactive Claude session with `robinhood-trading` loaded: `get_portfolio(account_number=<account>)`. Proceed only when `pending_deposits == 0` and settled cash is available.

- [ ] **Step 3: Install the schedule + enable**

```bash
sed 's#__VAULT__#'"$SCOUT_DATA_DIR"'#g' launchd/com.scout.trading.plist > ~/Library/LaunchAgents/com.scout.trading.plist
launchctl unload ~/Library/LaunchAgents/com.scout.trading.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.scout.trading.plist
scripts/trading.sh on
scripts/trading.sh status     # expect: enabled true
```

- [ ] **Step 4: Supervised first live run — watch the headless-gating question**

```bash
bash run-trading.sh
tail -f .scout-logs/trading-*.log
```
Observe: does `place_equity_order` execute autonomously, or get blocked (→ degrade path writes `PENDING-APPROVAL` + notifies)? This resolves the spec's biggest open risk.

- [ ] **Step 5: Record the outcome in the charter + commit**

Update the `agentic-trading.md` charter §Autonomy with run #1's result, then commit `trading [HH:MM]: run #1 result — headless execution autonomous|degraded`.

---

## Self-Review

**Spec coverage:** charter/goal → Task 5 + globals ✓; account/guardrails → Tasks 1,5 ✓; hybrid integration → Tasks 6,4+5,7 ✓; source-of-truth reconcile → Task 5 Step 1 ✓; toggle + CLI + ships-disabled → Tasks 1,2 ✓; OFF-leaves-positions → Task 2 + Task 5 ✓; autonomy + degrade → Task 5 Step 5 ✓; state files → Task 3 ✓; launchd dormant → Task 8 ✓; test-expiry wind-down → Task 4 Guard 3 + Task 5 Step 4 ✓; settlement-awareness → Task 5 Step 1 ✓.

**Placeholder scan:** code/config/XML shown in full. Operator-specific values (`account`, `notify_slack_id`, `deadline`) are intentional config placeholders, never hard-coded. Task 7's exact briefing-phase location is the one engine-integration detail to confirm against the assembly system at build time.

**Type/name consistency:** `trading-config.py get|set <dotted.key>` identical across Tasks 2 & 4; `config.yaml` keys consistent across Tasks 1,2,4,5; `nav-history.md` data-row format (`| YYYY-MM-DD`) consistent between Task 3 (seed) and Task 2 (grep).
