---
phase: core
name: action-items
slot: action-items
mode: [briefing, consolidation]
requires: null
---

## Step 0: Archive Old Action Items

Move any `action-items/action-items-*.md` files older than 7 days into `action-items/archive/`. Create the archive folder if it doesn't exist. Use the date in the filename to determine age.

```bash
mkdir -p {{SCOUT_DIR}}/action-items/archive
# Move files older than 7 days based on filename date
```

## Step 0.5: Critical Blocks Check

Before building the list, read `knowledge-base/scout-mistake-audit.md`. For every pattern with status **Open** that carries a {{USER_NAME}}-facing blocker — a setup task {{USER_NAME}} must do, an unanswered question {{INSTANCE_NAME}} asked him, or an approved-but-unapplied proposal:

1. Check whether it's been resolved since last run (search messaging, verify files/state).
2. If still unresolved and >24h old, surface it as a **🔴** item today: "**Still blocked:** [pattern] — [what {{USER_NAME}} needs to do]".
3. If stuck 48h+, call it out explicitly in the run summary/notification so it isn't lost.

This makes the mistake audit an active tracker, not a passive log — {{USER_NAME}} expects every run type (not just dreaming) to keep pushing critical blocks toward resolution. Track specifically: unresolved setup/integration tasks, open questions awaiting {{USER_NAME}}'s answer, and approved proposals not yet applied.

## Action Item Categories

Categorize every action item using these levels:

- **🔴 Urgent**: Needs attention today
- **🟡 To Do**: Should be done soon
- **🟢 Watching**: Tracking but no action needed yet
- **✅ Done**: Completed with evidence of completion

### Hard Rule — Deadline-Distance Ceiling

Urgency is **capped by how far away the deadline is** — distance sets a *ceiling*, not a floor. Before assigning any tier, compute the days between today and the item's real deadline:

- **30+ days out** → 🟢 Watching (max). Far-future items never lead the list.
- **7–30 days out** → 🟡 To Do (max).
- **< 7 days out** → eligible for 🔴 Urgent.
- **< 3 days out** → 🔴 when the next action is {{USER_NAME}}'s to take.

**Evaluate the actual deadline, not the source's tone.** Notification language — "action required", "expiring soon", "final notice", "limit reached" — is alarm-word framing, not real urgency, and never promotes an item past the distance ceiling. Deprecation / end-of-life notices specifically stay 🟢 Watching until ~2–3 weeks before the cutover. This ceiling is the counterpart to the deadline *escalation* rule under Knowledge Graph Personal Tasks below: escalation raises priority as a deadline nears; the ceiling stops a distant deadline from being surfaced as urgent on tone alone.

## Action Items File Format

Create `action-items/action-items-YYYY-MM-DD.md` using today's date. Include:

```markdown
# Action Items — YYYY-MM-DD

## 🔴 Urgent

- [ ] [#XXXX] **[Item title]** — [Description with specific details, not vague summaries]
  - Source: [Which connector(s) confirmed this]
  - Context: [[wikilink-to-relevant-kb-file]]

## 🟡 To Do

- [ ] [#XXXX] **[Item title]** — [Description]
  - Source: [connector evidence]
  - Context: [[wikilink]]

## 🟢 Watching

- [ ] [#XXXX] **[Item title]** — [What you're tracking and why]
  - Source: [connector evidence]
  - Context: [[wikilink]]

## ✅ Done

- [x] [#XXXX] **[Item title]** — [What was completed and how]
  - Evidence: [Link to message, PR, calendar change, or other proof]
  - Completed: [date/time]

## Carryover

Items carried forward from previous days that are still open.

- [ ] [#XXXX] **[Item title]** — [Status update since last check]
  - Originally from: action-items-YYYY-MM-DD
  - Current status: [what's changed]

## 🪵 Run notes & connector availability

_Always the LAST section of the file — run metadata is a footer for review, never a hero block at the top. First-paint should be 🔴 Urgent, not run narrative. Append newest entry first; keep only the last 3 runs (older entries roll off)._

- **YYYY-MM-DD HH:MM [timezone]** ([mode]) — X new / Y completed / Z carried forward. Connectors: [available, or "degraded: <name>"].
```

