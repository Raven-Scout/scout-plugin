# Agentic Trading — Optional Subsystem Design Spec

**Date:** 2026-06-26
**Status:** Design spec (proposal — pending review). **Ships DISABLED.**
**Repo:** `github.com/Raven-Scout/scout-plugin` (public)
**Approach:** Hybrid — RESEARCH sessions hunt candidates → a dedicated market-hours `trading` session decides/executes within hard guardrails → the briefing surfaces NAV. One master switch gates everything; ships off.

> ⚠️ **Experimental, real-money, opt-in.** This subsystem places **real brokerage orders** with **real money** via the `robinhood-trading` MCP. It is **disabled by default** and must be explicitly turned on by the operator. It is **not financial advice**, carries real risk of loss, and is provided as-is with no warranty. The reference test deliberately sandboxes a tiny amount of risk capital. Do not enable it with money you can't afford to lose.

## Problem / motivation

Scout is an autonomous knowledge + briefing system. This proposal adds an **optional** capability: let Scout run a small, fully-logged, guardrailed autonomous equity-trading loop against an operator-configured Robinhood account — as a *system test* of Scout's ability to close a real-world action loop (research → decide → act → log → report → iterate), not as a wealth strategy.

**Reference first-test goal:** turn a small stake (example: **$50 → $100 in ~2 weeks**) — framed **system-test-first**: the deliverable is a *working, safe, fully-logged autonomous loop that survives to iterate*. The dollar target is a **stretch**, not a mandate; the loop protects capital and never gambles to force it.

## What this is NOT

