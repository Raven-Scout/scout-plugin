---
phase: connector
name: asana
slot: inbound-scan
mode: [consolidation, briefing]
requires: asana
---

## Asana Inbound Scan — Task Activity

Check for changes to {{USER_NAME}}'s assigned Asana tasks and any newly created or assigned tasks.

**Primary path: the `asana-api` CLI** — a PAT-based wrapper. Run every Asana call as `python3 scripts/asana_api.py <subcommand>`; it reads the token from `~/.agent-skills/asana/asana_pat` (or `$ASANA_ACCESS_TOKEN`) so the secret never appears on the command line. It returns only the requested fields as JSON — pipe through `jq` and keep raw output in the sandbox.

> **Script path.** Every `scripts/asana_api.py` reference in this phase is **vault-relative**: at runtime the session cwd is `{{SCOUT_DIR}}` (every runner `cd`s there), and `/scout-setup` + `/scout-update` stage the script into `{{SCOUT_DIR}}/scripts/asana_api.py`. It is **not** the plugin repo's root `scripts/` dir — the source of truth lives in the plugin at `skills/asana-api/scripts/asana_api.py` and is copied into the vault by bootstrap. For raw/unsupported endpoints, use the CLI's own `request` subcommand (e.g. `asana_api.py request GET /path --query k=v`); there is no separate curl wrapper.

**Fallback:** if the CLI is unavailable (script missing, PAT not configured), use the Asana MCP server (`mcp__claude_ai_Asana__*`) with the equivalent parameters.

**Response shapes** (so the `jq` filters below are right): raw-passthrough commands — `request`, `section-tasks`, `search-tasks`, `project-assigned-tasks` — return `{"data": [...]}`; lookup commands — `task`, `task-comments`, `task-stories` — return `{"command", "count", "items": [...]}`; `board` returns `{"project_gid", "sections": [...]}`. Note `search-tasks` requires `--text` and `project-assigned-tasks` requires `--workspace {{ASANA_WORKSPACE_GID}}`.

Compute the lower bound once and reuse it across all Asana calls:

```
SINCE_DAYS = 1   # consolidation = 1d, briefing = 1d, weekend briefing = 3d
```

Convert to ISO-8601 (`modified_at.after`, `created_at.after`, `completed_at.after`).

### Status / Completion Changes

`search-tasks` requires `--text`, so for date-bounded assignee sweeps (no keyword) use the raw `request` passthrough to the workspace search endpoint:

```bash
# Recently completed (candidate Done items for reconciliation)
python3 scripts/asana_api.py request GET /workspaces/{{ASANA_WORKSPACE_GID}}/tasks/search \
  --query 'assignee.any=me' --query 'completed=true' --query 'completed_at.after=<SINCE>' \
  --opt-fields 'gid,name,completed_at,memberships.section.name' | jq '.data'

# Recently modified open (section move, due-date shift, custom-field update)
python3 scripts/asana_api.py request GET /workspaces/{{ASANA_WORKSPACE_GID}}/tasks/search \
  --query 'assignee.any=me' --query 'completed=false' --query 'modified_at.after=<SINCE>' --query 'sort_by=modified_at' \
  --opt-fields 'gid,name,due_on,modified_at,memberships.section.name' | jq '.data'
```

If the workspace is non-premium (search endpoint returns a 402/forbidden), fall back to `project-assigned-tasks <project_gid> --workspace {{ASANA_WORKSPACE_GID}} --completed false --include-comments` per configured project, and filter `modified_at >= <SINCE>` locally. (MCP fallback: `search_tasks(assignee_any="me", ...)` / `get_my_tasks`.)

Status transitions to watch for (Asana has no named statuses — section membership is the equivalent):
- **Backlog → In Progress section**: work started
- **In Progress → In Review / Awaiting QA**: work delivered
- **Any → Done section / completed=true**: task resolved
- **Backlog → a current-cycle section**: task was prioritized

For each change, update the KB task tracker and note implications for action items.

### Newly Created / Newly Assigned Tasks

