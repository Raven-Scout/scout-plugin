---
phase: core
name: meeting-prep
slot: meeting-prep-generation
mode: [briefing]
requires: null
---

## Meeting Prep Generation

After querying all connectors and before writing action items, generate prep files for today's meetings.

### Step 1: Match calendar events to meeting folders

Read `meetings/meetings.md` for the list of tracked meetings. For each meeting on today's calendar:
1. Normalize the event title (lowercase, strip whitespace)
2. Match against `calendar_match` patterns in each meeting's home file frontmatter
3. If matched and no dated file (`meetings/<slug>/YYYY-MM-DD.md`) exists yet, create it

### Step 2: Generate prep content

For each matched meeting, create the dated session file:

```
# [Meeting Name] — YYYY-MM-DD

**Time:** [time from calendar]
**Attendees:** [from calendar event + home file]

## Prep

### Since Last Session ([date])
[Query project files, Linear, Slack, GitHub for changes since last meeting. Read the most recent dated session file.]

### Open Action Items from Last Session
[Carry forward uncompleted items from last session's Synthesis]

### Next Session Notes (from inbox)
[Pull notes from the home file's "## Next Session Notes" section. Clear that section after pulling.]

### Suggested Talking Points
[Generate 3-5 talking points based on all the above context]

***

## Notes

<!-- Write your notes here during or after the meeting -->

***

## Synthesis

<!-- {{INSTANCE_NAME}} fills this after the meeting during consolidation -->
```

### Step 3: Handle untracked meetings

- Recurring calendar events with no matching folder → create folder + home file with `[new - needs review]` flag
- One-off meetings with 2+ attendees + project link → create a meeting folder

### Step 4: Update action items format

Include a meetings table in the action items file:

```
## 📅 Today's Meetings

| Time | Meeting | Prep | Status |
|------|---------|------|--------|
| HH:MM | Meeting Name | [[meeting-slug/YYYY-MM-DD|prep]] | upcoming |
```

Any preparation-specific action items must include `Context: [[meeting-slug/YYYY-MM-DD]]`.

---
phase: core
name: meeting-synthesis
slot: post-meeting-processing
mode: [consolidation]
requires: null
---

## Post-Meeting Processing

Check the calendar for meetings that ended since the last run. For each completed meeting with a folder:

### Step 1: Find notes and transcripts

a. Read the dated session file. Check if {{USER_NAME}} wrote anything in `## Notes`.
b. Search Google Drive for transcripts matching the meeting title and date.
c. If both exist, use both — manual notes for {{USER_NAME}}'s perspective, transcript for coverage.

### Step 2: Synthesize

Fill the `## Synthesis` section:

```
## Synthesis

**Completed by {{INSTANCE_NAME}}:** YYYY-MM-DD HH:MM timezone
**Sources:** [Manual notes / Google Drive transcript / Both / Neither]

### Key Discussion Points
- [Synthesized from notes + transcript]

### Decisions Made
| Decision | Owner | Also updated in |
|----------|-------|-----------------|
| [decision] | [who] | [[project-file]] |

### Action Items
| Item | Owner | Due | Linked |
|------|-------|-----|--------|
| [item] | [who] | [date] | [[project-file]] |

### Running Themes Updated
- [theme]: [what changed]
```

### Step 3: Propagate

a. **Decisions** → meeting home file Key Decisions + project files. Include `[[meeting-slug/YYYY-MM-DD]]` as source.
b. **Action items** → daily action-items file with `Source: [[meeting-slug/YYYY-MM-DD]]`. Apply standard cross-check.
c. **Running Themes** → update home file.
d. **Recent Sessions** → add row to home file.
e. **Today's Meetings table** → update status from `upcoming` to `done`.

### Step 4: Handle missing notes

If no manual notes AND no transcript, add `[no notes captured]` to home file's Recent Sessions. Do not fabricate.
