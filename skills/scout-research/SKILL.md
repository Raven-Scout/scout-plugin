---
name: scout-research
description: Launch a Scout research session as a background process. Goes outward — discovers new information about KB entities via web search, docs, and APIs, then integrates findings.
---

Launch a SCOUT research session as a background process on this machine.

Do the following steps:

1. Run `nohup bash ~/Scout/run-research.sh > /dev/null 2>&1 &` to launch the research session in the background. Capture the PID.

2. Find the most recent log file: `ls -t ~/Scout/.scout-logs/research-*.log | head -1`

3. Report to the user:
   - Confirm the research session is running and show the PID
   - Show the log file path so they can tail it if they want: `tail -f <log_file>`
   - Remind them that research sessions typically take 10-15 minutes and cover: selecting research targets from the queue and KB entities, deep web research, knowledge integration into entity files, and a Slack summary DM when done.
