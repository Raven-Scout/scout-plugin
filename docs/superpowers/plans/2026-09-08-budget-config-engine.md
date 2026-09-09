# Budget Configuration — Engine — Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the four budget knobs `scoutctl budget check` enforces one canonical `budget:` block in `scout-config.yaml`, plus `scoutctl budget show`/`set` so they can be read and changed without hand-editing YAML.

**Architecture:** The packaged defaults get a `budget:` block that a parity test pins to `BudgetConfig()`'s dataclass defaults, so the shipped YAML can never drift back into decoration. `budget_check.load_config` learns the canonical `(budget, *)` keys while keeping the legacy `plan:`/`thresholds:` spellings as a fallback — canonical wins via two separate scanner passes, because `scan_overrides` is last-wins within a pass and merging the key maps would let line order decide. A new `scout/scripts/budget_config.py` holds the interactive read/write support (JSON payload, line-surgical writer) so the pre-run gate module stays minimal.

**Tech Stack:** Python 3.11+, Typer 0.26, pytest, ruff (line-length 120, target py311). No new dependencies — the writer is line-based precisely to avoid pulling a round-trip YAML library.

**Spec:** `docs/superpowers/specs/2026-09-08-budget-config-design.md`

**Plan 2 of 2:** `../../../scout-app/docs/superpowers/plans/2026-09-08-budget-settings-app.md` — the macOS Settings section, a client of the CLI contract this plan builds. Execute this plan first.

## Global Constraints

- **Engine defaults do not change value.** The canonical block ships `daily_usd: 50`, `window_hours: 5`, `skip_at_pct: 80`, `failure_backoff_minutes: 60` — identical to today's operative `BudgetConfig()` constants. Merging this plan must not re-gate any existing install.
- **No pyyaml on the `budget check` path.** `budget_check.py` keeps using the `scan_overrides` regex scanner. `import yaml` is permitted only in tests and in modules the gate never imports.
- **No `scout.scripts` import at `cli.py` top level.** `tests/perf/test_no_heavy_imports.py` bans it. Every new command imports its module inside the function body.
- **House idiom for JSON in `cli.py`:** `import json as _json` inside the function.
- **Exactly four keys in the shipped `budget:` block.** A knob in the file that `budget_check` does not read is the defect this work removes.
- **Rounding is two-step and load-bearing:** `window_budget_usd = round(daily × hours / 24, 2)`, then `skip_threshold_usd = round(window_budget_usd × pct / 100, 2)`. Never collapse the chain — at the defaults the forms disagree (8.34 vs 8.33).
- **Lint/test commands:** `.venv/bin/pytest tests/ -v`, `.venv/bin/ruff check scout tests`, `.venv/bin/ruff format --check scout tests`. Run from `engine/`.

---

### Task 1: Canonical `budget:` block in the packaged defaults

Replaces the `budgets:` block nothing reads with a `budget:` block a parity test proves accurate, and moves the four assertions that referenced the old name.

**Files:**
- Modify: `scout/defaults/scout-config.yaml:11-13`
- Create: `tests/unit/test_budget_defaults_parity.py`
- Modify: `tests/unit/test_config.py:20`, `tests/unit/test_config.py:26-33`
- Modify: `tests/unit/test_config_vault_file.py:275-280`
- Modify: `tests/smoke/test_wheel_install.py:97`, `tests/smoke/test_wheel_install.py:107`

**Interfaces:**
- Consumes: `BudgetConfig` from `scout.scripts.budget_check` (already exists, unchanged in this task).
- Produces: the YAML key names every later task and Plan 2 use — `budget.daily_usd`, `budget.window_hours`, `budget.skip_at_pct`, `budget.failure_backoff_minutes`.

- [ ] **Step 1: Write the failing parity test**

Create `tests/unit/test_budget_defaults_parity.py`:

```python
"""The packaged defaults' ``budget:`` block must equal ``BudgetConfig()``.

``budget_check.py`` deliberately does not load the packaged-defaults layer — it
regex-scans the vault file to keep pyyaml off the pre-run gate path (#74). So the
shipped YAML does not *govern*; it documents. This test is what makes the
documentation trustworthy: if the two drift, CI goes red instead of the file
quietly becoming a lie, which is exactly what happened to the ``budgets:`` block
it replaced (shipped 150, real default 50, nothing reading either).
"""

from __future__ import annotations

from importlib.resources import as_file, files
from typing import Any

import yaml

from scout.scripts.budget_check import BudgetConfig


def _packaged_budget_block() -> dict[str, Any]:
    resource = files("scout") / "defaults" / "scout-config.yaml"
    with as_file(resource) as path:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "packaged defaults must be a YAML mapping"
    block = data.get("budget")
    assert isinstance(block, dict), "packaged defaults must define a `budget:` mapping"
    return block


def test_packaged_defaults_match_budget_config_dataclass() -> None:
    block = _packaged_budget_block()
    defaults = BudgetConfig()
    assert block["daily_usd"] == defaults.daily_budget_usd
    assert block["window_hours"] == defaults.window_hours
    assert block["skip_at_pct"] == defaults.skip_threshold_pct
    assert block["failure_backoff_minutes"] == defaults.failure_backoff_min


def test_packaged_budget_block_holds_exactly_the_enforced_knobs() -> None:
    """A knob in the shipped file that budget_check does not read is the defect
    this design removed — `budgets.max_per_session_usd` was one. Keep the block
    to exactly the four keys the gate enforces."""
    assert set(_packaged_budget_block()) == {
        "daily_usd",
        "window_hours",
        "skip_at_pct",
        "failure_backoff_minutes",
    }


def test_old_budgets_block_is_gone() -> None:
    """The plural `budgets:` key was read by nothing under scout/. Its removal
    is the point; a re-added copy would resurrect the two-schema problem."""
    resource = files("scout") / "defaults" / "scout-config.yaml"
    with as_file(resource) as path:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "budgets" not in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_budget_defaults_parity.py -v`

Expected: FAIL — `AssertionError: packaged defaults must define a `budget:` mapping` on the first three tests, and `test_old_budgets_block_is_gone` fails on `assert "budgets" not in data`.

- [ ] **Step 3: Replace the block in the packaged defaults**

In `scout/defaults/scout-config.yaml`, replace these three lines:

```yaml
budgets:
  daily_budget_estimate_usd: 150
  max_per_session_usd: 20
```

with:

```yaml
# The four knobs `scoutctl budget check` enforces before every scheduled run.
# These MUST equal BudgetConfig()'s defaults in scout/scripts/budget_check.py —
# tests/unit/test_budget_defaults_parity.py fails the build if they drift.
# Override them per-vault with `scoutctl budget set`, not by editing this file.
budget:
  daily_usd: 50
  window_hours: 5
  skip_at_pct: 80
  failure_backoff_minutes: 60
```

