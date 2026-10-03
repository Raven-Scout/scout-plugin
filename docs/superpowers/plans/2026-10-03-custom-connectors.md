# Custom Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any connector a user has — Outlook, Teams, a CRM, a data platform, a CLI — can be added to Scout through one vault file and a `scoutctl` command, and scheduled sessions actually read it.

**Architecture:** A vault-owned `connectors.custom.yaml` declares, per connector, which of Scout's three activities it serves (inbound / outbound / lookup), the tools for each, and a sentence of guidance (or a preset that supplies it). Assembly renders three shipped activity templates (`phases/custom/`) once per enabled custom connector and appends them after the shipped connector sections; the probe registry, the health roster, and the connector-log hook derive their entries from the same file. `scoutctl connectors custom add|remove` apply a change to the live brain files through a dedicated 3-way-merge path, which is the contract the desktop app and the `/scout-setup` / `/scout-connect` wizards both call.

**Tech Stack:** Python 3.11+ engine (`engine/scout`), Typer CLI, PyYAML, pytest; `git merge-file` via `scout.scripts.three_way_merge`; Markdown command prose under `commands/`.

**Spec:** `docs/superpowers/specs/2026-10-02-custom-connectors-design.md`

## Global Constraints

- Vault file: `<vault>/connectors.custom.yaml`, top level `schema_version: 1` + `connectors:` mapping. Never written by anything except `custom_connectors.write()`.
- Connector key: `^[a-z][a-z0-9_]{1,31}$`; must not equal a shipped probe key, any phase `requires:` value, or any key or target of `CONNECTOR_KEY_ALIASES`.
- Activities are exactly `inbound`, `outbound`, `lookup`. Guidance field is `focus` for inbound/outbound and `when` for lookup.
- Tool reference: an MCP tool name `mcp__<server>__<tool>` or `{bash: "<command>"}`. Every MCP tool and an MCP probe must share the connector's `server`.
- Inputs: names `^[a-z][a-z0-9_]*$`; stored in `scout-config.yaml` `connectors.inputs` as `<key>__<name>`; rendered as `{{INPUT_<NAME>}}`.
- Credential guard regex (reject on match anywhere in a definition): `(?<![A-Za-z0-9])(?:xox[abp]-|ghp_|gho_|github_pat_|sk-|lin_api_)[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._~+/-]{12,}`.
- `custom` subcommands always print one JSON object and exit `0` applied/unchanged/valid/dry-run, `1` error, `2` invalid/probe-failed, `3` saved-but-not-live (deferred or conflict).
- Custom health rows: `tier: custom`, `capabilities: [inbound]`, default `required_in_types: []`; shipped and overlay rows win on key collision. Custom rows never enter `connectors.snapshot.json`.
- A broken custom file must never crash assembly, the roster loader, the probe registry, the connector-log hook, or the health report — skip with a warning instead.
- Custom connectors are read-only: templates and wizard prose forbid tools that send, create, update, or delete.
- Fixtures are anonymized (repo `CLAUDE.md`): generic server names (`example_suite`, `dataplat`), people `Alex`/`Priya`/`Sam`, no real vendors, workspaces, or ids.
- Lint gates (CI `lint.yml`): `ruff check scout tests`, `ruff format --check scout tests`, `mypy scout`. Line length 120.

## Review Focus

1. **A hand-edited `connectors.custom.yaml` that is invalid YAML or has a broken entry** — assembly, roster, probes, hook, and doctor must all keep working and skip only the broken entry; the user expects their other connectors to keep running. Pinned in Task 4 (assembly + doctor) and Task 8 (roster/probe/hook).
2. **A connector present in the file but not in `connectors.enabled`** (user disabled it by hand) — no sections render for it. Pinned in Task 4.
3. **Re-running `/scout-connect outlook` to change its tools** — `add` replaces the entry and the rendered section changes in place; no duplicate entry, no duplicate section. Pinned in Task 6.
4. **Guidance text that innocently contains a credential-like prefix** (`task-list`, `risk-based`) — must not be rejected as a secret. Pinned in Task 1.
5. **A bash connector whose command starts with a generic binary** (`curl …`, `python3 …`) — the hook must not relabel every `curl` call in the session as that connector. Pinned in Task 8.

---

## Before you start

Work in the worktree `~/scout-plugin-wt-custom-connectors` on branch `docs/custom-connectors` (it already carries the spec and this plan). It has no venv of its own; create one there — **never** touch `~/scout-plugin/engine/.venv`, which is the user's live engine:

```bash
cd ~/scout-plugin-wt-custom-connectors/engine
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/pytest tests/unit -q   # baseline must be green before Task 1
```

All `pytest` commands below run from `engine/` with `.venv/bin/pytest`.

## File Structure

| File | Responsibility |
|---|---|
| `engine/scout/custom_connectors.py` (new) | Model, parsing/validation, presets loader, reserved keys, load/write of the vault file, bash-binary map for the hook. No assembly, no CLI. |
| `phases/presets/{mail,chat,calendar}.yaml` (new) | Default guidance text per activity. Data only. |
| `phases/custom/{inbound,outbound,lookup}.md` (new) | The three activity templates (one section each, no `requires:`). |
| `engine/scout/scripts/custom_assembly.py` (new) | Renders custom sections from templates + connectors. Shared by assembly and backport. |
| `engine/scout/scripts/bootstrap.py` (modify) | `_assemble` appends custom sections; `load_custom`, `config_from_vault`, `write_connector_config`, `apply_custom_change`; install writes the custom file. |
| `engine/scout/scripts/phase_backport.py` (modify) | Renders custom sections too; never back-ports into them. |
| `engine/scout/scripts/bootstrap_doctor.py` (modify) | Yellow warning per custom-file issue. |
| `engine/scout/scripts/custom_connector_ops.py` (new) | `validate` / `add` / `remove` / `list_custom` / `presets_json` with the JSON + exit-code contract. |
| `engine/scout/cli.py` (modify) | `connectors custom …`, `connectors presets`, `connectors list --json`, install flags, backport wiring. |
| `engine/scout/connectors.py` (modify) | `Tier.CUSTOM`; derived custom roster rows. |
| `engine/scout/scripts/connector_probes.py` (modify) | Derived custom probes. |
| `engine/scout/hooks/connector_log.py` (modify) | Custom bash binaries → connector key. |
| `commands/scout-setup.md`, `commands/scout-update.md` (modify), `commands/scout-connect.md` (new) | Wizard flow. |
| `README.md`, `CHANGELOG.md` (modify) | User docs. |

---

### Task 1: Custom-connector model and validation

**Files:**
- Create: `engine/scout/custom_connectors.py`
- Test: `engine/tests/unit/test_custom_connectors_model.py`

**Interfaces:**
- Produces (used by every later task):
  - `CUSTOM_FILE: str = "connectors.custom.yaml"`, `ACTIVITIES: tuple[str, ...]`, `GUIDANCE_FIELD: dict[str, str]`
  - `@dataclass(frozen=True) ToolRef(kind: str, value: str)` with properties `server -> str | None`, `binary -> str | None`
  - `@dataclass(frozen=True) Activity(tools: tuple[ToolRef, ...], guidance: str)`
  - `@dataclass(frozen=True) CustomConnector(key, display_name, server: str | None, probe: ToolRef, preset: str | None, activities: dict[str, Activity], notes="", needs_user_input: tuple[str, ...]=(), required_in_types: tuple[str, ...]=())` with property `health_key -> str`
  - `@dataclass(frozen=True) Issue(path: str, message: str)`
  - `@dataclass CustomLoad(connectors: dict[str, CustomConnector], issues: list[Issue], raw: dict[str, Any])`
  - `first_binary(cmd: str) -> str | None`
  - `parse_connector(key: str, body: Any, *, reserved: set[str], presets: dict[str, dict[str, str]]) -> tuple[CustomConnector | None, list[Issue]]`
  - `parse_file(raw: Any, *, reserved: set[str], presets: dict[str, dict[str, str]]) -> CustomLoad`
  - `default_plugin_root() -> Path`, `load_presets(plugin_root: Path) -> dict[str, dict[str, str]]`, `reserved_keys(plugin_root: Path) -> set[str]`
  - `load(vault: Path, *, plugin_root: Path | None = None) -> CustomLoad`
  - `dump(raw_connectors: dict[str, Any]) -> str`, `write(vault: Path, raw_connectors: dict[str, Any]) -> None`
  - `bash_binaries(vault: Path) -> dict[str, str]` (binary → connector key)

- [ ] **Step 1: Write the failing tests**

```python
"""custom_connectors: parsing and validation of connectors.custom.yaml entries."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout import custom_connectors as cc

PRESETS = {"mail": {"summary": "Mail", "inbound": "Inbox rules for {{USER_NAME}}.", "outbound": "Sent rules."}}
RESERVED = {"slack", "email", "gmail", "github"}


def _parse(key: str, body: object) -> tuple[cc.CustomConnector | None, list[cc.Issue]]:
    return cc.parse_connector(key, body, reserved=RESERVED, presets=PRESETS)


def _messages(issues: list[cc.Issue]) -> str:
    return " | ".join(f"{i.path}: {i.message}" for i in issues)


MAIL = {
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
    "outbound": {"tools": ["mcp__example_suite__search_messages"]},
}


def test_valid_mcp_connector_fills_guidance_from_preset():
    c, issues = _parse("suite_mail", MAIL)
    assert issues == []
    assert c is not None
    assert c.server == "example_suite"
    assert c.health_key == "mcp:example_suite"
    assert c.activities["inbound"].guidance == "Inbox rules for {{USER_NAME}}."
    assert c.activities["inbound"].tools == (cc.ToolRef("mcp", "mcp__example_suite__search_messages"),)
    assert "lookup" not in c.activities


def test_explicit_focus_overrides_preset():
    body = {**MAIL, "inbound": {"tools": MAIL["inbound"]["tools"], "focus": "Only the shared queue."}}
    c, issues = _parse("suite_mail", body)
    assert issues == [] and c is not None
    assert c.activities["inbound"].guidance == "Only the shared queue."


def test_bash_connector_without_server_keys_health_on_its_own_key():
    body = {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "inbound": {"tools": [{"bash": "tix list --assignee me"}], "focus": "Tickets that changed."},
    }
    c, issues = _parse("tickets", body)
    assert issues == [] and c is not None
    assert c.server is None
    assert c.health_key == "tickets"
    assert c.probe.binary == "tix"


@pytest.mark.parametrize(
    ("key", "body", "expected"),
    [
        ("Bad-Key", MAIL, "lowercase"),
        ("slack", MAIL, "built-in"),
        ("gmail", MAIL, "built-in"),
        ("suite_mail", "not a mapping", "must be a mapping"),
        ("suite_mail", {**MAIL, "colour": "blue"}, "unknown field"),
        ("suite_mail", {k: v for k, v in MAIL.items() if k != "display_name"}, "display_name: required"),
        ("suite_mail", {k: v for k, v in MAIL.items() if k != "probe"}, "probe: required"),
        ("suite_mail", {**MAIL, "preset": "fax"}, "unknown preset"),
        ("suite_mail", {**MAIL, "inbound": {"tools": []}}, "non-empty list"),
        ("suite_mail", {**MAIL, "inbound": {"tools": ["search_messages"]}}, "not an MCP tool name"),
        ("suite_mail", {**MAIL, "server": "other_suite"}, "belongs to server"),
        ("suite_mail", {k: v for k, v in MAIL.items() if k != "server"}, "server: required"),
        ("suite_mail", {"display_name": "X", "probe": MAIL["probe"], "server": "example_suite"}, "at least one"),
        ("dataplat", {"display_name": "D", "server": "dataplat", "probe": "mcp__dataplat__info",
                      "lookup": {"tools": ["mcp__dataplat__search"]}}, "when: required"),
        ("suite_mail", {**MAIL, "needs_user_input": ["Bad Name"]}, "needs_user_input"),
        ("suite_mail", {**MAIL, "required_in_types": ["sometimes"]}, "required_in_types"),
        ("suite_mail", {**MAIL, "notes": "token ghp_abcdefghijklmnop"}, "credential"),
        ("suite_mail", {**MAIL, "inbound": {"tools": MAIL["inbound"]["tools"], "when": "x"}}, "unknown field"),
    ],
)
def test_invalid_definitions_are_rejected_with_a_field_path(key, body, expected):
    c, issues = _parse(key, body)
    assert c is None
    assert expected in _messages(issues)


@pytest.mark.parametrize("text", ["Watch the task-list board.", "Use a risk-based triage.", "Ask about sk-8 sizing."])
def test_credential_guard_ignores_ordinary_words(text):
    c, issues = _parse("suite_mail", {**MAIL, "notes": text})
    assert issues == [], _messages(issues)
    assert c is not None and c.notes == text


def test_parse_file_keeps_valid_entries_when_another_is_broken():
    raw = {"schema_version": 1, "connectors": {"suite_mail": MAIL, "broken": {"display_name": "B"}}}
    loaded = cc.parse_file(raw, reserved=RESERVED, presets=PRESETS)
    assert set(loaded.connectors) == {"suite_mail"}
    assert any(i.path.startswith("connectors.broken") for i in loaded.issues)
    assert set(loaded.raw) == {"suite_mail", "broken"}


@pytest.mark.parametrize("raw", [["a list"], {"schema_version": 2, "connectors": {}}, {"schema_version": 1, "connectors": []}])
def test_parse_file_rejects_bad_top_level(raw):
    loaded = cc.parse_file(raw, reserved=RESERVED, presets=PRESETS)
    assert loaded.connectors == {}
    assert loaded.issues


def test_load_missing_file_is_empty(tmp_path: Path):
    assert cc.load(tmp_path, plugin_root=cc.default_plugin_root()).connectors == {}


def test_load_unparseable_file_reports_one_issue(tmp_path: Path):
    (tmp_path / cc.CUSTOM_FILE).write_text("connectors: [unclosed\n")
    loaded = cc.load(tmp_path, plugin_root=cc.default_plugin_root())
    assert loaded.connectors == {}
    assert loaded.issues and loaded.issues[0].path == cc.CUSTOM_FILE


def test_write_then_load_round_trips(tmp_path: Path):
    body = {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "inbound": {"tools": [{"bash": "tix list"}], "focus": "Changed tickets."},
    }
    cc.write(tmp_path, {"tickets": body})
    text = (tmp_path / cc.CUSTOM_FILE).read_text()
    assert text.startswith("# Custom connectors")
    assert yaml.safe_load(text) == {"schema_version": 1, "connectors": {"tickets": body}}
    assert set(cc.load(tmp_path, plugin_root=cc.default_plugin_root()).connectors) == {"tickets"}


def test_reserved_keys_cover_probes_aliases_and_phase_requires():
    reserved = cc.reserved_keys(cc.default_plugin_root())
    assert {"slack", "email", "gmail", "github", "claude_sessions"} <= reserved


def test_first_binary_skips_env_prefix_and_path():
    assert cc.first_binary("FOO=1 /usr/local/bin/tix list") == "tix"
    assert cc.first_binary("") is None


def test_bash_binaries_maps_probe_and_tools_and_skips_generic(tmp_path: Path):
    cc.write(
        tmp_path,
        {
            "tickets": {"display_name": "T", "probe": {"bash": "tix whoami"},
                        "inbound": {"tools": [{"bash": "tix list"}], "focus": "x"}},
            "webhook": {"display_name": "W", "probe": {"bash": "curl -sf https://example.com/health"},
                        "inbound": {"tools": [{"bash": "curl -s https://example.com/items"}], "focus": "x"}},
        },
    )
    assert cc.bash_binaries(tmp_path) == {"tix": "tickets"}


def test_bash_binaries_tolerates_garbage(tmp_path: Path):
    (tmp_path / cc.CUSTOM_FILE).write_text(":::\n")
    assert cc.bash_binaries(tmp_path) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_custom_connectors_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'custom_connectors'`.

