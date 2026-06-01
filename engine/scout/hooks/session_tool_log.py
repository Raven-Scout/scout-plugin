"""Stop hook: reconstruct per-tool-call records from the Claude Code session JSONL.

Replaces the per-PostToolUse Python spawn in ``connector_log`` (#72). The
old hook fired on every tool call, paying ~200-500 ms of Python cold-start
each time. For a 100-tool-call Scout session that was 20-50 seconds of
pure interpreter startup — the dominant CPU sink during a run.

This module fires once at session end (Stop hook), walks the session's
transcript JSONL, pairs ``tool_use`` events with their ``tool_result``
counterparts, and writes the same ``connector-calls-YYYY-MM-DD.jsonl``
rows the PostToolUse hook produced. Output is wire-compatible — the
``connector_health_report`` consumer doesn't notice the change.

Hook contract (Claude Code Stop hook stdin JSON):
  - ``transcript_path``: absolute path to the session JSONL (preferred)
  - ``session_id``: fallback used to derive the path under
    ``~/.claude/projects/<encoded-cwd>/<session_id>.jsonl``

Short-circuits when ``SCOUT_MODE`` is unset (interactive sessions don't
need tool-call accounting). Never raises — hooks must never block a
session.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
from zoneinfo import ZoneInfo

from scout import paths
from scout.events import Event, now_iso
from scout.hooks.connector_log import classify
from scout.ids import new_ulid


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool call reconstructed from a JSONL pair (tool_use + tool_result)."""

    tool_name: str
    tool_input: dict[str, Any]
    tool_response: dict[str, Any]


# ----- transcript discovery ------------------------------------------------


def _resolve_transcript_path(payload: dict[str, Any]) -> Path | None:
    """Pick the transcript path from the Stop-hook payload.

    Order:
      1. ``transcript_path`` if present and exists
      2. ``session_id`` resolved against ``~/.claude/projects/<encoded-cwd>/``
         using ``cwd`` from the payload (Claude Code encodes the cwd's path
         segments with leading-dash + each ``/`` becoming ``-``)
    """
    raw_path = payload.get("transcript_path")
    if isinstance(raw_path, str) and raw_path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_file():
            return candidate

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None

    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd:
        encoded = cwd.replace("/", "-")
        candidate = Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate

    # Last resort — scan every project dir for the session ID. Slow but only
    # used as a fallback when cwd is missing.
    projects = Path.home() / ".claude" / "projects"
    if projects.is_dir():
        for projdir in projects.iterdir():
            candidate = projdir / f"{session_id}.jsonl"
            if candidate.is_file():
                return candidate
    return None


# ----- JSONL parsing -------------------------------------------------------


def _iter_rows(jsonl_path: Path) -> Iterator[dict[str, Any]]:
    """Yield decoded JSON rows from the transcript. Malformed lines are skipped."""
    try:
        with jsonl_path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _tool_response_from_result(result: dict[str, Any]) -> dict[str, Any]:
    """Convert a JSONL ``tool_result`` block to the PostToolUse-shaped dict.

    The PostToolUse hook saw ``tool_response`` with fields like ``isError``,
    ``returncode``, ``error``, ``content`` (sometimes a list of blocks). The
    transcript ``tool_result`` block uses ``is_error`` (snake) and stores
    ``content`` as either a string or a list of typed blocks. We normalise
    so ``connector_log.classify``'s error detection still works.
    """
    out: dict[str, Any] = {}
    if result.get("is_error") is True:
        out["isError"] = True
    raw_content = result.get("content")
    if isinstance(raw_content, list):
        out["content"] = raw_content
    elif isinstance(raw_content, str):
        # Stuff the string into a single text block so callers expecting the
        # list shape don't crash. classify() doesn't read this path, but
        # we keep it compatible for downstream consumers.
        out["content"] = [{"type": "text", "text": raw_content}]
    return out


