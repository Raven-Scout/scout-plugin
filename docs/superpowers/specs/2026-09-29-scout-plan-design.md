# `/scout-plan`: day planning with calendar blocks and an estimate feedback loop

**Date:** 2026-09-29
**Status:** Design, implemented on this branch, for review
**Surface:** new interactive command `commands/scout-plan.md`; engine `scout/planning/`, `scout/action_items/plan_marks.py`; one rule and one Focus line in `phases/core/action-items.md`

## Context

Scout already knows what the user should do today (the action-items file) and what their day looks like (the calendar connector). It does not connect the two. A day with 20 open to-dos and 5 hours between meetings is overbooked, and nothing tells the user until the evening. Estimates, durations and time blocks exist nowhere in the line grammar, the parser, the KB schema or the apps.

People are also consistently wrong about how long work takes, in the same direction and by a stable amount per kind of work. A planner that never learns keeps making the same plan that fails the same way.

## Goal

In one conversation, the user gets a realistic plan for the day. Scout estimates each task that needs their own time, fits the tasks into the free time on their calendar, and on an explicit yes creates the calendar blocks and records the estimate and block on each task. The next time it runs, it asks how long those blocks really took, and its estimates get better from that.

## Design

### Flow (the command)

1. **Feedback first.** Tasks, open or done, whose `block:` has ended and that have no `actual:` are listed in one question (the listing includes done tasks, because a task checked off after its block is the usual case). Finished ones get `set-actual`. Unfinished ones can be replanned or have their block cleared.
2. **Read the day.** `list_events` over the work window. Busy time is every timed, non-declined event plus a gap around each one, with edges rounded inward to the grid. Earlier Scout blocks count as busy and their tasks as already planned.
3. **Pick candidates** from 🔴, 🟡 and 💡. Skipped: Watching items, waiting or blocked items, snoozed items, personal items unless asked. Each candidate is checked before it is estimated: its age comes from the vault history (`git log -S "[#TAG]"`), because every briefing rewrites the item text, and a linked Linear issue or GitHub issue or PR that is already closed or merged takes the task out of the plan and offers it for check-off.
4. **Estimate.** A kind of work (`deep`, `shallow`, `comms`, `review`), a raw estimate with a one-sentence reason, then a factor: calibrated per kind, else the configured buffer. The result is rounded up to the grid. Each task gets exactly one block, so a task planned above `deep_block_minutes` is not placed: it is listed for the user to split into smaller tasks.
5. **Pack.** Deep work goes into the longest windows. Small tasks share batch blocks. Packing stops at `capacity_pct` of free time, and what does not fit is listed for another day.
6. **Agree, then act.** The plan is shown as a table. After an explicit yes, `create_event` runs per block (busy, private, no notifications by default), then `set-estimate` and `set-block` per task, then a vault commit `plan [HH:MM]:`.

The command only ever edits or deletes events it created: the event id must be recorded in a task's `block:` marker, and `get_event` must show the user as the organizer with no other attendees. The title prefix plus a `[scout:TAG]` marker is enough to count an event as an earlier Scout block (busy time), never to edit or delete it, because anyone can put both into an invite. Step 1 covers every block that has already ended, today's included, so planning tomorrow in the evening still logs today.

### Markers (the grammar)

```
- [ ] [#TAG] **Task title** <separator> body
  - estimate: 45m (raw: 30m, kind: deep)
  - block: 2026-09-30 10:00-10:45 (event: <calendar event id>)
  - actual: 1h (2026-09-30)
```

- The markers are machine metadata, like `snoozed-until`, and are written only by `scoutctl`.
- A marker is **replaced in place**, never stacked.
- The order is fixed: estimate, block, actual, directly under the task line.
- Durations are `15m`, `45m`, `1h`, `1h15m`. Every value sits on `planning.increment_minutes`, and the engine rejects anything off the grid.
- `parser-corpus.json` is **unchanged**. It covers the task line only (its `_doc`), so no three-repo sync is needed.

### Engine surface

| Command | Does |
|---|---|
| `action-items set-estimate <dur> [--raw <dur>] [--kind <k>]` | write or replace `estimate:`; `--kind` without `--raw` stores no raw value (such a sample does not calibrate) |
| `action-items set-block --date --start --end [--event-id]` | write or replace `block:` (edges and length on the grid) |
| `action-items clear-block` | remove `block:` (no-op when absent) |
| `action-items clear-plan` | remove all three markers from a task (no-op when absent); the log keeps any recorded actual |
| `action-items set-actual <dur> [--on <date>]` | write or replace `actual:` and record a row in `.scout-state/planning-log.jsonl`, one per task per day (a correction replaces the earlier row) |
| `action-items list --json --with-plan` | adds `plan` (estimate, raw, kind, block, actual) per item; `--with-plan` without `--json` is an error |
| `planning show [--json]` | effective `planning:` block |
| `planning calibration [--json]` | per-kind `samples`, `median_ratio`, `factor`, `source` |