Leave the `thresholds:` block below it untouched — it holds unrelated keys (`rate_limit_warn_pct`, `rate_limit_block_pct`, `connector_staleness_hours`) and `test_config.py` asserts its presence.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_budget_defaults_parity.py -v`

Expected: PASS, 4 tests.

- [ ] **Step 5: Run the full suite to find the assertions that referenced `budgets`**

Run: `.venv/bin/pytest tests/ -q`

Expected: 4 failures, in `tests/unit/test_config.py` (2), `tests/unit/test_config_vault_file.py` (1), `tests/smoke/test_wheel_install.py` (1).

- [ ] **Step 6: Move the four assertions to the new key**

In `tests/unit/test_config.py`, `test_load_config_returns_defaults_when_no_user_override`, change:

```python
    assert "budgets" in cfg
```

to:

```python
    assert "budget" in cfg
```

In the same file, `test_user_config_overrides_defaults`, change the body to:

```python
    _write(
        fake_data_dir / "scout-config.yaml",
        {"budget": {"daily_usd": 999}},
    )
    cfg = config.load_config(fake_data_dir)
    assert cfg["budget"]["daily_usd"] == 999
    # Other default keys preserved
    assert "window_hours" in cfg["budget"]
```

(The old sibling-preservation assertion used `max_per_session_usd`, which no longer exists; `window_hours` is the equivalent sibling in the new block.)

In `tests/unit/test_config_vault_file.py`, `test_type_mismatched_section_warns_and_keeps_defaults`, change:

```python
    _write_vault_config(fake_data_dir, {"user": "oops", "budgets": 5})
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["timezone"] == config.DEFAULT_TIMEZONE
    assert cfg["budgets"]["daily_budget_estimate_usd"] == 150
    err = capsys.readouterr().err
    assert "user" in err and "budgets" in err
```

to:

```python
    _write_vault_config(fake_data_dir, {"user": "oops", "budget": 5})
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["timezone"] == config.DEFAULT_TIMEZONE
    assert cfg["budget"]["daily_usd"] == 50
    err = capsys.readouterr().err
    assert "user" in err and "budget" in err
```

In `tests/smoke/test_wheel_install.py`, change the probe string's key and the assertion:

```python
        " 'has_budget': 'budget' in cfg,"
```

```python
    assert probe_out == {"schema": 1, "has_budget": True, "has_thresholds": True}
```

- [ ] **Step 7: Run the full suite to verify green**

Run: `.venv/bin/pytest tests/ -q`

Expected: PASS, no failures. (The wheel smoke test may be skipped locally if `uv` is unavailable — that is fine, CI runs it.)

- [ ] **Step 8: Lint**

Run: `.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests`

Expected: no findings. If `format --check` objects, run `.venv/bin/ruff format scout tests` and re-run.

- [ ] **Step 9: Commit**

```bash
git add scout/defaults/scout-config.yaml tests/unit/test_budget_defaults_parity.py tests/unit/test_config.py tests/unit/test_config_vault_file.py tests/smoke/test_wheel_install.py
git commit -m "feat(config): canonical budget: block in packaged defaults

Replaces the budgets: block, which nothing under scout/ ever read — it
shipped daily_budget_estimate_usd: 150 while budget_check.py enforced a
hardcoded 50.0, and carried max_per_session_usd, which three unrelated
code paths ignore.

The new block is pinned to BudgetConfig()'s dataclass defaults by
test_budget_defaults_parity.py, so the shipped file cannot drift back
into decoration without a red build.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Canonical keys in the reader, with legacy fallback and provenance

Teaches `budget_check.load_config` the `(budget, *)` keys, keeps `plan:`/`thresholds:` working, and exposes which shape supplied the values so `show` can report it.

**Files:**
- Modify: `scout/scripts/budget_check.py:65` (rename `_CONFIG_BOUNDS` → `CONFIG_BOUNDS`), `:92-137` (key maps + `load_config`), `:236-262` (`run`), `:265-280` (`__all__`)
- Modify: `tests/unit/test_budget_check.py` (append a new section)

**Interfaces:**
- Consumes: `scan_overrides(text, *, flat_keys=..., nested_keys=...) -> dict[str, str]` from `scout.scripts._config_scan` (existing, unchanged).
- Produces, all importable from `scout.scripts.budget_check`:
  - `CONFIG_BOUNDS: dict[str, tuple[float, float]]` — public rename of `_CONFIG_BOUNDS`, keyed by dataclass field name.
  - `SOURCE_VAULT = "vault"`, `SOURCE_LEGACY = "legacy"`, `SOURCE_DEFAULTS = "defaults"`.
  - `load_config_with_source(config_path: Path) -> tuple[BudgetConfig, str]`.
  - `load_config(config_path: Path) -> BudgetConfig` — signature unchanged, now delegates.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_budget_check.py`. Add `SOURCE_DEFAULTS`, `SOURCE_LEGACY`, `SOURCE_VAULT` and `load_config_with_source` to the existing import block from `scout.scripts.budget_check`, then append this section:

```python
# ----- canonical `budget:` block ------------------------------------------


def test_load_config_reads_canonical_budget_block(tmp_path: Path) -> None:
    path = tmp_path / "scout-config.yaml"
    path.write_text(
        "budget:\n"
        "  daily_usd: 200\n"
        "  window_hours: 3\n"
        "  skip_at_pct: 90\n"
        "  failure_backoff_minutes: 30\n"
    )
    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == 200
    assert cfg.window_hours == 3
    assert cfg.skip_threshold_pct == 90
    assert cfg.failure_backoff_min == 30
    assert source == SOURCE_VAULT


def test_legacy_shape_still_works_and_is_reported_as_legacy(tmp_path: Path) -> None:
    path = tmp_path / "scout-config.yaml"
    path.write_text(
        "plan:\n"
        "  daily_budget_estimate_usd: 120\n"
        "  rate_limit_window_hours: 4\n"
        "thresholds:\n"
        "  skip_threshold_pct: 70\n"
        "  failure_backoff_minutes: 15\n"
    )
    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == 120
    assert cfg.window_hours == 4
    assert cfg.skip_threshold_pct == 70
    assert cfg.failure_backoff_min == 15
    assert source == SOURCE_LEGACY


@pytest.mark.parametrize("canonical_first", [True, False])
def test_canonical_beats_legacy_in_either_file_order(tmp_path: Path, canonical_first: bool) -> None:
    """scan_overrides is last-wins within one pass, so a single merged key map
    would let line order pick the winner. Both orderings must resolve canonical.
    """
    canonical = "budget:\n  daily_usd: 200\n  window_hours: 3\n"
    legacy = "plan:\n  daily_budget_estimate_usd: 120\n  rate_limit_window_hours: 4\n"
    path = tmp_path / "scout-config.yaml"
    path.write_text(canonical + legacy if canonical_first else legacy + canonical)

    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == 200
    assert cfg.window_hours == 3
    assert source == SOURCE_VAULT


def test_legacy_fills_knobs_the_canonical_block_omits(tmp_path: Path) -> None:
    """A half-migrated vault must not lose the knobs it hasn't migrated yet."""
    path = tmp_path / "scout-config.yaml"
    path.write_text(
        "budget:\n"
        "  daily_usd: 200\n"
        "thresholds:\n"
        "  failure_backoff_minutes: 15\n"
    )
    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == 200
    assert cfg.failure_backoff_min == 15
    assert cfg.window_hours == DEFAULT_WINDOW_HOURS
    assert source == SOURCE_VAULT


