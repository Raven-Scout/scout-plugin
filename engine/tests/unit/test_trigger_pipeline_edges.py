"""Remaining branches across the trigger pipeline's dedup store, engine and
action handlers.

`test_triggers_dedup.py` / `test_triggers_evaluate.py` / `test_triggers_dispatch.py`
cover the mainline. What's left is the "the host misbehaved" set: a corrupt
dedup cache, an unwritable dedup cache, a source that raises at construction
time, a truncated summary, the optional `link`/`preload` lines, and the real
detached spawn in `run_skill`.

Dedup is the at-most-once guard on automated actions — a mis-handled corrupt
cache means either duplicate fires or a permanently wedged trigger.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scout.errors import ConfigError
from scout.triggers import dedup as dedup_mod
from scout.triggers import engine as engine_mod
from scout.triggers.actions import run_skill as run_skill_mod
from scout.triggers.actions._summary import MAX_SUMMARY_CHARS, summarize
from scout.triggers.config import Action, ActionKind, Trigger
from scout.triggers.dedup import DedupStore
from scout.triggers.sources.base import ConnectorEvent

NOW = dt.datetime(2026, 5, 28, 14, 0, tzinfo=dt.UTC)


def _trigger(trigger_id: str = "t1", *, kind: str = "notify", **params) -> Trigger:
    return Trigger(
        id=trigger_id,
        source="scout_internal",
        match={"type": "slot.fire_failed"},
        action=Action(kind=ActionKind(kind), params=params),
        daily_fire_cap=3,
    )


def _event(**fields: Any) -> ConnectorEvent:
    return ConnectorEvent(
        source="scout_internal",
        source_event_id=fields.pop("event_id", "01HXAAA0000000000000000000"),
        ts="2026-05-28T13:59:00Z",
        raw_payload={},
        normalized_match_fields={"type": "slot.fire_failed", **fields},
    )


# ---------------------------------------------------------------------------
# _parse_iso_z
# ---------------------------------------------------------------------------


def test_parse_iso_z_accepts_both_offset_forms() -> None:
    assert dedup_mod._parse_iso_z("2026-05-28T14:00:00Z") == dedup_mod._parse_iso_z("2026-05-28T14:00:00+00:00")


# ---------------------------------------------------------------------------
# DedupStore load tolerance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "{torn",
        '["a", "list"]',
        "null",
        "42",
        '"a string"',
    ],
)
def test_a_corrupt_dedup_cache_starts_fresh(tmp_path: Path, body: str) -> None:
    """At-least-once is the documented contract: a corrupt cache re-fires
    rather than wedging the trigger forever."""
    path = tmp_path / "trigger-fires.json"
    path.write_text(body, encoding="utf-8")
    store = DedupStore(path)
    assert store.is_new("t1", "e1") is True
    assert store.state("t1") == {}


def test_non_dict_entries_are_dropped_on_load(tmp_path: Path) -> None:
    path = tmp_path / "trigger-fires.json"
    path.write_text(json.dumps({"t1": {"last_seen_event_id": "e1"}, "t2": "oops", "t3": [1, 2]}), encoding="utf-8")
    store = DedupStore(path)
    assert store.is_new("t1", "e1") is False
    assert store.is_new("t2", "anything") is True
    assert store.is_new("t3", "anything") is True


def test_a_missing_dedup_cache_starts_fresh(tmp_path: Path) -> None:
    assert DedupStore(tmp_path / "nope.json").is_new("t1", "e1") is True


# ---------------------------------------------------------------------------
# in_cooldown
# ---------------------------------------------------------------------------


def test_cooldown_is_off_when_the_window_is_zero_or_negative(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "d.json")
    store.record_fire("t1", "e1", NOW)
    assert store.in_cooldown("t1", 0, NOW) is False
    assert store.in_cooldown("t1", -5, NOW) is False


def test_cooldown_is_off_for_a_trigger_that_never_fired(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "d.json")
    assert store.in_cooldown("t1", 3600, NOW) is False


def test_cooldown_is_off_when_the_stored_timestamp_is_unparseable(tmp_path: Path) -> None:
    """A corrupt `last_fire_ts` must not hold a trigger in cooldown forever."""
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"t1": {"last_fire_ts": "not-a-date"}}), encoding="utf-8")
    assert DedupStore(path).in_cooldown("t1", 3600, NOW) is False


def test_cooldown_is_off_when_the_stored_timestamp_is_blank(tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"t1": {"last_fire_ts": ""}}), encoding="utf-8")
    assert DedupStore(path).in_cooldown("t1", 3600, NOW) is False


def test_cooldown_holds_inside_the_window_and_lifts_after(tmp_path: Path) -> None:
    store = DedupStore(tmp_path / "d.json")
    store.record_fire("t1", "e1", NOW)
    assert store.in_cooldown("t1", 3600, NOW + dt.timedelta(minutes=30)) is True
    assert store.in_cooldown("t1", 3600, NOW + dt.timedelta(minutes=61)) is False


# ---------------------------------------------------------------------------
# _save tolerance
# ---------------------------------------------------------------------------


def test_a_failed_dedup_save_never_raises_and_leaves_no_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dispatcher already fired the action by this point; a cache-write
    failure must not turn a successful fire into a traceback."""
    path = tmp_path / "d.json"
    store = DedupStore(path)

    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.name.endswith(".json.tmp"):
            raise OSError("read-only filesystem")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    store.record_fire("t1", "e1", NOW)  # must not raise

    assert not path.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_dedup_save_creates_the_parent_dir(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state" / "trigger-fires.json"
    store = DedupStore(path)
    store.record_fire("t1", "e1", NOW)
    assert path.is_file()
    assert json.loads(path.read_text())["t1"]["last_seen_event_id"] == "e1"


# ---------------------------------------------------------------------------
# engine — unavailable source
# ---------------------------------------------------------------------------


def _seed_triggers_yaml(vault: Path) -> None:
    state = vault / ".scout-state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "triggers.yaml").write_text(
        "schema_version: 1\n"
        "triggers:\n"
        "  - id: t1\n"
        "    source: scout_internal\n"
        "    match: {type: slot.fire_failed}\n"
        "    action: {kind: notify, tier: info, body: hi}\n"
        "    daily_fire_cap: 3\n",
        encoding="utf-8",
    )


