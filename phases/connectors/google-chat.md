---
phase: connector
name: google-chat
slot: outbound-scan
mode: [consolidation]
requires: google_chat
---

## Google Chat Outbound Scan — What {{USER_NAME}} Did

Search for messages FROM {{USER_NAME}} since the last run using the `gws` CLI (Google Workspace CLI). Outbound activity in Google Chat is the strongest signal for what {{USER_NAME}} has already handled.

### List Spaces {{USER_NAME}} Is In

```bash
gws chat spaces list --format json --page-all 2>/dev/null
```

This returns all spaces (DMs, group chats, named rooms) {{USER_NAME}} is a member of. Cache the space list for the rest of the run — it changes rarely. Note each space's `name` (e.g. `spaces/AAAA...`), `displayName`, and `spaceType` (`DIRECT_MESSAGE`, `GROUP_CHAT`, or `SPACE`).

### DMs Sent

For each `DIRECT_MESSAGE` space in the list, fetch recent messages and keep only those where `sender.name == "users/{{USER_GOOGLE_CHAT_ID}}"`:

```bash
gws chat spaces messages list \
  --params '{"parent":"spaces/AAAA...","filter":"create_time > \"<ISO-8601 since-last-run>\"","orderBy":"create_time DESC","pageSize":50}' \
  --format json 2>/dev/null
```

Each outbound DM is a strong signal:
- A reply to someone = that request/question is likely handled
- A proactive message = {{USER_NAME}} initiated something (delegation, follow-up)
- A message containing a link or attachment ID = possible deliverable completed

### Space Posts

For each project space listed in `channels.md` (or every `SPACE` / `GROUP_CHAT` in the cached list, scoped to high-priority ones), list messages since the last run and filter by `sender.name`:

```bash
gws chat spaces messages list \
  --params '{"parent":"spaces/BBBB...","filter":"create_time > \"<ISO-8601>\"","pageSize":50}' \
  --format json 2>/dev/null
```

Space posts indicate:
- Status updates given (the underlying work is done or in progress)
- Questions asked ({{USER_NAME}} is blocked or exploring)
- Answers provided ({{USER_NAME}} helped someone — may indicate context/expertise)

### Thread Replies

Google Chat threads are first-class. Each message has a `thread.name` (e.g. `spaces/AAA/threads/BBB`). After listing space messages, group by `thread.name` to find threads {{USER_NAME}} replied in. Thread replies are easy to miss but often indicate handled items — someone asked a question in a thread, {{USER_NAME}} replied, and the item is resolved.

To pull a full thread once a candidate thread is identified:

```bash
gws chat spaces messages list \
  --params '{"parent":"spaces/AAA","filter":"thread.name = spaces/AAA/threads/BBB"}' \
  --format json 2>/dev/null
```

### What to Record

For each outbound message found, note:
- **Who** it was sent to (DM peer, space `displayName`, or thread)
- **Topic** (brief summary of what was discussed)
- **Implications** for action items:
  - Did this complete something? (mark it Done)
  - Did this delegate something? (track the delegation)
  - Did this respond to a request? (the request is handled)
  - Did this create a new commitment? (new action item for {{USER_NAME}})

---
phase: connector
name: google-chat
slot: inbound-scan
mode: [consolidation, briefing]
requires: google_chat
---

## Google Chat Inbound Scan — What Happened to {{USER_NAME}}

Search for messages TO or MENTIONING {{USER_NAME}} since the last run. These are potential new action items or context updates.

### Direct Mentions

Google Chat's API has no global mention search. Iterate the space list (cached from the outbound scan if available) and, for each `SPACE` / `GROUP_CHAT`, list messages since the last run, then keep messages whose text contains `<users/{{USER_GOOGLE_CHAT_ID}}>` or whose `annotations[].userMention.user.name == "users/{{USER_GOOGLE_CHAT_ID}}"`:

```bash
gws chat spaces messages list \
  --params '{"parent":"spaces/BBBB...","filter":"create_time > \"<ISO-8601>\"","pageSize":50}' \
  --format json 2>/dev/null
```

Direct mentions are high-signal — someone specifically wanted {{USER_NAME}}'s attention.

### DMs Received

For each `DIRECT_MESSAGE` space, list messages since the last run and keep messages where `sender.name != "users/{{USER_GOOGLE_CHAT_ID}}"`. Inbound DMs often contain:
- Requests for help or input
- Questions needing answers
- Updates on shared work
- FYIs that may affect priorities

### Key Space Activity

Check spaces listed in `channels.md` for recent activity, even if {{USER_NAME}} wasn't mentioned. Important space activity includes:
- Decisions made that affect {{USER_NAME}}'s work
- New issues or blockers raised
- Status updates from collaborators
- Announcements that change priorities