All action items files must include `[[wikilinks]]` to any KB files referenced by action items.

### Hard Rule — Daily-File Completeness Invariant

`action-items/action-items-YYYY-MM-DD.md` must contain the **full carried-forward item list from the moment it exists**. Companion surfaces (scout-app, the TUI) render the daily file as the complete truth — a stub makes every open item invisible until the next full rewrite.

- If today's file does not exist when your session starts, run the deterministic backstop FIRST, then edit on top of the complete file:
  ```bash
  scoutctl action-items materialize
  ```
  It copies the most recent prior daily file (up to 7 days back) verbatim under a fresh date header and a provisional banner. Idempotent — a no-op when today's file exists. The runner preambles already call it before every session; this in-session call covers sessions launched outside the runners.
- **NEVER write a section that points at a previous day's file in lieu of the items.** "Carry forward in full from yesterday — see that file" is FORBIDDEN, no matter how lightweight your session is. This binds every session type — briefing, consolidation, research, dreaming, and any auxiliary session that happens to be the day's first writer.
- When you find the mechanical carry-forward banner at the top of today's file, you are the enriching pass: rewrite the header/focus sections for today, reconcile items normally, and remove the banner. Do not treat the banner as a reason to start a fresh file.
- This rule complements the continuity rules below: the count-guard and dropoff audit protect the ledger *across* days; this invariant protects the rendered surface *within* the day.

### Hard Rule — Every Task Line Has a Stable `[#TAG]`

**Every new task line you write MUST start with a stable `[#TAG]` identifier** — 2–8 uppercase letters/digits with at least one letter (e.g. `[#NAHSEND]`, `[#AI3026]`, `[#RSM]`). The tag is the structural identifier scout-app uses to mark tasks done, snooze them, and attach comments — without it, the app falls back to brittle markdown-substring matching that fails on emoji, italics, em-dashes, embedded links, or any non-ASCII drift. Issue #10 of scout-app catalogs the failure modes.

**Prefer a short, meaningful mnemonic** that hints at the task and is easy to cross-reference from other lines (e.g. `[#NAHSEND]`, `[#MIRO]`, `[#AI3026]`). When nothing meaningful fits, mint a random one:

```bash
PFX=$(scoutctl action-items new-prefix)   # random 4-char fallback id
echo "- [ ] [#${PFX}] **${SUBJECT}** ${BODY}" >> "$DAILY_FILE"
```

**Canonical task line shape:**
```
- [ ] [#TAG] **<bold subject>** <optional body, links, italic context>
```
The tag goes **after** the checkbox marker and **before** the bold subject. Exactly that order — the parser keys off the leading position.

