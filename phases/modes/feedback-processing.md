---
phase: mode
name: feedback-processing
slot: dreaming-phase-1
mode: [dreaming]
requires: slack
---

## Phase 1: Feedback Processing

This is the self-improvement loop. Harvest feedback from {{USER_NAME}}'s reactions and replies to {{INSTANCE_NAME}}'s messages, classify signals, update the mistake audit, and apply or propose improvements.

***

### Step 1a: Harvest Feedback from Slack

Read the bot's DM conversation with {{USER_NAME}} using `slack_read_channel` with channel_id `{{USER_SLACK_ID}}`.

**Determine the time window:**
1. Check the Recent Sessions table in `knowledge-base.md` for the last dreaming session entry.
2. If a previous dreaming entry exists, look back to that timestamp.
3. If no previous dreaming entry exists (first run), look back 24 hours from now.

**For each message authored by {{INSTANCE_NAME}} (the bot) within the time window:**
1. Call `slack_read_thread` on that message's timestamp to retrieve all thread replies and reactions.
2. Collect and record:
   - **Message content**: the original text {{INSTANCE_NAME}} sent
   - **Reactions**: each emoji name and who added it (user ID + display name if available)
   - **Thread replies**: each reply's full text, author (user ID + display name), and timestamp

Skip messages not authored by {{INSTANCE_NAME}}. Only harvest feedback on the bot's own outputs.

***

### Step 1b: Classify Feedback Signals

Categorize every piece of harvested feedback into one of these signal types:

| Signal Type | Indicators | Action |
|---|---|---|
| **Positive confirmation** | `+1`, thumbsup, checkmark, heart, or praise in thread replies ("great", "perfect", "exactly right") | Log what worked — the content, format, or behavior that earned approval |
| **Negative flag** | `x`, thumbsdown, `-1`, or criticism in thread replies ("wrong", "bad", "don't do this") | Log what failed — the specific output or behavior that was rejected |
| **Correction with context** | A thread reply that explains what was wrong AND provides the correct information or reasoning | Extract a concrete rule or heuristic from the correction. This is the highest-value signal. |
| **Mixed** | Positive reactions on some parts of a message, negative on others; or a reply that says "X was good but Y was wrong" | Separate into individual positive and negative items, handle each independently |

**Rules for classification:**
- A bare reaction with no thread context is a weaker signal than a reply with explanation. Still record it, but weight corrections with context higher.
- If the same message has conflicting signals from different people, note the conflict but weight {{USER_NAME}}'s signal highest.
- If a reaction is ambiguous (e.g., a thinking-face emoji), do not classify it as positive or negative. Skip it.

***

### Step 1c: Cross-Reference with Mistake Audit

Read `knowledge-base/scout-mistake-audit.md`. For every negative or correction signal from Step 1b:

**If the signal matches an existing pattern in the mistake audit:**
- Increment the occurrence count for that pattern.
- Add this instance as a new evidence entry (date, message content, feedback received).
- If the pattern's status was `Fixed` but this is a recurrence, change status back to `Open` and add a `[regression]` flag with the date. This is the most important update — regressions indicate the fix was incomplete.

**If the signal does NOT match any existing pattern:**
- Add a new entry to the mistake audit with:
  - **Error type**: category (e.g., "stale data", "wrong attribution", "hallucinated detail", "formatting issue", "missed context")
  - **What happened**: specific description of the incorrect output
  - **Root cause**: best assessment of why the error occurred (e.g., "relied on cached KB data without re-querying", "assumed person X was still on team Y")
  - **Fix needed**: concrete corrective action (e.g., "always re-query issue status before reporting", "cross-reference people.md entries with live sources")
  - **Occurrences**: 1
  - **Status**: Open

**For positive signals on previously problematic areas:**
- If a positive confirmation relates to a topic or behavior that has an Open or Fixed entry in the mistake audit, update the entry:
  - If status is `Open` and the positive signal shows the fix is working, change to `Fixed` with evidence (date + the positive feedback).
  - If status is already `Fixed`, add the positive signal as corroborating evidence.

***

### Step 1d: Determine and Apply Improvements

Based on the classified signals and mistake audit updates, determine what changes to make. Use this autonomy table:

| Target File | Autonomy Level | Action |
|---|---|---|
| `knowledge-base/scout-mistake-audit.md` | **Direct edit** | Apply updates from Step 1c immediately |
| KB files (content corrections) | **Direct edit** | Fix factual errors identified by feedback (e.g., wrong status, wrong person, outdated info) |
| `DREAMING.md` | **Direct edit** | Improve dreaming behavior based on patterns (e.g., adjust scoring weights, add checklist items) |
| `SKILL.md` | **Direct edit (transparency + reversibility)** | Self-apply additive, feedback-aligned, or pattern-closing edits directly, committed with a clear message so the change is reviewable and `git revert`-able. See gate criteria below. |
| New KB files or structural changes | **Direct edit** | Only if supported by clear evidence from multiple feedback signals |

**SKILL.md self-improvement model (proposal gate retired).** The old "PROPOSAL ONLY, never edit directly" gate is retired. The governing principle is now transparency and reversibility: improvements that are **additive, feedback-aligned, or close a logged mistake pattern** are applied directly and committed with a descriptive message (so {{USER_NAME}} can review and `git revert` any change). A **proposal** in `dreaming-proposals.md` is still required only for changes that are **large, structural, behavior-removing, genuinely uncertain, or that modify {{INSTANCE_NAME}}'s own governance/safety gating**. Those proposals are **opt-out**: a `Pending (auto-apply after <date>)` proposal is applied by a future run unless {{USER_NAME}} marks it `Rejected`; only governance/safety-gating changes require an explicit `Approved`.

**Harness fallback.** If the runtime blocks a direct `SKILL.md` commit (a safety classifier may prevent autonomous self-modification of the brain file), do not silently drop the improvement — file it as an opt-out `Pending` proposal instead, so it is applied by a later run or by {{USER_NAME}} interactively.

**Guardrails:**
- Behavior-removing or structural `SKILL.md` changes still go through a proposal — direct edits are for additive/corrective improvements.
- For KB content fixes, always cite the feedback that triggered the change: "Corrected per {{USER_NAME}} feedback on [date]: [brief description]."
- If a correction contradicts information from a live connector, investigate before changing. The correction may be about interpretation, not raw data.

***

### Step 1e: Handle Proposals

**First: apply approved AND ripe opt-out proposals.**

Read `dreaming-proposals.md`. Apply a proposal's change to `SKILL.md` when EITHER:
- its status is `Approved` (required for governance/safety-gating changes), OR
- its status is `Pending (auto-apply after <date>)` and that date has passed and it is not marked `Rejected`.

For each, apply the change exactly as specified, set status to `Applied — [today's date]`, and commit separately:
```bash
git -C {{SCOUT_DIR}} add -A && git -C {{SCOUT_DIR}} commit -m "dreaming [HH:MM]: applied proposal — <short description>"
```

**Engine back-port reminder (every run).** Applying a `SKILL`/`DREAMING`/`RESEARCH` proposal edits only the vault brain file; the same rule must also land in the engine's `phases/` fragments, or the next `/scout-update` re-render will sidecar it. So at the start of this step, scan for **applied proposals whose engine back-port is not yet merged** and, if any are owed, surface a standing reminder in the wrap notification (and carry it as an action item — it must not silently drop):

> ⚠️ Engine back-port owed: N applied proposal(s) not yet merged into the engine `phases/` — run `scoutctl phases backport` (dry-run first), review the diff, and open a PR. **Never auto-run it** — it writes the shared/distributable engine, so it stays operator-triggered.

Clear the reminder when the back-port PR merges. The apply creates the debt; the reminder keeps it visible until it's paid.

**Then: apply additive improvements directly; file proposals only for gated changes.**

For each improvement that targets `SKILL.md` (from Step 1d):
- **Additive / feedback-aligned / pattern-closing** → apply directly to `SKILL.md` and commit with a descriptive, revertable message. No proposal needed. (If the harness blocks the commit, fall back to an opt-out proposal per the Harness fallback note above.)
- **Large / structural / behavior-removing / uncertain / governance-or-safety-gating** → write a proposal using the format below (opt-out for the first four; governance/safety changes get `Status: Pending` and require an explicit `Approved`):