def test_no_budget_config_at_all_reports_defaults(tmp_path: Path) -> None:
    path = tmp_path / "scout-config.yaml"
    path.write_text("user:\n  email: alex@example.com\nplugin:\n  version_at_last_setup: 0.9.0\n")
    cfg, source = load_config_with_source(path)
    assert cfg == BudgetConfig()
    assert source == SOURCE_DEFAULTS


def test_missing_file_reports_defaults(tmp_path: Path) -> None:
    cfg, source = load_config_with_source(tmp_path / "nope.yaml")
    assert cfg == BudgetConfig()
    assert source == SOURCE_DEFAULTS


def test_out_of_range_canonical_value_falls_back_with_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Same tolerance the legacy keys get: `window_hours: 0` yields a $0 window
    budget and would silently skip every session from then on."""
    path = tmp_path / "scout-config.yaml"
    path.write_text("budget:\n  window_hours: 0\n  daily_usd: 200\n")
    cfg, _ = load_config_with_source(path)
    assert cfg.window_hours == DEFAULT_WINDOW_HOURS
    assert cfg.daily_budget_usd == 200
    assert "window_hours" in capsys.readouterr().err


def test_budget_key_under_the_wrong_parent_is_ignored(tmp_path: Path) -> None:
    """The scanner is parent-aware on purpose — scout-config.yaml has many
    subtrees written by several producers, and none of them may set the budget.
    """
    path = tmp_path / "scout-config.yaml"
    path.write_text("connectors:\n  inputs:\n    daily_usd: 999\n")
    cfg, source = load_config_with_source(path)
    assert cfg.daily_budget_usd == DEFAULT_DAILY_BUDGET_USD
    assert source == SOURCE_DEFAULTS


def test_load_config_keeps_its_single_return_signature(tmp_path: Path) -> None:
    """run() and the existing tests call load_config(path) -> BudgetConfig."""
    path = tmp_path / "scout-config.yaml"
    path.write_text("budget:\n  daily_usd: 77\n")
    assert load_config(path).daily_budget_usd == 77
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_budget_check.py -v -k "canonical or legacy or defaults or wrong_parent or single_return"`

Expected: FAIL at import — `ImportError: cannot import name 'load_config_with_source' from 'scout.scripts.budget_check'`.

- [ ] **Step 3: Implement the reader changes**

In `scout/scripts/budget_check.py`:

**3a.** Rename the bounds table to public (a second module needs it in Task 4). Change `_CONFIG_BOUNDS: dict[str, tuple[float, float]] = {` to `CONFIG_BOUNDS: dict[str, tuple[float, float]] = {`, and update the two references — the docstring mention at line 115 and the lookup at line 134 (the latter moves into `_coerce` below anyway).

**3b.** Rename the legacy key maps and add the canonical one. Replace the `_NESTED_CONFIG_KEYS` and `_CONFIG_KEYS` definitions with:

```python
# The canonical shape, written by `scoutctl budget set` and shipped in
# scout/defaults/scout-config.yaml. Takes precedence over the legacy spellings
# below — see load_config_with_source for why that needs two scanner passes.
_CANONICAL_NESTED_KEYS: dict[tuple[str, str], tuple[str, type]] = {
    ("budget", "daily_usd"): ("daily_budget_usd", float),
    ("budget", "window_hours"): ("window_hours", int),
    ("budget", "skip_at_pct"): ("skip_threshold_pct", float),
    ("budget", "failure_backoff_minutes"): ("failure_backoff_min", int),
}

# Pre-canonical shape. Still read so no existing vault needs migrating; the
# first `scoutctl budget set` writes the canonical block over it.
_LEGACY_NESTED_KEYS = {
    ("plan", "daily_budget_estimate_usd"): ("daily_budget_usd", float),
    ("plan", "rate_limit_window_hours"): ("window_hours", int),
    ("thresholds", "skip_threshold_pct"): ("skip_threshold_pct", float),
    ("thresholds", "failure_backoff_minutes"): ("failure_backoff_min", int),
}

# Flat top-level spellings — back-compat with hand-made override files.
_LEGACY_FLAT_KEYS = {
    "daily_budget_estimate_usd": ("daily_budget_usd", float),
    "rate_limit_window_hours": ("window_hours", int),
    "skip_threshold_pct": ("skip_threshold_pct", float),
    "failure_backoff_minutes": ("failure_backoff_min", int),
}

_CASTERS = {field: caster for field, caster in _LEGACY_FLAT_KEYS.values()}

SOURCE_VAULT = "vault"
SOURCE_LEGACY = "legacy"
SOURCE_DEFAULTS = "defaults"
```

Keep the existing explanatory comment above `_LEGACY_NESTED_KEYS` about matching on `(section, key)` rather than the bare key — it is still the reason the scanner is parent-aware.

**3c.** Replace `load_config` with the coercion helper plus the two-pass loader:

```python
def _coerce(raw: dict[str, str]) -> dict[str, Any]:
    """Cast scanned strings to their field types, dropping anything unusable.

    Out-of-range values fall back to the default with a warning rather than
    being enforced: `window_hours: 0` — a plausible spelling of "no window" —
    yields a $0 window budget and a $0 skip threshold, which would silently skip
    every session from then on.
    """
    out: dict[str, Any] = {}
    for field_name, raw_value in raw.items():
        try:
            value = _CASTERS[field_name](raw_value)
        except (TypeError, ValueError):
            continue
        low, high = CONFIG_BOUNDS[field_name]
        if not (low <= value <= high):
            print(
                f"[budget-check] ignoring {field_name}={value}: outside the usable range "
                f"[{low}, {high}] — using the default",
                file=sys.stderr,
            )
            continue
        out[field_name] = value
    return out


def load_config_with_source(config_path: Path) -> tuple[BudgetConfig, str]:
    """Parse the budget knobs from scout-config.yaml, reporting their provenance.

    The canonical `budget:` block wins over the legacy `plan:`/`thresholds:`
    spellings. That precedence needs TWO scanner passes: scan_overrides is
    last-wins within a single pass, so handing it one merged key map would let
    whichever block appears later in the file win — making the result depend on
    line order rather than on which spelling is canonical.

    Missing file or unparseable rows fall back to defaults, matching the bash
    original's `grep ... | awk`, which silently no-ops on missing keys.
    """
    if not config_path.exists():
        return BudgetConfig(), SOURCE_DEFAULTS
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return BudgetConfig(), SOURCE_DEFAULTS

    legacy = _coerce(
        scan_overrides(
            text,
            flat_keys={k: v[0] for k, v in _LEGACY_FLAT_KEYS.items()},
            nested_keys={k: v[0] for k, v in _LEGACY_NESTED_KEYS.items()},
        )
    )
    canonical = _coerce(
        scan_overrides(
            text,
            nested_keys={k: v[0] for k, v in _CANONICAL_NESTED_KEYS.items()},
        )
    )

    if canonical:
        source = SOURCE_VAULT
    elif legacy:
        source = SOURCE_LEGACY
    else:
        source = SOURCE_DEFAULTS
    return BudgetConfig(**{**legacy, **canonical}), source


def load_config(config_path: Path) -> BudgetConfig:
    """The four scalar knobs from scout-config.yaml. See load_config_with_source."""
    return load_config_with_source(config_path)[0]
```

**3d.** In `run`, take the source and say which shape supplied the values. Replace the body's config load and verbose block with:

```python
    config, source = load_config_with_source(config_path)
    decision = decide(tracker_path, config)
    if verbose:
        # Say WHICH file supplied the numbers — the silent-defaults fallback
        # is what kept #202 invisible for months.
        state = "" if config_path.exists() else " (missing — using engine defaults)"
        print(f"[budget-check] config: {config_path}{state}")
        if source == SOURCE_LEGACY:
            print(
                "[budget-check] values came from the legacy plan:/thresholds: keys — "
                "`scoutctl budget set` writes the canonical budget: block"
            )
        elif source == SOURCE_DEFAULTS:
            print("[budget-check] no budget: block in the vault — running on engine defaults")
        print(f"[budget-check] {decision.reason}")
        print(
            f"[budget-check] window: {config.window_hours}h, "
            f"daily: ${config.daily_budget_usd:.2f}, "
            f"window budget: ${config.window_budget_usd:.2f}, "
            f"skip at: ${config.skip_threshold_usd:.2f}"
        )
    return decision.exit_code
```

**3e.** Update `__all__`: add `"CONFIG_BOUNDS"`, `"SOURCE_DEFAULTS"`, `"SOURCE_LEGACY"`, `"SOURCE_VAULT"`, `"load_config_with_source"`, keeping it alphabetically sorted as it is today.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_budget_check.py -v`

Expected: PASS — the new tests plus every pre-existing one (the legacy-shape tests must still be green; that is the back-compat guarantee).

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests`

Expected: PASS, no lint findings.

- [ ] **Step 6: Commit**

```bash
git add scout/scripts/budget_check.py tests/unit/test_budget_check.py
git commit -m "feat(budget): read the canonical budget: block, legacy as fallback

Canonical (budget, *) keys take precedence over plan:/thresholds:.
Precedence needs two scan_overrides passes rather than one merged key
map: the scanner is last-wins within a pass, so a merged map would let
whichever block sits later in the file win.

load_config_with_source reports provenance so budget show can tell the
user whether they are configured, on legacy keys, or silently running
on engine defaults.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `scoutctl budget show`

The app's read path, plus the plain-text human view and a warning about the dead dotfile.

**Files:**
- Create: `scout/scripts/budget_config.py`
- Create: `tests/unit/test_budget_cli.py`
- Modify: `scout/cli.py:143` (append to `budget_app`, after `budget_check_cmd`)
- Modify: `tests/perf/test_startup.py` (append a latency case)

**Interfaces:**
- Consumes: `BudgetConfig`, `load_config_with_source`, `SOURCE_*` from `scout.scripts.budget_check` (Task 2); `paths.config_path`, `paths.data_dir` from `scout.paths`.
- Produces, importable from `scout.scripts.budget_config`:
  - `FIELD_TO_YAML_KEY: dict[str, str]` — dataclass field name → canonical YAML key.
  - `show_payload(data_dir: Path | None = None) -> dict[str, Any]` — the exact JSON contract Plan 2 decodes.
  - `warn_if_legacy_dotfile(data_dir: Path | None = None) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_budget_cli.py`:

```python
"""CLI + payload tests for `scoutctl budget {show,set}`.

