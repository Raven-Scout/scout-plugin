# Budget Configuration — Canonical YAML, `scoutctl budget` CLI & Settings Section — Design

**Date:** 2026-09-08
**Status:** Design — approved, not yet planned
**Repos:** `scout-plugin` (engine, contract owner) and `scout-app` (macOS UI). The
CLI contract in §4 is the interface between them; the app implements against it
and holds no YAML knowledge of its own.

## Problem

Budget gating is live and load-bearing. Every scheduled Scout run is preceded by
`scoutctl budget check` (`scout/scripts/budget_check.py`), whose exit code decides
whether the session runs at all: `0` proceed, `1` skip (over budget), `2` back off
(recent rate-limit or failure). It reads four knobs out of the vault's
`scout-config.yaml`.

Nobody can see or change those knobs without editing YAML by hand, and the
configuration that governs them is currently incoherent in three separate ways.

### 1. The engine ships two budget schemas that do not talk to each other

`scout/defaults/scout-config.yaml` declares:

```yaml
budgets:
  daily_budget_estimate_usd: 150
  max_per_session_usd: 20
```

`budget_check.py` never reads `budgets:`. Its scanner matches only these
`(section, key)` pairs:

| field | YAML location actually read | hardcoded default |
|---|---|---|
| `daily_budget_usd` | `plan.daily_budget_estimate_usd` | `50.0` |
| `window_hours` | `plan.rate_limit_window_hours` | `5` |
| `skip_threshold_pct` | `thresholds.skip_threshold_pct` | `80.0` |
| `failure_backoff_min` | `thresholds.failure_backoff_minutes` | `60` |

Nothing under `scout/` reads `cfg["budgets"]`. Its only consumers are four test
assertions. The shipped default of `150` has never governed anything; the real
default is the `50.0` constant in Python.

### 2. Because of that, a vault with no budget config gates far tighter than intended

The live vault's `scout-config.yaml` has no `plan:` and no `thresholds:` block, so
every run today gates on the hardcoded defaults: a `$50/day` budget prorated to a 5h
window is `$10.42`, and 80% of that means sessions skip once **$8.34** is spent in a
5-hour window. At a measured ~$3.67/session that is roughly two sessions before the
gate closes.

The file that documents the *intended* numbers — `$200/day`, a 3h window, skip at
90% — is `.scout-config.yaml`, **dotted**. `paths.config_path()` documents that
dotfile as one "no code path ever wrote", dead since #207/#202. It reads like live
configuration and is inert. This is how the tight gate stayed invisible.

### 3. There is no supported way to change any of it

No CLI surface, no UI. The only path is hand-editing a file that doubles as
bootstrap state.

## Goals

- **One canonical YAML block** in the engine that genuinely governs budget gating,
  and is provably the same as the defaults the code applies.
- **A supported read/write path** — CLI first, so the app is a client rather than a
  second implementation.
- **A Budget section in the macOS app's Settings** that makes the four knobs, and
  the gate they compute to, legible.
- **No behavior change for existing vaults** on upgrade.

## Non-goals

- Showing spend, cost history, or budget-remaining anywhere in the UI. `UsageRailCard`
  deliberately omits dollar figures (misleading on a quota-based plan seat); this
  design does not reopen that. The Budget section is configuration only.
- Exposing knobs the engine does not enforce. See "Deliberately dropped" below.
- Changing `budget check`'s decision logic, exit codes, or tracker parsing.
- Migrating or deleting the dead `.scout-config.yaml`. We warn about it; we do not
  touch it.

## Design

### 1. The canonical block

`scout/defaults/scout-config.yaml` — the `budgets:` block is **replaced** by:

```yaml
budget:
  daily_usd: 50
  window_hours: 5
  skip_at_pct: 80
  failure_backoff_minutes: 60
```

Two decisions inside this that deserve to be explicit:

**Values stay at today's operative defaults (50/5/80/60), not the dotfile's
200/3/90/30.** Changing the *engine* default silently re-gates every install on
upgrade, including installs whose owners never asked for it. A vault gets the
right numbers by writing an explicit block through the new CLI or UI — which is the
feature. The tight-gate problem is solved by making the state *visible* (§5), not by
moving the default under everyone.

**`max_per_session_usd: 20` is dropped from the YAML.** Nothing under `scout/` reads
it. The per-session cap that actually applies is a literal in the runner —
`--max-budget-usd 20.00` in `run-scout.sh` — while the separate `--max-budget`
option on `bootstrap install`/`upgrade` (default `"5.00"`) only seeds
`connectors.inputs.max_budget` in the vault, which no runner consults. Three
unrelated numbers, none of them wired to `budgets.max_per_session_usd`. Leaving an
unread knob in the shipped
file recreates the exact defect this design exists to remove. Recorded as a
follow-up below rather than silently dropped.

