"""triggers.yaml loading + validation (engine/scout/triggers/config.py).

All fixture content is synthetic/anonymized per CLAUDE.md (people Alex/Priya/Sam,
GitHub example-org/<repo>, Slack IDs are fake).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout.errors import ConfigError
from scout.triggers.config import ActionKind, Trigger, load_triggers

SKILLS = {"scout-dream", "scout-research"}


def _base_trigger(**overrides) -> dict:
    t = {
        "id": "slack_mention_alex",
        "enabled": True,
        "source": "slack",
        "match": {"type": "mention", "user": "U0123456789"},
        "action": {"kind": "notify", "via": ["telegram"]},
        "cooldown_seconds": 0,
        "daily_fire_cap": 200,
    }
    t.update(overrides)
    return t


def _write(tmp_path: Path, triggers: list[dict], *, schema_version: int = 1) -> Path:
    p = tmp_path / "triggers.yaml"
    p.write_text(yaml.safe_dump({"schema_version": schema_version, "triggers": triggers}), encoding="utf-8")
    return p


def _load(tmp_path: Path, triggers: list[dict], **kw) -> list[Trigger]:
    kw.setdefault("installed_skills", SKILLS)
    return load_triggers(_write(tmp_path, triggers), **kw)


# ----- happy path -----------------------------------------------------------


def test_valid_file_loads_full_record(tmp_path):
    (trigger,) = _load(tmp_path, [_base_trigger()])
    assert trigger.id == "slack_mention_alex"
    assert trigger.enabled is True
    assert trigger.source == "slack"
    assert trigger.match == {"type": "mention", "user": "U0123456789"}
    assert trigger.match_type == "mention"
    assert trigger.action.kind is ActionKind.NOTIFY
    assert trigger.action.params == {"via": ["telegram"]}
    assert trigger.cooldown_seconds == 0
    assert trigger.daily_fire_cap == 200
    assert trigger.allow_cycle is False


def test_enabled_defaults_true_and_false_is_parsed(tmp_path):
    t = _base_trigger()
    del t["enabled"]
    t2 = _base_trigger(id="second", enabled=False)
    first, second = _load(tmp_path, [t, t2])
    assert first.enabled is True
    assert second.enabled is False


def test_run_skill_with_installed_skill_loads(tmp_path):
    t = _base_trigger(action={"kind": "run_skill", "skill": "scout-dream"})
    (trigger,) = _load(tmp_path, [t])
    assert trigger.action.kind is ActionKind.RUN_SKILL
    assert trigger.action.params["skill"] == "scout-dream"


def test_empty_triggers_list_is_valid(tmp_path):
    assert _load(tmp_path, []) == []


# ----- structural validation ------------------------------------------------


def test_missing_file_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_triggers(tmp_path / "nope.yaml", installed_skills=SKILLS)


def test_unsupported_schema_version_rejected(tmp_path):
    p = _write(tmp_path, [_base_trigger()], schema_version=2)
    with pytest.raises(ConfigError, match="schema_version"):
        load_triggers(p, installed_skills=SKILLS)


@pytest.mark.parametrize("field", ["id", "source", "match", "action"])
def test_missing_required_field_rejected(tmp_path, field):
    t = _base_trigger()
    del t[field]
    with pytest.raises(ConfigError):
        _load(tmp_path, [t])


def test_duplicate_ids_rejected(tmp_path):
    with pytest.raises(ConfigError, match="duplicate"):
        _load(tmp_path, [_base_trigger(), _base_trigger()])


# ----- cap / cooldown rules --------------------------------------------------


def test_missing_daily_fire_cap_rejected(tmp_path):
    """No default-unlimited — runaway cost is the #1 risk (spec)."""
    t = _base_trigger()
    del t["daily_fire_cap"]
    with pytest.raises(ConfigError, match="daily_fire_cap"):
        _load(tmp_path, [t])


def test_daily_fire_cap_below_one_rejected(tmp_path):
    with pytest.raises(ConfigError, match="daily_fire_cap"):
        _load(tmp_path, [_base_trigger(daily_fire_cap=0)])


def test_negative_cooldown_rejected(tmp_path):
    with pytest.raises(ConfigError, match="cooldown_seconds"):
        _load(tmp_path, [_base_trigger(cooldown_seconds=-1)])


def test_cooldown_defaults_to_zero(tmp_path):
    t = _base_trigger()
    del t["cooldown_seconds"]
    (trigger,) = _load(tmp_path, [t])
    assert trigger.cooldown_seconds == 0


# ----- source / match validation ---------------------------------------------


def test_unknown_source_rejected(tmp_path):
    with pytest.raises(ConfigError, match="source"):
        _load(tmp_path, [_base_trigger(source="carrier_pigeon")])


def test_match_without_type_rejected(tmp_path):
    with pytest.raises(ConfigError, match="match.type"):
        _load(tmp_path, [_base_trigger(match={"user": "U0123456789"})])


def test_match_type_not_supported_by_source_rejected(tmp_path):
    """Each sources/*.py exposes SUPPORTED_MATCH_TYPES; validator cross-checks."""
    with pytest.raises(ConfigError, match="match.type"):
        _load(tmp_path, [_base_trigger(match={"type": "review_requested"})])


def test_github_source_supports_review_requested(tmp_path):
    t = _base_trigger(
        source="github",
        match={"type": "review_requested", "repo": ["example-org/widget-factory"]},
        action={"kind": "interactive"},
    )
    (trigger,) = _load(tmp_path, [t])
    assert trigger.source == "github"


# ----- action validation -------------------------------------------------------


def test_unknown_action_kind_rejected(tmp_path):
    with pytest.raises(ConfigError, match="action.kind"):
        _load(tmp_path, [_base_trigger(action={"kind": "self_destruct"})])


def test_run_skill_without_skill_rejected(tmp_path):
    with pytest.raises(ConfigError, match="skill"):
        _load(tmp_path, [_base_trigger(action={"kind": "run_skill"})])


def test_run_skill_with_uninstalled_skill_rejected(tmp_path):
    t = _base_trigger(action={"kind": "run_skill", "skill": "not-a-real-skill"})
    with pytest.raises(ConfigError, match="not-a-real-skill"):
        _load(tmp_path, [t])


# ----- cycle guard --------------------------------------------------------------


def _cycle_trigger(**overrides) -> dict:
    t = _base_trigger(
        id="internal_refire",
        source="scout_internal",
        match={"type": "trigger.fired"},
        action={"kind": "run_skill", "skill": "scout-dream"},
    )
    t.update(overrides)
    return t


def test_scout_internal_trigger_fired_run_skill_cycle_rejected(tmp_path):
    with pytest.raises(ConfigError, match="allow_cycle"):
        _load(tmp_path, [_cycle_trigger()])


def test_cycle_allowed_with_explicit_flag(tmp_path):
    (trigger,) = _load(tmp_path, [_cycle_trigger(allow_cycle=True)])
    assert trigger.allow_cycle is True


def test_scout_internal_notify_on_trigger_fired_is_fine(tmp_path):
    t = _cycle_trigger(action={"kind": "notify", "via": ["telegram"]})
    (trigger,) = _load(tmp_path, [t])
    assert trigger.action.kind is ActionKind.NOTIFY
