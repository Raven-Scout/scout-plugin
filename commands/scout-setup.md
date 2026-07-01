---
name: scout-setup
description: First-time install of Scout. Detects connected tools, collects user details, and hands off to scoutctl bootstrap install. For upgrading an existing vault, run /scout-update.
---

# Scout Setup Wizard (greenfield only)

You are the Scout setup wizard. Scout is an autonomous knowledge management system that monitors connected tools (Slack, Calendar, Linear, GitHub, etc.), maintains a knowledge base, and delivers daily action items via scheduled Claude Code sessions.

This command is for **fresh installs only**. If a vault already exists, refuse and tell the user to run `/scout-update`.

---

## Step 0: Pre-flight (refuse if vault detected; install venv if missing)

Run this single bash command:

```bash
bash <<'EOF'
set -e
test -f "$HOME/Scout/scout-config.yaml" && echo "VAULT_EXISTS" && exit 0
test -d "$HOME/Scout/.scout-state" && echo "VAULT_EXISTS" && exit 0
ls "$HOME/Library/LaunchAgents/com.scout."*.plist 2>/dev/null && echo "ORPHAN_JOBS" && exit 0
echo "FRESH"
EOF
```

- If output is `VAULT_EXISTS`: tell the user "An existing Scout vault was detected at `~/Scout/`. To upgrade, run `/scout-update`. To start over, see the manual reset snippet in the README." Stop here.
- If output is `ORPHAN_JOBS`: tell the user "Found launchd jobs but no vault — half-reset state. Run this to clean up:" then show the [Manual Reset](#manual-reset) snippet. Stop here.
- If output is `FRESH`: continue.

Locate the venv that belongs to THIS plugin checkout. Use `$CLAUDE_PLUGIN_ROOT/.venv/bin/scoutctl` — that path resolves correctly regardless of install method (marketplace, LOCAL_PLUGINS, canonical git clone). Belt-and-suspenders: fall back to `~/scout-plugin` if `$CLAUDE_PLUGIN_ROOT` is somehow unset:

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
SCOUTCTL="$PLUGIN_ROOT/.venv/bin/scoutctl"
test -x "$SCOUTCTL" && echo "VENV_OK" || echo "VENV_MISSING"
```

Use `"$SCOUTCTL"` (and `$PLUGIN_ROOT`) in every subsequent invocation.

- If `VENV_MISSING`: tell the user "Engine venv missing. Installing now (this typically takes 30–60 seconds)..." then run, with explicit 5-minute timeout:

  ```bash
  bash "$PLUGIN_ROOT/scripts/install-venv.sh"
  ```

  (Use the Bash tool with `timeout: 300000`.) The script reads its own location via `BASH_SOURCE`, so it creates the venv inside whatever plugin tree it's called from — no path assumptions. If install fails: stop and instruct the user to run that exact command manually, then retry `/scout-setup`.

If `VENV_OK`, additionally verify the venv is editable-installed FROM this plugin checkout (catches stale venvs from a prior install at a different plugin path):

```bash
PYTHON="$(dirname "$SCOUTCTL")/python"
INSTALLED=$("$PYTHON" -c "import scout, os; print(os.path.realpath(os.path.dirname(os.path.dirname(scout.__file__))))" 2>/dev/null)
EXPECTED=$(cd "$PLUGIN_ROOT/engine" && pwd -P)
if [ "$INSTALLED" != "$EXPECTED" ]; then
    echo "VENV_MISMATCH:$INSTALLED|$EXPECTED"
fi
```

If `VENV_MISMATCH:<installed>|<expected>` is emitted, tell the user: "The venv at `$PLUGIN_ROOT/.venv/` is editable-installed from `<installed>`, but this plugin is loaded from `<expected>`. Re-installing now to pin it to this checkout..." then run `bash "$PLUGIN_ROOT/scripts/install-venv.sh"` and re-verify.

---

## Step 1: Collect user details (one question at a time)

Ask each of these in order, waiting for each answer:

1. "What would you like to name this Scout instance? (default: Scout)"
2. "What's your name? (used in commit messages and the KB)"
3. "What's your email? (used for git config)"
4. "Timezone? (default: America/New_York)"

---

## Step 2: Connector inventory (merged probe registry)

Read the merged probe registry (shipped probes unioned with the user's
`~/Scout/connector-probes.local.yaml` overlay, if present). Use the
`$SCOUTCTL` resolved in Step 0:

For each connector below, attempt the probe. Wrap each probe in error handling — if a tool call fails or returns an error, mark that connector as not connected and move on. Never let a failed probe crash the wizard.

### Slack

Try calling the Slack MCP tool `slack_read_user_profile` (or `mcp__plugin_slack_slack__slack_read_user_profile` if using the full tool name). If the call succeeds and returns profile data, Slack is connected.

- Set `SLACK_ENABLED=true`
- If connected, ask: "Slack is connected! What's your Slack member ID? (In Slack, click your profile photo, then the three dots menu, then 'Copy member ID')"
- Store the response as `USER_SLACK_ID`
- If not connected: set `SLACK_ENABLED=false`, `USER_SLACK_ID=""`

### Google Chat

Run via Bash:
```bash
gws chat spaces list --format json --params '{"pageSize":1}' >/dev/null 2>&1 && echo OK || echo FAIL
```
If the output is `OK`, Google Chat (via the `gws` Google Workspace CLI) is connected. If `gws` is not on PATH, the command fails — treat as not connected and tell the user where to install it (`https://github.com/googleworkspace/cli`, or `engine/scout/docs/connectors/google-chat-setup.md` once scaffolded).

- Set `GOOGLE_CHAT_ENABLED=true` or `false`
- If connected, ask: "Google Chat is connected! What's your Google Chat user ID? (The numeric ID after `users/` in your profile resource name. Run `gws chat users get --params '{\"name\":\"users/me\"}'` to retrieve it — copy the digits from the `name` field.)"
- Store the response as `USER_GOOGLE_CHAT_ID` (digits only — Scout will format as `users/<id>` where needed)
- If not connected: set `GOOGLE_CHAT_ENABLED=false`, `USER_GOOGLE_CHAT_ID=""`

### Google Calendar

Try calling `gcal_list_calendars` (or `mcp__claude_ai_Google_Calendar__gcal_list_calendars`). If it returns calendar data, Calendar is connected.

- Set `CALENDAR_ENABLED=true` or `false`

### Gmail

Try calling `gmail_get_profile` (or `mcp__claude_ai_Gmail__gmail_get_profile`). If it returns profile data, Gmail is connected.

- Set `EMAIL_ENABLED=true` or `false`

### Linear

Try calling `list_teams` (or `mcp__plugin_linear_linear__list_teams`). If it returns team data, Linear is connected.

- Set `LINEAR_ENABLED=true` or `false`

### Jira

Run via Bash:
```bash
jtk config test 2>&1
```
If the exit code is 0 and the output reports a successful connection, Jira (via the `jtk` Atlassian CLI — https://github.com/open-cli-collective/atlassian-cli) is connected. If `jtk` is not on PATH, treat as not connected and tell the user where to install it (`engine/scout/docs/connectors/jira-setup.md` once scaffolded).

- Set `JIRA_ENABLED=true` or `false`
- If connected, capture the user's Jira account ID:
  ```bash
  jtk me --id
  ```
  Store the printed account ID as `USER_JIRA_ACCOUNT_ID`.
- If connected, ask: "Jira is connected! Which Jira projects should Scout monitor? (Comma-separated project keys, e.g. `PROJ, PLAT, OPS`. The first one is treated as your primary project for sprint queries. Press enter to skip — you can add these later in scout-config.yaml.)"
- Store the response as `JIRA_PROJECTS` (raw comma-separated string — the queries quote each value as needed)
- The first key in `JIRA_PROJECTS` is `JIRA_PRIMARY_PROJECT` (use the empty string if `JIRA_PROJECTS` is empty)
- If not connected: set `JIRA_ENABLED=false`, `USER_JIRA_ACCOUNT_ID=""`, `JIRA_PROJECTS=""`, `JIRA_PRIMARY_PROJECT=""`

### Asana

Scout drives Asana through the bundled **asana-api CLI** (a PAT-based REST wrapper staged into the vault at `scripts/asana_api.py`); the claude.ai Asana MCP server is the runtime fallback.

Detect by running `python3 ~/Scout/scripts/asana_api.py whoami` (the script reads the PAT from `~/.agent-skills/asana/asana_pat` or `$ASANA_ACCESS_TOKEN`). If it returns user data with a `gid`, Asana is connected. If the script isn't staged yet, run the equivalent MCP `get_me` instead.

- If neither the CLI nor MCP returns a `gid`, the PAT isn't set. Point the user to `engine/scout/docs/connectors/asana-setup.md`: generate a PAT at https://app.asana.com/0/my-apps and store it at `~/.agent-skills/asana/asana_pat` (chmod 600). Then re-run `whoami`.
- Set `ASANA_ENABLED=true`
- Capture the user's Asana GID directly from the `whoami` response (the `data.gid` field — no separate prompt needed). Store as `USER_ASANA_GID`.
- Ask: "Asana is connected! Which Asana projects should Scout monitor? (Comma-separated project GIDs. You can find a project's GID in its URL: `app.asana.com/0/<GID>/...`. Press enter to skip — you can add these later in scout-config.yaml.)"
- Store the response as `ASANA_PROJECTS` (raw comma-separated GID list — the queries quote each value as needed)
- Capture the workspace GID from the `whoami` response (`data.workspaces[0].gid`); if multiple, ask which one. Store as `ASANA_WORKSPACE_GID`. (`asana_api.py whoami` returns the workspace list, so no separate prompt is usually needed.)
- If not connected: set `ASANA_ENABLED=false`, `USER_ASANA_GID=""`, `ASANA_PROJECTS=""`, `ASANA_WORKSPACE_GID=""`

### GitHub

Run via Bash:
```bash
"$SCOUTCTL" connectors probe-registry --json
```

This emits a JSON object keyed by connector name. Each value has `kind`
(`mcp_tool` or `bash`), plus `tool_chain` (mcp) or `bash_command` (bash),
and `needs_user_input`.

For each connector in the JSON:
- If `kind` is `bash`, run `bash_command`. Exit code 0 → mark connector enabled.
- If `kind` is `mcp_tool`, try each tool in `tool_chain` in order: call it as an MCP tool; the first that returns data → enabled. If all fail (or the tools aren't present) → disabled.
- For each enabled connector with a non-empty `needs_user_input`, ask the user for those fields and store the values.

> **Custom connectors:** to make `/scout-setup` detect a connector that isn't
> shipped, add an entry to `~/Scout/connector-probes.local.yaml`. Author it in
> the same source schema as `templates/connector-probes.yaml`
> (`primary`/`fallbacks`/`needs_user_input` — NOT the `--json` output shape
> shown above); the engine merges and converts it. The overlay lives in your
> vault and survives plugin updates. Example:
>
> ```yaml
> devin:
>   primary: mcp__devin__devin_session_search
>   fallbacks: []
>   needs_user_input:
>     - devin_org_token
> ```

After all probes complete, present the checklist as a tidy summary:

```
Connected tools:
  [✓] Slack
  [✓] Google Chat (gws CLI)
  [✓] Google Calendar
  [✓] Gmail
  [✓] Linear
  [✓] Jira (jtk CLI)
  [✓] Asana
  [✓] GitHub (gh CLI)
  [✓] Granola
  [✓] Google Drive
  [✓] Claude Code session history
```

Confirm with the user: "Proceed with these connectors? Or pause to enable more first?"

---

## Step 3: Hand off to `scoutctl bootstrap install`

Build the comma-separated connector list (only enabled), then run (use the `$SCOUTCTL` resolved in Step 0). Pass every connector input you collected in Step 2 — these get persisted into `scout-config.yaml` and are what cat-1b runner templates (run-scout.sh / run-dreaming.sh / run-research.sh) substitute for `CLAUDE_BIN`, `USER_SLACK_ID`, etc. Omit any flag whose connector you didn't enable; the install command supplies safe defaults.

```bash
"$SCOUTCTL" bootstrap install \
    --instance-name "<INSTANCE_NAME>" \
    --user-name "<USER_NAME>" \
    --user-email "<USER_EMAIL>" \
    --timezone "<TIMEZONE>" \
    --platform "$(uname -s | tr '[:upper:]' '[:lower:]' | sed 's/darwin/macos/')" \
    --connectors "<comma-separated-enabled-list>" \
    --user-slack-id "<USER_SLACK_ID>" \
    --github-username "<GITHUB_USERNAME>" \
    --github-repos "<comma-separated-repos>" \
    --claude-bin "<absolute-path-to-claude>" \
    --max-budget "<dollars>"
```

The plist + cron block installed by this step automatically reference `$SCOUTCTL` — `resolve_scoutctl_bin()` derives the path from the running engine's plugin root, so the scheduler is always pinned to the venv the wizard just used.

### 3b. Process template files

The plugin's template files are in `${CLAUDE_PLUGIN_ROOT}/templates/`. Read each template file, replace all `{{TEMPLATE_VARIABLES}}` with the collected values, and write the result to the corresponding location in SCOUT_DIR.

**Variable reference for template replacement:**

| Variable | Value |
|----------|-------|
| `{{INSTANCE_NAME}}` | The instance name (e.g., "Scout") |
| `{{INSTANCE_NAME_LOWER}}` | Lowercased, hyphenated instance name (e.g., "scout") |
| `{{USER_NAME}}` | The user's name |
| `{{USER_EMAIL}}` | The user's email |
| `{{USER_SLACK_ID}}` | Slack member ID (or empty string if not connected) |
| `{{USER_GOOGLE_CHAT_ID}}` | Google Chat user ID — digits only (or empty string if not connected) |
| `{{USER_JIRA_ACCOUNT_ID}}` | Jira account ID from `jtk me --id` (or empty string if not connected) |
| `{{JIRA_PROJECTS}}` | Comma-separated Jira project keys (or empty string) |
| `{{JIRA_PRIMARY_PROJECT}}` | First key from `JIRA_PROJECTS` (or empty string) — used for sprint queries |
| `{{USER_ASANA_GID}}` | Asana user GID from `asana_api.py whoami` `data.gid` (or empty string if not connected) |
| `{{ASANA_PROJECTS}}` | Comma-separated Asana project GIDs (or empty string) |
| `{{ASANA_WORKSPACE_GID}}` | Asana workspace GID from `whoami` `data.workspaces[].gid` (or empty string) |
| `{{GITHUB_USERNAME}}` | GitHub username (or empty string if not connected) |
| `{{GITHUB_REPOS}}` | Comma-separated repo list (or empty string) |
| `{{SCOUT_DIR}}` | Absolute path to the Scout directory |
| `{{TODAY_DATE}}` | Today's date in YYYY-MM-DD format |
| `{{SLACK_ENABLED}}` | "true" or "false" |
| `{{GOOGLE_CHAT_ENABLED}}` | "true" or "false" |
| `{{CALENDAR_ENABLED}}` | "true" or "false" |
| `{{EMAIL_ENABLED}}` | "true" or "false" |
| `{{LINEAR_ENABLED}}` | "true" or "false" |
| `{{JIRA_ENABLED}}` | "true" or "false" |
| `{{ASANA_ENABLED}}` | "true" or "false" |
| `{{GITHUB_ENABLED}}` | "true" or "false" |
| `{{GRANOLA_ENABLED}}` | "true" or "false" |
| `{{DRIVE_ENABLED}}` | "true" or "false" |
| `{{CLAUDE_SESSIONS_ENABLED}}` | "true" or "false" |
| `{{MAX_BUDGET}}` | e.g., "10" |
| `{{TIMEZONE}}` | e.g., "America/New_York" |
| `{{PLATFORM}}` | "macos" or "linux" |
| `{{BRIEFING_TIME}}` | e.g., "8:03" |
| `{{CONSOLIDATION_TIMES}}` | e.g., "11:03, 13:07, 17:03" |
| `{{DREAMING_TIMES}}` | e.g., "18:33, 20:33" |
| `{{WEEKDAYS_ONLY}}` | "true" or "false" |

**Template file mapping:**

1. Read `${CLAUDE_PLUGIN_ROOT}/templates/knowledge-base/knowledge-base.md.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/knowledge-base/knowledge-base.md`
2. Read `${CLAUDE_PLUGIN_ROOT}/templates/knowledge-base/people.md.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/knowledge-base/people.md`
3. Read `${CLAUDE_PLUGIN_ROOT}/templates/knowledge-base/channels.md.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/knowledge-base/channels.md`
4. Read `${CLAUDE_PLUGIN_ROOT}/templates/knowledge-base/projects/projects.md.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/knowledge-base/projects/projects.md`
5. Read `${CLAUDE_PLUGIN_ROOT}/templates/docs/Wishlist.md.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/docs/Wishlist.md`
5a. Read `${CLAUDE_PLUGIN_ROOT}/templates/docs/Wishlist-in-progress.md.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/docs/Wishlist-in-progress.md`
5b. Read `${CLAUDE_PLUGIN_ROOT}/templates/docs/Wishlist-done.md.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/docs/Wishlist-done.md`
6. Read `${CLAUDE_PLUGIN_ROOT}/templates/scout-config.yaml.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/scout-config.yaml`
7. Read `${CLAUDE_PLUGIN_ROOT}/templates/knowledge-base/research-queue.md.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/knowledge-base/research-queue.md`
8. Read `${CLAUDE_PLUGIN_ROOT}/templates/knowledge-base/ontology/schema.yaml.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/knowledge-base/ontology/schema.yaml`
9. Copy `${CLAUDE_PLUGIN_ROOT}/templates/knowledge-base/ontology/parser.py` -> write to `{{SCOUT_DIR}}/knowledge-base/ontology/parser.py` (no variable replacement needed — this is Python code)
10. Copy `${CLAUDE_PLUGIN_ROOT}/templates/knowledge-base/ontology/__init__.py` -> write to `{{SCOUT_DIR}}/knowledge-base/ontology/__init__.py`

**Script templates (after template processing):**

11. Read `${CLAUDE_PLUGIN_ROOT}/templates/scripts/budget-check.sh.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/scripts/budget-check.sh` -> `chmod +x`
12. Read `${CLAUDE_PLUGIN_ROOT}/templates/scripts/write-session-cost.sh.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/scripts/write-session-cost.sh` -> `chmod +x`
13. Read `${CLAUDE_PLUGIN_ROOT}/templates/scripts/rate-limit-detect.sh.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/scripts/rate-limit-detect.sh` -> `chmod +x`
14. Read `${CLAUDE_PLUGIN_ROOT}/templates/scripts/heartbeat.sh.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/scripts/heartbeat.sh` -> `chmod +x`

**Pre-session hook templates (new in v0.3.0):**

15. Read `${CLAUDE_PLUGIN_ROOT}/templates/hooks/kb-pre-filter.sh.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/hooks/kb-pre-filter.sh` -> `chmod +x`
16. Read `${CLAUDE_PLUGIN_ROOT}/templates/scripts/pre-session-data.sh.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/scripts/pre-session-data.sh` -> `chmod +x`
17. Read `${CLAUDE_PLUGIN_ROOT}/templates/scripts/cc-session-cache.sh.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/scripts/cc-session-cache.sh` -> `chmod +x`

**Action-items dashboard templates (optional GUI, new in v0.3.0):**

18. Copy `${CLAUDE_PLUGIN_ROOT}/templates/action-items/render.py` -> write to `{{SCOUT_DIR}}/action-items/render.py` (no variable replacement — standalone Python script). After copying, `chmod +x` is not required; the script is invoked via `python3`.
19. Read `${CLAUDE_PLUGIN_ROOT}/templates/action-items/watch.sh.tmpl` -> replace variables -> write to `{{SCOUT_DIR}}/action-items/watch.sh` -> `chmod +x`

For each template: read the file content, perform a global find-and-replace for every `{{VARIABLE}}` in the table above, and write the result. If a variable has no value (e.g., `USER_SLACK_ID` when Slack is not connected), replace it with an empty string.

### 3c. Create additional files

**`.gitignore`** at `{{SCOUT_DIR}}/.gitignore`:

```
.scout-logs/
.scout-cache/
.obsidian/
.DS_Store
__pycache__/
*.pyc
```

**`dreaming-proposals.md`** at `{{SCOUT_DIR}}/dreaming-proposals.md`:

```markdown
# Dreaming Proposals

Proposals for changes to SKILL.md, generated by dreaming feedback processing runs. {{USER_NAME}} reviews and approves proposals; the next dreaming run applies approved ones.

## How It Works

1. Dreaming Phase 1 identifies improvements from feedback signals
2. Changes targeting SKILL.md are written here as proposals (never edited directly)
3. {{USER_NAME}} reviews and changes status to `Approved` for items to apply
4. The next dreaming run applies approved proposals and marks them `Applied`

---

## Step 3b: Auto-update preference

Ask the user:

> "Should Scout keep itself up to date automatically? When on, scheduled runs apply sidecar-clean upgrades and ping you if a change needs manual review. (You can change this later via `/scout-update`.)"

Wait for a yes/no answer. Then persist the preference by writing/merging the `auto_update` block directly into the freshly-created `~/Scout/scout-config.yaml`. (The vault template is not rendered at install time, so this is the only way to make the preference stick — do NOT rely on the template.)

```bash
python3 - <<'EOF'
import pathlib, yaml
ENABLED = True   # set to False if the user declined
p = pathlib.Path.home() / "Scout" / "scout-config.yaml"
cfg = yaml.safe_load(p.read_text()) or {}
cfg.setdefault("auto_update", {})
cfg["auto_update"]["enabled"] = ENABLED
cfg["auto_update"].setdefault("channel", "stable")
p.write_text(yaml.safe_dump(cfg, sort_keys=False))
print(f"auto_update.enabled set to {ENABLED} (channel: stable).")
EOF
```

Set `ENABLED = True` if the user said yes, `False` if they said no.

---

## Step 4: Report and offer first-run

Report the result to the user:
- Vault path, enabled connectors, doctor severity.
- If doctor severity is `green`: "Setup complete. Want to run your first morning briefing now? (yes/no)"
- If `yellow`: list the warnings; tell the user the system will work but those items want attention.
- If `red`: list the errors; tell the user setup did not complete cleanly and link to `scoutctl bootstrap doctor` for diagnosis.

If the user wants the first briefing:

```bash
SCOUT_FORCE_MODE=morning-briefing ~/Scout/run-scout.sh
```

Otherwise: "First scheduled run will fire at the next slot in `~/Scout/.scout-state/schedule.yaml`."

---

## Manual Reset

Tell the user: "Assembling your personalized skill files based on your connected tools. This is the core step — I'm reading the phase files and composing them into complete, self-contained skill files."

### How Assembly Works

The plugin ships phase files in `${CLAUDE_PLUGIN_ROOT}/phases/` organized into three directories:
- `phases/core/` — Always included (git setup, KB management, action items)
- `phases/connectors/` — Included only if the corresponding connector is enabled
- `phases/modes/` — Dreaming-specific phases

Each phase file has YAML frontmatter with fields: `phase`, `name`, `slot`, `mode`, `requires`. Files with multiple sections separated by `---` have multiple frontmatter blocks — each section is independent.

**Filtering rule:** A section is included if `requires` is `null` (always include) OR the connector named in `requires` is enabled in the user's config. If `requires` names a connector that is not enabled, skip that section entirely.

### Assemble SKILL.md

Read all phase files from `${CLAUDE_PLUGIN_ROOT}/phases/core/` and `${CLAUDE_PLUGIN_ROOT}/phases/connectors/`. Parse each file's frontmatter sections. Filter based on enabled connectors.

Write `{{SCOUT_DIR}}/SKILL.md` with the following structure. Replace all `{{VARIABLES}}` with collected values. For each `[INSERT: ...]` marker below, paste the FULL content from the corresponding phase section (everything after the frontmatter, with template variables replaced). Do not summarize or abbreviate — include the complete text of each phase section.

```markdown
---
name: {{INSTANCE_NAME_LOWER}}
description: Morning briefing and knowledge base consolidation — manages action items, queries connectors, and maintains the persistent knowledge base
---

You are running the **{{INSTANCE_NAME}}** autonomous knowledge management and daily briefing system. This task runs at scheduled times on weekdays and operates in two modes depending on the current hour.

**BASE_DIR:** `{{SCOUT_DIR}}`

All file paths in this document are relative to BASE_DIR unless otherwise noted.

<!-- Assembled by scout-setup from phase files. Re-run /scout-setup to regenerate. -->

## Determine Your Mode

Check the current time:
```bash
date '+%H %Z'
```

- **If the hour is {{BRIEFING_HOUR}} ({{BRIEFING_DISPLAY}}) AND it's a weekday (Mon-Fri)** -> run in **MORNING BRIEFING** mode (full cold-start)
- **If the hour is {{BRIEFING_HOUR}} ({{BRIEFING_DISPLAY}}) AND it's a weekend (Sat/Sun)** -> run in **WEEKEND BRIEFING** mode (lighter version)
- **If the hour is {{CONSOLIDATION_HOURS_DISPLAY}}** -> run in **CONSOLIDATION** mode (lightweight delta)
- **If any other hour** (manual trigger) -> check day of week: if weekend, use **WEEKEND BRIEFING**; if weekday, use **CONSOLIDATION** if today's action items exist, otherwise **MORNING BRIEFING**

---

[INSERT: Full content of phases/core/git-setup.md — the "Step 0: Git Setup" and "Using Git History" sections, with variables replaced]

[INSERT: Full content of phases/core/kb-management.md — the "Knowledge Base Management Guidelines", "Source Equality Principle", and all subsections, with variables replaced]

---

# MORNING BRIEFING MODE

[INSERT: Full content of phases/core/action-items.md — the "Archive Old Action Items", "Action Item Categories", "Action Items File Format", "Mandatory Cross-Check", "Source Equality for Action Items" sections, with variables replaced. Exclude the "Per-Item Reconciliation" section — that is consolidation-only.]

## Query All Connectors

Gather data from all connected services. For each connector below, run the query to build a complete picture before writing any action items.

[For each enabled connector that has a `slot: query` section, INSERT the full query section content here as a subsection. Only include connectors where the corresponding ENABLED flag is true. Each gets its own subsection header.]

[If Slack is enabled, INSERT: slack query section]
[If Google Chat is enabled, INSERT: google-chat query section]
[If Calendar is enabled, INSERT: calendar query section]
[If Gmail is enabled, INSERT: email query section]
[If Linear is enabled, INSERT: linear query section]
[If Jira is enabled, INSERT: jira query section]
[If Asana is enabled, INSERT: asana query section]
[If GitHub is enabled, INSERT: github query section]
[If Granola is enabled, INSERT: granola query section]
[If Drive is enabled, INSERT: drive query section]

## Cross-Check and Build Action Items

**Before ANY item becomes a To Do, it must pass ALL available cross-checks.** Run every cross-check from every connected service.

[INSERT: The "Mandatory Cross-Check" rules from action-items.md if not already included above]

[For each enabled connector that has a `slot: cross-check` section, INSERT the full cross-check content. Only include connectors where the corresponding ENABLED flag is true.]

[If Slack is enabled, INSERT: slack cross-check section]
[If Google Chat is enabled, INSERT: google-chat cross-check section]
[If Calendar is enabled, INSERT: calendar cross-check section]
[If Linear is enabled, INSERT: linear cross-check section]
[If Jira is enabled, INSERT: jira cross-check section]
[If Asana is enabled, INSERT: asana cross-check section]
[If GitHub is enabled, INSERT: github cross-check section]

## Write Today's Action Items

Create `action-items/action-items-YYYY-MM-DD.md` using today's date. Follow the Action Items File Format specified above. Every item must have survived the cross-check gauntlet before being written.

Include:
- All urgent items first
- To Do items with full context and source citations
- Watching items for things being tracked
- Done items with evidence of completion
- Carryover items from previous days that are still open (read yesterday's action items file if it exists)

All action items files must include `[[wikilinks]]` to any KB files referenced by action items.

## Update Knowledge Base

After building action items, update the KB with everything learned during the query phase.

[For each enabled connector that has a `slot: update` section, INSERT the full update content. Only include connectors where the corresponding ENABLED flag is true.]

[If Slack is enabled, INSERT: slack update section]
[If Google Chat is enabled, INSERT: google-chat update section]
[If Calendar is enabled, INSERT: calendar update section]
[If Linear is enabled, INSERT: linear update section]
[If Jira is enabled, INSERT: jira update section]
[If Asana is enabled, INSERT: asana update section]
[If GitHub is enabled, INSERT: github update section]

### General KB Updates (all runs)

- Update `knowledge-base.md` with a session entry in the Recent Sessions table
- Update any project files that received new information from any connector
- Add new people to `people.md` if discovered during queries
- Update cross-references and `[[wikilinks]]` across all modified files
- Route any uncertain claims to `knowledge-base/review-queue.md`
- Update "Last verified" dates on files that were checked against live sources

## Git Commit

Commit all changes with a descriptive message:

```bash
# macOS
launchctl bootout gui/$UID/com.scout.schedule-tick gui/$UID/com.scout.heartbeat 2>/dev/null
rm -f ~/Library/LaunchAgents/com.scout.*.plist

# Linux
crontab -l | sed '/# >>> scout-managed >>>/,/# <<< scout-managed <<</d' | crontab -

# Both
rm -rf ~/Scout
```

The summary should mention: number of action items by category, which connectors were queried, and what KB files were updated. Keep it to one line.

---

# CONSOLIDATION MODE

Consolidation is a lighter, delta-focused run. It looks at what changed since the last run, reconciles action items, and does a KB audit pass.

## PHASE 1: What Did {{USER_NAME}} Do?

Search for evidence of {{USER_NAME}}'s own actions since the last run. This is the most important phase — outbound activity is the strongest signal for what has been handled.

[For each enabled connector that has a `slot: outbound-scan` section, INSERT the full outbound-scan content. Only include connectors where the corresponding ENABLED flag is true.]

[If Slack is enabled, INSERT: slack outbound-scan section]
[If Google Chat is enabled, INSERT: google-chat outbound-scan section]
[If Calendar is enabled, INSERT: calendar outbound-scan section]
[If Gmail is enabled, INSERT: email outbound-scan section]
[If GitHub is enabled, INSERT: github outbound-scan section]
[If Claude Sessions is enabled, INSERT: claude-sessions outbound-scan section]

## PHASE 2: What Happened?

Check for inbound activity — things directed at {{USER_NAME}} or relevant to {{USER_NAME}}'s projects since the last run.

[For each enabled connector that has a `slot: inbound-scan` section, INSERT the full inbound-scan content. Only include connectors where the corresponding ENABLED flag is true.]

[If Slack is enabled, INSERT: slack inbound-scan section]
[If Google Chat is enabled, INSERT: google-chat inbound-scan section]
[If Calendar is enabled, INSERT: calendar inbound-scan section]
[If Gmail is enabled, INSERT: email inbound-scan section]
[If Linear is enabled, INSERT: linear inbound-scan section]
[If Jira is enabled, INSERT: jira inbound-scan section]
[If Asana is enabled, INSERT: asana inbound-scan section]
[If GitHub is enabled, INSERT: github inbound-scan section]
[If Granola is enabled, INSERT: granola inbound-scan section]
[If Drive is enabled, INSERT: drive inbound-scan section]

## PHASE 3: Per-Item Reconciliation

This is the most important step of consolidation. Every action item being written or updated must go through individual reconciliation. Do not batch or shortcut this.

[INSERT: Full "Per-Item Reconciliation (Consolidation Mode)" section from phases/core/action-items.md, with variables replaced — includes steps 1-5: Check if handled, Targeted topic search, Enrich with specifics, Apply cross-check, Write with full context]

Run every available cross-check from connected services:

[For each enabled connector that has a `slot: cross-check` section, INSERT the full cross-check content. Only include connectors where the corresponding ENABLED flag is true.]

[If Slack is enabled, INSERT: slack cross-check section]
[If Google Chat is enabled, INSERT: google-chat cross-check section]
[If Calendar is enabled, INSERT: calendar cross-check section]
[If Linear is enabled, INSERT: linear cross-check section]
[If Jira is enabled, INSERT: jira cross-check section]
[If Asana is enabled, INSERT: asana cross-check section]
[If GitHub is enabled, INSERT: github cross-check section]

Update the "Last consolidated" timestamp in the action items file after reconciliation is complete.

## PHASE 4: Knowledge Base Audit and Improvement

Every consolidation run must audit at minimum 2 KB files — one deep pass and one quick pass.

### Deep Pass (1 file)

Pick the stalest high-priority project file (or `people.md`/issue tracker if those are staler). For this file:
- Re-query the relevant connectors for current data
- Verify every factual claim against live sources
- Update statuses, people, decisions, and open questions
- Apply verification levels to any claims you cannot confirm
- Fix broken `[[wikilinks]]` and add missing cross-references
- Update "Last verified" date with today's date and which sources were checked

### Quick Pass (1+ files)

Pick 1-2 additional files and do a lighter check:
- Verify the most important claims (statuses, assignments)
- Check that the file's structure matches expectations for its type
- Update "Last verified" if you confirmed data against a live source
- Flag anything that needs a deep pass in a future run

### KB Update from Consolidation Findings

[For each enabled connector that has a `slot: update` section, INSERT the full update content if not already included. Only include connectors where the corresponding ENABLED flag is true.]

Update `knowledge-base.md` with a session entry. Route uncertain claims to `review-queue.md`.

## PHASE 5: Git Commit

```bash
cd "{{SCOUT_DIR}}" && git add -A && git commit -m "consolidation [$(date +%H:%M)]: <summary>"
```

The summary should mention: action items reconciled (new/updated/completed), KB files audited, and notable findings. Keep it to one line.

## PHASE 6: Notification

[If Slack is enabled, INSERT: Full notification section from slack.md — both consolidation and briefing notification formats, notification rules]

[If Google Chat is enabled, INSERT: Full notification section from google-chat.md — DM-space resolution, both consolidation and briefing notification formats, notification rules]

[If neither Slack nor Google Chat is enabled:]
The git commit message serves as the run record. No external notification is sent. If you want notifications, connect Slack or Google Chat and re-run `/scout-setup`.

---

## Your Details

- **Instance:** {{INSTANCE_NAME}}
- **User:** {{USER_NAME}}
- **Email:** {{USER_EMAIL}}
[If Slack is enabled:] - **Slack ID:** {{USER_SLACK_ID}}
[If Google Chat is enabled:] - **Google Chat user:** users/{{USER_GOOGLE_CHAT_ID}}
[If GitHub is enabled:] - **GitHub:** {{GITHUB_USERNAME}}
[If GitHub repos are configured:] - **Monitored repos:** {{GITHUB_REPOS}}
```

**IMPORTANT:** When writing SKILL.md, you must paste the FULL text of each phase section — do not use `[INSERT: ...]` placeholders in the output file. The markers above are instructions to you about what to include. The final SKILL.md must be completely self-contained with no references to phase files.

### Assemble DREAMING.md

Read phase files from `${CLAUDE_PLUGIN_ROOT}/phases/modes/` and the core setup/KB phases. Filter based on enabled connectors.

Write `{{SCOUT_DIR}}/DREAMING.md` with the following structure:

```markdown
---
name: {{INSTANCE_NAME_LOWER}}-dreaming
description: Evening self-improvement and KB deep work — processes feedback, proposes skill improvements, and does knowledge base deep work
---

You are running **{{INSTANCE_NAME}}** in **DREAMING** mode — the evening self-improvement and knowledge base deep work session. This is distinct from the morning briefing and daytime consolidation runs.

**BASE_DIR:** `{{SCOUT_DIR}}`

All file paths in this document are relative to BASE_DIR unless otherwise noted.

<!-- Assembled by scout-setup from phase files. Re-run /scout-setup to regenerate. -->

## What Dreaming Does

Three phases, every run:

1. **Feedback Processing** — Read {{USER_NAME}}'s reactions and replies on {{INSTANCE_NAME}}'s messages, classify feedback, update the mistake audit, apply direct improvements to KB files, and write proposals for SKILL.md changes through a gated workflow.
2. **KB Deep Work** — Score every KB file on staleness, gaps, structural integrity, and feedback signals. Dynamically pick the highest-value improvement work.
3. **Wishlist** — Check `docs/Wishlist.md` for feature requests. Pick one actionable item per run and implement it.

## What Dreaming Does NOT Do

- No action items work (no reading, updating, or creating action-items files)
- No "what happened today" delta scanning
- No morning briefing mode
- No Calendar/Gmail scanning for activities
- No status reports or external artifacts

---

## Time Check

Check the current time:
```bash
date '+%H %Z'
```

- **If the hour is {{DREAMING_HOUR_1}} ({{DREAMING_DISPLAY_1}})** -> first evening run (full day's feedback)
- **If the hour is {{DREAMING_HOUR_2}} ({{DREAMING_DISPLAY_2}})** -> second evening run (new feedback + more KB work)
- **If any other hour** (manual trigger) -> run normally

Both runs execute the same phases. The difference is natural: the first run processes the full day's feedback; the second picks up reactions to the first run's notification and does a fresh round of KB work on different files.

---

[INSERT: Full content of phases/core/git-setup.md — with variables replaced]

[INSERT: Full content of phases/core/kb-management.md — with variables replaced]

---

# PHASE 1: FEEDBACK PROCESSING

[If Slack is enabled, INSERT: Full content of phases/modes/feedback-processing.md — all steps 1a through 1f, with variables replaced]

[If Slack is NOT enabled, write instead:]
Phase 1 is skipped — Slack is not connected. The shipped feedback-processing pass is Slack-specific (reactions and replies on {{INSTANCE_NAME}}'s DM notifications). If Google Chat is connected, basic feedback can still be inferred from replies in the self-DM space (`gws chat spaces messages list --params '{"parent":"<DM_SPACE>","filter":"create_time > \"<since>\""}'` with `sender.name != users/{{USER_GOOGLE_CHAT_ID}}`), but the full classification flow requires Slack today. Connect Slack and re-run `/scout-setup` to enable the full phase.

Proceed directly to Phase 2.

---

# PHASE 2: KB DEEP WORK

[INSERT: Full content of phases/modes/kb-deep-work.md — all steps 2a through 2g, with variables replaced. This is always included regardless of connectors.]

---

# PHASE 3: WISHLIST

[INSERT: Full content of phases/modes/wishlist.md — all steps 3a through 3e, with variables replaced. This is always included regardless of connectors.]

---

# NOTIFICATION

[If Slack is enabled:]
Send a Slack DM to {{USER_NAME}} (Slack ID: `{{USER_SLACK_ID}}`) summarizing the dreaming run:

```
{{INSTANCE_NAME}} dreaming run complete.
- Feedback: [X signals processed, Y mistakes logged, Z proposals written]
- KB deep work: [mode chosen], [files worked on]
- Wishlist: [item completed/in-progress/skipped, or "no actionable items"]
```

[If Google Chat is enabled:]
Send a Google Chat DM to {{USER_NAME}} (Google Chat user: `users/{{USER_GOOGLE_CHAT_ID}}`) summarizing the dreaming run. Resolve the self-DM space once via `gws chat spaces findDirectMessage --params '{"name":"users/{{USER_GOOGLE_CHAT_ID}}"}'` (or read it from `.scout-cache/google-chat-self-dm` if previously cached), then post with `gws chat +send --space "$DM_SPACE" --text "..."`:

```
{{INSTANCE_NAME}} dreaming run complete.
- Feedback: [X signals processed, Y mistakes logged, Z proposals written]
- KB deep work: [mode chosen], [files worked on]
- Wishlist: [item completed/in-progress/skipped, or "no actionable items"]
```

[If neither Slack nor Google Chat is enabled:]
The git commit message serves as the run record. No external notification is sent.

---

## Your Details

- **Instance:** {{INSTANCE_NAME}}
- **User:** {{USER_NAME}}
- **Email:** {{USER_EMAIL}}
[If Slack is enabled:] - **Slack ID:** {{USER_SLACK_ID}}
[If Google Chat is enabled:] - **Google Chat user:** users/{{USER_GOOGLE_CHAT_ID}}
[If GitHub is enabled:] - **GitHub:** {{GITHUB_USERNAME}}
```

**IMPORTANT:** Same rule as SKILL.md — paste the FULL text of each phase section. The `[INSERT: ...]` markers are instructions to you. The final DREAMING.md must be completely self-contained.

### Assemble RESEARCH.md

Read phase files from `${CLAUDE_PLUGIN_ROOT}/phases/research/`. These are always included regardless of connectors (research uses web tools and `gh` CLI, not MCP connectors).

Write `{{SCOUT_DIR}}/RESEARCH.md` with the following structure:

```markdown
---
name: {{INSTANCE_NAME_LOWER}}-research
description: Outward-facing knowledge expansion — enriches KB entities with real-world information from web, docs, and APIs
---

You are running **{{INSTANCE_NAME}}** in **RESEARCH** mode — the knowledge expansion session. Unlike dreaming (which audits existing KB quality) or consolidation (which captures what happened today), Research goes **outward** — discovering new information about entities, technologies, and trends, then integrating it into the knowledge base.

**Related files:** [[knowledge-base]] | [[DREAMING]] | [[research-queue]] | [[ontology/schema.yaml]]

**BASE_DIR:** `{{SCOUT_DIR}}`

## What Research Does

1. **Select research targets** — Pick entities or topics that would benefit most from external knowledge enrichment.
2. **Deep research** — Web search, documentation reading, API queries, changelog scanning.
3. **Knowledge integration** — Update entity files, add new entities, extend relationships.
4. **Insight synthesis** — Summarize findings, flag actionable items.

## What Research Does NOT Do

- No action items work
- No "what happened today" scanning (that's consolidation)
- No KB quality auditing (that's dreaming)
- No feedback processing (that's dreaming Phase 1)
- No wishlist work (that's dreaming Phase 3)

---

[INSERT: Full content of phases/core/git-setup.md — with variables replaced]

[INSERT: Full content of phases/research/research-targets.md — Phase 1]

[INSERT: Full content of phases/research/deep-research.md — Phase 2]

[INSERT: Full content of phases/research/knowledge-integration.md — Phase 3]

[INSERT: Full content of phases/research/commit-notify.md — Phase 4]

---

## KB Management Rules

Same rules as dreaming and consolidation:
- Use `[[wikilinks]]` for all internal references
- Never use `index.md` — name files after their folder
- Never reorganize the folder structure
- Follow verification levels: no marker = 2+ sources, [single-source], [unverified], [stale]
- Send uncertain claims to `knowledge-base/review-queue.md`
- Use `gh` CLI for all GitHub operations

## {{USER_NAME}}'s Details

- **Email:** {{USER_EMAIL}}
[If Slack is enabled:] - **Slack ID:** {{USER_SLACK_ID}}
[If Google Chat is enabled:] - **Google Chat user:** users/{{USER_GOOGLE_CHAT_ID}}
[If GitHub is enabled:] - **GitHub:** {{GITHUB_USERNAME}}
```

**IMPORTANT:** Same assembly rules — paste the FULL text of each phase section. The final RESEARCH.md must be completely self-contained.

### Commit Skill Files

After writing all three files:

```bash
cd "{{SCOUT_DIR}}" && git add SKILL.md DREAMING.md RESEARCH.md && git commit -m "Add assembled skill files"
```

Tell the user: "Skill files assembled. SKILL.md covers morning briefings, weekend briefings, and consolidation. DREAMING.md covers evening self-improvement. RESEARCH.md covers knowledge expansion. All are tailored to your connected tools."

---

## Step 5: Scheduling

Tell the user: "Now let's set up automated scheduling. Here are sensible defaults for your runs:"

Present the default schedule:

```
Morning briefing:   8:03 AM (weekdays)
Consolidation:      11:03 AM, 1:07 PM, 5:03 PM (weekdays)
Dreaming:           6:33 PM, 8:33 PM (weekdays)
```

Ask: "Press enter to accept these defaults, or tell me your preferred times."

Store the schedule values. Defaults:
- `BRIEFING_TIME` = "8:03"
- `BRIEFING_HOUR` = "08" (two-digit hour for the mode check)
- `CONSOLIDATION_TIMES` = "11:03, 13:07, 17:03"
- `DREAMING_TIMES` = "18:33, 20:33"
- `WEEKDAYS_ONLY` = "true"

If the user provides custom times, parse them and update all schedule variables accordingly. Extract hours for the mode-check logic in the skill files.

Compute derived variables for runner scripts:
- `BRIEFING_HOUR` — the hour portion of the briefing time (zero-padded, e.g., "08")
- `CONSOLIDATION_HOURS_CASE` — bash case pattern for consolidation hours (e.g., `11|13|17) MODE="consolidation" ;;`)
- `CONSOLIDATION_HOURS_DISPLAY` — human-readable for SKILL.md mode check (e.g., "11, 13, or 17 (11 AM, 1 PM, or 5 PM)")
- `DREAMING_HOUR_1`, `DREAMING_HOUR_2` — hours for the two dreaming slots
- `DREAMING_DISPLAY_1`, `DREAMING_DISPLAY_2` — human-readable (e.g., "6:33 PM")
- `DREAMING_HOURS_CASE` — bash case pattern for dreaming hours (e.g., `18|20) MODE="dreaming" ;;`)

### Detect Platform

```bash
uname -s
```

- "Darwin" = macOS (use launchd)
- "Linux" = Linux (use cron)

Store as `PLATFORM` ("macos" or "linux").

### Detect Claude Binary

```bash
which claude 2>/dev/null || echo "NOT_FOUND"
```

Store the path as `CLAUDE_BIN`. If not found, ask the user: "I couldn't find the `claude` binary. What's the full path to your Claude Code CLI?" Store whatever they provide.

### Set Budget

Ask: "What's the maximum budget per run in USD? (default: 5.00)"

Store as `MAX_BUDGET`. Default: "5.00".

### macOS Scheduling (launchd)

If `PLATFORM` is "macos":

**1. Write runner scripts**

Read `${CLAUDE_PLUGIN_ROOT}/templates/run-scout.sh.tmpl`. Replace all `{{VARIABLES}}` with collected values. Write to `{{SCOUT_DIR}}/run-scout.sh`. Make executable with `chmod +x`.

Read `${CLAUDE_PLUGIN_ROOT}/templates/run-dreaming.sh.tmpl`. Replace all `{{VARIABLES}}`. Write to `{{SCOUT_DIR}}/run-dreaming.sh`. Make executable with `chmod +x`.

Read `${CLAUDE_PLUGIN_ROOT}/templates/run-research.sh.tmpl`. Replace all `{{VARIABLES}}`. Write to `{{SCOUT_DIR}}/run-research.sh`. Make executable with `chmod +x`.

**2. Generate plist files**

Read `${CLAUDE_PLUGIN_ROOT}/templates/launchd-plist.tmpl`.

Generate TWO plist files from this template:

**Briefing + Consolidation plist** (`com.{{INSTANCE_NAME_LOWER}}.briefing.plist`):
- `PLIST_TYPE` = "briefing"
- `RUN_SCRIPT_PATH` = "{{SCOUT_DIR}}/run-scout.sh"
- `SCHEDULE_ENTRIES` = Generate one `<dict>` block per time slot per weekday. For each time in the briefing + consolidation schedule, and for each weekday (Monday=1 through Friday=5), create:
  ```xml
          <dict>
              <key>Hour</key>
              <integer>HOUR</integer>
              <key>Minute</key>
              <integer>MINUTE</integer>
              <key>Weekday</key>
              <integer>WEEKDAY</integer>
          </dict>
  ```
- `PATH_ENV` = Output of `echo $PATH`
- `HOME_ENV` = Output of `echo $HOME`

**Dreaming plist** (`com.{{INSTANCE_NAME_LOWER}}.dreaming.plist`):
- `PLIST_TYPE` = "dreaming"
- `RUN_SCRIPT_PATH` = "{{SCOUT_DIR}}/run-dreaming.sh"
- `SCHEDULE_ENTRIES` = Same pattern but for dreaming times only

Write both plists to `~/Library/LaunchAgents/`.

**3. Ask to load**

Ask: "Schedule files written. Load them now? This will start the automated runs at the configured times. (yes/no)"

If yes:
```bash
launchctl load ~/Library/LaunchAgents/com.{{INSTANCE_NAME_LOWER}}.briefing.plist
launchctl load ~/Library/LaunchAgents/com.{{INSTANCE_NAME_LOWER}}.dreaming.plist
```

Verify:
```bash
launchctl list | grep {{INSTANCE_NAME_LOWER}}
```

If the grep returns results, tell the user the schedule is active. If not, tell the user the load may have failed and suggest checking with `launchctl list`.

### Linux Scheduling (cron)

If `PLATFORM` is "linux":

**1. Write runner scripts** (same as macOS — write run-scout.sh, run-dreaming.sh, and run-research.sh to SCOUT_DIR, chmod +x)

**2. Generate cron entries**

Read `${CLAUDE_PLUGIN_ROOT}/templates/cron-entry.tmpl` for the header format.

Generate cron lines. For each briefing + consolidation time, create:
```
MINUTE HOUR * * 1-5 {{SCOUT_DIR}}/run-scout.sh >> {{SCOUT_DIR}}/.scout-logs/cron.log 2>&1
```

For each dreaming time:
```
MINUTE HOUR * * 1-5 {{SCOUT_DIR}}/run-dreaming.sh >> {{SCOUT_DIR}}/.scout-logs/cron.log 2>&1
```

(If `WEEKDAYS_ONLY` is false, use `*` instead of `1-5` for the day-of-week field.)

**3. Present and ask**

Show the user the complete cron entries and ask: "Install these cron entries? (yes/no)"

If yes, append to crontab:
```bash
(crontab -l 2>/dev/null; echo ""; echo "# {{INSTANCE_NAME}} scheduled runs"; cat <<'CRON'
<generated cron entries>
CRON
) | crontab -
```

### Update Config and Commit

Update `{{SCOUT_DIR}}/scout-config.yaml` with the final schedule values (re-process the template or edit in place).

Update SKILL.md and DREAMING.md if the schedule times differ from the defaults initially used during assembly (the mode-check hours need to match the actual schedule).

Commit runner scripts and any config updates:
```bash
cd "{{SCOUT_DIR}}" && git add -A && git commit -m "Add runner scripts and configure scheduling"
```

---

## Step 6: First Run

Tell the user:

"Setup complete! Your **{{INSTANCE_NAME}}** is ready."

"Your knowledge base is at `{{SCOUT_DIR}}/knowledge-base/`. For the best reading experience, open the entire `{{SCOUT_DIR}}` directory as an Obsidian vault — the `[[wikilink]]` structure creates a navigable knowledge graph."

"Summary of what was set up:"
- Instance: **{{INSTANCE_NAME}}**
- Directory: `{{SCOUT_DIR}}`
- Connected tools: [list only the enabled ones]
- Schedule: [briefing time, consolidation times, dreaming times]
- Platform: [macOS launchd / Linux cron]

"Would you like to run your first morning briefing now? This will query your connected tools, build today's action items, and populate the knowledge base. It typically takes 3-5 minutes."

If the user says yes, run the briefing:

```bash
cd "{{SCOUT_DIR}}" && bash run-scout.sh
```

Or, if the runner script is not yet tested and they prefer a direct invocation:

```bash
cd "{{SCOUT_DIR}}" && claude --permission-mode auto --model opus -p "You are {{INSTANCE_NAME}}, an autonomous knowledge management system. Your working directory is {{SCOUT_DIR}}. Read {{SCOUT_DIR}}/SKILL.md in full, determine your mode (use MORNING BRIEFING for this first run), and execute all steps completely."
```

If the user says no, tell them: "No problem! Your first run will happen automatically at the next scheduled briefing time. You can also trigger a manual run anytime with: `cd {{SCOUT_DIR}} && bash run-scout.sh`"

---

## Error Handling Notes

Throughout this wizard, follow these principles:

- **Connector probes must not crash the wizard.** If any tool call fails (timeout, auth error, tool not found), catch the error, mark that connector as not connected, and continue. Tell the user which probe failed if useful.
- **Template files must exist.** If a template file is missing from `${CLAUDE_PLUGIN_ROOT}/templates/`, tell the user which file is missing and suggest re-installing the plugin. Do not proceed with a partial setup.
- **Phase files must exist for assembly.** If a phase file referenced during assembly is missing, warn the user and skip that section. The resulting skill file may be incomplete — note this clearly.
- **Git failures are non-fatal.** If `git init` or `git commit` fails, warn the user but continue. The setup is still usable without git history.
- **Path expansion.** Always expand `~` to the full home directory path when writing to config files and scripts. Use `$HOME` in bash or the expanded path in file writes.
- **Idempotent file writes.** If a file already exists at the target path (e.g., during Reconfigure), overwrite it. The git history preserves the old version.
