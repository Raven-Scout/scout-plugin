"""Dispatcher routing + the three action handlers (notify / run_skill / interactive).

Fixtures are synthetic/anonymized per CLAUDE.md.
"""

from __future__ import annotations

import json
from pathlib import Path

from scout.triggers.config import Action, ActionKind, Trigger
from scout.triggers.dispatcher import FireOutcome, dispatch, log_fire
from scout.triggers.sources.base import ConnectorEvent


def _trigger(kind: ActionKind = ActionKind.NOTIFY, params: dict | None = None, **kw) -> Trigger:
    defaults = {
        "id": "slack_mention_alex",
        "source": "slack",
        "match": {"type": "mention"},
        "daily_fire_cap": 10,
    }
    defaults.update(kw)
    return Trigger(action=Action(kind=kind, params=params or {}), **defaults)


def _event(**fields) -> ConnectorEvent:
    return ConnectorEvent(
        source="slack",
        source_event_id="1782910800.000200",
        ts="2026-07-01T13:00:00.000Z",
        raw_payload={"text": "ping <@U0123456789>"},
        normalized_match_fields={
            "type": "mention",
            "author": "priya",
            "channel": "C0123456789",
            "text": "ping <@U0123456789> can you review?",
            **fields,
        },
    )


class _FakeTelegram:
    def __init__(self, fail: bool = False):
        self.calls: list[dict] = []
        self.fail = fail

    def __call__(self, *, tier: str, body: str):
        if self.fail:
            from scout.errors import ConfigError

            raise ConfigError("Missing secret: telegram-bot-token")
        self.calls.append({"tier": tier, "body": body})


# ----- notify -----------------------------------------------------------------


def test_notify_dispatch_sends_to_telegram(fake_data_dir: Path):
    telegram = _FakeTelegram()
    trigger = _trigger(ActionKind.NOTIFY, {"via": ["telegram"]})
    outcome = dispatch(trigger, _event(), vault=fake_data_dir, send_telegram=telegram)

    assert isinstance(outcome, FireOutcome)
    assert outcome.status == "ok"
    assert outcome.trigger_id == "slack_mention_alex"
    assert outcome.event_id == "1782910800.000200"
    assert outcome.action_kind == "notify"
    assert len(telegram.calls) == 1
    body = telegram.calls[0]["body"]
    assert "slack_mention_alex" in body
    assert "can you review" in body
    assert telegram.calls[0]["tier"] == "info"


def test_notify_with_no_working_surface_is_an_error(fake_data_dir: Path):
    trigger = _trigger(ActionKind.NOTIFY, {"via": ["telegram"]})
    outcome = dispatch(trigger, _event(), vault=fake_data_dir, send_telegram=_FakeTelegram(fail=True))
    assert outcome.status == "error"
    assert "telegram" in json.dumps(outcome.detail)


def test_notify_unknown_surface_is_recorded_but_not_fatal(fake_data_dir: Path):
    telegram = _FakeTelegram()
    trigger = _trigger(ActionKind.NOTIFY, {"via": ["pager", "telegram"]})
    outcome = dispatch(trigger, _event(), vault=fake_data_dir, send_telegram=telegram)
    assert outcome.status == "ok"
    assert outcome.detail["surfaces"]["pager"].startswith("unsupported")
    assert outcome.detail["surfaces"]["telegram"] == "sent"


# ----- run_skill -----------------------------------------------------------------


