# Deployment view

Where each container actually sits on the owner's Mac, and which network
endpoints it reaches. Everything runs under one user account; there is no
server component. On Linux the two LaunchAgents become two lines in a managed
crontab block and the rest is unchanged.

```mermaid
C4Deployment
  title Scout - deployment on one Mac

  Deployment_Node(mac, "Owner's Mac", "macOS, one user account") {
    Deployment_Node(launchd, "launchd LaunchAgents", "~/Library/LaunchAgents") {
      Container(tick_job, "com.scout.schedule-tick", "plist, StartInterval 300, RunAtLoad", "scoutctl schedule tick")
      Container(hb_job, "com.scout.heartbeat", "plist, StartInterval 1800", "bash vault/scripts/heartbeat.sh")
    }
    Deployment_Node(plugin_root, "Plugin root", "~/.claude/plugins/marketplaces/scout-plugin/ or a git clone") {
      Container(plugin_files, "scout plugin", "commands, skills, hooks, phases, templates", "Loaded by Claude Code at session start")
      Container(venv, "scout-engine venv", ".venv, Python 3.11 or newer", "scoutctl console script, editable install of engine/")
    }
    Deployment_Node(vault_node, "Vault", "~/Scout or SCOUT_DATA_DIR, git repo, Obsidian vault") {
      Container(runners, "Runtime scripts", "bash", "run-scout.sh, run-dreaming.sh, run-research.sh, scripts/, hooks/")
      ContainerDb(vault_files, "Knowledge and briefings", "markdown, YAML", "brain files, knowledge-base/, action-items/, meetings/, scout-config.yaml")
      ContainerDb(state_dirs, "State, logs, caches", ".scout-state, .scout-logs, .scout-cache", "gitignored")
    }
    Deployment_Node(claude_home, "Claude Code", "~/.claude") {
      Container(cc, "claude CLI", "Claude Code", "Runs Scout sessions. Loads the plugin and the MCP connectors the owner has connected")
      ContainerDb(transcripts, "Session transcripts", "~/.claude/projects, one jsonl per session")
    }
    Deployment_Node(home, "Home directory", "~") {
      Container(shim, "scoutctl shim", "~/.local/bin/scoutctl", "exec into the venv scoutctl")
      ContainerDb(secrets, "Telegram secrets", "~/.scout-secrets, mode 600")
    }
  }

  Deployment_Node(internet, "Internet") {
    Container_Ext(anthropic, "Anthropic API", "api.anthropic.com:443")
    Container_Ext(mcp_remote, "MCP connector servers", "claude.ai connectors and plugin MCP servers: Slack, Google, Linear, Granola, Fathom")
    Container_Ext(gh_remote, "GitHub", "api via gh CLI, raw.githubusercontent.com marketplace.json")
    Container_Ext(tg, "Telegram Bot API", "api.telegram.org")
  }

  Rel(tick_job, venv, "schedule tick")
  Rel(hb_job, runners, "heartbeat.sh")
  Rel(runners, venv, "scoutctl")
  Rel(venv, runners, "spawns", "Popen")
  Rel(runners, cc, "launches", "claude -p")
  Rel(cc, plugin_files, "loads")
  Rel(cc, venv, "Stop hooks")
  Rel(cc, vault_files, "reads, writes, commits")
  Rel(cc, transcripts, "writes")
  Rel(venv, transcripts, "reads")
  Rel(venv, state_dirs, "reads and writes")
  Rel(runners, state_dirs, "reads and writes")
  Rel(venv, vault_files, "bootstrap, action items")
  Rel(shim, venv, "exec")
  Rel(venv, secrets, "reads")
  Rel(venv, anthropic, "TCP probe")
  Rel(cc, anthropic, "inference", "HTTPS")
  Rel(cc, mcp_remote, "tool calls", "MCP")
  Rel(cc, gh_remote, "gh CLI")
  Rel(venv, gh_remote, "update check", "HTTPS")
  Rel(venv, tg, "sendMessage", "HTTPS")
```

## Install paths and the single source of truth

The engine works from any plugin root. Three layouts are supported and the
scheduler must run the same `scoutctl` the plugin loads:

| Layout | Plugin root |
|---|---|
| Marketplace install (`install.sh`, `/plugin install scout@scout-plugin`) | `~/.claude/plugins/marketplaces/<marketplace>/scout-plugin/` |
| Local plugins or dev tree (`claude --plugin-dir`) | any directory |
| Canonical clone | `~/scout-plugin/` |

`scoutctl schedule install-plist` and `install-cron` derive the scheduler's
binary from the running engine's own package location
(`Path(scout.__file__).parent.parent.parent / .venv/bin/scoutctl`). There is no
override knob, so the plist can never point at a different engine than the one
that wrote it. `bootstrap doctor` reads the installed plist back and flags RED
if the path is missing, not executable, or under a macOS TCC-protected folder
(`~/Documents`, `~/Desktop`, `~/Downloads`), where launchd would be denied.

## How the pieces get there

1. `install.sh` (curl-pipe) checks for `claude` and `git`, installs `uv` if
   needed, runs `claude plugin marketplace add Raven-Scout/scout-plugin` and
   `claude plugin install scout@scout-plugin`, finds the install path via
   `claude plugin list --json`, then runs `scripts/install-venv.sh` to create
   `<plugin_root>/.venv` and editable-install `engine/`.
2. `/scout-setup` probes connectors, collects user details and runs
   `scoutctl bootstrap install`, which creates the vault, renders the runtime
   scripts, assembles the brain files, seeds `.scout-state/schedule.yaml`,
   installs both LaunchAgents (or the cron block) and the `~/.local/bin` shim,
   and stamps the plugin version into `scout-config.yaml`.
3. `/scout-update` refreshes the plugin (git pull or marketplace update), then
   `scoutctl bootstrap upgrade` re-renders the scripts with backups, three-way
   merges the brain files, and re-installs the jobs idempotently.

Releases are cut by `scripts/release.sh` (version bump across four files,
changelog promotion, PR to `main`, then tag) and published by the
`release.yml` workflow, which attaches the changelog section to a GitHub
release. `scoutctl self-update check` compares the installed version against
the raw `marketplace.json` on `main`; it reports, it never applies.
