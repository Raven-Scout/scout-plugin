"""GitHub trigger source — polls ``gh api notifications``.

One ``gh`` call per tick returns every unread notification thread since the
scan timestamp; the notification ``reason`` maps 1:1 onto the match types
below. Requires an authenticated ``gh`` CLI (same prerequisite as the
``github`` connector probe).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

from scout.errors import ConfigError
from scout.triggers.sources.base import ConnectorEvent

# GitHub notification `reason` values we normalize into match types.
SUPPORTED_MATCH_TYPES: list[str] = [
    "assign",
    "author",
    "comment",
    "mention",
    "review_requested",
    "state_change",
    "subscribed",
    "team_mention",
]

GH_TIMEOUT_SECONDS = 30


def _default_run_gh(args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return 127, "", "gh: command not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"gh {' '.join(args)}: timed out after {GH_TIMEOUT_SECONDS}s"
    return proc.returncode, proc.stdout, proc.stderr


class GitHubSource:
    """Poll GitHub notification threads for the authenticated user."""

    name = "github"
    SUPPORTED_MATCH_TYPES = SUPPORTED_MATCH_TYPES

    def __init__(self, *, run_gh: Callable[[list[str]], tuple[int, str, str]] | None = None) -> None:
        self._run_gh = run_gh or _default_run_gh

    def scan_since(self, ts: str) -> list[ConnectorEvent]:
        rc, stdout, stderr = self._run_gh(["api", f"notifications?since={ts}&all=false"])
        if rc != 0:
            raise ConfigError(f"gh api notifications failed (exit {rc}): {stderr.strip() or stdout.strip()}")
        try:
            threads = json.loads(stdout or "[]")
        except json.JSONDecodeError as e:
            raise ConfigError(f"gh api notifications returned non-JSON output: {e}") from e
        if not isinstance(threads, list):
            raise ConfigError("gh api notifications: expected a JSON array")

        events: list[ConnectorEvent] = []
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            updated_at = str(thread.get("updated_at", ""))
            # GitHub honors `since` server-side, but re-filter client-side so
            # a lenient/mocked backend can't replay old threads.
            if not updated_at or updated_at <= ts:
                continue
            reason = str(thread.get("reason", ""))
            subject = thread.get("subject") or {}
            repo = (thread.get("repository") or {}).get("full_name", "")
            thread_id = str(thread.get("id", ""))
            events.append(
                ConnectorEvent(
                    source=self.name,
                    # A later update to the same thread is a new event.
                    source_event_id=f"{thread_id}:{updated_at}",
                    ts=updated_at,
                    raw_payload=dict(thread),
                    normalized_match_fields={
                        "type": reason,
                        "reason": reason,
                        "repo": repo,
                        "title": subject.get("title", ""),
                        "subject_type": subject.get("type", ""),
                        "url": subject.get("url", ""),
                        "thread_id": thread_id,
                    },
                )
            )
        events.sort(key=lambda e: e.ts)
        return events

    def health_check(self) -> tuple[bool, str]:
        rc, _stdout, stderr = self._run_gh(["auth", "status"])
        if rc != 0:
            return False, f"gh auth status failed: {stderr.strip() or f'exit {rc}'}"
        return True, "ok"

    def supports_webhook(self) -> bool:
        return False
