# `/scout-plan` Implementation Plan

> **For agentic workers:** steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the day-planning command described in `docs/superpowers/specs/2026-09-29-scout-plan-design.md`: engine support for plan markers and estimate calibration, the interactive command, and the briefing rule that carries the markers forward.

**Architecture:**
- **New package `engine/scout/planning/`:**
  - `durations` (the grid),
  - `settings` (the `planning:` block),
  - `calibration` (the log and the factors),
  - `cli` (`scoutctl planning`).
- **New module `engine/scout/action_items/plan_marks.py`**, modelled on `snooze.py`, with five new verbs in `action_items/cli.py` (`set-estimate`, `set-block`, `clear-block`, `clear-plan`, `set-actual`) and `list --with-plan`.
- **No change** to the parser contract or to any existing verb's behaviour.

**Tech Stack:** Python 3.11, pytest, typer. Run from `engine/` with the repo venv.

### Task 1: Grid and durations
- [x] Tests: `tests/unit/test_planning_durations.py` (parse and format `15m`/`1h15m`, round up, grid check, block windows).
- [x] `scout/planning/durations.py`.

### Task 2: `planning:` config block
- [x] Tests: `tests/unit/test_planning_settings.py` (parity with `defaults/scout-config.yaml`, bounds and fallback per field, work window order, grid-aligned lengths).
- [x] `scout/planning/settings.py`, the `planning:` block in `scout/defaults/scout-config.yaml` (times quoted: YAML 1.1 reads a bare `09:00` as base 60).

### Task 3: Calibration log
- [x] Tests: `tests/unit/test_planning_calibration.py`.
- [x] `scout/planning/calibration.py` (`append_entry`, `load_entries`, `calibration`).

### Task 4: Plan markers
- [x] Tests: `tests/unit/test_action_items_plan_marks.py` (insert, replace, order, clear, actual plus log row, grid and kind validation, CRLF, explicit date, still counted as comments until the apps skip them, unparsable user lines left alone, the scan stopping at a child task, one log row per task per day).
- [x] `scout/action_items/plan_marks.py`.
- [x] `estimate`, `block`, `actual` added to `_common._SNOOZE_MARKER_AUTHORS` and `render.COMMENT_METADATA_KEYS`.

### Task 5: CLI and manifest
- [x] Tests: `tests/unit/test_planning_cli.py` (verbs, selector exclusivity, filename pinning, bad durations, `list --with-plan`, `planning show` and `planning calibration`, `planning_v1`, `materialize` keeps the markers).
- [x] Verbs in `scout/action_items/cli.py` (heavy imports inside the functions), `scout/planning/cli.py` registered in `scout/cli.py`, `planning_v1` in `scout/manifest.py`.

### Task 6: Command and phase
- [x] `commands/scout-plan.md`.
- [x] `phases/core/action-items.md`: "Plan Markers Carry Verbatim" hard rule and the day-planning Focus line.
- [x] README command list, `docs/architecture/02-containers.md`, `CHANGELOG.md` `[Unreleased]`.

### Task 7: Verification
- [x] `pytest tests/ --cov` (floor 98%), `ruff check`, `ruff format --check`, `mypy scout`, `shellcheck`.
- [ ] End to end against a real vault and calendar: plan a day, create blocks marked free and private, log actuals, check `planning calibration`, clean up the test events.
