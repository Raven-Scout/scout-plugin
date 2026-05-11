---
name: scout-dream
description: Launch a Scout dreaming session as a background process. Processes feedback, does KB deep work, and works on wishlist items.
---

Launch a SCOUT dreaming session as a background process on this machine.

Do the following steps:

1. Run `nohup bash ~/Scout/run-dreaming.sh > /dev/null 2>&1 &` to launch the dreaming session in the background. Capture the PID.

2. Find the most recent log file: `ls -t ~/Scout/.scout-logs/dreaming-*.log | head -1`

3. Report to the user:
   - Confirm the dreaming session is running and show the PID
   - Show the log file path so they can tail it if they want: `tail -f <log_file>`
   - Remind them that dreaming typically takes 10-15 minutes and covers: feedback processing (Slack reactions/replies), KB deep work (file scoring and targeted improvements), wishlist items, and a Slack summary DM when done.
