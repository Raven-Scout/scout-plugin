"""Stop-hook port — sums message.usage across a session transcript.

Direct port of ~/Scout/scripts/sum-session-tokens.sh. Reads a Stop-hook JSON
payload on stdin (session_id / transcript_path / cwd), opens the transcript
JSONL, sums per-turn token counts, computes per-turn cost against each turn's
model, and appends one row to .scout-logs/session-tokens.jsonl.

The Swift `SessionTokenEntry` decoder (scout-app/Scout/Models/SessionTokenEntry.swift)
consumes this JSONL — schema MUST stay byte-stable on field names and types.

Phase 1 of the usage-and-connector-health design. Pricing is a static table
(see PRICING_USD_PER_M_TOKENS); Phase 2 will replace dollar display with
quota utilization and the table becomes irrelevant.

Environment:
  SCOUT_MODE             — set by runner scripts. Falls back to "manual"
                           (note: differs from connector_log which short-circuits
                           when unset).
  SESSION_TOKENS_TRACKER — override tracker path (used by tests / parity bats).

Hooks must NEVER raise — main() catches all exceptions and returns 0.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any
from zoneinfo import ZoneInfo

from scout import paths
from scout.events import Event, now_iso
from scout.ids import new_ulid

# PRICING: $ per 1M tokens. Verify against https://www.anthropic.com/pricing
# before shipping; these are the published rates as of 2026-04-22 (verbatim
# from sum-session-tokens.sh:21-23). Phase 2 makes this table irrelevant —
# quota utilization replaces dollar display.
PRICING_USD_PER_M_TOKENS: dict[str, dict[str, float]] = {
    "claude-opus": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_create": 18.75},
    "claude-sonnet": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_create": 3.75},
    "claude-haiku": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_create": 1.00},
}

# Polling for transcript flush. Bash line 61: 6 attempts × 0.5s = 3s ceiling.
# On the happy path the first attempt finds usage and we never sleep.
# Module-level so tests can monkeypatch them down to zero.
_POLL_ATTEMPTS = 6
_POLL_INTERVAL_S = 0.5


def _model_family(model: str | None) -> str:
    """Map a Claude Code model string to its pricing family.

    Mirrors bash lines 99-102: prefix-match against {claude-opus, claude-sonnet,
    claude-haiku}; unknown / empty / None falls back to claude-opus
    (conservative — bash line 102 `else [$oi,$oo,$ocr,$occ]`).
    """
    if not model:
        return "claude-opus"
    for prefix in PRICING_USD_PER_M_TOKENS:
        if model.startswith(prefix):
            return prefix
    return "claude-opus"


def _is_known_model(model: str) -> bool:
    """Whether `model` matches one of the three known families (non-empty)."""
    if not model:
        return False
    return any(model.startswith(p) for p in PRICING_USD_PER_M_TOKENS)


def _utc_ts() -> str:
    """UTC ISO-8601 with `Z` suffix at seconds precision (matches bash line 36)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _et_ts() -> str:
    """ET local timestamp like '2026-04-28 16:00 EDT' (matches bash line 37)."""
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z")


def _tracker_path() -> Path:
    """Resolve the session-tokens.jsonl path.

    Precedence:
      1. SESSION_TOKENS_TRACKER env var (override; used by parity tests).
      2. paths.data_dir() / .scout-logs / session-tokens.jsonl (default).
    """
    override = os.environ.get("SESSION_TOKENS_TRACKER")
    if override:
        return Path(override).expanduser()
    return paths.data_dir() / ".scout-logs" / "session-tokens.jsonl"


def _zero_row(
    *,
    session_id: str,
    scout_mode: str,
    cwd: str,
    error: str,
) -> dict[str, Any]:
    """Build the error / no-data row (bash lines 41-48 and 67-74).

    Same shape as the success row, all numeric fields = 0, primary_model=null,
    error string set to `error`.
    """
    return {
        "ts": _utc_ts(),
        "ts_et": _et_ts(),
        "session_id": session_id,
        "scout_mode": scout_mode,
        "cwd": cwd,
        "primary_model": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cost_usd": 0,
        "num_turns": 0,
        "duration_ms": 0,
        "error": error,
    }


def _read_usage_turns(transcript_path: Path) -> list[dict[str, Any]]:
    """Read the transcript JSONL and return every turn that has message.usage.

    Lines that fail JSON parsing are silently skipped (jq `fromjson?` equivalent
    — see bash line 62). Same for blank lines.
    """
    turns: list[dict[str, Any]] = []
    try:
        with transcript_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message")
                if isinstance(msg, dict) and msg.get("usage") is not None:
                    turns.append(obj)
    except OSError:
        # Treated upstream as transcript_not_found; here we just return empty.
        return []
    return turns


def _poll_for_usage_turns(transcript_path: Path) -> list[dict[str, Any]]:
    """Poll up to _POLL_ATTEMPTS × _POLL_INTERVAL_S for usage turns to appear.

    Bash lines 60-65: on very short sessions Claude Code can fire Stop before
    the final assistant turn lands on disk; a brief poll lets the write flush.
    Zero latency on the happy path (first attempt finds usage and returns).
    """
    for attempt in range(_POLL_ATTEMPTS):
        turns = _read_usage_turns(transcript_path)
        if turns:
            return turns
        if attempt < _POLL_ATTEMPTS - 1 and _POLL_INTERVAL_S > 0:
            time.sleep(_POLL_INTERVAL_S)
    return []


