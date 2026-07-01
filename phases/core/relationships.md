---
phase: core
name: relationships
slot: relationships
mode: [briefing]
requires: null
---

## Relationship Maintenance

Action items track *tasks*; this tracks *relationships*. The goal is the chief-of-staff nudge {{USER_NAME}} can't get from a task list: "you haven't talked to X in a while," "this promise to a person is aging." Run this on briefing-type runs (including the weekend briefing), not on every consolidation delta.

### Keep `last_interaction` fresh

For each person {{USER_NAME}} interacted with this run (email, Slack, meeting, PR thread), set/update `last_interaction:` on their `knowledge-base/people/<slug>.md` entity to the most recent contact date. This is the signal everything below reads.

### Reconnect nudges (cadence-aware, not a fixed timer)

"Tracked" people are the ones who matter to {{USER_NAME}} — derive them the same way `about-you.md` does: top collaborators by interaction frequency and any recurring 1:1 cadence. Do **not** keep a hand-maintained list.

For each tracked person, compare `last_interaction` against their **normal** rhythm:

- A weekly-1:1 contact silent for a month → cooling. A monthly contact silent for a week → fine.
- Surface a cooling relationship as a **low-key digest line**, never a 🔴 (there's no deadline): "Haven't connected with [[people/<slug>]] in N weeks (usually ~weekly)."
- Skip anyone on a known leave/OOO state (check the entity, per the Action Items leave-state rule).

### Aging promises to people

Cross-reference open commitments {{USER_NAME}} made *to a specific person* (from the committed-reply tracking in the Slack/email phases, or KB) against `last_interaction` and elapsed time. A promise made to someone two weeks ago with no movement since is a relationship risk — surface it low-key, attributed to the person, with the original ask.

### Respect the contract

All relationship output obeys `profile/communication.md`. If the contract says relationship nudges are unwanted, or sets quiet hours/cadence, honor it — these are helpful prompts, not nags. Keep them in the **Scout Digest** (a brief "Relationships" note), not as separate interrupts.
