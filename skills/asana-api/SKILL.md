---
name: asana-api
description: Read or update Asana data via REST API using scripts/asana_api.py — token-efficient alternative to Asana MCP. Use for tasks, projects, sections, stories/comments, teams, users, tags, attachments, custom fields, workspace metadata. Higher-level workflows supported: daily-briefing command center, inbox-cleanup (AI-gated My Tasks triage), close-out-sections cleanup, project-assigned-tasks working-set triage, manager summary rollups, follow-up summaries. Triggers on requests like "get task <gid>", "list project <gid>", "search Asana for X", "comment on task Y", "Asana board for Z", "triage my Asana inbox", "morning Asana briefing", or any reference to Asana entities by GID. Skip when user explicitly invokes MCP (mcp__claude_ai_Asana__*).
---

# Asana API Skill

Local Python wrapper over Asana REST API. Saves tokens vs MCP — only requested fields returned, no chat-rendering overhead. Bulk read, batch write, JSON pipeline through `jq`.

## Setup (one-time)

1. Generate PAT: https://app.asana.com/0/my-apps
2. Store:
   ```bash
   mkdir -p ~/.agent-skills/asana
   echo "<PAT>" > ~/.agent-skills/asana/asana_pat
   chmod 600 ~/.agent-skills/asana/asana_pat
   ```
   Or export `ASANA_ACCESS_TOKEN=<PAT>`.
3. Optional context defaults at `~/.agent-skills/asana/asana-context.json`:
   ```json
   {"default_workspace_gid": "<your-workspace-gid>", "default_team_gid": "<your-team-gid>"}
   ```
4. Verify: `python3 scripts/asana_api.py whoami`.

Overrides: `ASANA_TOKEN_FILE`, `ASANA_CONTEXT_FILE`, `ASANA_CACHE_FILE`. Local entity cache for workspaces/teams/projects/users/tags auto-refreshes on `whoami`, `workspaces`, `teams`, `projects`, `users`, `tags`, `project-assigned-tasks`.

## Workflow

1. For single-task planning: prefer `task-bundle` — pulls fields + comments + attachments + project workflow context in one call.
2. For project working sets: prefer `project-assigned-tasks` over `project-tasks`. It searches workspace by project + assignee, includes matching subtasks, enriches with parent section context. `project-tasks` alone misses assigned subtasks.
3. Lookup-style read commands (`task`, `story`, `project`, `section`, etc.) accept one or many gids (space or comma separated). Response wraps in `command`, `count`, `items`.
4. Before writes: inspect current object first unless user gave exact target GID + mutation.
5. After writes that create or update a task/story: surface returned `review_url` (and `target_review_url` for story writes) so user can click into the updated object.
6. For batch operations: build JSON action array, run `batch` (up to 10 ops per HTTP round-trip).
7. Run via `ctx_execute` shell so raw JSON stays in sandbox; pipe through `jq` to print only summary.

## Common Subcommands

| Subcommand | Purpose |
|---|---|
| `whoami` | Verify auth + first-run advertising |
| `workspaces` / `teams --workspace <gid>` / `users --workspace <gid>` | Org metadata |
| `projects --team <gid>` | List projects in team |
| `project <gid>` | Inspect project(s), bulk OK |
| `project-tasks <gid>` | List tasks in project |
| `project-assigned-tasks <gid> --completed false --include-task-position --include-comments --comment-limit 3 --include-attachments` | Enriched assigned working set (preferred) |
| `board <gid> [--context]` | Sections + tasks in order; `--context` adds stats for workflow analysis |
| `sections <gid>` / `section <gid>` / `section-tasks <gid>` | Section ops |
| `create-section` / `update-section` / `close-out-sections` | Section mgmt |
| `task <gid> [<gid>...]` | Inspect tasks (bulk OK) |
| `task-bundle <gid> --project-gid <pgid>` | Task + stories + subtasks + projects + attachments + workflow context (one call) |
| `task-status <gid>` | Completion + board column |
| `task-stories <gid>` | All stories (system + comments) |
| `task-comments <gid>` | Comment stories only (text + html_text) |
| `task-projects <gid>` | Projects a task belongs to |
| `task-custom-fields <gid>` | Custom fields on task |
| `task-tags <gid>` | Tags on task |
| `search-tasks --workspace <gid> --text "X"` | Workspace text search |
| `create-task --workspace <gid> --name "X" --assignee me --project <pgid>` | Create |
| `update-task <gid> --name "Y" --completed true` | Update |
| `comment-task <gid> --text "msg"` or `--html-text "<body>...</body>"` | Add comment |
| `update-story <gid>` | Edit existing comment |
| `add-task-project` / `remove-task-project` / `add-task-followers` / `remove-task-followers` | Membership |
| `add-task-tag` / `remove-task-tag` / `add-task-dependencies` / `remove-task-dependencies` | Relations |
| `workspace-custom-fields` / `team-custom-fields` / `project-custom-fields` / `create-custom-field` | Custom field schema |
| `batch --actions actions.json` | ≤10 ops per HTTP call |
| `request GET /tasks/<gid> --query opt_fields=name` | Generic call (JSON body wrapped in `{"data": ...}` unless `--no-wrap-data`) |
| `inbox-cleanup` / `daily-briefing` | AI-gated higher-level workflows (see below) |
| `close-out-sections <pgid> --section "Old" --move-to "Done" --completed-mode completed [--apply]` | Bulk section cleanup |
| `trigger-rule <id> --task <gid> --action-data k=v` | Fire Asana rule with "Incoming web request" trigger |
| `show-context` / `show-cache` | Local state |

