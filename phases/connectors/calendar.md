---
phase: connector
name: calendar
slot: outbound-scan
mode: [consolidation]
requires: calendar
---

## Calendar Outbound Scan — What {{USER_NAME}} Changed

Check for calendar events recently cancelled or modified by {{USER_NAME}}. Calendar changes are strong signals of handled action items.

### Cancelled Events

Use `gcal_list_events` to look for events that were on the schedule but are now cancelled or removed. A cancelled meeting often means:
- The topic was resolved and the meeting is no longer needed
- {{USER_NAME}} handled the underlying issue
- A decision was made asynchronously

### Modified Events

Check for events that were recently rescheduled, had attendees added/removed, or had their description updated. Modifications indicate:
- Timeline changes for a project
- Scope changes (new attendees = expanding involvement)
- Prep work completed (description updated with agenda or materials)

### New Events Created

Check for events {{USER_NAME}} recently created. New events indicate:
- {{USER_NAME}} scheduled a follow-up (the trigger for the follow-up may be a completed action item)
- A new meeting series was started (new project or workstream)
- A one-off sync was set up to address something specific

For each change, note what it implies for action items — did cancelling this meeting mean something got done? Did creating a new event mean a commitment was made?

---
phase: connector
name: calendar
slot: inbound-scan
mode: [consolidation, briefing]
requires: calendar
---

## Calendar Inbound Scan — Meetings and Events

Pull today's and yesterday's calendar events to identify meetings that may have generated action items.

### Today's Meetings

Use `gcal_list_events` to get all events for today. For each meeting:
- Note the title, time, and attendees
- Check if meeting notes or transcripts are available from other connectors (Granola, Drive)
- Flag meetings that haven't happened yet — these provide context for the briefing ("{{USER_NAME}} has a meeting with X about Y at 2 PM")

### Yesterday's Meetings (Consolidation)

For consolidation runs, also pull yesterday's meetings. Meetings from yesterday are the most likely source of unprocessed action items:
- Check if transcripts or notes exist for these meetings
- Cross-reference attendees with `people.md`
- Any meeting that happened but has no notes/transcript anywhere should be flagged as a gap

### Recently Added/Changed Events

Check for events that were recently added or modified by others:
- New meeting invites = someone wants {{USER_NAME}}'s time (potential new action item or commitment)
- Changed meetings = priorities shifting
- Cancelled meetings (by others) = something changed that {{USER_NAME}} should know about

### Meeting-Table Compose-Time Cross-Check (mandatory)

The today's-meetings table is a recurring source of two bugs: wrong wall-clock times and phantom (carried-but-cancelled) rows. Three hard rules at compose time:

1. **Never derive wall-clock time from a recurring `eventId`/`recurringEventId` suffix** (e.g. `_R20260525T093000` is opaque UTC). The only authoritative time fields are `start.dateTime` + `start.timeZone` (or `originalStartTime.dateTime` for a series instance). Convert to the display timezone from those fields.
2. **Assert every row against a fresh same-run query.** Call `list_events` for today's window **in this run**, and assert each table row's displayed time matches the live event start to the minute. If the table is carried from a prior run without a fresh `list_events` this run, every time cell must be flagged `[carried]`.
3. **Phantom detection.** Every carried row must match a live event by `id`/`recurringEventId` in this run's `list_events` result. Rows with no live match must be removed or marked `[unverified — not on calendar this run]` with the query timestamp.

---
phase: connector
name: calendar
slot: query
mode: [briefing]
requires: calendar
---

## Calendar Query — Briefing Data Gathering

### Today's Schedule

Use `gcal_list_events` to get today's full calendar. For each event, note:
- Time and duration
- Title and description
- Attendees (cross-reference with `people.md`)
- Whether it's recurring or one-off
- Any preparation needed (check if there are linked documents, agendas, or open issues related to the meeting topic)

### Yesterday's Meetings

Pull yesterday's meetings to check for unprocessed action items. If meeting transcripts are available from other connectors, they'll be the primary source — but the calendar provides the meeting list to check against.

### Upcoming Context

Note any significant meetings in the next 2-3 days that {{USER_NAME}} should prepare for. Flag these in the briefing if preparation is needed (e.g., "You have a design review Thursday — the PR for feature X should be ready by then").

---
phase: connector
name: calendar
slot: cross-check
mode: [consolidation, briefing]
requires: calendar
---

## Calendar Cross-Check

Before promoting any candidate action item to To Do, verify against the calendar:

**Is this already scheduled?** Search for events related to the topic. If a meeting already exists to discuss or handle this item, it may not need a separate action item — or the action item should reference the meeting ("Prepare X before the meeting on [date]").

**Does a meeting already exist for this?** Check if an upcoming event addresses this topic. Common patterns:
- "Need to discuss X with Y" — check if a meeting with Y is already on the calendar
- "Should follow up on Z" — check if a follow-up meeting is already scheduled
- "Need to review W" — check if a review meeting exists

**Was an event recently cancelled?** A cancelled event related to this topic often means {{USER_NAME}} already handled it or the need went away. If a meeting titled "Discuss deployment issue" was cancelled, search for evidence that the deployment issue was resolved.

---
phase: connector
name: calendar
slot: update
mode: [consolidation, briefing]
requires: calendar
---

## Calendar-Sourced KB Updates

After scanning the calendar, update the knowledge base:

### Upcoming Meetings in Project Files

For each active project in the KB, check if there are upcoming meetings related to that project. Update the "Upcoming Meetings" section of the project file with:
- Meeting title, date/time
- Attendees
- Any known agenda items

Remove past meetings from "Upcoming Meetings" sections — these should have been converted to notes/decisions/action items. If a past meeting is still listed as "upcoming," that's a staleness signal.

### People Updates

If calendar events include attendees not yet in `people.md`, add them:
- Name (from the calendar invite)
- Email (from the calendar invite)
- Context: "Attendee in [meeting name] on [date]"
- Role `[single-source]` if determinable from the meeting context

### New Workstreams

If a new recurring meeting series appeared, this may indicate a new project or workstream. Check if a KB project file should be created (only if it meets the criteria: own meetings, own issues, own people).
