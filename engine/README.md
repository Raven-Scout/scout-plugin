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

## See also

- `../.claude-plugin/plugin.json` — Claude Code plugin manifest
- Scout unification design spec lives in the scout-app repo under
  `docs/superpowers/specs/`.
