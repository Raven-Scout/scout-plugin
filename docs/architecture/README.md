# Scout architecture — C4 model

C4 diagrams for the Scout plugin and engine, as of plugin version 0.9.0. The
diagrams are Mermaid source in these markdown files, so GitHub renders them
inline and any change is a reviewable diff.

| Level | File | What it answers |
|---|---|---|
| 1 · System context | [01-system-context.md](01-system-context.md) | Who uses Scout, which external systems it talks to, and how |
| 2 · Containers | [02-containers.md](02-containers.md) | The six runnable or storable parts of Scout and the calls between them |
| 3 · Components | [03-components.md](03-components.md) | Inside each container: plugin surface, engine modules, runtime scripts |
| Deployment | [04-deployment.md](04-deployment.md) | Where every container sits on the owner's Mac and which endpoints it reaches |
| Dynamic | [05-scheduled-run.md](05-scheduled-run.md) | One scheduled run, numbered from launchd tick to wrap-up DM |

Level 4 (code) is deliberately omitted. The two places where code-level detail
is a cross-repo contract, the action-item parser corpus and the
`session-tokens.jsonl` schema, are called out in the component diagrams and
guarded by tests rather than drawn.

## How to read the diagrams

- **Person** is the vault owner. Scout is single-tenant by design; every
  instance has exactly one.
- **Blue boxes** are Scout's own containers and components. **Grey boxes** are
  external systems: Claude Code, the OS scheduler, the work tools reached over
  MCP, GitHub, Telegram, Obsidian and the companion apps.
- **Cylinders** are data stores. Scout has two kinds and the distinction
  matters: the **vault** (markdown and YAML, committed to git, browsed in
  Obsidian) and **operational state** (`.scout-state`, `.scout-logs`,
  `.scout-cache`, all gitignored).
- **Arrow labels** name the mechanism, not just the relationship: which
  `scoutctl` subcommand, which file, which CLI flag. Where a label says
  `scoutctl`, the caller is shelling out to the engine. Labels on the context
  and container diagrams are deliberately terse; the prose and tables under each
  diagram carry the full call lists.
- **Notation.** Levels 1 and 2, the deployment view and the dynamic view use
  Mermaid's native C4 syntax. The component views use Mermaid flowcharts with
  the same colour code, because the C4 grid layout cannot route twenty-odd
  labelled edges legibly and the flowchart engine can.
- The **Scout session** container is a Claude Code process that Scout launches
  with `claude -p`. It is drawn inside the Scout boundary because Scout's
  behaviour lives in the prompt it runs, even though the runtime is external.

## Naming used throughout

| Term | Meaning |
|---|---|
| Plugin root | The directory Claude Code loads the plugin from: a marketplace cache entry or a clone |
| Vault | `$SCOUT_DATA_DIR`, default `~/Scout`. A git repo and an Obsidian vault |
| Brain file | One of `SKILL.md`, `DREAMING.md`, `RESEARCH.md` in the vault, assembled from `phases/` |
| Runner | `run-scout.sh`, `run-dreaming.sh` or `run-research.sh`, rendered from `templates/` into the vault |
| Slot | One scheduled session in `.scout-state/schedule.yaml`, for example `morning-briefing` |
| Connector | A work tool the session can reach, keyed by MCP server name or `github` |

## Keeping the diagrams honest

The diagrams describe the code in this repository, not the roadmap. When a
change moves a boundary, for example a new `scoutctl` caller, a new file in
`.scout-state`, or a new Claude Code hook event, update the matching diagram in
the same pull request. Mermaid C4 syntax is documented at
<https://mermaid.js.org/syntax/c4.html>; GitHub renders it natively in markdown
previews and pull requests.
