# `scoutctl bootstrap auto` — unified install/upgrade entrypoint

**Status:** proposal — tracked in GitHub issue, open for contributors.
**Drafted:** 2026-05-19
**Motivation:** see [PR #25](https://github.com/jordanrburger/scout-plugin/pull/25) and the
"plug-upgrade-rough-edges" bug bundle it fixed.

---

## Context

Today the engine exposes three bootstrap entrypoints — `install`, `upgrade`,
`migrate-legacy` — and the user picks the right one via slash commands
(`/scout-setup`, `/scout-update`). That design has two costs:

1. **All paths require an active Claude Code session.** A terminal-only user
   (CI, ssh box, headless server, sceptic of LLM-mediated setup) has no
   single command to run. They have to read the slash-command markdown,
   reconstruct the flag set by hand, and dispatch the right subcommand.
2. **The LLM is load-bearing for state detection.** The slash command
   reads vault markers in bash and branches; if the runbook drifts or the
   LLM skips a step, the user lands in the wrong subcommand (e.g., running
   `upgrade` on a legacy vault, getting the "run migrate-legacy first"
   error, having to switch contexts).

Both costs surfaced in PR #25 — the friend hit them as install/upgrade
papercuts because the engine's contract assumes a perfect runbook executor.

## Proposal

Add `scoutctl bootstrap auto` — a single command that:

1. **Detects vault state** from the filesystem (the same logic
   `bootstrap.py` already uses internally).
2. **Dispatches** to `install`, `migrate-legacy`, or `upgrade` accordingly.
3. **Reuses persisted inputs** from `scout-config.yaml` as defaults on
   re-runs, so an update doesn't need to re-supply every flag.
4. **Supports both interactive and non-interactive modes** through a
   single flag surface — terminal users get Typer prompts, slash-command
   users get pre-filled flags, both run the same code path.

### State → action table

| Filesystem state | Action |
|---|---|
| `$SCOUT_DIR` doesn't exist OR is empty | `install` |
| `$SCOUT_DIR/.scout-state/` exists, `scout-config.yaml` missing | `migrate-legacy` |
| `$SCOUT_DIR/scout-config.yaml` exists | `upgrade` |
| Pending `*.md.proposed-merge` sidecars present | refuse with sidecar-resolution hint (current behavior) |

### Flag surface

```
scoutctl bootstrap auto [OPTIONS]

  --interactive / --no-interactive    Prompt for missing required fields.
                                      Default: --interactive when stdin is
                                      a TTY, --no-interactive otherwise.
  --yes                               Skip the confirmation prompt before
                                      running the chosen action.
  --dry-run                           Print the plan and exit 0 without
                                      mutating anything.

  # Identity (required for install / migrate-legacy)
  --user-name TEXT
  --user-email TEXT
  --instance-name TEXT                Default: Scout

  # Environment
  --timezone TEXT                     Default: America/New_York
  --platform [auto|macos|linux]       Default: auto (detect from uname)

  # Connectors
  --connectors TEXT                   Comma-separated enabled names
  --user-slack-id TEXT
  --github-username TEXT
  --github-repos TEXT
  --claude-bin TEXT                   Default: auto (which claude)
  --max-budget TEXT                   Default: 5.00

  # Job lifecycle
  --no-jobs                           Skip launchd / cron registration
  --skip-claude                       Don't shell out to Claude during
                                      first-run sanity checks
```

### Semantics

**`--interactive` prompting:** Typer's `prompt=` mechanism handles missing
required fields one at a time. On re-run, the existing
`scout-config.yaml` provides defaults so the user just hits Enter to keep
each value. Non-interactive mode errors out with a clear message
indicating which flag is missing.

**`--platform auto`:** maps `uname -s` to `macos` / `linux`. Errors out
on unsupported platforms.

**`--claude-bin auto`:** runs `which claude` (or `command -v claude`).
Errors out if not found and interactive mode is off; in interactive mode
prompts the user.

**`--dry-run`:** prints the detected state, the action that would be
taken, and the rendered flag set, then exits 0. Useful for slash-command
debug ("what would auto do with this state?").

**Re-run convergence:** running `bootstrap auto` twice in a row on a
Plan-8 vault is identical to running `bootstrap upgrade` twice — fully
idempotent.

### Slash-command refactor

`/scout-setup` and `/scout-update` collapse into a single
`/scout-bootstrap` runbook that:

1. Probes connectors via MCP tools (still LLM-mediated — this is the
   piece that genuinely benefits from a conversational interface, since
   the probe registry has fallback chains and asks about user inputs).
2. Computes the right flag set.
3. Execs `scoutctl bootstrap auto --yes <flags...>`.

The two old commands stay as aliases for muscle memory but the runbook
underneath is one file. The engine doesn't lose any of `install`,
`upgrade`, or `migrate-legacy` — `auto` is a wrapper, not a replacement.

### Test surface

- Empty vault dir → dispatches to `install`.
- Legacy vault (`.scout-state/` present, no `scout-config.yaml`) →
  dispatches to `migrate-legacy`.
- Plan-8 vault → dispatches to `upgrade`.
- Pending sidecar → refuses with the same message `upgrade` uses today.
- `--dry-run` prints state and action without mutating.
- Re-run after an install reads the persisted connector inputs back as
  prompt defaults (verified via stdin scripting).
- `--no-interactive` + missing required field → exits non-zero with a
  flag-name hint.
- `--platform auto` resolves `macos` on Darwin runners and `linux`
  elsewhere; errors on unknown.

### Migration plan

Phase 1 (this proposal):
- Add `scoutctl bootstrap auto` next to existing subcommands.
- Add tests above.
- Add a `bootstrap` section to README documenting the three modes as
  implementation details and `auto` as the recommended entrypoint.

Phase 2 (follow-up issue):
- Refactor `/scout-setup` + `/scout-update` into one `/scout-bootstrap`
  runbook that calls `auto`. Keep both old names as thin aliases.

Phase 3 (follow-up issue):
- Consider deprecating the standalone `install` / `upgrade` /
  `migrate-legacy` subcommands once `auto` has been stable for a release
  or two. They'd remain reachable as `scoutctl bootstrap _install` etc.
  for tests and debugging.

## Non-goals

- **GUI installer.** Out of scope. The CLI surface is the contract.
- **Pip / Homebrew distribution.** Separate concern. `auto` is for users
  who already have the plugin checked out via Claude Code's marketplace
  flow.
- **Backwards-incompatible flag changes.** `install` / `upgrade` /
  `migrate-legacy` keep their current contracts for the foreseeable
  future.

## Open questions

1. **Where does `--user-name` come from on auto-dispatch to upgrade?**
   Today, `upgrade` reads name/email back from `scout-config.yaml`. If
   `auto` is called with `--user-name=Bob` on an existing vault, does it
   update the persisted value or ignore the flag? Proposal: warn and
   ignore unless `--update-identity` is passed.
2. **Should `auto` write a `bootstrap.log` line each run** so the doctor
   can show "last-run mode: upgrade (2026-05-19 09:14)"? Probably yes —
   trivial to add, makes diagnostics better.
3. **Interactive mode default on slash commands.** Slash commands set
   `--no-interactive` and supply all flags, so the LLM mediates the
   prompts. That's the right default — confirm with a contributor before
   shipping in case there's a case I'm missing.

## Reference

Original bug bundle that motivated this design: [PR #25](https://github.com/jordanrburger/scout-plugin/pull/25).
