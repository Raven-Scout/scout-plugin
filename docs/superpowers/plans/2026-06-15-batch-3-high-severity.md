# Batch 3 — High-Severity Audit Tail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 8 high-severity audit bugs (#41, #42, #43, #45, #46, #52, #53, #62) in the Scout engine, each with a regression test.

**Architecture:** Eight independent defects across separate modules (backfill, connectors, CLI bootstrap-upgrade, KB ontology, TUI spawn, kb_pre_filter hook, bootstrap backups). #41 and #42 share `backfill.py` and are fixed together. Each fix is TDD'd with a regression test; per the Plan 9 batch policy, anything touching `bootstrap.py` or the TUI gets a unit test + type hints. One branch (`fix/batch-3-high-severity`), one commit per task.

**Tech Stack:** Python 3.11, pytest, Typer, PyYAML, Textual (optional extra). Run from `engine/`: `../.venv/bin/python -m pytest`.

**Context for the engineer (verified 2026-06-15 against `fix/batch-3-high-severity` == `origin/main` @ `ae6f257`):**
- `scout.errors` exit-coded exceptions: `ScoutError`(1), `ConfigError`(10), `DataDirError`(11), `KBError`(20), `ActionItemError`(21), `ExternalProcessError`(30). No `KBSchemaError` yet (Task 4 adds it).
- `engine/tests/conftest.py` has an **autouse** `_hermetic_env` fixture: every test gets `HOME` pointed at a tmp dir and all `SCOUT_*` env vars scrubbed. A `fake_data_dir` fixture provides a real data-dir tree on demand.
- `cli.py` already maps `ScoutError` exit codes at command boundaries (see the `except _ConfigError as e: raise typer.Exit(code=_ConfigError.exit_code)` idiom around `cli.py:901`). `ConfigError` is imported at the top of `cli.py`.
- Branch is cut from current `main`, which already includes Batch 1 (hermetic fixture, etc.), Batch 2, and the lock fix — so a full `pytest tests/` here should be **fully green** before you start. If it isn't, stop and report.

---

### Task 0: Confirm clean baseline

- [ ] **Step 1: Verify branch and a green baseline**

Run: `git -C ~/scout-plugin branch --show-current` → expect `fix/batch-3-high-severity`.
Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/ -q 2>&1 | tail -2`
Expected: all pass, 0 failed (Batch 1's hermetic fixture means no live-vault flakes). If anything fails, STOP and report before making changes.

---

### Task 1: backfill — single-read parse (#41) + durable id-map registration (#42)

Two defects in `backfill_prefixes`: (#41) `parse_file(target)` and a separate `target.read_text()` are two reads — a file change between them maps `line_number` to the wrong raw line and corrupts unrelated content; (#42) if `add_prefix_to_line` raises mid-loop, the prefixes already written to the file are never registered in the id-map (registration runs only after the loop), so retries mint duplicates and `--by-id` breaks.

**Files:**
- Modify: `engine/scout/action_items/parser.py` (add a lines-based entry point)
- Modify: `engine/scout/action_items/backfill.py:44-91`
- Test: `engine/tests/unit/test_action_items_backfill.py`, `engine/tests/unit/test_action_items_parser.py`

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/unit/test_action_items_backfill.py` (it already imports `backfill_prefixes`; add any missing imports: `from pathlib import Path`, `import pytest`, `from scout.id_map import IdMap`):

```python
def test_backfill_registers_prefixes_written_before_a_mid_loop_failure(tmp_path, monkeypatch):
    """#42: if add_prefix_to_line raises partway through, every prefix already
    written to the file must still be registered+saved in the id-map, so a
    retry never re-mints a prefix that's already on disk."""
    data_dir = tmp_path
    items_dir = data_dir / "action-items"
    items_dir.mkdir()
    target = items_dir / "action-items-2026-06-15.md"
    target.write_text("## To Do\n- [ ] first task\n- [ ] second task\n")

    import scout.action_items.backfill as bf

    real_add = bf.add_prefix_to_line
    calls = {"n": 0}

    def flaky_add(target, *, line_number, prefix):
        calls["n"] += 1
        if calls["n"] == 2:  # second write blows up
            raise OSError("disk gremlin")
        return real_add(target, line_number=line_number, prefix=prefix)

    monkeypatch.setattr(bf, "add_prefix_to_line", flaky_add)

    with pytest.raises(OSError):
        bf.backfill_prefixes(target=target, data_dir=data_dir)

    # Whatever made it onto disk must be in the id-map.
    on_disk_prefixes = {
        m.group(1)
        for line in target.read_text().splitlines()
        if (m := __import__("re").search(r"\[#([A-Z0-9]{2,8})\]", line))
    }
    id_map = IdMap.load(data_dir)
    registered = id_map.in_use_prefixes()
    assert on_disk_prefixes, "expected at least one prefix written before the failure"
    assert on_disk_prefixes <= registered, (
        f"prefixes on disk {on_disk_prefixes} not all registered {registered}"
    )


def test_backfill_uses_single_file_read(tmp_path, monkeypatch):
    """#41: candidate selection must parse the same bytes it filters against —
    one read, not a parse_file read plus a separate read_text."""
    data_dir = tmp_path
    items_dir = data_dir / "action-items"
    items_dir.mkdir()
    target = items_dir / "action-items-2026-06-15.md"
    target.write_text("## To Do\n- [ ] only task\n")

    reads = {"n": 0}
    real_read_text = Path.read_text

    def counting_read_text(self, *a, **k):
        if self == target:
            reads["n"] += 1
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    bf_plan = __import__("scout.action_items.backfill", fromlist=["backfill_prefixes"]).backfill_prefixes(
        target=target, data_dir=data_dir, dry_run=True
    )
    assert len(bf_plan) == 1
    # dry-run must read the target exactly once for candidate selection
    # (parse + raw-line filter share that read), not twice.
    assert reads["n"] == 1, f"expected a single read of the target, got {reads['n']}"
```

