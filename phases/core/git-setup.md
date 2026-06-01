---
phase: core
name: git-setup
slot: setup
mode: [briefing, consolidation, dreaming, research]
requires: null
---

## Step 0: Git Setup

Silently ensure git is ready. Run every time without commentary:

```bash
cd {{SCOUT_DIR}}
git config user.email "{{USER_EMAIL}}"
git config user.name "{{INSTANCE_NAME}} Bot"
```

Do not mention git setup in your output unless something goes wrong.

## Using Git History

Git is a core part of the {{INSTANCE_NAME}} system — every change is committed with descriptive messages. Use git history actively during runs:

- `git log --oneline -20` — see what recent runs did (avoid duplicate work)
- `git diff HEAD~1` — inspect what the last run changed
- `git log --since="8 hours ago" --oneline` — understand what happened today
- `git show <commit>` — inspect a specific change when you need provenance

The commit history is as valuable as the files themselves. When verifying KB claims or understanding what was already audited, check git before querying connectors.

### Vault Git-Delta Narration (consolidation/briefing — mandatory before composing)

Interactive sessions ({{INSTANCE_NAME}} work sessions, manual edits, `/scout-work`, audits) commit to the vault between scheduled runs. Those commits are a **primary signal source**, not background noise — a `work [HH:MM]:` commit may already have done the thing an action item is about. Before composing the run summary:

```bash
git -C {{SCOUT_DIR}} log --since="<last run timestamp>" --pretty=format:'%h %s' --no-merges
```

Narrate any non-session commits (anything not authored by this run) in the summary. **Do not assert a "quiet window" / "nothing changed since last run" until this git scan returns zero non-session commits.** A silent delta in the vault contradicts a "quiet" claim and erodes trust.

## GitHub Access via `gh` CLI

All GitHub operations use the `gh` CLI, which is authenticated and has full access to both public and private repos. **Do not use GitHub MCP tools** — they have limited scope.

## Timezone Handling

The sandbox/server clock may run in UTC. You **must** use the configured timezone for all time checks and timestamp generation throughout the run. Never use bare `date` — always prefix with `TZ={{TIMEZONE}}` (or the timezone specified in your config).

## Session Cost Reporting (Final Action)

As the very last action before exit, log this session's cost:

```bash
{{SCOUT_DIR}}/scripts/write-session-cost.sh "$SESSION_TYPE" "${CLAUDE_MAX_BUDGET_USD:-0}" "${CLAUDE_BUDGET_SPENT_USD:-0}" 0
```

This feeds the budget-check system. If budget environment variables aren't available, pass `0` — the tracker still logs the timestamp and type.