The JSON shape asserted here is the contract scout-app's BudgetSettingsService
decodes (Plan 2). Changing a key name here is a breaking change for the app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts.budget_config import show_payload, warn_if_legacy_dotfile

runner = CliRunner()


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal vault with SCOUT_DATA_DIR pointed at it."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-logs").mkdir()
    return tmp_path


# ----- show_payload --------------------------------------------------------


def test_show_payload_reports_defaults_for_a_bare_vault(vault: Path) -> None:
    payload = show_payload(vault)
    assert payload["source"] == "defaults"
    assert payload["daily_usd"] == 50.0
    assert payload["window_hours"] == 5
    assert payload["skip_at_pct"] == 80.0
    assert payload["failure_backoff_minutes"] == 60
    assert payload["config_path"] == str(vault / "scout-config.yaml")


def test_show_payload_derives_the_gate_with_two_step_rounding(vault: Path) -> None:
    """The engine rounds the window budget to cents BEFORE applying the skip
    percentage. At the defaults the two forms disagree — 8.34 vs a collapsed
    8.33 — and scout-app mirrors this arithmetic, so the intermediate rounding
    is part of the contract, not an implementation detail.
    """
    payload = show_payload(vault)
    assert payload["window_budget_usd"] == 10.42
    assert payload["skip_threshold_usd"] == 8.34


def test_show_payload_reads_a_canonical_block(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        "budget:\n"
        "  daily_usd: 200\n"
        "  window_hours: 3\n"
        "  skip_at_pct: 90\n"
        "  failure_backoff_minutes: 30\n"
    )
    payload = show_payload(vault)
    assert payload["source"] == "vault"
    assert payload["daily_usd"] == 200
    assert payload["window_budget_usd"] == 25.0
    assert payload["skip_threshold_usd"] == 22.5


def test_show_payload_reports_legacy_source(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        "plan:\n  daily_budget_estimate_usd: 120\n  rate_limit_window_hours: 4\n"
    )
    payload = show_payload(vault)
    assert payload["source"] == "legacy"
    assert payload["daily_usd"] == 120


# ----- the dead dotfile ----------------------------------------------------


def test_warns_when_the_dotted_config_carries_budget_keys(vault: Path) -> None:
    (vault / ".scout-config.yaml").write_text(
        "plan:\n  daily_budget_estimate_usd: 200\n  rate_limit_window_hours: 3\n"
    )
    warning = warn_if_legacy_dotfile(vault)
    assert warning is not None
    assert ".scout-config.yaml" in warning


def test_no_warning_when_the_dotted_config_is_absent(vault: Path) -> None:
    assert warn_if_legacy_dotfile(vault) is None


def test_no_warning_when_the_dotted_config_has_no_budget_keys(vault: Path) -> None:
    (vault / ".scout-config.yaml").write_text("schedule:\n  off_peak_start: 23\n")
    assert warn_if_legacy_dotfile(vault) is None


# ----- CLI -----------------------------------------------------------------


