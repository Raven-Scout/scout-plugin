# `scoutctl phases backport` — design

**Date:** 2026-06-16
**Status:** Approved (design) — implementing
**Closes:** the manual half of Scout Open Question #10 (back-port applied run-brain edits into `phases/`)

## Problem

`SKILL.md` / `DREAMING.md` / `RESEARCH.md` in a Scout vault are **derived artifacts** assembled from `phases/{core,connectors,modes,research}/*.md` by `_assemble()` (`engine/scout/scripts/bootstrap.py`). When an interactive/dreaming session edits a vault brain file directly (e.g. applying an approved proposal), that edit lives **only** in the vault. On the next `/scout-update`, `_stage_cat4_upgrade()` 3-way-merges the freshly-assembled output against the `.scout-state/last-assembled/` snapshot; because the vault diverged from the snapshot and the plugin re-rendered the same region, the edit **sidecars** (`<name>.md.proposed-merge`) every time, until a human hand-resolves it. The fix is to push the vault's divergence back into the source `phases/` fragments so the next assembly already contains it.

## What assembly does (the thing we reverse)

`_assemble(kind)` returns `"# {kind}\n\n**BASE_DIR:** …\n"` followed by each selected phase **section body**, run through `render_template()` and joined by `\n\n`. Selection: SKILL = `core`+`connectors` (modes briefing/consolidation); DREAMING = `core`+`modes` (dreaming); RESEARCH = `core`+`research` (research); files globbed sorted, sections filtered by `requires` (enabled connector) and `mode`.

`render_template()` is **lossy**: `{{VAR}}` → concrete value (`{{USER_NAME}}`→"Jordan", `{{INSTANCE_NAME}}`→"Scout", …). Reversing the substitution is the central difficulty.

### Template variables, by reversal safety

