---
phase: research
name: Commit and Notify
slot: commit-notify
mode: [research]
requires: null
---

# PHASE 4: COMMIT & NOTIFY

## Step 4a: Commit

```bash
git -C "{{SCOUT_DIR}}" add -A && git -C "{{SCOUT_DIR}}" commit -m "research [HH:MM]: <summary of targets researched and key findings>"
```

Use `TZ={{TIMEZONE}} date '+%H:%M'` for the timestamp (default: `America/New_York`).

## Step 4b: Session Entry

Add to `knowledge-base.md` Recent Sessions table:
```markdown
| [Date] | Research (~[time]) | [Brief: targets researched, key findings, entities updated] |
```

## Step 4c: Session Cost

```bash
{{SCOUT_DIR}}/scripts/write-session-cost.sh research "${CLAUDE_MAX_BUDGET_USD:-0}" "${CLAUDE_BUDGET_SPENT_USD:-0}" 0
```

---

phase: research
name: Slack Research Notification
slot: notification
mode: [research]
requires: slack
---

## Step 4d: Slack Notification

Send a brief DM to {{USER_NAME}} (Slack ID: `{{USER_SLACK_ID}}`) summarizing:
1. What was researched
2. Key new information discovered
3. Entities updated
4. Actionable insights (if any)

Keep it concise — 5-8 lines max. The value is in the KB updates, not the summary.