- [ ] **Step 3: Write the implementation**

```python
"""Custom connectors — vault-owned definitions in ``<vault>/connectors.custom.yaml``.

A custom connector is any tool Scout does not ship a phase file for. Its entry
says which of Scout's three activities it serves (inbound, outbound, lookup),
which tools to call for each, and a sentence of guidance (or a preset that
supplies it). Assembly renders the shipped activity templates in
``phases/custom/`` once per enabled connector; the probe registry, the health
roster, and the connector-log hook derive their entries from the same file.

See docs/superpowers/specs/2026-10-02-custom-connectors-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CUSTOM_FILE = "connectors.custom.yaml"
SCHEMA_VERSION = 1
ACTIVITIES: tuple[str, ...] = ("inbound", "outbound", "lookup")
GUIDANCE_FIELD: dict[str, str] = {"inbound": "focus", "outbound": "focus", "lookup": "when"}
SLOT_TYPES = frozenset({"briefing", "consolidation", "dreaming", "research"})

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_INPUT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CREDENTIAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:xox[abp]-|ghp_|gho_|github_pat_|sk-|lin_api_)[A-Za-z0-9_-]{8,}"
    r"|Bearer\s+[A-Za-z0-9._~+/-]{12,}"
)
_FIELDS = frozenset(
    {"display_name", "server", "probe", "preset", "notes", "needs_user_input", "required_in_types", *ACTIVITIES}
)
# Binaries too generic to identify a connector: mapping them would relabel
# every unrelated call (curl, python3 …) in a session as that connector.
_GENERIC_BINARIES = frozenset(
    {"bash", "sh", "zsh", "env", "sudo", "cd", "curl", "wget", "python", "python3", "node", "npx", "uv", "uvx", "jq", "git"}
)
_HEADER = (
    "# Custom connectors — managed by `scoutctl connectors custom add/remove` and /scout-connect.\n"
    "# Hand edits are fine; `scoutctl connectors custom list` reports any problems.\n"
    "# Never put credentials here: inputs live in scout-config.yaml.\n"
)


@dataclass(frozen=True)
class ToolRef:
    kind: str  # "mcp" | "bash"
    value: str  # MCP tool name, or shell command

    @property
    def server(self) -> str | None:
        return self.value.split("__")[1] if self.kind == "mcp" else None

    @property
    def binary(self) -> str | None:
        return first_binary(self.value) if self.kind == "bash" else None


@dataclass(frozen=True)
class Activity:
    tools: tuple[ToolRef, ...]
    guidance: str  # `focus` (inbound/outbound) or `when` (lookup), preset-filled


@dataclass(frozen=True)
class CustomConnector:
    key: str
    display_name: str
    server: str | None
    probe: ToolRef
    preset: str | None
    activities: dict[str, Activity]
    notes: str = ""
    needs_user_input: tuple[str, ...] = ()
    required_in_types: tuple[str, ...] = ()

    @property
    def health_key(self) -> str:
        """The connector key connector_log.classify emits for this connector's calls."""
        return f"mcp:{self.server}" if self.server else self.key


@dataclass(frozen=True)
class Issue:
    path: str  # e.g. "connectors.outlook.inbound.tools[0]"
    message: str


@dataclass
class CustomLoad:
    connectors: dict[str, CustomConnector] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)  # every entry as written, valid or not


def first_binary(cmd: str) -> str | None:
    """Basename of the first command word, skipping ``FOO=bar`` prefixes."""
    tokens = cmd.split()
    idx = 0
    while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("-"):
        idx += 1
    return tokens[idx].rsplit("/", 1)[-1] if idx < len(tokens) else None


def _tool_ref(value: Any, path: str, issues: list[Issue]) -> ToolRef | None:
    if isinstance(value, str):
        parts = value.split("__")
        if value.startswith("mcp__") and len(parts) >= 3 and all(parts[1:]):
            return ToolRef("mcp", value)
        issues.append(
            Issue(path, f"{value!r} is not an MCP tool name (mcp__<server>__<tool>); write a command as {{bash: ...}}")
        )
        return None
    if isinstance(value, dict) and set(value) == {"bash"} and isinstance(value["bash"], str) and value["bash"].strip():
        return ToolRef("bash", value["bash"].strip())
    issues.append(Issue(path, 'must be an MCP tool name or {bash: "<command>"}'))
    return None


def _strings(value: Any) -> list[str]:
    """Every string anywhere inside a parsed YAML value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def parse_connector(
    key: str, body: Any, *, reserved: set[str], presets: dict[str, dict[str, str]]
) -> tuple[CustomConnector | None, list[Issue]]:
    """Validate one entry. Returns the connector only when there are no issues."""
    base = f"connectors.{key}"
    issues: list[Issue] = []
    if not _KEY_RE.match(key):
        issues.append(Issue(base, "key must be lowercase letters, digits and _ (2-32 chars, starting with a letter)"))
    if key in reserved:
        issues.append(Issue(base, f"{key!r} is a built-in connector key; pick another name"))
    if not isinstance(body, dict):
        issues.append(Issue(base, "must be a mapping"))
        return None, issues
    for unknown in sorted(set(body) - _FIELDS):
        issues.append(Issue(f"{base}.{unknown}", "unknown field"))
    if any(_CREDENTIAL_RE.search(s) for s in _strings(body)):
        issues.append(Issue(base, "looks like it contains a credential; ask for it via needs_user_input instead"))

    display_name = body.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        issues.append(Issue(f"{base}.display_name", "required"))
        display_name = ""

    probe: ToolRef | None = None
    if "probe" in body:
        probe = _tool_ref(body["probe"], f"{base}.probe", issues)
    else:
        issues.append(Issue(f"{base}.probe", "required"))

    preset = body.get("preset")
    if preset is not None and preset not in presets:
        known = ", ".join(sorted(presets)) or "none"
        issues.append(Issue(f"{base}.preset", f"unknown preset {preset!r} (known: {known})"))
        preset = None

    activities: dict[str, Activity] = {}
    for name in ACTIVITIES:
        if name not in body:
            continue
        apath = f"{base}.{name}"
        block = body[name]
        if not isinstance(block, dict):
            issues.append(Issue(apath, "must be a mapping with `tools`"))
            continue
        gfield = GUIDANCE_FIELD[name]
        for unknown in sorted(set(block) - {"tools", gfield}):
            issues.append(Issue(f"{apath}.{unknown}", f"unknown field (this activity takes `tools` and `{gfield}`)"))
        raw_tools = block.get("tools")
        if not isinstance(raw_tools, list) or not raw_tools:
            issues.append(Issue(f"{apath}.tools", "must be a non-empty list"))
            continue
        tools = [_tool_ref(t, f"{apath}.tools[{i}]", issues) for i, t in enumerate(raw_tools)]
        guidance = block.get(gfield)
        if guidance is None and preset is not None:
            guidance = presets[preset].get(name)
        if not isinstance(guidance, str) or not guidance.strip():
            issues.append(Issue(f"{apath}.{gfield}", "required (a sentence on what matters) unless a preset supplies it"))
            continue
        valid_tools = tuple(t for t in tools if t is not None)
        if len(valid_tools) == len(tools):
            activities[name] = Activity(valid_tools, guidance.strip())
    if not any(name in body for name in ACTIVITIES):
        issues.append(Issue(base, "declare at least one of inbound, outbound, lookup"))

    refs = [probe, *(t for a in activities.values() for t in a.tools)]
    mcp_refs = [r for r in refs if r is not None and r.kind == "mcp"]
    server = body.get("server")
    if mcp_refs:
        if not isinstance(server, str) or not server:
            issues.append(Issue(f"{base}.server", "required when any tool is an MCP tool"))
        else:
            for r in mcp_refs:
                if r.server != server:
                    issues.append(Issue(f"{base}.server", f"{r.value!r} belongs to server {r.server!r}, not {server!r}"))
    else:
        server = None

    needs = body.get("needs_user_input") or []
    if not isinstance(needs, list) or not all(isinstance(n, str) and _INPUT_RE.match(n) for n in needs):
        issues.append(Issue(f"{base}.needs_user_input", "must be a list of lowercase names like workspace_id"))
        needs = []

    types = body.get("required_in_types") or []
    if not isinstance(types, list) or not set(types) <= SLOT_TYPES:
        issues.append(Issue(f"{base}.required_in_types", f"must be a list drawn from {sorted(SLOT_TYPES)}"))
        types = []

    notes = body.get("notes") or ""
    if not isinstance(notes, str):
        issues.append(Issue(f"{base}.notes", "must be text"))
        notes = ""

    if issues or probe is None:
        return None, issues
    return (
        CustomConnector(
            key=key,
            display_name=display_name.strip(),
            server=server,
            probe=probe,
            preset=preset,
            activities=activities,
            notes=notes.strip(),
            needs_user_input=tuple(needs),
            required_in_types=tuple(types),
        ),
        [],
    )


def parse_file(raw: Any, *, reserved: set[str], presets: dict[str, dict[str, str]]) -> CustomLoad:
    """Validate a whole file. Invalid entries are dropped with issues; valid ones survive."""
    if raw is None:
        return CustomLoad()
    if not isinstance(raw, dict):
        return CustomLoad(issues=[Issue(CUSTOM_FILE, "must be a mapping with schema_version and connectors")])
    if raw.get("schema_version") != SCHEMA_VERSION:
        return CustomLoad(issues=[Issue(f"{CUSTOM_FILE}.schema_version", f"must be {SCHEMA_VERSION}")])
    conns = raw.get("connectors") or {}
    if not isinstance(conns, dict):
        return CustomLoad(issues=[Issue(f"{CUSTOM_FILE}.connectors", "must be a mapping of key → definition")])
    out = CustomLoad(raw={str(k): v for k, v in conns.items()})
    for key, body in out.raw.items():
        connector, issues = parse_connector(key, body, reserved=reserved, presets=presets)
        out.issues += issues
        if connector is not None:
            out.connectors[key] = connector
    return out


def default_plugin_root() -> Path:
    """Plugin root of the running engine (same derivation as connector_probes)."""
    import scout

    return Path(scout.__file__).parent.parent.parent


def load_presets(plugin_root: Path) -> dict[str, dict[str, str]]:
    """``phases/presets/<name>.yaml`` → {name: {summary, inbound, outbound, lookup}}. Bad files are skipped."""
    out: dict[str, dict[str, str]] = {}
    for path in sorted((plugin_root / "phases" / "presets").glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(raw, dict):
            out[path.stem] = {str(k): str(v).strip() for k, v in raw.items() if isinstance(v, str)}
    return out


def reserved_keys(plugin_root: Path) -> set[str]:
    """Keys a custom connector may not use: shipped probes, phase `requires:`, aliases."""
    from scout.scripts.connector_probes import CONNECTOR_KEY_ALIASES, load_registry
    from scout.scripts.phase_assembly import parse_phase_file

    keys: set[str] = set(CONNECTOR_KEY_ALIASES) | set(CONNECTOR_KEY_ALIASES.values())
    try:
        keys |= set(load_registry(plugin_root / "templates" / "connector-probes.yaml"))
    except (OSError, ValueError, yaml.YAMLError):
        pass
    for phase_file in (plugin_root / "phases").rglob("*.md"):
        try:
            keys |= {s.requires for s in parse_phase_file(phase_file) if s.requires}
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return keys


def load(vault: Path, *, plugin_root: Path | None = None) -> CustomLoad:
    """Read and validate ``<vault>/connectors.custom.yaml``. Never raises; a missing file is empty."""
    path = vault / CUSTOM_FILE
    if not path.exists():
        return CustomLoad()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        return CustomLoad(issues=[Issue(CUSTOM_FILE, f"could not be read: {e}")])
    root = plugin_root or default_plugin_root()
    return parse_file(raw, reserved=reserved_keys(root), presets=load_presets(root))


def dump(raw_connectors: dict[str, Any]) -> str:
    body = yaml.safe_dump(
        {"schema_version": SCHEMA_VERSION, "connectors": raw_connectors}, sort_keys=False, allow_unicode=True
    )
    return _HEADER + body


def write(vault: Path, raw_connectors: dict[str, Any]) -> None:
    """Atomically write the custom file (the only writer of it)."""
    path = vault / CUSTOM_FILE
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump(raw_connectors), encoding="utf-8")
    tmp.replace(path)


def bash_binaries(vault: Path) -> dict[str, str]:
    """Binary → connector key for every bash probe/tool. Unvalidated and never raises (hook path)."""
    try:
        raw = yaml.safe_load((vault / CUSTOM_FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    conns = raw.get("connectors") if isinstance(raw, dict) else None
    if not isinstance(conns, dict):
        return {}
    out: dict[str, str] = {}
    for key, body in conns.items():
        if not isinstance(body, dict):
            continue
        refs: list[Any] = [body.get("probe")]
        for name in ACTIVITIES:
            block = body.get(name)
            if isinstance(block, dict) and isinstance(block.get("tools"), list):
                refs += block["tools"]
        for ref in refs:
            if isinstance(ref, dict) and isinstance(ref.get("bash"), str):
                binary = first_binary(ref["bash"])
                if binary and binary not in _GENERIC_BINARIES and binary not in out:
                    out[binary] = str(key)
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_custom_connectors_model.py -q`
Expected: all PASS. (`test_reserved_keys_cover_probes_aliases_and_phase_requires` reads the real plugin tree.)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add engine/scout/custom_connectors.py engine/tests/unit/test_custom_connectors_model.py
git commit -m "feat(connectors): custom-connector model and validation (connectors.custom.yaml)"
```

---

### Task 2: Presets (mail, chat, calendar)

**Files:**
- Create: `phases/presets/mail.yaml`, `phases/presets/chat.yaml`, `phases/presets/calendar.yaml`
- Test: `engine/tests/unit/test_custom_presets.py`

**Interfaces:**
- Consumes: `cc.load_presets(plugin_root)` from Task 1.
- Produces: preset names `mail`, `chat`, `calendar`; each has `summary` plus text for the activities it covers (`mail`/`chat`: inbound, outbound, lookup; `calendar`: inbound, outbound).

- [ ] **Step 1: Write the failing test**

```python
"""Shipped presets: present, parseable, and free of provider-specific tool names."""

from __future__ import annotations

import re

from scout import custom_connectors as cc

PRESETS = cc.load_presets(cc.default_plugin_root())


def test_shipped_presets_exist_with_expected_activities():
    assert set(PRESETS) == {"mail", "chat", "calendar"}
    for name in ("mail", "chat"):
        assert {"summary", "inbound", "outbound", "lookup"} <= set(PRESETS[name])
    assert {"summary", "inbound", "outbound"} <= set(PRESETS["calendar"])


def test_presets_name_no_provider_tools():
    """Presets are provider-neutral: the connector's own tools are listed separately."""
    tool_like = re.compile(r"\b(gmail|gcal|slack)_[a-z_]+|mcp__")
    for name, preset in PRESETS.items():
        for activity, text in preset.items():
            assert not tool_like.search(text), f"{name}.{activity} names a provider tool"


