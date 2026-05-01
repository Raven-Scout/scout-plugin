"""Unit tests for scout.scripts.notify_telegram.

Plan 4 Task 6 baseline cases (5 in plan §1301-1308):

  1. Token + chat_id present → POST happy path with correct URL/body shape.
  2. Token file missing → ConfigError (exit code 10).
  3. Body > 4096 chars → split into multiple sendMessage POSTs.
  4. Tier flag → disable_notification toggled correctly.
  5. --dry-run prints request, doesn't POST.

Plus hermetic helpers covering the body splitter:

  6. Splitting prefers paragraph boundaries (\\n\\n).
  7. Splitting falls back to spaces when no newlines.
  8. Splitting hard-cuts when no breakable chars in a chunk.
  9. Empty body → ValueError.
 10. Unknown tier → ValueError naming valid tiers.
 11. CLI surfaces ConfigError as exit 10 and ValueError as exit 1.

All HTTP calls are mocked — no live Telegram traffic.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.errors import ConfigError
from scout.events import Event
from scout.scripts import notify_telegram

FAKE_TOKEN = "FAKE_TOKEN_123:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
FAKE_CHAT_ID = "987654321"


# ----- fixtures -------------------------------------------------------------


def _write_secret(path: Path, content: str) -> None:
    """Write a secret file and chmod 600 it.

    ``_read_secret`` enforces mode 600 — any non-owner permission bit
    triggers ``ConfigError``. Tests that bypass this helper and rely on
    the umask default (typically 644) will fail with ``ConfigError``,
    not with the assertion they expected.
    """
    path.write_text(content)
    path.chmod(0o600)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Hermetic secrets dir with valid fake credentials."""
    d = tmp_path / "secrets"
    d.mkdir()
    _write_secret(d / "telegram-bot-token", FAKE_TOKEN)
    _write_secret(d / "telegram-chat-id", FAKE_CHAT_ID)
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)
    return d


