# Scout brain structure: mode-based vs connector-based — accepted ADR

> **Implementation status:** the connector-based structure recommended below is **shipped and live** — `phase_assembly.py` + `bootstrap.py` assemble `phases/{connectors,core,modes,research}/` with section-level mode filtering, and `phases/core/00-run-modes.md` is the live dispatch table. What remains is the Phase-1 completeness work and the Phase-2 vault adoption (see Migration strategy).

**Date:** 2026-06-22
**Status:** Accepted — implemented (decision recorded 2026-07-05)
**Executed by:** the briefing-mode layer (#153, `ced9247` — Run Modes table, weekend scope, Monday Preview, briefing digest) and the connector `phases/` backport that established the modular per-connector sections. This document is the design-rationale record (ADR) for that direction: why Scout's brain is connector-based, and what trade-off was consciously accepted.

## Problem & why now

Scout has **two brains that have diverged in structure**:

- A running instance's `SKILL.md` (what scheduled runs execute) is **mode-based** — organized by *run mode*: `MORNING BRIEFING MODE`, `CONSOLIDATION MODE`, `WEEKEND BRIEFING MODE`, each a linear step sequence. It has accumulated ~24 mistake-audit Pattern fixes and several capabilities over months of use.
- The engine (`phases/`, what `/scout-update` renders from) is **connector-based** — organized by *connector × scan-direction* (`Calendar Outbound Scan`, `GitHub Query`, `Slack Inbound Scan`, …), with a `Run Modes` dispatch table (added in the briefing-mode layer) mapping the run's slot key → which sections to run.

Because the two structures don't match, every `/scout-update` that touches `SKILL.md` produces a 3-way-merge conflict (a `.proposed-merge` sidecar) that has to be resolved by keeping the vault version, and the regenerated runner now references a "Run Modes table" the mode-based brain doesn't contain. That friction is a standing tax, and it will recur on every release that changes the brain.

This was a fork worth settling deliberately — and, because it sets the long-term shape of how every Scout instance thinks, it got review by more than one person before being accepted.

## The structures, concretely

**Mode-based** (a running vault today):
```
# MORNING BRIEFING MODE          # CONSOLIDATION MODE           # WEEKEND BRIEFING MODE
  MB Step 1: Read KB               PHASE 1: What did the user do   WB Step 1: ...
  MB Step 2: Query connectors      PHASE 2: Delta scan             ...
  ... Step 6: Commit               ... PHASE 6: Notify
```
One self-contained narrative per mode; connector specifics are restated inside each mode.

**Connector-based** (the engine today):
```
## Run Modes — Read This First   ← dispatch table: slot key → which sections to run
## Calendar  Outbound Scan / Inbound Scan / Query / Cross-Check / KB-Updates
## GitHub    Outbound Scan / Inbound Scan / Query / ...
## Slack ... / Linear ... / Granola ...
```
Connector logic stated once; the run mode selects which sections execute, via the dispatch table. Assembled from modular `phases/connectors/*.md` + `phases/core/*.md`.

**Hybrid** (strawman): keep top-level mode orchestration, but factor shared connector logic into referenced sub-sections.

## Genuine evaluation

Scored on six criteria. The three the maintainer weighted heaviest — **vault↔engine convergence, self-improvement-loop fit, distributability** — carry the recommendation; legibility / mode-handling / prompt-cost are scored but secondary. (The maintainer explicitly *de-prioritized* personal legibility, which is the strongest argument *for* mode-based — that trade-off was pressure-tested in review and accepted, see Decisions taken.)

| Criterion (weight) | Mode-based | Connector-based | Hybrid |
|---|---|---|---|
| **Convergence** (high) | ✗ Poor — it's the vault-only shape; every engine brain change conflicts forever unless the engine reverts to it | ✓ Strong — adopting the engine's shape makes convergence true *by construction*; future changes merge clean | ~ Weak — still diverges from the engine's pure connector model |
| **Self-improvement fit** (high) | ~ Weak — monolithic; "improve how GitHub is scanned" must be edited in every mode that scans it (duplication); Patterns accrete into a sprawling file | ✓ Strong — modular per-connector units; one focused edit applies across all modes via the table; Patterns land in the relevant phase | ~ Mixed — indirection without full modularity |
| **Distributability** (high) | ~ Weak — less modular; harder for other users to enable/disable pieces | ✓ Strong — modular, opt-in connectors; the optional-connector catalog rides on this shape | ~ Weak |
| Legibility (low) | ✓ Strong — linear "this mode does X then Y"; easiest for a human to read top-to-bottom | ~ Medium — must read the table then jump across sections to assemble "what a morning run does" | ~ Medium |
| Mode-handling (med) | ✓ Strong — modes are explicit top-level banners | ~ Medium — modes via the dispatch table + Scan-vs-Query split; explicit enough since the Run Modes table | ✓ Strong |
| Prompt cost (med) | ~ Medium — connector specifics duplicated across modes | ✓ Good — stated once; modes select sections | ~ Medium |

**Recommendation: connector-based.** On the three weighted criteria it wins decisively; mode-based wins only on legibility, which was explicitly de-prioritized. Connector-based is also the one structure that *ends* the recurring merge tax (convergence by construction) and the only one the modular self-improvement loop and the optional-connector catalog are built to exploit. The hybrid is worst-of-both: it keeps some legibility but neither fully converges with the engine nor delivers the modular self-improvement win. The honest cost of the recommendation is human legibility — see Decisions taken.

## Migration strategy: upstream-first, then the vault adopts

Do **not** re-author the vault's `SKILL.md` into the new structure in place — that is the lossy local re-graft we already proved drops content. Instead:

- **Phase 1 — make the engine complete & canonical.** Finish porting the vault's brain content into the engine's connector-based phases (de-personalized): the ~24 SKILL Patterns and the capabilities (weekend mode, personal-text scanning, Monday Preview, briefing-side digest, mode determination, …). *Status (as of 2026-07-05):* under way — the parser graph layer, Validation Pass, and the briefing-mode layer (Run Modes table + weekend scope + Monday Preview + briefing digest, incl. `phases/core/monday-preview.md`) have all landed. Genuinely remaining: the **de-personalized ~24-Pattern port** and the **personal-text connector phase** (via the optional-connector catalog, spec in #152). The enrichment-recall subsystem's spec landed (#150) and its implementation is in review (#179).
- **Phase 2 — the vault adopts.** Once the engine's connector-based `SKILL.md` is a behavioral **superset** of the vault's mode-based one, the vault migrates by re-rendering from the engine and *accepting* the connector-based result (it no longer keeps its mode-based version). Because everything is already in the engine, this is "accept the engine's version," not a risky re-graft, and the snapshots converge naturally. The runner↔SKILL seam resolves automatically (the new brain has the Run Modes table the runner expects).

This sequencing makes convergence and distributability true *by construction*, and reduces the migration's risk to "did we finish Phase 1 completely?" — which is a checklist, not a judgment call.

## Zero-loss guarantee

The migration adopts the engine's brain only after a **completeness gate** passes:

1. **Inventory** the current vault `SKILL.md`: every Pattern # (the ~24), every capability, every per-mode behavior (what morning vs consolidation vs weekend each do).
2. **Assert presence** of every inventory item in the engine's connector-based `SKILL.md` (grep/automated checks where possible; structured review where not). Phase 2 does not proceed until the inventory is 100% covered.
3. **Behavioral validation** before it goes live: dry-run a morning briefing, a consolidation, and a weekend briefing against the migrated brain in a sandbox and diff the behavior/outputs against a mode-based baseline run; investigate every divergence.

The lossy one-shot union merge attempted earlier is explicitly *not* the mechanism — the loss it caused is the reason for the completeness gate.

## Decisions taken & accepted trade-offs

- **Connector-based is the canonical structure.** The legibility cost was weighed directly and consciously accepted: a human reading the brain top-to-bottom must consult the Run Modes table and jump across sections. Convergence, self-improvement fit, and distributability were judged to outweigh it.
- **Upstream-first migration is accepted**, including that it gates the vault migration on finishing the Phase-1 upstream effort (several PRs). No interim measures; in the meantime the vault keeps its mode-based brain and absorbs the (benign) runner↔SKILL seam.
- **Validation harness is deferred, not skipped:** how to dry-run the three modes safely without touching a live vault/connectors still needs design — it is a prerequisite of Phase 2, tracked as part of the migration work.
- **Other instances migrate via the same path:** any other mode-based vault adopts through the standard `/scout-update` flow once the engine is complete.
- **Point of no return is accepted:** after Phase 2 the vault is connector-based; reverting means resurrecting the mode-based file from git history, which was judged an acceptable escape hatch.

## Out of scope
- The actual implementation (the upstream PRs that complete Phase 1, and the Phase-2 adoption + validation harness) — those follow now that this direction is accepted.
- The optional-connector catalog and enrichment-recall subsystem have their own specs; this proposal depends on them only insofar as they complete Phase 1.