Append to `engine/tests/unit/test_action_items_parser.py` (it already exercises `parse_file`; add this):

```python
def test_parse_lines_matches_parse_file(tmp_path):
    """parse_lines(text.splitlines()) yields the same items as parse_file —
    so callers that already hold the file's bytes can parse without a 2nd read."""
    from scout.action_items.parser import parse_file, parse_lines

    md = "# T\n\n## 🔴 Urgent\n\n- [ ] alpha\n- [x] beta\n"
    f = tmp_path / "action-items-2026-06-15.md"
    f.write_text(md, encoding="utf-8")

    from_file = parse_file(f)
    from_lines = parse_lines(md.splitlines())
    assert [(i.title, i.status, i.line_number) for i in from_lines] == [
        (i.title, i.status, i.line_number) for i in from_file
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_action_items_parser.py -q -k parse_lines tests/unit/test_action_items_backfill.py -q -k "single_file_read or mid_loop"`
Expected: `parse_lines` test errors (`cannot import name 'parse_lines'`); `single_file_read` fails (2 reads); `mid_loop` fails (registered set missing the first prefix).

- [ ] **Step 3: Add `parse_lines` to the parser (single-read seam for #41)**

In `engine/scout/action_items/parser.py`, refactor `parse_action_items` so its body becomes a lines-based function and the file entry reads once then delegates. Replace lines 77-87 (the `def parse_action_items` header + the `if not filepath.exists(): return []` + `lines = filepath.read_text(...)` preamble) so the structure is:

```python
def parse_action_items(filepath: Path) -> list[ActionItem]:
    """Parse an action items markdown file into a list of ActionItem objects.

    Returns items sorted by: in_progress first, then open, then watching, then done.
    Within each status group, sorted by priority (🔴 > 🟡 > 🟢 > none).
    """
    if not filepath.exists():
        return []
    return parse_lines(filepath.read_text(encoding="utf-8").splitlines())


def parse_lines(lines: list[str]) -> list[ActionItem]:
    """Parse already-read markdown lines into ActionItem records.

    Lets a caller that already holds the file's bytes (e.g. backfill, which
    must filter against the same raw lines) parse without a second read,
    closing the parse-vs-reread TOCTOU window (#41).
    """
    items: list[ActionItem] = []
```

i.e. the existing body from `items: list[ActionItem] = []` (old line 86) onward stays exactly as-is but now lives under `parse_lines`, and the old `lines = filepath.read_text(...).splitlines()` line is removed (the data now arrives as the `lines` parameter). Do NOT change any parsing logic below that point.

- [ ] **Step 4: Rewrite `backfill_prefixes` to read once and register durably**

In `engine/scout/action_items/backfill.py`, change the import on line 41 and the body lines 44-91:

```python
    from scout.action_items.parser import parse_lines
    from scout.action_items.writer import add_prefix_to_line

    if not target.exists():
        return []
    raw_lines = target.read_text(encoding="utf-8").splitlines()
    items = parse_lines(raw_lines)

    def _has_checkbox(line_number: int) -> bool:
        idx = line_number - 1
        if not 0 <= idx < len(raw_lines):
            return False
        return _CHECKBOX_RE.match(raw_lines[idx]) is not None

    candidates = [i for i in items if i.status == "open" and i.short_prefix is None and _has_checkbox(i.line_number)]
    if not candidates:
        return []

    id_map = IdMap.load(data_dir)
    in_use = id_map.in_use_prefixes()

    plan: list[tuple[int, str, str]] = []
    for item in candidates:
        prefix = new_short_prefix(exclude=in_use)
        in_use.add(prefix)
        plan.append((item.line_number, prefix, item.title))

    if dry_run:
        return plan

    # Apply line edits from the bottom up so earlier line numbers don't shift
    # under us. Register each prefix in the id-map immediately after its write
    # succeeds, and save() in a finally — so a mid-loop failure still leaves the
    # id-map consistent with whatever reached disk (#42). Without this, a
    # partial write desyncs the map and a retry re-mints live prefixes.
    try:
        for line_no, prefix, title in sorted(plan, key=lambda p: p[0], reverse=True):
            add_prefix_to_line(target, line_number=line_no, prefix=prefix)
            id_map.register(
                IdMapEntry(
                    ulid=new_ulid(),
                    short_prefix=prefix,
                    last_title=title,
                    last_file=target.name,
                    last_line=line_no,
                )
            )
    finally:
        id_map.save()
    return plan
```

- [ ] **Step 5: Run the new tests, then the affected suites**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_action_items_backfill.py tests/unit/test_action_items_parser.py tests/integration/test_post_session_backfill.py -q`
Expected: all PASS. Then `../.venv/bin/python -m ruff check scout/action_items/backfill.py scout/action_items/parser.py tests/unit/test_action_items_backfill.py tests/unit/test_action_items_parser.py && ../.venv/bin/python -m ruff format --check scout/action_items/parser.py scout/action_items/backfill.py && ../.venv/bin/python -m mypy scout/action_items/parser.py scout/action_items/backfill.py` — all clean.

- [ ] **Step 6: Commit**

```bash
git add engine/scout/action_items/parser.py engine/scout/action_items/backfill.py engine/tests/unit/test_action_items_backfill.py engine/tests/unit/test_action_items_parser.py
git commit -m "fix(action-items): single-read backfill + durable id-map registration

#41: backfill now parses the same bytes it filters against (parse_lines on
one read) instead of parse_file + a separate read_text, closing the window
where a concurrent edit maps a line number onto the wrong raw line.
#42: register each prefix immediately after its write and save() in a
finally, so a mid-loop failure leaves the id-map consistent with what
reached disk (no duplicate-mint on retry).

