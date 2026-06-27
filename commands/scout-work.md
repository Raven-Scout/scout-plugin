---
name: scout-work
description: Interactive work session — walks through today's actionable items one at a time, presents a recommended action with draft content, and executes each one only with explicit approval. Runs in the current conversation (not as a background session).
---

# Scout Work Session

Run an interactive Scout work session to knock items off today's action items list.

Unlike `/scout-briefing`, `/scout-consolidation`, `/scout-dream`, and `/scout-research` (which run as background processes), this runs **in the current conversation**. You present actionable items one at a time with a recommended action ready to go, and wait for approval before executing.

The goal: the user decides, Scout drafts and executes. Never send a message, merge a PR, create a meeting, or take any externally-visible action without explicit approval.

---

## Phase 1: Load & Filter

1. Get today's date in the user's timezone. Read `scout-config.yaml` for the `timezone` field (default `America/New_York` if missing):
   ```bash
   TZ=<timezone> date '+%Y-%m-%d'
   ```

2. Read today's action items file: `<SCOUT_DIR>/action-items/action-items-YYYY-MM-DD.md`
   - If it doesn't exist, tell the user: "No action items file for today. Run `/scout-briefing` or `/scout-consolidation` first to generate one."
   - Stop here if no file.

3. Parse the file and collect all unchecked items (`- [ ]`) from these sections:
   - **🔴 Urgent / Time-Sensitive**
   - **🟡 To Do**
   - **Personal**

4. **Skip** items from the **🟢 Watching** section — those are monitoring, not actionable.

5. **Skip** items explicitly described as blocked by another item (e.g., "Blocked by PROJ-2630", "Waiting on Legal review").

6. Present a numbered summary:
   ```
   Found N actionable items:
   - X urgent
   - Y to do
   - Z personal

   Ready to start?
   ```

Wait for the user to confirm before proceeding.

---

## Phase 2: Work Loop

Process items in priority order: Urgent first, then To Do, then Personal.

For **each item**, do the following:

### Step 1: Triage — classify the action type

Use the user's connected tools to determine what kind of action this is. The matrix below is a starting point — use judgment when an item spans multiple categories.

| Type | Tools to use | Signals in the item |
|------|-------------|-------------------|
| **GitHub** | `gh` CLI (pr view, pr merge, pr review) | PR #, merge, reviewer, CI, GitHub URL |
| **Slack** | Slack MCP (slack_send_message, slack_read_channel, etc.) | Slack DM, reply, post, thread, channel name |
| **Calendar** | Google Calendar MCP (create_event, list_events) | Schedule, meeting, invite, calendar |
| **Linear** | Linear MCP (save_issue, get_issue) | Linear issue ID, status update, create issue |
| **Email** | Gmail MCP (create_draft, send_message) | Email, reply, draft, follow-up with an email address |
| **Research** | WebSearch, WebFetch | Research, look up, find out, investigate, compare |
| **Compound** | Multiple of the above | Item needs several steps in sequence |

### Step 2: Gather fresh context

Before presenting the item, get current state from the relevant tool. Items written in the morning briefing may already be resolved by the time the user sits down to work them.

- **GitHub:** `gh pr view <number> --json state,reviews,mergeable,statusCheckRollup` or `gh issue view` as appropriate
- **Slack:** Read the relevant channel or thread to get latest messages
- **Linear:** Get the issue's current status, assignee, and recent activity
- **Email:** Search the relevant thread for recent replies
- **Calendar:** List upcoming events to check for conflicts or existing meetings
- **Research:** Run a WebSearch query relevant to the item, fetch key results with WebFetch, summarize findings

### Step 3: Present the item

Use this format:

```
### [N/total] Item title
**Type:** GitHub | Slack | Calendar | Linear | Email | Research | Compound
**Current status:** <what you just found — has anything changed since the action items were written?>

**Recommended action:**
<specific action you'll take, with draft content shown>

> [For messages: show the exact draft text]
> [For GitHub: show the exact command]
> [For Calendar: show the meeting details]
> [For Research: show the summary and recommended next step]

**do it** / **skip** / **modify?**
```

### Step 4: Wait for approval

Do **nothing** until the user responds. Accept:
- **"do it"** / **"yes"** / **"go"** / **"y"** → execute the action
- **"skip"** / **"next"** / **"s"** / **"n"** → move to next item
- **"done"** / **"stop"** / **"quit"** → end the session, go to summary
- **Any other text** → treat as a modification (e.g., "change the message to say X", "merge but use squash", "also CC <name>")

### Step 5: Execute on approval

1. Take the action using the appropriate tool.
2. Update the action items file:
   - Change `- [ ]` to `- [x]` on the item's line
   - Append ` — ✅ Done via work session` to the line
3. Commit immediately with a `work [HH:MM]:` message so the git log shows work-session activity distinct from briefing/consolidation/dreaming:
   ```bash
   git -C <SCOUT_DIR> add -A && git -C <SCOUT_DIR> commit -m "work [HH:MM]: <brief item summary>"
   ```
4. Move to the next item.

### Step 6: Handle edge cases during the loop

- **Item already done:** If fresh context shows the item is already completed (PR merged by someone else, message already sent, meeting scheduled), mark it complete automatically, tell the user, and move on. Commit with a note that it was auto-resolved.
- **Item now blocked:** If fresh context reveals a new blocker, note it and skip.
- **Tool unavailable:** If a tool call fails, explain what happened and offer to skip or retry.

---

## Phase 3: Summary

After all items are done or the user says stop:

```
## Work Session Summary

**Completed (N):**
- ✅ Item 1 — [link/evidence]
- ✅ Item 2 — [link/evidence]

**Skipped (N):**
- ⏭️ Item 3 — reason
- ⏭️ Item 4 — reason

**Auto-resolved (N):**
- 🔄 Item 5 — was already done

**Remaining actionable items:** N
```

---

## Important Notes

- **Commit format:** `work [HH:MM]: <summary>` — consistent with `briefing [HH:MM]:`, `consolidation [HH:MM]:`, `dreaming [HH:MM]:`, `research [HH:MM]:`. The format makes the git log readable as an audit trail of who did what and when.
- **Never take an externally-visible action without explicit approval.** The whole point of this mode is that the user decides.
- **One item at a time.** Don't batch. Don't skip ahead. Present, wait, execute, commit, next.
- **If the user says "do all" or "auto"** — still present each item, but execute without waiting for individual approval. The user has given blanket consent for the session. Stop and ask again if anything non-obvious comes up (e.g., a draft that needs judgment on tone or recipient).
