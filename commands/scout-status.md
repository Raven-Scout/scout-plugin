---
name: scout-status
description: Show the current state of your Scout installation — config, last runs, KB health, pending proposals, and wishlist status.
---

# Scout Status Dashboard

You are displaying a status dashboard for the user's Scout installation. Follow each section below in order. Present the final output as a clean, readable dashboard — not as a series of intermediate steps.

---

## Step 1: Locate the Scout Config

Check for `scout-config.yaml` in these locations, in order:

1. The current working directory (`./scout-config.yaml`)
2. `~/Scout/scout-config.yaml`
3. `~/scout/scout-config.yaml`

```bash
for f in "./scout-config.yaml" "$HOME/Scout/scout-config.yaml" "$HOME/scout/scout-config.yaml"; do
    if [ -f "$f" ]; then echo "FOUND:$f"; break; fi
done
```

**If no config is found:**

Tell the user:

```
No Scout installation found. Run `/scout-setup` to create one.
```

Stop here. Do not proceed with the dashboard.

**If found:** note the path as `SCOUT_CONFIG` and derive `SCOUT_DIR` as its parent directory. Read the config file, then continue.

---

## Step 2: Parse the Config

From the YAML config, extract:

- `instance_name` → `INSTANCE_NAME`
- `user.name` → `USER_NAME`
- `user.email` → `USER_EMAIL`
- `connectors` block → `CONNECTORS` (the map of service → true/false)
- `schedule.briefing` → `BRIEFING_TIME`
- `schedule.consolidation` → `CONSOLIDATION_TIMES`
- `schedule.dreaming` → `DREAMING_TIMES`
- `platform` → `PLATFORM`
- `scout_dir` → `SCOUT_DIR` (use this if present; otherwise use the parent directory of the config file)

---

## Step 3: Gather Data

Run all of the following data-gathering steps before composing the dashboard output.

### 3a. Last Runs (git log)

```bash
git -C "SCOUT_DIR" log --oneline -10
```

Collect the 10 most recent commit messages. Identify the most recent commit that mentions "briefing", "consolidation", "dreaming", "research", "work", and "meta-review" respectively (case-insensitive match on the commit message).

### 3b. KB Health

```bash
find "SCOUT_DIR/knowledge-base" -type f -name "*.md" | sort
```

For each file found, read it and look for a line matching either:
- `**Last verified:**`
- `**Last updated:**`

Extract the date from that line. Compare it to today's date to determine staleness.

Staleness thresholds (apply per-file based on its content — if no priority signal is obvious, default to medium):

| Priority | Stale after |
|----------|-------------|
| High     | 3 days      |
| Medium   | 7 days      |
| Low      | 14 days     |

Files without any date marker count as "unknown" — flag them separately but do not count them as stale.

### 3c. Pending Proposals

Read `SCOUT_DIR/dreaming-proposals.md`.

Look for proposal blocks with status `Pending` or `Approved` (i.e., not yet `Applied` or `Rejected`). A proposal block typically looks like:

```
### Proposal N: ...
**Status:** Pending
```

Count how many Pending and Approved proposals exist.

### 3d. Wishlist

Read every `*.md` file in `SCOUT_DIR/docs/wishlist/` (if the directory exists). Each file is one item with YAML frontmatter — the `status:` field is the state (`open` | `in-progress` | `done` | `dropped`).

Collect the active items: `status: open` (new) and `status: in-progress`. Exclude `done` and `dropped`.

Count `open` vs `in-progress` so the dashboard can report e.g. "3 new, 2 in progress."

### 3e. Scheduler Health (macOS only)

Only run this step if `PLATFORM` is `macos` or if running on macOS (Darwin).

```bash
INSTANCE_LOWER=$(echo "INSTANCE_NAME" | tr '[:upper:]' '[:space:]' | tr ' ' '-' | tr -d '\n' | tr '[:upper:]' '[:lower:]')
launchctl list | grep "$INSTANCE_LOWER"
```

Note: derive `INSTANCE_LOWER` by lowercasing `INSTANCE_NAME` and replacing spaces with hyphens.

Collect the output lines. Each loaded plist appears as a row with PID (or `-`), last exit code, and label. Exit code `0` means healthy; any other value is a warning.

#### Scheduler bin-path validation (macOS)

A loaded plist can still be broken if it points at a non-existent or non-executable scoutctl, or if scoutctl lives under a macOS TCC-protected directory (`~/Documents/`, `~/Desktop/`, `~/Downloads/`). Those failures are invisible to `launchctl list` — the job loads fine but every tick crashes during Python init.

Read the installed schedule-tick plist and extract the scoutctl path it references:

```bash
PLIST="$HOME/Library/LaunchAgents/com.scout.schedule-tick.plist"
if [ -f "$PLIST" ]; then
    SCOUTCTL_BIN=$(/usr/libexec/PlistBuddy -c "Print :ProgramArguments:0" "$PLIST" 2>/dev/null)
    echo "PLIST_SCOUTCTL_BIN=$SCOUTCTL_BIN"
fi
```