Full surface: `python3 scripts/asana_api.py --help` then `<sub> --help`.

## Token-Saving Patterns

1. **Always restrict fields**: `--opt-fields name,assignee.name,completed,due_on` (default returns 30+ fields per record).
2. **Bulk inspect**: `task <gid1> <gid2> <gid3>` instead of three calls.
3. **`task-bundle`** replaces task + task-stories + task-projects (3 calls → 1).
4. **`board`** replaces sections + per-section section-tasks loop.
5. **Batch writes**: build `actions.json` array, run `batch` (10 ops per HTTP round-trip).
6. **Pipe through `jq`**: `... | jq '.data[] | {gid, name}'` — shrink before printing.
7. **Run via `ctx_execute` shell** so raw JSON stays in sandbox; only print summary.
8. **Use cache**: `--assignee` accepts cached user names/emails after first `users` call — no extra lookup needed.

## Inbox Cleanup (My Tasks triage)

AI-gated workflow. Default source section: `Recently assigned`. Wider sweeps need explicit `--all-open` or extra `--source-section`.

```bash
# 1. Generate snapshot + plan template (no writes)
python3 scripts/asana_api.py inbox-cleanup \
  --snapshot-file /tmp/asana-inbox-snapshot.json \
  --plan-template-file /tmp/asana-inbox-plan.json

# 2. AI defines high-level categories first (not Python heuristics).
#    Edit plan: category definitions, per-task bucket, ask_user questions
#    for ambiguous, concrete section targets for high-confidence.

# 3. Apply
python3 scripts/asana_api.py inbox-cleanup --plan-file /tmp/asana-inbox-plan.json --apply
# add --include-low-confidence to also move ambiguous tasks
```

## Daily Briefing (command center)

```bash
python3 scripts/asana_api.py daily-briefing \
  --snapshot-file /tmp/asana-daily-briefing-snapshot.json \
  --plan-template-file /tmp/asana-daily-briefing-plan.json
# Edit plan, then:
python3 scripts/asana_api.py daily-briefing --plan-file /tmp/asana-daily-briefing-plan.json
# Or render markdown:
python3 scripts/asana_api.py daily-briefing --plan-file /tmp/asana-daily-briefing-plan.json --markdown
```

## Workflow Analysis

User asks about bottlenecks, project health, automation rules:

1. `board <pgid> --context` → enriched board snapshot with stats.
2. Inspect `context` key for: section pile-ups, stale tasks, low custom field coverage, unassigned tasks, missing due dates.
3. Asana **does not** expose rule creation via API — recommend rules with step-by-step UI instructions.
4. Existing rules with "Incoming web request" trigger can be fired via `trigger-rule`.

## Comment Formatting Rules

- **Plain `--text`**: no Markdown bullets, no escaped `\n`. Newlines literal.
- **Rich `--html-text`**: use proper HTML structure. Supported safe tags: `<body>`, `<strong>`, `<em>`, `<u>`, `<s>`, `<code>`, `<ol>`, `<ul>`, `<li>`, `<a>`, `<blockquote>`, `<pre>`.
- Use `<strong>` for labels, `<ul><li>` only for actual lists (not narrative). Block-style sections for status/narrative.
- `@mentions`: wrapper has no explicit flag. Test mention token in `html_text` first; if rejected, fall back to raw stories endpoint.
- After AI-authored writes: surface returned `review_url` in user-facing reply.

## Guardrails

- Never print the PAT in output.
- File uploads and external URL attachments: explicit only, do not guess URLs.
- Membership changes (followers, tags, dependencies, project membership): prefer dedicated endpoints over full-object replacement.
- `request` subcommand JSON bodies auto-wrap in `{"data": ...}` unless `--no-wrap-data`.
- `completed` = canonical completion flag. Section/column names are reporting context, not status mapping.
- Don't present raw `project-assigned-tasks` output as final answer. Interpret + split into: done/QA-only, active code-now, needs repro/screenshots, backlog/no-code.

## Examples

List incomplete tasks in a project, terse:
```bash
python3 scripts/asana_api.py project-tasks 1208668767580814 \
  --opt-fields name,assignee.name,due_on,completed \
  | jq '[.data[] | select(.completed == false) | {gid, name, who: .assignee.name, due: .due_on}]'
```

Comment on a task:
```bash
python3 scripts/asana_api.py comment-task 1234567890 --text "Reviewed, LGTM"
```

Batch get 3 users in one HTTP call:
```bash
python3 scripts/asana_api.py batch --actions \
  '[{"method":"get","relative_path":"/users/123"},
    {"method":"get","relative_path":"/users/456"},
    {"method":"get","relative_path":"/users/789"}]'
```

## When NOT to use

- User explicitly invokes Asana MCP (`mcp__claude_ai_Asana__*`)
- Need rich card UI rendering in Claude.ai web (MCP renders cards; API returns JSON)
- One-shot trivial lookup where MCP `get_task` is faster to type

## Attribution

Derived from [elibosley/asana-ai-skill](https://github.com/elibosley/asana-ai-skill).