def test_presets_do_not_contain_frontmatter_fences():
    for preset in PRESETS.values():
        for text in preset.values():
            assert not any(line.strip() == "---" for line in text.splitlines())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_custom_presets.py -q`
Expected: FAIL — `set() == {"mail", "chat", "calendar"}`.

- [ ] **Step 3: Write the preset files**

`phases/presets/mail.yaml` (distilled from `phases/connectors/email.md`):

```yaml
summary: Email — inbox and sent mail
inbound: |
  Look at mail received since the last run. Prioritize, in order: mail from people in `people.md` or frequent
  correspondents; action-oriented subjects (review, approve, question, deadline, urgent); replies to threads
  {{USER_NAME}} started; meeting invites and agenda shares.

  Do not surface cold outreach, vendor marketing, or unsolicited sales mail. Unknown sender plus a pitch or
  "partnership" language means skip. Mass mail from an unknown source means skip. If unsure, file under
  Watching at most, never To Do.

  Automated alerts (credential expiry, quota or spend thresholds, billing, maintenance notices) are Watching by
  default. Promote one only when something {{USER_NAME}} relies on breaks within days. The alert's own wording
  ("action required") is not urgency.
outbound: |
  Look at mail {{USER_NAME}} sent since the last run; it is strong evidence of completed work. A reply to a
  request means that request is likely handled. A message carrying a deliverable (attachment, link, proposal)
  means something was completed. A scheduling message means follow-up is in progress. A forward means
  delegation or escalation. Ignore auto-replies, subscription confirmations, and newsletters.
lookup: |
  Search mail when an action item or meeting references a thread, a sender, or a document that was emailed,
  and the context is not already in the knowledge base.
```

`phases/presets/chat.yaml` (distilled from `phases/connectors/slack.md`):

```yaml
summary: Team chat — direct messages, mentions, channels
inbound: |
  Look at messages to or mentioning {{USER_NAME}} since the last run: direct messages first, then
  @-mentions, then threads {{USER_NAME}} started or replied in, then decisions, blockers, and status updates
  in channels tied to projects in `knowledge-base/projects/`. Check thread replies, not just top-level
  messages; asks are often buried in threads.

  Note who sent each message, where, what is being asked, how time-sensitive it is, and whether {{USER_NAME}}
  already answered. Do not call the source quiet unless both the to-{{USER_NAME}} and the
  from-{{USER_NAME}} scans ran.
outbound: |
  Look at messages {{USER_NAME}} sent since the last run, including thread replies. A reply to a request means
  it is likely handled. A status update means the underlying work is done or in progress. A message accepting
  ownership ("I'll take it", "on it") with no later resolving message stays an open action item for
  {{USER_NAME}} until it is resolved or dropped.
lookup: |
  Search chat when an action item or meeting references a conversation, a decision, or a person whose recent
  context is not in the knowledge base.
```

`phases/presets/calendar.yaml` (distilled from `phases/connectors/calendar.md`):

```yaml
summary: Calendar — meetings and schedule changes
inbound: |
  List today's and yesterday's events. For each meeting note the attendees (cross-reference `people.md`), the
  topic, and whether it likely produced action items or needs prep. Flag meetings in the next working day that
  need preparation {{USER_NAME}} has not done.
outbound: |
  Look for events {{USER_NAME}} created, cancelled, or changed since the last run. A cancelled meeting often
  means its topic was resolved. A new event often means {{USER_NAME}} scheduled a follow-up or committed to
  something. Added attendees suggest widening involvement; an updated description suggests prep was done.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_custom_presets.py tests/unit/test_custom_connectors_model.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add phases/presets engine/tests/unit/test_custom_presets.py
git commit -m "feat(connectors): mail, chat and calendar presets for custom connectors"
```

---

### Task 3: Activity templates and the custom renderer

**Files:**
- Create: `phases/custom/inbound.md`, `phases/custom/outbound.md`, `phases/custom/lookup.md`
- Create: `engine/scout/scripts/custom_assembly.py`
- Test: `engine/tests/unit/test_custom_assembly.py`

**Interfaces:**
- Consumes: `CustomConnector`, `ACTIVITIES` (Task 1); `parse_phase_file`, `render_template`, `PhaseSection` (existing `scout.scripts.phase_assembly`).
- Produces:
  - `TARGET_MODES: dict[str, set[str]]` (`SKILL`/`DREAMING`/`RESEARCH`)
  - `@dataclass(frozen=True) CustomSection(connector_key: str, activity: str, template: Path, raw_body: str, rendered_body: str)`
  - `render_custom_sections(plugin_root: Path, kind: str, connectors: dict[str, CustomConnector], enabled: set[str], vars_: dict[str, str], inputs: dict[str, str]) -> list[CustomSection]`

- [ ] **Step 1: Write the failing tests**

```python
"""custom_assembly: rendering custom-connector sections from the shipped templates."""

from __future__ import annotations

from scout import custom_connectors as cc
from scout.scripts.custom_assembly import render_custom_sections
from scout.scripts.phase_assembly import parse_phase_file

ROOT = cc.default_plugin_root()
VARS = {"USER_NAME": "Alex", "INSTANCE_NAME": "Scout"}


def _connector(key: str, **body: object) -> cc.CustomConnector:
    presets = cc.load_presets(ROOT)
    c, issues = cc.parse_connector(key, body, reserved=set(), presets=presets)
    assert c is not None, issues
    return c


SUITE = _connector(
    "suite_mail",
    display_name="Mail suite",
    server="example_suite",
    probe="mcp__example_suite__list_folders",
    preset="mail",
    inbound={"tools": ["mcp__example_suite__search_messages"]},
    outbound={"tools": ["mcp__example_suite__search_messages"]},
    notes="The shared support mailbox is noise.",
)
DATAPLAT = _connector(
    "dataplat",
    display_name="Data platform",
    server="dataplat",
    probe="mcp__dataplat__info",
    lookup={"tools": ["mcp__dataplat__search"], "when": "A meeting mentions a table owned by {{USER_NAME}}."},
    needs_user_input=["project_id"],
    inbound={"tools": [{"bash": "dp jobs --project {{INPUT_PROJECT_ID}}"}], "focus": "Failed jobs."},
)
CONNECTORS = {"suite_mail": SUITE, "dataplat": DATAPLAT}


def test_templates_are_single_section_without_requires():
    for activity in cc.ACTIVITIES:
        sections = parse_phase_file(ROOT / "phases" / "custom" / f"{activity}.md")
        assert len(sections) == 1
        assert sections[0].requires is None


def test_skill_renders_inbound_and_outbound_with_preset_and_notes():
    out = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"suite_mail"}, VARS, {})
    assert [(s.connector_key, s.activity) for s in out] == [("suite_mail", "inbound"), ("suite_mail", "outbound")]
    inbound = out[0].rendered_body
    assert "## Mail suite Inbound Scan" in inbound
    assert "`mcp__example_suite__search_messages` — call as an MCP tool" in inbound
    assert "cold outreach" in inbound  # preset text
    assert "for Alex" in inbound  # {{USER_NAME}} rendered, including inside preset text
    assert "The shared support mailbox is noise." in inbound
    assert "{{" not in inbound


def test_disabled_connector_renders_nothing():
    assert render_custom_sections(ROOT, "SKILL", CONNECTORS, set(), VARS, {}) == []


def test_lookup_lands_in_skill_and_research_but_outbound_never_in_research():
    enabled = {"suite_mail", "dataplat"}
    research = render_custom_sections(ROOT, "RESEARCH", CONNECTORS, enabled, VARS, {})
    assert [(s.connector_key, s.activity) for s in research] == [("dataplat", "lookup")]
    skill = render_custom_sections(ROOT, "SKILL", CONNECTORS, enabled, VARS, {})
    assert ("dataplat", "lookup") in [(s.connector_key, s.activity) for s in skill]
    assert render_custom_sections(ROOT, "DREAMING", CONNECTORS, enabled, VARS, {}) == []


def test_sections_are_ordered_by_key_then_activity():
    skill = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"suite_mail", "dataplat"}, VARS, {})
    assert [(s.connector_key, s.activity) for s in skill] == [
        ("dataplat", "inbound"),
        ("dataplat", "lookup"),
        ("suite_mail", "inbound"),
        ("suite_mail", "outbound"),
    ]


def test_inputs_are_namespaced_per_connector_and_bash_tools_render_as_commands():
    skill = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"dataplat"}, VARS, {"dataplat__project_id": "p-42"})
    inbound = skill[0].rendered_body
    assert "`dp jobs --project p-42` — run with Bash" in inbound
    assert "{{INPUT_PROJECT_ID}}" in skill[0].raw_body  # raw keeps placeholders for backport


def test_raw_body_keeps_template_vars_and_rendered_has_none():
    out = render_custom_sections(ROOT, "SKILL", CONNECTORS, {"suite_mail"}, VARS, {})
    assert "{{USER_NAME}}" in out[0].raw_body
    assert "{{CONNECTOR_" not in out[0].raw_body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_custom_assembly.py -q`
Expected: FAIL — `ModuleNotFoundError: scout.scripts.custom_assembly`.

- [ ] **Step 3: Write the templates**

Template bodies must never contain a line that is exactly `---` (it is the frontmatter delimiter).

`phases/custom/inbound.md`:

```markdown
---
phase: connector
name: custom-inbound
slot: inbound-scan
mode: [consolidation, briefing]
---

## {{CONNECTOR_NAME}} Inbound Scan — What Came In for {{USER_NAME}}

Check {{CONNECTOR_NAME}} for anything new since the last run that may need {{USER_NAME}}'s action, or that changes what {{USER_NAME}} knows about a project or a person.

### Tools

{{CONNECTOR_TOOLS}}

Read only: never call a {{CONNECTOR_NAME}} tool that sends, creates, updates, or deletes anything.

### What Matters Here

{{CONNECTOR_GUIDANCE}}

{{CONNECTOR_NOTES}}

### How to Treat What You Find

- Every item is a *candidate* action item; it must pass the cross-check before it becomes a To Do.
- Cross-reference people against `people.md` and projects against `knowledge-base/projects/`.
- Urgency comes from deadlines and who is asking, not from tone. When unsure whether something needs {{USER_NAME}}, file it under **Watching**, never To Do.
- Record a link or identifier for every item you surface so {{USER_NAME}} can open the source.
- If a {{CONNECTOR_NAME}} tool call fails, say so in the sources footer (`{{CONNECTOR_NAME}}: unavailable — <error>`). Never report the source as quiet when you could not read it.
```

`phases/custom/outbound.md`:

```markdown
---
phase: connector
name: custom-outbound
slot: outbound-scan
mode: [consolidation]
---

## {{CONNECTOR_NAME}} Outbound Scan — What {{USER_NAME}} Did There

Check {{CONNECTOR_NAME}} for what {{USER_NAME}} did since the last run. What {{USER_NAME}} sent, closed, or changed is the strongest evidence that an action item is done or in progress.

### Tools

{{CONNECTOR_TOOLS}}

Read only: never call a {{CONNECTOR_NAME}} tool that sends, creates, updates, or deletes anything.

### What Matters Here

{{CONNECTOR_GUIDANCE}}

{{CONNECTOR_NOTES}}

### What to Record

For each thing {{USER_NAME}} did, note who or what it touched, what it was about, and what it implies:

- A reply to a request: that request is likely handled.
- A delivered artifact (file, link, decision): something was completed.
- A new commitment: a new action item for {{USER_NAME}}.
- A hand-off: track the delegation.

If a {{CONNECTOR_NAME}} tool call fails, say so in the sources footer instead of assuming nothing happened.
```

`phases/custom/lookup.md`:

```markdown
---
phase: connector
name: custom-lookup
slot: query
mode: [consolidation, briefing, research]
---

## {{CONNECTOR_NAME}} Lookup — When to Query It

{{CONNECTOR_NAME}} is a reference source. Do not scan it every run; query it when this comes up:

{{CONNECTOR_GUIDANCE}}

{{CONNECTOR_NOTES}}

### Tools

{{CONNECTOR_TOOLS}}

Read only: never call a {{CONNECTOR_NAME}} tool that sends, creates, updates, or deletes anything.

### Using the Answer

