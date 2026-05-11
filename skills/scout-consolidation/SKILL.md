---
name: scout-consolidation
description: Launch a Scout consolidation session as a background process. Runs a lighter delta scan — captures what changed since the last run and updates action items and KB.
---

Launch a SCOUT consolidation session as a background process on this machine.

This runs the same script as the briefing but mode is auto-detected from the current hour. If run outside the normal schedule times (8 AM, 11 AM, 1 PM, 5 PM), it defaults to consolidation mode if today's action items already exist, or morning briefing if they don't.

Do the following steps:

1. Run `nohup bash ~/Scout/run-scout.sh > /dev/null 2>&1 &` to launch the session in the background. Capture the PID.

2. Find the most recent log file: `ls -t ~/Scout/.scout-logs/scout-*.log | head -1`

3. Report to the user:
   - Confirm the consolidation session is running and show the PID
   - Show the log file path so they can tail it if they want: `tail -f <log_file>`
   - Remind them that consolidation runs are lighter than morning briefings — they scan for deltas since the last run (new Slack messages, new calendar events, email sent, GitHub PRs) and update the existing action items file and KB accordingly.
