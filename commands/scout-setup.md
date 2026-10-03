---
name: scout-setup
description: First-time install of Scout. Detects connected tools, collects user details, and hands off to scoutctl bootstrap install. For upgrading an existing vault, run /scout-update.
---

# Scout Setup Wizard (greenfield only)

You are the Scout setup wizard. Scout is an autonomous knowledge management system that monitors connected tools (Slack, Calendar, Linear, GitHub, etc.), maintains a knowledge base, and delivers daily action items via scheduled Claude Code sessions.

This command is for **fresh installs only**. If a vault already exists, refuse and tell the user to run `/scout-update`.

---

## Step 0: Pre-flight (refuse if Scout.app manages the engine or a vault is detected; install venv if missing)

Run this single bash command. Its first check asks whether this engine is managed by Scout.app — before anything else, so an app-managed install is never touched (no venv is built in Claude Code's plugin cache):

```bash
bash <<'EOF'
set -e
grep -q '"managed_by": "scout-app"' "$HOME/.local/state/scout/engine.json" 2>/dev/null && echo "APP_MANAGED" && exit 0
test -f "$HOME/Scout/.scout-state/install-incomplete" && echo "INSTALL_INCOMPLETE" && exit 0
test -f "$HOME/Scout/scout-config.yaml" && echo "VAULT_EXISTS" && exit 0
test -d "$HOME/Scout/.scout-state" && echo "VAULT_EXISTS" && exit 0
ls "$HOME/Library/LaunchAgents/com.scout."*.plist 2>/dev/null && echo "ORPHAN_JOBS" && exit 0
echo "FRESH"
EOF
```

- If output is `APP_MANAGED`: tell the user "This Scout engine is managed by Scout.app. Please run setup from the Scout.app Settings pane instead." Stop here.
- If output is `INSTALL_INCOMPLETE`: an earlier install into `~/Scout/` stopped before it finished (`.scout-state/install-incomplete` is still there). Tell the user "A previous Scout install was interrupted before it finished. Setup will pick it up and finish it — every install step is safe to repeat." Then continue exactly as for `FRESH`: Step 4's `scoutctl bootstrap install` resumes the interrupted install instead of refusing it.
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
  [✓] Slack          [✓] Calendar          [✗] Gmail
  [✓] Linear         [✓] GitHub             [✗] Granola
  [✗] Drive          [✓] Claude Sessions
```

Confirm with the user: "Proceed with these connectors? Or pause to enable more first?"

---

## Step 3: Auto-update preference

Ask the user:

> "Should Scout keep itself up to date automatically? When on, scheduled runs apply upgrades and ping you if a change needs manual review. (You can change this later via `/scout-update`.)"

Wait for a yes/no answer. Pass it to the install in Step 4 as `--auto-update` (yes) or `--no-auto-update` (no); `bootstrap install` writes it into `~/Scout/scout-config.yaml` as `auto_update.enabled` (channel `stable`). Do NOT edit the config file by hand or with an inline `python3` script — the system `python3` on stock macOS has no PyYAML.

---

## Step 4: Hand off to `scoutctl bootstrap install`

Build the comma-separated connector list (only enabled), then run (use the `$SCOUTCTL` resolved in Step 0). Pass every connector input you collected in Step 2 — these get persisted into `scout-config.yaml` and are what cat-1b runner templates (run-scout.sh / run-dreaming.sh / run-research.sh) substitute for `USER_SLACK_ID`, etc. Omit any flag whose connector you didn't enable; the install command supplies safe defaults.

Omit `--claude-bin` unless the user told you a specific path: by default the engine detects the `claude` CLI (`PATH`, then `~/.local/bin/claude`, then Homebrew) and records the concrete path. If install prints `warning: claude CLI not executable at …`, stop and tell the user Claude Code must be installed (and signed in once with `claude`) before scheduled runs can work.

```bash
"$SCOUTCTL" bootstrap install \
    --managed-by claude-code \
    --instance-name "<INSTANCE_NAME>" \
    --user-name "<USER_NAME>" \
    --user-email "<USER_EMAIL>" \
    --timezone "<TIMEZONE>" \
    --platform "$(uname -s | tr '[:upper:]' '[:lower:]' | sed 's/darwin/macos/')" \
    --connectors "<comma-separated-enabled-list>" \
    --user-slack-id "<USER_SLACK_ID>" \
    --github-username "<GITHUB_USERNAME>" \
    --github-repos "<comma-separated-repos>" \
    --max-budget "<dollars>" \
    --auto-update   # or --no-auto-update, per Step 3
```

The plist + cron block installed by this step automatically reference `$SCOUTCTL` — `resolve_scoutctl_bin()` derives the path from the running engine's plugin root, so the scheduler is always pinned to the venv the wizard just used.

Capture exit code and stdout. The command emits one line per concern: `installed: <path>`, `doctor: green`, plus warnings for sidecar files or missing snapshots.

---

## Step 5: Report and offer first-run

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

If you need to wipe Scout entirely and start over:

```bash
# macOS
launchctl bootout gui/$UID/com.scout.schedule-tick gui/$UID/com.scout.heartbeat 2>/dev/null
rm -f ~/Library/LaunchAgents/com.scout.*.plist

# Linux
crontab -l | sed '/# >>> scout-managed >>>/,/# <<< scout-managed <<</d' | crontab -

# Both
rm -rf ~/Scout
```

Then re-run `/scout-setup`.
