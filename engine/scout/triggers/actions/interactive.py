"""``interactive`` action: write a needs-attention artifact + loud push.

Scout shouldn't decide autonomously here — the fire produces a structured
block in ``<vault>/needs-attention.md`` that ``/scout-work`` walks through,
plus a best-effort ``action_required`` push so the user knows something is
waiting. Zero LLM spend until the user acts.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from scout.triggers.actions._summary import summarize
from scout.triggers.actions.notify import _default_send_telegram
from scout.triggers.config import Trigger
from scout.triggers.sources.base import ConnectorEvent

ARTIFACT_FILENAME = "needs-attention.md"
_HEADER = "# Needs attention\n\nTrigger fires waiting on you. Work through them with `/scout-work`.\n"


def run(
    trigger: Trigger,
    event: ConnectorEvent,
    *,
    vault: Path,
    send_telegram: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    send_telegram = send_telegram or _default_send_telegram
    artifact = vault / ARTIFACT_FILENAME
    summary = summarize(trigger, event)

    block_lines = [
        f"## {event.ts} — trigger `{trigger.id}`",
        "",
        f"- source: {event.source} / {event.match_type}",
        f"- event id: `{event.source_event_id}`",
        f"- summary: {summary}",
    ]
    link = event.normalized_match_fields.get("permalink") or event.normalized_match_fields.get("url")
    if link:
        block_lines.append(f"- link: {link}")
    preload = trigger.action.params.get("preload")
    if preload:
        block_lines.append(f"- preload: `{preload}`")
    block_lines += ["- next: open an interactive session and run `/scout-work`", ""]

    if not artifact.exists():
        artifact.write_text(_HEADER + "\n", encoding="utf-8")
    with artifact.open("a", encoding="utf-8") as f:
        f.write("\n".join(block_lines) + "\n")

    # The push is best-effort — the artifact is the durable surface.
    notified: list[str] = []
    try:
        send_telegram(tier="action_required", body=f"{summary}\n→ waiting in {ARTIFACT_FILENAME}")
        notified.append("telegram")
    except Exception:  # noqa: BLE001 — missing secrets must not lose the artifact
        pass

    return {"artifact": str(artifact), "notified": notified}
