---
name: scout-briefing
description: Launch a Scout morning briefing session as a background process. Auto-detects mode (morning briefing, consolidation, or weekend briefing) from the current time and day.
---

Launch a SCOUT morning briefing session as a background process on this machine.

Do the following steps:

1. Run `nohup bash ~/Scout/run-scout.sh > /dev/null 2>&1 &` to launch the briefing session in the background. Capture the PID.

2. Find the most recent log file: `ls -t ~/Scout/.scout-logs/scout-*.log | head -1`

3. Report to the user:
   - Confirm the briefing session is running and show the PID
   - Show the log file path so they can tail it if they want: `tail -f <log_file>`
   - Remind them that briefing runs typically take 10-15 minutes and cover: calendar scan, Gmail, Slack, Linear, GitHub PRs — producing a fresh action-items file and updating the knowledge base. The session mode (morning briefing vs consolidation vs weekend briefing) is auto-detected from the current time and day.
