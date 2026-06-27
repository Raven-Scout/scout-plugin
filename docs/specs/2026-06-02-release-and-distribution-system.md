# Release & Distribution System — Design Spec

**Date:** 2026-06-02
**Status:** Approved design (pending implementation plan)
**Repo:** `github.com/jordanrburger/scout-plugin` (public)
**Approach:** C (hybrid) — local release script + CI enforcement + coordinated two-surface updater + `curl|bash` installer + guided sessions + auto-update seam.

## Problem

Scout is distributed publicly (colleagues, friends, OSS) but has no reliable release or update story:

- **Version drift bug (root cause of recent friction):** the version is duplicated across three files — `.claude-plugin/plugin.json` (`0.4.0`), `.claude-plugin/marketplace.json` (`0.3.0`, stale), `engine/pyproject.toml` (`0.4.0`). Because the marketplace manifest advertised `0.3.0`, `claude plugin install` saw "already installed" and never upgraded — forcing a manual cache rsync.
- **No release artifacts:** one ad-hoc `v0.4.0` tag, no GitHub Releases, no CHANGELOG, CI is lint+test only.
- **Updates are manual and uncoordinated:** there are two distribution surfaces and updating them is a multi-step manual dance.
- **No installer:** new users have no one-command bring-up.
- **No auto-update path.**

### Two distribution surfaces (the core complexity)

- **Surface A — the plugin** (`commands/`, `skills/`, `hooks/`, `phases/`, `engine/`): shipped via a Claude Code *marketplace*; updated by `claude plugin` / `/plugin`.
- **Surface B — the vault** (`~/Scout`): the user's data + generated `SKILL.md`/run-scripts, created/upgraded by `scoutctl bootstrap` (the 8-stage, sidecar-safe pipeline). `scout-config.yaml` tracks `version_at_last_setup` / `version_at_last_update`.

