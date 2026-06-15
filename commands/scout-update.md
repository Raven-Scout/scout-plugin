---
name: scout-update
description: Upgrade an existing Scout vault to the current plugin version. Idempotent — re-runs converge to the same state. For first-time install, run /scout-setup.
---

# Scout Update

You are the Scout updater. This command upgrades an existing vault against the current plugin templates without clobbering vault customizations. It runs an 8-stage pipeline (pre-flight → migrations → cat-1 file overwrites → cat-1b runner regeneration → cat-4 3-way merge → job lifecycle → version stamp → doctor).

This command is for **existing vaults only**. If no vault exists, refuse and tell the user to run `/scout-setup`.

---

## Locating scoutctl — canonical resolver

Every shell block in this command runs in a **fresh process**, so variables set in one block do not carry into the next. Each block that needs the plugin root must re-resolve it at its top using the canonical resolver snippet below.

**Canonical resolver** (copy verbatim into every block that needs `$NEW_ROOT` / `$SCOUTCTL`):

```bash
NEW_ROOT="$HOME/scout-plugin"
[ -d "$NEW_ROOT/.git" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c "import sys,json;print(next(p['installPath'] for m in json.load(sys.stdin).get('plugins',{}).values() for p in m if 'scout-plugin' in p['installPath']))" 2>/dev/null)"
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"
```

This prefers the maintainer git checkout (`~/scout-plugin` when a `.git` dir is present) and otherwise falls back to the freshly-installed marketplace cache path. **Do NOT use `${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}` in any block** — after Step 0.5 refreshes the plugin, `$CLAUDE_PLUGIN_ROOT` may still point at the pre-refresh path.

Use `"$SCOUTCTL"` in every subsequent invocation.

---

## Step 0.5: Refresh the plugin (Surface A) before upgrading the vault (Surface B)

This command updates **both** surfaces — the plugin first, then the vault against the freshly-refreshed plugin. That order matters: upgrading the vault against a stale plugin would apply old templates and miss engine fixes that landed since the last `claude plugin install`.

Pull the latest plugin code:

```bash
bash <<'EOF'
set -e
if [ -d "$HOME/scout-plugin/.git" ]; then
  git -C "$HOME/scout-plugin" pull --ff-only && echo "PULLED_DIRECTORY:$HOME/scout-plugin"
else
  claude plugin marketplace update scout-plugin || true
  claude plugin install scout@scout-plugin || true
  echo "REFRESHED_MARKETPLACE"
fi
EOF
```

Resolve the plugin root that the rest of this upgrade runs from. **Use this resolved `$NEW_ROOT` as the plugin root for every step below.** Because each shell block runs in a fresh process, re-resolve it at the top of each block that needs it using the canonical resolver (do NOT fall back to `$CLAUDE_PLUGIN_ROOT`, which may point at the pre-refresh plugin):

```bash
NEW_ROOT="$HOME/scout-plugin"
[ -d "$NEW_ROOT/.git" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c "import sys,json;print(next(p['installPath'] for m in json.load(sys.stdin).get('plugins',{}).values() for p in m if 'scout-plugin' in p['installPath']))" 2>/dev/null)"
echo "Upgrading vault against plugin root: $NEW_ROOT"
[ -x "$NEW_ROOT/.venv/bin/scoutctl" ] || bash "$NEW_ROOT/scripts/install-venv.sh"
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"
```

---

## Step 0: Pre-flight (refuse if no vault, no venv, pending sidecars, or mismatched venv)

Run:

```bash
bash <<'EOF'
set -e
NEW_ROOT="$HOME/scout-plugin"
[ -d "$NEW_ROOT/.git" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c "import sys,json;print(next(p['installPath'] for m in json.load(sys.stdin).get('plugins',{}).values() for p in m if 'scout-plugin' in p['installPath']))" 2>/dev/null)"
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"

test -f "$HOME/Scout/scout-config.yaml" || { echo "NO_VAULT"; exit 0; }
ls "$HOME/Scout/"{SKILL,DREAMING,RESEARCH}.md.proposed-merge 2>/dev/null && { echo "PENDING_SIDECARS"; exit 0; }
test -x "$SCOUTCTL" || { echo "VENV_MISSING:$NEW_ROOT"; exit 0; }

# Verify the venv is editable-installed FROM this plugin checkout.
# Otherwise we'd run the upgrade against the OTHER tree's templates.
PYTHON="$(dirname "$SCOUTCTL")/python"
INSTALLED=$("$PYTHON" -c "import scout, os; print(os.path.realpath(os.path.dirname(os.path.dirname(scout.__file__))))" 2>/dev/null)
EXPECTED=$(cd "$NEW_ROOT/engine" 2>/dev/null && pwd -P)
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
  bash "$NEW_ROOT/scripts/install-venv.sh"
  ```
  Then re-run `/scout-update`.
- `VENV_MISMATCH:<installed>|<expected>`: "The venv at `$NEW_ROOT/.venv/` is editable-installed from `<installed>`, but this plugin is loaded from `<expected>`. Running the upgrade now would apply the OTHER tree's templates, not the ones in this checkout. Re-install the venv pinned to this plugin source:" then show:
  ```
  bash "$NEW_ROOT/scripts/install-venv.sh"
  ```
  Then re-run `/scout-update`.
- `READY`: continue.

---

## Step 1: Show what's about to happen

Read the current and target plugin versions:

```bash
NEW_ROOT="$HOME/scout-plugin"
[ -d "$NEW_ROOT/.git" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c "import sys,json;print(next(p['installPath'] for m in json.load(sys.stdin).get('plugins',{}).values() for p in m if 'scout-plugin' in p['installPath']))" 2>/dev/null)"
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"
"$SCOUTCTL" version
python3 -c "import json; print(json.load(open('$NEW_ROOT/plugin.json'))['version'])"
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

- `~/Scout/connector-probes.local.yaml` (custom connector probes) is a user
  file, never templated, so it is preserved untouched across upgrades.

---

## Auto-update nudge

After reporting the upgrade result, check whether the user has auto-updates enabled:

```bash
python3 - <<'EOF'
import pathlib, yaml
p = pathlib.Path.home() / "Scout" / "scout-config.yaml"
if p.exists():
    cfg = yaml.safe_load(p.read_text()) or {}
    enabled = cfg.get("auto_update", {}).get("enabled", False)
    print("AUTO_UPDATE_ON" if enabled else "AUTO_UPDATE_OFF")
else:
    print("AUTO_UPDATE_OFF")
EOF
```

- If `AUTO_UPDATE_ON`: nothing to say — auto-updates are already configured.
- If `AUTO_UPDATE_OFF`: tell the user once: "Auto-updates are off — I can turn them on so Scout keeps itself current (sidecar-clean upgrades only; you'll be pinged on conflict). Want me to enable it?"

If the user agrees, write/merge the `auto_update` block into `~/Scout/scout-config.yaml`, preserving any other keys already in the file:

```bash
python3 - <<'EOF'
import pathlib, yaml
p = pathlib.Path.home() / "Scout" / "scout-config.yaml"
cfg = yaml.safe_load(p.read_text()) or {}
cfg.setdefault("auto_update", {})
cfg["auto_update"]["enabled"] = True
cfg["auto_update"].setdefault("channel", "stable")
p.write_text(yaml.safe_dump(cfg, sort_keys=False))
print("Auto-update enabled (channel: stable).")
EOF
```

If the user declines, acknowledge and move on — don't ask again in this session.