```bash
# Newly created assigned to me
python3 scripts/asana_api.py request GET /workspaces/{{ASANA_WORKSPACE_GID}}/tasks/search \
  --query 'assignee.any=me' --query 'created_at.after=<SINCE>' --query 'completed=false' \
  --opt-fields 'gid,name,due_on,projects,assignee,created_at' | jq '.data'

# New work in configured projects (may affect {{USER_NAME}} even when not assigned)
python3 scripts/asana_api.py request GET /workspaces/{{ASANA_WORKSPACE_GID}}/tasks/search \
  --query 'projects.any={{ASANA_PROJECTS}}' --query 'created_at.after=<SINCE>' --query 'completed=false' \
  --opt-fields 'gid,name,assignee,due_on' | jq '.data'
```

Asana exposes no "assignee changed" filter — treat any assigned task whose `created_at` predates the previous run as a likely re-assignment.

### Comments / Stories Updates

For each changed task, fetch only the comment stories (text + html_text) with the dedicated command:

```bash
python3 scripts/asana_api.py task-comments <GID> | jq '.items'
```

Recent comments may carry questions needing {{USER_NAME}}'s response, status notes / blockers, or reviewer feedback. Use `task-stories <GID>` when you also need system stories (section moves, completions, assignee changes).

### Mentions / Followers

```bash
python3 scripts/asana_api.py request GET /workspaces/{{ASANA_WORKSPACE_GID}}/tasks/search \
  --query 'followers.any=me' --query 'modified_at.after=<SINCE>' --query 'completed=false' \
  --opt-fields 'gid,name,assignee,due_on' | jq '.data'
```

Tasks where {{USER_NAME}} is a follower (often via `@`-mention in a description or comment) but not the assignee — high-signal context, often relevant even if not directly actionable.

---
phase: connector
name: asana
slot: query
mode: [briefing]
requires: asana
---

## Asana Query — Briefing Data Gathering

All commands run via the `asana-api` CLI (`python3 scripts/asana_api.py …`); MCP `asana_*` tools are the fallback when the CLI is unavailable.

### Priority Sections (always sweep) — Dynamic Roster

If `scout-config.yaml` defines `asana_priority_sections`, Scout enumerates every task in each referenced project section on the configured cadence. Prefer this over hardcoded task lists when the roster is fluid — e.g. a recurring weekly-status section in a leads project that grows as new team leads onboard.

Each config entry:
```yaml
asana_priority_sections:
  - section_gid: '<asana_section_gid>'
    name: '<display name>'
    cadence: monday | daily | weekly_friday | ...
    expected_cadence: weekly_friday   # optional — each task should have a new dated subtask this often
    self_assignee_gid: '<user_asana_gid>'   # entries assigned to this gid are role=self; others are role=direct_report
    why: <one-line reason>
```

**Sweep procedure (when cadence matches today):**

```bash
# 1. List section tasks  (section-tasks returns {"data": [...]})
python3 scripts/asana_api.py section-tasks <section_gid> \
  --opt-fields 'name,gid,assignee.name,assignee.gid,completed,modified_at' | jq '.data'

# 2a. Fetch a task with its subtasks
python3 scripts/asana_api.py task <task_gid> \
  --opt-fields 'name,notes,subtasks.gid,subtasks.name,subtasks.assignee.name,subtasks.completed,subtasks.due_on,subtasks.modified_at' | jq '.items[0]'

# 2e. Fetch the subtask's comments
python3 scripts/asana_api.py task-comments <subtask_gid> | jq '.items'
```

For single-task planning, `task-bundle <task_gid> --project-gid <project_gid>` pulls fields + comments + subtasks + project workflow context in one call.

1. **List section tasks** (above).
2. **For each task:**
   a. Fetch the task with subtasks.
   b. If the task uses a dated-subtask pattern (e.g. `Weekly Update - <Name> - YYYY-MM-DD`), identify the **latest dated subtask** by parsing the date suffix and sorting descending.
   c. Determine role: parent task's `assignee.gid == self_assignee_gid` → `role: self`; else → `role: direct_report`.
   d. Fetch that subtask.
   e. Fetch comments since previous run (`task-comments`; they are already comment-only).
   f. Classify write state: **Written** (notes extend meaningfully beyond template prompts) / **WIP/empty** (only template prompts) / **Missing** (no subtask for expected period).

