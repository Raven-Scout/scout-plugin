"""schedule_tick ↔ triggers integration: evaluate() runs on every tick, isolated.

Per the spec's event-flow diagram, ``triggers.evaluate()`` runs from the same
5-minute tick as ``schedule.evaluate()`` — before it — and a trigger-side
crash must never take down slot dispatch.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from scout.events import Event
from scout.scripts.schedule_tick import run as tick_run


def _minimal_vault(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    (tmp_path / ".scout-state").mkdir()
    (tmp_path / ".scout-logs").mkdir()
    (tmp_path / ".scout-state" / "schedule.yaml").write_text(
        "schema_version: 1\nslots:\n  smoke-slot:\n"
        "    type: manual\n    runner: run-scout.sh\n    fires_at_local: '00:01'\n"
        "    weekdays: [Mon, Tue, Wed, Thu, Fri, Sat, Sun]\n"
        "    missed_window_hours: 24\n    on_miss: skip\n    cooldown_minutes: 5\n"
    )


def test_tick_evaluates_triggers(tmp_path, monkeypatch):
    _minimal_vault(tmp_path, monkeypatch)
    with (
        patch("scout.scripts.schedule_tick._network_ready", return_value=True),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen,
        patch("scout.triggers.engine.evaluate") as mock_evaluate,
    ):
        mock_popen.return_value.pid = 99999
        ev = tick_run()

    assert isinstance(ev, Event)
    assert ev.kind == "schedule.tick.completed"
    assert mock_evaluate.call_count == 1
    kwargs = mock_evaluate.call_args.kwargs
    assert kwargs["vault"] == tmp_path
    assert kwargs["emit_event"] is not None


def test_trigger_crash_does_not_break_slot_dispatch(tmp_path, monkeypatch):
    _minimal_vault(tmp_path, monkeypatch)
    with (
        patch("scout.scripts.schedule_tick._network_ready", return_value=True),
        patch("scout.scripts.schedule_tick.subprocess.Popen") as mock_popen,
        patch("scout.triggers.engine.evaluate", side_effect=RuntimeError("boom")),
    ):
        mock_popen.return_value.pid = 99999
        ev = tick_run()

    # The tick still completes...
    assert ev.kind == "schedule.tick.completed"

    # ...and the failure is visible in the engine event stream.
    rows = []
    for log in (tmp_path / ".scout-logs").glob("schedule-events-*.jsonl"):
        rows += [json.loads(line) for line in log.read_text().splitlines()]
    failed = [r for r in rows if r["kind"] == "triggers.evaluate.failed"]
    assert len(failed) == 1
    assert "boom" in failed[0]["payload"]["error"]


def test_manifest_declares_triggers_v1_flag():
    from scout.manifest import build_manifest

    m = build_manifest()
    # Opt-in per spec: flips True once polling+dedup are verified live.
    assert m.features["triggers_v1"] is False
