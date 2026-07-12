# Connector Resilience — Skip-If-Degraded, Gap Tracking & Backfill — Design

**Date:** 2026-07-01 (revised 2026-07-03 after review)
**Status:** Design — not yet planned/scheduled
**Related issue:** [#121](https://github.com/Raven-Scout/scout-plugin/issues/121) — runner templates never export `SCOUT_MODE`; connector telemetry dark. Complementary, not a blocker (see §"Relationship to #121"). To be linked from the implementing PR.

## Problem

Scheduled Scout runs (briefing, consolidation, dreaming, research) depend on
connectors — Slack, Linear, Gmail, Calendar, Granola, Fathom, Drive, GitHub,
and others — to pull the signals they synthesize. When a connector's OAuth token
expires or an MCP server goes dark, the run **executes anyway** against whatever
connectors happen to be up. Three bad things follow:

1. **Silent degradation.** The user reads a briefing that says "nothing notable
   in Slack" when Slack was simply unreachable. The absence of signal is
   indistinguishable from the absence of data. The user has no idea a connector
   was down.
2. **KB poisoning.** Consolidation writes KB entries derived from a partial view.
   Because dreaming and future briefings read the KB, one blind run degrades the
   foundation of many later runs. A missed run is recoverable; a run that
   *appears* complete but was blind is worse than a miss.
3. **No recovery path.** After an extended outage (the motivating case: a user
   returning from 6 weeks of leave to find Linear and Slack were down the whole
   time), there is no way to reconstruct what was missed. The value the run was
   supposed to provide is simply gone.

The connector-health pipeline (`connector_health_report.py`) is
**retrospective** — it detects degradation *after* a run by rolling up JSONL
logs, and only alerts once a degradation pattern holds across runs. It cannot
prevent a blind run, and (per #121) has been structurally dark for weeks.

## Goals

- **Detect connector degradation *before* the main session runs** — cheaply,
  with no wasted model tokens.
- **Make the run/skip behavior configurable** per slot type (strict for
  briefing/consolidation, relaxed for dreaming/research).
- **Never lose context.** When runs are skipped, record the gap so the missing
  window is known and recoverable.
- **Give the user an explicit, one-click backfill** that reconstructs the entire
  missed period — regardless of how long — surfaced in the briefing output and
  in the macOS app.

## Non-goals

- Automatic backfill. Backfill is **always user-triggered** — it is
  token-expensive and the user decides when to spend on it.
- Overwriting or repairing KB entries written by earlier blind runs. Backfill
  *adds* entries for a gap window; it does not reconcile pre-existing bad data.
  (Cleanup of pre-policy blind runs is a one-time manual concern, out of scope.)
- Fixing #121. That is a separate 2-line fix; this design is independent of it.

## Key discovery — the probe is free

`claude mcp list` and `claude mcp get "<name>"` **already health-check every
approved connector** and report status as parseable text, with **zero model
tokens** spent (pure connection check, no LLM invocation):

```
$ claude mcp get "claude.ai Slack"
claude.ai Slack:
  Scope: claude.ai config
  Status: ✔ Connected

$ claude mcp list
claude.ai Slack: … - ✔ Connected
claude.ai Linear: … - ✔ Connected
claude.ai Notion: … - ! Needs authentication
claude.ai Acme Tools: … - ✘ Failed to connect
```

Verified 2026-07-01 with Claude Code 2.1.185:
- A single `claude mcp get "<name>"` returns in ~2.4s. A full `claude mcp list`
  health-checks **every** configured server — measured ~10s on a config with 44
  servers. The preflight must set an explicit timeout (default 60s; timeout →
  inconclusive, see §"Error handling") and the implementation should benchmark
  on a realistic config before choosing list-once vs. per-connector `get`s.
- Status markers: `✔ Connected` (healthy), `! Needs authentication`
  (token expired/never granted), `✘ Failed to connect` (server down),
  `⏸ Pending approval`.
- **Exit codes do not distinguish connected from needs-auth** (both `0`); only a
  *missing* server exits `1`. Therefore we **parse the `Status:` line**, not the
  exit code.

The probe is a plain, token-free subprocess call.

> **Fragility note.** The status glyphs are not a stable CLI contract; a routine
> Claude Code format change would make every parse inconclusive. Because the
> preflight fails open, that failure mode silently disables the entire
> protection — so repeated inconclusive probes must themselves alert
> (§"Error handling").

> **Harness note (future direction).** The `claude mcp list/get` interface is
> Claude-Code-specific. Support for other agent harnesses exposed via CLI
> (Codex, Gemini CLI, …) is **not planned** today, but the probe command and the
> backfill trigger are the two seams that would need to be parameterized per
> harness when/if that lands. The design isolates the probe behind a single
> script (`connector-preflight`) and the backfill trigger behind a single
> renderer (§Layer 3) so a future harness abstraction is an additive change,
> not a rewrite.

## Design

The layers are each independently useful and independently shippable.

### Layer 1 — Pre-session connector preflight

**Where.** A new pre-session step in **all three runner templates** —
`run-scout.sh.tmpl` (briefing + consolidation), `run-dreaming.sh.tmpl`, and
`run-research.sh.tmpl` — alongside the existing `budget-check.sh` gate. The
preflight runs *before* the main `claude` session is launched, so a skip costs
zero model tokens. It needs `$MODE` (the slot key) to resolve the slot type;
`run-dreaming.sh.tmpl` currently defines `MODE` *after* the pre-session gates,
so its `MODE` assignment must be hoisted above the gate block as part of this
change.

**Runner integration — the budget-check pattern, not a bare exit.** The runner
templates run under `set -euo pipefail`, so a bare non-zero exit from the
preflight would abort the script in a way indistinguishable from a crash and
would skip the post-session steps (post-session-backfill, the write-session-cost
fallback row). The dispatcher (`schedule_tick._spawn_runner`) launches the
runner detached with DEVNULL stdio and never reads its exit code, so the exit
status is only meaningful *inside* the runner. The integration therefore follows
the existing budget-check precedent — a guarded call with distinguished exit
codes:

```bash
# exit 0 = proceed; exit 3 = policy skip; anything else = preflight error → fail open
if ! "$SCOUTCTL" connectors preflight --slot-type "$SLOT_TYPE" >> "$LOG_FILE" 2>&1; then
    rc=$?
    if [ "$rc" -eq 3 ]; then
        echo "=== Connector preflight: skipping this run (degraded) ===" >> "$LOG_FILE"
        exit 0
    fi
    echo "=== Connector preflight errored (rc=$rc) — failing open ===" >> "$LOG_FILE"
fi
```

A skip is an orderly `exit 0` (like a budget skip); a preflight crash, a missing
`scoutctl`, or an unhandled exception never masquerades as a deliberate skip.

**What.** A `scoutctl connectors preflight --slot-type <type>` engine command:

1. Loads the connector roster (`scout.connectors.load_registry`).
2. Determines the **critical set** for this slot type via
   `registry.critical_for_slot_type(slot_type)` (the existing
   `required_in_types` mechanism).
3. Probes each critical connector:
   - **MCP connectors** — parsed out of one `claude mcp list` invocation,
     matched by a new explicit `harness_server_name` field (see below).
   - **Bash-probed connectors** (e.g. `github`) — a `preflight_command` run
     directly (e.g. `gh auth status`), harness-independently.
4. Classifies the run: **degraded** iff **any** critical connector is not
   connected; **healthy** otherwise. There is deliberately no quorum/threshold
   axis — see Layer 2.
5. Applies the slot type's `on_degraded` policy:
   - `skip` → write/extend a gap record (Layer 3), emit a
     `run.skipped_degraded` event, fire a Telegram/notification alert, exit
     **3** (the runner converts this to an orderly skip as above).
   - `warn` → write `.scout-cache/connector-degradation-pending.md` naming the
     dark connectors (see Layer 2), exit 0.
   - `run` → exit 0.
6. On **healthy** (only when *all* critical connectors are connected — a
   degraded run that proceeds under `warn`/`run` does **not** trigger this
   edge): record `last_healthy_run[slot_type] = now` in gap state (needed for
   gap `from`; see Layer 3) and **close any open gap** for this slot type (the
   recovery edge).

**Connector ↔ probe wiring.** Neither existing registry can drive the probe as
it stands: `connectors.yaml` keys (`mcp:claude_ai_Slack`) and display names
("Slack") cannot be mechanically matched to `claude mcp list`'s server names
("claude.ai Slack") — the underscore↔dot/space mapping is ambiguous (e.g.
`claude_ai_Github_MCP`) — and `connector-probes.yaml` stores in-session MCP
*tool* names built for the `/scout-setup` wizard, which a headless `scoutctl`
cannot invoke (its `linear` entry also targets the plugin-scoped connector
while scheduled runs use claude.ai Linear). The design therefore adds explicit
per-connector probe fields to `connectors.yaml` (overlay-able like the rest of
the roster):

```yaml
mcp:claude_ai_Slack:
  display_name: Slack
  harness_server_name: "claude.ai Slack"   # matched verbatim against `claude mcp list`
  ...
github:
  display_name: GitHub (gh CLI)
  preflight_command: "gh auth status"      # bash probe; exit 0 = healthy
  ...
```

A connector with neither field is not preflight-checkable and is simply not
probed (it can still be caught by Layer 1b).

**Preflight is best-effort prevention, not a guarantee.** It is a point-in-time
check *before* the session and it **fails open** (§"Error handling"): if the
probe can't determine health, the run proceeds. It therefore has two blind spots
that it cannot cover on its own:

1. **Inconclusive probe** — the probe errored, so the policy was never applied to
   this run.
2. **Mid-session failure** — the probe passed, but a token expired mid-run, or a
   connector not exercised at probe time failed when the session actually called
   it. The decision to run was already committed.

Both blind spots would otherwise produce exactly the outcome this design exists
to prevent: a blind run that the policy *would* have skipped, with no gap
recorded and no backfill offered. Layer 1b closes them.

### Layer 1b — Post-session reconciliation (safety net)

A reactive counterpart to the preflight, run *after* the session completes
(a new post-session step in the runner, alongside the existing cost-tracker).
It answers a different question than the preflight: not "are the connectors up
right now?" but **"which critical connectors actually errored *during* the run
that just finished?"**

**Reuse the existing classifier — do not invent a new dark-connector rule.**
`connector_health_report.compute_critical_alerts` already answers exactly this
question for the most recent session in the telemetry window, *including* the
false-positive suppressions the codebase has already paid for: Pattern #48
(never-wired connectors — zero successes ever — don't alert) and Pattern #54
(healthy in another mode within the liveness window → alive-but-unused, not an
outage). A naive "zero successful calls this session" rule would re-derive both
false-positive classes and recurringly open bogus, backfill-prompting gaps for
never-wired or legitimately-unused critical connectors.

`scoutctl connectors reconcile --slot-type <type>`:

1. Invoke the health-report classifier over the telemetry window
   (`.scout-logs/connector-calls-*.jsonl`); its "current session" (the most
   recent session in the window) is the run that just finished. No session id
   is passed — the runner has no reliable one to pass: the id exists only in
   hook payloads, and `claude-with-retry.sh` can produce several session ids
   per runner invocation.
2. Take the CRITICAL alerts for connectors required in this slot type. If any
   exist **and** the slot type's `on_degraded` policy is `skip` or `warn`,
   treat the just-finished run as degraded and **record a gap** (open, extend,
   or reopen per Layer 3). Under `warn` the user chose to run degraded, but the
   data was still missed — the gap keeps it recoverable. Only `run` opts out of
   gap recording entirely.
3. Emit a `run.reconciled_degraded` event naming the dark connectors.

This is the layer that makes the two-part story honest: the **preflight**
prevents wasted runs when degradation is cheaply detectable upfront; the
**reconciliation** guarantees that a blind run — however it slipped past the
preflight — still leaves a recorded gap. Together they mean *no silent gap*
(for `skip`/`warn` slot types, once both layers are deployed); neither alone
does.

Unlike the preflight (which is independent of #121), reconciliation **reads the
JSONL telemetry and therefore depends on #121 being fixed** — while
`SCOUT_MODE` is unset the telemetry is empty and reconciliation has nothing to
read. This is the one part of the design that is gated on #121, and it degrades
safely: with no telemetry it simply records no retroactive gaps (the preflight
still functions).

### Layer 2 — `on_degraded` policy (configurable)

New block in `scout-config.yaml`, defaulting to today's behavior so existing
installs are unaffected until they opt in:

```yaml
connector_policy:
  on_degraded: run              # skip | warn | run   (default: run — no change)

  # Optional per-slot-type overrides. Recommended posture:
  overrides:
    briefing:       skip
    consolidation:  skip
    dreaming:       warn
    research:       run
```

- `skip` — do not run; record a gap; alert. Cleanest for briefing/consolidation
  (a clean gap beats a poisoned KB).
- `warn` — run, but surface the degradation to the session and record a gap via
  reconciliation (see below). For slot types where a degraded run still has
  value (dreaming synthesizes the existing KB).
- `run` — current behavior; no gate, no gap.

**Degraded means: any critical connector down.** There is deliberately no
quorum/threshold knob (`all`/`majority`/`any`). A count-based rule cannot
express "the two connectors I care about most are down" — under a `majority`
quorum the motivating scenario itself (Slack + Linear down = 8 of the 10
consolidation-critical connectors still up) would pass and run blind. Tolerance
is tuned per slot type by **which connectors are marked critical** in
`required_in_types` — the per-connector mechanism the design already builds on.
A user who wants consolidation to tolerate a WhatsApp outage removes `whatsapp`
from consolidation's critical set (via the connectors overlay), rather than
loosening a global count.

**Warn-mode mechanics.** The preflight cannot export an environment variable
into the session (`scoutctl` is a child process; its environment dies with it —
this is the same env-propagation seam that silently failed in #121), and the
runner's `PROMPT` is a fixed heredoc that reads no env vars. Warn therefore
rides the proven pre-session file seam — the same channel as
`connector-alerts-pending.md`, which `connector_health_report` already writes
for sessions to consume:

- The preflight writes `.scout-cache/connector-degradation-pending.md` naming
  the dark connectors and the instruction set (prepend a degradation banner; do
  not record "nothing found"-style negative signals for the named connectors).
- The session phases (SKILL.md) consume pending files from `.scout-cache/` —
  a one-line addition to the existing pending-alerts consumption step.
- Because a prompt instruction is unenforced, warn does **not** rely on it for
  gap integrity: Layer 1b records the gap mechanically regardless of how well
  the session behaved. KB entries written during a known-degraded run also get
  a mechanical provenance tag (`<!-- degraded-run: slack,linear 2026-07-03 -->`)
  appended by the post-session step, so a later cleanup or backfill can find
  them.

Rationale for the recommended posture: dreaming is largely connector-independent
(it synthesizes the existing KB) so running degraded with a recorded gap is
fine; briefing and consolidation are connector-heavy (all 10 critical
connectors) and are where a blind run does real damage.

### Layer 3 — Gap tracking

**File.** `.scout-state/gaps.jsonl` in the vault, with an **explicit
`.gitignore` rule added by bootstrap**. Note: `.scout-state` is *not* blanket
gitignored — the convention is the opposite (`id-map.json` is deliberately
git-committed by post-session-backfill so stable IDs survive across machines).
`gaps.jsonl` is mutable operational state rewritten on every skip/extend/close;
committing it would be vault-history noise and a merge-conflict generator, so it
gets its own ignore entry.

**Semantics — one gap per contiguous outage per slot type.** A gap is *opened*
on the first skip (or first reconciled degraded run) for a slot type, *extended*
on each subsequent one, *closed* when a healthy run for that slot type occurs,
and — the transition the mid-session blind spot requires — *reopened* when
reconciliation discovers that the run which closed it (or a run after closure)
was in fact blind. A six-week outage is a **single** gap record covering the
whole window — not one record per skipped run. This is what makes the backfill
a single action over the entire period.

State machine:

```
(no gap) ──skip/reconcile──▶ open ──healthy run──▶ recovered ──user──▶ backfilled | dismissed
                              ▲                        │
                              └──reconcile finds blind run ≤ recovery──┘  (reopen + merge window)
```

- **Reopen + merge:** if reconciliation finds the "recovery" run itself (or a
  later run adjacent to the window) was blind, the `recovered` record reverts to
  `open`, its `to` clears, and the window extends — never a second record for
  the same outage. Reconciliation also re-stamps `last_healthy_run` in that
  case (the preflight's stamp at the blind run was wrong).
- The preflight owns open/extend/close in Phase 1; once reconciliation ships it
  shares the same store and owns the reopen edge.

Record shape:

```json
{
  "id": "<ulid>",
  "slot_type": "consolidation",
  "from": "2026-05-15T11:00:00Z",   // last healthy run of this slot type (window start)
  "to": null,                        // null while open; set to recovery ts when closed
  "missing_connectors": ["Slack", "Linear"],  // union across the outage
  "skipped_runs": 41,                // count, for display ("41 runs skipped")
  "status": "open",                  // open | recovered | backfilled | dismissed
  "acknowledged": false
}
```

- `from` is the **last healthy run** of the slot type (tracked per Layer 1
  step 6), so the window is the true span of missing data — not merely the first
  skipped target. If no prior healthy run is known, `from` falls back to the
  first skip's timestamp and the record notes `from_estimated: true`.
- **The backfill command is not stored — it is derived at render time** from
  `slot_type`/`from`/`to` by a single renderer (`scoutctl gaps render-command
  <id>`, also used as a library function). A stored string would be stale by
  construction: it would embed a `--to` while the gap is still open and go
  stale on every extend/reopen. The renderer is the harness seam — when
  multi-harness support lands, it is the one place that changes.
- On recovery, `to` is set and `status → recovered`; the record stays until the
  user backfills (`→ backfilled`) or dismisses (`→ dismissed`).

**`scoutctl gaps list [--json]`** reads this file for the CLI and the macOS app
(the `--json` output includes the rendered backfill command per gap).

### Layer 4 — Surfacing & one-click backfill

The same unacknowledged-gap set appears on three surfaces, all triggering the
identical rendered backfill command:

1. **Briefing / consolidation output.** When the session runs and open/recovered
   gaps exist, it prepends a banner:
   > ⚠️ **41 consolidation runs were skipped May 15 – Jun 26** (Slack, Linear
   > were down). Context from that period hasn't been processed. Run
   > `/scout-backfill --slot-type consolidation --from 2026-05-15 --to 2026-06-26`
   > to catch up.
2. **macOS app.** Reads `gaps.jsonl` (via `scoutctl gaps list --json`), shows a
   badge with the open-gap count and a **Backfill** button per gap. The button
   launches the rendered command through the app's **existing CLI launcher**
   (the AppleScript/Terminal mechanism in `ClaudeLauncher.swift`) — not a
   `claude://` deeplink; that scheme belongs to Claude Desktop and only
   (unreliably) prefills a chat. This is a first-class app surface, not an
   afterthought.
3. **`scoutctl gaps list`.** CLI inspection / scripting.

### Layer 5 — The `/scout-backfill` skill

A Claude Code slash command (`commands/scout-backfill.md`) taking
`--slot-type`, `--from`, `--to`. It reconstructs the *entire* missed window in
**one** invocation by fanning out subagents so no single context is exhausted:

- **One subagent per connector by default; chunk only on volume.** Most
  connectors can paginate a multi-week window in a single agent (Linear issues
  changed in range, Calendar events, Granola/Fathom transcripts). The skill
  splits a connector's window into time chunks only when its volume demands it
  (Slack/Gmail over long windows), and caps concurrent subagents (default 4).
  Each subagent's scan recipe **parameterizes the existing
  `phases/connectors/*.md` playbooks** (same sources, same signal discipline,
  with the date range injected) rather than restating them. The splitting is
  internal to the skill; the user sees one command and one result.
- **Synthesis owns relevance filtering.** Chunk agents summarize what happened
  in their slice; they structurally *cannot* judge what is "still open at
  `--to`" (that requires the end-of-window view). The synthesis pass — which
  reads all subagent summaries — deduplicates items spanning chunk boundaries,
  filters to what is still open/relevant, and writes catch-up entries to the
  KB / action items tagged with a `<!-- backfilled 2026-05-15..2026-06-26 -->`
  provenance marker (the same marker shape *proposed* for phases backport in
  the 2026-06-16 spec / #170 — not yet an existing convention; whichever lands
  first establishes it).
- **Volume discipline.** For high-volume connectors (Slack, Gmail) the subagents
  scope to *signal* — direct mentions, DMs, flagged/unresolved threads — not full
  channel history.
- On completion, mark the gap `status: backfilled, acknowledged: true`.

Claude Code performs the subagent orchestration natively from the skill prompt;
no new engine infrastructure is required for the fan-out.

## Data flow

```
schedule tick (unchanged) ── spawns runner with SCOUT_FORCE_MODE=<slot_key>
   │
   ▼
runner pre-session (all three run-*.sh.tmpl)
   ├─ budget-check.sh            (existing gate)
   └─ scoutctl connectors preflight --slot-type <type>     ← NEW (guarded call)
         ├─ claude mcp list (timeout-bounded) + bash probes
         ├─ degraded = any critical connector down → apply on_degraded
         ├─ healthy → stamp last_healthy_run, close open gap, exit 0
         ├─ warn    → write .scout-cache/connector-degradation-pending.md, exit 0
         ├─ skip    → open/extend gap, alert, exit 3 → runner exits 0 (orderly skip)
         └─ error   → any other rc → runner logs it and FAILS OPEN (run proceeds)
   │ (proceed)
   ▼
main claude session
   └─ consumes connector-degradation-pending.md → degradation banner,
      no negative signals for dark connectors
   └─ if open/recovered gaps exist → prepend backfill banner (rendered command)
   │
   ▼
runner post-session (all three run-*.sh.tmpl)
   ├─ provenance-tag KB writes if run was degraded (warn)
   └─ scoutctl connectors reconcile --slot-type <type>     ← NEW
         ├─ reuse connector_health_report classifier (#48/#54 suppression; needs #121)
         └─ CRITICAL for this slot type AND policy ∈ {skip, warn}
               → open/extend/REOPEN gap retroactively (safety net)
   │
   ▼  (user triggers, any time later)
/scout-backfill --slot-type … --from … --to …
   └─ subagent per connector (chunk on volume) → synthesis (dedup + relevance)
        → KB with provenance marker → mark backfilled
```

## Error handling

- **Preflight probe itself fails or times out** (e.g. `claude mcp list` errors,
  hangs past the timeout, or its output format changes and parsing yields no
  statuses): treat as *inconclusive*, not degraded — the preflight exits with a
  non-skip error code, the runner logs it and **fails open**. A broken probe
  must not silently block all runs. Honest scoping: in Phase 1 (before
  reconciliation ships) a fail-open blind run **does** go unrecorded; only once
  Layer 1b is deployed does "no silent gap" hold.
- **Repeated inconclusive probes alert.** Because fail-open + glyph parsing
  means a routine CLI format change would silently disable the entire
  protection, the preflight persists an inconclusive-streak counter; after
  **3 consecutive** inconclusive probes it fires the same notification channel
  as a degradation alert ("connector preflight has been inconclusive for N
  runs — probe may be broken").
- **Reconciliation with no telemetry** (#121 unfixed, or the session logged no
  rows): record no retroactive gaps and log a warning — degrade safely rather
  than guess. Reconciliation never blocks or retries the run; it only annotates
  after the fact.
- **Malformed `connector_policy`**: `ConfigError` naming the field; fall back to
  global defaults rather than crashing the runner.
- **`gaps.jsonl` write failure**: log to stderr but never crash the runner
  (same discipline as the connector-log hook).
- **Overlapping gaps / races**: gap open/extend/close/reopen is guarded by an
  advisory `fcntl` lock (same pattern as the schedule-tick lock in
  `.scout-state`).

## Testing

- Preflight parser: fixture outputs of `claude mcp list` covering each status
  marker (anonymized per CLAUDE.md); correct degraded classification (any
  critical connector down); unparseable output → inconclusive, not degraded.
- Runner integration: exit 3 → orderly skip (`exit 0`, post-session steps still
  reachable on the skip path's semantics); exit 1/127 → fail open, run proceeds;
  `run-dreaming.sh.tmpl` has `MODE` defined before the preflight call.
- `harness_server_name` matching: verbatim match against `mcp list` output;
  connector with neither probe field → not probed, no false degraded.
- Policy resolution: global default, per-slot override, unknown slot type,
  malformed config → fallback.
- Gap lifecycle: open on first skip; extend (not duplicate) on subsequent skips;
  `from` = last healthy run; close on recovery; **reopen + window merge** when a
  post-closure blind run is reconciled; one record across a multi-run outage
  including the reopen path; `skipped_runs` count correct.
- Fail-open: probe error → run proceeds; 3 consecutive inconclusive probes →
  alert fired.
- Reconciliation: delegates to `compute_critical_alerts` (a never-wired critical
  connector — Pattern #48 — opens **no** gap; cross-mode-alive — Pattern #54 —
  opens no gap); dark critical connector + policy `skip` or `warn` → gap
  recorded; policy `run` → no gap; no telemetry (#121 unfixed) → no gap, warning
  logged; a preflight-skip and a reconciliation for the same outage coalesce
  into one record (not two).
- Warn seam: preflight writes `connector-degradation-pending.md`; degraded-run
  KB writes carry the provenance tag.
- Surfacing: `scoutctl gaps list --json` shape includes the rendered command;
  banner rendering with/without open gaps; rendered command's `--from/--to`
  always match the record's current window (open gap → `--to` = now).
- Backfill skill: per-connector agent default, chunking only over the volume
  threshold, concurrency cap respected; synthesis dedups boundary-spanning
  items; provenance marker written; gap marked `backfilled`.

## Relationship to #121

#121 (runner never exports `SCOUT_MODE`, so the JSONL telemetry is dark) relates
to this design in two different ways depending on the layer:

- The **preflight (Layer 1)** does **not** read the JSONL telemetry — it probes
  live connector state — so it works even while #121 is unfixed.
- The **post-session reconciliation (Layer 1b)** *does* read the telemetry and is
  therefore **gated on #121**. Until `SCOUT_MODE` is exported the telemetry is
  empty and reconciliation records nothing (it degrades safely; the preflight
  still functions).

So #121 is a prerequisite for the *safety net*, not for the design as a whole.
It still needs its own fix (a ~2-line export in the runner templates) and will
be linked from the PR that implements this spec — ideally landing before or
alongside Phase 2 below.

## Phasing (shippable increments)

1. **Preflight + skip/warn/run policy** (Layers 1–2) — the operational win;
   stops blind runs immediately. Independent of #121. (Known limitation until
   Phase 2: fail-open blind runs go unrecorded.)
2. **Gap tracking + CLI + briefing banner** (Layers 3–4, minus the app) — makes
   gaps visible and recoverable-in-principle. Add **post-session reconciliation
   (Layer 1b)** here, once #121 is fixed, so both the proactive and reactive
   paths feed the same gap store and the reopen edge exists.
3. **`/scout-backfill` skill** (Layer 5) — the recovery action.
4. **macOS app gap surface + Backfill button** — first-class app integration
   via the existing CLI launcher.

## Acceptance

1. A scheduled briefing/consolidation with a critical connector down and
   `on_degraded: skip` does **not** run the main session, records a single open
   gap, and alerts — at zero model-token cost for the skip — and a preflight
   *error* (as opposed to a degraded verdict) never causes a skip.
2. `on_degraded` is honored per slot type; default config leaves current
   behavior unchanged. The motivating scenario (Slack + Linear down,
   consolidation `skip`) always skips — there is no threshold setting under
   which it runs blind.
3. A contiguous multi-run outage produces exactly **one** gap record spanning
   last-healthy-run → recovery, including when a blind run temporarily "closed"
   the gap and reconciliation reopened it.
4. A run that slips past the preflight (inconclusive probe, or a connector that
   fails mid-session) but was in fact blind still produces a gap via
   post-session reconciliation — for `skip` and `warn` slot types, with #121
   fixed — and never a duplicate record. Never-wired connectors (Pattern #48)
   do not produce gaps.
5. The briefing output and the macOS app both surface the gap and trigger the
   identical rendered `/scout-backfill` command, whose window always matches
   the record's current state.
6. `/scout-backfill` reconstructs the full window via subagent fan-out without
   context exhaustion and marks the gap backfilled.