### Priority Spaces (always sweep)

If `scout-config.yaml` defines a `google_chat_priority_spaces` list, sweep every entry on every run regardless of dynamic discovery. These are pinned strategic spaces (e.g. CTO direct-reports broadcast, org-wide engineering channels, weekly tech meetings) where missing a message has high cost. For each entry:

```bash
gws chat spaces messages list \
  --params '{"parent":"spaces/<id>","filter":"create_time > \"'$SINCE'\""}' \
  --format json
```

Record matched items with the same fields as Key Space Activity even when {{USER_NAME}} wasn't tagged.

### What to Record

For each inbound message found, note:
- **From** whom (resolve `sender.name` -> display name via cached `people.md` or `gws chat users get --params '{"name":"users/<id>"}'`)
- **Space/thread** where it appeared
- **What's being asked or communicated**
- **Urgency level** — is this time-sensitive?
- **Whether {{USER_NAME}} already responded** (cross-reference with outbound scan)

Remember: every inbound item is a *candidate* action item, not a confirmed one. It must pass the cross-check before becoming a To Do.

---
phase: connector
name: google-chat
slot: query
mode: [briefing]
requires: google_chat
---

## Google Chat Query — Briefing Data Gathering

Gather Google Chat context for the briefing. Check the past 24 hours of activity. Compute the lower bound once and reuse it across all `gws` calls:

```bash
SINCE=$(date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)
```

### Inbound — What Needs Attention

1. **Space inventory**: `gws chat spaces list --format json --page-all` (cache the result for the rest of the run).
2. **DMs to {{USER_NAME}}**: For each `DIRECT_MESSAGE` space, `gws chat spaces messages list --params '{"parent":"<space>","filter":"create_time > \"'$SINCE'\""}' --format json`. Keep messages whose `sender.name != "users/{{USER_GOOGLE_CHAT_ID}}"`. Prioritize peers in `people.md`.
3. **Mentions**: For each `SPACE` / `GROUP_CHAT`, list messages since `$SINCE` and keep messages where `annotations[].userMention.user.name == "users/{{USER_GOOGLE_CHAT_ID}}"` or whose `text` contains `<users/{{USER_GOOGLE_CHAT_ID}}>`.
4. **Key spaces**: Read recent messages in spaces listed in `channels.md` that are marked as high-priority. Look for anything actionable even if {{USER_NAME}} wasn't tagged.
4a. **Priority spaces (always sweep)**: If `scout-config.yaml` defines `google_chat_priority_spaces`, sweep every entry on every run regardless of dynamic discovery — `gws chat spaces messages list --params '{"parent":"spaces/<id>","filter":"create_time > \"'$SINCE'\""}' --format json` for each. These are pinned strategic spaces (e.g. CTO direct-reports broadcast, org-wide engineering channels, weekly tech meetings) where missing a message has high cost. Surface key updates even when {{USER_NAME}} wasn't tagged.

### Outbound — What's Already Handled

5. **Messages FROM {{USER_NAME}}**: For each space (DMs first, then high-priority spaces), filter the same listing by `sender.name == "users/{{USER_GOOGLE_CHAT_ID}}"`. This reveals what's already been dealt with — critical for avoiding stale action items.
6. **Thread participation**: Group {{USER_NAME}}'s outbound messages by `thread.name`. If {{USER_NAME}} replied in a thread about a topic, that topic is likely in-progress or handled.

### Synthesis

For each finding, note whether it's:
- A new request needing action
- An update on an existing project/issue (link to KB file)
- Something {{USER_NAME}} already handled (evidence from outbound search)
- FYI/context only (no action needed)

---
phase: connector
name: google-chat
slot: cross-check
mode: [consolidation, briefing]
requires: google_chat
---

## Google Chat Cross-Check

Before promoting any candidate action item to To Do, verify against Google Chat:

**Did {{USER_NAME}} already handle this?** Search outbound messages — DMs, space posts, and thread replies — about this topic. Google Chat has no full-text search across spaces, so:

1. Identify candidate spaces (DM with the requester, the space where the topic was raised, or any space tagged with the relevant project in `channels.md`).
2. For each candidate space, list messages with a `create_time` filter wide enough to cover the topic's lifetime, then locally grep `text` for topic-specific keywords AND filter by `sender.name == "users/{{USER_GOOGLE_CHAT_ID}}"`.

