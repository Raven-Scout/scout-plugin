---
phase: connector
name: email
slot: outbound-scan
mode: [consolidation]
requires: email
---

## Email Outbound Scan — What {{USER_NAME}} Sent

Check sent mail since the last run. Outbound emails are strong evidence that action items have been completed or are in progress.

### Sent Mail Search

Use `gmail_search_messages` with `from:{{USER_EMAIL}}` and date filters to find emails sent since the last run. For each sent email:

- **Recipient(s)**: Who was it sent to? Cross-reference with `people.md`.
- **Subject/Topic**: What was it about?
- **Implications for action items**:
  - A reply to someone's request = that request is likely handled
  - A proactive email with a deliverable (attachment, link, proposal) = something was completed
  - A scheduling or coordination email = follow-up is in progress
  - A forwarded email = delegation or escalation

### Cold Outreach Filter

When scanning sent mail, ignore:
- Automated replies or out-of-office messages
- Subscription confirmations or transactional emails
- Marketing platform sends (newsletters, campaigns)

Focus on person-to-person emails that indicate real work activity.

---
phase: connector
name: email
slot: inbound-scan
mode: [consolidation, briefing]
requires: email
---

## Email Inbound Scan — What {{USER_NAME}} Received

Check the inbox for important emails that may require action.

### Inbox Search

Use `gmail_search_messages` to find recent emails in the inbox. Prioritize:

1. **Emails from known contacts** — people in `people.md` or frequent correspondents
2. **Emails with action-oriented subjects** — containing words like "review," "approve," "update," "question," "help," "urgent," "deadline"
3. **Replies to threads {{USER_NAME}} started** — these may contain answers or follow-ups to {{USER_NAME}}'s outbound emails
4. **Calendar-related emails** — meeting invites, RSVPs, agenda shares (cross-reference with Calendar connector)

### Cold Outreach Filter

**Do NOT surface cold outreach emails, vendor marketing, or unsolicited sales emails as action items.** Apply these heuristics:

- Unknown sender + product pitch + no prior relationship = **skip entirely**
- Unknown sender + generic "partnership" or "opportunity" language = **skip**
- Vendor follow-up where {{USER_NAME}} never responded to the initial email = **skip**
- Mass email (BCC'd, mailing list) from unknown source = **skip**
- If uncertain whether an email is legitimate or cold outreach, file under **Watching** at most, **never under To Do**

### Automated-Alert Triage

Automated/transactional alerts — credential & token expiry, quota/spend thresholds, usage metrics, billing milestones, scheduled-maintenance notices — are **informational by default**: file them under **Watching** (🟢), never To Do or Urgent on tone alone. Promote one only when there is an *imminent functional impact*: a production credential expiring within days that breaks an active workflow, or a hard shutoff with a near deadline. The alert's own language ("action required", "expiring soon", "limit reached") is not urgency — evaluate the actual deadline and whether anything {{USER_NAME}} relies on would actually break. Test/dev/non-production credentials and routine threshold notices stay 🟢.

### What to Record

For each legitimate inbound email:
- **From** whom (name and relationship if known)
- **Subject/Topic**
- **What's being asked or communicated**
- **Whether {{USER_NAME}} already replied** (check sent mail for a response in the same thread)
- **Urgency** — explicit deadline, tone, or sender importance

### Drill Active Threads — Don't Trust the Snippet

`gmail_search_messages` returns a preview snippet of just *one* message in a thread — often not the latest. On any thread that has been replied to since the last run, the snippet you get is *not* the latest message; the reply you're looking for may already be sitting in the thread tail. **Never assert a thread's state — "still no reply", "awaiting X", "unanswered" — from the search snippet alone.** Inbound replies that land *between* {{USER_NAME}}'s own outbound messages are the classic miss: a `from:me`-only or snippet-only scan is structurally blind to them.

For every thread returned by search, you MUST fetch the **full thread** (`get_thread`) and read the full message list when ANY of these is true:

1. **The thread moved since the last run** — its latest message timestamp is newer than the prior run's email watermark (keep a last-checked watermark, e.g. `.scout-cache/last-email-checked.txt`; update it only *after* drilling, never on search alone).
2. **The thread ties to an open action item.** Build a per-run allowlist by grepping today's action-items file for `[[wikilinks]]`, issue identifiers (e.g. `PROJ-1234`), and email addresses, then match threads against it.
3. **A participant matches a tracked entity** — any email address listed on an open personal-task entity (`knowledge-base/personal/task-*.md`) or an active `knowledge-base/people/*.md` file. Slow-moving negotiations with tracked contacts are exactly where a stale snippet misleads.
4. **The action-items file already cites this thread with a state framing** (e.g., "awaiting their reply"). Mandatory re-read at compose time — the same rule the issue-tracker connector enforces for fast-moving issues.

After drilling, log coverage as `thread <id> drilled, last-message <timestamp>, n=<message-count>` so a post-run audit can confirm the thread was opened, not just searched. Convert message timestamps to the display timezone at write time (see the timezone rules in the git-setup phase).

**Snippet-vs-state guardrail (compose-time):** before writing or carrying any action item that cites an email thread, the most recent message you've actually *read* on that thread must be ≤ 1 run-window old (the consolidation lookback, or 24h for a briefing). If it's older, drill again. If the drilled state contradicts the carried framing (thread says "approved" while the carried item says "awaiting approval"), update the action item and log the divergence in the mistake audit.

---
phase: connector
name: email
slot: query
mode: [briefing]
requires: email
---

## Email Query — Briefing Data Gathering

### Inbox Check

Use `gmail_search_messages` to pull recent inbox messages (past 24 hours). Apply the same cold outreach filter as the inbound scan — do not surface unsolicited sales or vendor marketing.

Focus on:
1. Unread emails from known contacts
2. Emails requiring a response (questions, requests, approvals)
3. Emails with deadlines or time-sensitive content
4. Thread updates where {{USER_NAME}} is an active participant

### Sent Mail Check

Also search sent mail from the past 24 hours using `from:{{USER_EMAIL}}`. This reveals:
- What {{USER_NAME}} already handled via email
- Active threads where {{USER_NAME}} is waiting for a reply
- Deliverables sent (which may correspond to completed action items)

### Synthesis

For each email finding:
- Is this a new request needing action? (candidate action item)
- Is this an update on something tracked in the KB? (update the relevant project file)
- Is this something {{USER_NAME}} already handled? (evidence for marking items Done)
- Is this FYI only? (note context but no action item)
