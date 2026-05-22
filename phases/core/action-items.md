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

## Action Item Categories

Categorize every action item using these levels:

- **🔴 Urgent**: Needs attention today
- **🟡 To Do**: Should be done soon
- **🟢 Watching**: Tracking but no action needed yet
- **✅ Done**: Completed with evidence of completion

## Action Items File Format

Create `action-items/action-items-YYYY-MM-DD.md` using today's date. Include:

```markdown
# Action Items — YYYY-MM-DD

**Last consolidated:** YYYY-MM-DD HH:MM [timezone]

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
```

All action items files must include `[[wikilinks]]` to any KB files referenced by action items.

### Hard Rule — Every Task Line Has a Stable `[#XXXX]` Prefix

**Every new task line you write MUST start with a fresh `[#XXXX]` 4-char Crockford prefix.** The prefix is the structural identifier scout-app uses to mark tasks done, snooze them, and attach comments — without it, the app falls back to brittle markdown-substring matching that fails on emoji, italics, em-dashes, embedded links, or any non-ASCII drift. Issue #10 of scout-app catalogs the failure modes.

**Canonical task line shape:**
```
- [ ] [#XXXX] **<bold subject>** <optional body, links, italic context>
```

The prefix goes **after** the checkbox marker and **before** the bold subject. Exactly that order — the parser keys off it.

**Mint a fresh prefix per task** by shelling out to scoutctl. The CLI collision-checks against `id-map.json` so two consecutive calls always return different prefixes:

```bash
# Inside your task-writing loop:
PFX=$(scoutctl action-items new-prefix)
echo "- [ ] [#${PFX}] **${SUBJECT}** ${BODY}" >> "$DAILY_FILE"
```

**Carry-forward keeps the original prefix.** When propagating an item from yesterday's file into today's, copy the `[#XXXX]` verbatim — do NOT mint a new one. The prefix is the task's identity across days; minting a new one breaks the link in scout-app's session↔task store and severs the commit-history trail.

**Existing unprefixed lines (legacy carryover):** when you find a task carried forward from a pre-prefix era that lacks `[#XXXX]`, mint a fresh prefix for it on first touch. Or run the one-shot backfill at the top of the briefing:

```bash
scoutctl action-items backfill-prefixes "$DAILY_FILE"
```

The backfill is idempotent — already-prefixed lines are left alone — so it's safe to run defensively at the start of every briefing/consolidation step that writes new lines.

**Self-check before commit:** every `- [ ]` and `- [x]` line in the file MUST match the regex `^\s*- \[[ x]\] \[#[0-9A-HJKMNP-TV-Z]{4}\] `. A `grep` sanity check at compose time catches drift:

```bash
grep -nE '^\s*- \[[ x]\] ' "$DAILY_FILE" | grep -vE ' \[#[0-9A-HJKMNP-TV-Z]{4}\] ' && \
    echo "ERROR: lines missing [#XXXX] prefix above — fix before commit" >&2
```

If that grep finds anything, the file is non-compliant and scout-app's writes will silently fall back to fragile subject-matching for those lines.

## Knowledge Graph Personal Tasks

If the ontology parser is set up, query it for personal tasks and deadlines:

```bash
cd {{SCOUT_DIR}} && python knowledge-base/ontology/parser.py query --type task
```

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

Update the "Last consolidated" timestamp in the action items file after reconciliation is complete.
