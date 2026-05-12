# Scout Engine

Python package providing the `scoutctl` CLI, hooks, runners, and
library code for the Scout productivity system.

## Install (dev)

From this directory:

```bash
uv venv
uv pip install -e ".[dev,full]"
```

Verify:

```bash
scoutctl --help
scoutctl version
scoutctl manifest show
```

## Tests

```bash
pytest tests/
```

## Non-canonical install locations

The engine works at any path on disk, not just `~/scout-plugin/`. Three
install methods are all supported:

- **Claude Code marketplace** — plugin lands under
  `~/.claude/plugins/marketplaces/<marketplace>/scout-plugin/`.
- **Local-plugins / dev tree** — plugin lives in your own directory tree
  (e.g. `~/LOCAL_PLUGINS/scout-plugin/`).
- **Canonical git clone** — plugin at `~/scout-plugin/` directly.

How each layer adapts:

- **Slash commands (`/scout-setup`, `/scout-update`)**: use
  `$CLAUDE_PLUGIN_ROOT` (set by Claude Code at slash-command invocation)
  to locate the plugin, install the venv inside it via
  `scripts/install-venv.sh`, and verify the venv is editable-installed
  from the same checkout — flagged as `VENV_MISMATCH` otherwise.
- **`scoutctl schedule install-plist` / `install-cron`**: write
  `<plugin_root>/.venv/bin/scoutctl` into the plist or cron block, where
  `plugin_root` is derived from the running engine's package location
  (`Path(scout.__file__).parent.parent.parent`). No override knob —
  enforcing this single source of truth eliminates the failure mode
  where the scheduler runs a different scoutctl than the engine the
  user thinks is loaded.
- **`scoutctl bootstrap doctor` / `/scout-status`**: read the installed
  plist's `ProgramArguments[0]` and verify it points at an existing,
  executable scoutctl outside any macOS TCC-protected directory
  (`~/Documents/`, `~/Desktop/`, `~/Downloads/`). Flagged RED with a
  fix-command hint if any check fails.

The legacy `~/scout-plugin/` path is preserved everywhere as a fallback
default — canonical-layout installs see no behavior change.

## See also

- `../.claude-plugin/plugin.json` — Claude Code plugin manifest
- Scout unification design spec lives in the scout-app repo under
  `docs/superpowers/specs/`.