**Integrity check (`expected_cadence: weekly_friday`):** the latest *complete* (non-WIP, non-empty) dated subtask per task should correspond to the most recent Friday. If older, flag as overdue.

**Action item rules:**
- **`role: self` + own subtask WIP/empty/missing by cadence day** → 🔴 urgent: write/finish before dependent meeting.
- **`role: direct_report` + subtask older than expected_cadence OR WIP/empty by cadence day** → 🟡 to-do: nudge that person.
- **Inbound comment from manager on `role: self` subtask not replied to** → 🔴 urgent.

### Priority Tasks (one-off) — User-Pinned Cadences

For pinned individual tasks outside any section sweep:
```yaml
asana_priority_tasks:
  - gid: '<asana_task_gid>'
    name: '<display name>'
    cadence: monday_afternoon | daily | weekly_friday | ...
    role: self | direct_report | peer
    why: <one-line reason>
```

Same per-entry procedure as section entries (step 2a–f above).

### All Open Assigned Tasks

```bash
python3 scripts/asana_api.py project-assigned-tasks <PROJECT_GID> --workspace {{ASANA_WORKSPACE_GID}} \
  --completed false --include-task-position --include-comments --comment-limit 3 \
  --opt-fields 'gid,name,due_on,due_at,assignee,completed,projects.name,memberships.section.name,custom_fields' | jq '.data'
```

`project-assigned-tasks` searches the workspace by project + assignee and includes matching subtasks with parent-section context — it catches assigned subtasks that a bare project listing misses. For each task note: name + GID, project/section, due date (`due_on` date-only, `due_at` datetime), the `Priority` custom field if present, and recent comments.

### Tasks Due This Week

```bash
python3 scripts/asana_api.py request GET /workspaces/{{ASANA_WORKSPACE_GID}}/tasks/search \
  --query 'assignee.any=me' --query 'completed=false' \
  --query 'due_on.before=<TODAY+6d>' --query 'due_on.after=<TODAY-1d>' \
  --query 'sort_by=due_date' --query 'sort_ascending=true' \
  --opt-fields 'gid,name,due_on,memberships.section.name' | jq '.data'
```

Surface anything overdue (`due_on < today`) at the top of the briefing.

### Active Project Snapshot

For each project in `{{ASANA_PROJECTS}}`, pull active sections + tasks:

```bash
python3 scripts/asana_api.py board <PROJECT_GID> --context | jq '.'
```

`board` returns sections and their tasks in order; `--context` adds per-section stats for workflow analysis. Look for: assignments without progress, blockers, items at risk for the cycle.

### Priority Check

Flag tasks that are:
- **Overdue** (`due_on < today` and `completed=false`) — top priority, surface every run
- **High priority custom field** and not yet in an "In Progress" section
- **Stuck** — same section / same assignee for more than 2 days (compare `modified_at`)
- **Blocked** — has a `blocked` tag, a comment containing "blocked", or a "Blocked by" relation in `dependencies`

---
phase: connector
name: asana
slot: cross-check
mode: [consolidation, briefing]
requires: asana
---

## Asana Cross-Check

Before promoting any candidate action item to To Do, verify against Asana via the CLI (MCP fallback in brackets).

**Does a task already exist for this?** Search by keyword:

```bash
python3 scripts/asana_api.py search-tasks --workspace {{ASANA_WORKSPACE_GID}} \
  --text "<keywords>" --project {{ASANA_PROJECTS}} --completed false \
  --opt-fields 'gid,name,completed,memberships.section.name' | jq '.data'
```

(MCP fallback: `search_tasks(text=..., projects_any=...)`; non-premium fallback: `search_objects`.) If a matching task exists:
- Link the action item to the task (include the GID and permalink — `https://app.asana.com/0/0/<GID>`).
- Use the task's section / completed state as the source of truth for progress.
- Don't create a duplicate.

