---
phase: connector
name: jira
slot: inbound-scan
mode: [consolidation, briefing]
requires: jira
---

## Jira Inbound Scan — Issue Activity

Check for changes to {{USER_NAME}}'s assigned Jira issues and any newly created or assigned issues. All queries run via the local `jtk` CLI (https://github.com/open-cli-collective/atlassian-cli).

Compute the lower bound once and reuse it across all `jtk` calls:

```bash
SINCE_DAYS=1   # consolidation = 1d, briefing = 1d, weekend briefing = 3d
```

### Status Changes

Use `jtk issues search` with JQL filtered to {{USER_NAME}} (`assignee = currentUser()`) and updated since the last run:

```bash
jtk issues search \
  --jql "assignee = currentUser() AND updated >= -${SINCE_DAYS}d" \
  --fields "Key,Summary,Status,Priority,Updated" \
  --max 100
```

Status transitions to watch for:
- **To Do -> In Progress**: Someone (or {{USER_NAME}}) started working on it
- **In Progress -> In Review / Code Review**: Work is done, awaiting review
- **In Review -> Done / Resolved / Closed**: Issue resolved
- **Any -> Cancelled / Won't Do / Duplicate**: Issue no longer relevant
- **Backlog -> Selected for Development / To Do**: Issue was prioritized — new commitment

For each status change, update the KB issue tracker file and note implications for action items.

### Newly Created Issues

```bash
jtk issues search \
  --jql "(assignee = currentUser() OR reporter = currentUser()) AND created >= -${SINCE_DAYS}d" \
  --fields "Key,Summary,Status,Priority,Reporter,Assignee" \
  --max 100
```

Also scan key projects from `{{JIRA_PROJECTS}}` (configured at setup) for newly filed issues that may affect {{USER_NAME}}'s work even when not directly assigned:

```bash
jtk issues search \
  --jql "project in (${JIRA_PROJECTS}) AND created >= -${SINCE_DAYS}d" \
  --fields "Key,Summary,Status,Priority,Reporter" \
  --max 50
```

New issues may represent: new work assignments, bug reports needing triage, or feature requests needing evaluation.

### Newly Assigned Issues

```bash
jtk issues search \
  --jql "assignee = currentUser() AND assignee changed AFTER -${SINCE_DAYS}d" \
  --fields "Key,Summary,Status,Priority,Updated" \
  --max 100
```

Issues whose `assignee` changed to {{USER_NAME}} during the window are direct new action items.

### Comments and Updates

For each updated issue from the status-changes query, list new comments:

```bash
jtk comments list <ISSUE-KEY> --fulltext --max 20
```

Comments may contain:
- Questions needing {{USER_NAME}}'s response
- Status updates from collaborators
- Blockers or dependency changes
- Review feedback

### Mentions

```bash
jtk issues search \
  --jql "text ~ \"\\\"@${USER_JIRA_ACCOUNT_ID}\\\"\" AND updated >= -${SINCE_DAYS}d" \
  --fields "Key,Summary,Status,Updated" \
  --max 50
```

`@`-mentions in descriptions or comments are high-signal — someone wanted {{USER_NAME}}'s attention even if not assigned.

---
phase: connector
name: jira
slot: query
mode: [briefing]
requires: jira
---

## Jira Query — Briefing Data Gathering

### All Assigned Issues

```bash
jtk issues search \
  --jql "assignee = currentUser() AND statusCategory != Done" \
  --fields "Key,Summary,Status,Priority,Updated,Sprint,Issue Type" \
  --max 200
```

For each issue, note:
- **Key and summary** (e.g. `PROJ-123 — Fix auth race`)
- **Current status** (Backlog, To Do, In Progress, In Review, Done — exact names vary per Jira workflow)
- **Priority** (Highest, High, Medium, Low, Lowest)
- **Sprint / fix version** if applicable
- **Issue type** (Bug, Story, Task, Epic, Sub-task)
- **Recent updates** — fetch comments for issues updated in the last 24h

### New Issues Since Yesterday

```bash
jtk issues search \
  --jql "project in (${JIRA_PROJECTS}) AND created >= -1d" \
  --fields "Key,Summary,Status,Priority,Reporter" \
  --max 100
```

Even issues not assigned to {{USER_NAME}} may be relevant context (e.g. a teammate's bug report that affects {{USER_NAME}}'s project).

### Active Sprint Snapshot

If {{USER_NAME}} works in scrum-mode projects, pull the current sprint:

```bash
jtk issues list \
  --project "${JIRA_PRIMARY_PROJECT}" \
  --sprint current \
  --fields "Key,Summary,Status,Assignee,Priority" \
  --max 200
```

Look for: assignments without progress, blockers, items at risk for the sprint goal.

### Priority Check

Flag any issues that are:
- **Highest / High priority** and not yet In Progress
- **High priority** and stuck in the same status for more than 2 days (compare `Updated` to status duration)
- **Blocked** — has a `blocker`/`blocked` label, a comment containing "blocked", or an inbound `is blocked by` link (use `jtk links` if a candidate is found)

---
phase: connector
name: jira
slot: cross-check
mode: [consolidation, briefing]
requires: jira
---

## Jira Cross-Check

Before promoting any candidate action item to To Do, verify against Jira:

**Does a ticket already exist for this?** Search Jira issues by keyword to see if this action item is already tracked as a formal issue:

```bash
jtk issues search \
  --jql "text ~ \"<keywords>\" AND project in (${JIRA_PROJECTS})" \
  --fields "Key,Summary,Status,Assignee" \
  --max 20
```

If a ticket exists:
- Link the action item to the ticket (include the issue key, e.g. `PROJ-123`)
- Use the ticket's status as the source of truth for progress
- Don't create a duplicate action item if the ticket is already being tracked

**Has it already been resolved?** Check if a related issue was recently moved to Done / Resolved / Won't Do:

```bash
jtk issues search \
  --jql "text ~ \"<keywords>\" AND statusCategory = Done AND resolved >= -7d" \
  --fields "Key,Summary,Status,Resolution,Resolved" \
  --max 20
```

Common pattern: a meeting generates "we need to fix X" but X was already closed yesterday.

**Is the status current?** If the action item references a known issue key, verify the issue's current status:

```bash
jtk issues get <ISSUE-KEY> --fields "Status,Assignee,Priority,Resolution,Updated"
```

If the KB says "In Progress" but Jira says "Done," update the action item to Done and cite the issue's `Resolved` timestamp as evidence.

**Did {{USER_NAME}} already act on this ticket?** Check comments authored by {{USER_NAME}}:

```bash
jtk comments list <ISSUE-KEY> --fulltext --max 50
```

Filter the output for comments where the author matches {{USER_NAME}}. A recent comment, a transition `jtk transitions do <ISSUE-KEY> ...`, or an assignee change by {{USER_NAME}} all signal "already handled".

---
phase: connector
name: jira
slot: update
mode: [consolidation, briefing]
requires: jira
---

## Jira-Sourced KB Updates

After scanning Jira, update the knowledge base with current issue data. **Issue status staleness is the most common form of KB rot** — treat this update step as critical.

### Issue Tracker Sync

For every issue in the KB's issue tracker file:
1. **Verify the status matches Jira.** Run `jtk issues get <KEY> --fields Status,Priority,Assignee,Resolution` and reconcile.
2. **Update priority** if it changed.
3. **Add any new comments or context** that are relevant. Use `jtk comments list <KEY> --fulltext` and quote pertinent snippets.
4. **Move resolved issues** (statusCategory == Done) to a "Completed" section — don't delete; the history is useful. Cite the resolution and resolved-date.

### Spot-Check Requirement

Every run must spot-check at least 2-3 issue statuses against Jira as the source of truth. Pick issues that:
- Are Highest/High priority (most impactful if stale)
- Haven't been verified recently (check the "Last verified" note)
- Are referenced by current action items (most likely to cause errors if stale)

### New Issues

Add any newly discovered issues to the issue tracker file with:
- Issue key and summary (e.g. `PROJ-123 — Fix auth race`)
- Status, priority, assignee, reporter
- Issue type (Bug, Story, Task, Epic)
- Sprint / fix version if applicable
- Link to Jira (the URL is `${JIRA_BASE_URL}/browse/<KEY>` — base URL stored in `jtk` config; reference by key only in the KB and let the renderer resolve)

### Project File Updates

If issue changes affect active projects, update the relevant project files in `knowledge-base/projects/`:
- Changed issue statuses in the project's issues section
- New issues added to the project
- Completed milestones or resolved blockers
- Sprint progress notes if scrum is in use

### Epic / Parent Tracking

When issues belong to an epic (Jira's `Parent` or `Epic Link` field), record the epic key alongside each child issue. Group child issues under their epic in the project file so the KB shows the work's structural context, not just a flat list.