@pytest.fixture
def empty_secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Hermetic secrets dir with NO credentials present."""
    d = tmp_path / "secrets"
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)
    return d


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"ok": True, "result": {"message_id": 1}}
    resp.raise_for_status = MagicMock()
    return resp


# ----- 1. Happy path --------------------------------------------------------


def test_send_happy_path_posts_correct_url_and_body(secrets_dir: Path) -> None:
    body = "hello from Scout"
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        ev = notify_telegram.send(tier="info", body=body)

    assert mock_post.call_count == 1
    call = mock_post.call_args
    url = call.args[0] if call.args else call.kwargs.get("url")
    assert url == f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"

    # Payload sent as JSON or form data — accept either; assert by reading
    # whichever kwarg the implementation chose.
    payload = call.kwargs.get("json") or call.kwargs.get("data")
    assert payload is not None
    assert payload["chat_id"] == FAKE_CHAT_ID
    assert payload["text"] == body
    assert payload["disable_notification"] is True  # info → silent

    assert isinstance(ev, Event)
    assert ev.kind == "notification.sent"
    assert ev.source == "cli:notify_telegram"
    assert ev.payload["tier"] == "info"
    assert ev.payload["channel"] == "telegram"
    assert ev.payload["body_chars"] == len(body)


# ----- 2. Token file missing -----------------------------------------------


def test_send_missing_token_raises_config_error(empty_secrets_dir: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        notify_telegram.send(tier="info", body="test")
    assert ConfigError.exit_code == 10
    msg = str(exc_info.value)
    # Helpful message names the missing file path.
    assert "telegram-bot-token" in msg


def test_send_missing_chat_id_raises_config_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = tmp_path / "secrets"
    d.mkdir()
    _write_secret(d / "telegram-bot-token", FAKE_TOKEN)
    # chat-id deliberately absent
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)

    with pytest.raises(ConfigError) as exc_info:
        notify_telegram.send(tier="info", body="test")
    assert "telegram-chat-id" in str(exc_info.value)


# ----- 3. Body > 4096 chars splits ------------------------------------------


def test_send_long_body_splits_across_multiple_posts(secrets_dir: Path) -> None:
    body = "X" * 10000  # well over 4096
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        ev = notify_telegram.send(tier="info", body=body)

    assert mock_post.call_count >= 3  # 10000 / 4096 = 2.44 → 3 chunks
    chunks_sent: list[str] = []
    for call in mock_post.call_args_list:
        payload = call.kwargs.get("json") or call.kwargs.get("data")
        chunk = payload["text"]
        assert len(chunk) <= notify_telegram.MAX_MESSAGE_LEN
        chunks_sent.append(chunk)
    # Reconstruction matches original (no chars dropped/duplicated).
    assert "".join(chunks_sent) == body
    assert ev.payload["body_chars"] == len(body)


# ----- 4. Tier toggles disable_notification ---------------------------------


def test_tier_action_required_loud(secrets_dir: Path) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        notify_telegram.send(tier="action_required", body="urgent")
    payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args.kwargs.get("data")
    assert payload["disable_notification"] is False


def test_tier_info_silent(secrets_dir: Path) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        notify_telegram.send(tier="info", body="fyi")
    payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args.kwargs.get("data")
    assert payload["disable_notification"] is True


# ----- 5. --dry-run prints request body, doesn't POST -----------------------


def test_dry_run_does_not_post(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        ev = notify_telegram.send(tier="info", body="dry test", dry_run=True)
        assert mock_post.call_count == 0
    assert ev.payload.get("dry_run") is True


def test_dry_run_still_requires_secrets_for_fail_fast(empty_secrets_dir: Path) -> None:
    """Dry-run reads secrets so missing config surfaces immediately
    (operator wants to verify the install, not stub past it)."""
    with pytest.raises(ConfigError):
        notify_telegram.send(tier="info", body="x", dry_run=True)


# ----- 6/7/8. Body splitter boundary preferences ----------------------------


def test_split_prefers_paragraph_boundary() -> None:
    body = ("A" * 2000) + "\n\n" + ("B" * 3000)
    chunks = notify_telegram._split_message(body)
    assert len(chunks) == 2
    assert chunks[0].rstrip() == "A" * 2000
    assert chunks[1].lstrip() == "B" * 3000


def test_split_falls_back_to_spaces() -> None:
    # 5000 "A " tokens = 10000 chars, no newlines anywhere.
    body = "A " * 5000
    chunks = notify_telegram._split_message(body)
    assert len(chunks) >= 3
    for c in chunks:
        assert len(c) <= notify_telegram.MAX_MESSAGE_LEN
    # Splits must happen at spaces — chunk boundaries shouldn't bisect a
    # token. Reconstruction (allowing for joiner whitespace stripping) — the
    # splitter is allowed to drop a single boundary char per split.
    rejoined = "".join(chunks)
    # Confirm we lose at most one char per split (the boundary delimiter).
    assert len(rejoined) >= len(body) - len(chunks)


def test_split_hard_cuts_when_no_breakable_chars() -> None:
    body = "A" * 8000
    chunks = notify_telegram._split_message(body)
    assert len(chunks) == 2
    assert len(chunks[0]) == notify_telegram.MAX_MESSAGE_LEN
    assert len(chunks[1]) == 8000 - notify_telegram.MAX_MESSAGE_LEN
    assert chunks[0] + chunks[1] == body


def test_split_short_body_single_chunk() -> None:
    chunks = notify_telegram._split_message("hi")
    assert chunks == ["hi"]


# ----- 9. Empty body -> ValueError -----------------------------------------


def test_empty_body_raises_value_error(secrets_dir: Path) -> None:
    with pytest.raises(ValueError, match="body cannot be empty"):
        notify_telegram.send(tier="info", body="")


# ----- 10. Unknown tier -> ValueError --------------------------------------


def test_unknown_tier_raises_value_error(secrets_dir: Path) -> None:
    with pytest.raises(ValueError) as exc_info:
        notify_telegram.send(tier="bogus", body="test")
    msg = str(exc_info.value)
    assert "info" in msg
    assert "action_required" in msg


# ----- 11. CLI exit code mapping --------------------------------------------


def test_cli_notify_telegram_help(runner: CliRunner) -> None:
    result = runner.invoke(cli.app, ["notify", "telegram", "--help"])
    assert result.exit_code == 0
    assert "telegram" in result.stdout.lower()


def test_cli_notify_dry_run_outputs_event_json(runner: CliRunner, secrets_dir: Path) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        result = runner.invoke(
            cli.app,
            ["notify", "telegram", "--tier", "info", "--body", "hello", "--dry-run"],
        )
    assert mock_post.call_count == 0
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["kind"] == "notification.sent"
    assert payload["payload"]["channel"] == "telegram"
    assert payload["payload"]["dry_run"] is True


def test_cli_missing_secrets_exits_10(runner: CliRunner, empty_secrets_dir: Path) -> None:
    result = runner.invoke(
        cli.app,
        ["notify", "telegram", "--tier", "info", "--body", "x"],
    )
    assert result.exit_code == 10  # ConfigError


def test_cli_unknown_tier_exits_nonzero(runner: CliRunner, secrets_dir: Path) -> None:
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        result = runner.invoke(
            cli.app,
            ["notify", "telegram", "--tier", "bogus", "--body", "x"],
        )
    assert mock_post.call_count == 0
    assert result.exit_code != 0


def test_cli_newline_body_survives(runner: CliRunner, secrets_dir: Path) -> None:
    """--body with embedded newlines (from a shell heredoc) is preserved."""
    body = "line1\nline2\nline3"
    # Patch even on dry-run paths to ensure no accidental network call.
    with patch("scout.scripts.notify_telegram.requests.post"):
        result = runner.invoke(
            cli.app,
            ["notify", "telegram", "--tier", "info", "--body", body, "--dry-run"],
        )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["payload"]["body_chars"] == len(body)


# ----- Event shape sanity ---------------------------------------------------


def test_event_is_serializable_via_asdict(secrets_dir: Path) -> None:
    """Confirm Event survives dataclasses.asdict for JSON output path."""
    with patch("scout.scripts.notify_telegram.requests.post") as mock_post:
        mock_post.return_value = _ok_response()
        ev = notify_telegram.send(tier="info", body="x")
    d = dataclasses.asdict(ev)
    assert d["kind"] == "notification.sent"
    assert "id" in d and "ts" in d


# ----- Secret-handling defenses (token redaction + mode 600) ----------------


def test_dry_run_does_not_leak_token_to_stderr(secrets_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The Telegram URL embeds the token in its path; dry-run prints the
    URL; therefore dry-run must redact the token. Operators reading shell
    history or stderr-redirected logs should never see the raw token.
    """
    notify_telegram.send(tier="info", body="hi", dry_run=True)
    captured = capsys.readouterr()
    # The fake token from the fixture must NOT appear anywhere in stderr.
    assert FAKE_TOKEN not in captured.err
    # And the redacted form IS present, so the operator can see what
    # would have been POSTed.
    assert "<REDACTED>" in captured.err
    # Belt-and-braces: stdout should also be token-free (it's empty for
    # send() — only the CLI wraps it with stdout JSON).
    assert FAKE_TOKEN not in captured.out


