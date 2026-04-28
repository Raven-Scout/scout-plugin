"""Telegram Bot API outbound — wraps sendMessage behind ``scoutctl notify telegram``.

Reads ``~/.scout-secrets/telegram-bot-token`` and ``~/.scout-secrets/telegram-chat-id``
(both gitignored, mode 600). Tier controls Telegram's ``disable_notification`` flag:

  - ``info``            → ``disable_notification=True``  (silent push)
  - ``action_required`` → ``disable_notification=False`` (loud push)

Returns an :class:`~scout.events.Event` with ``kind="notification.sent"`` and
``source="cli:notify_telegram"``. Payload shape::

    {"tier": <tier>, "channel": "telegram", "body_chars": <int>, "dry_run": <bool>?}

Used by the Claude session at session-wrap from inside its prompt (via the Bash
tool):

    scoutctl notify telegram --tier action_required --body "..."

This fans out the wrap message to Telegram in addition to Slack DM. The runner
itself stays bash-only in Plan 4; Plan 7 will Pythonize the runner.

Bidirectional Telegram (a return-bridge for inbound replies as feedback signals)
is v0.7+ territory per the event-architecture spec. v0.4 ships the outbound
stub.

**Dry-run semantics.** ``--dry-run`` still reads the secrets so a missing
install fails fast (operator wants to verify, not stub past). It then echoes
what would be POSTed and returns an Event with ``dry_run=True`` instead of
hitting the network.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import requests

from scout.errors import ConfigError
from scout.events import Event, now_iso
from scout.ids import new_ulid

TELEGRAM_API = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096  # Telegram hard limit per sendMessage
SECRETS_DIR = Path.home() / ".scout-secrets"

VALID_TIERS = ("info", "action_required")
DEFAULT_TIMEOUT = 10.0


# ----- secret loading ------------------------------------------------------


def _read_secret(name: str) -> str:
    """Read a secret file from ``SECRETS_DIR``.

    Raises ``ConfigError`` (exit code 10) with an actionable message if the
    file is missing or unreadable. The error names the file path so the
    operator knows exactly what to create.
    """
    path = SECRETS_DIR / name
    if not path.exists():
        raise ConfigError(
            f"Missing secret: {path}. Create it (mode 600) — see engine/scout/docs/connectors/telegram-setup.md."
        )
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise ConfigError(f"Could not read {path}: {e}") from e
    if not value:
        raise ConfigError(f"Secret file is empty: {path}.")
    return value


# ----- body splitting ------------------------------------------------------


def _split_message(body: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split a body across chunks, each ≤ ``limit`` chars.

    Boundary preference, in order: ``\\n\\n`` (paragraph), ``\\n`` (line),
    ``' '`` (word), then a hard cut at ``limit``. Always returns at least
    one chunk; the empty string returns ``[""]`` (callers should reject
    empty bodies upstream — this function does not).

    The boundary delimiter is consumed by the split (i.e. ``\\n\\n`` between
    two chunks is not preserved in either side). Hard cuts preserve every
    character.
    """
    if len(body) <= limit:
        return [body]

    chunks: list[str] = []
    remaining = body
    while len(remaining) > limit:
        window = remaining[:limit]
        # Prefer paragraph boundary (consume the \n\n).
        cut: int | None = None
        consumed = 0
        idx = window.rfind("\n\n")
        if idx > 0:
            cut = idx
            consumed = 2
        else:
            idx = window.rfind("\n")
            if idx > 0:
                cut = idx
                consumed = 1
            else:
                idx = window.rfind(" ")
                if idx > 0:
                    cut = idx
                    consumed = 1
        if cut is None:
            # Hard cut — no breakable char in the window.
            chunks.append(window)
            remaining = remaining[limit:]
        else:
            chunks.append(remaining[:cut])
            remaining = remaining[cut + consumed :]
    if remaining:
        chunks.append(remaining)
    return chunks


# ----- send ----------------------------------------------------------------


def _build_payload(*, chat_id: str, text: str, tier: str) -> dict[str, Any]:
    """Build the JSON body for a single sendMessage call."""
    return {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": tier == "info",
    }


def send(
    tier: str,
    body: str,
    *,
    dry_run: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> Event:
    """Send a message via the Telegram Bot API and return an :class:`Event`.

    Validates ``tier`` and ``body``, reads the bot token + chat-id, splits
    long bodies, then issues one POST per chunk (or echoes the request body
    on ``dry_run``). Defense-in-depth: the CLI layer ALSO restricts ``--tier``
    via Typer choices, but this layer rejects unknown tiers in case a Python
    caller bypasses the CLI.

    Raises:
        ValueError: empty body or unknown tier.
        ConfigError: missing secret files (exit code 10).
        requests.RequestException: HTTP-layer failure during a real send.
            Bubbles up so the caller knows the send did not complete.
    """
    if not body:
        raise ValueError("body cannot be empty")
    if tier not in VALID_TIERS:
        raise ValueError(f"unknown tier {tier!r}; valid tiers are: {', '.join(VALID_TIERS)}")

    token = _read_secret("telegram-bot-token")
    chat_id = _read_secret("telegram-chat-id")

    chunks = _split_message(body)
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

    if dry_run:
        # Dry-run preamble goes to STDERR so stdout stays pure JSON for
        # downstream parsers (the CLI prints the Event to stdout after).
        for chunk in chunks:
            payload = _build_payload(chat_id=chat_id, text=chunk, tier=tier)
            print(f"[dry-run] POST {url}", file=sys.stderr)
            print(
                f"[dry-run] body: {json.dumps(payload, ensure_ascii=False)}",
                file=sys.stderr,
            )
        return Event(
            id=new_ulid(),
            ts=now_iso(),
            kind="notification.sent",
            source="cli:notify_telegram",
            payload={
                "tier": tier,
                "channel": "telegram",
                "body_chars": len(body),
                "dry_run": True,
            },
        )

    for chunk in chunks:
        payload = _build_payload(chat_id=chat_id, text=chunk, tier=tier)
        resp = requests.post(url, json=payload, timeout=timeout)
        # Surface HTTP errors so the caller sees a non-zero exit. The CLI
        # layer maps requests.RequestException to a non-zero exit code.
        resp.raise_for_status()

    return Event(
        id=new_ulid(),
        ts=now_iso(),
        kind="notification.sent",
        source="cli:notify_telegram",
        payload={
            "tier": tier,
            "channel": "telegram",
            "body_chars": len(body),
        },
    )


# ----- CLI entry -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Direct-call entry point (rarely used; the Typer CLI is the real path).

    - ConfigError → exit 10 (default ScoutError code; runner needs to know).
    - ValueError → exit 1.
    - HTTP errors → exit 2.
    - Success → exit 0; the Event JSON is printed to stdout.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="notify-telegram")
    parser.add_argument("--tier", default="info", choices=list(VALID_TIERS))
    parser.add_argument("--body", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        ev = send(tier=args.tier, body=args.body, dry_run=args.dry_run)
    except ConfigError as e:
        print(f"notify-telegram: {e}")
        return ConfigError.exit_code
    except ValueError as e:
        print(f"notify-telegram: {e}")
        return 1
    except requests.RequestException as e:
        print(f"notify-telegram: HTTP error: {e}")
        return 2

    print(json.dumps(asdict(ev), indent=2))
    return 0