```bash
gws chat spaces messages list \
  --params '{"parent":"spaces/AAA","filter":"create_time > \"<ISO-8601>\"","pageSize":200}' \
  --format json 2>/dev/null \
  | jq '.messages[] | select(.sender.name == "users/{{USER_GOOGLE_CHAT_ID}}") | select(.text | test("<keyword>"; "i"))'
```

- If {{USER_NAME}} sent a message about the topic, the item is likely **handled or in progress**. Read the message content to determine if it's fully resolved or still pending.
- If {{USER_NAME}} replied in a thread discussing this topic, pull the full thread (`filter=thread.name = spaces/AAA/threads/BBB`) to understand the current state.
- If {{USER_NAME}} posted a status update or shared a deliverable related to this item, mark it Done and cite the message resource name (`spaces/AAA/messages/BBB`) as evidence.

**Was this already discussed and resolved?** Sometimes a topic was raised, discussed, and resolved — all in a thread {{USER_NAME}} may not have participated in. List recent messages in the relevant project space, group by `thread.name`, and read full threads where the topic appears.

---
phase: connector
name: google-chat
slot: update
mode: [consolidation, briefing]
requires: google_chat
---

## Google Chat-Sourced KB Updates

After scanning Google Chat, update the knowledge base with any new information discovered:

### People Updates

- If new people appeared in threads, DMs, or space conversations who are not in `people.md`, add them with:
  - Name (resolve via `gws chat users get --params '{"name":"users/<id>"}'` if not already known)
  - Context (how they appeared — "mentioned in <space displayName> discussing project-x")
  - Google Chat user resource name (`users/<id>`) and email if visible
  - Role if determinable from context `[single-source]`
- If existing people showed new context (e.g., someone listed as "Engineering" is now clearly leading a specific project), update their entry with the new information and source citation.

### Space (Channel) Updates

- If new spaces were discovered that are relevant to {{USER_NAME}}'s work, add them to `channels.md` with:
  - Space `displayName` and resource name (`spaces/AAAA...`)
  - `spaceType` (`SPACE`, `GROUP_CHAT`, `DIRECT_MESSAGE`)
  - Purpose/context (what the space is used for based on observed messages)
  - Which project(s) it relates to
- If existing spaces have changed in relevance (e.g., a project space went quiet or a new one became active), note the change.

### Project Updates

- If Google Chat conversations revealed new decisions, status changes, or context for active projects, update the relevant project files in `knowledge-base/projects/`.
- Always cite the Google Chat source: "Per discussion in <space displayName> on [date]" or "Per DM from [person] on [date]." Include the message resource name (`spaces/AAA/messages/BBB`) so the source can be re-fetched with `gws chat spaces messages get`.

---
phase: connector
name: google-chat
slot: notification
mode: [consolidation, briefing]
requires: google_chat
---

## Google Chat Notification

Send a Google Chat DM to {{USER_NAME}} (Google Chat user ID: `{{USER_GOOGLE_CHAT_ID}}`) summarizing the run results. Resolve the DM space once per run, then post into it.

### Resolve the Self-DM Space

```bash
DM_SPACE=$(gws chat spaces findDirectMessage \
  --params '{"name":"users/{{USER_GOOGLE_CHAT_ID}}"}' \
  --format json 2>/dev/null \
  | jq -r '.name')
```

If `findDirectMessage` returns 404 (no DM exists yet), fall back to `users/{{USER_EMAIL}}` as the user alias. Cache `DM_SPACE` in `.scout-cache/google-chat-self-dm` so subsequent runs skip the lookup.

### Send the Notification

Use the `+send` helper for plain-text notifications:

```bash
gws chat +send --space "$DM_SPACE" --text "$(cat <<'EOF'
Scout consolidation complete.
- Action items: X new, Y completed, Z carried forward
- KB audited: <list of files checked/updated>
- Urgent: <any urgent items, or "none">
EOF
)"
```

For threaded replies or rich cards, call the raw API instead:

```bash
gws chat spaces messages create \
  --params '{"parent":"'"$DM_SPACE"'"}' \
  --json '{"text":"<message body>"}' \
  --format json 2>/dev/null
```

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

### Notification Rules

- Never include sensitive details in the notification — just summaries and counts.
- If there are urgent items, mention them by name (briefly) so {{USER_NAME}} knows to check.
- Always include where to find the full details: "Full report in {{SCOUT_DIR}}/action-items/"
- If the run encountered errors or couldn't access a connector, mention it briefly so {{USER_NAME}} knows the run was partial.
- Google Chat plain-text messages cap at 32,000 bytes. Scout notifications are short by design — splitting is not required, but if a body exceeds the cap, truncate with a "(truncated — see {{SCOUT_DIR}}/...)" suffix rather than fanning out multiple messages.
