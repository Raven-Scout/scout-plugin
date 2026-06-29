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
5. **(Optional, skippable)** "How would you like Scout to communicate with you — preferred language, tone/length, and anything it should always check with you before acting on? (Press Enter to skip — Scout fills this from defaults and learns the rest from your feedback. You can set it anytime with `/scout-profile`.)"

Capture the answer to #5 verbatim (or empty if skipped) for Step 3c. Everything else about the user — role, key people, focus, goals — Scout derives from your connectors on its own runs; do **not** ask for them here.

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

Capture exit code and stdout. The command emits one line per concern: `installed: <path>`, `doctor: green`, plus warnings for sidecar files or missing snapshots.

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

## Step 3c: Seed the communication contract (only if the user answered Q5)

If the user **skipped** Q5, do nothing here — the seeded `communication.md` defaults stand and Scout learns from feedback.

If they **answered**, open `~/Scout/knowledge-base/profile/communication.md` (already seeded by bootstrap) and fill in only the fields their answer covers, by replacing the matching `<!-- TODO: ... -->` sentinel(s):

- preferred language → the **Language** line
- tone / length → the **Tone & length** lines
- what to always check first → the **Always check with {{USER_NAME}} first** line under the autonomy contract

Leave every sentinel they didn't address untouched (Scout fills those from feedback later). Do not invent preferences beyond what they said. Then bump `last_reviewed:` to today. This is the same "post-write into the freshly-created vault" pattern as Step 3b — edit the live file directly; the template is not re-rendered at install time.

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