Then verify three properties of `SCOUTCTL_BIN`:

1. **Exists** — `[ -e "$SCOUTCTL_BIN" ]`. If not, flag as a broken plist.
2. **Executable** — `[ -x "$SCOUTCTL_BIN" ]`. If not, flag.
3. **Not under a TCC-protected dir** (after resolving symlinks). Resolve with `RESOLVED=$(readlink -f "$SCOUTCTL_BIN" 2>/dev/null || python3 -c "import os,sys;print(os.path.realpath(sys.argv[1]))" "$SCOUTCTL_BIN")`. Then check:
   ```bash
   case "$RESOLVED" in
       "$HOME/Documents/"*|"$HOME/Desktop/"*|"$HOME/Downloads/"*) flag ;;
   esac
   ```

Note: `realpath` on macOS BSD coreutils doesn't support `-f`; the Python one-liner is the portable fallback.

#### Scheduler bin-path validation (Linux)

If `PLATFORM` is `linux`, extract the scoutctl path from the user's crontab managed block:

```bash
SCOUTCTL_BIN=$(crontab -l 2>/dev/null | awk '
    /^# >>> scout-managed >>>$/ { in_block = 1; next }
    /^# <<< scout-managed <<<$/ { in_block = 0; next }
    in_block && /scoutctl schedule tick/ { print $6; exit }
')
```

Then check `[ -e "$SCOUTCTL_BIN" ]` and `[ -x "$SCOUTCTL_BIN" ]`. (TCC restrictions are macOS-specific; skip the protected-dir check on Linux.)

### 3f. Update status

Check the installed plugin version against the latest available, and read the auto-update preference:

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
"$PLUGIN_ROOT/.venv/bin/scoutctl" self-update check || echo "SELF_UPDATE_UNAVAILABLE"
```

Capture the output. The command prints the installed version, the available version, and whether an update is available (e.g. `up to date (<version>)` or `update available: <installed> -> <available>`). If the command exits non-zero or the output contains `SELF_UPDATE_UNAVAILABLE`, treat the update status as unavailable (network or other error).

Then read `auto_update.enabled` from `~/Scout/scout-config.yaml` (absent ⇒ treat as `false`):

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

Store both results for rendering in the dashboard below.

---

## Step 4: Compose and Display the Dashboard

Present the dashboard as follows. Use clean Markdown with headers and lists. Do not show raw shell output — interpret it into human-readable form.

---

```
╔══════════════════════════════════════════════════╗
║           SCOUT STATUS — <INSTANCE_NAME>         ║
╚══════════════════════════════════════════════════╝
```

### Config

| Field    | Value                                |
|----------|--------------------------------------|
| Instance | `<INSTANCE_NAME>`                    |
| User     | `<USER_NAME>` (`<USER_EMAIL>`)       |
| Dir      | `<SCOUT_DIR>`                        |
| Platform | `<PLATFORM>`                         |

### Connected Services

List every connector from the config. Use ✅ for `true` and ❌ for `false`:

```
  ✅ Slack
  ❌ Google Chat
  ❌ Google Calendar
  ✅ Gmail
  ❌ Jira
  ❌ Asana
  ...
```

### Schedule

```
  Briefing:       <BRIEFING_TIME>
  Consolidation:  <CONSOLIDATION_TIMES>
  Dreaming:       <DREAMING_TIMES>
```

---

### Last 10 Runs

Show the 10 most recent git log entries as a simple list (hash + message):

```
  abc1234  briefing [09:03]: ...
  def5678  consolidation [17:00]: ...
  ...
```

Then below the list, highlight:

```
  Last briefing:       <commit message and date/time if identifiable>
  Last consolidation:  <commit message and date/time if identifiable>
  Last dreaming:       <commit message and date/time if identifiable>
  Last research:       <commit message and date/time if identifiable>
  Last work session:   <commit message and date/time if identifiable>
  Last meta-review:    <commit message and date/time if identifiable>
```

If a run type hasn't happened yet, show: `(none found)`

---

### KB Health

```
  Total files:    X
  Up to date:     Y
  Need attention: Z
  Unknown dates:  W
```

If any files need attention, list them:

```
  ⚠️  knowledge-base/people.md  — last updated 12d ago (medium priority, stale after 7d)
  ⚠️  knowledge-base/channels.md — no date marker found
```

If all files are up to date:

```
  ✅ All KB files are fresh.
```

---

### Pending Proposals

If there are Pending or Approved proposals, list their titles and statuses:

```
  • [Pending]  Proposal 1: Add LinkedIn connector
  • [Approved] Proposal 2: Expand Slack channel coverage
```

If none:

```
  No pending proposals.
```

---

### Wishlist

If there are active (non-done) wishlist items, list them:

```
  • [in-progress] Sharing the Scout skill with others internally
  • Custom GUI and TUI for working through action items
  • ...
