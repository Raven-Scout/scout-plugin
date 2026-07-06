"""triggers.yaml loader + validator.

The vault-canonical trigger definition lives at
``~/Scout/.scout-state/triggers.yaml`` (alongside ``schedule.yaml``). No
plugin-shipped defaults: an absent file means no triggers, by design —
triggers are opt-in.

Validation rules (spec §Trigger-config validation rules): every trigger
needs ``id``/``source``/``match``/``action``; ``daily_fire_cap`` is
mandatory (no default-unlimited — runaway cost is the #1 risk);
``match.type`` must be in the source's ``SUPPORTED_MATCH_TYPES``;
``run_skill`` actions must name an installed skill; obvious
scout_internal fire-cycles are rejected unless ``allow_cycle: true``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scout.errors import ConfigError
from scout.triggers.sources import SOURCE_NAMES, supported_match_types

TRIGGERS_FILENAME = "triggers.yaml"


class ActionKind(enum.Enum):
    NOTIFY = "notify"
    RUN_SKILL = "run_skill"
    INTERACTIVE = "interactive"


@dataclass(frozen=True)
class Action:
    """One trigger's action: the kind plus its kind-specific params."""

    kind: ActionKind
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Trigger:
    """One validated trigger. Frozen — load_triggers rebuilds; never mutated."""

    id: str
    source: str
    match: dict[str, Any]
    action: Action
    daily_fire_cap: int
    cooldown_seconds: int = 0
    enabled: bool = True
    allow_cycle: bool = False

    @property
    def match_type(self) -> str:
        return str(self.match["type"])


def triggers_path(vault: Path) -> Path:
    """Vault-canonical triggers.yaml location (sibling of schedule.yaml)."""
    return vault / ".scout-state" / TRIGGERS_FILENAME


def default_installed_skills(plugin_root: Path | None = None) -> set[str]:
    """Names of skills shipped by this plugin checkout (``<root>/skills/*/``)."""
    if plugin_root is None:
        plugin_root = Path(__file__).parent.parent.parent.parent
    skills_dir = plugin_root / "skills"
    if not skills_dir.is_dir():
        return set()
    return {p.name for p in skills_dir.iterdir() if p.is_dir()}


def load_triggers(
    path: Path,
    *,
    installed_skills: set[str] | None = None,
) -> list[Trigger]:
    """Load + validate a triggers.yaml. Raises ConfigError on any violation."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError as e:
        raise ConfigError(f"triggers yaml at {path} not found") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"triggers yaml at {path} is malformed: {e}") from e
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"triggers yaml at {path} is not a mapping")

    version = data.get("schema_version", 1)
    if version != 1:
        raise ConfigError(f"triggers yaml at {path} has schema_version {version}; engine supports 1")

    raw_triggers = data.get("triggers", [])
    if not isinstance(raw_triggers, list):
        raise ConfigError(f"triggers yaml at {path}: 'triggers' must be a list")

    if installed_skills is None:
        installed_skills = default_installed_skills()

    triggers: list[Trigger] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(raw_triggers):
        trigger = _build_trigger(i, raw, installed_skills=installed_skills)
        if trigger.id in seen_ids:
            raise ConfigError(f"trigger {trigger.id!r}: duplicate id")
        seen_ids.add(trigger.id)
        triggers.append(trigger)
    return triggers


def _build_trigger(index: int, raw: Any, *, installed_skills: set[str]) -> Trigger:
    label = f"trigger[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{label}: expected a mapping, got {type(raw).__name__}")

    trigger_id = raw.get("id")
    if not isinstance(trigger_id, str) or not trigger_id.strip():
        raise ConfigError(f"{label}: missing required field 'id'")
    label = f"trigger {trigger_id!r}"

    for required in ("source", "match", "action"):
        if required not in raw:
            raise ConfigError(f"{label}: missing required field '{required}'")

    source = raw["source"]
    if source not in SOURCE_NAMES:
        raise ConfigError(f"{label}: unknown source {source!r}; supported: {', '.join(SOURCE_NAMES)}")

    match = raw["match"]
    if not isinstance(match, dict) or "type" not in match:
        raise ConfigError(f"{label}: match.type is required")
    match_type = match["type"]
    allowed = supported_match_types(source)
    if match_type not in allowed:
        raise ConfigError(
            f"{label}: match.type {match_type!r} is not supported by source {source!r}; supported: {', '.join(allowed)}"
        )

    action_raw = raw["action"]
    if not isinstance(action_raw, dict) or "kind" not in action_raw:
        raise ConfigError(f"{label}: action.kind is required")
    try:
        kind = ActionKind(action_raw["kind"])
    except ValueError as e:
        raise ConfigError(
            f"{label}: action.kind {action_raw['kind']!r} is not one of {[k.value for k in ActionKind]}"
        ) from e
    params = {k: v for k, v in action_raw.items() if k != "kind"}

    if kind is ActionKind.RUN_SKILL:
        skill = params.get("skill")
        if not isinstance(skill, str) or not skill.strip():
            raise ConfigError(f"{label}: action.kind=run_skill requires action.skill")
        if skill not in installed_skills:
            raise ConfigError(
                f"{label}: action.skill {skill!r} is not an installed skill; "
                f"installed: {', '.join(sorted(installed_skills)) or '(none)'}"
            )

    if "daily_fire_cap" not in raw:
        raise ConfigError(f"{label}: daily_fire_cap is required (no default-unlimited)")
    try:
        cap = int(raw["daily_fire_cap"])
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{label}: daily_fire_cap must be an integer") from e
    if cap < 1:
        raise ConfigError(f"{label}: daily_fire_cap must be >= 1, got {cap}")

    try:
        cooldown = int(raw.get("cooldown_seconds", 0))
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{label}: cooldown_seconds must be an integer") from e
    if cooldown < 0:
        raise ConfigError(f"{label}: cooldown_seconds must be >= 0, got {cooldown}")

    allow_cycle = bool(raw.get("allow_cycle", False))

    # Cycle guard: a scout_internal trigger that fires a skill on trigger.fired
    # re-enters the trigger pipeline — an obvious fire loop. Opt out explicitly.
    is_refire_cycle = source == "scout_internal" and match_type == "trigger.fired" and kind is ActionKind.RUN_SKILL
    if is_refire_cycle and not allow_cycle:
        raise ConfigError(
            f"{label}: match.type 'trigger.fired' with action.kind 'run_skill' creates a fire cycle; "
            f"set allow_cycle: true if this is intentional"
        )

    return Trigger(
        id=trigger_id,
        source=source,
        match=dict(match),
        action=Action(kind=kind, params=params),
        daily_fire_cap=cap,
        cooldown_seconds=cooldown,
        enabled=bool(raw.get("enabled", True)),
        allow_cycle=allow_cycle,
    )
