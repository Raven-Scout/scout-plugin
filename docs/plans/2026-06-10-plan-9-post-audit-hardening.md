# Plan 9 — Post-Audit Hardening & Triage Roadmap

**Date:** 2026-06-10
**Status:** Active. Supersedes `docs/plans/archive/PLAN-8-RESUME.md` (archived — its remaining items shipped in v0.5.0/v0.6.0).
**Source:** Full codebase + GitHub audit run 2026-06-10 against main @ `cce4684` (v0.6.0).

This is the sequencing roadmap. Each batch gets its own detailed implementation
plan (written when the batch starts, following the bite-sized TDD plan format).
Batch 1's plan exists: `docs/plans/2026-06-10-batch-1-quick-wins.md`.

---

## Audit snapshot (why this ordering)

- 39 open issues / 25 closed; 0 open PRs; strong velocity (14 closures + two releases in the first week of June).
- 36 of 39 open issues are from the May engine audit: 9 severity:high, 13 medium, 3 low, plus 8 perf-labeled.
- **New finding (not yet filed):** the test suite is not hermetic. With a live `~/Scout` vault, `pytest engine/tests/` fails 8 tests on clean main (e.g. `test_schedule_list_json_emits_full_slot_records` reads the live vault's 11 slots instead of the default 10). Root cause: `paths.data_dir()` falls back to `Path.home()/Scout` and `engine/tests/conftest.py` never isolates `HOME`; `fake_data_dir` is opt-in. CI is green only because runners have no vault.
- Test coverage is bimodal: `action_items/` ~100%, but `scripts/bootstrap.py` (672 LOC, untyped), `schedule_tick.py` (964 LOC), `connector_health_report.py` (710 LOC), the TUI (0 tests), and `hooks/session_tool_log.py` are the gaps — exactly where the high-severity audit bugs cluster.
- God modules: `cli.py` (1,158 LOC), `schedule_tick.py`, `connector_health_report.py`.
- CI gaps: no shellcheck on the `.sh.tmpl` templates, no plugin-manifest validation, no tag↔version check.
- Roadmap docs: `scoutctl bootstrap auto` (#26) and meeting management specs are complete but unimplemented.

---

## Batch 1 — Quick wins (~1 day) ✦ NEXT

Detailed plan: `docs/plans/2026-06-10-batch-1-quick-wins.md`. One PR per task, "Fixes #N" in each.

| Task | Issue | Shape |
|---|---|---|
| Hermetic test suite (autouse HOME/SCOUT_* isolation in conftest) | file new issue | conftest fixture + canary test |
| `render.parse()` strips leading `[#TAG]` prefix | #114 | strict xfails flip to green; remove markers |
| Plugin-side parser-corpus sha256 guard | #115 | one test; both repos verified in sync at `4ebe8ae3…e21a1` (the digest in the issue body is stale — corpus changed in #118) |
| `mark-done --undo` (+ fixes uppercase-`[X]` reopen) | #116, #56 | writer + resolve_target status param + CLI flag |
| launchd `bootout` before `bootstrap` on plist install | #48, #23 | two-line change per installer; fix already spelled out in #23 |
| `git merge-file` timeout in three-way merge | #47 | `timeout=30` + `TimeoutExpired` → `RuntimeError` |

## Batch 2 — Connector probe overlay (#97)

The only issue filed by an external user; plugin updates silently wipe custom
connector probes. Implement the proposed `~/Scout/connector-probes.local.yaml`
overlay merged over the shipped registry; `/scout-setup` reads the union.
Include an upgrade-path note in `/scout-update`.

## Batch 3 — High-severity audit tail

One focused PR each, same train as #106–#112: #41, #42, #43, #45, #46, #52
(TUI shell-injection + Popen off the event loop), #53, #62 (same-day `.bak`
clobber — user-data loss). **Policy:** every fix here that touches
`bootstrap.py`, `schedule_tick.py`, or the TUI brings a unit test and type
hints with it — this is how the untested-module debt gets paid down without a
dedicated refactor sprint.

## Batch 4 — Performance with direct cost impact

- #74 (high): consolidate the 10+ `python3 -c` spawns per pre-session run into one `scoutctl pre-session data` call.
- #84: cut the ~3.5k lines of static instructions loaded into every session — recurring token spend on every scheduled run; likely the highest-ROI perf item. Approach needs a short design note (progressive disclosure / per-mode pruning) before implementation.
- Remaining medium/low perf issues (#77, #78, #80, #82, #83) backfill ambient capacity.

## Batch 5 — CI hardening (~half day)

- shellcheck over `templates/run-*.sh.tmpl` and `templates/scripts/*.sh.tmpl` in `lint.yml`.
- Validate `.claude-plugin/plugin.json` + `marketplace.json` (valid JSON, versions match) in `lint.yml`.
- Release workflow: assert tag == `plugin.json` version before publishing.
- Consider a constraints/lock file for test runs (local click 8.3.3 vs unpinned `>=` deps is how local/CI drift sneaks in).

## Batch 6 — Feature: `scoutctl bootstrap auto` (#26)

Spec is finished (`docs/specs/scoutctl-bootstrap-auto.md`). Phase 1 only:
the state-detecting dispatcher + tests + README. Phases 2–3 (collapse
`/scout-setup`+`/scout-update` into `/scout-bootstrap`, deprecate standalone
subcommands) are follow-ups. This de-risks every install/upgrade — today the
LLM is load-bearing for state detection.

## Deferred (explicitly not now)

- **Meeting management** (`docs/specs/meeting-management.md`) — biggest net-new feature; build it on a quieter bug backlog, after Batch 6.
- Auto-*apply* updates on schedule (seam exists, wiring deferred).
- pip/Homebrew distribution.
- `cli.py` / `schedule_tick.py` / `connector_health_report.py` splits — opportunistic only (see Batch 3 policy), no big-bang refactor.

## Ongoing hygiene

- Label the unlabeled issues (#97, #114, #115, #116); create GitHub milestones mirroring these batches so the issue list reads as a plan.
- Medium/low audit tail (#54–#69) as ambient work.
- Delete the duplicate spec `docs/specs/2026-06-02-release-and-distribution-system.md` (duplicates `docs/plans/` copy) — verify byte-near-duplication first.
- Close #85 (housekeeping label issue — labels already applied).

## Exit criteria for Plan 9

1. Suite is hermetic (passes with a live vault) and stays green on CI matrix.
2. Zero open severity:high audit issues.
3. CI validates shell templates + manifests + tag/version sync.
4. #97 and #116 closed (external-user pain + only unrecoverable scout-app op).
5. `bootstrap auto` phase 1 shipped.
