---
phase: connector
name: slack
slot: outbound-scan
mode: [consolidation]
requires: slack
---

## Slack Outbound Scan — What {{USER_NAME}} Did

Search for messages FROM {{USER_NAME}} since the last run. This is the most important signal for understanding what {{USER_NAME}} has already handled.

### DMs Sent

Search for DMs sent by {{USER_NAME}} to frequent contacts. Each outbound DM is a strong signal:
- A reply to someone = that request/question is likely handled
- A proactive message = {{USER_NAME}} initiated something (delegation, follow-up, etc.)
- A message with an attachment or link = possible deliverable completed

Use `slack_search_public_and_private` with `from:{{USER_SLACK_ID}}` and date filters to find messages since the last run.

### Channel Posts

Search project channels for posts by {{USER_NAME}}. Check channels listed in the KB's `channels.md` file. Channel posts indicate:
- Status updates given (the underlying work is done or in progress)
- Questions asked ({{USER_NAME}} is blocked or exploring)
- Answers provided ({{USER_NAME}} helped someone — may indicate context/expertise)

### Thread Replies

Check for thread replies by {{USER_NAME}}. Thread replies are easy to miss but often indicate handled items — someone asked a question in a thread, {{USER_NAME}} replied, and the item is resolved.

### What to Record

For each outbound message found, note:
- **Who** it was sent to (person or channel)
- **Topic** (brief summary of what was discussed)
- **Implications** for action items:
  - Did this complete something? (mark it Done)
  - Did this delegate something? (track the delegation)
  - Did this respond to a request? (the request is handled)
  - Did this create a new commitment? (new action item for {{USER_NAME}})

---
phase: connector
name: slack
slot: inbound-scan
mode: [consolidation, briefing]
requires: slack
---

## Slack Inbound Scan — What Happened to {{USER_NAME}}

Search for messages TO or MENTIONING {{USER_NAME}} since the last run. These are potential new action items or context updates.

### Direct Mentions

Search for `<@{{USER_SLACK_ID}}>` mentions across all accessible channels. Direct mentions are high-signal — someone specifically wanted {{USER_NAME}}'s attention. Use `slack_search_public_and_private` with the user's Slack ID mention pattern.

### DMs Received

Check DMs received by {{USER_NAME}} from frequent contacts. Inbound DMs often contain:
- Requests for help or input
- Questions needing answers
- Updates on shared work
- FYIs that may affect priorities

### Replies on {{INSTANCE_NAME}}'s Own DM Threads (mandatory)

{{USER_NAME}} often replies *inside the thread* of a {{INSTANCE_NAME}} notification DM rather than starting a new message — those replies are invisible to a flat DM/mention search and silently drop task-asks. For **every** message {{INSTANCE_NAME}} (the bot) sent into the `{{USER_SLACK_ID}}` DM within the lookback window, call `slack_read_thread` on that message's timestamp and read all replies. This mirrors the dreaming Step 1a feedback harvest, but here the goal is **action items**: any reply that asks {{INSTANCE_NAME}} to do something, corrects a fact, or assigns a task becomes a candidate action item (a direct task-ask is 🔴 for today).

**Coverage assertion:** do not claim "0 inbound" / "quiet window" until you have confirmed every in-window bot DM had its thread read. State the count checked (e.g., "read threads on 4 bot DMs since last run") so the coverage is auditable.

### Key Channel Activity

Check project channels listed in `channels.md` for recent activity, even if {{USER_NAME}} wasn't mentioned. Important channel activity includes:
- Decisions made that affect {{USER_NAME}}'s work
- New issues or blockers raised
- Status updates from collaborators
- Announcements that change priorities

### 🔴 Project Channel Poll (mandatory)

