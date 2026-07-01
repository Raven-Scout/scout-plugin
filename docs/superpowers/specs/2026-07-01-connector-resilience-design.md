# Connector Resilience — Skip-If-Degraded, Gap Tracking & Backfill — Design

**Date:** 2026-07-01
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
claude.ai Keboola Jokes: … - ✘ Failed to connect
```

Verified 2026-07-01 with Claude Code 2.1.185:
- One `claude mcp get` call returns in ~2.4s.
- Status markers: `✔ Connected` (healthy), `! Needs authentication`
  (token expired/never granted), `✘ Failed to connect` (server down),
  `⏸ Pending approval`.
- **Exit codes do not distinguish connected from needs-auth** (both `0`); only a
  *missing* server exits `1`. Therefore we **parse the `Status:` line**, not the
  exit code.

The probe is a plain, token-free subprocess call.

> **Harness note (future direction).** The `claude mcp list/get` interface is
> Claude-Code-specific. Support for other agent harnesses exposed via CLI
> (Codex, Gemini CLI, …) is **not planned** today, but the probe command and the
> backfill trigger are the two seams that would need to be parameterized per
> harness when/if that lands. The design isolates both behind a single script
> (`connector-preflight`) and a single stored command string (in the gap record)
> so a future harness abstraction is an additive change, not a rewrite.

## Design

Three layers, each independently useful and independently shippable.

### Layer 1 — Pre-session connector preflight

**Where.** A new step in the runner's pre-session bash section
(`templates/run-scout.sh.tmpl`, alongside the existing `budget-check.sh` gate,
which already establishes the "skip this run with `exit 0`" precedent). The
preflight runs *before* the main `claude` session is launched, so a skip costs
zero model tokens. It has access to `$MODE` (the slot key) and thus knows the
slot type.

**What.** A `scoutctl connectors preflight --slot-type <type>` engine command:

1. Loads the connector roster (`scout.connectors.load_registry`) and the probe
   registry (`scout.scripts.connector_probes.resolve_registry`, incl. the user
   overlay from the connector-probe-overlay work, #97).
2. Determines the **critical set** for this slot type via
   `registry.critical_for_slot_type(slot_type)` (the existing
   `required_in_types` mechanism).
3. Runs the harness probe once (`claude mcp list`) and parses each critical
   connector's `Status:` line. Bash-probe connectors (e.g. `github` →
   `gh auth status`) are probed via their existing `connector-probes.yaml`
   `command`, harness-independently.
4. Classifies the run as **healthy** (all critical connectors `✔ Connected`) or
   **degraded** (one or more not connected), and applies the `on_degraded`
   policy (Layer 2) to decide: `skip`, `warn`, or `run`.
5. On `skip`: writes/extends a gap record (Layer 3), emits a `run.skipped_degraded`
   event, fires a Telegram/notification alert, and exits non-zero so the runner
   halts before the session.
6. On `warn`: exports `SCOUT_DEGRADED_CONNECTORS="<comma-separated display names>"`
   for the runner to pass into the session, then exits `0`.
7. On `run` / healthy: exits `0`. On healthy, also records
   `last_healthy_run[slot_type] = now` in gap state (needed for gap `from`; see
   Layer 3) and **closes any open gap** for this slot type (the recovery edge).

The `mcp_tool`-vs-`bash` probe distinction reuses the existing `ProbeKind` enum;
new connector categories in the future add a `ProbeKind` variant and the
preflight handles them without structural change.

### Layer 2 — `on_degraded` policy (configurable)

New block in `scout-config.yaml`, defaulting to today's behavior so existing
installs are unaffected until they opt in:

```yaml
connector_policy:
  # Global defaults.
  on_degraded: run              # skip | warn | run   (default: run — no change)
  required_threshold: all       # all | majority | any

  # Optional per-slot-type overrides. Recommended posture:
  overrides:
    briefing:       { on_degraded: skip, required_threshold: all }
    consolidation:  { on_degraded: skip, required_threshold: majority }
    dreaming:       { on_degraded: warn, required_threshold: any }
    research:       { on_degraded: run }
