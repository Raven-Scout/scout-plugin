# Changelog

All notable changes to the Scout plugin are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **DM legibility rule** (`phases/connectors/slack.md`) — wrap DMs must never surface a bare internal `#SHORTCODE` tag (expand inline or drop) and must include a clickable link to the day's action-items file (Obsidian URI / `file://` path). Presentation-layer only; the file-side tag-keying machinery is untouched.
- **No-unverified-negatives discipline** (`phases/core/action-items.md`) — before asserting a negative completion-state ("not done", "still your move", "not created") on a connector-observable item, the run must query that connector or mark the claim `[unverified — not queried this run]`.
- **Repo-creation verification** (`phases/connectors/github.md`) — the GitHub scan now lists recently-created/updated repos via `gh repo list`, since a brand-new repo has no PR and is invisible to the PR scans; verify "stand up repo X" items this way before carrying them as not-done.
- **Batch-comment triage mode** (`phases/modes/kb-deep-work.md` Step 2-pre, with a pointer in `phases/core/action-items.md`) — when an inline-comment sweep finds more than N=5 unprocessed `//==<<` markers, switch from resolve-each to inventory/categorize/route into a dated `comment-triage-YYYY-MM-DD.md` index, leaving markers in place and surfacing the index.
- **Research priority preemption** (`phases/research/research-targets.md`) — target selection runs 🔴/`START IMMEDIATELY`/user-directed queue items before the staleness-rotation or opportunistic pick, and surfaces a >1-run-open 🔴 directive as overdue.

### Changed
- **Audits remediate or hand off, never just report** (`phases/modes/kb-deep-work.md` Step 2-ontology) — after producing findings, audits split them `auto-remediable` (fix in-run or hand to the next run as a must-action queue) vs `backlog` (leave; don't auto-create), and the notification states what was *fixed*, not only what's wrong.

## [0.6.0] - 2026-06-07

### Added
- **Variable-length `[#TAG]` action-item IDs** — the recognition grammar now accepts 2–8 `[A-Z0-9]` tags containing at least one letter (e.g. `[#NAHSEND]`, `[#AI3026]`, `[#RSM]`), not just 4-char Crockford. Pure-numeric `[#123]` stays reserved for GitHub issue refs. The generation prompt now encourages meaningful mnemonics, with `action-items new-prefix` as the random fallback. Fixes the real-vault gap where scout-app fell back to brittle `--subject` matching on the most-referenced lines (scout-app#10, #117).
- **Deterministic post-session prefix backfill** — briefing/consolidation runners run `action-items backfill-prefixes` at session end, so every open task carries a stable `[#TAG]` independent of prompt compliance (#113).
- **Cross-language parser contract test** — a golden corpus + SHA-256 checksum guard proving the Python (scout-plugin) and Swift (scout-app) parsers agree, seeded with the historically-broken lines (#113, #117).

### Changed
- **`action-items --by-id` is ambiguity-aware** — errors when a tag is shared by multiple open tasks instead of silently acting on the first (reusable semantic tags can collide; #117).
- **`new_short_prefix` guarantees a letter** — minted Crockford-4 prefixes always contain at least one letter, so every mint is recognized by the widened grammar (#117).

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