def extract_tool_calls(rows: Iterable[dict[str, Any]]) -> list[ToolCallRecord]:
    """Walk the transcript and emit one ``ToolCallRecord`` per completed call.

    Pairs ``tool_use`` blocks (in assistant messages) with their matching
    ``tool_result`` blocks (in subsequent user messages) by ``tool_use_id``.
    Tool calls without a matching result are still emitted with an empty
    response — they happened, even if the session ended before the result
    landed.
    """
    pending: dict[str, ToolCallRecord] = {}
    completed: list[tuple[int, ToolCallRecord]] = []
    order: dict[str, int] = {}

    for row in rows:
        msg = row.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, list):
            continue

        if role == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                tool_id = block.get("id")
                if not isinstance(tool_id, str):
                    continue
                rec = ToolCallRecord(
                    tool_name=str(block.get("name") or "unknown"),
                    tool_input=dict(block.get("input") or {}),
                    tool_response={},
                )
                pending[tool_id] = rec
                order[tool_id] = len(order)

        elif role == "user":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                tool_id = block.get("tool_use_id")
                if not isinstance(tool_id, str):
                    continue
                prior = pending.pop(tool_id, None)
                if prior is None:
                    continue
                paired = ToolCallRecord(
                    tool_name=prior.tool_name,
                    tool_input=prior.tool_input,
                    tool_response=_tool_response_from_result(block),
                )
                completed.append((order[tool_id], paired))

    # Tool calls without a matching result — still record them.
    for tool_id, rec in pending.items():
        completed.append((order[tool_id], rec))

    completed.sort(key=lambda t: t[0])
    return [rec for _, rec in completed]


# ----- emit JSONL ----------------------------------------------------------


def _et_date() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _is_error(tool_response: dict[str, Any]) -> tuple[bool, str]:
    """Mirror the error-detection logic from connector_log.run."""
    is_error = False
    err_snippet = ""
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
    return is_error, err_snippet


def write_records(
    records: Iterable[ToolCallRecord],
    *,
    mode: str,
    session_id: str,
    log_dir: Path,
) -> int:
    """Append one JSONL row per record. Returns the number written."""
    if not records:
        return 0
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"connector-calls-{_et_date()}.jsonl"
    written = 0
    try:
        with out_path.open("a", encoding="utf-8") as f:
            ts_utc = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            for rec in records:
                is_err, err_snippet = _is_error(rec.tool_response)
                connector = classify(rec.tool_name, rec.tool_input)
                row: dict[str, Any] = {
                    "ts": ts_utc,
                    "session_id": session_id,
                    "mode": mode,
                    "tool": rec.tool_name,
                    "connector": connector,
                    "error": is_err,
                }
                if err_snippet:
                    row["err"] = err_snippet
                f.write(json.dumps(row) + "\n")
                written += 1
    except OSError:
        pass
    return written


# ----- driver --------------------------------------------------------------


def run(*, stdin: IO[str] | None = None) -> Event | None:
    """Read one Stop-hook payload from stdin and replay its tool calls.

    Returns ``None`` if SCOUT_MODE is unset (interactive session) or if the
    transcript can't be found. Errors are swallowed — hooks must never raise.
    """
    mode = os.environ.get("SCOUT_MODE")
    if not mode:
        return None

    src = stdin if stdin is not None else sys.stdin
    try:
        payload = json.load(src)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    transcript_path = _resolve_transcript_path(payload)
    if transcript_path is None:
        return None

    session_id = str(payload.get("session_id") or transcript_path.stem)
    log_dir = paths.data_dir() / ".scout-logs"

    records = extract_tool_calls(_iter_rows(transcript_path))
    count = write_records(records, mode=mode, session_id=session_id, log_dir=log_dir)

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="session.tool_log.written",
        source="hook:session-tool-log",
        payload={
            "session_id": session_id,
            "mode": mode,
            "transcript_path": str(transcript_path),
            "calls_written": count,
        },
    )


def main() -> int:
    try:
        run()
    except Exception:
        pass
    return 0


__all__ = [
    "ToolCallRecord",
    "extract_tool_calls",
    "main",
    "run",
    "write_records",
]
