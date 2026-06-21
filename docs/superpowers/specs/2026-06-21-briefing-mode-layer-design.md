# Briefing-mode layer for the connector-based SKILL — design

**Date:** 2026-06-21
**Status:** Proposed (design) — for review
**Closes:** the missing run-mode behavior in the connector-based `SKILL.md` (weekday-briefing vs weekend-briefing vs consolidation), and the dropped mode signal between the dispatcher and the model.

## Problem

The connector-based `SKILL.md` (assembled from `phases/core` + `phases/connectors`) organizes work by **connector × scan-direction** (`Calendar Outbound Scan`, `Calendar Query — Briefing Data Gathering`, …). It has no explicit notion of *run mode*, and three behaviors that depend on mode are absent:

1. **Weekend briefing** — a lighter Saturday/Sunday run (reduced work-connector scan, personal-task focus) is a distinct slot in the schedule but has no distinct behavior in the brain file.
2. **Monday Preview** — surfacing the next workday's meetings/deadlines on a weekend run.
3. **Scout Digest on the briefing side** — the "what {{INSTANCE_NAME}} did across sessions" digest exists only in the dreaming phase (`phases/modes/kb-deep-work.md`), not for briefing/consolidation runs.

Underlying all three is a plumbing gap: **the dispatcher knows the exact run mode but the model never sees it.**

## Current mode flow (the thing we're extending)

Mode is already a first-class concept at the scheduling layer, and is **lost** one hop before the model:

| Layer | State |
|---|---|
| **Schedule** (`engine/scout/schedule.snapshot.json`) | Distinct slots already exist, incl. `weekend-briefing` (type `briefing`, separate from `morning-briefing`) and `morning/midday/afternoon/evening-consolidation`. ✅ |
| **Dispatcher** (`engine/scout/scripts/schedule_tick.py`) | Spawns the runner with `SCOUT_FORCE_MODE=<slot_key>` — the exact slot, weekend included. ✅ |
| **Runner** (`templates/run-scout.sh.tmpl`) | Captures `MODE="${SCOUT_FORCE_MODE:-manual}"` and uses it for logging / pre-filter / cost — but the **session prompt tells the model to re-derive mode from the clock** (*"Step 2: Determine your mode based on the current hour"*). The authoritative slot key never reaches the model. ❌ |
| **Assembly** (`_assemble` in `engine/scout/scripts/bootstrap.py`) | `SKILL` = `core`+`connectors`, `modes={briefing, consolidation}`, `slot=None`. All briefing+consolidation sections land in one file; the model picks at runtime. `select_sections` already supports `slot=` and `modes=` filtering (`phases/scripts/phase_assembly.py`). |

Two consequences: (a) the model can't tell `weekend-briefing` from `morning-briefing` (both re-derive to "it's morning → briefing"), and (b) mode behavior rides entirely on clock/weekday derivation — historically the most fragile surface (cf. the weekday-derivation failure class).

> Note: `mode:` frontmatter selects the **assembly target** (which of SKILL/DREAMING/RESEARCH a section lands in), *not* the runtime slot. Weekend is a **slot**, not an assembly target — so weekend content is tagged `mode: [briefing]` (it belongs in SKILL) and is branched on **at runtime** by slot key. No new `mode:` type is introduced.

## Goals / non-goals

