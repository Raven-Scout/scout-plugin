# Connector Probe Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users add custom connector probes in `~/Scout/connector-probes.local.yaml` that survive plugin updates, by merging a shipped registry with a vault-resident overlay (#97).

**Architecture:** Extend the dormant `engine/scout/scripts/connector_probes.py` with a `resolve_registry()` that merges the shipped `templates/connector-probes.yaml` with the optional vault overlay (overlay wins on key collision). Expose it as `scoutctl connectors probe-registry [--json]`, and switch `/scout-setup` Step 2 to call it instead of `cat`-ing the raw shipped file.

**Tech Stack:** Python 3.11, pytest, Typer CLI, PyYAML. Run commands from `engine/` using the repo venv: `../.venv/bin/python -m pytest`.

**Context for the engineer (verified 2026-06-14 against `feat/connector-probe-overlay`, branched from `main` @ `5f31e23`):**
- Spec: `docs/superpowers/specs/2026-06-14-connector-probe-overlay-design.md`.
- `engine/scout/scripts/connector_probes.py` already has `load_registry(path) -> dict[str, Probe]` (single-file parser, fully tested in `engine/tests/unit/test_connector_probe_registry.py`). It raises **`ValueError`** on malformed input — existing tests assert `ValueError`, so leave that behavior intact.
- `Probe` is a frozen dataclass with `.name`, `.kind` (`ProbeKind.MCP_TOOL` / `ProbeKind.BASH`), `.tool_chain: list[str]`, `.bash_command: str`, `.needs_user_input: list[str]`. `ProbeKind.MCP_TOOL.value == "mcp_tool"`, `ProbeKind.BASH.value == "bash"`.
- `ConfigError` is in `scout.errors` (subclass of `ScoutError`).
- Plugin root is `Path(scout.__file__).parent.parent.parent` (verified == `/Users/jordanburger/scout-plugin`); shipped registry is at `<plugin_root>/templates/connector-probes.yaml` (verified exists). Data dir comes from `scout.paths.data_dir()`.
- The `connectors` Typer subapp is built in `engine/scout/cli.py` inside `_register_connectors()` (has `list`, `show`, `reload`, `snapshot`). **Subcommands import their heavy deps inside the function body** — a perf rule enforced by `engine/tests/perf/test_no_heavy_imports.py`. Follow that: import `connector_probes` inside the new command, not at module top.
- **This branch does NOT have the hermetic-test autouse fixture** (that's in the still-open Batch 1 PR #124). Consequence #1: this plan's tests must be self-contained — pass `plugin_root=`/`data_dir=` explicitly, or set `SCOUT_DATA_DIR` — never rely on `HOME` isolation. Consequence #2: a full `pytest tests/` run on this branch shows **2 pre-existing failures** in `test_cli_schedule_subapp.py` (the live-vault slot-count tests Batch 1 fixes). Those are not introduced here; the final-verification task accounts for them.

---

### Task 0: Confirm branch

- [ ] **Step 1: Verify you're on the feature branch**

Run: `git -C /Users/jordanburger/scout-plugin branch --show-current`
Expected: `feat/connector-probe-overlay`. (If not, the spec commit `e30ee4d` is on it; check it out.)

---

### Task 1: `resolve_registry()` — merge shipped + overlay

**Files:**
- Modify: `engine/scout/scripts/connector_probes.py`
- Test: `engine/tests/unit/test_connector_probe_registry.py`

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/unit/test_connector_probe_registry.py` (the file already imports `Path`, `dedent`, `pytest`, and from `scout.scripts.connector_probes`; add `resolve_registry` to that import and add `from scout.errors import ConfigError`):

```python
def _shipped(tmp_path: Path, body: str) -> Path:
    """Write a fake shipped registry under <plugin_root>/templates/."""
    templates = tmp_path / "plugin" / "templates"
    templates.mkdir(parents=True)
    (templates / "connector-probes.yaml").write_text(dedent(body))
    return tmp_path / "plugin"  # plugin_root


def _overlay(data_dir: Path, body: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "connector-probes.local.yaml").write_text(dedent(body))


def test_resolve_shipped_only_when_no_overlay(tmp_path):
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks: []
        """,
    )
    reg = resolve_registry(plugin_root=plugin_root, data_dir=tmp_path / "Scout")
    assert set(reg) == {"slack"}


def test_resolve_overlay_adds_new_connector(tmp_path):
    """The #97 repro: a custom devin probe in the overlay is merged in."""
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: mcp__plugin_slack_slack__slack_read_user_profile
          fallbacks: []
        """,
    )
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        devin:
          primary: mcp__devin__devin_session_search
          fallbacks: []
          needs_user_input:
            - devin_org_token
        """,
    )
    reg = resolve_registry(plugin_root=plugin_root, data_dir=data_dir)
    assert set(reg) == {"slack", "devin"}
    assert reg["devin"].tool_chain == ["mcp__devin__devin_session_search"]
    assert reg["devin"].needs_user_input == ["devin_org_token"]