def test_cli_http_error_does_not_leak_token(runner: CliRunner, secrets_dir: Path) -> None:
    """An HTTP 401 (revoked token) must not leak the bot token to stderr.

    ``str(requests.HTTPError)`` includes the request URL, and the
    Telegram URL embeds the token. The CLI handler must rebuild its
    error message without ``str(e)``.
    """
    import requests as _requests

    fake_url = f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"
    fake_resp = MagicMock()
    fake_resp.status_code = 401
    fake_resp.reason = "Unauthorized"
    fake_resp.url = fake_url
    # str(HTTPError) renders as: "<message>" — when constructed with a
    # response, it includes "for url: <url>" via raise_for_status. We
    # simulate that by setting the response and a message that mentions
    # the URL (matching real requests behaviour).
    err = _requests.HTTPError(
        f"401 Client Error: Unauthorized for url: {fake_url}",
        response=fake_resp,
    )
    fake_resp.raise_for_status = MagicMock(side_effect=err)

    with patch("scout.scripts.notify_telegram.requests.post", return_value=fake_resp):
        # mix_stderr=False so result.stderr is captured separately. Default
        # CliRunner merges them, which would also satisfy our assertion,
        # but separating makes the intent unambiguous.
        result = runner.invoke(
            cli.app,
            ["notify", "telegram", "--tier", "info", "--body", "x"],
        )

    # Non-zero exit on HTTP failure.
    assert result.exit_code == 2
    # The full output (stdout + stderr in mix-mode) MUST NOT contain
    # the token. ``result.output`` is the merged stream by default.
    assert FAKE_TOKEN not in result.output
    # The redacted message IS present so operators still get a useful
    # error.
    assert "401" in result.output
    assert "Unauthorized" in result.output


def test_read_secret_rejects_insecure_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret file with mode 644 (default umask) must be rejected.

    The setup doc tells operators to ``chmod 600``; the reader enforces
    it. The error message must include a ``chmod 600`` hint so operators
    know the exact fix.
    """
    d = tmp_path / "secrets"
    d.mkdir()
    bad = d / "telegram-bot-token"
    bad.write_text(FAKE_TOKEN)
    bad.chmod(0o644)  # world-readable — DEFAULT umask outcome, not safe
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)

    with pytest.raises(ConfigError) as exc_info:
        notify_telegram._read_secret("telegram-bot-token")
    msg = str(exc_info.value)
    assert "insecure permissions" in msg
    # Hint MUST include the exact chmod command and path.
    assert "chmod 600" in msg
    assert str(bad) in msg


def test_read_secret_rejects_group_readable_mode_640(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mode 640 (group-readable) is also rejected — 0o077 catches any
    group/other bit, not just 'world-readable'."""
    d = tmp_path / "secrets"
    d.mkdir()
    bad = d / "telegram-bot-token"
    bad.write_text(FAKE_TOKEN)
    bad.chmod(0o640)
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)

    with pytest.raises(ConfigError, match="insecure permissions"):
        notify_telegram._read_secret("telegram-bot-token")


def test_read_secret_accepts_mode_600(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mode 600 (owner-only) is accepted — sanity check on the gate."""
    d = tmp_path / "secrets"
    d.mkdir()
    ok = d / "telegram-bot-token"
    ok.write_text(FAKE_TOKEN)
    ok.chmod(0o600)
    monkeypatch.setattr(notify_telegram, "SECRETS_DIR", d)

    assert notify_telegram._read_secret("telegram-bot-token") == FAKE_TOKEN