- Cite what you found (link or identifier) next to the item it informs.
- If the lookup fails, note that beside the item rather than guessing.
```

- [ ] **Step 4: Write the renderer**

`engine/scout/scripts/custom_assembly.py`:

```python
"""Render custom-connector sections from the shipped activity templates.

One template per activity lives in ``phases/custom/``. For each enabled custom
connector and each activity it declares, the template is filled in two passes:
first the ``{{CONNECTOR_*}}`` values (which may themselves contain ``{{USER_NAME}}``
from preset text), then the usual brain-file vars plus the connector's
``{{INPUT_*}}`` values. Shared by bootstrap assembly and phase backport so the
two can never drift.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from scout.custom_connectors import ACTIVITIES, CustomConnector, ToolRef
from scout.scripts.phase_assembly import PhaseSection, parse_phase_file, render_template

# Which modes each brain file is consumed by — mirrors bootstrap._assemble.
TARGET_MODES: dict[str, set[str]] = {
    "SKILL": {"briefing", "consolidation"},
    "DREAMING": {"dreaming"},
    "RESEARCH": {"research"},
}

_CONNECTOR_VAR_RE = re.compile(r"\{\{(CONNECTOR_[A-Z_]+)\}\}")


@dataclass(frozen=True)
class CustomSection:
    connector_key: str
    activity: str
    template: Path
    raw_body: str  # CONNECTOR_* filled, brain-file vars still as {{VARS}}
    rendered_body: str  # fully rendered — what lands in the brain file


def _load_templates(plugin_root: Path) -> dict[str, tuple[Path, PhaseSection]]:
    out: dict[str, tuple[Path, PhaseSection]] = {}
    for activity in ACTIVITIES:
        path = plugin_root / "phases" / "custom" / f"{activity}.md"
        try:
            sections = parse_phase_file(path)
        except (OSError, ValueError, yaml.YAMLError) as e:
            print(f"warning: custom-connector template {path} unusable: {e}", file=sys.stderr)
            continue
        if len(sections) != 1:
            print(f"warning: custom-connector template {path} must hold exactly one section", file=sys.stderr)
            continue
        out[activity] = (path, sections[0])
    return out


def _tool_lines(tools: tuple[ToolRef, ...]) -> str:
    how = {"mcp": "call as an MCP tool", "bash": "run with Bash"}
    return "\n".join(f"- `{t.value}` — {how[t.kind]}" for t in tools)


def _connector_values(c: CustomConnector, activity: str) -> dict[str, str]:
    act = c.activities[activity]
    return {
        "CONNECTOR_NAME": c.display_name,
        "CONNECTOR_TOOLS": _tool_lines(act.tools),
        "CONNECTOR_GUIDANCE": act.guidance,
        "CONNECTOR_NOTES": f"**Notes from {{{{USER_NAME}}}}:** {c.notes}" if c.notes else "",
    }


def _input_vars(c: CustomConnector, inputs: dict[str, str]) -> dict[str, str]:
    return {f"INPUT_{name.upper()}": inputs.get(f"{c.key}__{name}", "") for name in c.needs_user_input}


def render_custom_sections(
    plugin_root: Path,
    kind: str,
    connectors: dict[str, CustomConnector],
    enabled: set[str],
    vars_: dict[str, str],
    inputs: dict[str, str],
) -> list[CustomSection]:
    """Sections for every enabled connector, ordered by key then inbound → outbound → lookup."""
    active = {k: c for k, c in connectors.items() if k in enabled}
    if not active:
        return []
    modes = TARGET_MODES[kind]
    templates = _load_templates(plugin_root)
    out: list[CustomSection] = []
    for key in sorted(active):
        c = active[key]
        for activity in ACTIVITIES:
            if activity not in c.activities or activity not in templates:
                continue
            path, template = templates[activity]
            if template.mode and not set(template.mode) & modes:
                continue
            values = _connector_values(c, activity)
            raw = _CONNECTOR_VAR_RE.sub(lambda m, v=values: v.get(m.group(1), m.group(0)), template.body)
            rendered = render_template(raw, {**vars_, **_input_vars(c, inputs)})
            out.append(CustomSection(key, activity, path, raw, rendered))
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_custom_assembly.py tests/unit/test_connector_key_invariant.py tests/unit/test_phase_assembly.py -q`
Expected: all PASS (the key-invariant suite globs `phases/**/*.md` and must accept the new requires-less templates).

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add phases/custom engine/scout/scripts/custom_assembly.py engine/tests/unit/test_custom_assembly.py
git commit -m "feat(connectors): inbound/outbound/lookup templates and custom-section renderer"
```

---

### Task 4: Assembly, backport, and doctor read custom connectors

**Files:**
- Modify: `engine/scout/scripts/bootstrap.py` (`_assemble`, new `load_custom`)
- Modify: `engine/scout/scripts/phase_backport.py` (`RenderedSection`, `build_rendered_sections`, `plan_backport`)
- Modify: `engine/scout/cli.py` (the `phases backport` command's `build_rendered_sections` call, ~line 1590)
- Modify: `engine/scout/scripts/bootstrap_doctor.py` (`run_doctor`)
- Test: `engine/tests/unit/test_custom_connectors_assembly_wiring.py`

**Interfaces:**
- Consumes: `render_custom_sections`, `CustomSection` (Task 3); `cc.load`, `cc.write`, `cc.CUSTOM_FILE` (Task 1).
- Produces:
  - `bootstrap.load_custom(cfg: BootstrapConfig) -> dict[str, CustomConnector]` (prints each issue to stderr)
  - `bootstrap._assemble(cfg, kind, *, custom: dict[str, CustomConnector] | None = None) -> str` — `None` means "load from `cfg.vault`"
  - `phase_backport.RenderedSection.custom_key: str | None = None`
  - `phase_backport.build_rendered_sections(phases_root, kind, vars_, enabled_connectors, *, custom=None, inputs=None)`

- [ ] **Step 1: Write the failing tests**

```python
"""Custom connectors flow into assembled brain files, backport, and doctor."""

from __future__ import annotations

from pathlib import Path

from scout import custom_connectors as cc
from scout.scripts.bootstrap import BootstrapConfig, _assemble, _template_vars, install
from scout.scripts.bootstrap_doctor import run_doctor
from scout.scripts.phase_backport import build_rendered_sections, plan_backport

PLUGIN = Path(__file__).parent.parent.parent.parent

SUITE = {
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
}


def _cfg(vault: Path, enabled: set[str]) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors=enabled,
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
    )


def test_enabled_custom_connector_is_appended_after_shipped_sections(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": SUITE})
    skill = _assemble(_cfg(tmp_path, {"slack", "suite_mail"}), "SKILL")
    assert "## Mail suite Inbound Scan" in skill
    assert skill.index("## Slack Inbound Scan") < skill.index("## Mail suite Inbound Scan")


def test_custom_connector_not_in_enabled_is_not_rendered(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": SUITE})
    assert "Mail suite" not in _assemble(_cfg(tmp_path, {"slack"}), "SKILL")


def test_broken_custom_file_does_not_break_assembly(tmp_path: Path, capsys):
    (tmp_path / cc.CUSTOM_FILE).write_text("connectors: [unclosed\n")
    skill = _assemble(_cfg(tmp_path, {"slack"}), "SKILL")
    assert "## Slack Inbound Scan" in skill
    assert cc.CUSTOM_FILE in capsys.readouterr().err


def test_broken_entry_is_skipped_and_valid_one_still_renders(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": SUITE, "broken": {"display_name": "B"}})
    skill = _assemble(_cfg(tmp_path, {"suite_mail", "broken"}), "SKILL")
    assert "## Mail suite Inbound Scan" in skill


def test_backport_renders_the_same_custom_sections_and_never_writes_into_them(tmp_path: Path):
    cc.write(tmp_path, {"suite_mail": SUITE})
    cfg = _cfg(tmp_path, {"suite_mail"})
    custom = cc.load(tmp_path, plugin_root=PLUGIN).connectors
    vars_ = _template_vars(cfg)
    sections = build_rendered_sections(PLUGIN / "phases", "SKILL", vars_, cfg.enabled_connectors, custom=custom, inputs={})
    custom_sections = [s for s in sections if s.custom_key == "suite_mail"]
    assert custom_sections and custom_sections[0].rendered_body in _assemble(cfg, "SKILL")

    snapshot = _assemble(cfg, "SKILL")
    anchor = "Check Mail suite for anything new since the last run that may need Alex's action, or that changes what Alex knows about a project or a person."
    assert anchor in snapshot
    live = snapshot.replace(anchor, anchor + "\n\nAlso skim the archive folder.")
    results = plan_backport(snapshot, live, sections, vars_)
    assert [r.status for r in results] == ["needs-review"]
    assert "connectors.custom.yaml" in results[0].reason


def test_doctor_warns_on_custom_file_issues(tmp_path: Path):
    vault = tmp_path / "Scout"
    install(_cfg(vault, set()))
    cc.write(vault, {"broken": {"display_name": "B"}})
    report = run_doctor(vault=vault, check_jobs=False)
    assert any(cc.CUSTOM_FILE in w and "connectors.broken" in w for w in report.warnings)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_custom_connectors_assembly_wiring.py -q`
Expected: FAIL — no custom sections in `_assemble` output; `build_rendered_sections()` rejects `custom=`.

- [ ] **Step 3: Wire `_assemble`**

In `engine/scout/scripts/bootstrap.py`, add imports next to the existing ones:

```python
from scout import custom_connectors
from scout.scripts.custom_assembly import render_custom_sections
```

Add above `_assemble`:

```python
def load_custom(cfg: BootstrapConfig) -> dict[str, custom_connectors.CustomConnector]:
    """Valid custom connectors in ``cfg.vault``; each problem is warned on stderr and that entry skipped."""
    result = custom_connectors.load(cfg.vault, plugin_root=cfg.plugin_root)
    for issue in result.issues:
        print(f"warning: {custom_connectors.CUSTOM_FILE}: {issue.path}: {issue.message}", file=sys.stderr)
    return result.connectors
```

Change the `_assemble` signature and its end:

```python
def _assemble(
    cfg: BootstrapConfig, kind: str, *, custom: dict[str, custom_connectors.CustomConnector] | None = None
) -> str:
    """Assemble SKILL/DREAMING/RESEARCH from phase files, then append custom-connector sections.

    ``custom=None`` loads ``connectors.custom.yaml`` from the vault; the custom-change
    apply path passes explicit before/after sets. ``phases/custom/`` is never globbed
    here — its templates have no ``requires:`` gate and are rendered only per connector.
    """
```

and replace the final `return "\n\n".join(bodies)` with:

```python
    if custom is None:
        custom = load_custom(cfg)
    for section in render_custom_sections(
        cfg.plugin_root, kind, custom, cfg.enabled_connectors, vars_, cfg.connector_inputs
    ):
        bodies.append(section.rendered_body)
    return "\n\n".join(bodies)
```

- [ ] **Step 4: Wire backport**

In `engine/scout/scripts/phase_backport.py`:

```python
from scout.custom_connectors import CustomConnector
from scout.scripts.custom_assembly import render_custom_sections
```

Add the field to `RenderedSection`:

```python
    custom_key: str | None = None  # set for custom-connector sections; never back-ported
```

Change `build_rendered_sections`:

```python
def build_rendered_sections(
    phases_root: Path,
    kind: str,
    vars_: dict[str, str],
    enabled_connectors: set[str],
    *,
    custom: dict[str, CustomConnector] | None = None,
    inputs: dict[str, str] | None = None,
) -> list[RenderedSection]:
```

and before `return out`:

```python
    for cs in render_custom_sections(phases_root.parent, kind, custom or {}, enabled_connectors, vars_, inputs or {}):
        out.append(
            RenderedSection(
                phase_file=cs.template,
                section_name=f"custom:{cs.connector_key}:{cs.activity}",
                raw_body=cs.raw_body,
                rendered_body=cs.rendered_body,
                custom_key=cs.connector_key,
            )
        )
```

In `plan_backport`, directly after `sec = matches[0]`:

```python
        if sec.custom_key is not None:
            # The template is shared by every connector and every user: an edit
            # inside a custom section belongs in that connector's entry instead.
            results.append(
                HunkResult(
                    "needs-review",
                    hunk.added,
                    phase_file=sec.phase_file,
                    section_name=sec.section_name,
                    anchor=hunk.anchor,
                    reason=(
                        f"inside custom connector {sec.custom_key!r} — change its entry in "
                        f"connectors.custom.yaml (or run /scout-connect {sec.custom_key}) instead"
                    ),
                )
            )
            continue
```

In `engine/scout/cli.py`, in the `phases backport` command, change

```python
            sections = build_rendered_sections(phases_root, k, vars_, cfg.enabled_connectors)
```

to

```python
            sections = build_rendered_sections(
                phases_root, k, vars_, cfg.enabled_connectors, custom=load_custom(cfg), inputs=cfg.connector_inputs
            )
```

and import `load_custom` from `scout.scripts.bootstrap` alongside that command's other bootstrap imports.

- [ ] **Step 5: Wire doctor**

In `engine/scout/scripts/bootstrap_doctor.py`, add:

```python
def _check_custom_connectors(*, vault: Path) -> list[str]:
    """One warning per problem in connectors.custom.yaml; that connector is skipped until fixed."""
    from scout.custom_connectors import CUSTOM_FILE, load

    return [f"{CUSTOM_FILE}: {i.path}: {i.message} (skipped until fixed)" for i in load(vault).issues]
```

and in `run_doctor`, after the hand-edit-backup loop:

```python
    warnings.extend(_check_custom_connectors(vault=vault))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_custom_connectors_assembly_wiring.py tests/unit/test_phase_backport.py tests/unit/test_bootstrap_install.py tests/unit/test_bootstrap_upgrade.py tests/unit/test_bootstrap_doctor.py -q`
Expected: all PASS.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add engine/scout/scripts/bootstrap.py engine/scout/scripts/phase_backport.py engine/scout/scripts/bootstrap_doctor.py engine/scout/cli.py engine/tests/unit/test_custom_connectors_assembly_wiring.py
git commit -m "feat(connectors): assemble custom-connector sections; backport and doctor know them"
```

---

### Task 5: Apply a custom-connector change to a live vault

**Files:**
- Modify: `engine/scout/scripts/bootstrap.py`
- Test: `engine/tests/unit/test_custom_connectors_apply.py`

**Interfaces:**
- Consumes: `_assemble(..., custom=...)`, `load_custom` (Task 4); `three_way_merge`, `_dump_keeping_comments`, `_atomic_write` (existing).
- Produces:
  - `config_from_vault(vault: Path, *, plugin_root: Path, plugin_version: str) -> BootstrapConfig`
  - `write_connector_config(vault: Path, *, enabled: set[str], inputs: dict[str, str]) -> None`
  - `@dataclass CustomApplyResult(status: str, updated: list[str], sidecars: list[str])` — status `applied | unchanged | deferred | conflict`
  - `apply_custom_change(before: BootstrapConfig, after: BootstrapConfig, *, custom_before: dict[str, CustomConnector], custom_after: dict[str, CustomConnector]) -> CustomApplyResult`

- [ ] **Step 1: Write the failing tests**

```python
"""apply_custom_change: add/remove reaches live brain files without a review sidecar."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

from scout import custom_connectors as cc
from scout.scripts.bootstrap import (
    BootstrapConfig,
    _assemble,
    apply_custom_change,
    config_from_vault,
    install,
    write_connector_config,
)

PLUGIN = Path(__file__).parent.parent.parent.parent
SUITE = {
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
}


def _installed(tmp_path: Path) -> BootstrapConfig:
    vault = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=vault,
            plugin_root=PLUGIN,
            instance_name="TestScout",
            instance_name_lower="testscout",
            user_name="Alex",
            user_email="alex@example.com",
            timezone="America/New_York",
            platform="macos",
            plugin_version="0.0.0",
            enabled_connectors={"slack"},
            connector_inputs={},
            skip_jobs=True,
            skip_claude=True,
        )
    )
    return config_from_vault(vault, plugin_root=PLUGIN, plugin_version="0.0.0")


def _add_suite(before: BootstrapConfig):
    custom_after = cc.parse_file(
        {"schema_version": 1, "connectors": {"suite_mail": SUITE}},
        reserved=set(),
        presets=cc.load_presets(PLUGIN),
    ).connectors
    after = dataclasses.replace(before, enabled_connectors=before.enabled_connectors | {"suite_mail"})
    return apply_custom_change(before, after, custom_before={}, custom_after=custom_after), after, custom_after


def test_config_from_vault_reads_what_install_wrote(tmp_path: Path):
    cfg = _installed(tmp_path)
    assert cfg.user_name == "Alex"
    assert cfg.enabled_connectors == {"slack"}


def test_add_on_clean_vault_updates_live_and_snapshot(tmp_path: Path):
    before = _installed(tmp_path)
    result, after, custom_after = _add_suite(before)
    assert result.status == "applied"
    assert result.updated == ["SKILL.md"]
    live = (before.vault / "SKILL.md").read_text()
    assert "## Mail suite Inbound Scan" in live
    snap = (before.vault / ".scout-state" / "last-assembled" / "SKILL.md").read_text()
    assert snap == _assemble(after, "SKILL", custom=custom_after)
    assert not (before.vault / "SKILL.md.proposed-merge").exists()


def test_add_keeps_the_users_own_edits(tmp_path: Path):
    before = _installed(tmp_path)
    live_path = before.vault / "SKILL.md"
    lines = live_path.read_text().splitlines()
    lines.insert(2, "Local rule: keep the briefing under one screen.")
    live_path.write_text("\n".join(lines) + "\n")
    result, _, _ = _add_suite(before)
    assert result.status == "applied"
    text = live_path.read_text()
    assert "Local rule: keep the briefing under one screen." in text
    assert "## Mail suite Inbound Scan" in text


def test_plugin_drift_defers_and_writes_nothing(tmp_path: Path):
    before = _installed(tmp_path)
    snap = before.vault / ".scout-state" / "last-assembled" / "SKILL.md"
    snap.write_text(snap.read_text() + "\nplugin changed underneath\n")
    live_before = (before.vault / "SKILL.md").read_text()
    result, _, _ = _add_suite(before)
    assert result.status == "deferred"
    assert (before.vault / "SKILL.md").read_text() == live_before
    assert not (before.vault / "SKILL.md.proposed-merge").exists()


def test_overlapping_user_edit_goes_to_sidecar(tmp_path: Path):
    before = _installed(tmp_path)
    live_path = before.vault / "SKILL.md"
    live_path.write_text(live_path.read_text() + "\nMy own footer.\n")
    result, _, _ = _add_suite(before)
    assert result.status == "conflict"
    assert result.sidecars == ["SKILL.md.proposed-merge"]
    assert "My own footer." in live_path.read_text()
    assert "Mail suite" not in live_path.read_text()


