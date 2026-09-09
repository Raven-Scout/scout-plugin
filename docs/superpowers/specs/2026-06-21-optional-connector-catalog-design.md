# Optional-connector catalog — design

**Date:** 2026-06-21
**Status:** Proposed (design) — for review
**Closes:** the absence of a way for users to discover and turn on optional connectors without hand-authoring them. Generalizes the one-off "Google Messages connector" need into a curated, browsable catalog.
**Folds in:** #172 — the email/`gmail` canonical-key mismatch — as a precondition of the catalog's key model (see §7).

## Problem

Scout ships a fixed default connector set (calendar, slack, github, linear, granola, drive, email, …). Adding anything outside that set — e.g. Google Messages for personal texts — today means hand-editing **three** places (`connectors.yaml` roster, `connector-probes.yaml` detection, `phases/connectors/<name>.md` behavior) and knowing how assembly gates on `enabled_connectors`. There is no way for a user to **browse** connectors Scout already knows how to drive but doesn't enable by default, and **turn one on** without that authoring.

We want a **curated, in-engine catalog**: a user browses the optional connectors that ship with Scout and flips one on with a single command (or a wizard toggle), no authoring. (Community-contributed connectors and a packaged-distribution format are explicitly **out of scope** — see Alternatives.)

## What exists already (the thing we extend)

The enable *mechanism* is largely built; the gap is discovery + a turn-on flow + a default-off marker.

| Piece | State |
|---|---|
| `connectors.yaml` (roster) | "Single source of truth" with `display_name`, `tier (official\|auto_discovered\|community)`, `capabilities`, `remediation`. Loaded by `engine/scout/connectors.py` (`Connector` dataclass + `ConnectorRegistry`, `load_registry`), which already layers an **optional vault overlay** on the packaged seed. |
| `connector-probes.yaml` | Declarative detection (primary MCP tool / bash + fallbacks + `needs_user_input`), user-extensible via `connector-probes.local.yaml`. Drives `/scout-setup` detection. |
| `phases/connectors/<name>.md` | Per-connector behavior, gated by `requires: <key>`; `_assemble()` includes a section only when its `requires` is in `enabled_connectors`. **So enabling a connector = adding its key to config + re-rendering.** |
| `scoutctl connectors …` | Sub-app already has `list` / `show` / `reload` / `probe-registry`. |

## Goals / non-goals

**Goals**
- Mark connectors as **optional** (default-off; never auto-enabled by detection).
- **Browse** the catalog (`scoutctl connectors catalog`) + surface it in the setup/update wizard.
- **Turn one on** (`scoutctl connectors enable <key>`) with guided setup: config write, input collection, setup steps, probe verify, and a brain-file re-render so it takes effect.
- Seed the catalog with **Google Messages** as the first entry / proof, de-personalized.
- Everything tenant-agnostic — **public engine.**

**Non-goals**
- No community contribution flow or packaged/distributable connector format (Alternative B, future).
- No scout-app catalog UI yet (later phase; the CLI/`--json` is the primitive it will consume).
- No reclassifying existing default connectors as optional.

## Design

### 1. Data model — extend `connectors.yaml` (+ `Connector` in `connectors.py`)
Add to a connector's roster entry:
- `optional: true` — default-off; detection never auto-enables it. Absent ⇒ today's behavior (default connector).
- `catalog:` block:
  - `summary` — one line for the `catalog` list.
  - `description` — what it scans / does.
  - `requirements` — what the user must have (e.g. "an Android phone signed in to Google Messages web").
  - `setup` — ordered list of manual steps printed on enable.

`optional` is **orthogonal** to `tier`: a curated optional connector is `tier: official, optional: true`. Extend the `Connector` dataclass + loader to parse the two fields (default `optional=False`, `catalog=None`); existing entries are unaffected.