`slack_search` misses threads and non-mention activity — the recurring cause of "Scout went quiet on project X." For every 🔴-priority project file (`knowledge-base/projects/*/<name>.md` with `priority: "🔴"` or a 🔴 status), read its `slack_channels:` YAML frontmatter and **directly read each declared channel** since the last run via `slack_read_channel(channel_id=<id>, limit=20)` — do not rely on search alone. Surface: every {{USER_NAME}} @-mention, every message from a person in the project's `worked_on_by`/key-people set, and every decision/blocker. List the polled channels in the sources footer (`🔴 channels polled: #a, #b`). If a 🔴 project has no `slack_channels:` frontmatter yet, add it (see project-file conventions in kb-management).

### Both Scans Required (gate)

The outbound (`from:`) and inbound (`to:`/mention) scans are BOTH mandatory every run — a scan reported with only `from:{{USER_NAME}}` is incomplete and the run is not done until the inbound `to:`/mention scan has also executed. Never declare Slack "quiet" off a one-directional scan.

### {{USER_NAME}}-Committed-Reply Tracking

Any thread reply by {{USER_NAME}} that accepts ownership — "I'll take care of it", "Got it", "On it", "Sure" — with no later outbound message resolving it MUST be auto-carried as a 🔴 action item until either the resolving action is observed or {{USER_NAME}} explicitly drops it. This catches the failure where {{USER_NAME}} accepts a task in-thread but the underlying ask never makes it into the carry-forward set.

### What to Record

For each inbound message found, note:
- **From** whom
- **Channel/context** where it appeared
- **What's being asked or communicated**
- **Urgency level** — is this time-sensitive?
- **Whether {{USER_NAME}} already responded** (cross-reference with outbound scan)

Remember: every inbound item is a *candidate* action item, not a confirmed one. It must pass the cross-check before becoming a To Do.

---
phase: connector
name: slack
slot: query
mode: [briefing]
requires: slack
---

## Slack Query — Briefing Data Gathering

Gather Slack context for the briefing. Check the past 24 hours of activity.

### Inbound — What Needs Attention

1. **DMs to {{USER_NAME}}**: Search for recent DMs received. Prioritize messages from frequent contacts and anyone in `people.md`.
2. **Mentions**: Search for `<@{{USER_SLACK_ID}}>` mentions across channels in the past 24 hours.
3. **Key channels**: Read recent messages in channels listed in `channels.md` that are marked as high-priority. Look for anything actionable even if {{USER_NAME}} wasn't tagged.

### Outbound — What's Already Handled

4. **Messages FROM {{USER_NAME}}**: Search for messages sent by {{USER_NAME}} in the past 24 hours. This reveals what's already been dealt with — critical for avoiding stale action items.
5. **Thread participation**: Check threads where {{USER_NAME}} has replied. If {{USER_NAME}} replied in a thread about a topic, that topic is likely in-progress or handled.

### Synthesis

For each finding, note whether it's:
- A new request needing action
- An update on an existing project/issue (link to KB file)
- Something {{USER_NAME}} already handled (evidence from outbound search)
- FYI/context only (no action needed)

---
phase: connector
name: slack
slot: cross-check
mode: [consolidation, briefing]
requires: slack
---

## Slack Cross-Check

Before promoting any candidate action item to To Do, verify against Slack:

**Did {{USER_NAME}} already handle this?** Search for {{USER_NAME}}'s outbound messages — DMs, channel posts, and thread replies — about this topic. Use topic-specific keywords in the search, not just broad date filters.

- If {{USER_NAME}} sent a message about the topic, the item is likely **handled or in progress**. Check the message content to determine if it's fully resolved or still pending.
- If {{USER_NAME}} replied in a thread discussing this topic, read the full thread to understand the current state.
- If {{USER_NAME}} posted a status update or shared a deliverable related to this item, mark it Done with a link to the message as evidence.

**Was this already discussed and resolved?** Search for the topic in relevant channels. Sometimes a topic was raised, discussed, and resolved — all in a thread that {{USER_NAME}} may not have been tagged in directly.