def test_no_change_is_unchanged(tmp_path: Path):
    before = _installed(tmp_path)
    result = apply_custom_change(before, before, custom_before={}, custom_after={})
    assert result.status == "unchanged"


def test_write_connector_config_keeps_comments(tmp_path: Path):
    before = _installed(tmp_path)
    path = before.vault / "scout-config.yaml"
    path.write_text("# my budget note\n" + path.read_text())
    write_connector_config(before.vault, enabled={"slack", "suite_mail"}, inputs={"suite_mail__box": "team"})
    text = path.read_text()
    assert text.startswith("# my budget note\n")
    data = yaml.safe_load(text)
    assert data["connectors"]["enabled"] == ["slack", "suite_mail"]
    assert data["connectors"]["inputs"] == {"suite_mail__box": "team"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_custom_connectors_apply.py -q`
Expected: FAIL — `ImportError: cannot import name 'apply_custom_change'`.

- [ ] **Step 3: Implement**

Add to `engine/scout/scripts/bootstrap.py` (after `_stage_merge_files_upgrade`):

```python
def config_from_vault(vault: Path, *, plugin_root: Path, plugin_version: str) -> BootstrapConfig:
    """BootstrapConfig for an existing vault, read from its scout-config.yaml (as `bootstrap upgrade` does)."""
    existing = yaml.safe_load((vault / "scout-config.yaml").read_text(encoding="utf-8")) or {}
    instance = existing.get("instance") or {}
    user = existing.get("user") or {}
    connectors = existing.get("connectors") or {}
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin_root,
        instance_name=instance.get("name", "Scout"),
        instance_name_lower=instance.get("name_lower", "scout"),
        user_name=user.get("name", ""),
        user_email=user.get("email", ""),
        timezone=existing.get("timezone", "America/New_York"),
        platform=existing.get("platform", "macos"),
        plugin_version=plugin_version,
        enabled_connectors=set(connectors.get("enabled") or []),
        connector_inputs=dict(connectors.get("inputs") or {}),
    )


def write_connector_config(vault: Path, *, enabled: set[str], inputs: dict[str, str]) -> None:
    """Rewrite connectors.enabled / connectors.inputs, keeping the file's comments (#251)."""
    path = vault / "scout-config.yaml"
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    connectors = data.setdefault("connectors", {})
    connectors["enabled"] = sorted(enabled)
    connectors["inputs"] = dict(inputs)
    _atomic_write(path, _dump_keeping_comments(text, data))


@dataclass
class CustomApplyResult:
    status: str  # "applied" | "unchanged" | "deferred" | "conflict"
    updated: list[str] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)


def apply_custom_change(
    before: BootstrapConfig,
    after: BootstrapConfig,
    *,
    custom_before: dict[str, custom_connectors.CustomConnector],
    custom_after: dict[str, custom_connectors.CustomConnector],
) -> CustomApplyResult:
    """Apply a custom-connector add/remove to the live brain files (custom-connectors spec §2).

    The caller holds the vault lock. ``before`` must re-assemble to the recorded
    snapshot for every changed brain file — that proves the plugin has not
    drifted, so the only difference is the custom delta, which is 3-way merged
    into live (keeping the user's own edits). On drift nothing is written and
    the change waits for /scout-update (a sidecar would block that very
    upgrade). A merge conflict goes to the usual ``.proposed-merge`` sidecar.
    """
    vault = after.vault
    snapshot_dir = vault / ".scout-state" / "last-assembled"
    plans: list[tuple[str, str, Path, Path]] = []
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        old = _assemble(before, kind, custom=custom_before)
        new = _assemble(after, kind, custom=custom_after)
        if old == new:
            continue
        live, snap = vault / f"{kind}.md", snapshot_dir / f"{kind}.md"
        if not live.exists() or not snap.exists() or snap.read_text(encoding="utf-8") != old:
            return CustomApplyResult(status="deferred")
        plans.append((old, new, live, snap))
    if not plans:
        return CustomApplyResult(status="unchanged")
    result = CustomApplyResult(status="applied")
    for old, new, live, snap in plans:
        merged = three_way_merge(base=old, ours=new, theirs=live.read_text(encoding="utf-8"))
        if merged.conflicts:
            sidecar = live.with_name(f"{live.name}.proposed-merge")
            _atomic_write(sidecar, merged.content)
            result.sidecars.append(sidecar.name)
            result.status = "conflict"
        else:
            _atomic_write(live, merged.content)
            _atomic_write(snap, new)
            result.updated.append(live.name)
    return result
```

- [ ] **Step 4: Use `config_from_vault` in the upgrade CLI**

In `engine/scout/cli.py` `cli_bootstrap_upgrade`, replace the hand-built `BootstrapConfig(...)` (the block reading `existing.get("instance", {})` … `connector_inputs=…`) with:

```python
        cfg = config_from_vault(vault, plugin_root=Path(__file__).parent.parent.parent, plugin_version=__version__)
        cfg.skip_jobs = skip_jobs
        cfg.skip_claude = skip_claude
```

keeping the existing malformed-YAML guard above it (it still `safe_load`s first and exits with `ConfigError.exit_code`), and importing `config_from_vault` from `scout.scripts.bootstrap`. Note `__post_init__` normalization still runs because `config_from_vault` constructs a fresh `BootstrapConfig`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_custom_connectors_apply.py tests/unit/test_bootstrap_upgrade.py tests/unit/test_cli_surface.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add engine/scout/scripts/bootstrap.py engine/scout/cli.py engine/tests/unit/test_custom_connectors_apply.py
git commit -m "feat(connectors): apply a custom-connector change to live brain files via 3-way merge"
```

---

### Task 6: `scoutctl connectors custom` and `connectors presets`

**Files:**
- Create: `engine/scout/scripts/custom_connector_ops.py`
- Modify: `engine/scout/cli.py` (`_register_connectors`)
- Test: `engine/tests/unit/test_custom_connector_ops.py`, `engine/tests/unit/test_cli_connectors_custom.py`

**Interfaces:**
- Consumes: Task 1 (`cc.*`), Task 3 (`render_custom_sections`), Task 5 (`config_from_vault`, `write_connector_config`, `apply_custom_change`), existing `_refuse_pending_sidecars`, `_template_vars`, `acquire_lock_with_wait`, `release_lock`, `LockBusyError`.
- Produces:
  - `@dataclass Outcome(status, key="", issues=[], message="", updated=[], sidecars=[], sections=[])` with `exit_code` property and `to_json() -> dict`
  - `run_bash_probe(command: str) -> tuple[bool, str]`
  - `validate(raw_def: Any, *, plugin_root: Path) -> Outcome`
  - `add(vault, raw_def, *, plugin_root, plugin_version, inputs: dict[str, str], dry_run=False, unverified=False, probe_runner=run_bash_probe) -> Outcome`
  - `remove(vault, key, *, plugin_root, plugin_version) -> Outcome`
  - `list_custom(vault, *, plugin_root) -> dict[str, Any]`
  - `presets_json(plugin_root) -> dict[str, Any]`
  - CLI: `scoutctl connectors custom {add,remove,validate,list}`, `scoutctl connectors presets`

- [ ] **Step 1: Write the failing ops tests**

`engine/tests/unit/test_custom_connector_ops.py`:

```python
"""custom_connector_ops: the JSON + exit-code contract behind `scoutctl connectors custom`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout import custom_connectors as cc
from scout.scripts import custom_connector_ops as ops
from scout.scripts.bootstrap import BootstrapConfig, install

PLUGIN = Path(__file__).parent.parent.parent.parent
SUITE = {
    "key": "suite_mail",
    "display_name": "Mail suite",
    "server": "example_suite",
    "probe": "mcp__example_suite__list_folders",
    "preset": "mail",
    "inbound": {"tools": ["mcp__example_suite__search_messages"]},
}
TICKETS = {
    "key": "tickets",
    "display_name": "Tickets",
    "probe": {"bash": "tix whoami"},
    "needs_user_input": ["team"],
    "inbound": {"tools": [{"bash": "tix list --team {{INPUT_TEAM}}"}], "focus": "Changed tickets."},
}


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=v,
            plugin_root=PLUGIN,
            instance_name="TestScout",
            instance_name_lower="testscout",
            user_name="Alex",
            user_email="alex@example.com",
            timezone="America/New_York",
            platform="macos",
            plugin_version="0.0.0",
            enabled_connectors={"slack"},
            connector_inputs={},
            skip_jobs=True,
            skip_claude=True,
        )
    )
    return v


def _add(vault: Path, definition: dict, **kw):
    kw.setdefault("inputs", {})
    return ops.add(vault, dict(definition), plugin_root=PLUGIN, plugin_version="0.0.0", **kw)


def _config(vault: Path) -> dict:
    return yaml.safe_load((vault / "scout-config.yaml").read_text())


def test_add_applies_and_records_everything(vault: Path):
    out = _add(vault, SUITE)
    assert (out.status, out.exit_code) == ("applied", 0)
    assert "## Mail suite Inbound Scan" in (vault / "SKILL.md").read_text()
    assert "suite_mail" in _config(vault)["connectors"]["enabled"]
    saved = yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"]["suite_mail"]
    assert "key" not in saved and saved["display_name"] == "Mail suite"


def test_re_add_replaces_the_entry_in_place(vault: Path):
    _add(vault, SUITE)
    changed = {**SUITE, "inbound": {"tools": SUITE["inbound"]["tools"], "focus": "Only the shared queue."}}
    out = _add(vault, changed)
    assert out.status == "applied"
    skill = (vault / "SKILL.md").read_text()
    assert skill.count("## Mail suite Inbound Scan") == 1
    assert "Only the shared queue." in skill
    assert list(yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"]) == ["suite_mail"]


def test_invalid_definition_exits_2_and_writes_nothing(vault: Path):
    out = _add(vault, {**SUITE, "server": "other_suite"})
    assert (out.status, out.exit_code) == ("invalid", 2)
    assert out.to_json()["issues"][0]["path"] == "connectors.suite_mail.server"
    assert not (vault / cc.CUSTOM_FILE).exists()


def test_missing_key_is_invalid(vault: Path):
    out = _add(vault, {k: v for k, v in SUITE.items() if k != "key"})
    assert out.status == "invalid" and out.issues[0].path == "key"


def test_missing_input_is_invalid_and_inputs_are_namespaced(vault: Path):
    assert _add(vault, TICKETS, probe_runner=lambda cmd: (True, "")).status == "invalid"
    out = _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=lambda cmd: (True, ""))
    assert out.status == "applied"
    assert _config(vault)["connectors"]["inputs"]["tickets__team"] == "ops"
    assert "`tix list --team ops` — run with Bash" in (vault / "SKILL.md").read_text()


def test_unknown_input_name_is_invalid(vault: Path):
    out = _add(vault, TICKETS, inputs={"team": "ops", "colour": "x"}, probe_runner=lambda cmd: (True, ""))
    assert out.status == "invalid"


def test_failed_bash_probe_exits_2_unless_unverified(vault: Path):
    failing = lambda cmd: (False, "tix: command not found")  # noqa: E731
    out = _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=failing)
    assert (out.status, out.exit_code) == ("probe-failed", 2)
    assert "command not found" in out.issues[0].message
    assert _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=failing, unverified=True).status == "applied"


def test_dry_run_returns_sections_and_writes_nothing(vault: Path):
    skill_before = (vault / "SKILL.md").read_text()
    out = _add(vault, SUITE, dry_run=True)
    assert (out.status, out.exit_code) == ("dry-run", 0)
    assert out.sections[0]["target"] == "SKILL.md"
    assert "## Mail suite Inbound Scan" in out.sections[0]["body"]
    assert (vault / "SKILL.md").read_text() == skill_before
    assert not (vault / cc.CUSTOM_FILE).exists()


def test_pending_sidecar_blocks_add(vault: Path):
    (vault / "SKILL.md.proposed-merge").write_text("pending")
    out = _add(vault, SUITE)
    assert (out.status, out.exit_code) == ("error", 1)
    assert "proposed-merge" in out.message


def test_remove_takes_out_sections_entry_enabled_and_inputs(vault: Path):
    _add(vault, TICKETS, inputs={"team": "ops"}, probe_runner=lambda cmd: (True, ""))
    out = ops.remove(vault, "tickets", plugin_root=PLUGIN, plugin_version="0.0.0")
    assert (out.status, out.exit_code) == ("applied", 0)
    assert "Tickets Inbound Scan" not in (vault / "SKILL.md").read_text()
    cfg = _config(vault)["connectors"]
    assert "tickets" not in cfg["enabled"] and "tickets__team" not in cfg["inputs"]
    assert yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"] == {}


def test_remove_unknown_key_is_invalid(vault: Path):
    assert ops.remove(vault, "nope", plugin_root=PLUGIN, plugin_version="0.0.0").exit_code == 2


def test_add_without_vault_is_an_error(tmp_path: Path):
    out = ops.add(tmp_path / "missing", dict(SUITE), plugin_root=PLUGIN, plugin_version="0.0.0", inputs={})
    assert (out.status, out.exit_code) == ("error", 1)


def test_list_custom_reports_definitions_and_issues(vault: Path):
    _add(vault, SUITE)
    raw = yaml.safe_load((vault / cc.CUSTOM_FILE).read_text())["connectors"]
    raw["broken"] = {"display_name": "B"}
    cc.write(vault, raw)
    listing = ops.list_custom(vault, plugin_root=PLUGIN)
    row = listing["connectors"][0]
    assert row == {
        "key": "suite_mail",
        "display_name": "Mail suite",
        "enabled": True,
        "server": "example_suite",
        "health_key": "mcp:example_suite",
        "preset": "mail",
        "activities": ["inbound"],
    }
    assert any(i["path"].startswith("connectors.broken") for i in listing["issues"])


def test_presets_json_lists_shipped_presets():
    assert set(ops.presets_json(PLUGIN)["presets"]) == {"mail", "chat", "calendar"}
```

