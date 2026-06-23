# Changelog

All notable changes to the Scout plugin are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Granola deliverable-invention gate** (`phases/connectors/granola.md`) — a rule upstreamed from accumulated instance experience: a dated commitment or deliverable extracted from a transcript needs a **verbatim source quote** or it's dropped — never attach an urgency marker ("due Friday", "committed to X") the transcript doesn't literally contain; ship without the marker or route to the review queue. Guards two invention traps: topic conflation (don't fuse two adjacent meeting topics into one dated item) and one-off-as-standing (a single mention is not a recurring cadence).

## [0.7.2] - 2026-06-22


### Added
- **Validation Pass dreaming work mode** (`phases/modes/kb-deep-work.md`) — a fourth KB-deep-work mode for flat-score nights whose deliverable is *existing* high-stakes claims re-grounded against live sources (corrected / downgraded / confirmed), explicitly **not** new sections or entities. Includes a verified-scope guard (a "0-drift / confirmed" label and the "N checked / K confirmed" count cover only claims actually queried this run; derived/comparative conclusions inherit their weakest input's status) and a default-lean toward validation over Gap Hunt on quiet nights. The Step 2d depth gate gets a companion exception so a genuine re-verification run passes on its check count rather than being flagged as superficial for adding no net-new content.
- **Knowledge-graph traversal layer (`parser.py`)** — `traverse()` (BFS reachability returning each reachable entity with its hop distance, first-reached relationship, and shortest typed path), `path()` (shortest typed path between two entities), and `to_networkx()` (lazy optional bridge to a `MultiDiGraph` for centrality/components) on `KnowledgeGraph`, plus `traverse` / `path` CLI commands (`--name` source, `--to` target, `--hops`, `--rels` type filter). Pure-stdlib BFS with no third-party dependency; `to_networkx()` imports networkx lazily and raises a friendly error if it's absent, so the core commands never require it.
- **Briefing-mode layer for the connector-based SKILL (#149)** — the dispatcher already resolves the exact run mode (the schedule slot key, weekend included) and spawns the runner with `SCOUT_FORCE_MODE=<slot>`, but the runner prompt told the model to *re-derive* mode from the clock (`date +%H`) — so `weekend-briefing` was indistinguishable from `morning-briefing` and mode rested on fragile clock/weekday derivation. The runner now passes the resolved `$MODE` slot key into the session prompt and the model dispatches on it (a new `phases/core/00-run-modes.md` table maps slot key → behavior, rendered at the top of `SKILL.md`); `manual`/unset runs fall back to a day+hour derivation. Three mode-specific behaviors land with it: a **light weekend briefing** (reduced work-connector scan via a Weekend Scope rule), a **Monday Preview** (next-workday meetings + imminent deadlines, weekend-only), and the **Scout Digest on the briefing side** (`phases/core/action-items.md`) so briefing/consolidation runs maintain the same one-per-day cross-session digest the dreaming phase already writes. Keeps one assembled `SKILL.md` and one cat-4 merge surface; the new sections are `mode`-scoped so they don't leak into `DREAMING`/`RESEARCH`. Design: `docs/superpowers/specs/2026-06-21-briefing-mode-layer-design.md`.

### Changed
- **CI hardening & test reliability (internal)** — shell templates (`templates/**/*.sh.tmpl`), `install.sh`, and the release scripts are now shellchecked in CI; plugin manifests are validated as parseable JSON with required keys (the version-sync guard is regex-based, so a malformed manifest could previously slip past); and the release workflow asserts the pushed tag matches the manifest version and refuses to publish empty release notes. Two CI flakes fixed: the `schedule_tick` event tests now freeze the clock (they relied on wall-clock and failed near 00:00 UTC), and the startup-latency tests take the best of N samples (a single noisy sample on a shared runner could breach the budget).

### Fixed
- **Audit-tail robustness sweep** — twelve small correctness/robustness fixes from the engine audit: `IdMap.load` guards corrupt/zero-byte JSON and drops an `exists()`/`open()` TOCTOU (#54); schedule-overlay new-key slots are copied, not aliased (#55); the action-items diff no longer silently drops duplicate `(section, title)` items — it pairs them 1:1 (#58); the TUI filter cycle guards `FILTER_OPTIONS.index` against a stale mode (#59); `schedule_tick` bounds its in-lock network probe to ~10s instead of ~48s (#60) and catches `ConfigError` from a `runtime: remote` spawn so it emits `slot.fire_failed` instead of crashing (#61); `connector_health_report` uses `Path.glob` so vault paths containing `[`/`]` still match (#63); KB frontmatter parsing uses a line-bounded `---` fence so a body rule can't truncate it (#64); `require_data_dir` collapses its exists()/is_dir() TOCTOU (#65); `new_short_prefix` distinguishes an empty exclude-set from `None` (#68); `fires_at_local` is normalized to zero-padded `HH:MM` (#69); and `kb_pre_filter` reads each KB file's head once per classify instead of twice (#78).

## [0.7.1] - 2026-06-19


### Added
- **Per-file Wishlist & Research Queue (#144)** — the vault's Wishlist and Research Queue move from single markdown files to per-file directories (`docs/wishlist/`, `knowledge-base/research-queue/`), one file per item, so items are easier to edit, track, and reference individually. An **idempotent migration runs automatically during `/scout-update`**: it writes every existing item to the new per-file layout *before* removing the legacy files (no content loss; safe to re-run), and the heartbeat detects open research items in the new layout with a fallback to the legacy file during the transition.
- **`scoutctl phases backport`** — reverse-maps vault brain-file edits (`SKILL`/`DREAMING`/`RESEARCH.md`) back into their source `phases/` fragments, so a future `/scout-update` re-render carries them forward instead of sidecaring (closes the manual half of the back-port gap). Diffs each brain file against its `.scout-state/last-assembled/` snapshot, locates each divergence in its fragment by anchor matching, conservatively re-templatizes (only long/unique vars auto-reverse; short ones like the instance/user name are flagged, never auto-written — so instance data can't leak into the engine), and **only auto-writes pure insertions that round-trip** (re-assembly reproduces the vault line); modified lines (diff `replace`) are downgraded to needs-review so the old text is never left behind. Default is a read-only report (`--apply` writes; `--kind`, `--vault` flags). Never commits/pushes/opens a PR. New module `scout.scripts.phase_backport` (pure, unit-tested) + `phases` CLI sub-app. Design: `docs/superpowers/specs/2026-06-16-scoutctl-phases-backport-design.md`.
- **Engine back-port reminder** (`phases/modes/feedback-processing.md`, Step 1e) — after a run applies a `SKILL`/`DREAMING`/`RESEARCH` proposal, it surfaces a standing reminder (in the wrap notification + as a carried action item) that the engine back-port is owed until its PR merges, pointing at `scoutctl phases backport`. Explicitly **operator-triggered only — never auto-run** (it writes the shared engine). Closes the "applied edits silently never reach `phases/`" loop with a nudge instead of automation.

### Documentation
- **Budget-skip troubleshooting** (`README.md`) — guidance for diagnosing sessions skipped by the budget gate (#143).

## [0.7.0] - 2026-06-17


### Added
- **User-extensible connector probes (#97)** — custom connector probes now live in `~/Scout/connector-probes.local.yaml` (same schema as the shipped registry), merged over the shipped probes with the overlay winning on key collision. The overlay lives in your vault and survives plugin updates — the previous behavior silently wiped custom probes on every update. New `scoutctl connectors probe-registry [--json]` emits the merged registry, and `/scout-setup` now reads it instead of the raw shipped file. A malformed overlay fails loudly (naming the file), never silently dropped.
- **`scoutctl action-items mark-done --undo`** — reopens a completed task (the reopen path the scout-app UI invokes; previously the only action-item operation with no working path). Supports `--by-id` and `--subject` like the other mutators (#116).
- **DM legibility rule** (`phases/connectors/slack.md`) — wrap DMs must never surface a bare internal `#SHORTCODE` tag (expand inline or drop) and must include a clickable link to the day's action-items file (Obsidian URI / `file://` path). Presentation-layer only; the file-side tag-keying machinery is untouched.
- **No-unverified-negatives discipline** (`phases/core/action-items.md`) — before asserting a negative completion-state ("not done", "still your move", "not created") on a connector-observable item, the run must query that connector or mark the claim `[unverified — not queried this run]`.
- **Repo-creation verification** (`phases/connectors/github.md`) — the GitHub scan now lists recently-created/updated repos via `gh repo list`, since a brand-new repo has no PR and is invisible to the PR scans; verify "stand up repo X" items this way before carrying them as not-done.
- **Batch-comment triage mode** (`phases/modes/kb-deep-work.md` Step 2-pre, with a pointer in `phases/core/action-items.md`) — when an inline-comment sweep finds more than N=5 unprocessed `//==<<` markers, switch from resolve-each to inventory/categorize/route into a dated `comment-triage-YYYY-MM-DD.md` index, leaving markers in place and surfacing the index.
- **Research priority preemption** (`phases/research/research-targets.md`) — target selection runs 🔴/`START IMMEDIATELY`/user-directed queue items before the staleness-rotation or opportunistic pick, and surfaces a >1-run-open 🔴 directive as overdue.
- **MIT License** (#135).

### Changed
- **Audits remediate or hand off, never just report** (`phases/modes/kb-deep-work.md` Step 2-ontology) — after producing findings, audits split them `auto-remediable` (fix in-run or hand to the next run as a must-action queue) vs `backlog` (leave; don't auto-create), and the notification states what was *fixed*, not only what's wrong.

### Fixed
- **Concurrent bootstrap could double-acquire the pipeline lock** — `acquire_lock`'s stale-lock pre-check removed the empty lock a racing winner leaves between its `O_EXCL` create and its PID write, letting two callers both "win". Stale recovery now happens only on the create conflict and only for a confirmed-dead PID (#36 residual).
- **`render.parse()` now strips the leading `[#TAG]` prefix** from `subject`/`plain_subject`, matching the Swift reference parser so `--subject` matching and click-to-copy needles agree across scout-plugin and scout-app (#114).
- **`mark-done`/reopen accept uppercase `[X]`** — a task completed as `[X]` is now reopenable, not just `[x]` (#56).
- **launchd plist re-install is idempotent** — `bootout` before `bootstrap` so `/scout-update` no longer emits `Bootstrap failed: 5: Input/output error` and leaves the old job loaded (#23, #48).
- **`bootstrap upgrade` can't hang forever** — `git merge-file` runs with a 30s timeout (#47).
- **Same-day runner backups are never clobbered** — a second `/scout-update` the same day writes `<name>.bak.<DATE>-1/-2/…` instead of overwriting the first run's backup and losing that hand-edit (#62).
- **`bootstrap upgrade` survives a non-UTF-8 or malformed `scout-config.yaml`** — read as UTF-8 with a typed error (exit 10) instead of an internal-error crash (#45).
- **A customized/missing KB schema no longer crashes** the `KnowledgeGraph` constructor (and the TUI) — typed `KBSchemaError`, and `validate()` tolerates entity types with no `properties:` key (#46).
- **TUI session spawner** — action-item titles can no longer break the AppleScript literal or inject shell (correct escaping + sanitized session name), and `osascript` runs off the Textual event loop so the UI doesn't freeze (#52).
- **KB freshness dates are DST-correct and discovery can't hang** — `parse_date` returns timezone-aware values, and KB-file discovery no longer follows symlinks (a symlink loop previously hung session startup) (#53).
- **Malformed/unreadable connectors YAML** surfaces a typed `ConfigError` instead of a raw `OSError` traceback (#43).
- **Action-item prefix backfill is corruption-safe** — it parses the same bytes it edits (closing a TOCTOU window) and registers each prefix as it writes, so a mid-run failure can't desync the id-map and re-mint live prefixes (#41, #42).
- **Plugin-side parser-corpus checksum guard** keeps the cross-language contract corpus byte-identical with scout-app (#115); the test suite is now hermetic against a live `~/Scout` vault.

### Performance
- **No per-file Python cold starts in the KB pre-filter** — `kb-pre-filter` now delegates to `scoutctl hook kb-pre-filter` instead of spawning a `python3 -c` interpreter per KB file via its date-parse fallback; this was the last per-session script still shelling out to Python (#74).

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