---
phase: connector
name: slack
slot: update
mode: [consolidation, briefing]
requires: slack
---

## Slack-Sourced KB Updates

After scanning Slack, update the knowledge base with any new information discovered:

### People Updates

- If new people appeared in threads, DMs, or channel conversations who are not in `people.md`, add them with:
  - Name
  - Context (how they appeared — "mentioned in #channel-name discussing project-x")
  - Slack handle if visible
  - Role if determinable from context `[single-source]`
- If existing people showed new context (e.g., someone who was listed as "Engineering" is now clearly leading a specific project), update their entry with the new information and source citation.

### Channel Updates

- If new channels were discovered that are relevant to {{USER_NAME}}'s work, add them to `channels.md` with:
  - Channel name and ID
  - Purpose/context (what the channel is used for based on observed messages)
  - Which project(s) it relates to
- If existing channels have changed in relevance (e.g., a project channel went quiet or a new one became active), note the change.

### Project Updates

- If Slack conversations revealed new decisions, status changes, or context for active projects, update the relevant project files in `knowledge-base/projects/`.
- Always cite the Slack source: "Per discussion in #channel-name on [date]" or "Per DM from [person] on [date]."

---
phase: connector
name: slack
slot: notification
mode: [consolidation, briefing]
requires: slack
---

## Slack Notification

Send a Slack DM to {{USER_NAME}} (Slack ID: `{{USER_SLACK_ID}}`) summarizing the run results.

### Consolidation Notification (3-5 lines)

Keep it tight. Example format:

```
Scout consolidation complete.
- Action items: X new, Y completed, Z carried forward
- KB audited: [list of files checked/updated]
- Urgent: [any urgent items, or "none"]
```

### Briefing Notification (5-8 lines)

Slightly more detail. Example format:

```
Scout morning briefing ready.
- Today's meetings: [count] ([first meeting time])
- Action items: X urgent, Y to-do, Z watching
- New since yesterday: [brief summary of new items]
- KB areas updated: [list]
- Review queue: [count] items pending your review
```

### Sources-Checked Footer

End every consolidation/briefing notification with a one-line transparency footer stating what was actually scanned, so coverage is auditable and "quiet" claims are backed by evidence:

```
Sources checked: N CC sessions · N vault/git commits · N Slack threads · N Linear updates · N Drive files
```

Only count sources you genuinely queried this run. A "quiet window" claim must be paired with non-zero scan counts — otherwise it reads as "didn't look," not "nothing happened."

### DM Legibility (no bare shortcodes; link the list)

Applies to every wrap DM. Two parts:

1. **No bare internal `#SHORTCODE` tags in the user-facing DM.** Action-items use `#SHORTCODE` tags as an *internal* continuity-keying convention (they match an item across daily files); they mean nothing to the reader as bare tokens. Either **expand inline** — `*semantic-layer weekly update* (#TAG)` or just the plain-English label — or **drop the tag from the DM**. A DM line whose only handle on an item is a bare `#CODE` is forbidden: the reader must understand it from plain English. The file-side `#SHORTCODE` machinery is untouched — this is presentation-layer only.
2. **Link the action-items file.** Any wrap DM that references "today's list" / action items must include a clickable link to today's file — an Obsidian URI `obsidian://open?vault={{INSTANCE_NAME}}&file=action-items%2Faction-items-<YYYY-MM-DD>` and/or a `file://{{SCOUT_DIR}}/action-items/action-items-<YYYY-MM-DD>.md` — so a tap opens the list.

### Notification Rules

- Never include sensitive details in the notification — just summaries and counts.
- If there are urgent items, mention them by name (briefly) so {{USER_NAME}} knows to check.
- Always include where to find the full details: "Full report in {{SCOUT_DIR}}/action-items/"
- If the run encountered errors or couldn't access a connector, mention it briefly so {{USER_NAME}} knows the run was partial.
