---
phase: connector
name: github
slot: outbound-scan
mode: [consolidation]
requires: github
---

## GitHub Outbound Scan — What {{USER_NAME}} Did

Check recent code activity by {{USER_NAME}} using the `gh` CLI. This reveals completed work that should be reflected in action items and the KB.

### Recent PRs by {{USER_NAME}}

```bash
gh pr list --author @me --state all --json number,title,url,repository,state,updatedAt --limit 10
```

For each PR found:
- **Merged PRs** = completed work. Mark any corresponding action items as Done.
- **Open PRs** = work in progress. Note the PR status (draft, review requested, changes requested, approved).
- **Closed PRs** (not merged) = abandoned approach. Check if the underlying issue is still open.

### PRs {{USER_NAME}} Was Involved In

```bash
gh search prs --involves @me --updated ">=$(date -v-4H +%Y-%m-%dT%H:%M:%S)" --json number,title,url,repository
```

This catches PRs where {{USER_NAME}} reviewed, commented, or was mentioned — broader than just authored PRs.

### PR Reviews by {{USER_NAME}}

Check if {{USER_NAME}} submitted reviews on others' PRs. A submitted review means:
- The review request action item is handled
- {{USER_NAME}} may be waiting for the author to address feedback (new watching item)

### Recent Commits

For key repos in `{{GITHUB_REPOS}}`, check recent commit activity:

```bash
gh api "/repos/{owner}/{repo}/commits?author={{GITHUB_USERNAME}}&since=$(date -v-8H +%Y-%m-%dT%H:%M:%SZ)" --jq '.[].commit.message' 2>/dev/null | head -20
```

Commits outside of PRs (direct pushes) also indicate completed work.

---
phase: connector
name: github
slot: inbound-scan
mode: [consolidation, briefing]
requires: github
---

## GitHub Inbound Scan — What Happened to {{USER_NAME}}

Check for GitHub activity directed at {{USER_NAME}} — review requests, assignments, and comments.

### PR Review Requests

```bash
gh search prs --review-requested @me --state open --json number,title,url,repository,author
```