Fixes #41
Fixes #42

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: connectors._load_yaml leaks OSError (#43)

`_load_yaml` converts `yaml.YAMLError` to `ConfigError` but lets `OSError`/`PermissionError`/`FileNotFoundError` (dangling symlink, unreadable overlay, race after the `exists()` check) escape as a raw traceback, breaking the CLI exit-code contract. The sibling `schedule.py:_load_yaml` already guards OS errors.

**Files:**
- Modify: `engine/scout/connectors.py:148-156`
- Test: `engine/tests/unit/test_connectors_yaml.py`

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/unit/test_connectors_yaml.py` (add `import pytest` and `from scout.errors import ConfigError` if absent):

```python
def test_load_yaml_unreadable_file_raises_configerror(tmp_path):
    """An OS-level read failure on a connectors YAML must surface as
    ConfigError, not a raw OSError traceback (#43)."""
    from scout.connectors import _load_yaml

    bad = tmp_path / "connectors.yaml"
    bad.symlink_to(tmp_path / "does-not-exist.yaml")  # dangling symlink → OSError on open
    with pytest.raises(ConfigError):
        _load_yaml(bad)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_connectors_yaml.py -q -k unreadable`
Expected: FAIL — raises `FileNotFoundError`/`OSError`, not `ConfigError`.

- [ ] **Step 3: Add the OSError guard**

In `engine/scout/connectors.py`, change `_load_yaml` (lines 148-156) to add an `OSError` branch before the `YAMLError` branch:

```python
def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise ConfigError(f"connectors yaml at {path} could not be read: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"connectors yaml at {path} is malformed: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"connectors yaml at {path} is not a mapping")
    return data
```

- [ ] **Step 4: Run the connectors tests**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_connectors_yaml.py -q && ../.venv/bin/python -m ruff check scout/connectors.py tests/unit/test_connectors_yaml.py && ../.venv/bin/python -m mypy scout/connectors.py`
Expected: all PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add engine/scout/connectors.py engine/tests/unit/test_connectors_yaml.py
git commit -m "fix(connectors): _load_yaml wraps OSError as ConfigError

A dangling symlink / unreadable / permission-denied connectors overlay
leaked a raw OSError traceback instead of a typed ConfigError (exit 10),
breaking the CLI error contract. Mirrors schedule.py's _load_yaml.

Fixes #43

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: bootstrap-upgrade reads scout-config.yaml unguarded (#45)

`cli_bootstrap_upgrade` does `_yaml.safe_load(cfg_path.read_text())` — no `encoding=` (UnicodeDecodeError under `LANG=C` with non-ASCII config) and no `yaml.YAMLError` guard (a half-written config). Both escape as exit 70 instead of the correct ConfigError exit 10.

**Files:**
- Modify: `engine/scout/cli.py` (the `existing = _yaml.safe_load(cfg_path.read_text()) or {}` line in `cli_bootstrap_upgrade`, ~line 1025)
- Test: `engine/tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/unit/test_cli.py` (it already constructs a `CliRunner`; add `from scout.errors import ConfigError` and any missing imports — `from typer.testing import CliRunner`, `from scout.cli import app`):

```python
def test_bootstrap_upgrade_malformed_config_exits_configerror(tmp_path, monkeypatch):
    """A malformed scout-config.yaml on the upgrade path must exit with
    ConfigError's code (10), not an internal-error code (#45)."""
    from typer.testing import CliRunner

    from scout.cli import app
    from scout.errors import ConfigError

    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("instance: [unclosed\n")  # invalid YAML
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))

    runner = CliRunner()
    result = runner.invoke(app, ["bootstrap", "upgrade"])
    assert result.exit_code == ConfigError.exit_code, result.stdout + result.stderr
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_cli.py -q -k bootstrap_upgrade_malformed`
Expected: FAIL — exit code is not 10 (raw YAMLError → Typer maps to 1, or the internal-error code).

- [ ] **Step 3: Guard the read+parse**

In `engine/scout/cli.py`, locate in `cli_bootstrap_upgrade` the lines:

```python
        import yaml as _yaml

        existing = _yaml.safe_load(cfg_path.read_text()) or {}
```

Replace the `existing = ...` line with a guarded read (keep the `import yaml as _yaml` line):

```python
        try:
            existing = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except (_yaml.YAMLError, UnicodeDecodeError) as e:
            typer.echo(f"scout-config.yaml is malformed: {e}", err=True)
            raise typer.Exit(code=ConfigError.exit_code) from e
```

(`ConfigError` is already imported at the top of `cli.py`; `typer` is in scope.)

- [ ] **Step 4: Run the CLI tests**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_cli.py -q && ../.venv/bin/python -m ruff check scout/cli.py tests/unit/test_cli.py && ../.venv/bin/python -m mypy scout/cli.py`
Expected: all PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add engine/scout/cli.py engine/tests/unit/test_cli.py
git commit -m "fix(bootstrap): guard scout-config.yaml read on upgrade (encoding + YAML)

cli_bootstrap_upgrade read the config with no encoding= (UnicodeDecodeError
under LANG=C with non-ASCII values) and no YAMLError guard (half-written
config). Both escaped as an internal-error exit instead of ConfigError's
exit 10. Read as utf-8 and map both to ConfigError's code.

Fixes #45

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: KB ontology crashes on user-customizable schema (#46)

`KnowledgeGraph._load_schema` uses bare `open()` + `yaml.safe_load` with no guard (missing/malformed `schema.yaml` crashes the constructor, taking down the TUI/CLI), and `validate()` does `type_def["properties"]` — a `KeyError` when a user defines an entity type with no `properties:` key.

**Files:**
- Modify: `engine/scout/errors.py` (add `KBSchemaError`)
- Modify: `engine/scout/kb/ontology.py` (`_load_schema` ~37-39, `validate` ~141)
- Test: `engine/tests/unit/test_kb_ontology.py`

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/unit/test_kb_ontology.py` (it imports from `scout.kb.ontology`; add `import pytest` and `from scout.errors import KBError`):

```python
def test_missing_schema_raises_kberror(tmp_path):
    """A missing schema.yaml must raise a typed KBError, not a raw OSError
    out of the KnowledgeGraph constructor (#46)."""
    from scout.kb.ontology import KnowledgeGraph

    with pytest.raises(KBError):
        KnowledgeGraph(kb_root=tmp_path, schema_path=tmp_path / "nope.yaml")


def test_malformed_schema_raises_kberror(tmp_path):
    """A syntactically invalid schema.yaml must raise KBError (#46)."""
    from scout.kb.ontology import KnowledgeGraph

    bad = tmp_path / "schema.yaml"
    bad.write_text("entity_types: [unclosed\n")
    with pytest.raises(KBError):
        KnowledgeGraph(kb_root=tmp_path, schema_path=bad)


def test_validate_entity_type_without_properties_key(tmp_path):
    """An entity type defined with no `properties:` key must not raise
    KeyError in validate() (#46)."""
    from scout.kb.ontology import KnowledgeGraph

    schema = tmp_path / "schema.yaml"
    schema.write_text("entity_types:\n  task: {}\n")
    kg = KnowledgeGraph(kb_root=tmp_path, schema_path=schema)
    # Should classify/validate without raising; result is a list of error strings.
    errors = kg.validate()
    assert isinstance(errors, list)
```

NOTE: confirm the `KnowledgeGraph.__init__` parameter names by reading `engine/scout/kb/ontology.py` (the test uses `kb_root=` and `schema_path=`). If the constructor uses different names, adjust the calls to match — do NOT change the constructor signature. Also confirm `validate()` takes no required args; if it does, pass the minimal valid arguments the existing tests in this file use.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_kb_ontology.py -q -k "missing_schema or malformed_schema or without_properties"`
Expected: missing/malformed raise `OSError`/`YAMLError` (not `KBError`); `without_properties` raises `KeyError`.

- [ ] **Step 3: Add `KBSchemaError` to errors.py**

In `engine/scout/errors.py`, after the `KBError` class, add:

```python
class KBSchemaError(KBError):
    """Raised when the knowledge-base schema YAML is missing or malformed."""
```

(It inherits `KBError`'s exit code, so the CLI/TUI surface is unchanged; it just gives callers a narrower type to catch.)

- [ ] **Step 4: Guard `_load_schema` and `validate`**

In `engine/scout/kb/ontology.py`, add `KBSchemaError` to the `from scout.errors import ...` line (and `KBError` if not already imported). Replace `_load_schema` (currently ~lines 37-39):

```python
    def _load_schema(self) -> dict[str, Any]:
        try:
            with open(self.schema_path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except OSError as e:
            raise KBSchemaError(f"KB schema not found at {self.schema_path}: {e}") from e
        except yaml.YAMLError as e:
            raise KBSchemaError(f"KB schema at {self.schema_path} is malformed: {e}") from e
```

And in `validate`, change the bare subscript (currently ~line 141) from `type_def["properties"].get("required", [])` to:

```python
            for prop in type_def.get("properties", {}).get("required", []):
```

- [ ] **Step 5: Run the KB tests**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_kb_ontology.py -q && ../.venv/bin/python -m ruff check scout/kb/ontology.py scout/errors.py tests/unit/test_kb_ontology.py && ../.venv/bin/python -m mypy scout/kb/ontology.py scout/errors.py`
Expected: all PASS / clean.

- [ ] **Step 6: Commit**

```bash
git add engine/scout/errors.py engine/scout/kb/ontology.py engine/tests/unit/test_kb_ontology.py
git commit -m "fix(kb): typed errors for missing/malformed schema; tolerate no-properties types

_load_schema wrapped bare open()+safe_load with no guard, so a missing or
malformed user schema.yaml crashed the KnowledgeGraph constructor (and the
TUI). validate() did type_def['properties'] — a KeyError when an entity
type has no properties: key. Add KBSchemaError(KBError), wrap the load
(utf-8), and use .get('properties', {}).

Fixes #46

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: TUI spawn — shell-injection + blocking Popen (#52)

`spawn_session` interpolates the action-item title into an AppleScript `do script` string literal escaping only `"` — single quotes, backticks, and `$()` in a title break the literal / inject shell. And `subprocess.Popen` runs on the Textual event-loop thread, freezing the UI. Fix the injection by (a) extracting the command/AppleScript building into a `textual`-free module with correct AppleScript escaping + a sanitized session-name slug (so it's unit-testable in CI without Textual), and (b) offloading the `Popen` to a worker thread.

**Files:**
- Create: `engine/scout/tui/spawn_cmd.py` (pure, no Textual import)
- Modify: `engine/scout/tui/screens/spawn.py`
- Test: `engine/tests/unit/test_tui_spawn_cmd.py` (new; no Textual dependency)

- [ ] **Step 1: Write the failing tests**

Create `engine/tests/unit/test_tui_spawn_cmd.py`:

```python
"""Unit tests for the Textual-free spawn command builder (#52).

Lives outside scout.tui.screens.spawn so it runs in CI without `textual`.
"""

from __future__ import annotations

from scout.tui.spawn_cmd import applescript_literal, build_terminal_applescript


def test_applescript_literal_escapes_backslash_and_quote():
    assert applescript_literal('a"b\\c') == '"a\\"b\\\\c"'


def test_build_terminal_applescript_quotes_nasty_title_and_prompt():
    title = 'pwn"; rm -rf ~ #`whoami`$(id)'
    prompt = 'do the "thing" now'
    cmd, script = build_terminal_applescript(title=title, prompt=prompt)

    # Session name is a safe slug — no quotes/backticks/spaces/$ leak through.
    assert all(c.isalnum() or c == "-" for c in cmd.split('"')[1]) or "scout-" in cmd
    # The AppleScript `do script` argument is a single well-formed double-quoted
    # literal: every interior double-quote is backslash-escaped.
    inner = script.split("do script ", 1)[1].strip().splitlines()[0]
    assert inner.startswith('"') and inner.endswith('"')
    body = inner[1:-1]
    # No UNescaped double-quote inside the literal body.
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        assert body[i] != '"', f"unescaped quote in AppleScript literal: {inner!r}"
        i += 1


def test_build_terminal_applescript_prompt_is_shell_quoted():
    cmd, _ = build_terminal_applescript(title="t", prompt="a b; c")
    # The prompt reaches the shell as a single quoted argument.
    assert "'a b; c'" in cmd or '"a b; c"' in cmd
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_tui_spawn_cmd.py -q`
Expected: FAIL — `ModuleNotFoundError: scout.tui.spawn_cmd`.

- [ ] **Step 3: Create the pure builder module**

Create `engine/scout/tui/spawn_cmd.py`:

```python
"""Pure (Textual-free) builders for the TUI session spawner.

Kept separate from scout.tui.screens.spawn so the security-critical
command/AppleScript construction is unit-testable without importing Textual,
and so escaping is centralized rather than ad-hoc string interpolation (#52).
"""

from __future__ import annotations

import re
import shlex

_SLUG_RE = re.compile(r"[^A-Za-z0-9-]+")


def session_slug(title: str) -> str:
    """A shell- and AppleScript-safe session name from an action-item title.

    Collapses everything outside [A-Za-z0-9-] to a single dash, so no quote,
    backtick, space, or shell metacharacter from the title can reach the
    command line. Bounded to 30 chars.
    """
    slug = _SLUG_RE.sub("-", title).strip("-")
    return (slug[:30] or "session").strip("-") or "session"


def applescript_literal(s: str) -> str:
    """Return `s` as a double-quoted AppleScript string literal.

    AppleScript string literals escape backslash and double-quote with a
    backslash; nothing else is special inside them. Escape backslash FIRST.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_terminal_applescript(*, title: str, prompt: str) -> tuple[str, str]:
    """Build (shell_command, applescript) for launching a Claude session.

    `shell_command` is fully shell-quoted (prompt via shlex.quote, session
    name via a safe slug). `applescript` wraps `cd ~/Scout && <shell_command>`
    in a properly escaped AppleScript string literal so no title content can
    break the `do script` argument or inject shell.
    """
    name = f"scout-action-{session_slug(title)}"
    cmd = f"claude --name {shlex.quote(name)} -p {shlex.quote(prompt)}"
    do_script_arg = applescript_literal(f"cd ~/Scout && {cmd}")
    applescript = (
        'tell application "Terminal"\n'
        "    activate\n"
        f"    do script {do_script_arg}\n"
        "end tell"
    )
    return cmd, applescript
```

- [ ] **Step 4: Run the builder tests**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_tui_spawn_cmd.py -q`
Expected: all PASS.

- [ ] **Step 5: Rewire `spawn.py` to use the builder + offload Popen**

In `engine/scout/tui/screens/spawn.py`: remove the `import shlex` (no longer used there) and replace `spawn_session` (lines 45-70) and `action_confirm` (lines 118-120):

```python
def spawn_session(item: ActionItem) -> str:
    """Spawn a Claude Code session in a new Terminal window (macOS).

    Returns the shell command that was launched. Blocking (fork+exec); call
    off the UI thread — see SpawnConfirmScreen.action_confirm.
    """
    from scout.tui.spawn_cmd import build_terminal_applescript

    prompt = build_prompt(item)
    cmd, apple_script = build_terminal_applescript(title=item.title, prompt=prompt)

    subprocess.Popen(
        ["osascript", "-e", apple_script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return cmd
```

and:

```python
    def action_confirm(self) -> None:
        # osascript fork+exec blocks ~tens of ms; run it off the Textual event
        # loop so the UI doesn't freeze, then dismiss (#52).
        self.run_worker(lambda: spawn_session(self.item), thread=True)
        self.dismiss(True)
```

- [ ] **Step 6: Verify the smoke test still imports and run the builder tests once more**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_tui_spawn_cmd.py tests/unit/test_tui_smoke.py -q && ../.venv/bin/python -m ruff check scout/tui/spawn_cmd.py scout/tui/screens/spawn.py tests/unit/test_tui_spawn_cmd.py && ../.venv/bin/python -m ruff format --check scout/tui/spawn_cmd.py scout/tui/screens/spawn.py && ../.venv/bin/python -m mypy scout/tui/spawn_cmd.py`
Expected: builder tests PASS; `test_tui_smoke.py` PASSES or SKIPS (textual not installed → skip is fine); ruff/mypy clean. (mypy on `screens/spawn.py` may be skipped per existing `[[tool.mypy.overrides]]` for `textual.*`; only assert mypy clean on `spawn_cmd.py`.)

- [ ] **Step 7: Commit**

```bash
git add engine/scout/tui/spawn_cmd.py engine/scout/tui/screens/spawn.py engine/tests/unit/test_tui_spawn_cmd.py
git commit -m "fix(tui): safe spawn command building + non-blocking launch

The spawner interpolated the action-item title into an AppleScript do-script
literal escaping only double-quotes — single quotes, backticks, and \$() in a
title broke the literal / injected shell. And Popen ran on the Textual event
loop, freezing the UI. Extract a Textual-free builder (CI-testable) that
slugs the session name and escapes the AppleScript literal correctly, and
run osascript on a worker thread.

Fixes #52

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: kb_pre_filter — naive datetime DST + symlink-following rglob (#53)

`parse_date` returns a naive datetime (ET tzinfo is attached later in `classify`), so any caller that forgets drifts an hour across DST. And `discover_kb_files` uses `Path.rglob("*.md")`, which follows symlinks — a symlink loop in the KB dir recurses forever and hangs session startup.

**Files:**
- Modify: `engine/scout/hooks/kb_pre_filter.py` (`parse_date` ~178-183, `classify` ~248, `discover_kb_files` ~196-197)
- Test: `engine/tests/unit/test_hooks_kb_pre_filter.py`

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/unit/test_hooks_kb_pre_filter.py` (add `import threading` if absent):

```python
def test_parse_date_returns_tz_aware_et():
    """#53: parse_date must return an ET-aware datetime so callers can't drift
    across DST by forgetting to attach tzinfo."""
    from scout.hooks.kb_pre_filter import ET, parse_date

    dt = parse_date("2026-06-15")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset() == ET.utcoffset(dt.replace(tzinfo=None))


def test_discover_kb_files_does_not_follow_symlink_loop(tmp_path):
    """#53: a symlink loop in the KB dir must not hang discovery."""
    from scout.hooks.kb_pre_filter import discover_kb_files

    kb = tmp_path / "knowledge-base"
    kb.mkdir()
    (kb / "real.md").write_text("# real\n")
    # Create a self-referential symlink loop: kb/loop -> kb
    (kb / "loop").symlink_to(kb, target_is_directory=True)

    result: list = []
    error: list = []

    def run():
        try:
            result.append(discover_kb_files(tmp_path))
        except Exception as e:  # noqa: BLE001 - test harness
            error.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "discover_kb_files hung on a symlink loop"
    assert not error, f"discover_kb_files raised: {error}"
```

NOTE: confirm `discover_kb_files`'s parameter — the test passes the vault root `tmp_path` and expects it to look under `knowledge-base/`. Read the current signature; if it takes the kb root directly, pass `kb` instead. Match the existing call convention used elsewhere in this test file.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_hooks_kb_pre_filter.py -q -k "tz_aware or symlink_loop"`
Expected: `tz_aware` FAILS (naive datetime, `tzinfo is None`); `symlink_loop` FAILS by timing out (thread still alive after 10s) — or passes slowly; either way it must become fast+safe after the fix.

- [ ] **Step 3: Make `parse_date` ET-aware and stop following symlinks**

In `engine/scout/hooks/kb_pre_filter.py`:

(a) `parse_date` — attach `ET` to the returned datetime (the loop currently does `return datetime.strptime(cleaned, fmt)`):

```python
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=ET)
        except ValueError:
            continue
    return None
```

(b) `classify` — remove the now-redundant tzinfo attachment. Find the line that does `parsed.replace(tzinfo=ET)` (the value returned by `parse_date` is now already ET-aware) and use `parsed` directly. Concretely, replace the `parsed = parse_date(...)` follow-up that reattaches tz — if the code reads like `parsed_et = parsed.replace(tzinfo=ET)`, change downstream uses to `parsed` (which is already aware). Do NOT change the UTC-elapsed arithmetic below it.

(c) `discover_kb_files` — replace the symlink-following `rglob` walk. Add `import os` at the top of the module (it currently imports only `pathlib`/stdlib — verify and add `os` if missing). Replace:

```python
    for p in kb_root.rglob("*.md"):
```

with an `os.walk` that does not follow symlinks (Python 3.11 has no `rglob(follow_symlinks=...)`):

```python
    for dirpath, _dirnames, filenames in os.walk(kb_root, followlinks=False):
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            p = Path(dirpath) / fname
```

Keep the existing per-file filter/append logic that followed the old `for p in ...:` line, re-indented under the inner `for fname` loop. (Read the current loop body and preserve it exactly, only changing the iteration source and indentation.)

- [ ] **Step 4: Run the hook tests**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_hooks_kb_pre_filter.py -q`
Expected: all PASS — including the existing `test_classify_age_is_dst_correct_across_spring_forward` (the DST arithmetic still holds since `parse_date` now returns the same ET-aware value `classify` used to compute). Then `../.venv/bin/python -m ruff check scout/hooks/kb_pre_filter.py tests/unit/test_hooks_kb_pre_filter.py && ../.venv/bin/python -m mypy scout/hooks/kb_pre_filter.py` — clean.

- [ ] **Step 5: Commit**

```bash
git add engine/scout/hooks/kb_pre_filter.py engine/tests/unit/test_hooks_kb_pre_filter.py
git commit -m "fix(hooks): ET-aware parse_date + non-symlink-following KB walk

parse_date returned a naive datetime (tzinfo attached later in classify), so
any caller that forgot drifted an hour across DST; it now returns an
ET-aware value. discover_kb_files used rglob, which follows symlinks — a
symlink loop in the KB dir recursed forever and hung session startup; switch
to os.walk(followlinks=False).

Fixes #53

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: bootstrap same-day .bak overwrite (#62)

`_stage_cat1b_runners` names backups `{vault_rel}.bak.{today}` and `shutil.copy2` unconditionally — a second `/scout-update` the same day silently overwrites the first run's backup, losing the earlier hand-edit. Make the backup path unique so no run clobbers another's.

**Files:**
- Modify: `engine/scout/scripts/bootstrap.py:219-229`
- Test: `engine/tests/unit/test_bootstrap_upgrade.py`

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/unit/test_bootstrap_upgrade.py` (reuse the file's existing install/upgrade helpers; if it has a helper to build a `BootstrapConfig` + run install/upgrade, use it. Otherwise this self-contained test drives `_stage_cat1b_runners` indirectly via the public upgrade entry the other tests use). Add `import datetime as _dt` and `from unittest.mock import patch` if absent:

```python
def test_same_day_second_upgrade_does_not_clobber_first_backup(tmp_path, monkeypatch):
    """#62: two upgrades on the same calendar day must not overwrite each
    other's runner .bak — the first hand-edit's backup must survive."""
    import scout.scripts.bootstrap as bs

    # Freeze the date so both upgrades compute the same {today} suffix.
    class _FixedDate(_dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 15)

    target = tmp_path / "run-scout.sh"

    # First backup of content "A".
    target.write_text("A\n")
    first = bs._unique_backup_path(tmp_path / "run-scout.sh")
    import shutil

    shutil.copy2(target, first)
    assert first.exists()

    # Second backup the same day must pick a DIFFERENT path, leaving "A" intact.
    target.write_text("B\n")
    second = bs._unique_backup_path(tmp_path / "run-scout.sh")
    assert second != first, "same-day second backup reused the first backup path"
    shutil.copy2(target, second)

    assert first.read_text() == "A\n"  # first backup preserved
    assert second.read_text() == "B\n"
```

NOTE: this test calls a new helper `_unique_backup_path` you will add in Step 3. If the team prefers testing through the full `upgrade` API instead, the existing `test_bootstrap_upgrade.py` helpers can drive two upgrades with a patched `_dt.date.today`; either way the assertion is "two same-day backups coexist."

- [ ] **Step 2: Run it to verify it fails**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_bootstrap_upgrade.py -q -k same_day`
Expected: FAIL — `_unique_backup_path` doesn't exist (AttributeError).

- [ ] **Step 3: Add a unique-backup helper and use it**

In `engine/scout/scripts/bootstrap.py`, add a module-level helper (near the other private helpers):

```python
def _unique_backup_path(target: Path) -> Path:
    """A backup path for `target` that never overwrites an existing backup.

    Keeps the familiar ``<name>.bak.<YYYY-MM-DD>`` form for the first backup
    of the day; on a second same-day run, appends ``-1``, ``-2``, ... so an
    earlier run's backup of a different hand-edit is never clobbered (#62).
    """
    today = _dt.date.today().isoformat()
    base = target.with_name(f"{target.name}.bak.{today}")
    if not base.exists():
        return base
    n = 1
    while True:
        candidate = target.with_name(f"{target.name}.bak.{today}-{n}")
        if not candidate.exists():
            return candidate
        n += 1
```

Then in `_stage_cat1b_runners`, replace the backup block (lines 222-226):

```python
                bak = _unique_backup_path(target)
                shutil.copy2(target, bak)
                backups.append(bak.name)
```

(Remove the old `today = _dt.date.today().isoformat()` / `bak = cfg.vault / f"{vault_rel}.bak.{today}"` lines and the "Overwrites same-day backup" comment — the helper now owns naming. Note `_unique_backup_path` derives the path from `target`, which already lives in the vault, so the `cfg.vault / vault_rel` construction is no longer needed for the backup.)

- [ ] **Step 4: Run the bootstrap-upgrade tests**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/unit/test_bootstrap_upgrade.py -q && ../.venv/bin/python -m ruff check scout/scripts/bootstrap.py tests/unit/test_bootstrap_upgrade.py && ../.venv/bin/python -m mypy scout/scripts/bootstrap.py`
Expected: all PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add engine/scout/scripts/bootstrap.py engine/tests/unit/test_bootstrap_upgrade.py
git commit -m "fix(bootstrap): never overwrite a same-day runner backup

Two /scout-update runs on the same calendar day reused the
<name>.bak.<DATE> path, so the second silently clobbered the first run's
backup and lost that hand-edit. _unique_backup_path keeps the dated name
for the first backup and suffixes -1/-2/... on repeats.

Fixes #62

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Final verification + PR

- [ ] **Step 1: Full suite, lint, types**

Run: `cd ~/scout-plugin/engine && ../.venv/bin/python -m pytest tests/ -q 2>&1 | tail -3`
Expected: 0 failed, 0 xfailed (this branch has Batch 1's hermetic fixture, so no live-vault flakes).
Run: `../.venv/bin/python -m ruff check scout/ tests/ && ../.venv/bin/python -m ruff format --check scout/ tests/ && ../.venv/bin/python -m mypy scout/`
Expected: all clean. (If `ruff format --check` flags a new file, run `../.venv/bin/python -m ruff format scout/ tests/` and amend the relevant commit.)

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin fix/batch-3-high-severity
gh pr create --base main --head fix/batch-3-high-severity --title "fix: Batch 3 — high-severity audit tail (#41 #42 #43 #45 #46 #52 #53 #62)" --body "$(cat <<'EOF'
## Why

The eight remaining severity:high audit bugs from the 2026-06-10 audit (Plan 9, Batch 3). Each is a silent-corruption, crash, or hang risk; each fix ships with a regression test (and, per the batch policy, anything touching bootstrap.py or the TUI gets a unit test + type hints).

## What's here (one commit each)

- **#41/#42 backfill** — parse the file once (new `parse_lines`) so candidate selection can't map a line number onto the wrong raw line after a concurrent edit; register each prefix immediately after its write inside a `try/finally` that saves, so a mid-loop failure leaves the file and id-map consistent.
- **#43 connectors** — `_load_yaml` wraps `OSError` as `ConfigError` (dangling symlink / unreadable overlay no longer leaks a raw traceback).
- **#45 bootstrap upgrade** — read `scout-config.yaml` as utf-8 and map `YAMLError`/`UnicodeDecodeError` to `ConfigError`'s exit code instead of an internal-error exit.
- **#46 KB ontology** — `KBSchemaError(KBError)`; `_load_schema` guards missing/malformed schema; `validate()` tolerates entity types with no `properties:` key (no more constructor crash taking down the TUI).
- **#52 TUI spawn** — extract a Textual-free builder with correct AppleScript-literal escaping + a sanitized session slug (kills the title shell-injection, CI-testable without Textual), and run `osascript` on a worker thread so the UI doesn't freeze.
- **#53 kb_pre_filter** — `parse_date` returns an ET-aware datetime (no DST drift); `discover_kb_files` uses `os.walk(followlinks=False)` so a symlink loop can't hang session startup.
- **#62 bootstrap backups** — `_unique_backup_path` never overwrites a same-day runner `.bak`.

Plan: `docs/superpowers/plans/2026-06-15-batch-3-high-severity.md`.

## Testing

Each fix is TDD'd (failing test → fix → green). Full suite green, `ruff` + `ruff format` + `mypy` clean. The TUI injection logic is tested in CI via the Textual-free `spawn_cmd` module.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes (2026-06-15)

- **Coverage:** #41 (Task 1, single-read), #42 (Task 1, finally-save), #43 (Task 2), #45 (Task 3), #46 (Task 4, KBSchemaError + validate), #52 (Task 5, escaping + worker), #53 (Task 6, tz + os.walk), #62 (Task 7). All 8 covered.
- **Type consistency:** `parse_lines(lines: list[str]) -> list[ActionItem]` (Task 1) used by backfill; `_unique_backup_path(target: Path) -> Path` (Task 7); `build_terminal_applescript(*, title, prompt) -> tuple[str, str]` + `applescript_literal`/`session_slug` (Task 5) — names consistent across tasks and tests.
- **Verify-by-reading flags:** three tasks (#46 constructor param names, #53 `discover_kb_files` signature + whether `os` is imported, #62 whether to test via helper or full upgrade) have explicit NOTE callouts telling the implementer to confirm against current code and adapt the call sites (not the production signatures). These are genuine "read the file to confirm names" points, not placeholders — the fix itself is fully specified.
- **Independence:** every task touches a disjoint module set (Task 1: parser/backfill; 2: connectors; 3: cli; 4: errors/kb; 5: tui; 6: hooks; 7: bootstrap), so order is flexible and there are no inter-task merge conflicts.
