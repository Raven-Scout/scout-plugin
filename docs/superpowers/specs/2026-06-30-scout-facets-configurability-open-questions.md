# Scout "facets": configurable feature layers — open questions

**Date:** 2026-06-30
**Status:** Open questions / for discussion — no implementation proposed yet
**Motivating example:** PR #176 (user profile + goals-as-priority lens + relationship maintenance) — a back-port of one contributor's personal-vault capability into the public engine.

## Context

Scout is increasingly grown by **back-porting a capability that proved out in a single contributor's personal vault into the shared engine**. PR #176 is the canonical example: it adds a "facet" (a user-profile model, a goals prioritization lens, and relationship-maintenance nudges) that is woven into the existing session-type brain files rather than living as a standalone, toggleable unit.

The individual change is sound. The *pattern* — accreting facets this way — surfaces four structural problems that we don't yet have answers for. This note captures them as open questions before we add the next facet, so we decide the strategy deliberately rather than discovering it one PR at a time.

A facet today is not one artifact. PR #176 writes state across **four layers**:

| Layer | What PR #176 adds | File / mechanism |
|---|---|---|
| **Phase fragments** | `phases/core/00-about-you.md`, `phases/core/relationships.md`, edits to `action-items.md`, `claude-sessions.md`, `feedback-processing.md` | assembled into SKILL/DREAMING/RESEARCH by `bootstrap.py:_assemble` |
| **Seeded vault files** | `knowledge-base/profile/{about-you,communication,goals}.md` | cat-2 (install-only, never-overwritten) via `_INSTALL_ONLY_TEMPLATES` + `_stage_install_only_seeds` |
| **Schema / entity data** | `last_interaction` on `person` + the instruction to write it onto `people/*.md` | `templates/.../ontology/schema.yaml.tmpl` |
| **Config / migration state** | `profile-files-v1` marker | `scout-config.yaml` `plugin.applied_migrations` |

Plus a new user-facing command (`/scout-profile`) and edits to `/scout-setup` and `/scout-update`.

## Problem 1 — Facets are not reversible

There is no inverse operation for any of the four layers above. Disabling or removing a facet means:

- **Phase fragments** can be pulled from a *future* re-render, but only through the 3-way merge against `.scout-state/last-assembled/` — and only if the user hasn't hand-edited the assembled output.
- **Seeded cat-2 files are never removed.** By design `_stage_install_only_seeds` only ever *adds*; an upgrade that dropped the facet would orphan `profile/` rather than clean it up.
- **Entity-data writes** (`last_interaction:` smeared across `people/*.md`) have no rollback at all.
- **The migration marker** stays in `applied_migrations` forever.

"Reversible" in practice means *ask Claude to manually clean up*, which is exactly the fragility we want to avoid as facet count grows.

## Problem 2 — Each facet makes upgrades heavier and is all-or-nothing

To land one facet, PR #176 had to modify the **global** bootstrap pipeline:

- add a dir to `_CAT1_DIR_LAYOUT`,
- add three entries to `_INSTALL_ONLY_TEMPLATES`,
- add a migration marker, and
- flip `upgrade()` to **replay all install-only seeds** (`_stage_install_only_seeds(cfg)`), not just the facet's.

That last change is a scope broadening: any cat-2 file a user deliberately deleted (`inbox.md`, `dreaming-proposals.md`, `scout-mistake-audit.md`, `review-queue.md`, `meetings.md`) is now resurrected on the next upgrade. It's idempotent and arguably self-healing, but it's a behavior change made *in service of* one facet, affecting all of them. There is no per-facet boundary — every facet rides the same shared, all-or-nothing path.

## Problem 3 — Drift has no propagation story

cat-2 files are "never overwritten." That means they can't *conflict* on upgrade — but they also can't *receive fixes*.

Concretely: PR #176's `communication.md.tmpl` has a sentinel-vs-default inconsistency (mixed `<!-- TODO -->`-plus-trailing-default lines) that, combined with the "replace only the sentinel" write instruction in `/scout-profile`, can produce malformed lines (`Reply in: French English (until inferred otherwise)`). If we fix the template, **every existing vault keeps the broken seed forever** — there is no mechanism to push a corrected cat-2 seed to vaults that already have one.

This is distinct from the cat-1 `.proposed-merge` sidecar machinery, which handles drift for *overwritten* files. cat-2 seeds fall outside it. We have no "seed migration" concept (edit-preserving, idempotent, version-aware patching of already-seeded files).

## Problem 4 — No standardized back-port procedure

`engine/scout/scripts/phase_backport.py` (`scoutctl phases backport`, Scout Open Question #10) already reverse-maps **prose edits to existing phase fragments** from a vault's assembled brain files back into `phases/`, gated on a round-trip check. That's real and useful — but it covers only one slice. A *structural* facet like PR #176 — new files, new dirs, schema fields, new commands, migration markers — is entirely hand-assembled. The tooling covers maybe a third of what a facet back-port actually needs.

## Sketch of a direction (not a proposal yet)

Before reaching for Skills as the configurability mechanism, note there is **already a gating primitive** in `phase_assembly.py:select_sections`: phases filter on `requires: <connector>` and `mode: [...]`. A facet toggle could extend it:

- A `features:` block in `scout-config.yaml` (e.g. `features: {relationships: false}`).
- Phases tagged `requires: feature:relationships` simply don't assemble into the brain files when the feature is off — reusing the existing merge/snapshot/3-way machinery, no Skills indirection.

This would address **Problem 1** (reversibility — disabling re-renders the facet out) and **Problem 2** (a per-facet boundary at the assembly layer). It does **not** address **Problem 3** (cat-2 seed teardown + template-fix propagation) or **Problem 4** (structural back-port tooling) — those need their own answers.

> Caveat: `requires:`/`mode:` only gate *phase fragments*. The seeded files, schema fields, and commands sit outside assembly, so a complete facet toggle needs a story for those layers too — which loops back to Problems 1 and 3.

## Open questions to decide

1. **What is the unit of a facet?** Just phase fragments, or the whole (phases + seeds + schema + commands) bundle? The configurability mechanism depends on this answer.
2. **Config-driven gating vs Skills vs something else** for turning facets on/off. What are the trade-offs for reversibility, discoverability, and migration weight?
3. **Do we need a "seed migration" category** — edit-preserving, version-aware patching of already-seeded cat-2 files — to solve drift (Problem 3)? Or do we accept that seeds are frozen-at-install forever?
4. **Teardown semantics.** When a facet is disabled or removed, what happens to orphaned vault files and entity-data writes (`last_interaction`)? Leave them? Quarantine them? Document "no teardown" as intentional?
5. **Back-port procedure.** Should `scoutctl phases backport` grow to cover structural facets, or do we want a separate documented checklist for promoting a personal-vault capability to the engine?
6. **Default-on vs default-off** for newly back-ported facets, and whether existing vaults opt in on upgrade or get them automatically (current behavior is automatic via seed-replay).

## Non-goals

- Re-litigating PR #176's individual merits — it's reviewed separately. This note is about the *pattern* it exemplifies.
- Committing to Skills, a `features:` block, or any specific mechanism. This is a problem statement, not a design.