```

If all items are done or the directory is empty or doesn't exist:

```
  Wishlist is clear.
```

---

### Scheduler Health *(macOS only)*

If not on macOS, omit this section entirely.

For each launchctl entry found:

```
  ✅ com.scout.briefing      — running (PID 12345)
  ✅ com.scout.consolidation  — idle (last exit: 0)
  ⚠️  com.scout.dreaming       — last exit: 1  ← check logs
```

If no entries are found for this instance:

```
  ⚠️  No launchd plists found for '<INSTANCE_NAME_LOWER>'. Scheduler may not be configured.
  Run `/scout-setup` and choose "Reconfigure" to set up scheduling.
```

If the bin-path validation (step 3e sub-checks above) found issues, render one of these blocks immediately under the launchctl table:

```
  ❌ scoutctl path in plist not executable: /Users/foo/old/.venv/bin/scoutctl
      Fix: scoutctl schedule install-plist --force
      (the install command re-derives the canonical path from the loaded
      plugin's venv — no manual override needed)
```

```
  ❌ scoutctl is under ~/Documents (resolved: /Users/foo/Documents/.../scoutctl)
      macOS TCC blocks launchd from reading this directory. Either:
        - Move the plugin out of ~/Documents/, or
        - Grant Full Disk Access to /opt/homebrew/bin/python3.x in
          System Settings → Privacy & Security → Full Disk Access,
      then re-install: scoutctl schedule install-plist --force
```

On Linux, swap "in plist" for "in crontab" and "install-plist" for "install-cron".

If bin-path validation passes, no extra output — the launchctl table alone is enough.

---

### Update status

Using the version info and auto-update flag collected in step 3f, display:

```
  Plugin:       <installed-version>  (up to date (<version>) or update available: <installed> -> <available>)
  Auto-update:  on  /  off
```

If the update check was unavailable (network error or non-zero exit), display:

```
  Plugin:       <installed-version>  (update status: unavailable (couldn't reach marketplace))
  Auto-update:  on  /  off
```

If an update is available, add: "Run `/scout-update` to apply it."

---

### Knowledge Graph Health

Run the ontology parser to check the knowledge graph. Prefer the project venv if present (`SCOUT_DIR/.venv/bin/python`) — falls back to system `python3` then `python`:

```bash
PY="$SCOUT_DIR/.venv/bin/python"
[ -x "$PY" ] || PY=$(command -v python3 || command -v python || echo "")
[ -n "$PY" ] && cd "$SCOUT_DIR" && "$PY" knowledge-base/ontology/parser.py stats 2>/dev/null
[ -n "$PY" ] && cd "$SCOUT_DIR" && "$PY" knowledge-base/ontology/parser.py validate 2>/dev/null
```

If the parser succeeds, show **all four** facets — counts by type, total relationships, validation status, and an orphan summary (counted separately from errors):

```
  Entities:       N (X person, Y project, Z organization, W technology, ...)
  Relationships:  N
  Validation:     0 errors / N errors
  Orphans:        N entities with no relationships
```

Compute the orphan count by re-running `validate` and counting lines that match `Orphaned entity` — these are warnings, not validation errors. List up to 5 most recently modified orphan entity files (use `find SCOUT_DIR/knowledge-base/ontology/entities -name '*.md' -newer ...` or `ls -t`) when the count is non-zero, so the user can see what's drifting:

```
  ⚠️  Orphans (top 5 by recency):
    - knowledge-base/ontology/entities/people/<name>.md  — <YYYY-MM-DD>
    ...
```

If `Entities: 0` even though the parser ran, surface the gap:

```
  ⚠️  Parser runs but graph is empty. Likely cause: KB markdown files lack YAML frontmatter
  with `name:` and `type:` keys. Run a dreaming session to auto-seed entities from prose,
  or re-run scripts/seed-entities-from-people.py manually.
```

If the parser isn't set up (file doesn't exist) OR the runtime is unavailable (no python3 / yaml module missing), show:

```
  Knowledge graph not configured. Run /scout-setup to set up the ontology.
  (Tip: if python3 is present but yaml is missing — `pip3 install pyyaml` or use a venv.)
```

---

### Budget Tracking

Check if a usage tracker file exists at `SCOUT_DIR/.scout-logs/usage-tracker.jsonl`.

If it exists, parse the last 24 hours of entries and show:

```
  Sessions today:      N (briefing: X, consolidation: Y, dreaming: Z, research: W)
  Estimated spend:     $X.XX (last 24h)
  Last failure:        <time and type, or "none">
  Rate limit events:   N in last 24h
```

If it doesn't exist:

```
  No usage tracking data yet. Cost tracking begins after the first run.
```

---

End the dashboard with a one-line summary:

```
Scout is healthy. / Scout needs attention — Z KB files stale, X proposals pending.
```

Choose the appropriate variant based on findings. "Needs attention" if any of: KB files stale, proposals pending with Approved status, scheduler entries missing or with non-zero exit codes.
