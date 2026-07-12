# Asana Connector — Setup

Scout's Asana connector reads tasks, sections, projects, comments, and stories — and can post comments (including `@`-mentions) — through the bundled **asana-api CLI** (`scripts/asana_api.py`), a PAT-based REST wrapper. The claude.ai Asana MCP server (`mcp__claude_ai_Asana__*`) is the runtime fallback when the CLI is unavailable.

The CLI is token-efficient (returns only the fields you ask for, keeps raw JSON out of context) and works on any Asana tier without OAuth round-trips.

## 1. Generate a Personal Access Token (PAT)

1. Open https://app.asana.com/0/my-apps
2. **Create new token** → name it (e.g. `scout`) → copy the token (shown once).
3. Store it where the CLI looks for it:

   ```bash
   mkdir -p ~/.agent-skills/asana
   printf '%s' "<YOUR_PAT>" > ~/.agent-skills/asana/asana_pat
   chmod 600 ~/.agent-skills/asana/asana_pat
   ```

   Or export `ASANA_ACCESS_TOKEN=<YOUR_PAT>` in your shell profile. The CLI reads the file (or env var) internally, so the secret never appears on a command line.

The script itself is staged into the vault at `~/Scout/scripts/asana_api.py` by `/scout-setup` / `/scout-update` (source of truth: `skills/asana-api/`).

## 2. Verify

```bash
python3 ~/Scout/scripts/asana_api.py whoami
```

A successful response returns `data.gid`, `data.name`, `data.email`, and `data.workspaces[]`. That means Scout will detect the connector during `/scout-setup`.

If it errors with an auth message, the PAT is missing/expired — regenerate (step 1). If the script isn't staged yet, run the MCP `get_me` as a fallback and re-run `/scout-update` to stage the script.

## 3. Capture Your Asana GID + Workspace

`/scout-setup` pulls both directly from the `whoami` response — no manual copy needed:

- `user.asana_gid` ← `data.gid`
- `asana_workspace_gid` ← `data.workspaces[0].gid` (if you belong to more than one workspace, the wizard asks which)

To look them up later, just re-run `whoami`.

## 4. Pick the Projects Scout Should Monitor

Scout queries are scoped to a comma-separated project GID list (`asana_projects` in `scout-config.yaml`). Asana has no project keys — only numeric GIDs.

Find a GID from the project URL `https://app.asana.com/0/<PROJECT_GID>/...`, or list them:

```bash
python3 ~/Scout/scripts/asana_api.py projects --team <TEAM_GID>
# or, across the workspace:
python3 ~/Scout/scripts/asana_api.py request GET /workspaces/<WORKSPACE_GID>/projects --data 'limit=200&opt_fields=name,gid' | jq '.data'
```

Pick the projects you actually work in. Avoid org-wide noisy projects — they slow the daily sweep without adding signal.

## 5. Priority Sections / Tasks (optional but recommended)

For recurring rosters (e.g. a weekly-status section that grows as people onboard), configure `asana_priority_sections` in `scout-config.yaml` rather than hardcoding task GIDs. Each entry references a section by GID and a cadence; Scout sweeps the whole section on schedule via `section-tasks`. See the comments in `templates/scout-config.yaml.tmpl` for the schema.

## 6. Posting Comments + @mentions

Scout can reply on a task when a slot calls for it (e.g. a weekly-status nudge):

```bash
# plain text
python3 ~/Scout/scripts/asana_api.py comment-task <task_gid> --text "Status synced."

# with an @mention — Asana rich-text markup carrying the person's Asana GID
python3 ~/Scout/scripts/asana_api.py comment-task <task_gid> \
  --html-text '<body><a data-asana-gid="<PERSON_ASANA_GID>"/> please refresh before Friday.</body>'
```

Resolve a person's GID from `people.md` (`asana_gid:` field) or `users --workspace <WORKSPACE_GID>`. Scheduled runs never post without an approval gate; interactive `/scout-work` asks per item.

## 7. Premium vs Non-Premium Workspaces

The workspace task-search endpoint (`/workspaces/<gid>/tasks/search`, used for date-bounded assignee sweeps and keyword cross-checks) requires a **Premium / Business** workspace. On non-premium workspaces those calls return a 402/forbidden; the phases fall back to `project-assigned-tasks <project_gid>` (works on any tier) and filter locally. No config change needed.

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `whoami` returns auth error | PAT missing/expired/revoked | Regenerate at https://app.asana.com/0/my-apps, re-store at `~/.agent-skills/asana/asana_pat` |
| `asana_api.py: No such file` | Script not staged into the vault | Re-run `/scout-update` (stages `scripts/asana_api.py`) |
| `tasks/search` returns "premium required" | Non-premium workspace | Phases auto-fall back to `project-assigned-tasks` — no action |
| Project GIDs return empty results | Project archived/renamed | Re-list projects and refresh `asana_projects` |
| Wrong workspace's tasks appear | Multi-workspace account | Set `asana_workspace_gid` in scout-config.yaml |
| `@`-mention doesn't notify | Wrong/omitted `data-asana-gid` | Use the person's numeric Asana GID; confirm via `users --workspace <gid>` |
| Everything fails, MCP works | CLI/PAT broken | Connector falls back to `mcp__claude_ai_Asana__*` automatically; fix the PAT when convenient |

## 9. What Scout Reads / Writes

**Reads** (per scheduled run):

- `project-assigned-tasks <project_gid> --workspace <ws>` / `request … /tasks/search` for the assigned-work snapshot
- `section-tasks` + `task` + `task-comments` for priority-section sweeps
- `task` / `task-stories` for individual cross-checks
- `search-tasks --text` for keyword cross-checks
- `whoami` once at setup to capture the user GID + workspace list

**Writes:** None automatically. Scout never creates, completes, comments on, or deletes tasks during scheduled runs. Write actions (closing a task, posting a comment / `@`-mention via `comment-task`) happen only through the interactive `/scout-work` flow with explicit per-item approval. The CLI exposes the write commands (`create-task`, `update-task`, `comment-task`, …) but they are intentionally not invoked by scheduled runs.