```markdown
### [Date] — [Short description]
**Trigger:** [specific feedback or pattern that prompted this]
**Proposed change:** [specific edit with before/after text, or exact addition with location]
**Rationale:** [why this change prevents the issue or improves behavior]
**Evidence:** [specific feedback instances — dates, message content, reactions]
**Status:** Pending (auto-apply after [today + 3 days])   # or just "Pending" for governance/safety changes
```

**Quality bar for proposals:** Every proposal must be specific enough that a future dreaming run can apply it mechanically without ambiguity. "Make Scout better at X" is not a proposal. "In SKILL.md section Y, change line Z from 'always do A' to 'do A only when B, otherwise do C'" is a proposal.

***

### Step 1f: Proactive Enrichment Recall — the "🧠 Help me remember" loop

This is the *pull*-capture half of the feedback loop: {{INSTANCE_NAME}} proactively asks {{USER_NAME}} a small, rotating set of questions about facts **no connector can see** — in-person events, decisions, relationships, head-knowledge — and later feeds the answers back into the KB, enriching the graph faster than passive scanning can. It lives in this phase because this phase already harvests in-thread replies (Step 1a) and is the natural place to record a dismissal.

**1. Handle answers & dismissals to the *previous* run's questions** (from the thread replies harvested in Step 1a):
- **Answered** → capture the fact into the appropriate KB file (cite: "per {{USER_NAME}} enrichment reply on [date]"), and log the round to `knowledge-base/enrichment-qa-log.md`.
- **Dismissed** ("I don't care" / "you should already know this" / a ❌ on the question) → append the question's distinguishing keyword (a short substring, one per line) to `scripts/enrichment-stoplist.txt` so it can never resurface. Log the round as dismissed.

**2. Generate this run's candidates.** Run the read-only generator (it never writes to the KB):

```bash
python3 {{SCOUT_DIR}}/scripts/generate-enrichment-questions.py --limit 3 \
  --exclude "<prior run's surfaced fingerprint>" [--exclude "<another>" ...]
```

- The persistent stoplist (`scripts/enrichment-stoplist.txt`) is loaded automatically on every run.
- Pass one `--exclude` per question surfaced in the **previous** run (its topic / source / keyword). This is the **rotation** rule — a question never repeats two runs in a row — made mechanical by the script; do not re-derive it by hand.

**3. Apply the quality rules** (the valuable kernel — the generator enforces most of this, but you are the last gate):
- **User-only-answerable, not connector-answerable.** Drop any question a connector or research session could resolve (customer/contract status, "who introduced X", SDK/API facts). The bar is "is this a head-fact only {{USER_NAME}} holds?"
- **No over-suppression to zero.** A constant trickle is the goal. If the generator returns nothing, do **not** ask nothing — hand-mine the day's deltas for one judgment question rather than staying silent.
- **Self-containment.** A question referencing a specific artifact must carry a link/locator **and** a one-line gist, and keep the artifact title visually distinct from the ask — including on re-surface (never "the question from earlier").

**4. Compose the "🧠 Help me remember" block** into the dreaming wrap DM — the picked questions (typically 1–3), each self-contained per the rule above. This is the only user-facing output of this step; everything else is state.

**Record what was surfaced** to `knowledge-base/enrichment-qa-log.md` so the *next* run can rotate it out (pass it back via `--exclude`) and match answers/dismissals in Step 1a.

***

### Step 1g: Commit

If Phase 1 made any changes (mistake audit updates, KB fixes, dreaming improvements, applied proposals, new proposals, enrichment stoplist/Q&A-log updates):

```bash
git -C {{SCOUT_DIR}} add -A && git -C {{SCOUT_DIR}} commit -m "dreaming [HH:MM]: feedback processing — <summary of changes>"
```

The summary should mention what was processed: e.g., "3 feedback signals, 1 new mistake pattern, 2 KB fixes" or "applied 1 approved proposal, added 2 new proposals, 1 enrichment answer captured."

If Phase 1 found no actionable feedback (no reactions, no thread replies in the time window) **and** surfaced no enrichment questions, skip the commit and proceed to Phase 2. Log "No feedback signals found in time window" in the session entry.
