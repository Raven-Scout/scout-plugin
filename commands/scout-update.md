---
name: scout-update
description: Upgrade an existing Scout vault to the current plugin version. Idempotent — re-runs converge to the same state. For first-time install, run /scout-setup.
---

# Scout Update

You are the Scout updater. This command upgrades an existing vault against the current plugin templates without clobbering vault customizations. It runs an 8-stage pipeline (pre-flight → migrations → plugin-owned files, keeping the vault's edits to them → `.gitignore` merge → cat-4 3-way merge → job lifecycle → version stamp → doctor).

This command is for **existing vaults only**. If no vault exists, refuse and tell the user to run `/scout-setup`.

---

## Locating scoutctl — canonical resolver

Every shell block in this command runs in a **fresh process**, so variables set in one block do not carry into the next. Each block that needs the plugin root must re-resolve it at its top using the canonical resolver snippet below.

**Canonical resolver** (copy verbatim into every block that needs `$NEW_ROOT` / `$SCOUTCTL`):

```bash
NEW_ROOT="$HOME/scout-plugin"
[ -d "$NEW_ROOT/.git" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);e=d if isinstance(d,list) else [p for ps in d.get("plugins",{}).values() for p in ps];print(next((p["installPath"] for p in e if p.get("id")=="scout@scout-plugin"),""))' 2>/dev/null)"
[ -n "$NEW_ROOT" ] || NEW_ROOT="$(ls -d "$HOME"/.claude/plugins/cache/scout-plugin/scout/*/ 2>/dev/null | sort -V | tail -1)"
NEW_ROOT="${NEW_ROOT%/}"
[ -n "$NEW_ROOT" ] || { echo "PLUGIN_ROOT_NOT_FOUND"; exit 1; }
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"
```

This prefers the maintainer git checkout (`~/scout-plugin` when a `.git` dir is present), then the `scout@scout-plugin` entry of `claude plugin list --json` (which has emitted both a flat list and a `{"plugins": {...}}` map — the snippet accepts either), then the newest version directory in the marketplace cache. It never yields an empty root: if all three miss it prints `PLUGIN_ROOT_NOT_FOUND` and stops — tell the user the plugin isn't installed and to re-run the installer. **Do NOT use `${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}` in any block** — after Step 0.5 refreshes the plugin, `$CLAUDE_PLUGIN_ROOT` may still point at the pre-refresh path.

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
  # On an existing install, install only unpacks the new cache and keeps the old version
  # registered, so the resolver below would hand back the old plugin. update switches it.
  claude plugin update scout@scout-plugin || true
  echo "REFRESHED_MARKETPLACE"
fi
EOF
```

Resolve the plugin root that the rest of this upgrade runs from. **Use this resolved `$NEW_ROOT` as the plugin root for every step below.** Because each shell block runs in a fresh process, re-resolve it at the top of each block that needs it using the canonical resolver (do NOT fall back to `$CLAUDE_PLUGIN_ROOT`, which may point at the pre-refresh plugin):

```bash
NEW_ROOT="$HOME/scout-plugin"
[ -d "$NEW_ROOT/.git" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);e=d if isinstance(d,list) else [p for ps in d.get("plugins",{}).values() for p in ps];print(next((p["installPath"] for p in e if p.get("id")=="scout@scout-plugin"),""))' 2>/dev/null)"
[ -n "$NEW_ROOT" ] || NEW_ROOT="$(ls -d "$HOME"/.claude/plugins/cache/scout-plugin/scout/*/ 2>/dev/null | sort -V | tail -1)"
NEW_ROOT="${NEW_ROOT%/}"
[ -n "$NEW_ROOT" ] || { echo "PLUGIN_ROOT_NOT_FOUND"; exit 1; }
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
  | python3 -c 'import sys,json;d=json.load(sys.stdin);e=d if isinstance(d,list) else [p for ps in d.get("plugins",{}).values() for p in ps];print(next((p["installPath"] for p in e if p.get("id")=="scout@scout-plugin"),""))' 2>/dev/null)"
[ -n "$NEW_ROOT" ] || NEW_ROOT="$(ls -d "$HOME"/.claude/plugins/cache/scout-plugin/scout/*/ 2>/dev/null | sort -V | tail -1)"
NEW_ROOT="${NEW_ROOT%/}"
[ -n "$NEW_ROOT" ] || { echo "PLUGIN_ROOT_NOT_FOUND"; exit 1; }
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"

test -f "$HOME/Scout/scout-config.yaml" || { echo "NO_VAULT"; exit 0; }
# Any one sidecar blocks the upgrade. parser.py's can only be one an older
# engine left behind; current upgrades park parser.py conflicts instead.
PENDING=""
for f in SKILL.md DREAMING.md RESEARCH.md knowledge-base/ontology/parser.py; do
  if [ -e "$HOME/Scout/$f.proposed-merge" ]; then PENDING="$PENDING $f.proposed-merge"; fi
