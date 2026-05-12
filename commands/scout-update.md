---
name: scout-update
description: Upgrade an existing Scout vault to the current plugin version. Idempotent — re-runs converge to the same state. For first-time install, run /scout-setup.
---

# Scout Update

You are the Scout updater. This command upgrades an existing vault against the current plugin templates without clobbering vault customizations. It runs an 8-stage pipeline (pre-flight → migrations → cat-1 file overwrites → cat-1b runner regeneration → cat-4 3-way merge → job lifecycle → version stamp → doctor).

This command is for **existing vaults only**. If no vault exists, refuse and tell the user to run `/scout-setup`.

---

## Locating scoutctl

Use the venv that lives inside the plugin Claude Code loaded — `$CLAUDE_PLUGIN_ROOT/.venv/bin/scoutctl`. That guarantees the upgrade reads templates and engine code from THIS plugin checkout, not from some other clone whose venv happens to be on `$PATH`. Belt-and-suspenders: fall back to `~/scout-plugin/.venv/bin/scoutctl` if `$CLAUDE_PLUGIN_ROOT` is somehow unset.

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
SCOUTCTL="$PLUGIN_ROOT/.venv/bin/scoutctl"
```

Use `"$SCOUTCTL"` in every subsequent invocation.

---

## Step 0: Pre-flight (refuse if no vault, no venv, pending sidecars, or mismatched venv)

Run:

```bash
bash <<'EOF'
set -e
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
SCOUTCTL="$PLUGIN_ROOT/.venv/bin/scoutctl"

test -f "$HOME/Scout/scout-config.yaml" || { echo "NO_VAULT"; exit 0; }
ls "$HOME/Scout/"{SKILL,DREAMING,RESEARCH}.md.proposed-merge 2>/dev/null && { echo "PENDING_SIDECARS"; exit 0; }
test -x "$SCOUTCTL" || { echo "VENV_MISSING:$PLUGIN_ROOT"; exit 0; }

# Verify the venv is editable-installed FROM this plugin checkout.
# Otherwise we'd run the upgrade against the OTHER tree's templates.
PYTHON="$(dirname "$SCOUTCTL")/python"
INSTALLED=$("$PYTHON" -c "import scout, os; print(os.path.realpath(os.path.dirname(os.path.dirname(scout.__file__))))" 2>/dev/null)
EXPECTED=$(cd "$PLUGIN_ROOT/engine" 2>/dev/null && pwd -P)
if [ -n "$INSTALLED" ] && [ -n "$EXPECTED" ] && [ "$INSTALLED" != "$EXPECTED" ]; then
    echo "VENV_MISMATCH:$INSTALLED|$EXPECTED"
    exit 0
fi
echo "READY"
EOF
```

- `NO_VAULT`: "No Scout vault found at `~/Scout/`. Run `/scout-setup` for a fresh install."
- `PENDING_SIDECARS`: "Unresolved merge conflicts from a prior `/scout-update`:" — list the sidecar files. Then: "Edit each file to remove conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), then run `mv X.md.proposed-merge X.md` for each. Then re-run `/scout-update`."
- `VENV_MISSING:<plugin-root>`: "Engine venv missing at `<plugin-root>/.venv/`. Install it with:" then show:
  ```
  bash "$CLAUDE_PLUGIN_ROOT/scripts/install-venv.sh"
  ```
  (or substitute the actual plugin root if `$CLAUDE_PLUGIN_ROOT` isn't set in the user's shell). Then re-run `/scout-update`.
- `VENV_MISMATCH:<installed>|<expected>`: "The venv at `$PLUGIN_ROOT/.venv/` is editable-installed from `<installed>`, but this plugin is loaded from `<expected>`. Running the upgrade now would apply the OTHER tree's templates, not the ones in this checkout. Re-install the venv pinned to this plugin source:" then show:
  ```
  bash "$CLAUDE_PLUGIN_ROOT/scripts/install-venv.sh"
  ```
  Then re-run `/scout-update`.
- `READY`: continue.

---

## Step 1: Show what's about to happen

Read the current and target plugin versions:

```bash
"$SCOUTCTL" version
python3 -c "import json; print(json.load(open('${CLAUDE_PLUGIN_ROOT}/plugin.json'))['version'])"
grep version_at_last_update ~/Scout/scout-config.yaml || true
```

Tell the user: "Plugin version: `<plugin>`. Vault was last updated against version `<vault>`. About to apply Plan 8 upgrade pipeline. Proceed? (yes/no)"

If user declines, stop.

---

## Step 2: Run `scoutctl bootstrap upgrade`

```bash
"$SCOUTCTL" bootstrap upgrade
```

Capture exit code (0 = green, 1 = yellow, 2 = red) and stdout/stderr.

---

## Step 3: Report

- If exit 0: "Upgrade complete. Doctor: green. New version recorded."
- If exit 1: list every `warning:` line. Highlight any `conflict (sidecar):` rows — these are the SKILL/DREAMING/RESEARCH files the user must merge by hand. Provide the resolution instructions: edit the sidecar, `mv X.md.proposed-merge X.md`, re-run `/scout-update`.
- If exit 2: list every `error:` line. Suggest `scoutctl bootstrap doctor` for a clean read of the current state.

If runner backups appeared (`run-*.sh.bak.*`), tell the user the live runners had hand-edits that have been preserved as backups; the fresh templates were installed.
