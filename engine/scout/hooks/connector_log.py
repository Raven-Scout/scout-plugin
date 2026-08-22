"""PostToolUse hook port — appends one JSONL record per tool call.

Direct port of ~/Scout/hooks/connector-log.sh. Behavior identical:
  - Short-circuits when SCOUT_MODE is unset (interactive sessions).
  - Emits one row to .scout-logs/connector-calls-YYYY-MM-DD.jsonl per call.
  - Tag-classifies tool_name → connector key (preserves bash classify() exactly).
  - ET-date-stamps the JSONL filename (TZ=America/New_York).
  - Truncates error snippets at 160 chars (matches bash original).
  - Never raises — hooks must never break a session.

v0.4 returns an Event in addition to writing JSONL; v0.5 will append the
Event to the SQLite event store via the same emit() shape.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from typing import IO, Any
from zoneinfo import ZoneInfo

from scout import paths
from scout.events import Event, now_iso
from scout.ids import new_ulid

try:
    import fcntl
except ImportError:  # non-POSIX (Windows) — advisory locks unavailable.
    fcntl = None  # type: ignore[assignment]


def _lock_exclusive(f: IO[str]) -> None:
    """Take an exclusive advisory lock; no-op where fcntl is unavailable.

    Released implicitly when the file is closed.
    """
    if fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)


# Bash-invoked binaries that ARE connectors, mapped to their canonical key.
# Matched anywhere in the command, not just at position 0 — see _bash_key().
# Deliberately narrow: only binaries the connector-health alerter actually
# probes. `git` is NOT here — it stays `bash:git`, as before.
_BASH_CONNECTORS = {"gh": "github"}


def _bash_key(cmd: str) -> str:
    """Classify a Bash command by the connector binary it actually invokes.

    The first token is not a reliable label: ``cd ~/Scout && gh pr list`` is a
    GitHub call that a first-token reading records as ``bash:cd``. The health
    alerter then sees zero ``github`` rows and fires a false CRITICAL — the run
    did call GitHub, it just phrased the call with a prefix.

    So scan every command position for a known connector binary, splitting on
    the shell operators that start a new command (``&&``, ``||``, ``;``, ``|``,
    newline). Positions after a redirect or inside quotes are not parsed — this
    is a labeller, not a shell — but the prefix forms that actually occur in
    Scout runs are covered.

    Falls back to ``bash:<first-token>`` so non-connector calls label as before.

    Source: scout-mistake-audit Pattern #144, confirmed 2026-08-13 by an
    accidental A/B (same token, same day: three ``cd … && gh …`` calls → CRITICAL,
    five bare ``gh …`` calls → 0 alerts). Approved by Jordan 2026-08-13 21:20 ET.
    """
    if not cmd:
        return "bash"

    segments = cmd.replace("&&", "\n").replace("||", "\n").replace(";", "\n")
    segments = segments.replace("|", "\n")

    first = ""
    for segment in segments.split("\n"):
        tokens = segment.split()
        # Skip a leading env-assignment prefix (FOO=bar cmd …).
        idx = 0
        while idx < len(tokens) and "=" in tokens[idx] and not tokens[idx].startswith("-"):
            idx += 1
        if idx >= len(tokens):
            continue
        head = tokens[idx].rsplit("/", 1)[-1]  # /usr/bin/gh → gh
        if head in _BASH_CONNECTORS:
            return _BASH_CONNECTORS[head]
        if not first:
            first = head

    return f"bash:{first}" if first else "bash"


def classify(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Map a Claude Code tool_name + tool_input to a connector key.

    Ported from connector-log.sh:65-76; the Bash branch now scans for the
    connector binary rather than trusting the first token (Pattern #144).
    """
    if tool_name == "Bash":
        return _bash_key((tool_input.get("command") or "").strip())
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        if len(parts) >= 2:
            return f"mcp:{parts[1]}"
    return tool_name.lower()


def run(*, stdin: IO[str] | None = None) -> Event | None:
    """Read one PostToolUse JSON payload from stdin, write one JSONL row, return Event.

    Returns None if SCOUT_MODE is unset (interactive session) or if stdin is malformed.
    """
    mode = os.environ.get("SCOUT_MODE")
    if not mode:
        return None

    src = stdin if stdin is not None else sys.stdin
    try:
        data = json.load(src)
    except Exception:
        return None  # malformed — never raise from a hook

    tool_name = data.get("tool_name", "unknown")
    tool_input = data.get("tool_input") or {}
    tool_response = data.get("tool_response") or {}
    session_id = data.get("session_id", "")

    is_error = False
    err_snippet = ""
    if isinstance(tool_response, dict):
        if tool_response.get("isError") is True:
            is_error = True
        rc = tool_response.get("returncode")
        if isinstance(rc, int) and rc != 0:
            is_error = True
        if tool_response.get("error"):
            is_error = True
            err_snippet = str(tool_response["error"])[:160]
        content = tool_response.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("isError"):
                    is_error = True
                    if not err_snippet:
                        err_snippet = (item.get("text") or "")[:160]

    connector = classify(tool_name, tool_input)
    ts_utc = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    record: dict[str, Any] = {
        "ts": ts_utc,
        "session_id": session_id,
        "mode": mode,
        "tool": tool_name,
        "connector": connector,
        "error": is_error,
    }
    if err_snippet:
        record["err"] = err_snippet

    et_date = _et_date()
    log_dir = paths.data_dir() / ".scout-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"connector-calls-{et_date}.jsonl"
    line = json.dumps(record) + "\n"
    try:
        with out_path.open("a", encoding="utf-8") as f:
            # Hooks fire concurrently for parallel tool calls. O_APPEND only
            # guarantees atomicity up to PIPE_BUF (~512B on some macOS FS), so
            # large tool-response rows can interleave. An exclusive advisory
            # lock serializes the append; the lock releases on close. (#38)
            _lock_exclusive(f)
            f.write(line)
    except Exception as exc:
        # Never break the session on a log-write failure — but don't swallow
        # it silently either; a dropped row is an unsignalled gap in the
        # audit log. Surface to stderr (the hook's output is captured). (#38)
        print(f"connector-log: failed to append row: {exc!r}", file=sys.stderr)

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="tool.call.logged",
        source="hook:connector-log",
        payload=record,
    )


def _et_date() -> str:
    """Eastern-Time date string YYYY-MM-DD."""
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def main() -> int:
    """CLI entry point: scoutctl hook connector-log."""
    try:
        run()
    except Exception:
        pass  # never break the session
    return 0