def test_resolve_overlay_overrides_shipped_key(tmp_path):
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: shipped_tool
          fallbacks: []
        """,
    )
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        slack:
          primary: overlay_tool
          fallbacks: []
        """,
    )
    reg = resolve_registry(plugin_root=plugin_root, data_dir=data_dir)
    assert reg["slack"].tool_chain == ["overlay_tool"]


def test_resolve_empty_overlay_is_noop(tmp_path):
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: t
          fallbacks: []
        """,
    )
    data_dir = tmp_path / "Scout"
    data_dir.mkdir()
    (data_dir / "connector-probes.local.yaml").write_text("")
    reg = resolve_registry(plugin_root=plugin_root, data_dir=data_dir)
    assert set(reg) == {"slack"}


def test_resolve_malformed_overlay_raises_configerror_naming_file(tmp_path):
    plugin_root = _shipped(
        tmp_path,
        """
        slack:
          primary: t
          fallbacks: []
        """,
    )
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        broken:
          fallbacks: []
        """,  # missing 'primary'
    )
    with pytest.raises(ConfigError, match="connector-probes.local.yaml"):
        resolve_registry(plugin_root=plugin_root, data_dir=data_dir)


def test_resolve_missing_shipped_raises_configerror(tmp_path):
    plugin_root = tmp_path / "plugin"  # no templates/ dir
    with pytest.raises(ConfigError, match="connector-probes.yaml"):
        resolve_registry(plugin_root=plugin_root, data_dir=tmp_path / "Scout")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `../.venv/bin/python -m pytest tests/unit/test_connector_probe_registry.py -q -k resolve`
Expected: FAIL — `ImportError: cannot import name 'resolve_registry'`.

- [ ] **Step 3: Implement `resolve_registry`**

In `engine/scout/scripts/connector_probes.py`, add `from scout.errors import ConfigError` to the imports, then append:

```python
def _default_plugin_root() -> Path:
    """Plugin root = the dir that contains the engine venv and templates/.

    Derived from the running package location, mirroring
    install_schedule_plist.resolve_scoutctl_bin().
    """
    import scout

    return Path(scout.__file__).parent.parent.parent


def resolve_registry(
    *,
    plugin_root: Path | None = None,
    data_dir: Path | None = None,
) -> dict[str, Probe]:
    """Merge the shipped probe registry with the optional user overlay.

    Shipped: ``<plugin_root>/templates/connector-probes.yaml`` (required).
    Overlay: ``<data_dir>/connector-probes.local.yaml`` (optional).
    Union of the two; on a key collision the overlay entry wins, letting a
    user repoint a shipped probe or add new connectors that survive plugin
    updates (#97).

    Raises ConfigError (naming the offending file) if the shipped registry
    is missing/invalid or the overlay is invalid. The overlay being absent
    or empty is normal and leaves the shipped set unchanged.
    """
    if plugin_root is None:
        plugin_root = _default_plugin_root()
    if data_dir is None:
        from scout import paths

        data_dir = paths.data_dir()

    shipped_path = plugin_root / "templates" / "connector-probes.yaml"
    if not shipped_path.exists():
        raise ConfigError(f"shipped connector-probes.yaml not found at {shipped_path}")
    try:
        merged = dict(load_registry(shipped_path))
    except ValueError as e:
        raise ConfigError(f"shipped connector-probes.yaml is invalid: {e}") from e

    overlay_path = data_dir / "connector-probes.local.yaml"
    if overlay_path.exists():
        try:
            overlay = load_registry(overlay_path)
        except ValueError as e:
            raise ConfigError(f"{overlay_path.name} is invalid: {e}") from e
        merged.update(overlay)  # overlay wins on key collision

    return merged
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `../.venv/bin/python -m pytest tests/unit/test_connector_probe_registry.py -q`
Expected: PASS (existing single-file `load_registry` tests + the 6 new `resolve_registry` tests).

- [ ] **Step 5: Commit**

```bash
git add engine/scout/scripts/connector_probes.py engine/tests/unit/test_connector_probe_registry.py
git commit -m "feat(connectors): resolve_registry merges shipped probes with vault overlay