Four test sites assert the old key and move with it:
`tests/unit/test_config.py:20,33`, `tests/unit/test_config_vault_file.py:278`,
`tests/smoke/test_wheel_install.py:97`.

### 2. Reader changes in `budget_check.py`

Canonical keys join the nested scan; the legacy spellings stay as a fallback so
existing vaults keep working untouched:

```python
_CANONICAL_KEYS = {
    ("budget", "daily_usd"):                ("daily_budget_usd",   float),
    ("budget", "window_hours"):             ("window_hours",       int),
    ("budget", "skip_at_pct"):              ("skip_threshold_pct", float),
    ("budget", "failure_backoff_minutes"):  ("failure_backoff_min", int),
}
# _NESTED_CONFIG_KEYS (plan:/thresholds:) and _CONFIG_KEYS (flat) become _LEGACY_*
```

**Precedence cannot come from merging the two maps.** `scan_overrides` is a
single-pass line scanner and is last-wins within a pass, so a merged map would let
line order decide which spelling won. `load_config` therefore scans **legacy first,
then canonical over the result**. A vault carrying both spellings resolves to the
canonical value regardless of where the blocks sit in the file.

`_CONFIG_BOUNDS` validation is unchanged and applies to both spellings — a canonical
`window_hours: 0` falls back to the default with the same stderr warning as the
legacy key does today.

`--verbose` gains one line naming which shape supplied the values, so a vault still
running on `plan:`/`thresholds:` says so out loud rather than looking canonical.

### 3. Defaults parity

`budget_check` deliberately does not use `scout.config.load_config` — it regex-scans
the vault file to keep pyyaml off the pre-run gate path, which is the startup cost
the #74 port existed to eliminate. That stays. The Python `DEFAULT_*` constants
remain operative.

To stop the shipped YAML from drifting back into decoration, a new test parses the
`budget:` block out of `scout/defaults/scout-config.yaml` and asserts field-by-field
equality with `BudgetConfig()`. The shipped file becomes documentation that CI
proves accurate, and the two can never disagree again without a red build.

### 4. CLI contract

**`scoutctl budget show --json`** — the app's read path:

```json
{
  "config_path": "/Users/…/Scout/scout-config.yaml",
  "source": "vault",
  "daily_usd": 200.0,
  "window_hours": 3,
  "skip_at_pct": 90.0,
  "failure_backoff_minutes": 30,
  "window_budget_usd": 25.0,
  "skip_threshold_usd": 22.5
}
```

`source` is one of:

- `vault` — a canonical `budget:` block supplied the values
- `legacy` — `plan:`/`thresholds:` supplied them
- `defaults` — the file has neither; the engine's constants apply

`window_budget_usd` and `skip_threshold_usd` are the existing `BudgetConfig`
properties, serialized so the app can display the effective gate without deriving it.

**`scoutctl budget set [--daily-usd N] [--window-hours N] [--skip-at-pct N]
[--failure-backoff-minutes N] [--json]`** — every flag optional; only flags passed
are written, so a partial edit cannot blank the rest. Values are validated against
`_CONFIG_BOUNDS` **before** any write and rejected with a non-zero exit and a
message naming the offending knob and its range — `set` is a user action, so an
out-of-range value is an error, not the silent default-fallback the read path uses.

The write is **line-surgical**: locate the `budget:` block and replace only its four
scalar lines in place; if absent, append the block with a short comment header.
Every other subtree and every comment in the file survives byte-for-byte. This
matters because `scout-config.yaml` is not single-purpose — `scout/config.py`'s own
docstring notes it "doubles as bootstrap state (version stamps, connectors,
schedule, plan)" written by several producers. A pyyaml round-trip would strip every
comment in it; a whole-file rewrite risks dropping a subtree a newer bootstrap wrote.

Persistence is tmp-in-same-directory → `os.replace`, guarded by an mtime re-check
with bounded retry — the same contract the app's `GuardedFileWrite` implements, so
a concurrent bootstrap write is re-applied onto rather than clobbered.

No `budget validate` subcommand: `set` validates, and there is no second writer for
it to serve. (Contrast `schedule validate`, which exists because the app composes
`schedule.yaml` itself.)

**`budget show` warns** on stderr when a dotted `.scout-config.yaml` exists carrying
budget keys, stating that the file is not read. That dead file is precisely how the
$8.34 gate stayed hidden.

### 5. App: `BudgetSettingsService` + Settings section

`BudgetSettingsService` (`@MainActor`, `ObservableObject`) is constructed in
`AppState` from the same `scoutctlExecutable` / `scoutctlArgumentsPrefix` /
`ProcessRunner` trio `ScheduleEditService` already takes. `load()` shells
`budget show --json` and publishes; `save()` shells `budget set` and reloads. **The
app parses and emits no YAML.**