A correct update must coordinate both: bump the plugin (new phases/engine) **and** re-run the vault upgrade (re-assemble + 3-way-merge the vault's `SKILL.md` from the new phases).

## Goals

1. **Reliable releases:** one command cuts a consistent, tested, tagged, GitHub-released version; version drift is structurally impossible.
2. **Reliable updates** for existing users: one command updates both surfaces, sidecar-safe.
3. **One-command install** for new users (`curl|bash`), handing off to the guided vault setup.
4. **Guided/interactive sessions are first-class** — setup, update, and auto-update configuration are coherent and share `scout-config.yaml`.
5. **Auto-update seam** designed now; actual auto-*apply* deferred but gated and safe.

Non-goals (YAGNI): auto-apply-on-schedule wiring (seam only); installing the `claude` CLI itself; headless vault bootstrap (setup is deliberately interactive).

## Design

### 1. Versioning — single source of truth

`.claude-plugin/plugin.json` is **canonical**. `marketplace.json` and `engine/pyproject.toml` are *derived*: the release tooling writes the canonical version into them. Hand-editing any one of them in isolation is caught by CI.

A small **tested Python module** `engine/scout/scripts/versioning.py` owns the logic:
- `read_versions() -> dict[file -> version]`
- `assert_in_sync()` — raises if the three disagree (used by the CI guard + test).
- `bump(level|explicit) -> str` and `write_all(version)` — compute + propagate the new version.

Keeping this in Python (not shell) makes it unit-testable and reuses the existing CI.

### 2. `scripts/release.sh [patch|minor|major | X.Y.Z]`

Thin shell wrapper over `versioning.py`. Preconditions: clean tree, on `main`, up to date with `origin/main`. Steps:
1. Compute new version (`versioning.bump`).
2. `versioning.write_all` → updates all three manifests.
3. Promote `CHANGELOG.md` `## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`.
4. Run `ruff check` + `ruff format --check` + `mypy scout` + `pytest` — fail-fast (never tag a red tree).
5. Commit `release: vX.Y.Z`, tag `vX.Y.Z`, push branch + tag.

### 3. CI version-sync guard (permanent drift fix)

- `engine/tests/unit/test_version_sync.py` — asserts the three version fields are byte-identical (runs in the normal test job).
- A step in `lint.yml` invoking `versioning.assert_in_sync` for a fast, explicit signal.

This makes the `0.3.0`/`0.4.0` class of bug impossible to merge.

### 4. Release workflow — `.github/workflows/release.yml`

On `v*` tag push: run the existing test matrix, then create a **GitHub Release** whose notes are the matching `CHANGELOG.md` section.

### 5. `CHANGELOG.md`

New file, Keep-a-Changelog format. `## [Unreleased]` accrues entries during dev; `release.sh` promotes it per cut.

### 6. Two-surface updater — extend `/scout-update`

Today `/scout-update` upgrades the vault only. Extend it to the single "update Scout" command:

1. **Bring the plugin to latest.** Adapts to marketplace type:
   - GitHub marketplace (public users): `claude plugin marketplace update scout-plugin` (git pull) + reinstall.
   - Directory marketplace (maintainer, `~/scout-plugin`): `git -C ~/scout-plugin pull`; the editable venv auto-reflects it.
2. **Ensure the engine venv** via the plugin's existing `scripts/install-venv.sh`.
3. **Upgrade the vault against the _new_ plugin.** Resolve the freshly-installed plugin root (from `claude plugin list` / `installed_plugins.json`) and invoke `<new-plugin-root>/.venv/bin/scoutctl bootstrap upgrade` **by absolute path** — avoiding the stale `$CLAUDE_PLUGIN_ROOT`-in-session problem, so no restart is needed. This is the existing sidecar-safe 8-stage pipeline (including the `parser.py` 3-way merge).
4. **Doctor + report** old→new version and any `.proposed-merge` sidecars.

Plain `/plugin update` remains the Surface-A-only path.

### 7. Installer — `install.sh` (`curl|bash`, new users)

```
curl -fsSL https://raw.githubusercontent.com/jordanrburger/scout-plugin/main/install.sh | bash
```
1. **Preconditions:** verify `claude` CLI present (if missing, point to CC's installer — do not silently install it); ensure `git` + `uv`.
2. `claude plugin marketplace add jordanrburger/scout-plugin` (idempotent).
3. `claude plugin install scout@scout-plugin`.
4. Run `scripts/install-venv.sh` in the installed plugin root.
5. **Hand off:** print "Open Claude Code and run `/scout-setup`."

Idempotent (re-run = marketplace update + reinstall). Lives at repo root, served via raw GitHub; README documents the one-liner. A `--check`/dry-run mode exercises the precondition logic for tests.

**Clean split:** `install.sh` = first-time plugin+engine bring-up · `/scout-setup` = interactive vault creation · `/scout-update` = the one-command updater thereafter.

### 8. Guided/interactive sessions (first-class)

Three coordinated touchpoints sharing `scout-config.yaml`:

- **`/scout-setup`** (guided, first-time): detect connectors, collect details, bootstrap the vault, **and** ask the auto-update preference, writing an `auto_update:` block.
- **`/scout-update`** (the two-surface updater): if `auto_update.enabled` is false, surface a one-time "enable auto-updates?" nudge.
- **`/scout-status`** (exists): the "am I current?" dashboard — installed version, *available* version (from `self-update --check`), and the auto-update setting.

### 9. Auto-update seam (designed now; auto-apply deferred)

`scout-config.yaml` gains:
```yaml
auto_update:
  enabled: false      # opt-in, set via /scout-setup
  channel: stable
```
- **Build now:** `scoutctl self-update --check` — read-only; compares installed vs the marketplace's published version; powers `/scout-status` and the nudge. Unit-tested against a mocked remote version.
- **Deferred (gated):** when `enabled`, a scheduled/heartbeat run runs the coordinated upgrade **only if sidecar-clean**; on any conflict it does not touch the vault and notifies the user (Slack/Telegram) to run `/scout-update`. Reuses the existing scheduler — no new daemon. Auto-update can never leave a broken state (same contract as the manual path).

## Testing

| Component | Test |
|---|---|
| `versioning.py` bump/sync | unit tests (`test_versioning.py`) |
| 3-manifest sync | `test_version_sync.py` + `lint.yml` step |
| `self-update --check` | unit test with mocked remote version |
| Two-surface updater | unit test for new-plugin-root resolution; vault upgrade already covered by `test_bootstrap_upgrade.py` |
| `install.sh` | `--check` dry-run unit/shell test; full curl|bash smoke-verified manually |
| `release.sh` | precondition + dry-run path; full run exercised on first real release |

## Scope

- **Now — priority 1 (release pipeline):** `versioning.py` + `release.sh` + version-sync CI guard + `release.yml` + `CHANGELOG.md`.
- **Now — priority 2 (updates + install + guided):** `install.sh`; two-surface `/scout-update`; `/scout-setup` auto-update prompt; `auto_update` config block; `/scout-status` "update available?"; `scoutctl self-update --check` (read-only).
- **Deferred (seam ready):** flipping auto-update to auto-apply on the schedule.

## Risks / notes

- **CC GitHub-marketplace version resolution** (latest-`main` vs tag-pinned) should be verified during implementation; the design works regardless because version-sync + marketplace.json-on-`main` is the delivery path and tags/Releases are for humans + CHANGELOG.
- **Maintainer vs public marketplace type** — the updater must handle both directory (maintainer) and GitHub (public) marketplaces; step 1 branches on the configured source.
- The installer cannot bootstrap the vault headlessly by design — `/scout-setup` is interactive and stays so.
