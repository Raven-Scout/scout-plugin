"""Telegram Bot API inbound — wraps ``getUpdates`` behind ``scoutctl notify telegram-read``.

The counterpart to :mod:`scout.scripts.notify_telegram`, which is outbound-only.
Without this module a vault that moves its wrap notification to Telegram
**publishes on one surface and harvests feedback from another**, and the
feedback-harvest phase keeps reporting a clean ``0 feedback signals`` that is
true, correctly sourced, and no longer an answer to the question being asked.
See issue #215.

Three properties of this surface, each of which changes how the reader must
behave relative to the Slack path:

1. **Authorship needs no test.** In a private chat ``getUpdates`` returns only
   *incoming* messages — the bot's own sends never come back. The Slack self-DM
   footer test must **not** be applied here or it will discard real feedback.
   ``is_bot`` is still asserted rather than assumed, because a group upgrade
   would change this silently.
2. **Replies arrive with their parent attached** (``reply_to_message``), so one
   call covers standalone messages *and* replies-to-wraps. The two-call
   ``read_channel`` + ``read_thread`` trap does not exist here.
3. **Retention is ~24h, not permanent.** Telegram drops unconsumed updates after
   about a day. A missed harvest **destroys** the signal rather than deferring
   it — materially different from Slack's permanent history, which is why the
   phase fragment orders this read early in the run.

**This reader never consumes.** ``getUpdates`` without an ``offset`` is
idempotent — Telegram keeps the queue. Passing ``offset=<update_id>+1`` deletes
everything up to it *permanently*, with no second read and nothing on disk, so a
consuming reader that died between fetch and write would destroy the only copy
of a signal. There is deliberately no flag for it: every call re-reads the whole
retained window, and the 24h expiry bounds it.

**Empty is not silence.** Four distinct states return an empty list at the call
site — genuine silence, a registered webhook (``getUpdates`` is blocked while one
is set), a competing consumer, and an auth failure — and only the first is
silence. ``pending_update_count`` from ``getWebhookInfo`` is the independent
witness: **zero returned while pending is non-zero is a fault, not silence.**
:func:`read` returns a ``status`` of ``"fault"`` for the other three, and the CLI
exits non-zero on it, because reporting ``0 signals`` off any of them is the same
un-executed-call-read-as-silence defect this module exists downstream of.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from scout.scripts.notify_telegram import TELEGRAM_API, _read_secret

DEFAULT_TIMEOUT = 20.0

# Requested explicitly so reactions are not silently filtered out. Live delivery
# of ``message_reaction`` to a private-chat bot is **unverified** — no real
# reaction was available to test against — so a reaction arriving is a bonus, not
# a guarantee. Never report "0 reactions" as an observation about the user; that
# is unverified capability, not measured silence.
ALLOWED_UPDATES = ["message", "edited_message", "message_reaction"]

STATUS_OK = "ok"
STATUS_FAULT = "fault"


@dataclass
class InboundReport:
    """Result of one inbound read.

    ``status`` is ``"ok"`` only when the call executed against an unblocked
    queue. Every other outcome is ``"fault"`` and carries a ``fault`` string —
    callers must not derive a signal count from a faulted report.
    """

    status: str
    fetched: int = 0
    inbound: int = 0
    reported: int = 0
    pending_update_count: int | None = None
    webhook_url: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    fault: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _tz() -> ZoneInfo:
    """Display timezone, via the canonical resolver the scheduler uses."""
    from scout.scripts.schedule_tick import _local_tz_name

    try:
        return ZoneInfo(_local_tz_name())
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _call(token: str, method: str, **params: Any) -> dict[str, Any]:
    """GET a Bot API method, never leaking the token into an error string.

    ``str(RequestException)`` can include the request URL, and the Telegram URL
    embeds the bot token in its path (``/bot<token>/getUpdates``). On a 401 —
    exactly what an operator debugs live — the raw token would otherwise land in
    a log. Errors are rebuilt from status/reason, and any residual occurrence of
    the token is redacted defensively.
    """
    url = f"{TELEGRAM_API}/bot{token}/{method}"
    try:
        resp = requests.get(url, params=params or None, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc).replace(token, "<REDACTED>")}
    if resp.status_code != 200:
        body = (resp.text or "")[:300].replace(token, "<REDACTED>")
        return {"ok": False, "error": f"HTTP {resp.status_code} {resp.reason}: {body}"}
    try:
        return resp.json()
    except ValueError as exc:
        return {"ok": False, "error": f"unparseable response: {exc}"}


def _local(epoch: int, tz: ZoneInfo) -> str:
    """Telegram ``date`` is a UTC epoch. Convert at read time, never print raw."""
    return datetime.fromtimestamp(epoch, tz=UTC).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")


def _name(frm: dict[str, Any]) -> str:
    parts = (frm.get("first_name"), frm.get("last_name"))
    return " ".join(p for p in parts if p) or str(frm.get("id", "?"))


def _flatten(update: dict[str, Any], tz: ZoneInfo) -> dict[str, Any] | None:
    """Reduce one update to the fields the harvest phase classifies on."""
    if "message" in update or "edited_message" in update:
        edited = "edited_message" in update
        msg = update.get("edited_message") or update["message"]
        frm = msg.get("from", {})
        replied = msg.get("reply_to_message")
        return {
            "kind": "edited_message" if edited else "message",
            "update_id": update.get("update_id"),
            "at": _local(msg.get("date", 0), tz),
            "epoch": msg.get("date", 0),
            "author": _name(frm),
            "is_bot": bool(frm.get("is_bot")),
            "text": msg.get("text") or msg.get("caption") or "",
            # A reply carries the wrap it answers, so the signal arrives already
            # attached to the output it is feedback ON — which the Slack path has
            # to reconstruct with a second call.
            "replying_to": (replied.get("text") or "")[:160] if replied else None,
        }
    if "message_reaction" in update:
        rx = update["message_reaction"]
        frm = rx.get("user", {})
        return {
            "kind": "reaction",
            "update_id": update.get("update_id"),
            "at": _local(rx.get("date", 0), tz),
            "epoch": rx.get("date", 0),
            "author": _name(frm),
            "is_bot": bool(frm.get("is_bot")),
            "text": "",
            "reactions": [r.get("emoji") or r.get("custom_emoji_id", "?") for r in rx.get("new_reaction", [])],
            "on_message_id": rx.get("message_id"),
        }
    return None


def parse_since(raw: str, tz: ZoneInfo) -> int:
    """Parse a ``--since`` value to a unix epoch, or raise ``ValueError``.

    ISO-8601 is tried **first**, because the caller's natural form for "the last
    run's timestamp" is an ISO stamp with a ``T`` — that is exactly how
    ``.scout-state/last-fire.json`` stores every slot's timestamp. A parser that
    accepted only ``YYYY-MM-DD HH:MM:SS`` would reject the one form the caller
    actually possesses, and the refusal would reach the call site as an empty
    report: the same defect, one layer down.

    Naive values are interpreted in the display timezone.
    """
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return int(parsed.timestamp())
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(raw, fmt).replace(tzinfo=tz).timestamp())
        except ValueError:
            continue
    raise ValueError(f"could not parse --since {raw!r}")


def read(since: str | None = None) -> InboundReport:
    """Read the retained inbound window. Never consumes the queue.

    Raises ``ConfigError`` (exit 10) if the bot token is missing, unreadable, or
    insecurely permissioned — the same contract as the outbound path, so an
    operator sees one failure mode for one misconfiguration.

    Raises ``ValueError`` if ``since`` cannot be parsed. The ``since`` filter is
    applied to **output only**: the fetch is always the full retained window, so
    the pending cross-check below stays honest regardless of the window asked for.
    """
    tz = _tz()
    token = _read_secret("telegram-bot-token")
    cutoff = parse_since(since, tz) if since else None

    hook = _call(token, "getWebhookInfo")
    hook_result = hook.get("result") or {}
    hook_url = hook_result.get("url") or ""
    pending = hook_result.get("pending_update_count")

    got = _call(token, "getUpdates", allowed_updates=json.dumps(ALLOWED_UPDATES), timeout=0)

    if not got.get("ok"):
        return InboundReport(
            status=STATUS_FAULT,
            pending_update_count=pending,
            webhook_url=hook_url,
            fault=f"getUpdates failed: {got.get('error') or got.get('description') or 'unknown'}",
        )

    raw_updates = got.get("result", []) or []
    items = [f for f in (_flatten(u, tz) for u in raw_updates) if f]
    inbound = [i for i in items if not i["is_bot"]]
    shown = sorted(
        (i for i in inbound if cutoff is None or i["epoch"] >= cutoff),
        key=lambda i: i["epoch"],
    )

    report = InboundReport(
        status=STATUS_OK,
        fetched=len(raw_updates),
        inbound=len(inbound),
        reported=len(shown),
        pending_update_count=pending,
        webhook_url=hook_url,
        items=shown,
    )

    # Two blocked states that are indistinguishable from silence at the call
    # site. Both must outrank an empty result, not be reported as one.
    if hook_url:
        report.status = STATUS_FAULT
        report.fault = (
            f"a webhook is registered ({hook_url}); getUpdates is blocked while one is "
            "set and returns nothing whether or not the user wrote"
        )
    elif not raw_updates and pending:
        report.status = STATUS_FAULT
        report.fault = (
            f"0 updates returned but getWebhookInfo reports {pending} pending — something else is consuming the queue"
        )
    return report


def render(report: InboundReport, since: str | None = None) -> str:
    """Render a report for the session prompt.

    A faulted report renders the refusal **in the report's own banner shape**, on
    stdout, so a caller that pipes this reader (``| tail``, ``| head``, any
    capture of stdout alone) cannot read the refusal as an empty harvest.
    """
    out = ["=== TELEGRAM FEEDBACK ===", ""]

    if not report.ok:
        out.append(f"  ERROR — {report.fault}")
        out.append("  This is NOT zero feedback. Nothing was read; do not report a signal count.")
        return "\n".join(out)

    if not report.items:
        window = f" at/after {since}" if since else ""
        out.append(f"  OK — 0 inbound message(s){window}.")
        out.append(f"  Retained window held {report.fetched} update(s); queue not consumed.")
        out.append(
            "  Genuine silence: the call executed, the webhook slot is empty, and pending "
            f"reads {report.pending_update_count}."
        )
        out.append("")
        out.append("  CAVEAT — Telegram drops unconsumed updates after ~24h, so this is a statement")
        out.append("  about the last day only, NOT about the run window if that window is older.")
        return "\n".join(out)

    out.append(f"  {report.reported} inbound item(s) — every one is the user (the bot's own sends")
    out.append("  never come back through getUpdates in a private chat).")
    out.append("")
    for i in report.items:
        head = f"  [{i['at']}] {i['author']}"
        if i["kind"] == "reaction":
            emoji = " ".join(i.get("reactions") or [])
            out.append(f"{head} — REACTION {emoji} on msg {i.get('on_message_id')}")
        else:
            tag = " (edited)" if i["kind"] == "edited_message" else ""
            out.append(f"{head}{tag}")
            if i.get("replying_to"):
                out.append(f"      ↳ replying to: {i['replying_to']!r}")
            for line in (i["text"] or "<no text>").splitlines() or ["<no text>"]:
                out.append(f"      {line}")
        out.append("")

    out.append('  Every item above must reach a classification or an explicit "no action needed,')
    out.append("  because —" + '" before the feedback phase closes. A retrieved reply that is not')
    out.append("  acted on is the same defect as one never retrieved.")
    if report.pending_update_count:
        out.append("")
        out.append(f"  Queue not consumed ({report.pending_update_count} pending) — re-reads are idempotent.")
    return "\n".join(out)
