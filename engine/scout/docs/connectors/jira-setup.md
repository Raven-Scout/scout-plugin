# Jira Connector — Setup

Scout's Jira connector reads issues, comments, transitions, and sprints through the `jtk` Atlassian CLI (https://github.com/open-cli-collective/atlassian-cli).

## 1. Install `jtk`

Verify what you have:

```bash
jtk --version
```

If missing, install (one of):

```bash
# Homebrew (macOS / Linux)
brew install open-cli-collective/tap/jtk

# Go
go install github.com/open-cli-collective/atlassian-cli/cmd/jtk@latest
```

Consult the upstream README for the current install path: https://github.com/open-cli-collective/atlassian-cli

## 2. Authenticate

`jtk` needs your Jira Cloud base URL, email, and an API token.

1. Generate an API token at https://id.atlassian.com/manage-profile/security/api-tokens (label it `jtk` so you can revoke it later).
2. Run the guided init:

```bash
jtk init
```

Provide the base URL (e.g. `https://your-org.atlassian.net`), your email, and the token. The credentials are stored in the OS keyring where supported.

Verify the connection:

```bash
jtk config test
```

A success message means Scout will detect the connector during `/scout-setup`. If the test fails, re-run `jtk init` with a fresh token.

## 3. Capture Your Account ID

Scout stores your Jira account ID in `scout-config.yaml` as `user.jira_account_id`. The setup wizard asks for it, but you can fetch it manually:

```bash
jtk me --id
```

Copy the printed account ID (e.g. `5b10ac8d82e05b22cc7d4ef5`).

## 4. Pick the Projects Scout Should Monitor

Scout queries are scoped to a comma-separated project list (`jira_projects` in `scout-config.yaml`). The first key is treated as the primary project for sprint queries.

List project keys you have access to:

```bash
jtk projects list --max 200
```

Pick the keys you actually work in (e.g. `PROJ, PLAT, OPS`). Avoid adding org-wide noisy projects — they make the daily JQL queries slower and noisier without adding signal.

## 5. Optional — Confirm a Sample JQL Returns

Scout's queries lean on JQL. Confirm at least one returns a non-empty result:

```bash
jtk issues search --jql "assignee = currentUser() AND statusCategory != Done" --max 5
```

If the response is `No issues found` and you do have open assigned issues, double-check the base URL / account in `jtk config show` — you may be authed against the wrong Atlassian site.

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `jtk: command not found` | Binary not on PATH | Reinstall, or add the brew/go bin dir to `PATH` |
| `jtk config test` fails with 401 | API token rotated, expired, or revoked | Generate a new token and re-run `jtk init` |
| JQL returns 400 / `Field "X" does not exist` | Custom field renamed or absent in your instance | Edit the JQL in `phases/connectors/jira.md` (or the assembled `SKILL.md`) to match your field names |
| Empty results despite open issues | Wrong Atlassian site auth | `jtk config show` and re-init against the right base URL |
| Sprint queries fail | Project isn't scrum-mode (no sprints) | Drop sprint queries by leaving `jira_primary_project` empty |

## 7. What Scout Reads / Writes

**Reads** (per scheduled run):

- `jtk issues search` with JQL filters scoped to {{USER_NAME}} or `${JIRA_PROJECTS}`
- `jtk issues get <KEY>` for individual issue cross-checks
- `jtk comments list <KEY>` for thread context and "did {{USER_NAME}} already act on this?" detection
- `jtk issues list --sprint current` for the active-sprint snapshot in briefings
- `jtk me --id` once at setup to capture the account ID

**Writes:** None. Scout never transitions tickets, files comments, or assigns issues automatically. Any write actions ({{USER_NAME}} commenting, transitioning, etc.) happen through the interactive `/scout-work` flow with explicit approval per item.
