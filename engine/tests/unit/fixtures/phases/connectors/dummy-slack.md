---
phase: connector
name: dummy-slack
slot: query
mode: [briefing]
requires: slack
---

## Slack Query

Slack ID: {{USER_SLACK_ID}}.

---
phase: connector
name: dummy-slack
slot: outbound-scan
mode: [consolidation]
requires: slack
---

## Slack Outbound

Outbound for {{USER_NAME}}.