```

- `on_degraded`:
  - `skip` — do not run; record a gap; alert. Cleanest for briefing/consolidation
    (a clean gap beats a poisoned KB).
  - `warn` — run, but set `SCOUT_DEGRADED_CONNECTORS` so the session prepends a
    degradation banner to its output and refrains from writing
    "nothing found"-style negative signals for the dark connectors.
  - `run` — current behavior; no gate.
- `required_threshold` defines what "degraded" means relative to the critical
  set: `all` (every critical connector up), `majority` (> half), `any` (≥ 1 up).

Rationale for the recommended posture: dreaming is largely connector-independent
(it synthesizes the existing KB) so running degraded is fine; briefing and
consolidation are connector-heavy (all 10 critical connectors) and are where a
blind run does real damage.

### Layer 3 — Gap tracking

**File.** `.scout-state/gaps.jsonl` (vault state; gitignored like other
`.scout-state` runtime files).

**Semantics — one gap per contiguous outage per slot type.** A gap is *opened*
on the first skip for a slot type, *extended* on each subsequent skip, and
*closed* when a healthy run for that slot type occurs. A six-week outage is a
**single** gap record covering the whole window — not one record per skipped
run. This is what makes the backfill a single action over the entire period.

Record shape:

```json
{
  "id": "<ulid>",
  "slot_type": "consolidation",
  "from": "2026-05-15T11:00:00Z",   // last healthy run of this slot type (window start)
  "to": null,                        // null while open; set to recovery ts when closed
  "missing_connectors": ["Slack", "Linear"],  // union across the outage
  "skipped_runs": 41,                // count, for display ("41 runs skipped")
  "backfill_command": "/scout-backfill --slot-type consolidation --from 2026-05-15 --to 2026-06-26",
  "status": "open",                  // open | recovered | backfilled | dismissed
  "acknowledged": false
}
```

- `from` is the **last healthy run** of the slot type (tracked per Layer 1
  step 7), so the window is the true span of missing data — not merely the first
  skipped target. If no prior healthy run is known, `from` falls back to the
  first skip's timestamp and the record notes `from_estimated: true`.
- `backfill_command` is stored as a ready-to-run string so every surface
  (briefing output, macOS app, CLI) triggers the identical action. This string
  is the harness seam noted above.
- On recovery, `to` is set and `status → recovered`; the record stays until the
  user backfills (`→ backfilled`) or dismisses (`→ dismissed`).

**`scoutctl gaps list [--json]`** reads this file for the CLI and the macOS app.

### Layer 4 — Surfacing & one-click backfill

The same unacknowledged-gap set appears on three surfaces, all triggering the
one stored `backfill_command`:

1. **Briefing / consolidation output.** When the session runs and open/recovered
   gaps exist, it prepends a banner:
   > ⚠️ **3 consolidation runs were skipped May 15 – Jun 26** (Slack, Linear were
   > down). Context from that period hasn't been processed.
   > **[Backfill this period →](claude://…)**
   The link is a Claude Code deeplink that opens the CLI and runs the stored
   `/scout-backfill …` command.
2. **macOS app.** Reads `gaps.jsonl`, shows a badge with the open-gap count and a
   **Backfill** button per gap. The button fires the same command (deeplink today;
   harness-appropriate trigger in the multi-harness future). This is a
   first-class app surface, not an afterthought.
3. **`scoutctl gaps list`.** CLI inspection / scripting.

### Layer 5 — The `/scout-backfill` skill

A Claude Code slash command (`commands/scout-backfill.md`) taking
`--slot-type`, `--from`, `--to`. It reconstructs the *entire* missed window in
**one** invocation by fanning out subagents so no single context is exhausted:

- **Split by connector × time chunk.** The skill divides the window into chunks
  sized to fit comfortably in a subagent context (e.g. weekly), and spawns one
  subagent per connector per chunk. Each subagent queries its connector's
  historical API for its slice (Linear issues changed in the range, Calendar
  events, Granola/Fathom transcripts, Gmail threads, Slack mentions/DMs/flagged
  threads) and distills to a compact summary. The number of agents scales with
  the window length — a 6-week gap spins up more agents than a 3-day gap. This
  splitting is internal to the skill; the user sees one button and one result.
- **Synthesis.** A final pass reads all subagent summaries and writes catch-up
  entries to the KB / action items, tagged as backfilled with a
  `<!-- backfilled 2026-05-15..2026-06-26 -->` provenance marker (mirroring the
  phases-backport provenance convention, #170).
- **Volume discipline.** For high-volume connectors (Slack, Gmail) the subagents
  scope to *signal* — direct mentions, DMs, flagged/unresolved threads — not full
  channel history, and only surface what is *still open/relevant* at `--to`, so
  stale-but-resolved items don't reappear as fresh work.
- On completion, mark the gap `status: backfilled, acknowledged: true`.

Claude Code performs the subagent orchestration natively from the skill prompt;
no new engine infrastructure is required for the fan-out.

## Data flow

```
schedule tick (unchanged) ── spawns runner with SCOUT_FORCE_MODE=<slot_key>
   │
   ▼