**Goals**
- The model dispatches on the **authoritative** run mode (the dispatcher's slot key), not a re-derived clock guess; `manual` runs fall back to day+hour derivation.
- Weekend-briefing, Monday Preview, and briefing-side Scout Digest behaviors exist and are applied deterministically per mode.
- Keep **one** assembled `SKILL.md` and **one** cat-4 merge/snapshot surface (the upgrade machinery we just stabilized).
- All new content is tenant-agnostic (template vars; no instance-specific data) — this is the public engine.

**Non-goals**
- No new assembled artifacts or per-mode brain files (that's Alternative B, rejected below).
- No change to the schedule slots or the dispatcher's `SCOUT_FORCE_MODE` contract (already correct).
- No hardcoded run hours in the brain content — the dispatch table keys on **slot semantics**, not a specific schedule.

## Design (Option C — one SKILL + explicit dispatch table)

Three parts.

### 1. Plumb the mode signal to the model
`templates/run-scout.sh.tmpl`: pass the resolved `$MODE` into the session prompt instead of asking the model to read the clock. Replace *"Step 2: Determine your mode based on the current hour"* with, in effect:

> Your run mode is **`$MODE`** (the dispatcher's slot key). Follow the matching row of the Run Modes table in SKILL.md. If `$MODE` is `manual`, derive the closest mode from the current day and hour.

`$MODE` is already in scope in the runner. This is the load-bearing fix — the other two parts are inert without it.

### 2. `phases/core/run-modes.md` (new) — the dispatch table
A `mode: [briefing, consolidation]` core section, ordered to render near the **top** of SKILL.md (so the model reads it before the connector sections). It maps slot key → behavior:

| `SCOUT_FORCE_MODE` (slot key) | Behavior |
|---|---|
| `morning-briefing` | **Full briefing.** Run the connector **Query — Briefing Data Gathering** sections; build the full action-items list; emit the wrap notification + Scout Digest. |
| `weekend-briefing` | **Light briefing.** Reduced scan: personal-task + calendar focus, skip/abbreviate heavy work-connector scans (per the "weekend scope" note); add **Monday Preview**; weekend-appropriate framing of items. |
| `morning- / midday- / afternoon- / evening-consolidation` | **Delta scan.** Run the connector **Outbound/Inbound Scan** sections + per-item reconciliation since the last run. |
| `manual` / unset | Derive the closest mode from current day + hour, then follow that row. |

The table is the single source of "what each mode does"; connector/core sections stay mode-neutral and are *invoked by* the table rather than each re-explaining mode.

### 3. The three mode-specific behaviors (small, clearly-scoped additions)
- **Monday Preview** — a `mode: [briefing]` core section, applied only on `weekend-briefing` per the table: preview the next workday's meetings + imminent deadlines.
- **Weekend scope note** — a short rule (referenced by the `weekend-briefing` row) defining which work-connector scans run lighter or are skipped on weekends.
- **Scout Digest (briefing side)** — add the digest block to the briefing wrap/notification section (`phases/connectors/slack.md` / `phases/core/action-items.md`, TBD in implementation) so briefing/consolidation runs can emit it, mirroring the dreaming phase that already has it.

## Alternatives considered

**A — One SKILL, runtime dispatch with no explicit table.** Same single-file shape, but weekend behavior is described inline and the model *infers* what each mode means. Rejected: it reintroduces exactly the inference-fragility this is meant to remove; the dispatch table is cheap insurance.

**B — Assembly-time specialization (per-mode brain files).** Use `select_sections(slot=…)` to assemble separate mode-specialized files; the runner reads the one matching its slot. Rejected for now: it multiplies assembled artifacts **and** their `.scout-state/last-assembled/` snapshots and cat-4 sidecar/merge surfaces — the machinery we just spent significant effort stabilizing — and departs from the engine's established "one SKILL, model picks" model that briefing and consolidation already share. Revisit only if prompt size becomes a real constraint.

## Implementation plan
1. `templates/run-scout.sh.tmpl` — inject `$MODE` into the session prompt; keep the `:-manual` fallback wording.
2. `phases/core/run-modes.md` — new section (frontmatter `phase: core`, `mode: [briefing, consolidation]`, an early `slot`/ordering hint), containing the dispatch table.
3. `phases/core/…` — add the **Monday Preview** section (`mode: [briefing]`) and the **weekend scope** note.
4. Briefing notification section — add the **Scout Digest** block (exact host file decided during implementation; candidates: `phases/connectors/slack.md`, `phases/core/action-items.md`).
5. Regenerate + diff `SKILL.md` for a sample config; confirm the table + new sections render and parse.

## Testing / verification
- `_assemble("SKILL")` includes Run Modes table, Monday Preview, and Scout Digest, and still parses (no `parse_phase_file` warning); `_assemble("DREAMING"/"RESEARCH")` unchanged (the new sections are `mode: [briefing]`-scoped and must not leak).
- Runner template renders with `$MODE` interpolated into the prompt; `shellcheck` clean (the repo lints runner templates).
- Existing bootstrap-upgrade / phase-assembly / ontology tests stay green.
- Manual: assemble against a sample vault config and read the resulting SKILL top-section for clarity.

## Risks / open questions
- **Behavior change for existing consolidation runs:** making mode explicit (vs clock-derived) should be a no-op in practice, but every scheduled run reads this file — call out in the PR for review.
- **`manual` fallback:** must still derive a sensible mode from day+hour; weekends with no consolidation slot should map to a briefing-style read.
- **Prompt size:** weekday runs carry the (small) weekend sections. Accepted; revisit with Alternative B only if it matters.
- **Host file for Scout Digest** on the briefing side — settle in implementation review.

## Out of scope / future
- Enrichment-recall subsystem (separate effort — no engine home yet).
- The de-personalized **Patterns** batch (separate PRs).
- Google Messages as an opt-in connector (separate PR).
