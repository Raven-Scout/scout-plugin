"""Coverage of `notify_telegram.main()` and the remaining `_read_secret` paths.

`test_scripts_notify_telegram.py` covers `send()`, `_split_message`, the
permission checks and the Typer command. `main()` — the argparse entry point a
Python caller or a bare `python -m` invocation reaches — is untested, and it
carries its own copy of the exit-code map and the bot-token redaction. A
divergence between the two copies is exactly the kind of thing that leaks a
token, so pin both.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from scout.errors import ConfigError
from scout.scripts import notify_telegram

FAKE_TOKEN = "1234567:FAKE-TOKEN-DO-NOT-USE"
FAKE_CHAT_ID = "-1009999999999"


def _write_secret(path: Path, value: str) -> None:
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "secrets"
    d.mkdir()
    _write_secret(d / "telegram-bot-token", FAKE_TOKEN)
    _write_secret(d / "telegram-chat-id", FAKE_CHAT_ID)
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)
    return d


@pytest.fixture
def empty_secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "secrets"
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)
    return d


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# _read_secret — the two paths the existing suite doesn't reach
# ---------------------------------------------------------------------------


def test_read_secret_rejects_an_empty_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A touched-but-never-filled secret is a common half-finished install; the
    error must name the file rather than failing later with a 401."""
    d = tmp_path / "secrets"
    d.mkdir()
    _write_secret(d / "telegram-bot-token", "   ")
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)

    with pytest.raises(ConfigError, match="Secret file is empty"):
        notify_telegram._read_secret("telegram-bot-token")


def test_read_secret_surfaces_an_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / "secrets"
    d.mkdir()
    secret = d / "telegram-bot-token"
    _write_secret(secret, FAKE_TOKEN)
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)

    real_read_text = Path.read_text

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == secret:
            raise OSError("input/output error")
        return real_read_text(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", maybe_boom)
    with pytest.raises(ConfigError, match="Could not read"):
        notify_telegram._read_secret("telegram-bot-token")


def test_read_secret_names_the_setup_doc_when_the_file_is_absent(empty_secrets_dir: Path) -> None:
    with pytest.raises(ConfigError, match="telegram-setup.md"):
        notify_telegram._read_secret("telegram-bot-token")


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_dry_run_prints_the_event_json(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert notify_telegram.main(["--body", "hello", "--dry-run"]) == 0
    captured = capsys.readouterr()
    # stdout is pure JSON; the [dry-run] preamble goes to stderr.
    payload = json.loads(captured.out)
    assert payload["kind"] == "notification.sent"
    assert payload["payload"] == {
        "tier": "info",
        "channel": "telegram",
        "body_chars": 5,
        "dry_run": True,
    }
    assert "[dry-run] POST" in captured.err
    assert FAKE_TOKEN not in captured.err


def test_main_real_send_prints_the_event_json(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        assert notify_telegram.main(["--tier", "action_required", "--body", "ship it"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["payload"]["tier"] == "action_required"
    assert "dry_run" not in payload["payload"]
    assert mock_post.call_count == 1


def test_main_missing_secrets_exits_with_the_configerror_code(
    empty_secrets_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The runner keys off exit 10 to distinguish "not installed" from
    "send failed"."""
    assert notify_telegram.main(["--body", "hello"]) == ConfigError.exit_code
    assert "notify-telegram: Missing secret" in capsys.readouterr().out


def test_main_empty_body_exits_one(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert notify_telegram.main(["--body", ""]) == 1
    assert "body cannot be empty" in capsys.readouterr().out


def test_main_rejects_an_unknown_tier_at_the_argparse_layer(secrets_dir: Path) -> None:
    """argparse `choices` fails before send() is reached, so this is a
    SystemExit(2), not a mapped return code."""
    with pytest.raises(SystemExit) as exc:
        notify_telegram.main(["--tier", "shout", "--body", "hello"])
    assert exc.value.code == 2


def test_main_requires_a_body(secrets_dir: Path) -> None:
    with pytest.raises(SystemExit):
        notify_telegram.main([])


def test_main_http_error_redacts_the_bot_token(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`str(HTTPError)` embeds the request URL, and the Telegram URL carries
    the bot token in its path — a 401 must not print it."""
    resp = MagicMock()
    resp.status_code = 401
    resp.reason = "Unauthorized"
    err = requests.HTTPError(
        f"401 Client Error: Unauthorized for url: https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage",
        response=resp,
    )

    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.side_effect = err
        assert notify_telegram.main(["--body", "hello"]) == 2

    out = capsys.readouterr().out
    assert "HTTP 401 Unauthorized (token redacted in URL)" in out
    assert FAKE_TOKEN not in out


def test_main_http_error_without_a_response_degrades_gracefully(
    secrets_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value.raise_for_status.side_effect = requests.HTTPError("boom", response=None)  # type: ignore[arg-type]
        assert notify_telegram.main(["--body", "hello"]) == 2
    assert "HTTP ? Unknown" in capsys.readouterr().out


def test_main_transport_error_exits_two(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.side_effect = requests.ConnectTimeout("connect timed out")
        assert notify_telegram.main(["--body", "hello"]) == 2
    assert "HTTP error: connect timed out" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _split_message — the line-boundary branch
# ---------------------------------------------------------------------------


def test_split_prefers_a_line_boundary_when_there_is_no_blank_line() -> None:
    """Boundary preference is paragraph > line > word > hard cut. The existing
    suite covers paragraph, word and hard cut; this is the line rung."""
    limit = 20
    body = "a" * 15 + "\n" + "b" * 15
    chunks = notify_telegram._split_message(body, limit=limit)
    assert chunks == ["a" * 15, "b" * 15]
    assert all(len(c) <= limit for c in chunks)