⚠️ **`--review-requested @me` expands team membership**, so this list mixes genuine review asks with {{USER_NAME}}'s *own delegated work* (a Linear issue delegated to an agent like Devin → the agent opens the PR and requests {{USER_NAME}}'s *team*). Classify each PR before treating it as a review item — see the three-bucket rule under **PRs Requesting {{USER_NAME}}'s Review** in the briefing query slot:
1. **Own delegated work** (`devin/` branch or bot author implementing {{USER_NAME}}'s own issue, or linked issue assigned to {{USER_NAME}}) → track-to-merge, **not** review.
2. **Requested by name** (`{{GITHUB_USERNAME}}` is a `User` in `reviewRequests`) → real review action item.
3. **Requested via team only** → shared queue, any teammate can satisfy.

Pull `gh pr view N --repo R --json author,headRefName,reviewRequests,isDraft` to classify. For buckets 2–3, note who requested it, which repo, how long it's waited (prioritize older), and PR size if available.

### New Issues Assigned

```bash
gh search issues --assignee @me --state open --updated ">=$(date -v-24H +%Y-%m-%dT%H:%M:%S)" --json number,title,url,repository
```

Check for issues recently assigned to {{USER_NAME}} or recently updated issues where {{USER_NAME}} is the assignee.

### Comments on {{USER_NAME}}'s PRs

For key repos in `{{GITHUB_REPOS}}`, check recent comments:

```bash
gh api "/repos/{owner}/{repo}/issues/comments?since=$(date -v-4H +%Y-%m-%dT%H:%M:%SZ)" --jq '.[] | select(.user.login != "{{GITHUB_USERNAME}}") | {body: .body[:100], user: .user.login, issue_url: .issue_url}' 2>/dev/null | head -50
```

Comments on {{USER_NAME}}'s PRs may contain:
- Review feedback needing response
- Questions about the implementation
- Approval or merge notifications
- CI/CD status updates

### Repository Activity

For repos in `{{GITHUB_REPOS}}`, check for significant activity:
- New releases or tags
- Branch protection changes
- New contributors or team changes

---
phase: connector
name: github
slot: query
mode: [briefing]
requires: github
---

## GitHub Query — Briefing Data Gathering

### Open PRs by {{USER_NAME}}

```bash
gh pr list --author @me --state open --json number,title,url,repository,reviewDecision,updatedAt,isDraft
```

For each open PR, note:
- Current review status (pending, approved, changes requested)
- Whether it's a draft
- How long it's been open
- Any CI checks failing

### PRs Requesting {{USER_NAME}}'s Review

```bash
gh search prs --review-requested @me --state open --json number,title,url,repository,author,createdAt
```

⚠️ **Do not blindly bucket these as "review these PRs."** `--review-requested @me` **expands team membership**, so this list mixes genuine review asks with {{USER_NAME}}'s *own delegated work* — e.g. a Linear issue {{USER_NAME}} delegated to an agent (Devin), where the agent opened the PR and requested {{USER_NAME}}'s *team*. That PR is "{{USER_NAME}}'s work to land/track to merge," **not** their queue to review. Presenting it as a review obligation creates a self-review loop.

Classify each returned PR into one of three buckets before writing any action item. The `author` field is already fetched; pull the branch + reviewer breakdown per PR:

```bash
# For each PR number N in repo R from the list above:
gh pr view N --repo R --json author,headRefName,reviewRequests,isDraft
```

1. **Your own delegated work → "track to merge," NOT review.** Any of:
   - the PR's branch is an agent-delegation branch (`headRefName` starts with `devin/`, or author is a bot like `app/devin-ai-integration`), **and** it implements an issue assigned to {{USER_NAME}}; or
   - the linked issue is assigned to {{USER_NAME}} (the PR is the implementation of {{USER_NAME}}'s own in-progress issue).
   Surface these under in-progress/track-to-merge framing, never "review requested to you."
2. **Requested by name (direct) → real review action item.** {{USER_NAME}}'s GitHub login (`{{GITHUB_USERNAME}}`) appears as a **User** entry in `reviewRequests`. This is a personal review ask.
3. **Requested via team only → shared queue (softer).** {{USER_NAME}} is *not* a named `reviewRequests` user but one of their **Team** entries is. Any teammate can satisfy it — present it as a team review queue item, distinct from a direct personal request, and don't escalate it as if only {{USER_NAME}} can clear it.

Only buckets 2 and 3 are review action items; bucket 1 belongs with {{USER_NAME}}'s own open work. When in doubt between a direct vs team request, check whether `{{GITHUB_USERNAME}}` is a named `reviewRequests` user.

### Recent Comments on Open PRs

For repos in `{{GITHUB_REPOS}}`, check for recent comment activity on open PRs:

```bash
gh api "/repos/{owner}/{repo}/pulls?state=open&sort=updated&direction=desc&per_page=5" --jq '.[].number' 2>/dev/null | while read pr; do
  gh api "/repos/{owner}/{repo}/pulls/$pr/comments?since=$(date -v-24H +%Y-%m-%dT%H:%M:%SZ)" --jq '.[] | {user: .user.login, body: .body[:80]}' 2>/dev/null
done | head -30
```

### Open Issues Assigned

```bash
gh search issues --assignee @me --state open --json number,title,url,repository,labels
```

---
phase: connector
name: github
slot: cross-check
mode: [consolidation, briefing]
requires: github
---

## GitHub Cross-Check

### Per-Claim Evidence Anchors

Every PR / merge / review / CI state claim — whether in a DM summary or an action-item row — must carry its **own** inline evidence anchor: the specific field value that proves it, not just a trailing "Source: GitHub" line. Anchor each claim to the datum:

- "merged" → cite the `mergedAt` timestamp (e.g. _merged 2026-06-01T14:22Z_)
- "approved" / "changes requested" → cite `reviewDecision` and the reviewer
- "CI green/red" → cite the `statusCheckRollup` conclusion
- "PR #N exists / open" → cite `#N` and `state`

A trailing source line covering a multi-claim sentence is insufficient — if a sentence asserts three states, it needs three anchors. This makes each claim independently verifiable and prevents one stale field from contaminating a whole row (Pattern #69 family).

Before promoting any candidate action item to To Do, verify against GitHub:

**When an action item references a specific PR, check the PR's current state:**

```bash
gh pr view {number} --repo {owner}/{repo} --json state,reviewDecision,mergedAt,closedAt,statusCheckRollup
```

- If the PR was merged since the action item was created, the item may be Done.
- If new commits were pushed that address review comments, update the item accordingly (e.g., "changes requested" -> "changes pushed, re-review needed").
- If CI checks are now passing that were previously failing, update the item.

**When an action item references code work, check for related PRs:**

```bash
gh search prs --author @me --repo {owner}/{repo} -- "{search terms}"
```

If a PR exists for the work described in the action item, link them and use the PR status as the source of truth.

**When an action item is about reviewing a PR, check if the review was already submitted:**

```bash
gh api "/repos/{owner}/{repo}/pulls/{number}/reviews" --jq '.[] | select(.user.login == "{{GITHUB_USERNAME}}") | {state: .state, submitted_at: .submitted_at}' 2>/dev/null
```

If {{USER_NAME}} already submitted a review, the item is Done.

---
phase: connector
name: github
slot: update
mode: [consolidation, briefing]
requires: github
---

## GitHub-Sourced KB Updates

After scanning GitHub, update the knowledge base with current code activity.

### PR Status in Project Files

For each active project in the KB, check if any open PRs are associated with that project. Update the project file with:
- PR number, title, and current status
- Review status (who reviewed, what was the decision)
- CI status if relevant
- Link to the PR

Update or remove references to PRs that have been merged or closed since the last run.

### Contributor Activity

If GitHub activity reveals new contributors to projects {{USER_NAME}} is involved in, add them to `people.md`:
- Name and GitHub username
- Context: "Contributor to {repo} — opened PR #{number}" or "Reviewed PR #{number} in {repo}"
- Role `[single-source]` based on their contribution pattern

### Issue Tracker Updates

If using GitHub Issues (instead of or alongside Linear), sync issue statuses:
- Verify open/closed status matches the KB
- Update labels and assignees
- Note any new issues filed in key repos

### Repository State

If significant repo changes occurred (new release, branch changes, CI/CD updates), note them in the relevant project files.