Adds resolve_registry(plugin_root, data_dir): shipped
templates/connector-probes.yaml unioned with an optional
~/Scout/connector-probes.local.yaml, overlay winning on key collision.
Malformed/missing files raise ConfigError naming the file. Wires up the
previously-dormant connector_probes loader.

Refs #97

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `scoutctl connectors probe-registry [--json]`

**Files:**
- Modify: `engine/scout/cli.py` (inside `_register_connectors()`)
- Test: `engine/tests/unit/test_cli_connectors_subapp.py` (new)

- [ ] **Step 1: Write the failing CLI test**

Create `engine/tests/unit/test_cli_connectors_subapp.py`:

```python
"""CLI tests for `scoutctl connectors probe-registry`."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from scout.cli import app

runner = CliRunner()


def _overlay(data_dir: Path, body: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "connector-probes.local.yaml").write_text(dedent(body))


def test_probe_registry_json_lists_shipped_connectors():
    result = runner.invoke(app, ["connectors", "probe-registry", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    # Shipped registry ships these (templates/connector-probes.yaml).
    assert "slack" in data
    assert "github" in data
    assert data["slack"]["kind"] == "mcp_tool"
    assert data["github"]["kind"] == "bash"


def test_probe_registry_json_includes_overlay(tmp_path, monkeypatch):
    """A vault overlay adds a connector the wizard will then probe (#97)."""
    data_dir = tmp_path / "Scout"
    _overlay(
        data_dir,
        """
        devin:
          primary: mcp__devin__devin_session_search
          fallbacks: []
        """,
    )
    # SCOUT_DATA_DIR steers resolve_registry's default data_dir at the
    # overlay; the shipped half comes from the real repo templates/.
    monkeypatch.setenv("SCOUT_DATA_DIR", str(data_dir))
    result = runner.invoke(app, ["connectors", "probe-registry", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert "devin" in data
    assert data["devin"]["tool_chain"] == ["mcp__devin__devin_session_search"]
    assert "slack" in data  # shipped still present


def test_probe_registry_default_is_tab_separated():
    result = runner.invoke(app, ["connectors", "probe-registry"])
    assert result.exit_code == 0, result.stdout + result.stderr
    first = next(line for line in result.stdout.splitlines() if line.strip())
    assert not first.startswith("{")  # not JSON
    assert "\t" in first
```

- [ ] **Step 2: Run it to verify it fails**

Run: `../.venv/bin/python -m pytest tests/unit/test_cli_connectors_subapp.py -q`
Expected: FAIL — no such command `probe-registry` (nonzero exit / usage error).

- [ ] **Step 3: Implement the command**

In `engine/scout/cli.py`, inside `_register_connectors()` (alongside the existing `list`/`show`/`reload`/`snapshot` commands), add:

```python
    @connectors_app.command("probe-registry")
    def cli_connectors_probe_registry(
        json_out: bool = typer.Option(
            False, "--json", help="Emit the merged registry as JSON (consumed by /scout-setup)."
        ),
    ) -> None:
        """Print the connector probe registry: shipped union the ~/Scout overlay.

        Merges templates/connector-probes.yaml with an optional
        ~/Scout/connector-probes.local.yaml (overlay wins on key collision)
        so custom connector probes survive plugin updates (#97).
        """
        import json as _json

        from scout.scripts.connector_probes import ProbeKind, resolve_registry

        reg = resolve_registry()
        if json_out:
            out: dict[str, dict] = {}
            for name in sorted(reg):
                p = reg[name]
                entry: dict = {"kind": p.kind.value, "needs_user_input": p.needs_user_input}
                if p.kind is ProbeKind.BASH:
                    entry["bash_command"] = p.bash_command
                else:
                    entry["tool_chain"] = p.tool_chain
                out[name] = entry
            typer.echo(_json.dumps(out, indent=2))
        else:
            for name in sorted(reg):
                p = reg[name]
                primary = p.bash_command if p.kind is ProbeKind.BASH else (p.tool_chain[0] if p.tool_chain else "")
                typer.echo(f"{name}\t{p.kind.value}\t{primary}")
```

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `../.venv/bin/python -m pytest tests/unit/test_cli_connectors_subapp.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the perf guard still passes (lazy import rule)**

Run: `../.venv/bin/python -m pytest tests/perf/test_no_heavy_imports.py -q`
Expected: PASS — `connector_probes`/`yaml` are imported inside the command body, not at module top.

- [ ] **Step 6: Commit**

```bash
git add engine/scout/cli.py engine/tests/unit/test_cli_connectors_subapp.py
git commit -m "feat(cli): scoutctl connectors probe-registry [--json]

Emits the merged shipped+overlay probe registry for /scout-setup to
consume. --json for the wizard; tab-separated default for humans. Import
is in-body to respect the no-heavy-imports startup-latency rule.

Refs #97

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Wizard + docs

**Files:**
- Modify: `commands/scout-setup.md` (Step 2, ~lines 76-98)
- Modify: `commands/scout-update.md` (upgrade notes)

- [ ] **Step 1: Point the wizard at the merged registry**

In `commands/scout-setup.md`, replace the Step 2 heading and the `cat` block. Change the heading line `## Step 2: Connector inventory (read templates/connector-probes.yaml)` to:

```
## Step 2: Connector inventory (merged probe registry)
```

Replace the read block (the fenced `cat ${CLAUDE_PLUGIN_ROOT}/templates/connector-probes.yaml`) with:

````
Read the merged probe registry (shipped probes unioned with the user's
`~/Scout/connector-probes.local.yaml` overlay, if present). Use the
`$SCOUTCTL` resolved in Step 0:

```bash
"$SCOUTCTL" connectors probe-registry --json
```

This emits a JSON object keyed by connector name. Each value has `kind`
(`mcp_tool` or `bash`), plus `tool_chain` (mcp) or `bash_command` (bash),
and `needs_user_input`.
````

Then update the per-entry instructions immediately below so they read off the JSON shape:

```
For each connector in the JSON:
- If `kind` is `bash`, run `bash_command`. Exit code 0 → mark connector enabled.
- If `kind` is `mcp_tool`, try each tool in `tool_chain` in order: call it as an MCP tool; the first that returns data → enabled. If all fail (or the tools aren't present) → disabled.
- For each enabled connector with a non-empty `needs_user_input`, ask the user for those fields and store the values.
```

- [ ] **Step 2: Document the overlay in scout-setup.md**

Immediately after the per-entry instructions (before the checklist summary block), add:

```
> **Custom connectors:** to make `/scout-setup` detect a connector that isn't
> shipped, add an entry to `~/Scout/connector-probes.local.yaml` (same schema
> as the shipped registry) and re-run setup. This overlay lives in your vault
> and survives plugin updates. Example:
>
> ```yaml
> devin:
>   primary: mcp__devin__devin_session_search
>   fallbacks: []
>   needs_user_input:
>     - devin_org_token
> ```
```

- [ ] **Step 3: Note overlay preservation in scout-update.md**

In `commands/scout-update.md`, add a line to the upgrade report/notes section (where sidecar handling is described) stating:

```
- `~/Scout/connector-probes.local.yaml` (custom connector probes) is a user
  file, never templated, so it is preserved untouched across upgrades.
```

(Place it near the existing notes about runner `.bak` files / sidecar merges so the "what survives an upgrade" list is in one place.)

- [ ] **Step 4: Sanity-check the wizard command end-to-end**

```bash
cd /Users/jordanburger/scout-plugin/engine && ../.venv/bin/python -m scout connectors probe-registry --json | head -20
```

Expected: a JSON object including `slack`, `github`, etc. (the shipped set; no overlay on this machine unless you created one).

- [ ] **Step 5: Commit**