def _compute_cost_usd(turns: list[dict[str, Any]]) -> float:
    """Per-turn cost summation (bash lines 89-107).

    Each turn is priced against ITS OWN model's family, not the primary's.
    Unknown models fall back to Opus. Order of operations matches the bash:
    sum(per_turn_dollar_amounts) where each per_turn_dollar_amount is
    (i*price_in + o*price_out + cr*price_cr + cc*price_cc) / 1_000_000.
    """
    total = 0.0
    for t in turns:
        msg = t.get("message") or {}
        usage = msg.get("usage") or {}
        family = _model_family(msg.get("model"))
        rates = PRICING_USD_PER_M_TOKENS[family]
        i = usage.get("input_tokens") or 0
        o = usage.get("output_tokens") or 0
        cr = usage.get("cache_read_input_tokens") or 0
        cc = usage.get("cache_creation_input_tokens") or 0
        total += (
            i * rates["input"] + o * rates["output"] + cr * rates["cache_read"] + cc * rates["cache_create"]
        ) / 1_000_000
    return total


def _primary_model(turns: list[dict[str, Any]]) -> str:
    """Most-frequent model across usage turns (bash lines 86-87).

    Tie-break: Counter.most_common is stable in iteration order. The bash uses
    jq `group_by | max_by(length)`, which picks the first by sort order — so on
    ties the two implementations may diverge. The fixture deliberately uses
    2 Opus + 1 Sonnet so primary is unambiguous.
    """
    models = [(t.get("message") or {}).get("model") or "" for t in turns]
    counts = Counter(m for m in models if m)
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def _first_unknown_model(turns: list[dict[str, Any]]) -> str:
    """First turn whose model is not in {opus, sonnet, haiku} (bash lines 112-116).

    jq selects all unknowns then takes `.[0]` — so iteration order = transcript
    order. Returns "" if all turns use known models.
    """
    for t in turns:
        model = (t.get("message") or {}).get("model") or ""
        if not _is_known_model(model):
            return model
    return ""


def _append_row(record: dict[str, Any]) -> None:
    """Append one compact JSON line to the tracker. Best-effort — never raises."""
    tracker = _tracker_path()
    try:
        tracker.parent.mkdir(parents=True, exist_ok=True)
        with tracker.open("a", encoding="utf-8") as f:
            # Compact output to match `jq -nc` (bash line 41 etc.).
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _make_event(record: dict[str, Any]) -> Event:
    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="session.tokens.summed",
        source="hook:session-tokens",
        payload=record,
    )


def run(*, stdin: IO[str] | None = None) -> Event | None:
    """Read Stop-hook payload, sum transcript usage, append one row, return Event.

    Returns:
        - Event in all success and recoverable-error paths (transcript_not_found,
          no_usage_turns, unknown_model, success).
        - None only when the stdin payload itself is unparseable (malformed JSON).
    """
    src = stdin if stdin is not None else sys.stdin
    try:
        payload = json.load(src)
    except (json.JSONDecodeError, ValueError):
        return None  # malformed stdin — never raise from a hook

    if not isinstance(payload, dict):
        return None

    session_id = str(payload.get("session_id") or "")
    transcript_path_str = str(payload.get("transcript_path") or "")
    cwd = str(payload.get("cwd") or "")
    scout_mode = os.environ.get("SCOUT_MODE") or "manual"

    # Guard 1: missing / empty transcript path (bash line 40 `[ -z ... ]`).
    if not transcript_path_str:
        record = _zero_row(
            session_id=session_id,
            scout_mode=scout_mode,
            cwd=cwd,
            error="transcript_not_found",
        )
        _append_row(record)
        return _make_event(record)

    transcript_path = Path(transcript_path_str).expanduser()

    # Guard 2: file doesn't exist. Don't poll — go straight to the error row.
    if not transcript_path.is_file():
        record = _zero_row(
            session_id=session_id,
            scout_mode=scout_mode,
            cwd=cwd,
            error="transcript_not_found",
        )
        _append_row(record)
        return _make_event(record)

    # File exists but may still be flushing; poll up to ~3s for usage turns.
    turns = _poll_for_usage_turns(transcript_path)
    if not turns:
        record = _zero_row(
            session_id=session_id,
            scout_mode=scout_mode,
            cwd=cwd,
            error="no_usage_turns",
        )
        _append_row(record)
        return _make_event(record)

    # Vectorize the four token sums in a single pass.
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_create = 0
    for t in turns:
        usage = (t.get("message") or {}).get("usage") or {}
        input_tokens += usage.get("input_tokens") or 0
        output_tokens += usage.get("output_tokens") or 0
        cache_read += usage.get("cache_read_input_tokens") or 0
        cache_create += usage.get("cache_creation_input_tokens") or 0

    primary = _primary_model(turns)
    cost_usd = _compute_cost_usd(turns)
    unknown = _first_unknown_model(turns)
    error = f"unknown_model:{unknown}" if unknown else None

    record = {
        "ts": _utc_ts(),
        "ts_et": _et_ts(),
        "session_id": session_id,
        "scout_mode": scout_mode,
        "cwd": cwd,
        "primary_model": primary,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_create,
        "cost_usd": cost_usd,
        "num_turns": len(turns),
        "duration_ms": 0,  # reserved; bash always writes 0
        "error": error,
    }
    _append_row(record)
    return _make_event(record)


def main() -> int:
    """CLI entry point: scoutctl hook session-tokens. Always returns 0."""
    try:
        run()
    except Exception:
        # Hooks must never break a session.
        pass
    return 0