runner pre-session (run-scout.sh.tmpl)
   ├─ budget-check.sh            (existing gate)
   └─ scoutctl connectors preflight --slot-type <type>     ← NEW
         ├─ claude mcp list  (token-free) + bash probes
         ├─ apply on_degraded policy
         ├─ healthy → record last_healthy_run, close open gap, exit 0
         ├─ warn    → export SCOUT_DEGRADED_CONNECTORS, exit 0
         └─ skip    → open/extend gap in gaps.jsonl, alert, exit ≠0 (runner halts)
   │ (exit 0)
   ▼
main claude session
   └─ if SCOUT_DEGRADED_CONNECTORS set → prepend degradation banner
   └─ if open/recovered gaps exist    → prepend backfill banner + deeplink
   │
   ▼  (user clicks, any time later)
/scout-backfill --slot-type … --from … --to …
   └─ subagent fan-out (connector × time chunk) → synthesis → KB → mark backfilled
```

## Error handling

- **Preflight probe itself fails** (e.g. `claude mcp list` errors/times out):
  treat as *inconclusive*, not degraded — default to running (fail-open), and log
  a warning. A broken probe must not silently block all runs.
- **Malformed `connector_policy`**: `ConfigError` naming the field; fall back to
  global defaults rather than crashing the runner.
- **`gaps.jsonl` write failure**: log to stderr but never crash the runner
  (same discipline as the connector-log hook).
- **Overlapping gaps / races**: gap open/extend/close is guarded by the same
  advisory-lock pattern used elsewhere in `.scout-state`.

## Testing

- Preflight parser: fixture outputs of `claude mcp list` covering each status
  marker; correct healthy/degraded classification per `required_threshold`.
- Policy resolution: global default, per-slot override, unknown slot type,
  malformed config → fallback.
- Gap lifecycle: open on first skip; extend (not duplicate) on subsequent skips;
  `from` = last healthy run; close on recovery; one record across a multi-run
  outage; `skipped_runs` count correct.
- Fail-open: probe error → run proceeds.
- Surfacing: `scoutctl gaps list --json` shape; banner rendering with/without
  open gaps.
- Backfill skill: chunking math scales with window length; provenance marker
  written; gap marked `backfilled`.

## Relationship to #121

#121 (runner never exports `SCOUT_MODE`, so the *retrospective* telemetry is
dark) is **complementary and independent**:

- This design's preflight does **not** read the JSONL telemetry, so it works
  even while #121 is unfixed.
- Once #121 is fixed, the retrospective health report and this proactive
  preflight reinforce each other: preflight prevents blind runs going forward;
  the health matrix gives historical visibility.

#121 still needs its own fix and will be linked from the PR that implements this
spec.

## Phasing (shippable increments)

1. **Preflight + skip/warn/run policy** (Layers 1–2) — the operational win;
   stops blind runs immediately.
2. **Gap tracking + CLI + briefing banner** (Layers 3–4, minus the app) — makes
   gaps visible and recoverable-in-principle.
3. **`/scout-backfill` skill** (Layer 5) — the recovery action.
4. **macOS app gap surface + Backfill button** — first-class app integration.

## Acceptance

1. A scheduled briefing/consolidation with a critical connector down and
   `on_degraded: skip` does **not** run the main session, records a single open
   gap, and alerts — at zero model-token cost for the skip.
2. `on_degraded` is honored per slot type; default config leaves current
   behavior unchanged.
3. A contiguous multi-run outage produces exactly **one** gap record spanning
   last-healthy-run → recovery.
4. The briefing output and the macOS app both surface the gap and trigger the
   identical `/scout-backfill` command.
5. `/scout-backfill` reconstructs the full window via subagent fan-out without
   context exhaustion and marks the gap backfilled.
