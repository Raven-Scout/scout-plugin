---
phase: connector
name: fathom
slot: inbound-scan
mode: [consolidation, briefing]
requires: fathom
---

## Fathom Inbound Scan — Meeting Transcripts & Summaries

Pull meeting recordings from Fathom since the last run and extract actionable information.

### Retrieve Recent Meetings

Use `mcp__fathom__list_meetings` with `created_after` set to the last run timestamp (ISO 8601) and `include_summary: true`, `include_action_items: true` to get an overview of recent meetings.

For each meeting returned, review the summary and action items first. If more detail is needed (e.g., ambiguous commitments, unclear context), pull the full transcript with `mcp__fathom__get_meeting_transcript` using the `recording_id` and `url` from the listing.

For each meeting, extract:

1. **Action items mentioned** — any commitment like "I'll do X," "{{USER_NAME}} will handle Y," or "we need to Z." Record exactly what was said, by whom, and in what context. Cross-reference with Fathom's own action items from the listing.
2. **Decisions made** — any agreement or conclusion reached during the meeting. Record the decision, who was involved, and what it supersedes (if anything).
3. **Commitments by others** — things other people said they'd do that {{USER_NAME}} should track. These become Watching items.
4. **Deadlines or timelines mentioned** — any dates or timeframes referenced ("by Friday," "next sprint," "end of quarter").
5. **Open questions** — things that were raised but not resolved in the meeting.

### Critical Warning: Transcripts Are Signals, Not Facts

**Meeting transcripts are SIGNALS, not FACTS.** A transcript is a noisy recording of a conversation — people misspeak, context is lost, and not every statement represents a real commitment.

Every action item extracted from a transcript is a **candidate** that must be verified:
- Did {{USER_NAME}} already complete this? (Check outbound messages, code activity, email)
- Was this superseded by a later discussion? (Check more recent transcripts, Slack messages)
- Is this actually assigned to {{USER_NAME}}? (Transcripts often attribute items vaguely — verify against the issue tracker)
- Is this the same item already tracked elsewhere? (Deduplicate against existing action items)

**Never write a transcript-sourced action item directly to the action items file without cross-checking it first.**

### Attendee Extraction

Note all meeting attendees from the `calendar_invitees` field and transcript speaker names. Cross-reference with `people.md` and add any new people with context: "Attendee in [meeting title] on [date]."

### Person Lookup for Context

If a meeting involves someone {{USER_NAME}} is preparing to meet again, use `mcp__fathom__find_person` with `recorded_by: "anyone"` to pull up prior meeting history with that person. This enriches the people KB and helps with meeting prep.

---
phase: connector
name: fathom
slot: query
mode: [briefing]
requires: fathom
---

## Fathom Query — Briefing Data Gathering

### Recent Meeting Notes

Use `mcp__fathom__list_meetings` with `created_after` set to 24 hours ago, `include_summary: true`, and `include_action_items: true` to check for recent meetings.

For each meeting:

1. Review the AI summary via the listing (or call `mcp__fathom__get_meeting_summary` for more detail)
2. Pull the full transcript with `mcp__fathom__get_meeting_transcript` if the summary is insufficient for action item extraction
3. Extract action items and commitments (apply the same "signals, not facts" principle)
4. Note decisions that affect current projects
5. Identify any follow-up meetings that were discussed

Limit full transcript pulls to at most 3 meetings per run — transcripts are large. Prioritize meetings that appear most actionable based on their summaries.

### Topic Search for Active Projects

If there are active projects in the KB, use `mcp__fathom__search_meetings` with relevant project keywords and `recorded_by: "anyone"` to find recent discussions that may not have been captured yet. Limit to `created_after` of the last briefing run to avoid reprocessing.

### Cross-Reference with Calendar

Compare the list of meetings with transcripts against the calendar. Look for gaps:
- Meetings on the calendar that have no Fathom recording (may need manual notes, or the meeting was cancelled, or recording was not enabled)
- Fathom recordings for meetings not on the calendar (ad-hoc calls, informal meetings)

When both a transcript and calendar entry exist for the same meeting, use both for richer context: the calendar provides attendees and timing, the transcript provides content.

### Context for Today's Meetings

If today's calendar has meetings with attendees or topics that appeared in recent Fathom recordings, note the connection. Use `mcp__fathom__find_person` with `recorded_by: "anyone"` to pull meeting history with today's attendees. This helps {{USER_NAME}} prepare: "You're meeting with X again today — in yesterday's call, you committed to Y."
