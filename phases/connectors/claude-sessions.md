---
phase: connector
name: claude-sessions
slot: outbound-scan
mode: [consolidation]
requires: claude_sessions
---

## Claude Sessions Outbound Scan — What {{USER_NAME}} Worked On

Scan recent Claude Code sessions to understand what {{USER_NAME}} worked on with AI assistance. This catches work that may not appear in other connectors — code written but not yet committed, research done, documents drafted, bugs debugged, or plans created.

### Find Recent Sessions

```bash
# Find session files modified in the last 24 hours
find ~/.claude/projects -name "*.jsonl" -mtime -1 2>/dev/null
```

### Scan Session History

```bash
# Quick scan of recent user prompts with project context
tail -50 ~/.claude/history.jsonl 2>/dev/null | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        d = json.loads(line)
        proj = d.get('project', '?').split('/')[-1][:30]
        print(f\"{d['timestamp']} | {proj} | {d['display'][:100]}\")
    except (json.JSONDecodeError, KeyError):
        pass
"
```

### What to Look For

Session history reveals what {{USER_NAME}} spent time on. Common patterns:

- **PRs created or worked on** — if {{USER_NAME}} used Claude to help with a PR, the PR likely exists (cross-check with GitHub). The action item for that work may be Done or In Progress.
- **Bugs debugged** — if {{USER_NAME}} was debugging something, the bug may now be fixed. Check for commits or PR activity.
- **Documents written** — if {{USER_NAME}} drafted a document or proposal, it may have been sent or shared (check email, Drive, Slack).
- **Research completed** — if {{USER_NAME}} was researching a topic, the research phase of that action item may be Done.
- **Code reviewed** — if {{USER_NAME}} used Claude to help review code, that review may have been submitted (check GitHub).
- **Plans created** — if {{USER_NAME}} made implementation plans, the underlying work may be starting or in progress.

### Matching Sessions to Action Items

For each session activity found:
1. Identify which project or action item it relates to
2. Check if the work resulted in a tangible output (commit, PR, message, document)
3. If a corresponding action item exists, update its status based on the session evidence
4. If the session reveals new work not yet tracked, note it as context for the consolidation

This connector catches the gap between "{{USER_NAME}} worked on something" and "the output appeared in another system." Work done in Claude sessions often shows up in GitHub, email, or Slack shortly after — but during consolidation, the session may be the earliest signal of completed or in-progress work.

### Uncommitted Working-Tree Sweep

A session may have changed files that were never committed — invisible to `git log` and to GitHub. For each repo a session touched, also check the working tree so "in progress" vs "done" is grounded:

```bash
cd <repo> && git status --porcelain && git log --since='<last-run-time>' --oneline
```

### Narrate {{INSTANCE_NAME}}'s Own Development

{{INSTANCE_NAME}} maintains its own codebase, and {{USER_NAME}}'s work *on the system itself* is a real signal the standard connector scans miss — those scans target {{USER_NAME}}'s work product (the repos and trackers tied to projects), not the instance's own development. So commits and PRs against the engine/plugin or a companion app, and sessions spent building {{INSTANCE_NAME}}, go invisible unless swept explicitly.

The vault's own git delta is already narrated (see the Git Setup phase). Extend the **same treatment to the instance's other repositories** — an engine/plugin checkout, a desktop or mobile app — each run:

```bash
# For each instance-owned dev repo (read from this instance's configuration, not hardcoded):
git -C <repo> log --since='<last-run-time>' --oneline --shortstat
```

Narrate the deltas in the run summary, and surface an instance-owned PR that's open and waiting on review (or stale ≥48h) in the action items just as a work-product PR would be. **Parameterize the repo set** — read it from this instance's configuration; never hardcode an absolute user path or a specific private-repo name. An instance whose only repo is its vault simply relies on the vault narration.

### Claim Gate — No "Actively Building X" Without a Cited Signal

Do not assert that {{USER_NAME}} "is actively building / working on X" unless you can cite a concrete signal: a session JSONL with matching prompts, a commit SHA, an open PR, or dirty working-tree files. A session *title* or a single prompt is weak evidence — tie the claim to the tangible artifact, or downgrade it to "{{USER_NAME}} opened a session about X" rather than asserting active work.

### Profile Signal — Feed `knowledge-base/profile/`

Your own sessions are a uniquely good source for the user profile, because they show how {{USER_NAME}} actually works and writes — something the inbound connectors can't see. Stays local (`cc-session-cache` summaries); nothing leaves the machine. While scanning, also harvest, with the same confidence discipline as everywhere else:

- **Identity & focus → `profile/about-you.md`:** recurring projects, domains, and tools across sessions sharpen the "current focus" and role/team fields. Tag inferred claims `[single-source]` until corroborated.
- **Communication style → `profile/communication.md`:** the language {{USER_NAME}} writes in and the way they phrase requests (terse vs. narrative, direct vs. exploratory) are a strong prior for the reply-language and tone defaults. Treat this as a *signal*, not a confirmed preference — the Dreaming feedback loop, not this scan, is what writes confirmed comms changes.
- **Candidate goals → `profile/goals.md`:** a theme {{USER_NAME}} returns to across many sessions is a goal candidate — draft it under **Proposed (unconfirmed)** with the sessions as evidence. Never auto-confirm.