def test_budget_show_json_emits_the_payload(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "show", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert set(data) == {
        "config_path",
        "source",
        "daily_usd",
        "window_hours",
        "skip_at_pct",
        "failure_backoff_minutes",
        "window_budget_usd",
        "skip_threshold_usd",
    }


def test_budget_show_json_stays_parseable_with_the_dotfile_warning(vault: Path) -> None:
    """The warning goes to stderr so --json stdout is machine-readable."""
    (vault / ".scout-config.yaml").write_text("plan:\n  daily_budget_estimate_usd: 200\n")
    result = runner.invoke(app, ["budget", "show", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["source"] == "defaults"
    assert ".scout-config.yaml" in result.stderr


def test_budget_show_human_output_names_the_gate(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "show"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "8.34" in result.stdout
    assert "defaults" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_budget_cli.py -v`

Expected: FAIL at import — `ModuleNotFoundError: No module named 'scout.scripts.budget_config'`.

- [ ] **Step 3: Create the module**

Create `scout/scripts/budget_config.py`:

```python
"""Read/write support for the vault's canonical ``budget:`` block.

Split from :mod:`scout.scripts.budget_check` on purpose. That module sits on the
pre-run gate path and stays minimal — one regex scanner, no pyyaml (#74).
Everything here serves interactive callers instead: ``scoutctl budget show`` and
``set``, and through them the macOS app's Settings pane.

The writer is line-based rather than a YAML round-trip because
``scout-config.yaml`` is not single-purpose — it doubles as bootstrap state
(version stamps, connectors, schedule) written by several producers. A pyyaml
round-trip would strip every comment in it, and a whole-file rewrite risks
dropping a subtree a newer bootstrap wrote.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scout import paths
from scout.scripts.budget_check import load_config_with_source

# Dataclass field name -> canonical YAML key under `budget:`.
FIELD_TO_YAML_KEY = {
    "daily_budget_usd": "daily_usd",
    "window_hours": "window_hours",
    "skip_threshold_pct": "skip_at_pct",
    "failure_backoff_min": "failure_backoff_minutes",
}

# Keys whose presence in a dotted `.scout-config.yaml` means someone believes
# that file configures the budget. It does not — see warn_if_legacy_dotfile.
_DOTFILE_BUDGET_MARKERS = (
    "daily_budget_estimate_usd",
    "rate_limit_window_hours",
    "skip_threshold_pct",
    "failure_backoff_minutes",
    "daily_usd",
    "skip_at_pct",
)


def show_payload(data_dir: Path | None = None) -> dict[str, Any]:
    """The effective budget config plus the gate it computes to.

    This dict IS the `scoutctl budget show --json` contract that scout-app's
    BudgetSettingsService decodes. The two derived fields are served from here
    so the app has an authoritative value to check its own arithmetic against.
    """
    config_path = paths.config_path(data_dir)
    config, source = load_config_with_source(config_path)
    return {
        "config_path": str(config_path),
        "source": source,
        "daily_usd": config.daily_budget_usd,
        "window_hours": config.window_hours,
        "skip_at_pct": config.skip_threshold_pct,
        "failure_backoff_minutes": config.failure_backoff_min,
        "window_budget_usd": config.window_budget_usd,
        "skip_threshold_usd": config.skip_threshold_usd,
    }


def warn_if_legacy_dotfile(data_dir: Path | None = None) -> str | None:
    """Warn when a dotted ``.scout-config.yaml`` carries budget keys.

    Nothing reads that file — ``paths.config_path`` documents it as a dotfile no
    code path ever wrote, dead since #207/#202 — but it reads like live
    configuration, which is how a far tighter gate than anyone intended stayed
    invisible for months. Returns the warning text, or None when there's nothing
    to say.
    """
    dotted = (data_dir or paths.data_dir()) / ".scout-config.yaml"
    try:
        text = dotted.read_text(encoding="utf-8")
    except OSError:
        return None
    if not any(marker in text for marker in _DOTFILE_BUDGET_MARKERS):
        return None
    return (
        f"{dotted} carries budget keys but is read by nothing — the live file is "
        "scout-config.yaml (no dot). Values in the dotted file have no effect; "
        "`scoutctl budget set` writes the file that does."
    )


__all__ = ["FIELD_TO_YAML_KEY", "show_payload", "warn_if_legacy_dotfile"]
```

- [ ] **Step 4: Add the `show` command**

In `scout/cli.py`, immediately after `budget_check_cmd` (line 143), add:

```python
@budget_app.command("show")
def budget_show_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit the effective config as JSON."),
) -> None:
    """Print the effective budget config and the gate it computes to."""
    import json as _json

    from scout.scripts.budget_config import show_payload, warn_if_legacy_dotfile

    payload = show_payload()
    if warning := warn_if_legacy_dotfile():
        typer.echo(f"warning: {warning}", err=True)

    if as_json:
        typer.echo(_json.dumps(payload, indent=2))
        return

    typer.echo(f"config:  {payload['config_path']} ({payload['source']})")
    typer.echo(f"daily:   ${payload['daily_usd']:.2f}")
    typer.echo(f"window:  {payload['window_hours']}h → ${payload['window_budget_usd']:.2f}")
    typer.echo(f"skip at: {payload['skip_at_pct']:.0f}% → ${payload['skip_threshold_usd']:.2f}")
    typer.echo(f"backoff: {payload['failure_backoff_minutes']}m after a failed run")
```

The `scout.scripts.budget_config` import must stay inside the function body — `tests/perf/test_no_heavy_imports.py` bans `scout.scripts` at `cli.py` top level.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_budget_cli.py -v`

Expected: PASS, 10 tests.

- [ ] **Step 6: Add the latency guard**

`budget show` sits on an interactive app path, so it joins the startup guard. Append to `tests/perf/test_startup.py`:

```python
@pytest.mark.perf
def test_scoutctl_budget_show_latency() -> None:
    """scout-app calls this when the Settings pane opens. It does real file I/O,
    so it gets a looser budget than --help, but a heavy import creeping into the
    path must still show up here."""
    best_ms, stdout = _best_latency_ms(["budget", "show", "--json"])
    assert best_ms < BUDGET_SHOW_BUDGET_MS, (
        f"scoutctl budget show took {best_ms:.0f}ms (best of {_RUNS}, "
        f"budget: {BUDGET_SHOW_BUDGET_MS}ms). Check for heavy top-level imports."
    )
    assert "skip_threshold_usd" in stdout
```

and add the constant next to the existing budgets near the top of the file:

```python
BUDGET_SHOW_BUDGET_MS = 400
```

- [ ] **Step 7: Run the perf test**

Run: `.venv/bin/pytest tests/perf/test_startup.py -v -m perf`

Expected: PASS. If `budget show` exceeds 400ms locally, do not raise the constant — find the heavy import, which is the whole point of the guard.

- [ ] **Step 8: Run the full suite and lint**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests`

Expected: PASS, no lint findings.

- [ ] **Step 9: Commit**

```bash
git add scout/scripts/budget_config.py scout/cli.py tests/unit/test_budget_cli.py tests/perf/test_startup.py
git commit -m "feat(budget): scoutctl budget show

Emits the effective config plus the derived gate as JSON — the contract
scout-app's Settings pane decodes — and as a human-readable summary that
names which file and which key shape supplied the numbers.

Also warns when a dotted .scout-config.yaml carries budget keys. That
file is read by nothing but reads like live config, which is how a gate
at \$8.34 per 5h window stayed invisible.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `scoutctl budget set`

The write path: validate, then update only the four scalar lines in place, preserving every other byte.

**Files:**
- Modify: `scout/scripts/budget_config.py` (append the writer)
- Modify: `scout/cli.py` (append `budget_set_cmd` after `budget_show_cmd`)
- Modify: `tests/unit/test_budget_cli.py` (append a writer section)

**Interfaces:**
- Consumes: `CONFIG_BOUNDS`, `BudgetConfig` from `scout.scripts.budget_check`; `FIELD_TO_YAML_KEY`, `show_payload` from Task 3; `paths.require_data_dir`.
- Produces, importable from `scout.scripts.budget_config`:
  - `BudgetWriteError(Exception)`.
  - `validate_updates(updates: dict[str, float | int]) -> None`.
  - `apply_updates(text: str, updates: dict[str, float | int]) -> str` — pure, testable without a filesystem.
  - `write_budget(updates: dict[str, float | int], *, data_dir: Path | None = None, max_attempts: int = 4) -> dict[str, Any]` — returns the post-write `show_payload`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_budget_cli.py`. Extend the `scout.scripts.budget_config` import to add `BudgetWriteError`, `apply_updates`, `validate_updates`, `write_budget`, then append:

```python
# ----- apply_updates (pure) ------------------------------------------------

_MULTI_PRODUCER_CONFIG = """\
# Scout vault config — hand-edited and bootstrap-written.
user:
  name: Alex
  email: alex@example.com

budget:
  # Calibrated from tracked sessions.
  daily_usd: 150
  window_hours: 5
  skip_at_pct: 80
  failure_backoff_minutes: 60

plugin:
  version_at_last_setup: 0.9.0
  applied_migrations: []
"""


def test_apply_updates_rewrites_only_the_named_lines() -> None:
    out = apply_updates(_MULTI_PRODUCER_CONFIG, {"daily_budget_usd": 200})
    assert "  daily_usd: 200\n" in out
    # Everything else survives byte-for-byte.
    assert "  window_hours: 5\n" in out
    assert "  # Calibrated from tracked sessions.\n" in out
    assert "  version_at_last_setup: 0.9.0\n" in out
    assert "# Scout vault config — hand-edited and bootstrap-written.\n" in out
    assert out.count("budget:") == 1


def test_apply_updates_leaves_the_file_untouched_when_nothing_changes() -> None:
    assert apply_updates(_MULTI_PRODUCER_CONFIG, {"daily_budget_usd": 150}) == _MULTI_PRODUCER_CONFIG


def test_apply_updates_writes_integers_without_a_trailing_zero() -> None:
    out = apply_updates(_MULTI_PRODUCER_CONFIG, {"skip_threshold_pct": 90.0})
    assert "  skip_at_pct: 90\n" in out
    assert "90.0" not in out


def test_apply_updates_keeps_a_genuine_fraction() -> None:
    out = apply_updates(_MULTI_PRODUCER_CONFIG, {"daily_budget_usd": 12.5})
    assert "  daily_usd: 12.5\n" in out


def test_apply_updates_inserts_a_key_the_block_lacks() -> None:
    partial = "budget:\n  daily_usd: 150\n\nplugin:\n  version_at_last_setup: 0.9.0\n"
    out = apply_updates(partial, {"window_hours": 3})
    assert "  daily_usd: 150\n  window_hours: 3\n" in out
    assert "plugin:\n" in out


def test_apply_updates_appends_a_complete_block_when_absent() -> None:
    out = apply_updates("user:\n  name: Alex\n", {"daily_budget_usd": 200})
    assert "user:\n  name: Alex\n" in out
    assert "budget:\n" in out
    assert "  daily_usd: 200\n" in out
    # Unspecified knobs are written at their defaults so the block is complete.
    assert "  window_hours: 5\n" in out
    assert "  skip_at_pct: 80\n" in out
    assert "  failure_backoff_minutes: 60\n" in out


def test_apply_updates_appends_to_an_empty_file() -> None:
    out = apply_updates("", {"daily_budget_usd": 200})
    assert out.startswith("\n# Budget") or out.lstrip().startswith("# Budget")
    assert "  daily_usd: 200\n" in out


def test_apply_updates_ignores_a_budget_key_under_another_parent() -> None:
    """`daily_usd` nested under connectors must not be mistaken for the block."""
    text = "connectors:\n  inputs:\n    daily_usd: 1\n"
    out = apply_updates(text, {"daily_budget_usd": 200})
    assert "    daily_usd: 1\n" in out
    assert "budget:\n" in out
    assert "  daily_usd: 200\n" in out


# ----- validate_updates ----------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("window_hours", 0),
        ("window_hours", -1),
        ("skip_threshold_pct", 101),
        ("skip_threshold_pct", -1),
        ("daily_budget_usd", -5),
        ("failure_backoff_min", -1),
    ],
)
def test_validate_updates_rejects_out_of_range(field: str, value: float) -> None:
    """The read path tolerates a bad value so a mangled vault can't block a run.
    A `set` is a deliberate action, so the same value is an error — silently
    storing something else would show the user a number the gate ignores.
    """
    with pytest.raises(BudgetWriteError) as excinfo:
        validate_updates({field: value})
    assert str(value) in str(excinfo.value)


def test_validate_updates_accepts_the_boundaries() -> None:
    validate_updates({"daily_budget_usd": 0, "skip_threshold_pct": 100, "window_hours": 1})


# ----- write_budget --------------------------------------------------------


def test_write_budget_persists_and_returns_the_new_payload(vault: Path) -> None:
    payload = write_budget(
        {
            "daily_budget_usd": 200,
            "window_hours": 3,
            "skip_threshold_pct": 90,
            "failure_backoff_min": 30,
        },
        data_dir=vault,
    )
    assert payload["source"] == "vault"
    assert payload["daily_usd"] == 200
    assert payload["skip_threshold_usd"] == 22.5
    assert "daily_usd: 200" in (vault / "scout-config.yaml").read_text()


def test_write_budget_is_a_partial_update(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        "budget:\n  daily_usd: 150\n  window_hours: 5\n  skip_at_pct: 80\n  failure_backoff_minutes: 60\n"
    )
    write_budget({"daily_budget_usd": 200}, data_dir=vault)
    text = (vault / "scout-config.yaml").read_text()
    assert "daily_usd: 200" in text
    assert "window_hours: 5" in text
    assert "skip_at_pct: 80" in text


def test_write_budget_rejects_out_of_range_before_touching_the_file(vault: Path) -> None:
    original = "budget:\n  daily_usd: 150\n"
    (vault / "scout-config.yaml").write_text(original)
    with pytest.raises(BudgetWriteError):
        write_budget({"skip_threshold_pct": 500}, data_dir=vault)
    assert (vault / "scout-config.yaml").read_text() == original


def test_write_budget_leaves_no_tmp_files(vault: Path) -> None:
    write_budget({"daily_budget_usd": 200}, data_dir=vault)
    assert [p.name for p in vault.iterdir() if p.name.endswith(".tmp")] == []


def test_write_budget_retries_when_a_concurrent_writer_lands(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scout-config.yaml has several producers — bootstrap writes version stamps
    and connectors into it. When the file changes between our read and our
    replace, the writer must re-read and re-apply rather than clobber.

    Driven through `_mtime_ns` rather than by mutating the file from a patched
    `read_text`: write_budget samples the mtime *after* reading, so a mutation
    staged at read time is already reflected in the sample and the guard would
    correctly see no change. Forcing the second call (the pre-replace check) to
    disagree is what actually exercises the retry branch.
    """
    from scout.scripts import budget_config

    config_path = vault / "scout-config.yaml"
    config_path.write_text("budget:\n  daily_usd: 150\n")

    calls = {"n": 0}
    real_mtime_ns = budget_config._mtime_ns

    def mtime_with_one_collision(path: Path) -> int | None:
        # Two calls per attempt: the read-time sample, then the pre-replace
        # check. On call 2 report a change and land the concurrent write, so the
        # retry reads merged content.
        calls["n"] += 1
        if calls["n"] == 2:
            config_path.write_text(
                config_path.read_text() + "plugin:\n  version_at_last_setup: 0.9.0\n"
            )
            return -1
        return real_mtime_ns(path)

    monkeypatch.setattr(budget_config, "_mtime_ns", mtime_with_one_collision)
    write_budget({"daily_budget_usd": 200}, data_dir=vault)

    final = config_path.read_text()
    assert "daily_usd: 200" in final
    assert "version_at_last_setup: 0.9.0" in final  # the concurrent write survived
    assert calls["n"] >= 3  # proves a second attempt happened


# ----- CLI set -------------------------------------------------------------


def test_budget_set_writes_and_reports(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "set", "--daily-usd", "200", "--window-hours", "3", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["daily_usd"] == 200
    assert data["window_hours"] == 3
    assert data["source"] == "vault"


def test_budget_set_with_no_flags_exits_2(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "set"])
    assert result.exit_code == 2
    assert "at least one" in result.stderr


def test_budget_set_out_of_range_exits_1_and_names_the_knob(vault: Path) -> None:
    result = runner.invoke(app, ["budget", "set", "--skip-at-pct", "500"])
    assert result.exit_code == 1
    assert "skip_at_pct" in result.stderr
    assert not (vault / "scout-config.yaml").exists()


def test_budget_set_then_show_round_trips(vault: Path) -> None:
    assert runner.invoke(app, ["budget", "set", "--daily-usd", "200", "--window-hours", "3"]).exit_code == 0
    result = runner.invoke(app, ["budget", "show", "--json"])
    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert (data["daily_usd"], data["window_hours"]) == (200, 3)
    assert data["skip_threshold_usd"] == 22.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_budget_cli.py -v`

Expected: FAIL at import — `ImportError: cannot import name 'apply_updates' from 'scout.scripts.budget_config'`.

- [ ] **Step 3: Implement the writer**

Append to `scout/scripts/budget_config.py`, and extend its imports to:

```python
import os
import re
from pathlib import Path
from typing import Any

from scout import paths
from scout.scripts.budget_check import CONFIG_BOUNDS, BudgetConfig, load_config_with_source
```

Then append:

```python
class BudgetWriteError(Exception):
    """A `budget set` could not be applied. Carries a user-facing message."""


_BLOCK_HEADER = "budget:"
_TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*:")
_INDENTED_KEY_RE = re.compile(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*:")
_APPENDED_BLOCK_COMMENT = (
    "# Budget gating for scheduled runs — inspect with `scoutctl budget show`.\n"
    "# Written by `scoutctl budget set`; safe to edit by hand.\n"
)


def validate_updates(updates: dict[str, float | int]) -> None:
    """Raise :class:`BudgetWriteError` on any out-of-range value.

    The read path falls back to the default with a warning on a bad value —
    tolerance belongs there, because a mangled vault must never block a run. A
    `set` is a deliberate user action, so the same value is an error: storing
    something the gate will ignore would leave the UI showing a lie.
    """
    for field_name, value in updates.items():
        if field_name not in CONFIG_BOUNDS:
            raise BudgetWriteError(f"unknown budget field: {field_name}")
        low, high = CONFIG_BOUNDS[field_name]
        if not (low <= value <= high):
            raise BudgetWriteError(
                f"{FIELD_TO_YAML_KEY[field_name]}={value} is outside the usable range [{low}, {high}]"
            )


def _fmt(value: float | int) -> str:
    """Emit the value the way a person writes it: 90, not 90.0; 12.5 stays 12.5."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _find_block_start(lines: list[str]) -> int | None:
    """Index of the top-level ``budget:`` header line, or None.

    Matched at zero indent only — a `budget:` key nested under some other
    subtree is a different key, the same rule the read-side scanner follows.
    """
    for i, line in enumerate(lines):
        if line.rstrip() == _BLOCK_HEADER and not line.startswith((" ", "\t")):
            return i
    return None


def _find_block_end(lines: list[str], start: int) -> int:
    """Index one past the block's last content line.

    The block ends where the next top-level key opens (or at EOF). Trailing
    blank lines are excluded so an inserted key lands beside its siblings
    rather than after the blank separator.
    """
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if _TOP_LEVEL_KEY_RE.match(lines[i]):
            end = i
            break
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


def _block_indent(lines: list[str], start: int, end: int) -> str:
    for i in range(start + 1, end):
        if match := _INDENTED_KEY_RE.match(lines[i]):
            return match.group(1)
    return "  "


def _append_block(text: str, yaml_updates: dict[str, float | int]) -> str:
    """Append a complete ``budget:`` block, defaulting the unspecified knobs.

    Writing all four rather than only what was passed means the file states the
    whole gate, so the next reader (human or `budget show`) sees the real
    configuration instead of a partial override plus invisible defaults.
    """
    defaults = BudgetConfig()
    complete: dict[str, float | int] = {
        "daily_usd": defaults.daily_budget_usd,
        "window_hours": defaults.window_hours,
        "skip_at_pct": defaults.skip_threshold_pct,
        "failure_backoff_minutes": defaults.failure_backoff_min,
    }
    complete.update(yaml_updates)
    body = "".join(f"  {key}: {_fmt(value)}\n" for key, value in complete.items())
    prefix = text if not text or text.endswith("\n") else text + "\n"
    return f"{prefix}\n{_APPENDED_BLOCK_COMMENT}{_BLOCK_HEADER}\n{body}"


def apply_updates(text: str, updates: dict[str, float | int]) -> str:
    """Return ``text`` with the canonical ``budget:`` block updated in place.

    Only the scalar lines for keys in ``updates`` are rewritten. Every other byte
    — other subtrees, comments, blank lines, the trailing-newline convention —
    is preserved. Pure: no filesystem, so the byte-preservation guarantee is
    testable directly.
    """
    yaml_updates = {FIELD_TO_YAML_KEY[field]: value for field, value in updates.items()}
    lines = text.splitlines(keepends=True)

    start = _find_block_start(lines)
    if start is None:
        return _append_block(text, yaml_updates)

    end = _find_block_end(lines, start)
    remaining = dict(yaml_updates)
    for i in range(start + 1, end):
        match = _INDENTED_KEY_RE.match(lines[i])
        if match is None or match.group(2) not in remaining:
            continue
        indent, key = match.group(1), match.group(2)
        newline = "\n" if lines[i].endswith("\n") else ""
        lines[i] = f"{indent}{key}: {_fmt(remaining.pop(key))}{newline}"

    if remaining:
        indent = _block_indent(lines, start, end)
        lines[end:end] = [f"{indent}{key}: {_fmt(value)}\n" for key, value in remaining.items()]

    return "".join(lines)


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def write_budget(
    updates: dict[str, float | int],
    *,
    data_dir: Path | None = None,
    max_attempts: int = 4,
) -> dict[str, Any]:
    """Apply ``updates`` to the vault config; return the resulting show payload.

    Re-reads and re-applies if the file changes between our read and our
    replace, matching the app's ``GuardedFileWrite`` contract. scout-config.yaml
    has several producers — bootstrap writes version stamps and connectors into
    it — so a blind write can silently drop a concurrent one.
    """
    validate_updates(updates)
    target = paths.require_data_dir(data_dir)
    config_path = paths.config_path(target)

    for _ in range(max_attempts):
        try:
            text = config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = ""
        except OSError as e:
            raise BudgetWriteError(f"cannot read {config_path}: {e}") from e
        mtime_at_read = _mtime_ns(config_path)

        updated = apply_updates(text, updates)
        if updated == text:
            return show_payload(target)

        # A concurrent writer landed between our read and now → loop and
        # reapply onto their content rather than overwriting it.
        if _mtime_ns(config_path) != mtime_at_read:
            continue

        tmp = config_path.with_name(f"{config_path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(updated, encoding="utf-8")
            os.replace(tmp, config_path)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise BudgetWriteError(f"cannot write {config_path}: {e}") from e
        return show_payload(target)

    raise BudgetWriteError(
        f"{config_path} kept changing under us — another writer is active; nothing was written"
    )
```

Update `__all__` to:

```python
__all__ = [
    "FIELD_TO_YAML_KEY",
    "BudgetWriteError",
    "apply_updates",
    "show_payload",
    "validate_updates",
    "warn_if_legacy_dotfile",
    "write_budget",
]
```

- [ ] **Step 4: Add the `set` command**

In `scout/cli.py`, after `budget_show_cmd`, add:

```python
@budget_app.command("set")
def budget_set_cmd(
    daily_usd: float | None = typer.Option(None, "--daily-usd", help="Daily budget estimate, USD."),
    window_hours: int | None = typer.Option(None, "--window-hours", help="Rolling window for cost accumulation."),
    skip_at_pct: float | None = typer.Option(
        None, "--skip-at-pct", help="Skip a session at this percent of the window budget."
    ),
    failure_backoff_minutes: int | None = typer.Option(
        None, "--failure-backoff-minutes", help="Back off this many minutes after a failed run."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the resulting config as JSON."),
) -> None:
    """Write one or more budget knobs to the vault's scout-config.yaml."""
    import json as _json

    from scout.scripts.budget_config import BudgetWriteError, write_budget

    candidates = {
        "daily_budget_usd": daily_usd,
        "window_hours": window_hours,
        "skip_threshold_pct": skip_at_pct,
        "failure_backoff_min": failure_backoff_minutes,
    }
    updates = {field: value for field, value in candidates.items() if value is not None}
    if not updates:
        typer.echo(
            "nothing to set — pass at least one of --daily-usd, --window-hours, "
            "--skip-at-pct, --failure-backoff-minutes",
            err=True,
        )
        raise typer.Exit(2)

    try:
        payload = write_budget(updates)
    except BudgetWriteError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    if as_json:
        typer.echo(_json.dumps(payload, indent=2))
        return
    typer.echo(f"wrote {payload['config_path']}")
    typer.echo(f"window:  {payload['window_hours']}h → ${payload['window_budget_usd']:.2f}")
    typer.echo(f"skip at: {payload['skip_at_pct']:.0f}% → ${payload['skip_threshold_usd']:.2f}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_budget_cli.py -v`

Expected: PASS, all tests.

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest tests/ -q && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests`

Expected: PASS, no lint findings.

- [ ] **Step 7: Verify against the real vault, read-only first**

Run: `.venv/bin/python -m scout.cli budget show`

Expected: reports `~/Scout/scout-config.yaml`, `source: defaults`, and `skip at: 80% → $8.34` — confirming the finding that motivated this work. If a dotted `.scout-config.yaml` is present, the dotfile warning appears on stderr.

Do **not** run `budget set` against the real vault as part of this task — that is the user's call to make from the app in Plan 2, or deliberately from the CLI.

- [ ] **Step 8: Commit**

```bash
git add scout/scripts/budget_config.py scout/cli.py tests/unit/test_budget_cli.py
git commit -m "feat(budget): scoutctl budget set

Line-surgical writer: only the named scalar lines inside the budget:
block change, so other subtrees and every comment survive byte-for-byte.
scout-config.yaml has several producers, so the write is tmp + os.replace
behind an mtime guard that re-applies rather than clobbering a
concurrent bootstrap write.

Out-of-range values are an error here, unlike on the read path, where
tolerance exists so a mangled vault cannot block a run.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Plan Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 canonical block; `budgets:` removed; four test sites | Task 1 |
| §1 `max_per_session_usd` dropped | Task 1 (parity test asserts exactly four keys) |
| §2 canonical keys, legacy fallback, two-pass precedence, bounds, `--verbose` provenance | Task 2 |
| §3 defaults parity test | Task 1 |
| §4 `budget show --json`, `source` values, derived fields | Task 3 |
| §4 dotfile warning | Task 3 |
| §4 `budget set`, optional flags, validation, surgical write, mtime guard | Task 4 |
| §4 no `budget validate` subcommand | n/a — deliberately absent |
| Testing: canonical parses, both orderings, bounds, no-block, parity, CLI shape, surgical bytes, out-of-bounds exit, append-when-absent, partial flags, mtime retry, perf | Tasks 1–4 |
| §5 app side | Plan 2 |

**Type consistency:** `FIELD_TO_YAML_KEY` keys are the `BudgetConfig` dataclass field names (`daily_budget_usd`, `window_hours`, `skip_threshold_pct`, `failure_backoff_min`) — the same strings `CONFIG_BOUNDS` is keyed by and the same ones `validate_updates`, `apply_updates`, and `write_budget` accept. The CLI maps its flag names onto those field names in one place (`candidates` in `budget_set_cmd`). JSON payload keys are the YAML spellings (`daily_usd`, `skip_at_pct`, `failure_backoff_minutes`) — deliberately different from field names, and asserted as an exact set in `test_budget_show_json_emits_the_payload` so Plan 2 can rely on them.

**Known sharp edge, deliberate:** `source` is coarse. A `budget:` block whose values are all out of range yields an empty `canonical` dict and reports `legacy` or `defaults` — honest, since the defaults are what actually apply, but it means `source == "vault"` is not proof that all four values came from the file. Plan 2's UI uses `source` only to decide which advisory line to show, never to decide what the values are.
