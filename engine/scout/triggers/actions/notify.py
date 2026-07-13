"""``notify`` action: push the matched event to surfaces. No LLM spend.

v1 supports one surface: ``telegram`` (the engine's existing outbound,
``scout.scripts.notify_telegram``). Unknown surfaces are recorded as
unsupported rather than failing the fire — but if NO surface delivers,
the action fails so the fire log shows the notification was lost.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from scout.triggers.actions._summary import summarize
from scout.triggers.config import Trigger
from scout.triggers.sources.base import ConnectorEvent

DEFAULT_SURFACES = ["telegram"]


def _default_send_telegram(*, tier: str, body: str) -> None:
    from scout.scripts.notify_telegram import send

    send(tier=tier, body=body)


def run(
    trigger: Trigger,
    event: ConnectorEvent,
    *,
    vault: Path,
    send_telegram: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    send_telegram = send_telegram or _default_send_telegram
    surfaces = trigger.action.params.get("via") or DEFAULT_SURFACES
    body = summarize(trigger, event)

    results: dict[str, str] = {}
    delivered = 0
    for surface in surfaces:
        if surface == "telegram":
            try:
                send_telegram(tier="info", body=body)
            except Exception as e:  # noqa: BLE001 — per-surface isolation
                results[surface] = f"error: {type(e).__name__}: {e}"
            else:
                results[surface] = "sent"
                delivered += 1
        else:
            results[surface] = "unsupported surface (v1 supports: telegram)"

    if delivered == 0:
        raise RuntimeError(f"notify delivered to no surface: {results}")
    return {"surfaces": results}
