---
name: scout-update
description: Upgrade an existing Scout vault to the current plugin version. Idempotent — re-runs converge to the same state. For first-time install, run /scout-setup.
---

# Scout Update

You are the Scout updater. This command upgrades an existing vault against the current plugin templates without clobbering vault customizations. It runs an 8-stage pipeline (pre-flight → migrations → cat-1 file overwrites → cat-1b runner regeneration → cat-4 3-way merge → job lifecycle → version stamp → doctor).

This command is for **existing vaults only**. If no vault exists, refuse and tell the user to run `/scout-setup`.

---

## Step 0: Pre-flight (refuse if no vault; refuse if pending sidecars)

Run:

```bash
bash <<'EOF'
set -e
test -f "$HOME/Scout/scout-config.yaml" || { echo "NO_VAULT"; exit 0; }
ls "$HOME/Scout/"{SKILL,DREAMING,RESEARCH}.md.proposed-merge 2>/dev/null && { echo "PENDING_SIDECARS"; exit 0; }
test -x "$HOME/scout-plugin/.venv/bin/scoutctl" || { echo "VENV_MISSING"; exit 0; }
echo "READY"
EOF
```

- `NO_VAULT`: "No Scout vault found at `~/Scout/`. Run `/scout-setup` for a fresh install."
- `PENDING_SIDECARS`: "Unresolved merge conflicts from a prior `/scout-update`:" — list the sidecar files. Then: "Edit each file to remove conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), then run `mv X.md.proposed-merge X.md` for each. Then re-run `/scout-update`."
- `VENV_MISSING`: "Engine venv missing. Run `bash ~/scout-plugin/scripts/install-venv.sh` then re-run `/scout-update`."
- `READY`: continue.

---

## Step 1: Show what's about to happen

Read the current and target plugin versions:

```bash
~/scout-plugin/.venv/bin/scoutctl version
python3 -c "import json; print(json.load(open('${CLAUDE_PLUGIN_ROOT}/plugin.json'))['version'])"
grep version_at_last_update ~/Scout/scout-config.yaml || true
```

Tell the user: "Plugin version: `<plugin>`. Vault was last updated against version `<vault>`. About to apply Plan 8 upgrade pipeline. Proceed? (yes/no)"

If user declines, stop.

---

## Step 2: Run `scoutctl bootstrap upgrade`

```bash
~/scout-plugin/.venv/bin/scoutctl bootstrap upgrade
```

Capture exit code (0 = green, 1 = yellow, 2 = red) and stdout/stderr.

---

## Step 3: Report

- If exit 0: "Upgrade complete. Doctor: green. New version recorded."
- If exit 1: list every `warning:` line. Highlight any `conflict (sidecar):` rows — these are the SKILL/DREAMING/RESEARCH files the user must merge by hand. Provide the resolution instructions: edit the sidecar, `mv X.md.proposed-merge X.md`, re-run `/scout-update`.
- If exit 2: list every `error:` line. Suggest `scoutctl bootstrap doctor` for a clean read of the current state.

If runner backups appeared (`run-*.sh.bak.*`), tell the user the live runners had hand-edits that have been preserved as backups; the fresh templates were installed.
