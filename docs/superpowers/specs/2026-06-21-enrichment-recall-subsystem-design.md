# Proactive enrichment-recall subsystem (the "🧠 Help me remember" loop) — design

**Date:** 2026-06-21
**Status:** Proposed (design) — for review
**Closes:** the absence of any proactive-enrichment capability in the engine (no script, no phase, no state files). Upstreams a subsystem that exists only in the running instance.

## Problem

A mature instance accumulates a **pull-capture** loop: at the end of a dreaming run, the system appends a small ranked set of questions — "🧠 Help me remember …" — to its wrap DM, asking the user about facts **no connector can see** (in-person events, decisions, relationships, things that live only in the user's head). The answers flow back into the KB, enriching the graph faster than passive scanning can.

None of this exists in the engine:
- **No generator** — the running instance has `scripts/generate-enrichment-questions.py` (~600 lines, pure-stdlib, read-only) that scans the KB for connector-blind gaps and emits a ranked candidate list. The engine has no equivalent.
- **No phase** — no instruction telling a dreaming run to ask, how to rank/rotate, or how to present.
- **No state** — no persistent stoplist (permanent "never ask this" suppression) or Q&A log.

This is the inverse of the *push*-capture notepad idea: instead of waiting for the user to dump notes, the system asks.

## What the running subsystem does (the thing we port)

**Generator** (`generate-enrichment-questions.py`): a read-only scanner over the KB that ranks candidate questions by intent, applies suppression, and prints a short list (or `--json`). It **never writes** to the KB.

| Rank | Source scanned | Why only the user can answer |
|---|---|---|
| 0 | explicit `[needs: …]` inline gap-flags | deliberately placed connector-blind marker — highest intent |
| 1 | `review-queue.md` "Question for the user:" lines | explicitly user-directed |
| 2 | entity/KB "Open Question(s)" sections (personal-scoped only) | the open question names a head-fact, not a research task |
| 3 | `[single-source]` / `[unverified]` claims | the second source is often the user |
| 4 | thin/stub entity files | a connector can create the node; only the user knows the substance |

Interfaces: `--limit N`, `--json`, `--kb DIR`, `--thin-threshold CHARS`, `--exclude SUBSTR…` (one-run **rotation** — the run passes the prior run's surfaced fingerprints so a question never repeats two runs in a row), `--reject-file PATH` (a **persistent stoplist** — substrings suppressed forever).

**State files** (vault-owned):
- `enrichment-stoplist.txt` — two never-ask classes: (a) capabilities the system already instruments (asking reads as "doesn't know its own system"), and (b) questions the user has explicitly dismissed. Appended to when the user rejects a question in-thread.
- `enrichment-qa-log.md` — the Q&A history (what was asked, what was answered, what it enriched).

**Quality rules** the running instance learned the hard way (the valuable generic kernel):
- **User-only-answerable, not connector-answerable.** Drop any question a connector/research session could resolve (customer status, attribution "who introduced X", SDK/API facts). The generator encodes this as conservative regex guards; the bar is "is this a head-fact?"
- **No over-suppression to zero.** Quality-filtering must not collapse to a 0-question steady state; when the static scan comes up empty, hand-mine the day's deltas for a judgment question. A constant trickle is the goal.
- **Self-containment.** A question referencing a specific artifact must carry a link/locator + one-line gist, and keep the artifact title visually distinct from the ask — including on re-surface (never "the question from earlier").

## Goals / non-goals

**Goals**
- Port the generator as a tenant-agnostic cat-1 engine script, de-personalized.
- Seed empty, vault-owned state files that survive upgrades.
- Add a dreaming-phase section that invokes the generator, applies the quality rules, presents the "🧠 Help me remember" block, and runs the rotation + rejection loop.
- All content tenant-agnostic; **this is the public engine.**

**Non-goals**
- No new connector. Enrichment reads the existing KB only.
- No auto-answering. The user answers; a later run captures the answer into the KB.
- Not solving the engine's missing dreaming **notification-composition** phase in general (see Risks) — this attaches to the closest existing home.

## Design

### 1. Port the generator → `templates/scripts/generate-enrichment-questions.py`
Wire it into `_CAT1_FILES_FROM_PLUGIN` (`engine/scout/scripts/bootstrap.py`), exactly as `scripts/recurring-task-status.py` is today (raw `.py`, copied verbatim on install/upgrade, no template rendering). The scanning logic is already generic; **de-personalization is the work**:
- **Comments/docstrings** — strip the dated user-feedback quotes, real names, employer references, and `Pattern #NN` citations (the engine doesn't ship that audit); keep the rank rationale in generic terms.
- **String literals** — the argparse description and the printed header both name the user literally → generic ("… enrichment questions").
- **Regex heuristics** — at least one connector-answerable guard hardcodes the employer name in an alternation (`…|<employer> customer|…`). Generalize to employer-agnostic patterns (or drive off a small configurable term list) **without losing** the connector-answerable-vs-head-fact discrimination — this is the subtlest part and the thing reviewers should scrutinize.

### 2. Seed state files (install-only) → `_INSTALL_ONLY_TEMPLATES`
Add, alongside `review-queue.md`:
- `scripts/enrichment-stoplist.txt` — shipped **empty** (just a comment header explaining the format). The running instance's stoplist is full of instance-specific suppressions; a fresh install starts clean and accumulates its own.
- `knowledge-base/enrichment-qa-log.md` — shipped as an empty header. Install-only means an upgrade never clobbers the user's accumulated suppression/history.

### 3. Add the recall section → `phases/modes/feedback-processing.md`
Enrichment is the *pull-capture* half of the feedback loop, so it belongs in the feedback phase (which already harvests reactions/replies and handles in-thread dismissals — the natural place to also append a rejected question to the stoplist). The new section instructs the run to:
1. Run the generator with `--exclude <prior run's surfaced fingerprints>` (rotation) and the default `--reject-file` (persistent suppression).
2. Apply the **quality rules** above; if the generator returns nothing, hand-mine the day's deltas rather than asking nothing.
3. Compose the **"🧠 Help me remember"** block into the wrap DM, self-contained per the rule.
4. On a user dismissal in-thread, append the question's keyword to `enrichment-stoplist.txt` (permanent suppression); log the round to `enrichment-qa-log.md`.

## Alternatives considered

**A — Prose-only (no script).** Re-express the scan as model instructions; no `.py`. Rejected: the script *is* the mechanical guarantee that rotation/suppression happen deterministically. A model re-deriving the scan each run is slower, non-deterministic, and reintroduces exactly the over-ask / over-suppress failures the script was built to fix.

**B — Port the script but keep state ephemeral** (no persistent stoplist/log). Rejected: loses permanent suppression (the user's "never ask this again") and rotation memory — the two things that keep the feature from becoming annoying.

## Implementation plan
1. `templates/scripts/generate-enrichment-questions.py` — de-personalized copy of the generator; add to `_CAT1_FILES_FROM_PLUGIN`.
2. `templates/enrichment-stoplist.txt.tmpl` + `templates/enrichment-qa-log.md.tmpl` (empty headers) — add to `_INSTALL_ONLY_TEMPLATES` at `scripts/enrichment-stoplist.txt` / `knowledge-base/enrichment-qa-log.md`.
3. `phases/modes/feedback-processing.md` — new recall section (the four steps above) with the quality rules, tenant-agnostic.
4. A unit test for the generator (ranking + stoplist + `--exclude` rotation against a fixture KB), mirroring the existing script tests.

## Testing / verification
- Generator unit test: fixture KB with one item per rank + a stoplist + `--exclude`; assert ranked order, that stoplisted/excluded items are dropped, and that it never writes to the KB.
- `_assemble("DREAMING")` includes the recall section and parses; it must **not** leak into SKILL/RESEARCH.
- Bootstrap install seeds the two state files; bootstrap **upgrade** leaves an existing non-empty stoplist/log untouched (install-only).
- ruff + shellcheck clean; existing tests green.

## Risks / open questions
- **Regex generalization** — removing the employer-specific connector-answerable guard without weakening the head-fact discrimination is the riskiest change; flag the diff explicitly for review.
- **No dreaming notification-composition phase** — the engine composes the dreaming DM implicitly (runner instruction + scattered "surface in the wrap notification" refs). The recall attaches to `feedback-processing.md`, but a future consolidation of dreaming notification into one phase would be a cleaner home — out of scope here, noted.
- **Empty stoplist on fresh installs** means new users get more questions early; acceptable and arguably desirable (the "constant trickle" goal), and it self-tunes as they dismiss.
- **qa-log growth/retention** — unbounded today; a rotation/retention policy is a possible follow-up.

## Out of scope / future
- Briefing-mode layer (separate spec, #149).
- De-personalized Patterns batch; Google Messages opt-in connector.
- Wiring enrichment into briefing/consolidation runs (today it's a dreaming-run behavior).