### 2. Browse — `scoutctl connectors catalog [--json]`
Lists entries where `optional: true`, showing `summary`, `requirements`, and an **enabled/available** marker (computed against `scout-config.yaml` `connectors.enabled`). `--json` for the wizard and a future app screen.

### 3. Enable / disable
`scoutctl connectors enable <key>`:
1. Validate `<key>` exists and is `optional` (a default connector is already on; refuse with a hint).
2. Add `<key>` to `connectors.enabled` in `scout-config.yaml` (idempotent), preserving other keys.
3. Collect the probe's `needs_user_input` interactively → write to `connectors.inputs`.
4. Run the probe (primary + fallbacks) → report reachable / not-yet (not a hard failure — manual setup may be pending).
5. Print the `catalog.setup` steps for manual bits (e.g. *pair Google Messages at messages.google.com/web*).
6. **Re-render the brain files** so the connector's `requires:`-gated sections take effect now — invoke the cat-4 assembly path (`_assemble` + `_stage_cat4_upgrade` merge against the `.scout-state/last-assembled/` snapshot). Adding a connector's sections is **additive**, so the 3-way merge is clean (no conflict with vault edits); live `SKILL.md`/`DREAMING.md` update and the snapshot advances. If the re-render would conflict (unexpected), fall back to instructing `/scout-update` and leave config set.

`scoutctl connectors disable <key>`: remove from `connectors.enabled` (config only; a re-render on next `/scout-update` drops the sections). Leaves collected inputs in place.

### 4. Default-off + detection
`/scout-setup` probe-detection still auto-enables the **default** set. An optional connector whose probe passes is **surfaced, not enabled** — "available, want to add?". Detection ≠ activation for catalog connectors.

### 5. Wizard integration (`/scout-setup` & `/scout-update`)
After default detection, an **"Optional connectors you can add"** step reads `scoutctl connectors catalog --json` and offers each as an opt-in toggle; opting in runs the `enable` flow (steps 2–6). `/scout-update` highlights catalog connectors that became available since the user's recorded version. (Wizard prose lives in the setup/update command flow; the data + actions come from the CLI primitive.)

### 6. Seed entry / proof — Google Messages
The first catalog connector, demonstrating the format end-to-end:
- `connectors.yaml`: `optional: true`, `tier: official`, `catalog` block (summary "personal text messages", requirements "Android + Google Messages web", setup = the browser-pairing steps).
- `connector-probes.yaml`: a probe asserting the browser-automation tool is present (actual session pairing is a manual setup step, verified at runtime).
- `phases/connectors/google-messages.md`: de-personalized personal-text scanning behavior, `requires: google_messages`, `mode: [briefing, consolidation]`.
- **No contacts ship.** The contact list is vault-only state the user accumulates; the engine ships scanning behavior only.

### 7. Canonical-key invariant (precondition — fixes #172)

The `enable` flow (§3: add `<key>` to `connectors.enabled`, then re-render so the `requires:`-gated sections appear) only works if a connector is named by **one** key string across the namespaces a connector key lives in:

| Namespace | File | Mail connector today |
|---|---|---|
| Probe / detection key (written into `connectors.enabled`) | `connector-probes.yaml` | `gmail` |
| Config enabled key | `scout-config.yaml` `connectors.enabled` | `gmail` |
| Phase gate | `phases/connectors/<name>.md` `requires:` | **`email`** |
| Roster entry | `connectors.yaml` | `mcp:claude_ai_Gmail` (health-id namespace) |

Make agreement a **hard invariant of the catalog model**, enforced in validation: `scoutctl connectors` (and a CI check) asserts that every phase `requires:` resolves to a roster entry and equals the key its probe emits. A connector whose namespaces disagree is **silently un-enableable** — `enable <key>` writes a key the phase gate never matches, so the sections never render: no error, just missing behavior. That is the exact trap the catalog's turn-key promise must not inherit.