- **Not a wealth strategy.** Honest base rate for measured equity trading over ~2 weeks ≈ **−50% to +40%**, not a reliable double. The design accepts it probably won't double.
- **Not options trading.** The reference account is a **cash** account with options disabled (`option_level: ""`). Equities + fractional shares only. (Options are the only realistic 2-week-double vehicle and aren't available — which is *why* the target is a stretch.)
- **Not always-on.** Opt-in subsystem; disabled by default; one switch turns the whole thing off.

## Constraints (operator-configured)

- **Account:** a single operator-configured Robinhood account that is `agentic_allowed: true`. (Robinhood exposes an `agentic_allowed` flag per account; only those are actionable.) Configured as `account` in `config.yaml`.
- **Cash-account settlement:** for a cash account there's no PDT limit, but **sale proceeds settle T+1** and unsettled cash can't be recycled without good-faith-violation risk → realistically a handful of round-trips per fortnight, not rapid day-trading. The loop is **settlement-aware** (`settled_cash_only: true`).

## Architecture (hybrid integration)

Three integration points so the subsystem reuses what Scout already does well:

1. **RESEARCH sessions — the *outward* half.** A recurring `research-queue/` item makes research runs hunt candidates (catalysts, earnings, momentum, sector news) and write them to a **watchlist** with theses. *Toggle-gated — skipped when disabled.*
2. **A new lightweight `trading` session — the *decision + execution* half.** A `TRADING` brain + `run-trading.sh` + a launchd plist on a market-hours cadence (~10:00 & ~15:30 ET, weekdays). Runs the loop below. `run-trading.sh` exits immediately when disabled, so a scheduled fire is always harmless.
3. **Briefing — the *surface*.** One line in the morning briefing: `Trading: NAV $X (Δ today), N positions, M days left`. *Omitted when disabled.*

**Why hybrid:** bolting onto research/dreaming alone fails the cadence test (research is infrequent; dreaming runs after market close); a brand-new everything duplicates the candidate-research Scout already does. Hybrid puts each half where it belongs.

### Source of truth
- **Robinhood is authoritative** for positions, cash, and realized P&L — reconcile **live every run**; never trust a cached ledger (mirrors Scout's "the existing KB is not trusted as fact" principle).
- **The vault owns intent:** rules (`config.yaml`), candidate theses (watchlist), the append-only decision log, and NAV-over-time history.

## Components

```
<vault>/knowledge-base/projects/agentic-trading/
  agentic-trading.md        # charter / living doc
  config.yaml               # master switch + all guardrail params (source of truth)
  watchlist.md              # candidate names + theses (written by RESEARCH)
  decision-log.md           # append-only: every buy/sell/hold/skip + reasoning + outcome
  nav-history.md            # NAV snapshot per run, progress toward goal
<vault>/TRADING.md          # the trading-session brain (the loop)
<vault>/run-trading.sh      # entrypoint; exits immediately if disabled
scripts/trading.sh          # convenience CLI: on | off | status
scripts/trading-config.py   # config get/set (PyYAML)
launchd/com.scout.trading.plist   # market-hours schedule (dormant until enabled)
```

### `config.yaml`

```yaml
enabled: false          # ← MASTER SWITCH. Ships OFF. Nothing runs until true.
mode: auto              # auto = place orders; propose = draft + one-tap-approve DM
account: "<your-robinhood-account-number>"
notify_slack_id: "<your-slack-id>"
test:
  goal_nav: 100
  start_nav: 50
  deadline: "<YYYY-MM-DD>"
guardrails:
  capital_cap: 50               # never trade beyond this; hard ceiling
  max_position_pct: 50          # max single-name exposure (~2 concurrent names)
  stop_loss_pct: 18             # mandatory hard stop on every position
  daily_loss_cap_pct: 12        # NAV down >12% in a day → no new entries that day
  circuit_breaker_nav: 35       # NAV <= floor → full halt + require re-auth
  min_price: 3                  # no penny/OTC names
  no_leveraged_inverse: true    # no leveraged/inverse ETFs in v1
  settled_cash_only: true       # avoid good-faith violations
```

## The loop (each `trading` session)

0. **Guard** — read `config.yaml`; if `enabled: false`, exit silently.
1. **Reconcile** — pull live portfolio / positions / open orders / realized P&L → compute NAV, settled vs unsettled cash, progress to goal, trading days left.
2. **Risk gate** — circuit breaker (NAV ≤ floor → halt + notify), daily loss cap, stop-loss flags.
3. **Manage open positions** — re-test each thesis vs. live quote; exit on stop / broken thesis / target.
4. **Consider entries** — best watchlist thesis that fits guardrails + **settled** cash. Skipping is a valid, logged outcome.
5. **Execute** — `review_equity_order` (dry-run) → `place_equity_order`; limit orders with defined stops.
6. **Log + report** — append every decision *including skips* to the decision log; update NAV history; commit; send a tight notification.

## Guardrails (what makes "autonomous" safe)

- **Capital ceiling.** Never trades beyond `capital_cap`. Only the configured account.
- **Max single position** ≈ `max_position_pct` of NAV (no all-in).
- **Mandatory stop-loss** on every position.
- **Daily loss cap** — NAV down > `daily_loss_cap_pct` on the day → no new entries.
- **Circuit breaker** — NAV ≤ `circuit_breaker_nav` → full halt + operator re-authorization.
- **Universe** — liquid US equities/ETFs, price ≥ `min_price`; no penny/OTC; no leveraged/inverse in v1.
- **Settlement-aware** — trade only settled cash.

All guardrails live in `config.yaml` → tunable without code changes.

## The toggle (optional-subsystem requirement)

**One master switch, disabled by default.** `config.yaml: enabled` is the single source of truth; every consumer reads it and no-ops when false (`run-trading.sh` exits; RESEARCH skips the watchlist hunt; briefing omits the line). Convenience CLI: `scripts/trading.sh on | off | status`.

**Semantics of OFF:** stops all *new* activity and management — open positions just sit (no panic-sell). Liquidating is a separate explicit action. The circuit breaker is the *automated* halt; this toggle is the *manual* one.

**Key safety property:** creating the files starts nothing. Ships disabled. Adding the launchd plist is risk-free — dormant until `scripts/trading.sh on`.

## Autonomy & the headless-execution risk

Default is **fully autonomous within guardrails**. One honest open risk: **scheduled/headless sessions may have real-money `place_equity_order` blocked by the harness safety classifier** (the same gating family that blocks some autonomous messaging/commit actions).

**Graceful degrade (built into step 5):** if the order tool is denied headless, the loop writes the intended order to a **pending-orders queue** + sends a **one-tap-approve notification** instead of silently failing — i.e. `mode: auto` degrades to `propose` for that order. The first live run resolves which mode is actually available — verify, don't assume.

## Build sequence (high level)

1. Scaffold `config.yaml` (**disabled**) + state files.
2. `scripts/trading.sh` (on/off/status) + `scripts/trading-config.py`.
3. `TRADING` brain + `run-trading.sh` (disabled/weekend/deadline guards + headless degrade).
4. Recurring RESEARCH watchlist item (toggle-gated).
5. Briefing one-liner (toggle-gated).
6. launchd plist (market-hours; ships dormant).
7. Dry run while disabled → verify reconcile + logging + report with **no orders, no spend**.
8. Enable once cash settles; watch run #1 to learn whether headless autonomous execution is allowed or degrades.

## Open questions / risks

- **Headless execution gating** — the biggest unknown; resolved empirically on run #1 (degrade path covers it).
- **Cash settlement timing** — gates the start of live trading.
- **Research vs. market cadence** — research is infrequent; the dedicated market-hours session is the fix, but its scheduling reliability is itself worth watching.
- **Test expiry** — after the deadline the subsystem auto-quiets (report-only wind-down) unless renewed.
- **Distribution caveat** — as a public, real-money feature it carries reputational/liability surface; hence the prominent disclaimer, default-off posture, and hard guardrails.

## Decision record

- **2026-06-26** — Brainstormed + green-lit. Decisions: (1) system-test-first (not swing-for-the-double); (2) fully autonomous within guardrails (degrade-to-propose if headless-blocked); (3) optional subsystem, single master switch, ships disabled; (4) defaults — 50% single-name cap, launchd plist shipped dormant.
