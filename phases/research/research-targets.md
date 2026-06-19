---
phase: research
name: Research Target Selection
slot: target-selection
mode: [research]
requires: null
---

# PHASE 1: SELECT RESEARCH TARGETS

## Step 1a: Check the Research Queue

Read every `*.md` file in `knowledge-base/research-queue/` (the thin `knowledge-base/research-queue.md` is now just the run log — see the end of this step). Each file is one topic: YAML frontmatter (`title`, `status`, `priority`, `date`, optional `area`) plus a body with the research brief / findings.

`status: open` and `in-progress` are the work queue; `done` and `dropped` are resolved. If {{USER_NAME}} has explicitly queued topics, those take priority.

**Priority preemption (`priority: urgent` items run first).** Before the staleness-rotation guard or any opportunistic "work what this morning surfaced" pick, the run MUST:

1. **Scan the queue for any item with `priority: urgent` (the START-IMMEDIATELY directive) and run it first.** A user `urgent` directive is not just another queue item — it preempts the rotation and the day's incidental find.
2. **Only fall through** to the rotation (Step 1b scoring) or an opportunistic lane when no `urgent` item is outstanding.
3. **Surface as overdue:** any `urgent` item still `open` across **>1 research run** must be called out as **overdue** in the wrap notification, so a starved top priority can't go silent.

This does NOT stop the opportunistic lane (a good incidental find is still worth pursuing) — it fixes the **ordering** (`urgent` first). A "this is the top priority" intention stays inert until the picker mechanically honors it.

After researching a topic, set its frontmatter `status: done` (or `in-progress`) and add findings to the body; write the run's "Last verified …" continuity note to `knowledge-base/research-queue.md` (the run log).

## Step 1b: Score Entities for Research Need

If the queue is empty (no `open`/`in-progress` items, or after completing them), score entities:

**Priority order:**
1. Entities {{USER_NAME}} interacted with this week (from dreaming session logs)
2. 🔴 HIGH priority project entities
3. People entities with thin external context
4. Organizations with no industry/competitive context
5. Technology topics related to active projects

**Skip:** Entities that were researched in the last 7 days (check git log for `research:` commits).

## Step 1c: Pick 1-3 Research Targets

Select targets based on available budget. Each target gets a focused research cycle. Prefer depth on 1-2 targets over shallow passes on many.
