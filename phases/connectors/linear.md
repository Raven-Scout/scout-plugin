---
phase: connector
name: linear
slot: inbound-scan
mode: [consolidation, briefing]
requires: linear
---

## Linear Inbound Scan — Issue Activity

Check for changes to {{USER_NAME}}'s assigned issues and any newly created or assigned issues.

### {{USER_NAME}}'s Own Linear Activity Since Last Run (delta-scan — surface this first)

Before the issue-state scan below, build an **actor-attributed** delta of what {{USER_NAME}} did in Linear since the last run — issues he created, moved (state change), commented on, or edited. This is the Linear analog of the Slack outbound scan and is a primary signal of completed/in-progress work. Surface it **early** in the run summary (before "Today's Wins") — never silently omit it.

- **Volume-sanity gate:** if the delta is suspiciously empty (zero activity) for a normally-active window, re-fetch before concluding "no Linear activity" — a silent empty result erodes trust more than a slow run.
- **Re-fetch fast-moving issues at compose time:** any issue you're about to cite with a state framing ("In Review", "blocked") must be re-read at compose time, not carried from cache (mirrors the Gmail/Pattern #43 rule).

### Project-Update Polling for {{USER_NAME}}-Led Projects (leadership pings)

`list_issues` + issue comments **miss project-level status-update comments**, which is where leadership often pings. For every Linear project {{USER_NAME}} leads or owns, call `get_status_updates` (and `list_comments` on the project) for updates since the last run.

**Leadership-ping headline rule:** if a project-update comment on {{USER_NAME}}'s own project comes from someone with a managerial/leadership relationship to {{USER_NAME}} (per `people.md` / the ontology `manages` relation), auto-promote it to the run summary's 🔴 headline — a manager's ask on {{USER_NAME}}'s project is the highest-priority inbound signal and must not be buried.

### Status Changes

Use `list_issues` filtered to {{USER_NAME}}'s assignments to check for status changes since the last run. Status transitions to watch for:
- **To Do -> In Progress**: Someone (or {{USER_NAME}}) started working on it
- **In Progress -> In Review**: Work is done, awaiting review
- **In Review -> Done**: Issue resolved
- **Any -> Cancelled/Duplicate**: Issue no longer relevant
- **Backlog -> To Do**: Issue was prioritized — new commitment

For each status change, update the KB issue tracker file and note implications for action items.

### Newly Created Issues

Check for issues created since the last run that are assigned to {{USER_NAME}} or are in projects/teams {{USER_NAME}} belongs to. New issues may represent:
- New work assignments
- Bug reports needing triage
- Feature requests needing evaluation

### Newly Assigned Issues

Check for issues recently assigned to {{USER_NAME}} that weren't assigned before. These are direct new action items.

### Comments and Updates

Check for new comments on {{USER_NAME}}'s assigned issues. Comments may contain:
- Questions needing {{USER_NAME}}'s response
- Status updates from collaborators
- Blockers or dependency changes
- Review feedback

---
phase: connector
name: linear
slot: query
mode: [briefing]
requires: linear
---

## Linear Query — Briefing Data Gathering

### All Assigned Issues

Use `list_issues` to get all issues currently assigned to {{USER_NAME}}. For each issue, note:
- **Title and identifier** (e.g., PROJ-123)
- **Current status** (Backlog, To Do, In Progress, In Review, Done)
- **Priority** (Urgent, High, Medium, Low, None)
- **Project/parent** if applicable
- **Recent updates** — any comments or status changes in the past 24 hours
- **Labels/tags** that indicate category or urgency

### New Issues Since Yesterday

Filter for issues created in the past 24 hours in {{USER_NAME}}'s teams/projects. Even issues not assigned to {{USER_NAME}} may be relevant context (e.g., a teammate's bug report that affects {{USER_NAME}}'s project).

### Priority Check

Flag any issues that are:
- **Urgent priority** and not yet In Progress
- **High priority** and stuck in the same status for more than 2 days
- **Blocked** (has a "blocked" label or comment indicating a blocker)

---
phase: connector
name: linear
slot: cross-check
mode: [consolidation, briefing]
requires: linear
---

## Linear Cross-Check

Before promoting any candidate action item to To Do, verify against Linear:

**Does a ticket already exist for this?** Search Linear issues by keyword to see if this action item is already tracked as a formal issue. If a ticket exists:
- Link the action item to the ticket (include the issue identifier)
- Use the ticket's status as the source of truth for progress
- Don't create a duplicate action item if the ticket is already being tracked

**Has it already been resolved?** Check if a related issue was recently moved to Done or Cancelled. Common pattern: a meeting generates "we need to fix X" but X was already fixed yesterday via a Linear issue that was closed.

**Is the status current?** If the action item references a known issue, verify the issue's current status in Linear matches what the KB says. If the KB says "In Progress" but Linear says "Done," the action item should be marked Done.

---
phase: connector
name: linear
slot: update
mode: [consolidation, briefing]
requires: linear
---

## Linear-Sourced KB Updates

After scanning Linear, update the knowledge base with current issue data. **Issue status staleness is the most common form of KB rot** — treat this update step as critical.

### Issue Tracker Sync

For every issue in the KB's issue tracker file:
1. **Verify the status matches Linear.** If the KB says "To Do" but Linear says "In Progress," update the KB immediately.
2. **Update priority** if it changed in Linear.
3. **Add any new comments or context** that are relevant to understanding the issue.
4. **Remove or archive issues** that are Done/Cancelled in Linear (move to a "Completed" section, don't delete — the history is useful).

### Spot-Check Requirement

Every run must spot-check at least 2-3 issue statuses against Linear as the source of truth. Pick issues that:
- Are high priority (most impactful if stale)
- Haven't been verified recently (check the "Last verified" note)
- Are referenced by current action items (most likely to cause errors if stale)

### New Issues

Add any newly discovered issues to the issue tracker file with:
- Issue identifier and title
- Status, priority, assignee
- Project/parent issue if applicable
- Link to Linear

### Project File Updates

If issue changes affect active projects, update the relevant project files:
- Changed issue statuses in the project's issues section
- New issues added to the project
- Completed milestones or resolved blockers
