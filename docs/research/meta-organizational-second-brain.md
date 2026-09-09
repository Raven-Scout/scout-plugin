# Scout vs Meta's "Organizational Second Brain"

**Date:** 2026-09-05
**Source:** Sengar, Nawrocki, Shah & Kommireddi, [*An Organizational Second Brain: Building an AI That Learns From Experts*](https://engineering.fb.com/2026/09/02/ml-applications/organizational-second-brain-ai-learns-from-experts/), Engineering at Meta, 2026-09-02.
**Basis:** the engine at commit `dd4a726` (v0.9.0 + unreleased fixes) and one live Scout deployment about five months old. Deployment-specific names have been generalized; the numbers are that deployment's, reported as-is.

Meta describes a compliance-review agent built from 200+ structured knowledge files, composable "recipes," a two-stage evaluation harness, and an automated pipeline that turns expert corrections into verified pull requests. After three sprints over six weeks they report assessment time down from days to minutes, roughly 80% fewer tokens per turn after restructuring, and **zero regressions** across improvement cycles.

This note maps that architecture onto Scout, component by component, to answer three questions: what should Scout borrow, where is Scout already ahead or doing something Meta's design does not cover, and where is Scout weaker and what is the concrete fix.

**The one-paragraph verdict.** Meta and Scout independently arrived at the same foundation: knowledge in version-controlled text files rather than model weights, many small files with YAML frontmatter forming a dependency graph, procedures separated from facts, humans gating self-modification, and a nightly loop that turns corrections into edits. Scout is *ahead* on everything Meta's article does not discuss: grounding in a live, multi-source world; temporal decay and staleness enforcement; a persistent, numbered self-model of its own failure modes; autonomous scheduling, budgeting, and lane-liveness; and a pull-capture channel where the agent interviews its user. Scout is *weaker* on exactly one layer, and it is the one Meta says makes the other three work: **evaluation**. Scout has no targeted replay, no regression suite, and no independent reviewer of its own self-edits. Today the regression detector is the operator noticing.

---

## 1. What Meta built

| Layer | What it is | Load-bearing claim |
|---|---|---|
| **Knowledge architecture** | 200+ files in a strict taxonomy: *position files* (organizational stances plus machine-actionable routing implications), *taxonomy/vocabulary files* (single-source-of-truth glossaries), *routing indexes* (input characteristics → which positions and procedures apply, deterministically), *gateway files* (threshold tests before entering a domain). Every file declares `depends_on` / `referenced_by` in frontmatter, forming a bidirectional dependency graph. Knowledge is partitioned by **density × usage frequency**: high-density, every-turn material lives in the curated wiki; sparse, situational material is served by retrieval and never loaded into the wiki. | "Separate what the agent knows from how it reasons." The impact of any edit is traceable through the graph. |
| **Reasoning layer** | *Recipes*: imperative, composable, multi-step procedures that reference knowledge files but **contain no domain facts**. A top-level routing recipe selects sub-recipes per analytical phase. Each step loads only the knowledge it needs (*progressive disclosure*). | Restructuring from one flat instruction file to recipe-driven stages cut tokens per turn by about 80%. Failures attribute cleanly to one layer: was the knowledge wrong, or the procedure? |
| **Evaluation framework** | Two stages gate every change. **Targeted replay**: re-run the scenario that triggered the correction; a *blind* judge compares the new output to the expert's feedback without knowing what changed. **Regression testing**: domain benchmark suites (structured Q&A, LLM judges where several answers are defensible) run in parallel; any regression retries compilation with a prompt describing where it regressed. | "Zero regressions," because every landed fix automatically joins the regression suite. |
| **Improvement loop** | **Diagnosis**: extract every substantive signal from the expert's conversation trace *alongside the agent's knowledge manifest* (every file loaded, when, how used), then apply one attribution test: *could the agent have reached the right answer from the materials it loaded?* Yes → recipe problem. No → knowledge gap. Experts disagree → ambiguity, flagged for humans. **Compilation**: sub-agents analyze impact in parallel (cross-references, conflicts, token budget, test coverage, duplication); an **independent adversarial reviewer in fresh context** sees only the diff; a **deterministic linter** (dangling references, size budgets, identifier collisions, dependency cycles) passes or fails, non-probabilistically. **Validation**: replay and regression. **Expert review**: pull requests with full audit trails; experts review proven fixes instead of debugging raw failures. | "Treat maintenance as a compilation problem and automate it." Every improvement is a text edit an expert can review in 30 seconds. |

**Humans in control** runs across all four layers through *checkpoints* (the agent surfaces intermediate reasoning to confirm, correct, or redirect) and *escalations* (genuine ambiguity is handed to the expert, whose choice sets the path). Meta names three simultaneous purposes: quality control, **training signal**, and **trust calibration**. Experts trust a system they can watch reason, and one that flags uncertainty rather than masking it.

---

## 2. Layer-by-layer mapping

Verdict key: **Parity** = same shape, comparable rigor · **Ahead** = Scout has it and Meta doesn't, or Scout's is stronger · **Weaker** = Meta has it and Scout doesn't, or Scout's is thinner · **Different** = deliberate divergence, not a gap.

| # | Meta component | Scout counterpart | Verdict |
|---|---|---|---|
| 1 | **Text files, not weights.** Version-controlled, diffable, reversible. | Identical commitment; see README §Design Philosophy → *Git as Foundation* and *KB as Persistent Memory*. Markdown vault plus git as the audit log; `git revert` is the veto mechanism. The reference deployment has 2,787 commits in 149 days. | **Parity**, by independent convergence. Anthropic's Managed Agents "Dreaming" feature (May 2026) recommended the same "many small focused files" shape. |
| 2 | **Position files**: curated organizational stances with constraints and routing implications. | Project files (`projects/<name>/<name>.md`: status, decisions made, open questions, key people), the Key Decisions Log in `knowledge-base.md`, people and personal entity files. Different in kind: Meta's positions are *interpretations distilled offline from documents*; Scout's are *live-verified state* re-grounded against connectors on a freshness budget. | **Parity** (different substrate, same role). |
| 3 | **Taxonomy / vocabulary files**: single-source-of-truth glossaries. | `knowledge-base/ontology/schema.yaml`: entity types with required and optional properties, typed relationships with declared inverses and symmetry, validated by `parser.py`. Plus controlled vocabularies for verification markers (`[single-source]`, `[unverified]`, `[stale]`, `[contradicted]`, `[speculative]`), priority tiers, and the `cadence` / `surface_window` DSLs for recurring tasks. **But** these vocabularies are spread across the brain files, the schema, and a staleness-rules doc; there is no single glossary file. | **Ahead** on formality · **Weaker** on single-source-of-truth. |
| 4 | **Routing indexes**: deterministic input → applicable positions and procedures. | Routing is by *clock*, not by *input*: hour and weekday select briefing, consolidation, or weekend mode. `slack_channels:` frontmatter on high-priority projects drives a channel poll, and `surface_rule` on task entities gates surfacing. The reference deployment also carries a hand-written, date-scoped coverage rule for one top-priority project inside the dreaming brain file, whose own text says it should be a general mechanism. | **Weaker**: Scout has no "given this input, load these files" layer. |
| 5 | **Gateway files**: threshold tests before entering an analytical domain. | The gates exist: the Causal-Claim Gate, No Unverified Negatives, the Leave-State compose gate, transcript-derived names must pass an ontology match, source-claim integrity. They live as prose paragraphs inline in phase files, not as separable, machine-checkable predicates. | **Parity** in concept · **Weaker** in form. |
| 6 | **Bidirectional dependency graph** so edit impact is traceable. | For the **knowledge base**: wikilinks plus typed relationships (1,312 across 196 entities in the reference deployment), inverse-aware, validated by the parser, measured by graph-health scripts. For the **brain files and scripts**: nothing. A phase fragment that invokes `scripts/foo.py` or reads a KB file declares no dependency, so renames and dead paths fail silently. The reference deployment ran for two months with two mandated health checks dead at import, and its own project instructions pointed at a script path with no file behind it. | **Ahead** (KB) · **Weaker** (brain → script/KB). |
| 7 | **Density × frequency partition**: wiki for every-turn material, retrieval for sparse material; never load sparse material into the wiki. | Partial. The staleness rules exclude archive and audit folders from freshness ranking, and the `kb-pre-filter` hook (`templates/hooks/kb-pre-filter.sh.tmpl`, `scoutctl hook kb-pre-filter`) pre-computes staleness so a run does not re-read everything. But there is **no retrieval layer**; the semantic-index item has been on the wishlist since April. In the reference deployment, 427 KB files, 119 research write-ups, and document transcriptions sit in one flat "read the index, follow wikilinks" hierarchy. | **Weaker**. |
| 8 | **Recipes**: imperative, composable, fact-free procedures; a routing recipe delegates to sub-recipes. | `SKILL.md`, `DREAMING.md`, and `RESEARCH.md` are the recipes. In the engine they *are* composable: `phases/{core,modes,connectors,research}` fragments are selected per enabled connector, slot, and mode and assembled by `engine/scout/scripts/phase_assembly.py`; `engine/tests/unit/test_phase_assembly.py` checks every shipped fragment parses. Two departures from Meta: composition happens at **install and upgrade time**, not at runtime, so each run still loads the whole assembled brain; and the recipes are **not fact-free**. The operator's identifiers, repo and channel lists, canonical incident stories, and dozens of mistake-pattern narratives are embedded in the procedure files. | **Parity** on composition · **Weaker** on fact-free discipline and runtime loading. |
| 9 | **Progressive disclosure**: each step carries only its own instructions and knowledge; ~80% token reduction. | Pre-session caches (`.scout-cache/`, written by hooks; "pre-session caching beats mid-session tool calls") are the same instinct applied to *data*. For *instructions*, the brain files are read whole every run: 1,512 + 745 + 228 lines in the reference deployment. An April 2026 proposal to split `SKILL.md` into a router plus per-mode files (estimated ~40% savings) is still in progress. | **Weaker**. |
| 10 | **Checkpoints**: the agent pauses mid-analysis for confirm, correct, redirect. | Deliberately absent mid-run: Scout is async-first by design. The checkpoint equivalents sit *between* runs: the proposal gate (opt-out auto-apply; governance changes still need explicit approval), inline `//==<< >>==//` comments in the work surface, app-side approve/decline, Slack reactions. Governance is layered further than Meta describes: proposal file → harness safety classifier → post-hoc revert → engine/vault three-way merge on upgrade (`three_way_merge.py`). | **Different** (async by design) · **Ahead** on governance layering. |
| 11 | **Escalations**: ambiguity handed to the expert; their choice sets the path. | The review queue ("uncertain? review queue, not KB"; never merge, split, or reassign people on one source), plus something Meta's design does not have: **pull-capture**. The enrichment block interviews the user for facts no connector can see and logs every ask and answer in a visible ledger (README: *It asks you, sparingly, for what only you know*). Meta's experts only *correct*; Scout's user is also *asked*. | **Ahead**. |
| 12 | **Diagnosis**: extraction plus one attribution test (knowledge gap / recipe problem / ambiguity), using the run's knowledge manifest. | The mistake audit: 196 numbered patterns in the reference deployment, each with error type, what happened, root cause, fix needed, and a mandatory pattern → proposal bridge. Richer than Meta's in *taxonomy*: prose-rule-without-driver, index-vs-file drift, gitignored-output-invisible, dark-lane, sampled-measurement laundering. Thinner in *repeatability*: no standard attribution question, and no **knowledge manifest**. The connector-call log records which tools a run called, not which KB files it read, so "could the run have known?" is answered by narrative rather than evidence. | **Ahead** on taxonomy · **Weaker** on repeatable attribution. |
| 13 | **Compilation**: sub-agents analyze impact (cross-references, conflicts, token budget, coverage, duplication) before an edit. | Dreaming applies direct edits to KB, `DREAMING.md`, and scripts, and files proposals for `SKILL.md`. There is no impact-analysis step. The consequence is visible in the brain files: near-duplicate batch-triage rules in two files, and a deprioritization clause that fights a coverage rule, a contradiction the dreaming file documents in its own text rather than resolving. | **Weaker**. |
| 14 | **Independent adversarial review**: a fresh-context agent sees only the diff and hunts contradictions and broken edge cases. | None. The dreaming run that drafts a rule is the only model that reads it before commit. The harness safety classifier is a *safety* gate (it blocks self-modification without an explicit directive), not a *quality* reviewer. The operator is the sole reviewer of proposals (48 filed, 41 applied in the reference deployment). | **Weaker**. |
| 15 | **Deterministic linter**: dangling references, size budgets, identifier collisions, dependency cycles; pass/fail, non-probabilistic. | Scout has *more* deterministic checks than Meta lists: `parser.py validate`, git-date-based vault freshness with claimed-date divergence, proposal-status reconciliation, action-item continuity, the count-guard and zombie check, session-lane liveness, schema parity, and `test_phase_assembly`. Scout learned the lesson behind them the hard way and named it: *a prose rule with no driver doesn't fire*. **But they are advisory, run by the LLM inside the session on the honor system**, and several write only to a gitignored cache. In the reference deployment, two of the four checks the dreaming brain mandates were dead at import for two months and nothing noticed. Meta's linter is a *gate*: nothing lands if it fails. Several of these scripts also live only in the vault and are not yet back-ported to `templates/scripts/`. | **Ahead** in breadth · **Weaker** in enforcement. |
| 16 | **Targeted replay**: re-run the original failing scenario; a blind judge compares to the expert's feedback. | None. When a pattern's fix ships, the evidence that it worked is "positive feedback later" or "hasn't recurred." | **Weaker**: the largest single gap. |
| 17 | **Regression suite that grows with every fix.** | None. The mistake audit has a status `Fixed → Open — regression`, which by construction means a regression is detected **in production, in front of the user**. The reference deployment's audit records at least four named regressions and mentions the word 58 times. Meta reports zero regressions because every fix becomes a test. | **Weaker**: the largest single gap. |
| 18 | **Landing**: PRs with full audit trails; experts review proven fixes. | Typed commit prefixes (`dreaming [HH:MM]: SKILL.md self-edit — …`), one commit per self-edit, a proposals archive with commit hashes and back-port state, wrap-message disclosure of every self-edit, in-thread feedback receipts. Finer-grained provenance than a PR. The open sore is the two-brain split: applied proposals must be hand-back-ported to `phases/` (`scoutctl phases backport`, operator-triggered by design). | **Parity / Ahead** on provenance. |
| 19 | **Operations** (not discussed by Meta). | TZ-aware scheduler (`schedule_tick.py`), per-run and rolling-window budget gates (`budget_check.py`), per-tool-call connector logging and health roll-ups (`connector_health_report.py`), session cost tracking, and a session-lane liveness check (vault-side, shipped 2026-09-04, not yet wired into a tick). | **Ahead**: Meta is silent. |
| 20 | **Grounding in a changing world** (not discussed by Meta; its corpus is documents). | Source equality across eight connectors; corroboration before assertion; *the user's own actions beat meeting notes*; freshness budgets per file class enforced from git committer-date, not the claimed date; REM pruning of at least five stale things per dreaming run; sunset clauses on rules. Meta says positions "require frequent updating" and says nothing about how staleness is detected. | **Ahead / unique**. |

---

## 3. Where Scout arrived at the same shape

Meta's article is the second external system in 2026, after Anthropic's Dreaming, to converge on Scout's substrate: **small, human-readable, version-controlled text files with structured frontmatter, curated offline, edited by a nightly loop, gated by humans.** Three specifics are worth noting because they are load-bearing design claims in Scout that now have an independent large-organization witness:

1. **"Keep complexity in text files rather than fine-tuned model weights."** Meta's stated reason is Scout's reason: an expert can review a text edit in seconds and revert it; weights make the human a tourist in the agent's mind.
2. **Knowledge / procedure separation**, which is the `SKILL.md`-vs-`knowledge-base/` split and the engine's `phases/` fragments. Meta's version is stricter (recipes contain *no* facts), which is where Scout should tighten (§5.4).
3. **Corrections become edits, and every edit is logged**, which is the mistake-audit → proposal bridge. Meta adds the one step Scout is missing: the edit is *tested* before it lands (§5.1).

---

## 4. Where Scout is ahead or unique

These are the things Meta's four layers do not cover at all. They are also the parts of Scout that would be hardest for a document-corpus system to bolt on.

- **The world moves; the corpus doesn't.** Meta's agent reasons over curated positions and static references. Scout's "knowledge" is *state about a live environment*: an issue status that flipped an hour ago, a PR that was reviewed while the run was composing, a config value that changed ninety minutes before the run read it. Source equality, corroboration, verification markers, and "the user's actions beat meeting notes" exist because Scout's truth has a half-life. This is a different and harder problem, and the article does not address it.
- **Temporal machinery is a design contribution.** Per-class freshness budgets, git committer-date as the authoritative signal, claimed-date divergence detection, very-stale escalation, decay by default (REM cleanup), rules carrying `review_at` / `expires` dates. Meta's only staleness statement is that positions need "frequent updating."
- **The self-model is persistent and numbered.** Meta's diagnosis produces transient issues that become edits. Scout's produces a *taxonomy of its own failure modes* that compounds: patterns cite patterns, and later patterns are found because of earlier ones. The audit is explicitly the trust mechanism (README: *It keeps a model of its own mistakes*). Meta's trust calibration is watching the agent reason; Scout's is reading the agent's own record of being wrong.
- **Pull-capture.** Scout interviews its user for facts no connector can see and keeps a ledger of the answers. Meta's experts are a correction source only.
- **Operational self-awareness.** Budget gating, connector health, lane liveness, cost per session. A flywheel that isn't running produces zero regressions too, which is exactly what the reference deployment's dreaming lane did for 64 days while 914 commits landed around it. Scout has now built the detector Meta would also need and does not mention.
- **Governance of self-modification is layered.** Proposal gate with opt-out timers, a harness-level classifier that blocks governance edits without an explicit directive, post-hoc revert, and a public-engine / private-vault split with a three-way merge on upgrade. Meta's PRs govern *content*; Scout has had to decide who may change the agent's *rules*.
- **Breadth.** One vault spans personal records and production infrastructure. Meta's system is one compliance domain. Breadth is why Scout cannot copy Meta's benchmark approach wholesale (§6), and it is also why pull-capture and the temporal machinery had to be invented.

---

## 5. Where Scout is weaker, and the fix for each

Ranked by how much of Meta's result depends on it.

### 5.1 No evaluation layer (rows 16–17): the headline gap

**Problem.** A fix is declared "Fixed" when the user stops complaining. Regressions are detected in production. Nothing re-runs the scenario that produced a mistake, and nothing prevents a later edit from silently undoing an earlier fix. Meta's "zero regressions" is not a quality of their model; it is a property of a suite that grows by one case per fix.

**Why it is tractable for Scout.** Scout's outputs are not Q&A with right answers, so a domain benchmark in Meta's sense is impossible. But most mistake-audit patterns are about **processing logic**, not world knowledge: a UTC timestamp written as local time; a weekday hand-typed instead of derived; an open item silently dropped at carry-forward; a checked row left in an active section; GitHub's `reviewDecision` read as "nobody has reviewed" when five substantive comment reviews existed; a number explicitly labelled as a first-ten-minutes sample propagated as a rate (and wrong by 3.7×); "not created" asserted without listing repositories; a proposal index that disagreed with the proposal files underneath it. Each has a frozen input and an expected behavior. Those are fixtures.

**Fix, in three tiers, in order.**

- **Tier 0 (days): make the existing checks a gate.** Bundle `parser.py validate`, the continuity check, the count-guard and zombie check, proposal-status reconciliation, schema parity, and a phase-fragment parse into one `vault-lint`; run it as a git pre-commit hook in the vault and as `scoutctl lint`. **A failing lint blocks the run's commit.** This is Meta's deterministic linter, assembled from parts Scout already owns, and it turns roughly eight advisory scripts into the one thing they are not today: enforced. It also ends the dead-check failure class, because a check that fails at import fails the commit loudly instead of writing nothing to a gitignored cache.
- **Tier 1 (weeks): pattern → fixture.** A new dreaming rule: when a pattern is logged, the same run writes `scout-mistakes/fixtures/pattern-NNN/` with the frozen input (the connector payload excerpt, the prior daily file, the message) and an `expected.md` stating the rule as an assertion. `scoutctl eval replay --pattern N` runs the relevant phase fragment against the fixture and hands the output plus `expected.md` to a **blind judge** in fresh context that does not know what changed. A pattern may move to `Fixed` only on a passing replay, not on a quiet week.
- **Tier 2 (weeks): nightly regression.** Dreaming's first phase opens by replaying every fixture. A failing fixture flips its pattern to `Open — regression` **before the user sees it in production**, and the wrap message leads with it. Start with the roughly thirty processing-logic patterns; the suite grows by one per new pattern, exactly as Meta's does.

### 5.2 Diagnosis is narrative, not a test (row 12)

**Problem.** Each audit entry does root-cause analysis well, but freehand. Two runs can attribute the same failure to different layers, and there is no record of what the failing run had actually loaded.

**Fix.** (a) A required `attribution:` field on every new pattern (`knowledge-gap | procedure | ambiguity | harness | operations`), answered by Meta's one question: *could the run have reached the right conclusion from what it loaded?* (b) A **knowledge manifest** per scheduled run: the set of KB files read during the session, derived from the run's own Claude Code session transcript. Scout already parses these transcripts for the user's own sessions (`cc_session_cache.py`), so this is a filter, not a new subsystem. Store it beside the connector-calls log. Attribution then becomes checkable: if the file with the right answer is in the manifest, it was a procedure failure.

### 5.3 No independent reviewer of self-edits (rows 13–14)

**Problem.** The model that drafts a rule is the only model that reads it before commit. The brain files show the cost: duplicated rules, a documented self-contradiction, and 1,500 lines nobody has audited for coherence.

**Fix.** Before any brain-file edit or applied proposal commits, spawn one **fresh-context subagent** that receives only the diff and the lint output, with a single job: find contradictions with existing rules, duplicates, sunset conflicts, and facts embedded in procedure. Its verdict goes into the commit message and the wrap message. Brain edits are rare (dozens per quarter), so this costs almost nothing against a per-run budget cap.

### 5.4 Recipes are not fact-free, and are loaded whole (rows 8–9)

**Problem.** `SKILL.md` is part rulebook, part incident log, part config. Every run pays to read the story behind each rule; a consolidation loads the morning-briefing procedure; the operator's identifiers live in three brain files. Meta's 80% token reduction came from fixing exactly this.

**Fix.** Three moves. (a) **Strip narratives**: each rule keeps its imperative text and a link to its mistake-audit pattern; the story lives in the audit, which already holds it. (b) **Move facts to data**: identifiers, repo and channel lists, and date-scoped coverage rules go to `scout-config.yaml`, KB frontmatter, or a `surface_rule` on the project entity (the general mechanism the dreaming file's own sunset note asks for). (c) **Finish the runtime split** the April proposal described: a small router plus per-mode files, so a consolidation loads consolidation phases only. The engine already has the fragments in `phases/`; the missing piece is loading them per slot at runtime instead of concatenating at install. Scout's own April estimate was ~40%; Meta's 80% is the ceiling.

### 5.5 No dependency declarations for brain → scripts and KB (row 6)

**Problem.** Phase fragments invoke scripts and read KB files by path with no declaration, so a rename or a dead path is invisible until a run silently does nothing.

**Fix.** Add `depends_on:` to phase-fragment frontmatter listing every script and KB path the fragment invokes; the Tier 0 lint checks each exists and is executable. This is the cheapest item on the list, and it extends the dependency graph Scout already validates for the KB to the brain that reads it.

### 5.6 No retrieval tier (row 7)

**Problem.** Dense and sparse material share one hierarchy; runs either read a file or never see it. In the reference deployment, 62% of KB files were past twice their freshness budget on 2026-09-04, and 119 research write-ups had no index until that day.

**Fix.** A `tier: wiki | reference | archive` frontmatter field. Runs read `wiki` by default; `reference` is retrieved on demand via the semantic index already on the wishlist; `archive` is excluded from everything but search. The freshness ranking then only nags about `wiki`, which is where a very-stale percentage becomes meaningful instead of alarming.

### 5.7 The flywheel must actually turn

Not a Meta borrow but the precondition for everything above. Meta's loop is triggered by each expert correction; Scout's is a scheduled lane that sits last in line for budget. In the reference deployment it was dark for 64 days while 914 commits landed around it, because every liveness surface watched a *connector* or a *single run* and nothing asked whether a *lane* was still producing output. The detector now exists (a session-lane liveness script, judged by committed output rather than last-fire time) but is not wired into a tick, and dreaming has no reserved budget slice. Wire the watchdog and give dreaming an earlier slot or a reserved slice; otherwise Tiers 0–2 are rules with no driver, the exact failure Scout's own audit names.

---

## 6. What Scout should *not* borrow

- **Synchronous mid-run checkpoints.** Meta's expert sits in the conversation. Scout's user is asleep when dreaming runs, and that is the point. Keep escalation async; borrow only the *taxonomy* (checkpoint vs escalation) and the attribution test.
- **Fixed Q&A benchmarks.** There is no gold answer to "what should the user do today." Use invariant checks and replayed fixtures (§5.1), not domain benchmarks.
- **Full multi-agent compilation per edit.** Meta runs parallel sub-agents on every fix. Under a per-run budget cap that is nightly bankruptcy. Reserve the fresh-context reviewer for brain-file edits and applied proposals; KB content edits get the lint, not a committee.
- **Curated positions as a substitute for live grounding.** Meta distills documents offline into stances because its documents do not change hourly. Scout's project files must stay re-verified state, not interpretations; the review queue and verification markers are the right primitive for a moving world.

---

## 7. Scorecard

| Dimension | Meta (per article) | Scout (reference deployment, 2026-09-05) |
|---|---|---|
| Age of system | 3 sprints / 6 weeks | 149 days, 2,787 commits |
| Knowledge files | 200+ | 427 KB markdown files; 196 ontology entities, 1,312 typed relationships, 20 entity types |
| Procedure surface | recipes, per-step loading | `SKILL.md` 1,512 + `DREAMING.md` 745 + `RESEARCH.md` 228 lines, loaded whole |
| Token reduction from structure | ~80% per turn | not measured; April proposal estimated ~40%, unapplied |
| Failure-mode record | transient issues → edits | 196 numbered patterns with root causes and recurrence tracking |
| Self-edit review | adversarial fresh-context agent + linter + expert PR review | operator + harness safety classifier; no linter gate, no reviewer |
| Deterministic checks | 1 linter (gate) | ~8 scripts (advisory; 2 were dead for 2 months) |
| Regression detection | replay + suite; **zero regressions** | production, via the operator; at least 4 named regressions in the audit |
| Structural health | not reported | 21 live ontology validation errors; 13.5% broken-link rate; 62% of files very-stale after a 64-day dark lane |
| Human channels | in-conversation checkpoints and escalations | Slack reactions and threads, inline `//==<<` comments, app approvals, enrichment Q&A ledger, git commits |
| Grounding | curated documents + retrieval | 8 live connectors, source equality, freshness budgets |
| Operations | not discussed | scheduler, budget gating, connector health, lane liveness |

The honest read of the two right-hand columns: Scout has five months of operational history and an unusually candid record of its own failures; Meta has the evaluation discipline that would have caught most of those failures before a human did.

---

## 8. Proposed next steps for the engine

None of these are implemented by this document. Each changes what scheduled runs do and needs an explicit decision.

1. **A lint gate** (§5.1 Tier 0): `scoutctl lint` plus a vault pre-commit hook that can block a run's commit. Governance change; explicit approval, not an opt-out timer.
2. **The pattern → fixture convention** (§5.1 Tier 1) as a dreaming-phase rule in `phases/modes/feedback-processing.md`, with "Fixed requires a passing replay."
3. **Finish the brain split** (§5.4): router plus per-mode loading at runtime, building on `phase_assembly.py`.
4. **Wire session-lane liveness into the tick and reserve dreaming budget** (§5.7).
5. **Back-port the vault-only checks** named in row 15 into `templates/scripts/` so a lint gate has something to gate.

## Related reading

- Anthropic, *Dreams* and *Memory Stores* for Managed Agents (platform docs, May 2026). Covers Meta's *diagnosis + landing* for a memory store and none of its *evaluation*. Read together, the two articles bracket Scout: Anthropic validated the substrate; Meta shows the missing test harness.
- README §Design Philosophy (*Source Equality*, *Verification Levels*, *KB as Persistent Memory*, *Git as Foundation*) for the commitments this comparison tests.
- `docs/specs/event-triggers.md` for the "respond to the world, not just the clock" direction that §4's first bullet depends on.