- The command finds the engine through `SCOUT_SCOUTCTL`, then `${CLAUDE_PLUGIN_ROOT}/.venv/bin/scoutctl`, then `scoutctl` on `PATH`. A `claude --plugin-dir` session gets neither of the last two pointing at the checkout, so testing a checkout means starting Claude Code with `SCOUT_SCOUTCTL=<checkout>/.venv/bin/scoutctl`.
- All verbs take `--by-id` / `--subject` and the optional daily-file path, like `snooze`.
- A line is a marker only when its value parses (`- block: waiting on Priya` is left alone), and the scan for a task stops at the next task line, so a parent never touches a child task's markers.
- The comment lister (`_common.list_comment_lines`) and the HTML renderer still count the markers as comments, exactly as the Mac and iOS apps do. `delete-comment` and `edit-comment` address comments by the index the apps show, so the engine, the Mac app and iOS must start skipping the markers in the same release (see Out of scope).
- `planning show` and `planning calibration` read the vault from `SCOUT_DATA_DIR`; the command sets it to the vault it works in.
- The manifest advertises `planning_v1`.

### Config (`planning:`)

`work_start`, `work_end`, `increment_minutes` (5, 10, 15, 20, 30 or 60), `capacity_pct`, `buffer`, `new_work_buffer`, `deep_block_minutes`, `batch_block_minutes`, `meeting_gap_minutes`, `calibration_min_samples`, `calibration_window`, `calibration_min_factor`, `calibration_max_factor`, `event_title_prefix`, `event_visibility`, `event_availability`.

- Every value is bounded. A bad value warns and falls back to its default, the same as the `agent_sessions` block. `work_start` and `work_end` must sit on the grid too.
- `PlanningSettings()` and `defaults/scout-config.yaml` are pinned equal by a parity test.

### Feedback loop

- `calibration()` takes the last `calibration_window` rows per kind and computes the median of `actual / raw`, clamped to `calibration_min_factor..calibration_max_factor`.
- The factor is used only once a kind has `calibration_min_samples` rows. Until then the plain `buffer` applies.
- Rows are pooled under `all` too.
- Actuals are always the user's answer, never inferred from the calendar or from the check-off time.

### Briefing integration

- A new hard rule in `phases/core/action-items.md` makes carry-forward, section moves (including Recently Completed) and full rewrites copy the markers verbatim, keeps them out of the `- Refs:` line, and forbids writing them by hand.
- A Focus line (gated on `planning_v1`) suggests `/scout-plan review` when past blocks still need their actual time. Otherwise it suggests `/scout-plan` when today has no block.
- `materialize` already copies the previous day verbatim, and a test pins that the markers survive it.

## Decisions

| Fork | Choice |
|---|---|
| Where planning happens | In a Claude conversation. The engine has no Google credentials, and the Calendar MCP already has `list_events` and `create_event`. |
| Automatic vs approved blocks | Approved. The work calendar is shared, so nothing is written without an explicit yes. Same rule as `/scout-work`. |
| Linking an event to a task | `[scout:TAG]` in the description plus the event id in the `block:` marker. The MCP `create_event` has no `extendedProperties`. |
| Event defaults | `busy`, `private`, `notificationLevel: NONE`. Colleagues see busy time, not task titles. Configurable. |
| Grid | 15 minutes by default, enforced by the engine, so estimates, blocks and actuals compare like with like. |
| Source of truth for calibration | An append-only log in the vault (`.scout-state/planning-log.jsonl`), versioned with the user's data. The markers alone would lose history when items are archived. |
| Parser contract | Unchanged. Sub-bullets are outside it. |

## Out of scope (follow-ups)

- **Reading calendar edits back.** A moved block locks the new time, and a deleted block returns the task to planning.
- **Week planning** (`/scout-plan week`) and a weekly capacity line in the briefing.
- **Skipping the markers as comments, in all three at once.** The Mac and iOS parsers read any `  - word: text` sub-bullet as a comment, so for now the markers show up as comments from `estimate`, `block` and `actual`, and the engine counts them the same way to keep comment indexes in agreement. A follow-up lands the carve-out in the engine lister and renderer, the Mac app (with a plan chip on the task card) and iOS together.
- **Several blocks per task**, if splitting long tasks across blocks turns out to be needed.

## Testing

- **Unit tests:**
  - durations and the grid,
  - settings bounds and parity,
  - calibration (defaults below the sample threshold, median, clamping, window, pooling, bad rows),
  - markers (insert, replace, order, clear, grid and kind validation, CRLF, date pinning, not-a-comment in the lister and the renderer),
  - CLI plumbing (selectors, filename pinning, bad durations, `--with-plan`, `planning show` and `planning calibration`),
  - the manifest flag,
  - `materialize` carrying the markers.
- **Coverage** stays above the 98% floor.
- **The command** is exercised end to end against a real vault and calendar, with test blocks marked as free and private, before merge.
