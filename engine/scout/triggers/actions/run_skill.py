"""``run_skill`` action: spawn a Scout runner with the event payload attached.

The event payload is written to ``.scout-cache/trigger-events/<...>.json``
and its path exported as ``$SCOUT_TRIGGER_EVENT_PATH`` (spec Open Question
#3) so the skill can read what fired it. The runner (default
``run-scout.sh``, same contract as scheduled slots) is spawned detached
with ``SCOUT_FORCE_MODE=<skill>``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scout.triggers.config import Trigger
from scout.triggers.sources.base import ConnectorEvent

DEFAULT_RUNNER = "run-scout.sh"
EVENT_PAYLOAD_DIR = ".scout-cache/trigger-events"


def _default_spawn(cmd: list[str], env: dict[str, str]) -> int:
    proc = subprocess.Popen(
        cmd,
        cwd=env.get("SCOUT_DATA_DIR"),
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.pid


def _safe_filename(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw)


def write_event_payload(trigger: Trigger, event: ConnectorEvent, *, vault: Path) -> Path:
    payload_dir = vault / EVENT_PAYLOAD_DIR
    payload_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_filename(f"{trigger.id}-{event.source_event_id}") + ".json"
    payload_path = payload_dir / name
    payload = {
        "trigger_id": trigger.id,
        "source": event.source,
        "event": {
            "source_event_id": event.source_event_id,
            "ts": event.ts,
            "normalized_match_fields": event.normalized_match_fields,
            "raw_payload": event.raw_payload,
        },
    }
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload_path


def run(
    trigger: Trigger,
    event: ConnectorEvent,
    *,
    vault: Path,
    spawn: Callable[[list[str], dict[str, str]], int] | None = None,
) -> dict[str, Any]:
    spawn = spawn or _default_spawn
    skill = str(trigger.action.params["skill"])
    runner = str(trigger.action.params.get("runner", DEFAULT_RUNNER))

    payload_path = write_event_payload(trigger, event, vault=vault)

    env = os.environ.copy()
    env["SCOUT_FORCE_MODE"] = skill
    env["SCOUT_DATA_DIR"] = str(vault)
    env["SCOUT_TRIGGER_ID"] = trigger.id
    env["SCOUT_TRIGGER_EVENT_PATH"] = str(payload_path)

    pid = spawn([str(vault / runner)], env)
    return {"skill": skill, "runner": runner, "pid": pid, "event_payload_path": str(payload_path)}
