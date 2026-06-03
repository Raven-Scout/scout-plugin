# Changelog

All notable changes to the Scout plugin are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-06-03


### Added
- **One-command installer** — `curl -fsSL …/install.sh | bash` brings up the plugin + engine and hands off to interactive `/scout-setup`.
- **Release pipeline** — single-source-of-truth versioning across all four manifests (`scout.scripts.versioning`), `scripts/release.sh`, a CI version-drift guard, and this `CHANGELOG.md`; `v*` tags publish a GitHub Release.
- **`scoutctl self-update check`** — read-only installed-vs-available version check (robust semver parse; graceful offline error).
- **`auto_update` config** (disabled by default) + `/scout-setup` preference prompt + `/scout-status` update dashboard.

### Changed
- **`/scout-update` updates both surfaces** — refreshes the plugin, then upgrades the vault against the refreshed plugin (sidecar-safe; resolves the new plugin root per shell block).
- **Releases land via PR** — `main` is ruleset-protected, so `release.sh` prepares a release branch + PR and finalizes by tagging the merge.

## [0.4.0] - 2026-06-02

### Added
- Dreaming-proposal backlog ported into the engine (phases, schema, recurring-task primitive).
- `session-tool-log` Stop hook (per-tool accounting reconstructed from the session JSONL).
- 3-way merge for vault-edited `parser.py` on upgrade (Pattern #68).

### Changed
- `connector_health_report`: Pattern #54 cross-mode liveness suppression.