| Safe to auto-reverse (long / unique) | Risky (short / common — flag, don't auto-rewrite) |
|---|---|
| `USER_EMAIL`, `USER_SLACK_ID`, `GITHUB_USERNAME`, `SCOUT_DIR`, `SCOUTCTL_BIN` | `USER_NAME`, `INSTANCE_NAME`, `INSTANCE_NAME_LOWER`, `TIMEZONE`, `PLATFORM`, `TODAY_DATE`, `MAX_BUDGET`, `AUTO_UPDATE_ENABLED` |

Risky vars are short tokens that appear legitimately in prose ("Scout", a date, a timezone). Auto-replacing every "Scout" with `{{INSTANCE_NAME}}` would corrupt the fragment. Policy: auto-reverse only the safe set; when a risky value appears in an added line, **surface it in the report** so a human decides, but write the literal as-is.

## Goals / non-goals

**Goals**
- Map each vault↔snapshot divergence hunk to its source phase fragment + location.
- Reverse-templatize added lines conservatively (safe vars only).
- Write the confidently-mappable, **round-trip-verified** edits into `phases/` on a branch, marked, and emit a report.

**Non-goals (YAGNI)**
- No auto-opening or auto-merging a PR (human reviews + opens; the engine is the public source of truth).
- No automatic wiring into the dreaming apply-flow (future; this is an operator-invoked command).
- No attempt to back-port **vault-only drift** (content in no phase) or hunks that don't round-trip — those are reported, never guessed.

## Command interface

```
scoutctl phases backport [--kind SKILL|DREAMING|RESEARCH|all] [--apply] [--date YYYY-MM-DD] [--vault PATH]
```

- Default **dry-run**: prints the report (mapped / needs-review / unmapped), writes nothing.
- `--apply`: writes the round-trip-verified edits into `phases/` with a `<!-- backported {date} -->` marker line, then re-prints the report. The operator commits + opens the PR.
- `--kind all` (default) processes all three brain files.
- `--date` overrides the marker date (default: today); `--vault` overrides the vault path (default: resolved like the rest of bootstrap).

## Architecture

A new pure-logic module + a thin CLI command:

- **`engine/scout/scripts/phase_backport.py`** — all logic, no I/O side effects in the core functions (so they're unit-testable):
  - `diff_hunks(snapshot: str, live: str) -> list[Hunk]` — added/changed line groups with their surrounding context (reuse `difflib`).
  - `locate_section(hunk, rendered_sections) -> SectionMatch | None` — match the hunk's unchanged anchor (prefer the nearest preceding heading) against each rendered section body; return the owning `PhaseSection` + char offset, or `None` if zero/ambiguous matches.
  - `retemplatize(text, vars_) -> tuple[str, list[str]]` — reverse the **safe** vars, return `(text_with_placeholders, risky_hits_found)`.
  - `plan_backport(cfg, kind) -> BackportPlan` — orchestrates: assemble-vs-snapshot diff → per-hunk locate + retemplatize → produce candidate phase-file edits.
  - `verify_roundtrip(cfg, plan) -> BackportPlan` — apply candidate edits to in-memory copies of the phase files, re-run `_assemble`, and keep only the hunks whose result reproduces the vault line; downgrade the rest to `needs-review`.
- **`engine/scout/cli.py`** — register a `phases` Typer sub-app (mirrors the existing `manifest`/`schedule`/`bootstrap` sub-apps) with the `backport` command; it wires the BootstrapConfig, calls `plan_backport` → `verify_roundtrip`, writes on `--apply`, and renders the report.

Reuses `phase_assembly.parse_phase_file` / `select_sections` / `render_template` and bootstrap's `_assemble` / `_template_vars` / config resolution (refactor the minimum needed to import them; no behavior change to assembly).

## Data flow

```
vault/KIND.md  ┐
               ├─ diff_hunks ─► [Hunk] ─► locate_section ─► retemplatize ─► candidate edits
last-assembled/KIND.md ┘                        │                                  │
phases/*.md ─ parse+render ─► rendered sections ┘                                  ▼
                                                                          verify_roundtrip (re-assemble == live?)
                                                                                   │
                                                                  ┌────────────────┴───────────────┐
                                                              applied (write on --apply)      needs-review / unmapped (report only)
```

## Report format

```
phases backport — KIND (N hunks)
  ✓ applied      phases/modes/kb-deep-work.md  «Step 2-pre»   (+6 lines, round-trip ✓)
  ⚠ needs-review phases/connectors/slack.md    «Notification» (risky var 'Scout' on +2 lines)
  ✗ unmapped     SKILL.md:412                  (no source section — vault-only drift)
```

## Error handling / edge cases

- **Parser-skipped sections** (bare `---` rules in a body — known limitation): such sections don't appear in `rendered_sections`, so hunks in them resolve to `None` → `unmapped`, reported, never written.
- **Ambiguous anchor** (context matches >1 section): `locate_section` returns `None` → `needs-review`.
- **Round-trip miss**: any candidate whose re-assembly doesn't reproduce the live line is downgraded to `needs-review`. This is the hard safety gate — a written phase edit always reproduces the vault.
- **No snapshot** (`.scout-state/last-assembled/KIND.md` absent): abort with a clear message (nothing to diff against).
- **`--apply` writes only**; never commits, pushes, or opens a PR.

## Testing (TDD)

- `retemplatize`: safe vars reversed; risky values left literal but reported; no false positives in prose.
- `diff_hunks`: contiguous adds, interleaved context, multi-hunk.
- `locate_section`: unique-anchor hit; ambiguous → None; parser-skipped section → None.
- `verify_roundtrip`: a mappable edit round-trips and is kept; a deliberately-unmappable edit is downgraded.
- **End-to-end**: assemble a fixture vault from fixture phases → snapshot → hand-edit the assembled file (an in-section addition) → `plan_backport` + `verify_roundtrip` + apply → re-assemble → equals the hand-edited file. Mirrors the existing `engine/tests/unit/test_phase_assembly.py` style.

## Out of scope / future

Auto-invoking from the dreaming apply-flow; auto-PR; resolving vault-only drift (those hard-rules should be authored into phases deliberately, not synthesized by a reverse mapper).