- [ ] **Step 2: Run the ops tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_custom_connector_ops.py -q`
Expected: FAIL — `ImportError: cannot import name 'custom_connector_ops'`.

- [ ] **Step 3: Implement the ops module**

`engine/scout/scripts/custom_connector_ops.py`:

```python
"""add / remove / validate / list for custom connectors — the logic behind `scoutctl connectors custom`.

Kept out of cli.py so the contract the desktop app and the wizards call (one JSON
object + stable exit codes) is unit-testable without Typer. See
docs/superpowers/specs/2026-10-02-custom-connectors-design.md §3.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scout import custom_connectors as cc
from scout.scripts.bootstrap import (
    BootstrapConfig,
    _refuse_pending_sidecars,
    _template_vars,
    apply_custom_change,
    config_from_vault,
    write_connector_config,
)
from scout.scripts.bootstrap_lock import LockBusyError, acquire_lock_with_wait, release_lock
from scout.scripts.custom_assembly import render_custom_sections

_EXIT_CODES = {
    "applied": 0,
    "unchanged": 0,
    "valid": 0,
    "dry-run": 0,
    "error": 1,
    "invalid": 2,
    "probe-failed": 2,
    "deferred": 3,
    "conflict": 3,
}

_MESSAGES = {
    "applied": "Live: the next scheduled run reads it.",
    "unchanged": "Already up to date.",
    "deferred": "Saved. The plugin changed since your last update, so this takes effect after /scout-update.",
    "conflict": (
        "Saved, but your own edits to the brain file overlap this change. Resolve the .proposed-merge file "
        "listed in `sidecars`, then move it over the original."
    ),
}

ProbeRunner = Callable[[str], tuple[bool, str]]


@dataclass
class Outcome:
    status: str
    key: str = ""
    issues: list[cc.Issue] = field(default_factory=list)
    message: str = ""
    updated: list[str] = field(default_factory=list)
    sidecars: list[str] = field(default_factory=list)
    sections: list[dict[str, str]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES[self.status]

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "key": self.key,
            "message": self.message,
            "issues": [{"path": i.path, "message": i.message} for i in self.issues],
            "updated": self.updated,
            "sidecars": self.sidecars,
            "sections": self.sections,
        }


def run_bash_probe(command: str) -> tuple[bool, str]:
    """Run a bash probe; exit 0 means connected."""
    try:
        proc = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip()[:300]


def _split(raw_def: Any) -> tuple[str, dict[str, Any], list[cc.Issue]]:
    if not isinstance(raw_def, dict):
        return "", {}, [cc.Issue("definition", "must be a mapping with a `key` field")]
    body = dict(raw_def)
    key = body.pop("key", None)
    if not isinstance(key, str) or not key:
        return "", body, [cc.Issue("key", "required: the connector key, e.g. outlook")]
    return key, body, []


def validate(raw_def: Any, *, plugin_root: Path) -> Outcome:
    key, body, issues = _split(raw_def)
    if not issues:
        _, issues = cc.parse_connector(
            key, body, reserved=cc.reserved_keys(plugin_root), presets=cc.load_presets(plugin_root)
        )
    return Outcome("invalid" if issues else "valid", key, issues)


def _no_vault(vault: Path) -> Outcome | None:
    if (vault / "scout-config.yaml").exists():
        return None
    return Outcome("error", message=f"no vault at {vault} — run /scout-setup first")


def _commit(
    key: str,
    before: BootstrapConfig,
    after: BootstrapConfig,
    custom_before: dict[str, cc.CustomConnector],
    custom_after: dict[str, cc.CustomConnector],
    raw_after: dict[str, Any],
) -> Outcome:
    """Apply first, then write the definition and config, all under the session lock."""
    vault = after.vault
    lock = vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        acquire_lock_with_wait(lock)
    except LockBusyError:
        return Outcome("error", key, message="A Scout session is running; try again when it finishes.")
    try:
        try:
            _refuse_pending_sidecars(vault)
        except RuntimeError as e:
            return Outcome("error", key, message=str(e))
        result = apply_custom_change(before, after, custom_before=custom_before, custom_after=custom_after)
        cc.write(vault, raw_after)
        write_connector_config(vault, enabled=after.enabled_connectors, inputs=after.connector_inputs)
    finally:
        release_lock(lock)
    return Outcome(
        result.status, key, message=_MESSAGES[result.status], updated=result.updated, sidecars=result.sidecars
    )


def add(
    vault: Path,
    raw_def: Any,
    *,
    plugin_root: Path,
    plugin_version: str,
    inputs: dict[str, str],
    dry_run: bool = False,
    unverified: bool = False,
    probe_runner: ProbeRunner = run_bash_probe,
) -> Outcome:
    if (missing := _no_vault(vault)) is not None:
        return missing
    key, body, issues = _split(raw_def)
    if issues:
        return Outcome("invalid", key, issues)
    connector, issues = cc.parse_connector(
        key, body, reserved=cc.reserved_keys(plugin_root), presets=cc.load_presets(plugin_root)
    )
    before = config_from_vault(vault, plugin_root=plugin_root, plugin_version=plugin_version)
    new_inputs = dict(before.connector_inputs)
    for name, value in inputs.items():
        new_inputs[f"{key}__{name}"] = value
    if connector is not None:
        for name in sorted(set(inputs) - set(connector.needs_user_input)):
            issues.append(cc.Issue(f"connectors.{key}.needs_user_input", f"{name!r} is not an input this connector declares"))
        for name in connector.needs_user_input:
            if not new_inputs.get(f"{key}__{name}"):
                issues.append(cc.Issue(f"connectors.{key}.needs_user_input", f"no value for {name!r}; pass --input {name}=<value>"))
    if issues or connector is None:
        return Outcome("invalid", key, issues)
    if connector.probe.kind == "bash" and not unverified:
        ok, detail = probe_runner(connector.probe.value)
        if not ok:
            message = f"`{connector.probe.value}` failed: {detail or 'non-zero exit'}"
            return Outcome("probe-failed", key, [cc.Issue(f"connectors.{key}.probe", message)])

    current = cc.load(vault, plugin_root=plugin_root)
    after = dataclasses.replace(
        before, enabled_connectors=before.enabled_connectors | {key}, connector_inputs=new_inputs
    )
    if dry_run:
        sections = [
            {"target": f"{kind}.md", "activity": s.activity, "body": s.rendered_body}
            for kind in ("SKILL", "RESEARCH")
            for s in render_custom_sections(plugin_root, kind, {key: connector}, {key}, _template_vars(after), new_inputs)
        ]
        return Outcome("dry-run", key, sections=sections)
    return _commit(
        key,
        before,
        after,
        current.connectors,
        {**current.connectors, key: connector},
        {**current.raw, key: body},
    )


def remove(vault: Path, key: str, *, plugin_root: Path, plugin_version: str) -> Outcome:
    if (missing := _no_vault(vault)) is not None:
        return missing
    current = cc.load(vault, plugin_root=plugin_root)
    if key not in current.raw:
        return Outcome("invalid", key, [cc.Issue(f"connectors.{key}", "no such custom connector")])
    before = config_from_vault(vault, plugin_root=plugin_root, plugin_version=plugin_version)
    after = dataclasses.replace(
        before,
        enabled_connectors=before.enabled_connectors - {key},
        connector_inputs={k: v for k, v in before.connector_inputs.items() if not k.startswith(f"{key}__")},
    )
    return _commit(
        key,
        before,
        after,
        current.connectors,
        {k: c for k, c in current.connectors.items() if k != key},
        {k: v for k, v in current.raw.items() if k != key},
    )


def list_custom(vault: Path, *, plugin_root: Path) -> dict[str, Any]:
    current = cc.load(vault, plugin_root=plugin_root)
    enabled: set[str] = set()
    if (vault / "scout-config.yaml").exists():
        enabled = config_from_vault(vault, plugin_root=plugin_root, plugin_version="").enabled_connectors
    rows = [
        {
            "key": c.key,
            "display_name": c.display_name,
            "enabled": c.key in enabled,
            "server": c.server,
            "health_key": c.health_key,
            "preset": c.preset,
            "activities": [a for a in cc.ACTIVITIES if a in c.activities],
        }
        for c in sorted(current.connectors.values(), key=lambda c: c.key)
    ]
    return {"connectors": rows, "issues": [{"path": i.path, "message": i.message} for i in current.issues]}


def presets_json(plugin_root: Path) -> dict[str, Any]:
    return {"presets": cc.load_presets(plugin_root)}
```

- [ ] **Step 4: Run the ops tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_custom_connector_ops.py -q`
Expected: all PASS.

- [ ] **Step 5: Write the failing CLI tests**

`engine/tests/unit/test_cli_connectors_custom.py`:

```python
"""CLI surface for `scoutctl connectors custom …` and `connectors presets`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout.cli import app
from scout.scripts.bootstrap import BootstrapConfig, install

runner = CliRunner()
PLUGIN = Path(__file__).parent.parent.parent.parent
DEF = """\
key: suite_mail
display_name: Mail suite
server: example_suite
probe: mcp__example_suite__list_folders
preset: mail
inbound:
  tools: [mcp__example_suite__search_messages]
"""


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=v, plugin_root=PLUGIN, instance_name="TestScout", instance_name_lower="testscout",
            user_name="Alex", user_email="alex@example.com", timezone="America/New_York", platform="macos",
            plugin_version="0.0.0", enabled_connectors=set(), connector_inputs={}, skip_jobs=True, skip_claude=True,
        )
    )
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def test_add_from_stdin_prints_json_and_exits_0(vault: Path):
    result = runner.invoke(app, ["connectors", "custom", "add", "--file", "-"], input=DEF)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "applied"