```bash
git add commands/scout-setup.md commands/scout-update.md
git commit -m "docs(setup): /scout-setup reads merged probe registry; document overlay

Step 2 now calls 'scoutctl connectors probe-registry --json' instead of
cat-ing the shipped file, so custom probes in
~/Scout/connector-probes.local.yaml are detected. Documents the overlay in
scout-setup and notes it survives upgrades in scout-update.

Refs #97

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Final verification + PR

- [ ] **Step 1: Targeted suite — prove Batch 2 is green**

Run: `../.venv/bin/python -m pytest tests/unit/test_connector_probe_registry.py tests/unit/test_cli_connectors_subapp.py tests/perf/test_no_heavy_imports.py -q`
Expected: all PASS.

- [ ] **Step 2: Full suite — confirm the only failures are the known pre-existing ones**

Run: `../.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5`
Expected: the ONLY failures are `test_cli_schedule_subapp.py::test_schedule_list_json_emits_full_slot_records` and `::test_list_upcoming_large_window_returns_all_slots` — the live-vault slot-count tests fixed by Batch 1 (PR #124), not introduced here. If anything else fails, investigate before proceeding. (Once Batch 1 merges and this branch rebases on main, those two also disappear.)

- [ ] **Step 3: Lint + types**

```bash
../.venv/bin/python -m ruff check scout/ tests/
../.venv/bin/python -m ruff format --check scout/ tests/
../.venv/bin/python -m mypy scout/
```

Expected: all clean. (If `ruff format --check` flags a new file, run `../.venv/bin/python -m ruff format scout/ tests/` and amend the relevant commit.)

- [ ] **Step 4: Push and open the PR**

```bash
git push -u origin feat/connector-probe-overlay
gh pr create --base main --title "feat(connectors): user-extensible probe registry via ~/Scout overlay (#97)" --body "$(cat <<'EOF'
## Why

`templates/connector-probes.yaml` lives in the versioned plugin cache, so any custom connector probe a user adds is silently wiped on the next plugin update (#97 — the only externally-reported issue in the audit backlog). There was no extension point that survived updates.

## What's here

- A vault-resident overlay, `~/Scout/connector-probes.local.yaml` (same schema), merged over the shipped registry — overlay wins on key collision. It's never templated by bootstrap, so it survives every upgrade.
- `resolve_registry(plugin_root, data_dir)` in the previously-dormant `connector_probes.py` performs the merge with loud `ConfigError`s that name the offending file (a malformed overlay is never silently dropped).
- `scoutctl connectors probe-registry [--json]` exposes the merged registry; `/scout-setup` Step 2 now calls it instead of `cat`-ing the raw shipped file, so the merge is deterministic in the engine rather than performed in the LLM's context.
- Docs: the overlay is documented in `/scout-setup` and noted as upgrade-safe in `/scout-update`.

Design spec: `docs/superpowers/specs/2026-06-14-connector-probe-overlay-design.md`.

## Approach

TDD, one commit per layer (engine merge → CLI → wizard/docs). Out of scope by design (YAGNI): disabling shipped probes, auto-creating a stub overlay, a scout-config.yaml block.

## Testing

`resolve_registry` unit tests (shipped-only, overlay-adds, overlay-overrides, empty, malformed→ConfigError, missing-shipped→ConfigError) and CLI `--json` tests (shipped + overlay merge) all pass; `ruff`, `ruff format`, `mypy` clean. NOTE: this branch is cut from pre-Batch-1 `main`, so a full suite run shows the 2 live-vault `test_cli_schedule_subapp.py` failures that PR #124 fixes — not introduced here; they vanish once #124 merges.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes (2026-06-14)

- **Spec coverage:** overlay file (Tasks 1,3) · resolver merge + overlay-wins (Task 1) · CLI subcommand (Task 2) · loud ConfigError naming file (Task 1) · wizard integration (Task 3) · scout-update note (Task 3) · all 8 spec test cases (Tasks 1-2). No gaps.
- **No placeholders:** every code/command step is concrete.
- **Type consistency:** `resolve_registry(*, plugin_root, data_dir)`, `ProbeKind.value`, `Probe.tool_chain`/`.bash_command`/`.needs_user_input` used consistently across tasks and match the verified definitions.
- **Known-state caveat:** the 2 pre-existing `test_cli_schedule_subapp.py` failures are documented in Task 4 so the engineer isn't misled.
