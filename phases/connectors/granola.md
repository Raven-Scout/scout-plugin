---
phase: connector
name: granola
slot: inbound-scan
mode: [consolidation, briefing]
requires: granola
---

## Granola Inbound Scan — Meeting Transcripts

Pull meeting transcripts for meetings since the last run and extract actionable information.

### Retrieve Recent Transcripts

Use `list_meetings` to find meetings since the last run, then use `get_meeting_transcript` for each meeting to pull the full transcript.

For each transcript, extract:

1. **Action items mentioned** — any commitment like "I'll do X," "{{USER_NAME}} will handle Y," or "we need to Z." Record exactly what was said, by whom, and in what context.
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

### Deliverable-Invention Gate

A dated commitment or deliverable pulled from a transcript needs a **verbatim source quote**, or it doesn't get written. Before attaching any urgency marker — "must be done by X", "before Y", "committed to Z", "due Friday" — to an extracted item, confirm the transcript (or a post-meeting message) contains the exact sentence stating that commitment. If the quote doesn't exist verbatim, ship the item **without** the urgency marker, or route it to `review-queue.md` for {{USER_NAME}} to confirm — never synthesize the deadline.

Two traps that produce invented deliverables:
- **Topic conflation.** When two adjacent topics surface in the same meeting (e.g. a documentation deliverable and an unrelated upcoming trip), do not fuse them into one dated item. Write them separately and let {{USER_NAME}} cull.
- **One-off ≠ standing.** A single mention is not a recurring cadence. "Every Friday" / "every Monday" commitments must come from the speaker's own words, not be inferred from one occurrence.

### Extraction Gate — Content, Not Breadcrumbs

A "✅ transcript captured" / "notes available for [meeting]" line is **not** extraction — it is a breadcrumb that silently drops the meeting's substance. For every meeting {{USER_NAME}} attended where a transcript exists, you MUST produce the substantive extraction above (action items, decisions, commitments-by-others, deadlines, open questions) or an explicit "reviewed — no actionable content (reason)" note. A bare capture/availability breadcrumb is forbidden as the only output for an attended meeting.

**Pre-commit self-check:** before committing, grep the run's notes/KB/action-items for transcript breadcrumb rows that carry no extracted substance:

```bash
grep -rniE 'transcript (captured|available)|notes (captured|available)' "$DAILY_FILE" {{SCOUT_DIR}}/knowledge-base 2>/dev/null \
  && echo "WARN: transcript breadcrumb(s) above — confirm each has substantive extraction, not just a capture note" >&2
```

If a flagged meeting has no decisions/commitments/follow-ups recorded near it, go back and extract them (or record the explicit no-content note) before commit.

### Attendee Extraction

Note all meeting attendees. Cross-reference with `people.md` and add any new people with context: "Attendee in [meeting title] on [date]."

---
phase: connector
name: granola
slot: query
mode: [briefing]
requires: granola
---

## Granola Query — Briefing Data Gathering

### Recent Meeting Notes

Use `list_meetings` to check for recent meetings (past 24 hours) with available transcripts. For each meeting:

1. Pull the transcript using `get_meeting_transcript`
2. Extract action items and commitments (apply the same "signals, not facts" principle)
3. Note decisions that affect current projects
4. Identify any follow-up meetings that were discussed

### Cross-Reference with Calendar

Compare the list of meetings with transcripts against the calendar. Look for gaps:
- Meetings on the calendar that have no transcript (may need manual notes, or the meeting was cancelled)
- Transcripts for meetings not on the calendar (ad-hoc calls, informal meetings)

When both a transcript and calendar entry exist for the same meeting, use both for richer context: the calendar provides attendees and timing, the transcript provides content.

### Context for Today's Meetings

If today's calendar has meetings with attendees or topics that appeared in recent transcripts, note the connection. This helps {{USER_NAME}} prepare: "You're meeting with X again today — in yesterday's call, you committed to Y."