def test_run_skill_writes_payload_and_spawns_runner(fake_data_dir: Path):
    spawned: list[tuple[list[str], dict]] = []

    def spawn(cmd: list[str], env: dict) -> int:
        spawned.append((cmd, env))
        return 4242

    trigger = _trigger(ActionKind.RUN_SKILL, {"skill": "scout-dream"}, id="internal_dream")
    outcome = dispatch(trigger, _event(), vault=fake_data_dir, spawn=spawn)

    assert outcome.status == "ok"
    assert outcome.detail["pid"] == 4242
    assert outcome.detail["skill"] == "scout-dream"

    (cmd, env) = spawned[0]
    assert cmd[0].endswith("run-scout.sh")
    assert env["SCOUT_FORCE_MODE"] == "scout-dream"
    assert env["SCOUT_TRIGGER_ID"] == "internal_dream"
    assert env["SCOUT_DATA_DIR"] == str(fake_data_dir)

    payload_path = Path(env["SCOUT_TRIGGER_EVENT_PATH"])
    assert payload_path.exists()
    payload = json.loads(payload_path.read_text())
    assert payload["trigger_id"] == "internal_dream"
    assert payload["event"]["source_event_id"] == "1782910800.000200"
    assert payload["event"]["normalized_match_fields"]["author"] == "priya"


def test_run_skill_spawn_failure_is_an_error(fake_data_dir: Path):
    def spawn(cmd: list[str], env: dict) -> int:
        raise FileNotFoundError("run-scout.sh not found")

    trigger = _trigger(ActionKind.RUN_SKILL, {"skill": "scout-dream"})
    outcome = dispatch(trigger, _event(), vault=fake_data_dir, spawn=spawn)
    assert outcome.status == "error"
    assert "run-scout.sh" in outcome.detail["error"]


def test_run_skill_honors_custom_runner(fake_data_dir: Path):
    spawned: list[tuple[list[str], dict]] = []
    trigger = _trigger(ActionKind.RUN_SKILL, {"skill": "scout-dream", "runner": "custom-runner.sh"})
    dispatch(trigger, _event(), vault=fake_data_dir, spawn=lambda cmd, env: spawned.append((cmd, env)) or 1)
    assert spawned[0][0][0].endswith("custom-runner.sh")


# ----- interactive ------------------------------------------------------------------


def test_interactive_appends_needs_attention_artifact(fake_data_dir: Path):
    telegram = _FakeTelegram()
    trigger = _trigger(ActionKind.INTERACTIVE, {"preload": "review_pr"}, id="gh_review", source="github")
    outcome = dispatch(trigger, _event(), vault=fake_data_dir, send_telegram=telegram)

    assert outcome.status == "ok"
    artifact = fake_data_dir / "needs-attention.md"
    assert artifact.exists()
    text = artifact.read_text()
    assert "gh_review" in text
    assert "1782910800.000200" in text

    # Interactive pushes are loud (action_required).
    assert telegram.calls and telegram.calls[0]["tier"] == "action_required"

    # A second fire appends, never overwrites.
    dispatch(trigger, _event(), vault=fake_data_dir, send_telegram=telegram)
    assert artifact.read_text().count("## ") == 2


def test_interactive_without_telegram_is_still_ok(fake_data_dir: Path):
    trigger = _trigger(ActionKind.INTERACTIVE)
    outcome = dispatch(trigger, _event(), vault=fake_data_dir, send_telegram=_FakeTelegram(fail=True))
    assert outcome.status == "ok"  # artifact written; push is best-effort
    assert (fake_data_dir / "needs-attention.md").exists()


# ----- fire log ----------------------------------------------------------------------


def test_log_fire_appends_utc_dated_jsonl(fake_data_dir: Path):
    log_dir = fake_data_dir / ".scout-logs"
    outcome = FireOutcome(
        trigger_id="slack_mention_alex",
        event_id="1782910800.000200",
        action_kind="notify",
        status="ok",
        detail={"surfaces": {"telegram": "sent"}},
        ts="2026-07-01T13:05:00.000Z",
    )
    log_fire(log_dir, outcome)
    log_path = log_dir / "trigger-fires-2026-07-01.jsonl"
    assert log_path.exists()
    row = json.loads(log_path.read_text().splitlines()[0])
    assert row["trigger_id"] == "slack_mention_alex"
    assert row["event_id"] == "1782910800.000200"
    assert row["status"] == "ok"
    assert row["ts"] == "2026-07-01T13:05:00.000Z"