**Tag rules:**
- 2–8 chars, `[A-Z0-9]`, at least one letter. (Pure-numeric like `[#555]` is reserved for GitHub issue refs and is NOT a valid tag.)
- **Unique within the file** — never give two open tasks the same tag (scout-app's `--by-id` will refuse an ambiguous tag).
- **Carry-forward keeps the original tag verbatim.** When propagating an item from yesterday into today, copy its `[#TAG]` exactly — do NOT mint a new one. The tag is the task's identity across days.

**Existing unprefixed lines (legacy carryover):** when you find a task that lacks a `[#TAG]`, give it one on first touch, or run the idempotent one-shot backfill (it leaves already-tagged lines alone):
```bash
scoutctl action-items backfill-prefixes "$DAILY_FILE"
```

**Self-check before commit:** every `- [ ]`/`- [x]` line MUST carry a `[#TAG]`. A heuristic grep catches drift:
```bash
grep -nE '^\s*- \[[ x]\] ' "$DAILY_FILE" | grep -vE ' \[#[A-Z0-9]{2,8}\] ' && \
    echo "ERROR: lines missing [#TAG] prefix above — fix before commit" >&2
```
If that grep finds anything, the file is non-compliant and scout-app's writes will fall back to fragile subject-matching for those lines.

### Hard Rule — Trim by Demotion, Never by Omission

When the list grows long, achieve focus by **reprioritizing**, never by hiding items from view. "Don't overwhelm me" and "don't drop my items" are both real constraints — resolve the tension by demotion *within* view, not omission *from* view.

1. Keep a tight, time-bound **🔴 Urgent** set at the top.
2. Demote lower-priority open items down the tiers (🔴→🟡→🟢) and reorder — {{USER_NAME}} can scan a long, ordered list and ignore the bottom; he cannot recover items that aren't rendered at all.
3. Mark an item `[unverified]` or drop it **only** when {{USER_NAME}} has explicitly said so (reply, reaction, or an inline `//==<<` directive).

**Every open carried item MUST be rendered as its own `- [ ]` checkbox row in exactly one section.** Summary lines like "…plus the standing backlog (#A, #B, … etc.) — carried unchanged" are **FORBIDDEN** as a substitute for rendering individual items. An `etc.` that hides open items reads as a drop from {{USER_NAME}}'s perspective even when the IDs technically persist inside the prose string.

**Compose-time count-guard (run before commit):** count the rendered open rows (`- [ ]` lines plus 🟢 Watching bullets) in today's file. That count MUST be ≥ the prior day's open-item count minus any items closed this run (`- [x]`) or dropped on an explicit {{USER_NAME}} directive. If today's count is lower, items were collapsed/omitted — expand them back into individual rows before committing.

```bash
# Compose-time count-guard
PREV=$(ls -t {{SCOUT_DIR}}/action-items/action-items-*.md | sed -n 2p)
prev_open=$(grep -cE '^\s*- \[ \] ' "$PREV" 2>/dev/null || echo 0)
today_open=$(grep -cE '^\s*- \[ \] ' "$DAILY_FILE")
closed_today=$(grep -cE '^\s*- \[x\] ' "$DAILY_FILE")
[ "$today_open" -lt $((prev_open - closed_today)) ] && \
    echo "ERROR: open-row count dropped ($prev_open→$today_open, only $closed_today closed) — items were collapsed; expand them before commit" >&2
```

### Hard Rule — Continuity-Dropoff Audit (ID-level, pre-commit)

The count-guard catches *how many* dropped; this catches *which*. Before committing, diff today's open `[#XXXX]` ids against the prior day's. For every id open in N-1 but absent in N:

1. It has a ✅ Recently Completed entry in today's file → confirmed closure, OK.
2. It has an explicit `//==<<` drop directive since N-1 → confirmed drop, OK.
3. **Otherwise → silent dropoff.** Re-add it to today's file with a `[carried-via-audit]` annotation, surface `🚨 N items silently dropped from yesterday — please verify` in the notification, and write a `review-queue.md` entry. Never let an open item vanish without one of (1)/(2).

### Hard Rule — Leave-State Compose Gate

Before generating any 🔴/🟡 action item that names a specific person as the next-action owner, read that person's entity file (`knowledge-base/people/<slug>.md`). If it carries an active leave/out-of-office state (a `status:` containing `leave`/`oof`/`vacation`/`paternity`/`maternity`, a dated leave block overlapping today, or a 🚨/`### ACTIVE STATUS` body header) — do NOT assign them as the owner. Reframe to 🟢 Watching with `(on leave through <date>; auto-resume on return)`, unless the action item is itself about *responding to* that leave. This prevents assigning work to someone Scout already knows is away.

Whenever you flip an item from `[ ]` to `[x]` — or process a {{USER_NAME}}-authored `[x]`, an inline `//==<<` close-out directive ("close this out", "I don't need this anymore", "move to completed"), or a close-it-out reply — you MUST, in the same write:

1. Add an entry under `## ✅ Recently Completed` summarizing the closure with date + evidence (commit hash / transcript / message ts / the inline-comment quote) and any wikilinks the prior framing carried.
2. **Delete the original row from its origin section** (🔴/🟡/🟢). Never leave a checked row sitting in an active section — that's a duplicate-surface graveyard.
3. If the closure is too trivial to deserve a Recently-Completed entry, still delete the origin row. Never write the entry without the inverse delete.

**Inline `//==<<` close-out directives are first-class delete instructions** — acknowledging one in the next DM is insufficient; the file must be physically updated (check box → write completed entry → delete origin row → remove the marker).

**Batch exception (threshold N=5).** If a run encounters **more than 5 unprocessed `//==<<` markers** across the vault (a large batch dropped at once), do NOT try to resolve them all this run — switch to **triage mode**: inventory + categorize + route them into a dated `knowledge-base/comment-triage-YYYY-MM-DD.md` index and the right queues, leaving the markers in place, and surface the index in the wrap notification. Full rule in the KB-deep-work phase (Step 2-pre).

**Pre-commit audit** — no `[x]` rows may sit outside the Recently Completed section:

```bash
grep -nE '^\s*- \[x\]' "$DAILY_FILE" | grep -viE 'recently completed|## ✅' \
  && echo "WARN: checked rows above are outside ✅ Recently Completed — migrate or annotate '_(kept intentionally — REASON)_' before commit" >&2
```

(Sunset: retire this rule when a programmatic `action-items` done-lifecycle ships and handles section migration deterministically.)

### Hard Rule — Transcript-Derived Names Must Pass an Ontology Match

Auto-transcribed sources (Granola, Gemini/Drive auto-notes, Fathom recaps, meeting summaries) frequently mis-hear names. **Never** write a transcribed name into action items, KB, or a DM without first resolving it against the knowledge graph. For every name-shaped token from a transcribed source:

```bash
cd {{SCOUT_DIR}} && python knowledge-base/ontology/parser.py name_lookup --token "<Token>"
```

- **Exact match** → use the matched entity with its `[[people/<slug>]]` wikilink.
- **Fuzzy match** (within threshold ≈ Levenshtein-2 / phonetic-equivalent) → use that entity, append a `[name-fuzzy-resolved]` marker.
- **No match** → write at most "Contact person matching '<verbatim-token>' (no KB match — please confirm)" and route a `[transcript-drift]` entry to `review-queue.md`. **NEVER elevate an unresolved transcribed name to a 🔴 headline** — 🟡 with the explicit "no KB match" framing is the ceiling.

If {{USER_NAME}} corrects a name ("who is X?", "X doesn't exist", "this is hallucinated"), bind the misheard form to the correct entity as a "known transcription drift" note so it resolves next time.

## Knowledge Graph Personal Tasks

If the ontology parser is set up, query it for personal tasks and deadlines:

```bash
cd {{SCOUT_DIR}} && python knowledge-base/ontology/parser.py query --type task --exclude-status "completed,cancelled"
```

**Always filter terminal statuses — never query bare `--type task`.** A bare query returns *every* task entity, including ones already finished, so completed work resurfaces on the daily list as if it were still open (the classic regression: a task whose `status` flipped to `completed` weeks ago reappears tomorrow). Pass `--exclude-status "completed,cancelled"` to get the currently-actionable set — task entities use mixed statuses (`open` / `in-progress` / `scheduled` / …), so excluding the terminal ones is more robust than trying to enumerate the live ones. Narrow further with `--domain personal` / `--domain work` when you want a single slice, `--status <s>` for an exact status, and `--deadline-before <ISO-date>` for deadline sweeps.

**`surface_rule` windows are authoritative.** Before rendering any `type: task` entity into the daily file, read its `surface_rule:` block. If `default: do_not_surface` and **no** `windows` entry covers today's date AND **no** `always_visible_if` condition fires, the task is intentionally muted — do not render it, do not flag it stale, do not mention it in the wrap notification. If a window covers today, render at that window's `surface:` priority using its `task:` label override if present. If `always_visible_if` fires, force 🔴. A task with no `surface_rule` falls back to surfacing daily until `status: completed` (back-compat). This stops long-window tasks (multi-week trips, future-dated deadlines) from polluting the daily surface during their idle phases.

For the morning briefing, focus on:

1. **Open personal tasks** — tasks with `domain: personal` and `status: open`. These carry forward into daily action items alongside work tasks.
2. **Deadline escalation** — any task with a `deadline` field. Apply escalating priority:
   - 7+ days out: keep existing priority
   - 3-7 days out: escalate to 🟡 if not already higher
   - <3 days out: escalate to 🔴
3. **Birthday alerts** — check if any person entity has a `birthday` field matching today's month/day. If so, add a reminder to the action items.

Personal tasks appear in the action items file in a **Personal** section after work items.

During consolidation, also check for completion signals:
- If any personal task has a `completion_signal: gmail_confirmation`, check Gmail for matching confirmations. If found, update the entity file's `status` to `completed` and add `completed_date`.
- If {{USER_NAME}} reported completion via Slack DM, update the entity file.
- Carry open personal tasks forward in the action items file's Personal section.

## Recurring-Task Cadences (briefing AND every consolidation)

Recurring commitments are **cadence-driven, not event-driven** — a "quiet delta" consolidation must still surface them. This is the load-bearing fix for missed standing commitments (e.g. a weekly Friday status update). If the KB has any `recurring_task` entities, run the cadence computer at compose time:

```bash
cd {{SCOUT_DIR}} && python recurring-task-status.py --date "$(TZ={{TIMEZONE}} date '+%Y-%m-%d')"
# (script lives at {{SCOUT_DIR}}/scripts/recurring-task-status.py)
```

**Live-completion lookup (do this before trusting the date math).** For each entity with a `completion_evidence` source (`linear_project_update`, `slack_post`, `gmail_confirmation`), resolve the *real* last-completion date from the live source — e.g. Linear `get_project` → `lastUpdateAt`, or a Slack/Gmail search — and feed it back so the verdict reflects live evidence (the override is not written to the entity file):

```bash
python recurring-task-status.py --date "<today>" \
    --last-completed <entity-slug>=<YYYY-MM-DD>
```

For each entity the script returns:

1. **`due` / `overdue`** → **mandatory** action item. 🔴 when `surface_window` is `T-0 morning` or the item is `overdue`; 🟡 when the window is wider than 24h. Link `[[recurring-tasks/<name>]]`. `domain: personal` ones go in the Personal section.
2. **`done`** (completion evidence satisfied for this cadence window) → surface as ✅ Recently Completed, not a TODO.
3. **`surfacing`** (inside the window, not yet the due day) → 🟡 heads-up.
4. **`upcoming` / `unknown`** → no action item.

**Do not write "quiet window" / "nothing material" framing until the `due`/`overdue` list is exhausted** — a `weekly:friday` cadence is by definition material on a Friday.

## Mandatory Cross-Check

**Before ANY item becomes a To Do, it must pass ALL available cross-checks.** The cross-check adapts to your connected services:

- **If Calendar connected:** Is this already scheduled? Does a meeting already exist for this? Was an event recently cancelled (meaning {{USER_NAME}} already handled it)?
- **If project tracker connected (e.g., Linear, GitHub Issues, Jira):** Does a ticket already exist? Has it already been resolved?
- **If messaging connected (e.g., Slack, email):** Did {{USER_NAME}} already handle this? Search outbound messages about this topic. If {{USER_NAME}} sent a message about it, the item is likely handled or in progress.
- **If code host connected (e.g., GitHub, GitLab):** Did {{USER_NAME}} already submit a PR, merge code, or commit changes related to this? Check recent activity.
- **Always (regardless of connectors):** Is this the same item phrased differently? Deduplication pass across all candidates.

For each candidate action item, apply every available cross-check from the list above. The number of checks scales with your connected services — a 2-connector setup uses 2 checks, a 5-connector setup uses all 5. Every check that CAN be run MUST be run before an item is written.

## Source Equality for Action Items

**Meeting transcripts and messages are signals, not facts.** Every action item candidate must be verified against other available sources before being written down. A meeting transcript saying "{{USER_NAME}} will do X" does not mean X is a valid action item — it means X is a *candidate* that must survive the cross-check.

**{{USER_NAME}}'s own actions are the most important signal.** What {{USER_NAME}} has actually DONE (messages sent, meetings cancelled, code committed, PRs submitted) always takes priority over what notes say they should do. If a meeting transcript says "{{USER_NAME}} will send the proposal" but {{USER_NAME}} already sent it (found in outbound messages or sent mail), the item is Done, not To Do.

## Per-Item Reconciliation (Consolidation Mode)

During consolidation runs, every action item being written or updated must go through individual reconciliation. This is the most important step — do not batch or shortcut it.

**For EVERY action item:**

### 1. Check if {{USER_NAME}} already handled it
Search for evidence that {{USER_NAME}} completed or progressed the item:
- Outbound messages or DMs about the topic
- Calendar changes (cancelled events, new events created)
- Code commits, PRs opened or merged
- Documents created or edited
- Session history showing work done with AI tools

If evidence of completion exists, mark the item ✅ Done with a citation to the evidence.

**No unverified negatives.** Before asserting a *negative* completion-state — "not done yet", "still your move", "awaiting X", "unsent", "not created" — where the completion would be **observable on a connector**, you MUST query that connector this run. A "nothing happened" is a claim, not the default: an unverified negative is the easiest thing to assert lazily and the hardest to notice is wrong, and telling {{USER_NAME}} that completed work was ignored erodes trust. If you can't query (connector down, out of scope), write the claim `[unverified — not queried this run]`, never as fact. (Repo-creation items are the classic trap — verify with `gh repo list`, not a PR scan; see the GitHub Repository Activity phase.)

### 2. Do a targeted topic search
Search specifically for the topic keywords across all available connectors. Don't rely on the broad scan — do a focused search for this specific item. This catches context that a general sweep might miss.

### 3. Enrich with specifics
Never write vague action items. If a meeting transcript says "{{USER_NAME}} will PR something," don't write that — search the code host for the actual PR. If a to-do says "contact someone about a project," check messaging and the issue tracker to see if that's already been done and what the actual next steps are.

**Bad:** "Follow up on the deployment issue"
**Good:** "Reply to [Person]'s message in #project-channel about the staging deployment failure (error: connection timeout on service X) — they asked for help debugging at 2:15 PM"

### 4. Apply the cross-check
Run every available cross-check (calendar, issue tracker, messaging, code host, deduplication) against this specific item.

### 5. Write with full context and evidence
- If completed: mark ✅ with evidence (link to the message, calendar change, PR, commit, etc.)
- If partially done: describe what's done and what specifically remains
- If not started: include the full context from all sources, not just the one that surfaced it
- Always include source citations showing which connectors confirmed the item

After reconciliation is complete, refresh the `## 🪵 Run notes & connector availability` block at the **bottom** of the action items file — prepend this run's entry (timestamp, mode, counts, connector availability) as the newest line and trim the block to the last 3 runs. Do not write run metadata at the top of the file.

## Scout Digest

At the end of every briefing and consolidation run, append or update a **Scout Digest** section at the bottom of today's action-items file (`action-items/action-items-YYYY-MM-DD.md`), before the Sources line. This is the same digest the dreaming phase maintains — there is **one** digest per day, shared across all of today's sessions, so {{USER_NAME}} can catch up on what {{INSTANCE_NAME}} has been doing across runs without reading each one.

**Format:**

```markdown
## Scout Digest — [Date] ([Time])

**{{INSTANCE_NAME}} ran N sessions today** (breakdown by type). Here's what needs your attention:

### Files to Review
- **[[file]]** — what changed and why {{USER_NAME}} should look at it

### Your Input Needed
| Item | What {{INSTANCE_NAME}} needs | Priority |
|------|-----------------|----------|
| ... | ... | 🔴/🟡/🟢 |

### KB Growth Today
- Ontology stats, new entities, patterns added
```

**Rules:**
- If a digest already exists from an earlier session today, **update it in place** (don't duplicate) — bump the time, fold in this run's changes.
- Only include files that changed **substantively** (not just timestamp updates).
- "Your Input Needed" lists ONLY items where {{USER_NAME}}'s action unblocks {{INSTANCE_NAME}} or a project.
- Keep it scannable — no walls of text. Link to KB files for details.
- On a `weekend-briefing` run, fold the **Monday Preview** into the digest rather than emitting it separately.