def test_add_invalid_exits_2_with_issues(vault: Path, tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(DEF.replace("server: example_suite", "server: other"))
    result = runner.invoke(app, ["connectors", "custom", "add", "--file", str(bad)])
    assert result.exit_code == 2
    assert json.loads(result.stdout)["issues"][0]["path"] == "connectors.suite_mail.server"


def test_add_unparseable_file_exits_2(vault: Path):
    result = runner.invoke(app, ["connectors", "custom", "add", "--file", "-"], input="key: [unclosed\n")
    assert result.exit_code == 2
    assert json.loads(result.stdout)["status"] == "invalid"


def test_malformed_input_flag_exits_2(vault: Path):
    result = runner.invoke(app, ["connectors", "custom", "add", "--file", "-", "--input", "novalue"], input=DEF)
    assert result.exit_code == 2


def test_validate_list_remove_round_trip(vault: Path):
    assert runner.invoke(app, ["connectors", "custom", "validate", "--file", "-"], input=DEF).exit_code == 0
    runner.invoke(app, ["connectors", "custom", "add", "--file", "-"], input=DEF)
    listing = json.loads(runner.invoke(app, ["connectors", "custom", "list"]).stdout)
    assert [c["key"] for c in listing["connectors"]] == ["suite_mail"]
    removed = runner.invoke(app, ["connectors", "custom", "remove", "suite_mail"])
    assert removed.exit_code == 0 and json.loads(removed.stdout)["status"] == "applied"


def test_presets_command_prints_json():
    result = runner.invoke(app, ["connectors", "presets"])
    assert result.exit_code == 0
    assert "mail" in json.loads(result.stdout)["presets"]
```

- [ ] **Step 6: Run the CLI tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_cli_connectors_custom.py -q`
Expected: FAIL — `No such command 'custom'`.

- [ ] **Step 7: Wire the CLI**

Inside `_register_connectors()` in `engine/scout/cli.py`, after the `snapshot` command:

```python
    custom_app = typer.Typer(help="Custom connectors: any tool Scout should read (connectors.custom.yaml).")
    connectors_app.add_typer(custom_app, name="custom")

    def _plugin_root() -> Path:
        return Path(__file__).parent.parent.parent

    def _emit(payload: dict, code: int = 0) -> None:
        import json as _json

        typer.echo(_json.dumps(payload, indent=2))
        raise typer.Exit(code=code)

    def _read_definition(file: str):
        """Parse a definition file ('-' = stdin). Returns (data, None) or (None, Outcome)."""
        import yaml as _yaml

        from scout.custom_connectors import Issue
        from scout.scripts.custom_connector_ops import Outcome

        try:
            text = sys.stdin.read() if file == "-" else Path(file).read_text(encoding="utf-8")
            return _yaml.safe_load(text), None
        except (OSError, UnicodeDecodeError, _yaml.YAMLError) as e:
            return None, Outcome("invalid", issues=[Issue("definition", f"could not be read: {e}")])

    @custom_app.command("add")
    def cli_custom_add(
        file: str = typer.Option(..., "--file", help="Definition (YAML or JSON) with a `key` field; '-' reads stdin."),
        input_: list[str] = typer.Option([], "--input", help="NAME=VALUE for a needs_user_input entry (repeatable)."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show the sections it would render; write nothing."),
        unverified: bool = typer.Option(False, "--unverified", help="Skip the bash probe (the app path)."),
    ) -> None:
        """Add or replace a custom connector and apply it to the live brain files."""
        from scout import __version__
        from scout import paths as _paths
        from scout.custom_connectors import Issue
        from scout.scripts.custom_connector_ops import Outcome, add

        data, failed = _read_definition(file)
        if failed is not None:
            _emit(failed.to_json(), failed.exit_code)
        inputs: dict[str, str] = {}
        for item in input_:
            name, sep, value = item.partition("=")
            if not sep or not name:
                bad = Outcome("invalid", issues=[Issue("--input", f"{item!r} is not NAME=VALUE")])
                _emit(bad.to_json(), bad.exit_code)
            inputs[name] = value
        outcome = add(
            _paths.data_dir(),
            data,
            plugin_root=_plugin_root(),
            plugin_version=__version__,
            inputs=inputs,
            dry_run=dry_run,
            unverified=unverified,
        )
        _emit(outcome.to_json(), outcome.exit_code)

    @custom_app.command("remove")
    def cli_custom_remove(key: str) -> None:
        """Remove a custom connector and apply the removal."""
        from scout import __version__
        from scout import paths as _paths
        from scout.scripts.custom_connector_ops import remove

        outcome = remove(_paths.data_dir(), key, plugin_root=_plugin_root(), plugin_version=__version__)
        _emit(outcome.to_json(), outcome.exit_code)

    @custom_app.command("validate")
    def cli_custom_validate(file: str = typer.Option(..., "--file", help="Definition file; '-' reads stdin.")) -> None:
        """Validate one definition without writing anything."""
        from scout.scripts.custom_connector_ops import validate

        data, failed = _read_definition(file)
        outcome = failed if failed is not None else validate(data, plugin_root=_plugin_root())
        _emit(outcome.to_json(), outcome.exit_code)

    @custom_app.command("list")
    def cli_custom_list() -> None:
        """List custom connectors and any problems in connectors.custom.yaml."""
        from scout import paths as _paths
        from scout.scripts.custom_connector_ops import list_custom

        _emit(list_custom(_paths.data_dir(), plugin_root=_plugin_root()))

    @connectors_app.command("presets")
    def cli_connectors_presets() -> None:
        """Preset names and their default guidance per activity (JSON)."""
        from scout.scripts.custom_connector_ops import presets_json

        _emit(presets_json(_plugin_root()))
```

`_emit` raises `typer.Exit`, so code after a failing `_emit` never runs; mypy may still flag `data` as possibly `None` — `add`/`validate` accept `Any`, so no narrowing is needed.

- [ ] **Step 8: Run all custom tests**

Run: `.venv/bin/pytest tests/unit/test_cli_connectors_custom.py tests/unit/test_custom_connector_ops.py tests/unit/test_cli_connectors_subapp.py -q`
Expected: all PASS.

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add engine/scout/scripts/custom_connector_ops.py engine/scout/cli.py engine/tests/unit/test_custom_connector_ops.py engine/tests/unit/test_cli_connectors_custom.py
git commit -m "feat(cli): scoutctl connectors custom add/remove/validate/list and connectors presets"
```

---

### Task 7: `bootstrap install` accepts custom connectors

**Files:**
- Modify: `engine/scout/scripts/bootstrap.py` (`BootstrapConfig`, new stage, `install`)
- Modify: `engine/scout/cli.py` (`cli_bootstrap_install`)
- Test: `engine/tests/unit/test_bootstrap_install_custom.py`

**Interfaces:**
- Consumes: `cc.parse_file`, `cc.reserved_keys`, `cc.load_presets`, `cc.write` (Task 1).
- Produces: `BootstrapConfig.custom_connectors_raw: dict[str, Any] | None = None`; CLI flags `--custom-connectors-file PATH`, `--custom-input KEY.NAME=VALUE` (repeatable).

- [ ] **Step 1: Write the failing tests**

```python
"""bootstrap install --custom-connectors-file: a fresh vault reads custom connectors from day one."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from scout import custom_connectors as cc
from scout.cli import app

runner = CliRunner()
CUSTOM = """\
schema_version: 1
connectors:
  suite_mail:
    display_name: Mail suite
    server: example_suite
    probe: mcp__example_suite__list_folders
    preset: mail
    needs_user_input: [mailbox]
    inbound:
      tools: [mcp__example_suite__search_messages]
"""


def _argv(*extra: str) -> list[str]:
    return [
        "bootstrap", "install", "--user-name", "Alex", "--user-email", "alex@example.com",
        "--connectors", "slack", "--no-jobs", "--claude-bin", "/bin/echo", *extra,
    ]


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    v = tmp_path / "Scout"
    monkeypatch.setenv("SCOUT_DATA_DIR", str(v))
    return v


def test_install_writes_custom_file_enables_it_and_renders_it(vault: Path, tmp_path: Path):
    f = tmp_path / "custom.yaml"
    f.write_text(CUSTOM)
    result = runner.invoke(app, _argv("--custom-connectors-file", str(f), "--custom-input", "suite_mail.mailbox=team"))
    assert result.exit_code == 0, result.output
    assert (vault / cc.CUSTOM_FILE).exists()
    config = yaml.safe_load((vault / "scout-config.yaml").read_text())["connectors"]
    assert set(config["enabled"]) == {"slack", "suite_mail"}
    assert config["inputs"]["suite_mail__mailbox"] == "team"
    assert "## Mail suite Inbound Scan" in (vault / "SKILL.md").read_text()


def test_invalid_custom_file_exits_2_before_creating_the_vault(vault: Path, tmp_path: Path):
    f = tmp_path / "custom.yaml"
    f.write_text(CUSTOM.replace("server: example_suite", "server: other"))
    result = runner.invoke(app, _argv("--custom-connectors-file", str(f), "--custom-input", "suite_mail.mailbox=t"))
    assert result.exit_code == 2
    assert "connectors.suite_mail.server" in result.output
    assert not vault.exists()


def test_missing_custom_input_exits_2(vault: Path, tmp_path: Path):
    f = tmp_path / "custom.yaml"
    f.write_text(CUSTOM)
    result = runner.invoke(app, _argv("--custom-connectors-file", str(f)))
    assert result.exit_code == 2
    assert "mailbox" in result.output
    assert not vault.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_bootstrap_install_custom.py -q`
Expected: FAIL — `No such option: --custom-connectors-file`.

- [ ] **Step 3: Implement the stage**

In `BootstrapConfig` (after `auto_update`):

```python
    # /scout-setup's custom connectors (a validated connectors.custom.yaml
    # `connectors:` mapping). Install writes it before the first assembly.
    custom_connectors_raw: dict[str, Any] | None = None
```

(add `from typing import Any` to the imports). New stage next to `_stage_seed_schedule`:

```python
def _stage_write_custom_connectors(cfg: BootstrapConfig) -> None:
    """Install only: write connectors.custom.yaml so the first assembly includes it."""
    if cfg.custom_connectors_raw:
        custom_connectors.write(cfg.vault, cfg.custom_connectors_raw)
```

In `install()`, call it immediately before `_stage_cat4_install(cfg)`:

```python
        _stage_write_custom_connectors(cfg)
        _stage_cat4_install(cfg)
```

- [ ] **Step 4: Implement the CLI flags**

In `cli_bootstrap_install`, add parameters:

```python
        custom_connectors_file: Path | None = typer.Option(
            None, "--custom-connectors-file", help="A connectors.custom.yaml body from /scout-setup."
        ),
        custom_input: list[str] = typer.Option(
            [], "--custom-input", help="KEY.NAME=VALUE for a custom connector's needs_user_input (repeatable)."
        ),
```

and before `cfg = BootstrapConfig(...)`:

```python
        plugin_root = Path(__file__).parent.parent.parent
        enabled = set(c.strip() for c in connectors.split(",") if c.strip())
        custom_inputs: dict[str, str] = {}
        for item in custom_input:
            target, sep, value = item.partition("=")
            ckey, dot, name = target.partition(".")
            if not sep or not dot or not ckey or not name:
                typer.echo(f"  invalid: --custom-input {item!r} is not KEY.NAME=VALUE", err=True)
                raise typer.Exit(code=2)
            custom_inputs[f"{ckey}__{name}"] = value
        custom_raw = None
        if custom_connectors_file is not None:
            import yaml as _yaml

            from scout import custom_connectors as _cc

            try:
                raw = _yaml.safe_load(custom_connectors_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, _yaml.YAMLError) as e:
                typer.echo(f"  invalid: {custom_connectors_file}: {e}", err=True)
                raise typer.Exit(code=2) from e
            loaded = _cc.parse_file(
                raw, reserved=_cc.reserved_keys(plugin_root), presets=_cc.load_presets(plugin_root)
            )
            problems = [f"{i.path}: {i.message}" for i in loaded.issues]
            for c in loaded.connectors.values():
                problems += [
                    f"connectors.{c.key}.needs_user_input: no value for {n!r}; pass --custom-input {c.key}.{n}=<value>"
                    for n in c.needs_user_input
                    if not custom_inputs.get(f"{c.key}__{n}")
                ]
            if problems:
                for p in problems:
                    typer.echo(f"  invalid: {p}", err=True)
                raise typer.Exit(code=2)
            custom_raw = loaded.raw
            enabled |= set(loaded.connectors)
```

Then in the `BootstrapConfig(...)` call use `plugin_root=plugin_root`, `enabled_connectors=enabled`, merge `**custom_inputs` into the existing `connector_inputs={...}` dict, and pass `custom_connectors_raw=custom_raw`. This validation runs before `install(cfg)`, so an invalid file never creates the vault.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_bootstrap_install_custom.py tests/unit/test_bootstrap_install.py tests/unit/test_cli_surface.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add engine/scout/scripts/bootstrap.py engine/scout/cli.py engine/tests/unit/test_bootstrap_install_custom.py
git commit -m "feat(bootstrap): install takes --custom-connectors-file and --custom-input"
```

---

### Task 8: Health roster, probe registry, and connector-log hook

**Files:**
- Modify: `engine/scout/connectors.py` (`Tier`, `load_registry`, new `_custom_roster_entries`)
- Modify: `engine/scout/scripts/connector_probes.py` (`resolve_registry`, new `_custom_probes`)
- Modify: `engine/scout/hooks/connector_log.py` (`_bash_key`)
- Modify: `engine/scout/cli.py` (`connectors list --json`)
- Test: `engine/tests/unit/test_custom_connectors_health.py`

**Interfaces:**
- Consumes: `cc.load`, `cc.bash_binaries`, `CustomConnector.health_key`, `ToolRef.binary` (Task 1).
- Produces: `Tier.CUSTOM = "custom"`; derived rows in `load_registry()`; derived probes in `resolve_registry()`; `connectors list --json` → `{"connectors": [{"key", "display_name", "tier", "required_in_types"}]}`.

- [ ] **Step 1: Write the failing tests**

```python
"""Custom connectors in the health roster, the probe registry, and the connector-log hook."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from scout import custom_connectors as cc
from scout.cli import app
from scout.connectors import Tier, load_registry
from scout.hooks.connector_log import classify
from scout.scripts.connector_probes import ProbeKind, resolve_registry
from scout.scripts.connectors_snapshot import build_snapshot

runner = CliRunner()
DEFS = {
    "suite_mail": {
        "display_name": "Mail suite",
        "server": "example_suite",
        "probe": "mcp__example_suite__list_folders",
        "preset": "mail",
        "inbound": {"tools": ["mcp__example_suite__search_messages"]},
        "required_in_types": ["briefing"],
    },
    "suite_chat": {
        "display_name": "Chat suite",
        "server": "example_suite",
        "probe": "mcp__example_suite__list_chats",
        "preset": "chat",
        "inbound": {"tools": ["mcp__example_suite__search_chats"]},
    },
    "tickets": {
        "display_name": "Tickets",
        "probe": {"bash": "tix whoami"},
        "inbound": {"tools": [{"bash": "tix list"}], "focus": "Changed tickets."},
    },
    "webhook": {
        "display_name": "Webhook feed",
        "probe": {"bash": "curl -sf https://example.com/health"},
        "inbound": {"tools": [{"bash": "curl -s https://example.com/items"}], "focus": "New items."},
    },
}


def test_roster_gets_one_custom_row_per_server_and_one_per_bash_connector(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    reg = load_registry()
    row = reg["mcp:example_suite"]
    assert row.tier is Tier.CUSTOM
    assert row.display_name == "Chat suite, Mail suite"
    assert [t.value for t in row.required_in_types] == ["briefing"]
    assert reg["tickets"].tier is Tier.CUSTOM
    assert len(reg["tickets"].remediation.first_fix) <= 180


def test_shipped_rows_win_and_broken_file_yields_no_custom_rows(fake_data_dir: Path):
    (fake_data_dir / cc.CUSTOM_FILE).write_text("connectors: [unclosed\n")
    reg = load_registry()
    assert reg["mcp:claude_ai_Slack"].tier is Tier.OFFICIAL
    assert not any(reg[k].tier is Tier.CUSTOM for k in reg.keys())


def test_snapshot_never_contains_custom_rows(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    keys = {row["key"] for row in build_snapshot()["connectors"]}
    assert "mcp:example_suite" not in keys and "tickets" not in keys


def test_probe_registry_includes_custom_probes(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    reg = resolve_registry(data_dir=fake_data_dir)
    assert reg["suite_mail"].kind is ProbeKind.MCP_TOOL
    assert reg["suite_mail"].tool_chain == ["mcp__example_suite__list_folders"]
    assert reg["tickets"].kind is ProbeKind.BASH
    assert reg["tickets"].bash_command == "tix whoami"
    assert reg["slack"].kind is ProbeKind.MCP_TOOL  # shipped untouched


def test_probe_registry_survives_a_broken_custom_file(fake_data_dir: Path):
    (fake_data_dir / cc.CUSTOM_FILE).write_text(":::\n")
    assert "slack" in resolve_registry(data_dir=fake_data_dir)


def test_hook_labels_custom_binaries_but_not_generic_ones(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    assert classify("Bash", {"command": "cd ~/Scout && tix list"}) == "tickets"
    assert classify("Bash", {"command": "curl -s https://example.com/items"}) == "bash:curl"
    assert classify("Bash", {"command": "gh pr list"}) == "github"
    assert classify("mcp__example_suite__search_messages", {}) == "mcp:example_suite"


def test_connectors_list_json_includes_custom_rows(fake_data_dir: Path):
    cc.write(fake_data_dir, DEFS)
    result = runner.invoke(app, ["connectors", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = {r["key"]: r for r in json.loads(result.stdout)["connectors"]}
    assert rows["mcp:example_suite"]["tier"] == "custom"
    assert rows["mcp:claude_ai_Slack"]["tier"] == "official"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_custom_connectors_health.py -q`
Expected: FAIL — `AttributeError: CUSTOM`.

- [ ] **Step 3: Roster**

In `engine/scout/connectors.py`:

```python
class Tier(enum.Enum):
    OFFICIAL = "official"
    AUTO_DISCOVERED = "auto_discovered"
    COMMUNITY = "community"
    CUSTOM = "custom"  # derived from the vault's connectors.custom.yaml
```

In `load_registry`, after the overlay loop and before building `connectors`:

```python
    for key, raw in _custom_roster_entries(overlay_data_dir).items():
        merged.setdefault(key, raw)  # shipped and overlay rows win
```

Add:

```python
def _custom_roster_entries(data_dir: Path) -> dict[str, dict[str, Any]]:
    """Roster rows derived from ``<data_dir>/connectors.custom.yaml`` (custom-connectors spec §5).

    One row per MCP server (keyed ``mcp:<server>``, matching connector_log.classify)
    and one per bash-only connector. Never raises: a broken custom file must not take
    connector health down with it (bootstrap doctor reports the file instead).
    """
    try:
        from scout.custom_connectors import load

        connectors = load(data_dir).connectors
    except Exception:
        return {}
    grouped: dict[str, list[Any]] = {}
    for c in connectors.values():
        grouped.setdefault(c.health_key, []).append(c)
    rows: dict[str, dict[str, Any]] = {}
    for health_key, members in grouped.items():
        names = ", ".join(sorted(c.display_name for c in members))
        keys = ", ".join(sorted(c.key for c in members))
        if health_key.startswith("mcp:"):
            first_fix = f"Reconnect {names} at https://claude.ai/settings/connectors (or /mcp for a local server)."
        else:
            binary = members[0].probe.binary or members[0].key
            first_fix = f"Check that `{binary}` runs in a terminal, then run /scout-connect {members[0].key}."
        rows[health_key] = {
            "display_name": names,
            "tier": "custom",
            "capabilities": ["inbound"],
            "required_in_types": sorted({t for c in members for t in c.required_in_types}),
            "remediation": {
                "first_fix": first_fix[:180],
                "detail": f"Custom connector(s) {keys} in connectors.custom.yaml. "
                f"If the tools were renamed, run /scout-connect for each to re-derive the definition.",
            },
        }
    return rows
```

- [ ] **Step 4: Probe registry**

In `engine/scout/scripts/connector_probes.py`, at the end of `resolve_registry` before `return merged`:

```python
    for key, probe in _custom_probes(data_dir, plugin_root).items():
        merged.setdefault(key, probe)  # the overlay wins on collision
```

Add:

```python
def _custom_probes(data_dir: Path, plugin_root: Path) -> dict[str, Probe]:
    """Probes derived from connectors.custom.yaml. Invalid entries are skipped, never raised."""
    from scout.custom_connectors import load

    out: dict[str, Probe] = {}
    for key, c in load(data_dir, plugin_root=plugin_root).connectors.items():
        needs = list(c.needs_user_input)
        if c.probe.kind == "bash":
            out[key] = Probe(name=key, kind=ProbeKind.BASH, bash_command=c.probe.value, needs_user_input=needs)
        else:
            out[key] = Probe(name=key, kind=ProbeKind.MCP_TOOL, tool_chain=[c.probe.value], needs_user_input=needs)
    return out
```

- [ ] **Step 5: Hook**

In `engine/scout/hooks/connector_log.py`, add:

```python
def _custom_bash_connectors() -> dict[str, str]:
    """Binary → key for custom bash connectors. Never raises (hook path)."""
    try:
        from scout.custom_connectors import bash_binaries

        return bash_binaries(paths.data_dir())
    except Exception:
        return {}
```

In `_bash_key`, compute the map once, right after the `if not cmd:` guard:

```python
    known = {**_custom_bash_connectors(), **_BASH_CONNECTORS}  # shipped wins
```

and replace the two `_BASH_CONNECTORS` lookups inside the loop with `known`:

```python
        if head in known:
            return known[head]
```

- [ ] **Step 6: `connectors list --json`**

Replace `cli_connectors_list` in `engine/scout/cli.py`:

```python
    @connectors_app.command("list")
    def cli_connectors_list(
        json_out: bool = typer.Option(False, "--json", help="Emit the roster as JSON (consumed by Scout.app)."),
    ) -> None:
        """List the registered connector roster (shipped + overlay + custom)."""
        import json as _json

        from scout.connectors import load_registry

        reg = load_registry()
        if json_out:
            rows = [
                {
                    "key": key,
                    "display_name": reg[key].display_name,
                    "tier": reg[key].tier.value,
                    "required_in_types": [t.value for t in reg[key].required_in_types],
                }
                for key in sorted(reg.keys())
            ]
            typer.echo(_json.dumps({"connectors": rows}, indent=2))
            return
        for key in sorted(reg.keys()):
            c = reg[key]
            typer.echo(f"{key}\t{c.tier.value}\t{c.display_name}")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_custom_connectors_health.py tests/unit/test_connectors_yaml.py tests/unit/test_hooks_connector_log.py tests/unit/test_connector_probe_registry.py tests/unit/test_scripts_connectors_snapshot.py tests/unit/test_scripts_connector_health.py tests/unit/test_cli_connectors_subapp.py -q`
Expected: all PASS. Then confirm the snapshot guard CI runs is unaffected:

Run: `.venv/bin/python -m scout.scripts.connectors_snapshot --check --target scout/connectors.snapshot.json`
Expected: exit 0.

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff check scout tests && .venv/bin/ruff format scout tests && .venv/bin/mypy scout
git add engine/scout/connectors.py engine/scout/scripts/connector_probes.py engine/scout/hooks/connector_log.py engine/scout/cli.py engine/tests/unit/test_custom_connectors_health.py
git commit -m "feat(health): custom connectors in the roster, probe registry and connector-log hook"
```

---

### Task 9: Wizard — `/scout-setup`, `/scout-connect`, `/scout-update`

**Files:**
- Modify: `commands/scout-setup.md` (Step 2 blockquote → Step 2b; Step 4 flags)
- Create: `commands/scout-connect.md`
- Modify: `commands/scout-update.md` (Step 3 note + pointer)
- Test: `engine/tests/unit/test_wizard_custom_connectors_prose.py`

**Interfaces:**
- Consumes: CLI from Tasks 6–7 (`connectors custom add --file - --input`, `bootstrap install --custom-connectors-file --custom-input`, `connectors probe-registry --json`, `connectors presets`).

- [ ] **Step 1: Write the failing prose guard**

```python
"""The setup wizards route every tool to custom connectors and never dead-end the user."""

from __future__ import annotations

from pathlib import Path

COMMANDS = Path(__file__).parent.parent.parent.parent / "commands"
DEAD_ENDS = ("not supported", "doesn't come with", "does not come with", "phase file i'd write", "no probe for it")


def _text(name: str) -> str:
    return (COMMANDS / name).read_text(encoding="utf-8")


def test_scout_setup_offers_uncovered_servers_and_installs_them():
    text = _text("scout-setup.md")
    assert "Step 2b" in text
    assert "--custom-connectors-file" in text
    assert "--custom-input" in text
    assert "connectors presets" in text


def test_scout_connect_exists_and_calls_the_engine():
    text = _text("scout-connect.md")
    assert text.startswith("---\nname: scout-connect\n")
    assert "connectors custom add --file -" in text
    assert "connectors custom remove" in text


def test_scout_update_points_to_scout_connect():
    assert "/scout-connect" in _text("scout-update.md")


def test_no_wizard_prose_dead_ends_the_user():
    for name in ("scout-setup.md", "scout-connect.md", "scout-update.md"):
        lowered = _text(name).lower()
        for phrase in DEAD_ENDS:
            assert phrase not in lowered, f"{name} contains {phrase!r}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_wizard_custom_connectors_prose.py -q`
Expected: FAIL — `Step 2b` missing; `scout-connect.md` not found.

- [ ] **Step 3: Write `commands/scout-connect.md`**

````markdown
---
name: scout-connect
description: Add, update or remove a tool Scout reads — any connected MCP server or CLI (Outlook, Teams, a CRM, a data platform…). Writes connectors.custom.yaml through scoutctl and makes it live.
---

# Scout Connect

Make Scout read a tool it doesn't have built-in instructions for. Usage: `/scout-connect <tool>` to add or update, `/scout-connect --remove <key>` to remove, or `/scout-connect` with no argument to see what's connected but unused.

Resolve the engine the same way `/scout-setup` does:

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
SCOUTCTL="$PLUGIN_ROOT/.venv/bin/scoutctl"
test -x "$SCOUTCTL" || SCOUTCTL="$(command -v scoutctl)"
```

## Hard rule

Never tell the user Scout can't read a tool, lacks support for it, or needs a hand-written instructions file. A tool ends in exactly one of three states: **added**, **skipped because the user chose to**, or **"sign in first"** — in which case say *"Sign in through `/mcp` (or claude.ai → Settings → Connectors), then run `/scout-connect <tool>`."*

## 1. Find the tool's server

Every MCP tool is named `mcp__<server>__<tool>`. Collect the distinct `<server>` values available in this session (use ToolSearch with `+<name>` to load deferred tools). Skip servers that are tooling for this app rather than a source of work (browsers, terminals, session or sidebar managers, schedulers, visualizers). Skip servers already covered: `"$SCOUTCTL" connectors probe-registry --json` lists every probe; a server is covered when one of its `tool_chain` entries starts with `mcp__<server>__`.

With no argument, list the uncovered servers and ask which to add. If the tool the user named has no server here and no CLI on `PATH`, use the "sign in first" outcome.

## 2. Draft one definition per surface

Read the server's tool names and descriptions. One server can be several connectors — a productivity suite may be mail, calendar and chat; give each its own key (`outlook`, `outlook_calendar`, `teams`).

For each connector decide:

- **Activities.** `inbound` = new things that may need the user's action. `outbound` = what the user did there (evidence of completed work) — only where the tool records the user's own actions (mail sent, messages posted, tickets closed). `lookup` = query on demand, with a `when` sentence.
- **Tools.** 1–4 read tools per activity (search, list, get, read). **Never** a tool that sends, posts, creates, updates or deletes.
- **Preset.** `mail`, `chat` or `calendar` when the surface is one of those (`"$SCOUTCTL" connectors presets` shows the text). Otherwise write `focus` (inbound/outbound) or `when` (lookup): one or two sentences on what matters to this user, drafted from what you know about their work.
- **Probe.** The cheapest read tool (list folders, whoami, get profile). Call it now. If it errors, use the "sign in first" outcome for that connector.
- **Inputs.** If the tools need a value only the user knows (a workspace or project id), add it to `needs_user_input` and ask for it.

Definition shape (YAML; `key` is required):

```yaml
key: teams
display_name: Microsoft Teams
server: <server>
probe: mcp__<server>__<cheap_read_tool>
preset: chat
inbound:
  tools: [mcp__<server>__<search_tool>, mcp__<server>__<read_thread_tool>]
notes: "<optional: anything the user told you to skip or prioritize>"
```

A CLI tool uses `{bash: "<command>"}` for the probe and tools, and no `server`.

## 3. Confirm, then add

Show each connector in three lines — *name · what it scans · what it looks up* — and ask the user to confirm or adjust. Then pipe each definition to the engine:

```bash
"$SCOUTCTL" connectors custom add --file - --input <name>=<value> <<'EOF'
<definition YAML>
EOF
```

Read the JSON it prints:

- `status: applied` → tell the user the next scheduled run reads it.
- `status: invalid` → fix each `issues[].path` and retry; don't show raw YAML errors to the user unless you can't fix them.
- `status: probe-failed` → the CLI isn't working yet; show `issues[0].message` and use the "sign in first" outcome.
- `status: deferred` → saved; tell the user it goes live after `/scout-update`.
- `status: conflict` → saved; tell the user their own edits to `SKILL.md` overlap and walk them through resolving the `.proposed-merge` file listed in `sidecars`.
- `status: error` → show `message` (for example, a session is running — try again shortly).

## Remove

`/scout-connect --remove <key>`: confirm with the user, then run `"$SCOUTCTL" connectors custom remove <key>` and report the status the same way.
````

- [ ] **Step 4: Edit `commands/scout-setup.md`**

Replace the whole `> **Custom connectors:** …` blockquote in Step 2 (the paragraph starting "to make `/scout-setup` detect a connector that isn't shipped" through the closing ```` ``` ```` of its `devin:` example) with nothing, and insert this new section between the "Confirm with the user: …" line of Step 2 and the `---` before Step 3:

````markdown
---

## Step 2b: Everything else the user works in

The shipped probes cover a fixed set of tools. Everything else the user has connected — Outlook, Teams, a CRM, a support desk, a data platform, a CLI — becomes a **custom connector** so Scout reads it too. Follow `commands/scout-connect.md` sections 1–2 (find uncovered servers, draft one definition per surface, call each probe) with two differences:

1. **Offer by name first.** "I also see <server names> connected. Want Scout to read them?" Then ask: "Anything else you use for work?"
2. **Don't call `connectors custom add`** — there is no vault yet. Collect the confirmed definitions into one file instead, keyed by connector (no `key:` field inside each entry):

```bash
CUSTOM_FILE="$(mktemp -t scout-custom).yaml"
cat > "$CUSTOM_FILE" <<'EOF'
schema_version: 1
connectors:
  outlook:
    display_name: Outlook
    server: <server>
    probe: mcp__<server>__<cheap_read_tool>
    preset: mail
    inbound:
      tools: [mcp__<server>__<search_tool>]
    outbound:
      tools: [mcp__<server>__<search_tool>]
EOF
```

`"$SCOUTCTL" connectors presets` prints the preset text if you want to show the user what Scout will look for.

Never tell the user Scout can't read a tool, lacks support for it, or needs a hand-written instructions file. A tool is either added, skipped by the user's choice, or waiting on sign-in: *"Sign in through `/mcp`, then run `/scout-connect <tool>` after setup."*

Add the custom connectors to the checklist from Step 2 with a ✓, e.g. `[✓] Outlook (custom)`.
````

In Step 4's install command, add these two lines before `--max-budget`:

```bash
    --custom-connectors-file "$CUSTOM_FILE" \
    --custom-input "<key>.<name>=<value>" \
```

and add this sentence after the code block: "Omit `--custom-connectors-file` if Step 2b added nothing; repeat `--custom-input` once per `needs_user_input` value. Install exits 2 and prints `invalid: <path>: <message>` for a bad definition or a missing input — fix the file and re-run; nothing was written."

- [ ] **Step 5: Edit `commands/scout-update.md`**

Replace the bullet

```markdown
- `~/Scout/connector-probes.local.yaml` (custom connector probes) is a user
  file, never templated, so it is preserved untouched across upgrades.
```

with

```markdown
- `~/Scout/connectors.custom.yaml` (custom connectors) and
  `~/Scout/connector-probes.local.yaml` (custom probes) are user files, never
  templated, so upgrades leave them untouched — and every upgrade re-renders the
  custom connectors' sections from the current templates.

After reporting, if `"$SCOUTCTL" connectors probe-registry --json` shows that a
connected MCP server isn't covered by any probe, offer: "You also have <server>
connected — run `/scout-connect <server>` to have Scout read it."
```

- [ ] **Step 6: Run the guard and the existing command-prose tests**

Run: `.venv/bin/pytest tests/unit/test_wizard_custom_connectors_prose.py tests/unit/test_cli_surface.py tests/unit/test_connector_key_invariant.py -q`
Expected: all PASS.

- [ ] **Step 7: Validate the plugin and commit**

Run (from the worktree root): `claude plugin validate .`
Expected: passes (the new command has `name` + `description` frontmatter).

```bash
git add commands/scout-setup.md commands/scout-connect.md commands/scout-update.md engine/tests/unit/test_wizard_custom_connectors_prose.py
git commit -m "feat(wizard): /scout-setup reads every connected tool; new /scout-connect"
```

---

### Task 10: Docs, changelog, and the full gate

**Files:**
- Modify: `README.md` (`## Supported Connectors`)
- Modify: `CHANGELOG.md` (`## [Unreleased]`)

- [ ] **Step 1: README**

At the end of the `## Supported Connectors` section, add:

````markdown
### Any other tool: custom connectors

Scout can read any tool you have connected — Outlook, Teams, a CRM, a support desk, a data platform, a CLI — without a built-in integration. `/scout-setup` offers every connected server it doesn't already cover; afterwards, `/scout-connect <tool>` adds one.

Each custom connector is one entry in `~/Scout/connectors.custom.yaml`: which tools to call, and for which of Scout's three activities — **inbound** (what came in that may need you), **outbound** (what you did, as evidence a task is done), **lookup** (query on demand). Mail, chat and calendar tools can use a preset that carries Scout's tuned rules for those surfaces. The file survives plugin updates, and `scoutctl connectors custom add|remove|list` manages it (the Mac app uses the same commands).
````

- [ ] **Step 2: CHANGELOG**

Under `## [Unreleased]`, add an `### Added` section above `### Fixed`:

```markdown
### Added
- **Custom connectors: Scout reads any tool you have connected** (`engine/scout/custom_connectors.py`, `phases/custom/`, `phases/presets/`, `commands/scout-connect.md`) — Outlook, Teams and every other tool without a built-in phase used to dead-end `/scout-setup` with "Scout doesn't come with it". A connector is now one entry in the vault's `connectors.custom.yaml` naming its tools for any of three activities (inbound, outbound, lookup), with optional `mail` / `chat` / `calendar` presets carrying the tuned rules from the shipped phases. `/scout-setup` offers every connected server the shipped probes don't cover; `/scout-connect` adds one later. `scoutctl connectors custom add|remove|validate|list` and `connectors presets` are the contract the Mac app will call; `bootstrap install` takes `--custom-connectors-file`. Adding a connector reaches the live `SKILL.md` through a 3-way merge that keeps your own edits; if the plugin changed since your last update it waits for `/scout-update` instead. Custom connectors appear in connector health (`tier: custom`, never critical unless you opt in) and `connectors list --json`.
```

- [ ] **Step 3: Full gate**

Run, from `engine/`:

```bash
.venv/bin/pytest tests/ -q
.venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests && .venv/bin/mypy scout
.venv/bin/python -m scout.scripts.connectors_snapshot --check --target scout/connectors.snapshot.json
.venv/bin/python -m scout.scripts.versioning check
```

Expected: every command exits 0. Report the pytest pass count.

- [ ] **Step 4: End-to-end smoke in a throwaway vault**

```bash
export SCOUT_DATA_DIR="$(mktemp -d)/Scout"
.venv/bin/scoutctl bootstrap install --user-name Alex --user-email alex@example.com --connectors slack --no-jobs --claude-bin /bin/echo
printf 'key: dataplat\ndisplay_name: Data platform\nprobe: {bash: "true"}\ninbound:\n  tools: [{bash: "echo jobs"}]\n  focus: Failed jobs.\n' \
  | .venv/bin/scoutctl connectors custom add --file -
grep -c "## Data platform Inbound Scan" "$SCOUT_DATA_DIR/SKILL.md"
.venv/bin/scoutctl connectors list --json | grep -c '"tier": "custom"'
.venv/bin/scoutctl connectors custom remove dataplat
grep -c "Data platform" "$SCOUT_DATA_DIR/SKILL.md" || true
unset SCOUT_DATA_DIR
```

Expected: `add` prints `"status": "applied"`; the two `grep -c` after it print `1`; `remove` prints `"status": "applied"`; the last grep prints `0`.

- [ ] **Step 5: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: custom connectors in README and CHANGELOG"
```

---

## Follow-up (not in this plan)

**Desktop app (`Raven-Scout/Scout`), separate PR:** `ConnectorHealthService` merges `scoutctl connectors list --json` over the bundled snapshot so custom rows render with real names, and its decoder accepts `tier: "custom"`. The "Add connector" sheet calling `connectors custom add --unverified` follows the app-managed engine work (Scout#115 / #104).
