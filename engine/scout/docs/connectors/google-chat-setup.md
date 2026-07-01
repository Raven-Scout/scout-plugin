# Google Chat Connector — Setup

Scout's Google Chat connector reads spaces, DMs, threads, and reactions through the `gws` Google Workspace CLI (https://github.com/googleworkspace/cli) and posts run-summary notifications back into a self-DM.

## 1. Install `gws`

The `gws` binary is distributed via npm and Homebrew. Verify what you have:

```bash
gws --version
```

If missing, install it (one of):

```bash
# npm
npm install -g @googleworkspace/cli

# Homebrew (macOS / Linux)
brew install googleworkspace/tap/gws
```

The exact install command may evolve — consult the upstream README at https://github.com/googleworkspace/cli for the current path.

## 2. Authenticate

`gws` reads credentials from one of the following, in priority order:

| Variable | Purpose |
|----------|---------|
| `GOOGLE_WORKSPACE_CLI_TOKEN` | Pre-obtained OAuth2 access token (highest priority — useful for short-lived runs) |
| `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` | Path to OAuth credentials JSON file |
| `GOOGLE_WORKSPACE_CLI_CLIENT_ID` + `GOOGLE_WORKSPACE_CLI_CLIENT_SECRET` | Client credentials used by `gws auth login` |

For a personal install, run the interactive login once:

```bash
gws auth login
```

Confirm Scout's required scopes are granted. At minimum:

- `https://www.googleapis.com/auth/chat.spaces.readonly` (list spaces, find DM)
- `https://www.googleapis.com/auth/chat.messages.readonly` (read messages, threads, reactions)
- `https://www.googleapis.com/auth/chat.messages.create` (post run-summary DMs)

Verify the install:

```bash
gws chat spaces list --format json --params '{"pageSize":1}'
```

A 200 response with a `spaces` array (possibly empty) means Scout will detect the connector during `/scout-setup`. A 401/403 means re-run `gws auth login` and ensure the scopes above are checked.

## 3. Find Your Google Chat User ID

Scout stores a numeric user ID (no `users/` prefix) in `scout-config.yaml` as `user.google_chat_id`. To retrieve it:

```bash
gws chat users get --params '{"name":"users/me"}' --format json
```

Copy the digits from the `name` field (e.g. `users/123456789012345678901` -> `123456789012345678901`). The setup wizard will prompt for this value when it detects Google Chat is connected.

## 4. Optional — Cache the Self-DM Space

The first run resolves your self-DM space via:

```bash
gws chat spaces findDirectMessage --params '{"name":"users/<YOUR_ID>"}'
```

The notification phase writes the resulting space resource name into `.scout-cache/google-chat-self-dm` so subsequent runs skip the lookup. This file is gitignored and auto-recreated.

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `gws: command not found` | Binary not on PATH | Reinstall, or add the npm/brew bin dir to `PATH` |
| `error: Auth — credentials missing or invalid` (exit 2) | OAuth token expired or scopes missing | `gws auth login` and re-grant scopes |
| `findDirectMessage` returns 404 | No DM space exists yet | Send any manual message to yourself in Google Chat once, then re-run |
| Empty `spaces.list` response | New account / no spaces joined yet | Join at least one space or wait for the first DM, then re-run |

## 6. What Scout Reads / Writes

**Reads** (per scheduled run):

- `gws chat spaces list` (cached for the run)
- `gws chat spaces messages list` per space, scoped by `create_time` since the last run
- `gws chat spaces messages get` for individual message details when cross-checking
- `gws chat users get` to resolve unknown sender IDs into names

**Writes** (notification phase only):

- `gws chat +send --space <self-DM> --text "<run summary>"` (or the equivalent `gws chat spaces messages create` for richer payloads)

No external Google Chat data is written outside the self-DM. Scout never posts into shared spaces on your behalf.