def test_an_unavailable_source_is_reported_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One broken connector must not stop the tick from evaluating the others —
    the error is collected and the loop continues."""
    _seed_triggers_yaml(tmp_path)

    def boom(_name: str, *, vault: Path):
        raise ConfigError("slack trigger source: user_slack_id is not configured")

    # engine.py does `from ... import get_source`, so patch its own binding.
    monkeypatch.setattr(engine_mod, "get_source", boom)

    result = engine_mod.evaluate(vault=tmp_path, now=NOW)
    assert result.fired == []
    assert len(result.errors) == 1
    assert result.errors[0]["source"] == "scout_internal"
    assert "unavailable:" in result.errors[0]["error"]


def test_a_source_the_seam_dict_does_not_hold_is_reported(tmp_path: Path) -> None:
    """The `sources` seam is a plain dict; a trigger naming a key it lacks
    raises KeyError, which must surface as a collected error."""
    _seed_triggers_yaml(tmp_path)
    result = engine_mod.evaluate(vault=tmp_path, now=NOW, sources={})
    assert result.errors and "unavailable:" in result.errors[0]["error"]


# ---------------------------------------------------------------------------
# _summary
# ---------------------------------------------------------------------------


def test_summary_prefers_the_first_populated_content_field() -> None:
    assert "the text" in summarize(_trigger(), _event(text="the text", title="the title"))
    assert "the title" in summarize(_trigger(), _event(title="the title", slot_key="morning"))
    assert "morning" in summarize(_trigger(), _event(slot_key="morning", reason="because"))
    assert "because" in summarize(_trigger(), _event(reason="because"))


def test_summary_falls_back_to_the_event_id() -> None:
    out = summarize(_trigger(), _event(event_id="01HXBBB0000000000000000000"))
    assert out.endswith("01HXBBB0000000000000000000")


def test_summary_is_truncated_with_an_ellipsis() -> None:
    """This string goes into a Telegram push and a markdown artifact; an
    unbounded event body would blow both budgets."""
    out = summarize(_trigger(), _event(text="x" * 1000))
    content = out.split(": ", 1)[1]
    assert len(content) == MAX_SUMMARY_CHARS
    assert content.endswith("…")


def test_summary_carries_the_trigger_and_source_labels() -> None:
    out = summarize(_trigger("nightly-check"), _event(text="hi"))
    assert out.startswith("[trigger:nightly-check] scout_internal/slot.fire_failed: ")


# ---------------------------------------------------------------------------
# interactive action — the optional lines
# ---------------------------------------------------------------------------


def test_interactive_artifact_includes_a_permalink_when_present(tmp_path: Path) -> None:
    from scout.triggers.actions.interactive import ARTIFACT_FILENAME, run

    run(
        _trigger(kind="interactive"),
        _event(text="ping", permalink="https://acme-co.slack.com/archives/C0123456789/p1700000000000000"),
        vault=tmp_path,
        send_telegram=lambda **_k: None,
    )
    text = (tmp_path / ARTIFACT_FILENAME).read_text()
    assert "- link: https://acme-co.slack.com/archives/C0123456789/p1700000000000000" in text


def test_interactive_artifact_falls_back_to_url_for_the_link(tmp_path: Path) -> None:
    from scout.triggers.actions.interactive import ARTIFACT_FILENAME, run

    run(
        _trigger(kind="interactive"),
        _event(text="ping", url="https://api.github.com/repos/example-org/widgets/pulls/42"),
        vault=tmp_path,
        send_telegram=lambda **_k: None,
    )
    assert "- link: https://api.github.com/" in (tmp_path / ARTIFACT_FILENAME).read_text()


def test_interactive_artifact_omits_the_link_line_when_there_is_none(tmp_path: Path) -> None:
    from scout.triggers.actions.interactive import ARTIFACT_FILENAME, run

    run(
        _trigger(kind="interactive"),
        _event(text="ping"),
        vault=tmp_path,
        send_telegram=lambda **_k: None,
    )
    assert "- link:" not in (tmp_path / ARTIFACT_FILENAME).read_text()


# ---------------------------------------------------------------------------
# run_skill — the real detached spawn
# ---------------------------------------------------------------------------


def test_default_spawn_detaches_and_silences_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trigger fires from a 5-minute dispatcher tick; the runner must outlive
    that tick and must not write into its stdout."""
    seen: dict[str, object] = {}

    class FakePopen:
        pid = 4711

        def __init__(self, cmd, **kwargs):
            seen["cmd"] = cmd
            seen.update(kwargs)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    pid = run_skill_mod._default_spawn(["/vault/run-scout.sh"], {"SCOUT_DATA_DIR": "/vault"})
    assert pid == 4711
    assert seen["cmd"] == ["/vault/run-scout.sh"]
    assert seen["cwd"] == "/vault"
    assert seen["start_new_session"] is True
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["stdout"] is subprocess.DEVNULL
    assert seen["stderr"] is subprocess.DEVNULL