done
if [ -n "$PENDING" ]; then echo "PENDING_SIDECARS:$PENDING"; exit 0; fi
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
- `PENDING_SIDECARS:<files>`: "Unresolved merge conflicts from a prior `/scout-update`:" — list the sidecar files (paths are relative to `~/Scout/`). Then: "Edit each file to remove conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`), then run `mv <file>.proposed-merge <file>` for each. Then re-run `/scout-update`." A `knowledge-base/ontology/parser.py.proposed-merge` was left by an older engine; the vault's `parser.py` is the one running, so either merge the sidecar into it as above, or delete the sidecar: this upgrade then retries the merge itself and parks any conflict under `.scout-state/drift/` without blocking.
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
  | python3 -c 'import sys,json;d=json.load(sys.stdin);e=d if isinstance(d,list) else [p for ps in d.get("plugins",{}).values() for p in ps];print(next((p["installPath"] for p in e if p.get("id")=="scout@scout-plugin"),""))' 2>/dev/null)"
[ -n "$NEW_ROOT" ] || NEW_ROOT="$(ls -d "$HOME"/.claude/plugins/cache/scout-plugin/scout/*/ 2>/dev/null | sort -V | tail -1)"
NEW_ROOT="${NEW_ROOT%/}"
[ -n "$NEW_ROOT" ] || { echo "PLUGIN_ROOT_NOT_FOUND"; exit 1; }
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"
"$SCOUTCTL" version
grep -m1 '"version"' "$NEW_ROOT/.claude-plugin/plugin.json"
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

Then report every `vault edit <outcome>: <file> — …` line. These are the vault's own edits to plugin-owned files (scripts, hooks, runners, `render.py`, `parser.py`); none of them blocks a later upgrade:

- `kept` / `merged`: the edit survived (merged into the plugin's update where both changed). Mention that `scoutctl bootstrap drift --patch` turns it into a plugin PR — the plugin repo is public, so the user reviews every added line for personal details first. Not for `parser.py`: the vault grows it on purpose, so its edits are vault content and `--patch` leaves it out.
- `conflict`: the edit and the plugin's update overlap, or (if the line says "could not merge") git could not run. The user's version is still running; the update is parked at `.scout-state/drift/<file>.plugin` with a conflict-marked draft at `<file>.merge`.
  - To keep both: merge by hand into `<file>`, then run `scoutctl bootstrap drift --resolve <file>`. It refuses until the update's lines are in the file.
  - To take the plugin's version: `cp .scout-state/drift/<file>.plugin <file>`.
  - To keep only the user's version: `--resolve <file> --drop-update`. Confirm this with the user first: it drops the plugin's fix.
  - A `parser.py` conflict with no recorded base ("no record of the last render") can't be checked, so plain `--resolve` refuses it; after merging by hand, settle it with `--resolve <file> --drop-update`.
- `error`: the file couldn't be read or written (permissions, a locked file). It was left untouched and everything else upgraded. Report the reason shown.
- `replaced` (with a `backup:` path): this was the vault's first upgrade with no record of the last render, and the file matched no release, so the plugin's version was installed and the vault's copy parked at `.scout-state/drift/<file>.vault`. `scoutctl bootstrap drift --diff` shows what the copy had; dismiss it with `scoutctl bootstrap drift --resolve <file>`, or copy it back over `<file>` to keep its edit (later upgrades then protect it).

- `~/Scout/connector-probes.local.yaml` (custom connector probes) is a user
  file, never templated, so it is preserved untouched across upgrades.

---

## Auto-update nudge

After reporting the upgrade result, check whether the user has auto-updates enabled. Use the engine venv's Python (it ships PyYAML; the system `python3` on stock macOS does not), with `$NEW_ROOT` from the canonical resolver:

```bash
"$NEW_ROOT/.venv/bin/python" - <<'EOF'
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
- If `AUTO_UPDATE_OFF`: tell the user once: "Auto-updates are off — I can turn them on so Scout keeps itself current (sidecar-clean upgrades only; you'll be pinged on conflict; your own edits to Scout's scripts are kept). Want me to enable it?"

If the user agrees, turn it on with `scoutctl config set-auto-update`. It rewrites only the `auto_update` block of `~/Scout/scout-config.yaml` (adding it if absent, keeping an existing channel) and leaves every other line and comment as it was. Do **not** write this file with a pyyaml load-and-dump — that deletes every comment in it.

```bash
NEW_ROOT="$HOME/scout-plugin"
[ -d "$NEW_ROOT/.git" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);e=d if isinstance(d,list) else [p for ps in d.get("plugins",{}).values() for p in ps];print(next((p["installPath"] for p in e if p.get("id")=="scout@scout-plugin"),""))' 2>/dev/null)"
[ -n "$NEW_ROOT" ] || NEW_ROOT="$(ls -d "$HOME"/.claude/plugins/cache/scout-plugin/scout/*/ 2>/dev/null | sort -V | tail -1)"
NEW_ROOT="${NEW_ROOT%/}"
[ -n "$NEW_ROOT" ] || { echo "PLUGIN_ROOT_NOT_FOUND"; exit 1; }
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"
"$SCOUTCTL" config set-auto-update --enabled
```

It prints `auto_update: enabled (channel: stable) — …`. If it exits 1, show the user its `error:` line and suggest setting `auto_update.enabled: true` in the file by hand; don't fall back to rewriting the file yourself.

If the user declines, acknowledge and move on — don't ask again in this session.