`SettingsView` gains a **Budget** section between "Claude Code" and "Proposals":

- Four numeric fields, one per knob.
- An explicit **Save** button. Writes are subprocesses, not `@AppStorage` — they
  cannot be fired per-keystroke the way the existing toggles are.
- A derived readout beneath, recomputed as you type:
  *"3h window → $25.00 budget · sessions skip at $22.50"*
- When `source` is `defaults`, a warning line states it plainly: no budget block in
  this vault, running on engine defaults, sessions skip at $8.34. When `source` is
  `legacy`, a line noting the old key shape and that saving migrates it.
- Non-zero exit from `budget set` surfaces its stderr, as `ScheduleEditService`
  failures do.

**On the duplicated formula.** The derived readout has to update as you type, before
any save, so the computation necessarily exists in Swift as well as Python. Rather
than a shared fixture, the app recomputes on **load** and compares against the
engine-returned `window_budget_usd` / `skip_threshold_usd`; a mismatch surfaces as a
warning. Self-checking, and no cross-repo artifact to keep in sync.

The Swift mirror must reproduce `BudgetConfig`'s **two-step rounding**, not the
algebraically-equivalent single expression:

```
window_budget_usd  = round(daily × hours / 24,        2)
skip_threshold_usd = round(window_budget_usd × pct / 100, 2)   # rounds the ROUNDED window
```

The intermediate rounding is load-bearing. At the engine defaults the two forms
disagree — `round(round(50×5/24,2)×0.8,2)` is `8.34`, while `round(50×5/24×0.8,2)`
is `8.33`. A Swift mirror that collapses the chain would trip the parity warning on
the default configuration, on first launch, for every user. Both the Swift
implementation and the Python parity test must pin a case in this disagreeing
family (50/5/80 is the natural one) rather than only 200/3/90, where the two forms
happen to agree.

**One refactor.** `SettingsCard`, `SettingsRow`, `SettingsField` and `SettingsInput`
are `private` inside a ~400-line `SettingsView.swift`. They move to
`Scout/Shell/SettingsComponents.swift` as internal, so the budget section is its own
file rather than growing that one further.

## Testing

**Python**

- Canonical `budget:` block parses to the right `BudgetConfig`.
- Canonical beats legacy with **both** file orderings (the regression the two-pass
  scan exists to prevent).
- Out-of-range canonical values fall back to defaults with a warning, as legacy does.
- No block at all → engine defaults.
- **Parity test**: `defaults/scout-config.yaml`'s `budget:` block equals
  `BudgetConfig()` field-by-field.
- New `test_budget_cli.py`: `show --json` shape and every `source` value; `set`
  writes surgically (assert all other bytes of the file unchanged); `set` rejects
  each out-of-bounds knob with a non-zero exit; append-when-absent; partial flags
  leave unpassed knobs alone; mtime-guard retry.
- `budget show` joins the startup-latency perf guard — it sits on an interactive
  app path.

**Swift**

- `BudgetSettingsServiceTests` against a stub `ProcessRunner`, mirroring
  `ScheduleEditServiceTests`: decode of each `source`, save argument construction,
  partial-edit behavior, non-zero-exit error surfacing.
- Derived-value parity check against engine-returned values.

## Migration & compatibility

- Existing vaults with `plan:`/`thresholds:` keep working unchanged; `budget show`
  reports `source: legacy`, and the first `budget set` writes the canonical block.
  Both spellings may coexist; canonical wins.
- Vaults with neither are unaffected on upgrade — same constants as today, now
  visible and labelled as defaults.
- `budgets:` in the packaged defaults disappears. Nothing under `scout/` reads it;
  only the four test assertions move.

## Follow-ups (explicitly out of scope)

1. **Wire the per-session cap to config.** `max_per_session_usd` is dropped from the
   YAML here because nothing reads it. Reconciling the three unrelated per-session
   numbers (the `run-scout.sh` literal, the `bootstrap --max-budget` seed landing in
   `connectors.inputs.max_budget`, and the dropped `budgets.max_per_session_usd`)
   means teaching the runner to read config first, then re-adding one knob to
   `budget:` and to the Settings section.
2. **The dead `.scout-config.yaml`.** `budget show` warns; it does not migrate or
   delete. A `scoutctl budget migrate-legacy` that lifts the dotfile's values into
   the canonical block would close the loop.
3. **The knobs with no consumer** — `min_session_gap_minutes`,
   `extra_session_threshold_pct`, `off_peak_start`/`end` — are described in the
   dotfile and enforced nowhere. Each needs enforcement before it deserves UI.