def test_default_spawn_tolerates_a_missing_scout_data_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **k: seen.update(k) or type("P", (), {"pid": 1})())
    run_skill_mod._default_spawn(["/bin/true"], {})
    assert seen["cwd"] is None


def test_default_spawn_really_runs_a_script(tmp_path: Path) -> None:
    """One unmocked spawn, to prove the Popen wiring works end-to-end."""
    import os

    marker = tmp_path / "ran"
    script = tmp_path / "run.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    script.chmod(0o755)

    pid = run_skill_mod._default_spawn([str(script)], {"SCOUT_DATA_DIR": str(tmp_path), "PATH": os.environ["PATH"]})
    assert pid > 0
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        pass
    assert marker.exists()


def test_safe_filename_strips_path_and_shell_characters() -> None:
    """The event id becomes a filename under `.scout-cache/trigger-events/`;
    a Slack `ts` or a GitHub thread id can contain `/`, `.` and `:`."""
    assert run_skill_mod._safe_filename("t1-1782910800.000200") == "t1-1782910800.000200"
    assert run_skill_mod._safe_filename("t1-../../etc/passwd") == "t1-.._.._etc_passwd"
    assert run_skill_mod._safe_filename("t1-a b;c") == "t1-a_b_c"


def test_write_event_payload_records_the_whole_event(tmp_path: Path) -> None:
    path = run_skill_mod.write_event_payload(_trigger("nightly"), _event(text="ping", event_id="e1"), vault=tmp_path)
    assert path == tmp_path / run_skill_mod.EVENT_PAYLOAD_DIR / "nightly-e1.json"
    payload = json.loads(path.read_text())
    assert payload["trigger_id"] == "nightly"
    assert payload["source"] == "scout_internal"
    assert payload["event"]["normalized_match_fields"]["text"] == "ping"
    assert payload["event"]["ts"] == "2026-05-28T13:59:00Z"
