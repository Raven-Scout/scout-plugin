"""triggers.evaluate(): the per-tick polling loop (combined-query + dedup + caps).

Fixtures are synthetic/anonymized per CLAUDE.md.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import yaml

from scout.triggers.engine import evaluate
from scout.triggers.sources.base import ConnectorEvent

NOW = dt.datetime(2026, 7, 1, 16, 0, 0, tzinfo=dt.UTC)  # 12:00 ET
SKILLS = {"scout-dream"}


class FakeSource:
    """Scriptable TriggerSource stand-in; records scan_since calls."""

    SUPPORTED_MATCH_TYPES = ["mention"]

    def __init__(
        self, name: str, events: list[ConnectorEvent], *, healthy: bool = True, error: Exception | None = None
    ):
        self.name = name
        self.events = events
        self.healthy = healthy
        self.error = error
        self.scan_calls: list[str] = []

    def scan_since(self, ts: str) -> list[ConnectorEvent]:
        self.scan_calls.append(ts)
        if self.error is not None:
            raise self.error
        return self.events

    def health_check(self) -> tuple[bool, str]:
        return (True, "ok") if self.healthy else (False, "token missing")

    def supports_webhook(self) -> bool:
        return False


def _mention(event_id: str, *, author: str = "priya", text: str = "ping") -> ConnectorEvent:
    return ConnectorEvent(
        source="slack",
        source_event_id=event_id,
        ts="2026-07-01T15:55:00.000Z",
        raw_payload={},
        normalized_match_fields={"type": "mention", "author": author, "text": text, "is_self": False},
    )


def _write_triggers(vault: Path, triggers: list[dict]) -> None:
    state = vault / ".scout-state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "triggers.yaml").write_text(yaml.safe_dump({"schema_version": 1, "triggers": triggers}), encoding="utf-8")


def _notify_trigger(**overrides) -> dict:
    t = {
        "id": "slack_mention_alex",
        "source": "slack",
        "match": {"type": "mention"},
        "action": {"kind": "notify", "via": ["telegram"]},
        "cooldown_seconds": 0,
        "daily_fire_cap": 10,
    }
    t.update(overrides)
    return t


class _Telegram:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, *, tier: str, body: str):
        self.calls.append({"tier": tier, "body": body})


def _evaluate(vault: Path, sources: dict, *, telegram=None, emitted=None, now=NOW, **kw):
    def emit_event(**kwargs):
        if emitted is not None:
            emitted.append(kwargs)

    kw.setdefault("installed_skills", SKILLS)
    return evaluate(
        vault=vault,
        now=now,
        sources=sources,
        send_telegram=telegram or _Telegram(),
        emit_event=emit_event,
        **kw,
    )


# ----- basics -------------------------------------------------------------------


def test_no_triggers_file_is_a_noop(fake_data_dir: Path):
    result = _evaluate(fake_data_dir, {})
    assert result.fired == []
    assert result.triggers_evaluated == 0
    assert not (fake_data_dir / ".scout-cache" / "trigger-fires.json").exists()


def test_matching_event_fires_and_is_logged(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    telegram = _Telegram()
    emitted: list[dict] = []
    src = FakeSource("slack", [_mention("ev-1", text="ping <@U0123456789>")])

    result = _evaluate(fake_data_dir, {"slack": src}, telegram=telegram, emitted=emitted)

    assert len(result.fired) == 1
    assert result.fired[0].status == "ok"
    assert telegram.calls and "ping" in telegram.calls[0]["body"]

    # One combined scan per source per tick.
    assert len(src.scan_calls) == 1

    # Audit log row written under .scout-logs/trigger-fires-<date>.jsonl.
    log_files = list((fake_data_dir / ".scout-logs").glob("trigger-fires-*.jsonl"))
    assert len(log_files) == 1
    row = json.loads(log_files[0].read_text().splitlines()[0])
    assert row["trigger_id"] == "slack_mention_alex"

    # trigger.fired emitted into the engine event stream.
    fired_events = [e for e in emitted if e["kind"] == "trigger.fired"]
    assert len(fired_events) == 1
    assert fired_events[0]["source"] == "trigger:slack_mention_alex"
    assert fired_events[0]["payload"]["event_id"] == "ev-1"


def test_same_event_does_not_refire_on_next_tick(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    events = [_mention("ev-1")]
    first = _evaluate(fake_data_dir, {"slack": FakeSource("slack", events)})
    second = _evaluate(fake_data_dir, {"slack": FakeSource("slack", events)}, now=NOW + dt.timedelta(minutes=5))
    assert len(first.fired) == 1
    assert second.fired == []


def test_non_matching_event_does_not_fire(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger(match={"type": "mention", "author": "sam"})])
    result = _evaluate(fake_data_dir, {"slack": FakeSource("slack", [_mention("ev-1", author="priya")])})
    assert result.fired == []
    assert result.events_scanned == 1


def test_one_event_can_fire_multiple_triggers_independently(fake_data_dir: Path):
    _write_triggers(
        fake_data_dir,
        [_notify_trigger(), _notify_trigger(id="slack_mention_backup")],
    )
    result = _evaluate(fake_data_dir, {"slack": FakeSource("slack", [_mention("ev-1")])})
    assert sorted(o.trigger_id for o in result.fired) == ["slack_mention_alex", "slack_mention_backup"]


def test_disabled_trigger_is_ignored(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger(enabled=False)])
    src = FakeSource("slack", [_mention("ev-1")])
    result = _evaluate(fake_data_dir, {"slack": src})
    assert result.fired == []
    # No enabled trigger for the source → don't even pay the connector call.
    assert src.scan_calls == []


# ----- cooldown / caps -----------------------------------------------------------


def test_cooldown_blocks_second_fire_within_gap(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger(cooldown_seconds=3600)])
    result = _evaluate(
        fake_data_dir,
        {"slack": FakeSource("slack", [_mention("ev-1"), _mention("ev-2")])},
    )
    assert len(result.fired) == 1


def test_daily_cap_throttles_and_notifies_once(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger(daily_fire_cap=1)])
    telegram = _Telegram()
    emitted: list[dict] = []
    result = _evaluate(
        fake_data_dir,
        {"slack": FakeSource("slack", [_mention("ev-1"), _mention("ev-2"), _mention("ev-3")])},
        telegram=telegram,
        emitted=emitted,
    )
    assert len(result.fired) == 1
    assert len(result.throttled) == 1

    throttle_events = [e for e in emitted if e["kind"] == "trigger.throttled"]
    assert len(throttle_events) == 1
    cap_notices = [c for c in telegram.calls if "cap" in c["body"]]
    assert len(cap_notices) == 1

    # Next tick, still over cap: stays throttled but does NOT re-notify.
    telegram2 = _Telegram()
    emitted2: list[dict] = []
    again = _evaluate(
        fake_data_dir,
        {"slack": FakeSource("slack", [_mention("ev-4")])},
        telegram=telegram2,
        emitted=emitted2,
        now=NOW + dt.timedelta(minutes=5),
    )
    assert again.fired == []
    assert len(again.throttled) == 1
    assert [c for c in telegram2.calls if "cap" in c["body"]] == []


# ----- failure isolation ------------------------------------------------------------


def test_unhealthy_source_is_skipped_and_recorded(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    src = FakeSource("slack", [_mention("ev-1")], healthy=False)
    result = _evaluate(fake_data_dir, {"slack": src})
    assert result.fired == []
    assert result.skipped_sources == [{"source": "slack", "reason": "token missing"}]
    assert src.scan_calls == []


def test_source_scan_error_does_not_break_other_sources(fake_data_dir: Path):
    _write_triggers(
        fake_data_dir,
        [
            _notify_trigger(),
            _notify_trigger(id="gh_reviews", source="github", match={"type": "review_requested"}),
        ],
    )
    gh_event = ConnectorEvent(
        source="github",
        source_event_id="1001:2026-07-01T15:58:00Z",
        ts="2026-07-01T15:58:00Z",
        raw_payload={},
        normalized_match_fields={"type": "review_requested", "repo": "example-org/widget-factory"},
    )
    broken = FakeSource("slack", [], error=RuntimeError("rate limited"))
    working = FakeSource("github", [gh_event])
    result = _evaluate(fake_data_dir, {"slack": broken, "github": working})

    assert [o.trigger_id for o in result.fired] == ["gh_reviews"]
    assert len(result.errors) == 1
    assert result.errors[0]["source"] == "slack"


def test_invalid_triggers_yaml_is_reported_not_raised(fake_data_dir: Path):
    state = fake_data_dir / ".scout-state"
    state.mkdir(exist_ok=True)
    (state / "triggers.yaml").write_text("triggers: [{id: broken}]", encoding="utf-8")
    result = _evaluate(fake_data_dir, {})
    assert result.fired == []
    assert result.errors and "config" in result.errors[0]


def test_scan_window_is_bounded_by_lookback(fake_data_dir: Path):
    _write_triggers(fake_data_dir, [_notify_trigger()])
    src = FakeSource("slack", [])
    _evaluate(fake_data_dir, {"slack": src}, lookback_minutes=60)
    assert src.scan_calls == ["2026-07-01T15:00:00Z"]
