"""Slack trigger source — polls the Slack Web API for mentions.

v1 supports ``mention`` only: one ``search.messages`` call per tick for
``<@USER_ID>``, filtered client-side by timestamp (the combined-query
optimization — one connector call serves every slack trigger).

Auth: a user token with ``search:read`` at
``~/.scout-secrets/slack-search-token`` (mode 600, same convention as the
Telegram secrets). The Scout user's Slack ID comes from
``scout-config.yaml`` → ``connectors.inputs.user_slack_id``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

import yaml

from scout.errors import ConfigError
from scout.triggers.sources.base import ConnectorEvent

SUPPORTED_MATCH_TYPES: list[str] = ["mention"]

SEARCH_URL = "https://slack.com/api/search.messages"
TOKEN_FILENAME = "slack-search-token"
SECRETS_DIR = Path.home() / ".scout-secrets"
DEFAULT_TIMEOUT = 15.0
PAGE_SIZE = 100


def _parse_iso_z(ts: str) -> dt.datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return dt.datetime.fromisoformat(ts)


def _default_http_get(url: str, *, params: dict, headers: dict, timeout: float) -> dict:
    import requests

    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _read_token() -> str:
    """Read the Slack search token (mode-600 enforced, Telegram-secret style)."""
    path = SECRETS_DIR / TOKEN_FILENAME
    if not path.exists():
        raise ConfigError(f"Missing secret: {path}. Create it (mode 600) with a Slack token that has search:read.")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise ConfigError(f"{path} has insecure permissions {oct(mode)}; expected 600. Run: chmod 600 {path}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ConfigError(f"Secret file is empty: {path}.")
    return value


def _user_id_from_config(vault: Path) -> str | None:
    cfg_path = vault / "scout-config.yaml"
    if not cfg_path.exists():
        return None
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError, OSError):
        return None
    value = ((cfg.get("connectors") or {}).get("inputs") or {}).get("user_slack_id")
    return value or None


class SlackSource:
    """Poll Slack mentions of the Scout user via ``search.messages``."""

    name = "slack"
    SUPPORTED_MATCH_TYPES = SUPPORTED_MATCH_TYPES

    def __init__(
        self,
        *,
        user_id: str | None = None,
        token_reader: Callable[[], str] | None = None,
        http_get: Callable[..., dict] | None = None,
    ) -> None:
        self._user_id = user_id
        self._token_reader = token_reader or _read_token
        self._http_get = http_get or _default_http_get

    @classmethod
    def for_vault(cls, vault: Path) -> SlackSource:
        return cls(user_id=_user_id_from_config(vault))

    def scan_since(self, ts: str) -> list[ConnectorEvent]:
        if not self._user_id:
            raise ConfigError("slack trigger source: user_slack_id is not configured (scout-config.yaml)")
        token = self._token_reader()
        since_epoch = _parse_iso_z(ts).timestamp()

        data = self._http_get(
            SEARCH_URL,
            params={
                "query": f"<@{self._user_id}>",
                "count": PAGE_SIZE,
                "sort": "timestamp",
                "sort_dir": "desc",
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=DEFAULT_TIMEOUT,
        )
        if not data.get("ok"):
            raise ConfigError(f"slack search.messages failed: {data.get('error', 'unknown_error')}")

        events: list[ConnectorEvent] = []
        for msg in (data.get("messages") or {}).get("matches", []):
            msg_ts = str(msg.get("ts", ""))
            try:
                epoch = float(msg_ts)
            except ValueError:
                continue
            if epoch <= since_epoch:
                continue
            author = msg.get("user", "")
            channel = msg.get("channel") or {}
            iso = (
                dt.datetime.fromtimestamp(epoch, tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.")
                + f"{int(epoch * 1000) % 1000:03d}Z"
            )
            events.append(
                ConnectorEvent(
                    source=self.name,
                    source_event_id=msg_ts,
                    ts=iso,
                    raw_payload=dict(msg),
                    normalized_match_fields={
                        "type": "mention",
                        "author": author,
                        "author_name": msg.get("username", ""),
                        "channel": channel.get("id", ""),
                        "channel_name": channel.get("name", ""),
                        "text": msg.get("text", ""),
                        "permalink": msg.get("permalink", ""),
                        "is_self": bool(author) and author == self._user_id,
                    },
                )
            )
        events.sort(key=lambda e: e.source_event_id)
        return events

    def health_check(self) -> tuple[bool, str]:
        if not self._user_id:
            return False, "user_slack_id not configured in scout-config.yaml"
        try:
            self._token_reader()
        except ConfigError as e:
            return False, str(e)
        return True, "ok"

    def supports_webhook(self) -> bool:
        return False
