# Connector Probe Overlay — Design (#97)

**Date:** 2026-06-14
**Issue:** [#97](https://github.com/jordanrburger/scout-plugin/issues/97) — `/scout-setup`: connector probe registry edits lost on plugin update (no extensibility for custom connectors)
**Batch:** Plan 9, Batch 2 (`docs/plans/2026-06-10-plan-9-post-audit-hardening.md`)

## Problem

`templates/connector-probes.yaml` is the registry `/scout-setup` reads to detect which connectors a user has. It ships inside the versioned plugin cache (`.../scout-plugin/scout/<version>/templates/`). A user who adds a custom connector entry there (e.g. a `devin` probe pointing at `mcp__devin__devin_session_search`) gets correct detection — until the next plugin update replaces the file and silently drops the entry. There is no extension point that survives updates.

## Current state (as built)

- `/scout-setup` Step 2 (`commands/scout-setup.md:76-87`) does `cat ${CLAUDE_PLUGIN_ROOT}/templates/connector-probes.yaml` and the LLM interprets each entry, probing `primary` then `fallbacks`.
- `engine/scout/scripts/connector_probes.py` has a typed loader `load_registry(path) -> dict[str, Probe]` with full validation, **but it is dormant** — only exercised by `engine/tests/unit/test_connector_probe_registry.py`, never wired into a CLI command or the wizard.
- The shipped registry lives only at repo-root `templates/connector-probes.yaml`. The package `engine/scout/defaults/` holds plists/schedule/config, NOT the probe registry.
- Plugin root is derivable from the package: `Path(scout.__file__).parent.parent.parent` (mirrors `resolve_scoutctl_bin()` in `install_schedule_plist.py:38`), and the shipped registry is at `<plugin_root>/templates/connector-probes.yaml`.
- The `connectors` Typer subapp (`engine/scout/cli.py:_register_connectors`) already has `list`, `show`, `reload`, `snapshot`.

## Design

### 1. Overlay file

`~/Scout/connector-probes.local.yaml` (i.e. `<data_dir>/connector-probes.local.yaml`).

- Same schema as the shipped registry (top-level mapping of `name -> {primary, fallbacks?, command?, needs_user_input?}`).
- Optional. Absent or empty → behavior is identical to today (shipped registry only).
- It is **not** a known shipped-template name, so `scoutctl bootstrap install`/`upgrade` never writes or overwrites it. That is what makes it survive plugin updates — the core requirement of #97.

### 2. Engine — extend `connector_probes.py`

- Leave `load_registry(path) -> dict[str, Probe]` unchanged (already tested as the single-file parser).
- Add a resolver:

  ```python
  def resolve_registry(
      *,
      plugin_root: Path | None = None,
      data_dir: Path | None = None,
  ) -> dict[str, Probe]:
      """Merge the shipped probe registry with the user overlay.

      Shipped: <plugin_root>/templates/connector-probes.yaml (required).
      Overlay: <data_dir>/connector-probes.local.yaml (optional).
      Union; on key collision the overlay entry wins.
      """
  ```

  - `plugin_root` defaults to `Path(scout.__file__).parent.parent.parent`.
  - `data_dir` defaults to `paths.data_dir()`.
  - Shipped registry missing → `ConfigError` naming the expected path (a broken install).
  - Overlay present → parse with the same `load_registry`; merge `{**shipped, **overlay}` (overlay wins per key).
  - Overlay absent → return shipped as-is. Overlay empty/`{}` → no-op merge.

### 3. CLI — `scoutctl connectors probe-registry [--json]`

Added to the existing `connectors` subapp.

- Calls `resolve_registry()`.
- `--json`: emit the merged registry as a single JSON object keyed by connector name, each value carrying `kind` (`mcp_tool`/`bash`), `tool_chain` (mcp) or `bash_command` (bash), and `needs_user_input`. This is the wizard's input — deterministic and parseable.
- Default (no `--json`): human-readable lines, consistent with `connectors list` (e.g. `name<TAB>kind<TAB>primary-or-command`).
- A present-but-broken overlay surfaces as a `ConfigError` to stderr with a nonzero exit — never a silent drop.

### 4. Validation / error handling

The overlay is user-authored; correctness feedback must be loud:

- Malformed overlay (non-mapping top level, entry missing `primary`, `bash` probe missing `command`, `needs_user_input`/`fallbacks` given as a string, unreadable file) → `ConfigError` that names `connector-probes.local.yaml` and the specific problem. **Never silently skipped** — silent-skip would recreate the "my custom connector vanished" failure that motivated this issue.
- Malformed shipped registry → `ConfigError` (plugin bug).
- These reuse the messages `load_registry` already raises; the resolver only needs to attribute which file failed.

### 5. Wizard integration — `commands/scout-setup.md`

- Step 2 changes from `cat ${CLAUDE_PLUGIN_ROOT}/templates/connector-probes.yaml` to:

  ```bash
  "$SCOUTCTL" connectors probe-registry --json
  ```

  The merge now happens deterministically in the engine instead of in the LLM's context.
- Per-entry probing logic (try `primary`, fall through `fallbacks`, run `bash` commands, collect `needs_user_input`) is unchanged.
- Add a short note: custom connector probes go in `~/Scout/connector-probes.local.yaml` (same schema) and survive plugin updates.

### 6. Docs — `commands/scout-update.md`

Add a line to the upgrade report/notes confirming `connector-probes.local.yaml` is preserved across upgrades, closing the loop the issue opened.

### 7. Testing

`engine/tests/unit/test_connector_probe_registry.py` (extend) and a CLI test:

- shipped-only (no overlay) → returns shipped set.
- overlay adds a new connector (the Devin repro: `devin -> mcp__devin__devin_session_search`) → present in the merged set.
- overlay overrides a shipped key (e.g. repoint `slack.primary`) → overlay value wins.
- overlay absent → shipped unchanged.
- overlay empty file → shipped unchanged.
- malformed overlay (missing `primary`) → `ConfigError` naming the overlay file.
- shipped registry missing → `ConfigError`.
- `scoutctl connectors probe-registry --json` emits valid JSON containing both a shipped and an overlay-added connector.

Tests construct `plugin_root` and `data_dir` under `tmp_path` (hermetic; consistent with the new autouse isolation fixture).

## Out of scope (YAGNI)

- Disabling/removing a shipped probe via the overlay (e.g. `granola: null`). The issue is about *adding* connectors; no disable mechanism.
- Auto-creating a stub/example overlay at install time.
- Moving probe wiring into `scout-config.yaml`.

## Acceptance

1. A `connector-probes.local.yaml` entry is detected by `/scout-setup` and still detected after a plugin update.
2. `scoutctl connectors probe-registry --json` returns the union of shipped + overlay, overlay winning on collisions.
3. A malformed overlay fails loudly, naming the file.
4. No existing connector-probe behavior changes when no overlay is present.
