---
phase: core
name: run-modes
slot: run-modes
mode: [briefing, consolidation]
requires: null
---

## Run Modes — Read This First

Your run mode is handed to you by the dispatcher as a **slot key** (the runner prompt states it as ``your run mode is `<slot-key>` ``). Do **not** re-derive the mode from the clock — the slot key is authoritative. Find the matching row below and follow it; every other section in this file is *invoked by* a row here rather than re-deciding the mode on its own.

| `SCOUT_FORCE_MODE` (slot key) | What to run |
|---|---|
| `morning-briefing` | **Full briefing.** Run each connector's **Query — Briefing Data Gathering** section, build the full action-items list, generate meeting prep, and emit the wrap notification **and** the Scout Digest. |
| `weekend-briefing` | **Light weekend briefing.** Apply the **Weekend Scope** rule below: personal-task + calendar focus, abbreviate or skip the heavy work-connector scans. Add the **Monday Preview**. Frame items for a weekend (no "today's standup" urgency). Still emit the wrap notification + Scout Digest. |
| `morning-consolidation`, `midday-consolidation`, `afternoon-consolidation`, `evening-consolidation` | **Delta scan.** Run each connector's **Outbound Scan** and **Inbound Scan** sections plus per-item reconciliation since the last run; update the action-items list and KB; emit the wrap notification + Scout Digest. Skip the full briefing data-gathering. |
| `manual` or unset | Derive the closest mode from the current day and hour (run `date '+%u %H'`: weekday morning → `morning-briefing`; weekend → `weekend-briefing`; weekday midday/afternoon/evening → the matching `*-consolidation`), then follow that row. |

The slot key is descriptive, not a hardcoded clock contract: a row keys on **what the slot means** (briefing vs. light weekend briefing vs. consolidation delta), so re-timing the schedule never changes behavior.

***

### Weekend Scope

On `weekend-briefing` runs, keep the scan light:

- **Run:** calendar (next-workday lookahead — see Monday Preview), personal-task / action-item review, and any KB updates that fall out naturally.
- **Abbreviate or skip:** the heavy work-connector scans (Slack/email/Linear/GitHub deep sweeps). A quick check for anything explicitly addressed to {{USER_NAME}} is fine; a full inbound/outbound reconciliation is not — that's what the weekday consolidation runs are for.
- **Tone:** weekend framing. Surface what genuinely needs weekend attention; don't manufacture workday urgency.
