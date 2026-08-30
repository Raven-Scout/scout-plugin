---
phase: mode
name: feedback-processing
slot: dreaming-phase-1
mode: [dreaming]
requires: any-of(slack, notify:telegram)
---

## Phase 1: Feedback Processing

This is the self-improvement loop. Harvest feedback from {{USER_NAME}}'s reactions and replies to {{INSTANCE_NAME}}'s messages, classify signals, update the mistake audit, and apply or propose improvements.

***

### Step 1a: Determine which surfaces to harvest

**Read `feedback.surfaces` from `scout-config.yaml` before reading anything.** It lists the surfaces to harvest, and it exists because the surface {{INSTANCE_NAME}} *publishes* the wrap on and the surface it *reads replies from* are configured independently and can drift apart. When they drift, nothing errors: the read still executes, still returns well-formed results, and still reports a clean `0 feedback signals` — true, correctly sourced, and no longer an answer to the question being asked.

**Harvest every surface listed, and read Telegram first when it is one of them.** Telegram drops unconsumed updates after ~24h, so a missed read there *destroys* the signal; Slack history is permanent and can be re-read tomorrow.

**Report counts per surface — `Telegram: N · Slack: M` — never a merged total.** One number cannot distinguish *"both surfaces quiet"* from *"one surface unread"*, and the second is the failure this step exists to catch.

**Cross-check the config against where the wrap actually went.** If the last run's wrap was sent with `scoutctl notify telegram` and `feedback.surfaces` does not list `telegram`, stop and report that as a finding — it is the exact drift described above, live.

***

### Step 1b: Harvest Feedback from Telegram

*Skip this step only if `telegram` is absent from `feedback.surfaces`.*

```bash
scoutctl notify telegram-read --since "<last dreaming session timestamp, ISO or epoch>"
```

The reader **never consumes the queue** — re-reads are idempotent, so running it twice is safe and running it in a session that later fails loses nothing.

**Read its exit code, and never report a signal count off a non-zero one.** Four distinct states return an empty list at the Bot API call site — genuine silence, a registered webhook (`getUpdates` is blocked while one is set), a competing consumer, and an auth failure — and only the first is silence. The command distinguishes them for you:

| Exit | Meaning | What you may report |
|---|---|---|
| `0` | The call executed against an unblocked queue | The count it printed, including `0` |
| `1` | A fault — webhook registered, competing consumer, or API error | **Nothing.** Report the fault itself as a finding. `0 signals` is unsupported. |
| `10` | Missing or insecurely-permissioned secrets | **Nothing.** Report the misconfiguration. |

**Do not apply an authorship test here.** In a private chat `getUpdates` returns only *incoming* messages — the bot's own sends never come back — so every item printed is {{USER_NAME}}. The Slack self-DM footer test would discard real feedback if applied to this surface.

**Replies arrive with their parent attached.** A `↳ replying to:` line means the item is feedback *on that specific wrap*; there is no second call to make and no thread to forget to open.

**A `0 inbound` result is a statement about the last ~24h only,** not about the run window if that window is older. If the gap since the last dreaming session exceeds 24h, say so rather than reporting the window as quiet.

**Reactions are unverified capability.** `message_reaction` is requested, but live delivery to a private-chat bot has not been confirmed against a real reaction. Never report *"0 reactions"* as an observation about {{USER_NAME}}.

***

### Step 1c: Harvest Feedback from Slack

*Skip this step only if `slack` is absent from `feedback.surfaces`.*

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

**A thread read that errors is not a thread with no replies.** If `slack_read_thread` returns an error object, retry it before concluding anything — an un-executed call must never contribute to a `0 signals` count.

***

### Step 1d: Classify Feedback Signals

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

### Step 1e: Cross-Reference with Mistake Audit

Read `knowledge-base/scout-mistake-audit.md`. For every negative or correction signal from Step 1d:

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

### Step 1f: Determine and Apply Improvements

Based on the classified signals and mistake audit updates, determine what changes to make. Use this autonomy table:

| Target File | Autonomy Level | Action |
|---|---|---|
| `knowledge-base/scout-mistake-audit.md` | **Direct edit** | Apply updates from Step 1e immediately |
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

### Step 1g: Handle Proposals

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

For each improvement that targets `SKILL.md` (from Step 1f):
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

### Step 1h: Commit

If Phase 1 made any changes (mistake audit updates, KB fixes, dreaming improvements, applied proposals, new proposals):

```bash
git -C {{SCOUT_DIR}} add -A && git -C {{SCOUT_DIR}} commit -m "dreaming [HH:MM]: feedback processing — <summary of changes>"
```

The summary should mention what was processed: e.g., "3 feedback signals, 1 new mistake pattern, 2 KB fixes" or "applied 1 approved proposal, added 2 new proposals."

If Phase 1 found no actionable feedback (no reactions, no thread replies in the time window), skip the commit and proceed to Phase 2. Log "No feedback signals found in time window" in the session entry.