**Has it already been resolved?** Check recently completed matches:

```bash
python3 scripts/asana_api.py search-tasks --workspace {{ASANA_WORKSPACE_GID}} \
  --text "<keywords>" --completed true \
  --opt-fields 'gid,name,completed_at' | jq '.data'
```

Common pattern: a meeting generates "we need to fix X" but X was already completed yesterday by someone else.

**Is the status current?** If the action item references a known GID:

```bash
python3 scripts/asana_api.py task <GID> \
  --opt-fields 'completed,completed_at,assignee.name,due_on,memberships.section.name,custom_fields' | jq '.items[0]'
```

If the KB says "In Progress" but Asana says `completed=true`, update the action item to Done and cite `completed_at` as evidence.

**Did {{USER_NAME}} already act on this task?** Inspect recent stories:

```bash
python3 scripts/asana_api.py task-stories <GID> | jq '.items'
```

A recent story authored by {{USER_NAME}} (comment, section move, completion, assignee change) signals "already handled" — use the story `created_at` and `text` as evidence.

---
phase: connector
name: asana
slot: update
mode: [consolidation, briefing]
requires: asana
---

## Asana-Sourced KB Updates

After scanning Asana, update the knowledge base with current task data. **Task status staleness is the most common form of KB rot** — treat this update step as critical.

### Task Tracker Sync

For every Asana task referenced in the KB:
1. **Verify the state matches Asana.** `python3 scripts/asana_api.py task <GID> --opt-fields 'completed,assignee.name,memberships.section.name,due_on,custom_fields'` and reconcile.
2. **Update section / completion** if it changed.
3. **Update due date or priority** if either shifted.
4. **Add new comments or context.** Pull `task-comments <GID>` for the most recent updates and quote pertinent snippets in the KB.
5. **Move completed tasks** to a "Completed" section in the KB — don't delete; cite `completed_at` and `completed_by.name`.

### Spot-Check Requirement

Every run must spot-check at least 2-3 task states against Asana as the source of truth. Pick tasks that are high priority, haven't been verified recently, or are referenced by current action items.

### Posting Comments (incl. @mentions)

When a slot calls for {{INSTANCE_NAME}} to reply on a task (e.g. a weekly-status nudge or a manager-summary follow-up), post via `comment-task`. Plain text:

```bash
python3 scripts/asana_api.py comment-task <task_gid> --text "Status synced — see KB for details."
```

To **@mention** a person, use `--html-text` with Asana's rich-text mention markup — a self-closing anchor carrying the user's Asana GID. Asana renders it as `@Name` and notifies them:

```bash
python3 scripts/asana_api.py comment-task <task_gid> \
  --html-text '<body><a data-asana-gid="{{ASANA_GID_OF_PERSON}}"/> please refresh this before Friday.</body>'
```

Resolve the GID from `people.md` (the `asana_gid` field) or `users --workspace {{ASANA_WORKSPACE_GID}}`. For multi-line or longer comments, use `--html-text-file`. Never post an externally-visible comment without {{USER_NAME}}'s approval during interactive sessions.

### New Tasks

Add newly discovered tasks to the tracker with: GID + name, project(s)/section, assignee, due date, priority custom field if present, permalink (`https://app.asana.com/0/0/<GID>`), and a one-line context on why it matters.

### Project File Updates

If task changes affect active projects, update `knowledge-base/projects/`: changed task states, new tasks, completed milestones (`resource_subtype="milestone"`), section progress for the active cycle.

### People Updates

If new assignees, followers, or commenters appeared who are not in `people.md`, add them:
- Name (resolve via `python3 scripts/asana_api.py users --workspace {{ASANA_WORKSPACE_GID}}` or `user <GID>` if only a GID is known)
- Asana user GID (as `asana_gid:` — needed for @mention markup above)
- Email if visible
- Context: "Assignee on <task name> in <project>" or "Commented on <task name>"
- Role `[single-source]` based on observed activity
