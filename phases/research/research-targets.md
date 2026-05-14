---
phase: research
name: Research Target Selection
slot: target-selection
mode: [research]
requires: null
---

# PHASE 1: SELECT RESEARCH TARGETS

## Step 1a: Check the Research Queue

Read `knowledge-base/research-queue.md`. If {{USER_NAME}} has explicitly queued topics, those take priority.

**Queue item format:**
```markdown
- [ ] Topic or entity name — why this matters / what to look for
```

Checked items (`- [x]`) are done. Unchecked items are the work queue.

## Step 1b: Score Entities for Research Need

If the queue is empty (or after completing queued items), score entities:

**Priority order:**
1. Entities {{USER_NAME}} interacted with this week (from dreaming session logs)
2. 🔴 HIGH priority project entities
3. People entities with thin external context
4. Organizations with no industry/competitive context
5. Technology topics related to active projects

**Skip:** Entities that were researched in the last 7 days (check git log for `research:` commits).

## Step 1c: Pick 1-3 Research Targets

Select targets based on available budget. Each target gets a focused research cycle. Prefer depth on 1-2 targets over shallow passes on many.