**Existing violation (#172).** The mail connector already breaks the invariant: its probe + config key is `gmail`, but the phase is `requires: email`, so `select_sections` drops the **entire** email phase from the assembled `SKILL.md` on real vaults (verified — a live `SKILL.md` carries zero email-phase markers while the Slack control is present). Every *other* connector's probe key already equals its phase `requires:` (`slack`, `calendar`, `linear`, `github`, `granola`, `drive`, `claude_sessions`); mail is the lone diverging case. Reconciling it is a **precondition** for the catalog — otherwise the catalog ships atop a key model already inconsistent for a *default* connector, and the first provider-variant connector (Outlook/IMAP) repeats the break.

**Resolution (recommended).** Standardize on the **provider-neutral** key `email` — already the canonical name in the default set above, throughout this spec, and in the phase. Map the Gmail probe to emit `email`, keep `gmail` as a recognized **alias**, and normalize `gmail → email` in `connectors.enabled` idempotently on `/scout-update` so existing vaults migrate with no manual edit. A future Outlook/IMAP probe then maps to the same `email` capability key — one phase, many providers. *Lower-effort alternative, rejected:* rename the phase to `requires: gmail` (one line, no migration) — but it couples a capability to one provider and forces a second key per mail provider later. Final call sits with this catalog work.

### 8. Testing
- Schema: `connectors.yaml` with `optional` + `catalog` parses; `load_registry` exposes both; absent fields default safely.
- `catalog`: lists only `optional` connectors with correct enabled/available markers; `--json` shape stable.
- `enable`: writes config idempotently, collects `needs_user_input`, prints setup, probes, triggers a clean re-render that includes the new sections; refuses a non-optional or unknown key. `disable`: reverts config; inputs preserved.
- Assembly: an enabled optional connector's sections appear in the target brain file and **do not leak** into other targets; disabling excludes them on re-render.
- Wizard: optional connectors are never auto-enabled even when their probe passes.
- Key-consistency invariant (§7): every phase `requires:` resolves to a roster entry and equals its probe-emitted key; the `gmail → email` alias normalizes a legacy config; an intentionally-mismatched fixture **fails** validation (guards against silently un-enableable connectors, the #172 class of bug).

## Alternatives considered

**B — Connector "package" directories** (`connectors/<name>/` bundling roster+probe+phase+script). The right model *if* community sharing were a goal — but that's scoped out, and it's a sizeable refactor (migrate every connector, rework roster/probe/assembly loading) for a deferred maybe. The chosen design leaves B as a clean future path: a package format could *generate* the three artifacts this design already consumes.

**C — A separate `connectors-catalog.yaml` manifest.** Adds a *fourth* place a connector is defined, which can drift from the roster/probe/phase. Rejected in favor of making the existing roster the catalog.

## Risks / open questions
- **Enable re-render coupling.** `enable` invoking the cat-4 assembly path is the turn-key promise but couples the command to the merge machinery. Mitigation: the change is purely additive (new `requires:`-gated sections), so the merge is clean; the `/scout-update` fallback covers the unexpected-conflict case. Worth a careful look in review.
- **Probe semantics for setup-gated connectors.** Google Messages can't be fully probed from the CLI (pairing is manual/browser). The probe asserts the *capability* (browser tool present); true readiness is confirmed at run time. Catalog entries should be honest that "probe passed" ≠ "set up".
- **Disable leaves inputs/state.** Intentional (re-enabling shouldn't re-prompt), but worth confirming.
- **Legacy-config migration for #172.** Existing vaults carry `gmail` in `connectors.enabled`; the `gmail → email` normalization must run idempotently on `/scout-update` and only when no genuine provider distinction is intended. Additive and low-risk, but it edits live config — confirm the normalization is a no-op on a vault that has already migrated.

## Out of scope / future
- Community-contributed connectors + a packaged distribution format (Alternative B).
- scout-app catalog screen (consumes this CLI/`--json`).
- The de-personalized Patterns batch; the briefing-mode layer (spec #149, merged) and enrichment-recall subsystem (spec #150) are tracked separately.
