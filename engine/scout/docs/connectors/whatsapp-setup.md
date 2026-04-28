# WhatsApp inbound — operator setup

WhatsApp inbound lets a Scout session pull recent message threads from your
personal WhatsApp account into a briefing — same way Slack threads, Granola
transcripts, or Gmail searches feed in. It is **read-only by design**: Scout
never replies, never forwards, never sends a sticker. The session reads
threads, extracts signal, and surfaces it in your action items or briefing
notes.

The integration uses the
[verygoodplugins/whatsapp-mcp](https://github.com/verygoodplugins/whatsapp-mcp)
project: a small Go bridge that speaks the WhatsApp protocol via the
`whatsmeow` library, plus a Python MCP server that exposes a stdio interface
to Claude Code. The bridge holds a persistent session (~20-day TTL after a
one-time QR pairing).

> **Read this first.** WhatsApp threads contain attacker-controlled text —
> anyone you message with can paste anything they like into a conversation.
> The "Lethal trifecta" section below covers the prompt-injection caveat.
> By enabling WhatsApp inbound you are accepting that risk in exchange for
> the signal value.

## Privacy posture (what Scout will and won't do)

- **Inbound only.** The connector is registered in `connectors.yaml` with
  `capabilities: [inbound]`. Scheduled scout runs read; they do not write.
- **Allowlisted contacts only.** The WhatsApp phase MD instructs the session
  to scan only threads where the contact has a matching `whatsapp_id` (or
  matching `phone_number`) entry in `knowledge-base/people/`. Threads with
  contacts who are not in your KB are ignored.
- **No write tools, ever.** The MCP server exposes three write-capable tools
  (`send_message`, `send_audio_message`, `send_file`). The WhatsApp phase MD
  forbids calling any of them. This is defense-in-depth on top of the
  architectural inbound-only intent — there is no runtime sandbox in v0.4;
  the policy is enforced by the SCOUT skill prose.
- **Local data, local bridge.** The Go bridge runs on your Mac. WhatsApp
  message history stays on your machine; nothing transits a Scout-operated
  server.

If any of those don't sit right, don't enable this connector.

## 1. Install the WhatsApp MCP project

The recommended location is `~/.local/share/whatsapp-mcp/` (XDG-style; keeps
the bridge out of your project trees and your vault). **The path is your
choice** — anywhere you can `cd` into and run a Go binary works. If you pick
a different path, just keep it consistent across the install command, the
plist template, and the MCP config.

```bash
mkdir -p ~/.local/share
git clone https://github.com/verygoodplugins/whatsapp-mcp ~/.local/share/whatsapp-mcp
cd ~/.local/share/whatsapp-mcp
```

Layout (per upstream README):

```
whatsapp-mcp/
├── whatsapp-bridge/         Go bridge source (whatsmeow)
└── whatsapp-mcp-server/     Python MCP server (entry: main.py)
```

Build the Go bridge:

```bash
cd ~/.local/share/whatsapp-mcp/whatsapp-bridge
go build -o whatsapp-bridge
```

This produces a binary named `whatsapp-bridge` in the `whatsapp-bridge/`
subdirectory. (If the upstream project ever renames the binary, update the
plist template's `ProgramArguments` to match.)

Prerequisites you may need first:

- **Go** (any reasonably modern version; 1.21+ is safe). `brew install go`.
- **Python** + **uv** for the MCP server. `brew install uv`.

## 2. First-time pairing — scan the QR

The first time the bridge runs, it prints a QR code in the terminal and waits
for you to link it to your phone. Run it interactively:

```bash
cd ~/.local/share/whatsapp-mcp/whatsapp-bridge
./whatsapp-bridge
```

On your phone (Android — Google Messages and WhatsApp both live here):

1. Open WhatsApp.
2. Tap the three-dot menu → **Linked Devices**.
3. Tap **Link a device**.
4. Point the camera at the QR in your terminal.

Once paired, the bridge prints a "logged in" line and starts listening on
`localhost:8080`. The session lasts roughly 20 days before WhatsApp's
`whatsmeow` library forces a re-pair. When that happens you'll see auth
errors in `bridge.err.log` — repeat this step.

You can `Ctrl-C` out once paired; the session credentials are persisted in
the working directory.

## 3. Run the bridge as a launchd service

Once you've paired interactively, you'll want the bridge running
unattended so Scout's scheduled runs can connect. The plist template is at:

```
engine/scout/docs/connectors/com.scout.whatsapp-bridge.plist.template
```

Copy it into your LaunchAgents directory and edit:

```bash
cp ~/scout-plugin/engine/scout/docs/connectors/com.scout.whatsapp-bridge.plist.template \
   ~/Library/LaunchAgents/com.scout.whatsapp-bridge.plist
$EDITOR ~/Library/LaunchAgents/com.scout.whatsapp-bridge.plist
```

Replace the placeholders (the comment block at the top of the template lists
each one):

- `__WHATSAPP_MCP_DIR__` → e.g. `/Users/yourname/.local/share/whatsapp-mcp`
  (must be the absolute path; `~` doesn't expand inside plists).
- `__USER_HOME__` → output of `echo $HOME`, e.g. `/Users/yourname`.

Load and start:

```bash
launchctl load -w ~/Library/LaunchAgents/com.scout.whatsapp-bridge.plist
launchctl list | grep com.scout.whatsapp-bridge
```

A healthy entry shows a non-`-` PID. Logs go to `bridge.log` and
`bridge.err.log` inside the install directory.

To restart after upstream changes or a session expiry:

```bash
launchctl kickstart -k gui/$UID/com.scout.whatsapp-bridge
```

To unload entirely:

```bash
launchctl unload ~/Library/LaunchAgents/com.scout.whatsapp-bridge.plist
```

## 4. Wire the MCP server into Claude Code

The Python MCP server connects to Claude Code over stdio. Add an entry to
your Claude Code MCP config (`~/.claude.json`, in the `mcpServers` block —
this is where your existing Slack / Linear / Granola connectors live; open
the file and you'll see the shape).

Per the upstream README, the entry uses `uv` to run the server in-place:

```json
{
  "mcpServers": {
    "whatsapp-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/yourname/.local/share/whatsapp-mcp/whatsapp-mcp-server",
        "run",
        "main.py"
      ]
    }
  }
}
```

Replace the `--directory` path with wherever you cloned the repo. Restart
Claude Code (or reload MCP servers) to pick up the new entry.

The connector key Scout's hooks classify this under is `mcp:whatsapp-mcp`
— see `engine/scout/connectors.yaml` for the row that drives connector-health
reporting and remediation prose.

> **TODO** verify against upstream README at the time you install:
> <https://github.com/verygoodplugins/whatsapp-mcp>. The exact `command` /
> `args` shape can drift between releases. The shape above matches what the
> upstream README documented as of this doc's authoring.

## 5. Allowlist the contacts you want scanned

Scout reads the allowlist from your `knowledge-base/people/` files. To make a
contact eligible for WhatsApp scanning, add the relevant identifier to the
person's frontmatter. Per the v0.4 spec §6 (Data directory contract,
person frontmatter), identifiers are an **open set** — any key you add is
preserved as-is.

Two ways to anchor a contact to a WhatsApp thread:

```yaml
---
name: Example Person
type: person
phone_number: "+15551234567"   # E.164 — doubles as WhatsApp join key
---
```

or the explicit form (preferred when a person uses a different number for
WhatsApp than for SMS / phone calls):

```yaml
---
name: Example Person
type: person
phone_number: "+15551234567"
whatsapp_id: "+15559876543"     # E.164; overrides phone_number for WhatsApp scans
---
```

Use full E.164 (`+`-prefixed country code, no spaces, no dashes). The
WhatsApp phase MD does the matching: it pulls the contact list from the
bridge, looks up each number against `whatsapp_id` first, falls back to
`phone_number`, and ignores everything else.

> The allowlist is **not** enforced by code in v0.4. It's enforced by the
> SCOUT skill's WhatsApp phase MD reading the people files. If you bypass
> the skill (e.g. by calling MCP tools manually in an interactive Claude
> Code session), nothing stops you from reading any thread the bridge has
> access to. That's intentional — interactive sessions are user-controlled.
> Don't rely on the allowlist as a security boundary; rely on it as a
> behavior boundary for scheduled runs.

## 6. The lethal trifecta — what could go wrong

The "lethal trifecta" of LLM agent risk is: (1) access to private data,
(2) exposure to attacker-controlled inputs, (3) ability to exfiltrate
outwards. WhatsApp inbound checks (1) and (2) by default — a thread with
anyone you've ever messaged is a thread that can contain whatever they
want to put in front of an LLM.

The mitigations Scout relies on:

- **Architectural inbound-only intent.** The connector row in
  `connectors.yaml` says `capabilities: [inbound]`. Scheduled phases that
  load the WhatsApp MD operate under a "read but never call write tools"
  contract.
- **Explicit write-tool denylist.** The WhatsApp phase MD names the three
  write tools — `send_message`, `send_audio_message`, `send_file` — and
  tells the model never to invoke them. Per the upstream tool inventory
  (14 tools total), these are the only ones with side effects on the
  outside world. Everything else (contact search, thread listing, message
  fetching, media downloads to local disk) is read-only.
- **Allowlisted contact scope.** Scout will only scan threads from people
  you explicitly added to `knowledge-base/people/` with a matching
  identifier. A random `+1-202-555-0100` who messages you out of the blue
  won't be ingested.
- **Human review.** Everything Scout produces goes into your briefing or
  action items, which you read before doing anything. There is no "scout
  acted on a WhatsApp message and you found out later."

There is no runtime sandbox enforcing any of this in v0.4. The v0.5+
event-architecture spec discusses a stronger capability gate (see the
"Connector taxonomy" section), and bidirectional Telegram (the v0.7+
inbound return-bridge for replies) is where Scout starts having to enforce
this in code rather than prose. For v0.4 the user acknowledges by enabling.

## 7. Verifying the connector

After install, you can confirm Scout sees the bridge:

```bash
# Bridge is up:
launchctl list | grep com.scout.whatsapp-bridge
curl -s http://localhost:8080/health || echo "(no /health endpoint — bridge logs will tell you)"

# Connector is registered:
scoutctl manifest show 2>&1 | grep -i whatsapp
```

After the next scheduled briefing or weekend-briefing, check the
connector-health rollup:

```bash
grep -A 4 'WhatsApp' ~/Scout/knowledge-base/connector-health.md
```

If the row shows `0 calls` you've likely got an MCP-config gap; if it shows
errors you've likely got a bridge-down or session-expired situation —
remediation prose is in `connectors.yaml` under `mcp:whatsapp-mcp`.

## See also

- [`com.scout.whatsapp-bridge.plist.template`](com.scout.whatsapp-bridge.plist.template)
  — the launchd plist template referenced above.
- `telegram-setup.md` (TODO — created by Plan 4 Task 6) — outbound Telegram
  bot setup. Bidirectional Telegram (inbound return-bridge for replies) is
  v0.7+ per the event-architecture spec.
- `engine/scout/connectors.yaml` — the `mcp:whatsapp-mcp` row, including
  remediation prose surfaced in connector-health reports.
- v0.4 spec §6 (Data directory contract) — person frontmatter schema, where
  the open-set identifier convention is established.
- Upstream: <https://github.com/verygoodplugins/whatsapp-mcp>.
