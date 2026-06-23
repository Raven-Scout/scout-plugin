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

`gmail_search_messages` returns a preview snippet of just *one* message in a thread — often not the latest. For any thread that has moved since the last run (newer than your last-checked watermark, or tied to an open action item or a tracked contact), fetch the **full thread** (`get_thread`) and read the tail before drawing any conclusion. **Never assert a thread's state — "still no reply", "awaiting X", "unanswered" — from the search snippet alone:** the reply you're looking for may already be sitting in the thread tail. Threads attached to an open 🔴/🟡 item are mandatory drills.

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
