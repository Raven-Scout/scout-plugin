"""Unit tests for scout.scripts.telegram_inbound (issue #215).

Four of these are **negative controls whose pre-fix answer was "0 signals"** —
the whole point of the module is that an empty list at the call site has four
causes and only one of them is silence:

  1. A webhook is registered while a real message is present  → fault, exit 1
  2. 0 updates returned while getWebhookInfo reports 3 pending → fault, exit 1
  3. getUpdates returns ok:false (401)                        → fault, exit 1
  4. The network call raises                                  → fault, exit 1

Plus the positive path, the never-consume guarantee, authorship handling,
reply/reaction flattening, --since parsing (including the ISO form
last-fire.json actually stores), and the CLI exit-code contract.

All HTTP is mocked — no live Telegram traffic.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.errors import ConfigError
from scout.scripts import telegram_inbound as ti

FAKE_TOKEN = "FAKE_TOKEN_123:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
TZ = ZoneInfo("UTC")

# 2026-08-29 09:36:01 UTC
EPOCH = 1787996161


def _write_token(tmp_path: Path) -> Path:
    p = tmp_path / "telegram-bot-token"
    p.write_text(FAKE_TOKEN)
    p.chmod(0o600)
    return p


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    """Point the shared secret reader at a tmp dir with a valid token."""
    _write_token(tmp_path)
    monkeypatch.setattr("scout.scripts.notify_telegram.secrets_dir", lambda: tmp_path)
    monkeypatch.setattr(ti, "_tz", lambda: TZ)
    return tmp_path


def _resp(payload: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.reason = "OK" if status == 200 else "Unauthorized"
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


def _hook(url: str = "", pending: int = 0) -> dict:
    return {"ok": True, "result": {"url": url, "pending_update_count": pending}}


def _message(text: str = "do the thing", *, is_bot: bool = False, reply: str | None = None) -> dict:
    msg = {
        "message_id": 42,
        "date": EPOCH,
        "from": {"id": 7, "first_name": "Alex", "last_name": "Example", "is_bot": is_bot},
        "text": text,
    }
    if reply:
        msg["reply_to_message"] = {"text": reply}
    return {"update_id": 1001, "message": msg}


def _updates(*items: dict) -> dict:
    return {"ok": True, "result": list(items)}


# ----- positive path --------------------------------------------------------


def test_reads_inbound_message(secrets):
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(_message()))]):
        rep = ti.read()
    assert rep.ok and rep.status == ti.STATUS_OK
    assert rep.reported == 1
    assert rep.items[0]["text"] == "do the thing"
    assert rep.items[0]["author"] == "Alex Example"


def test_never_passes_offset(secrets):
    """The queue must never be consumed: no `offset` may reach getUpdates."""
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(_message()))]) as g:
        ti.read()
    params = g.call_args_list[1].kwargs["params"]
    assert "offset" not in params
    assert json.loads(params["allowed_updates"]) == ti.ALLOWED_UPDATES


def test_reply_carries_its_parent(secrets):
    with patch.object(
        ti.requests,
        "get",
        side_effect=[_resp(_hook()), _resp(_updates(_message("wrong", reply="Scout Digest — Saturday")))],
    ):
        rep = ti.read()
    assert rep.items[0]["replying_to"] == "Scout Digest — Saturday"


def test_bot_authored_items_are_excluded_not_assumed_absent(secrets):
    """A group upgrade would start returning bot messages; assert, don't assume."""
    with patch.object(
        ti.requests,
        "get",
        side_effect=[_resp(_hook()), _resp(_updates(_message(is_bot=True), _message("real")))],
    ):
        rep = ti.read()
    assert rep.fetched == 2 and rep.inbound == 1
    assert rep.items[0]["text"] == "real"


def test_reaction_update_is_flattened(secrets):
    rx = {
        "update_id": 1002,
        "message_reaction": {
            "message_id": 42,
            "date": EPOCH,
            "user": {"id": 7, "first_name": "Alex", "is_bot": False},
            "new_reaction": [{"emoji": "👍"}],
        },
    }
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(rx))]):
        rep = ti.read()
    assert rep.items[0]["kind"] == "reaction"
    assert rep.items[0]["reactions"] == ["👍"]


def test_genuine_silence_is_ok_not_fault(secrets):
    """The one empty state that IS silence: no webhook, nothing pending."""
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates())]):
        rep = ti.read()
    assert rep.ok and rep.reported == 0 and rep.fault is None
    assert "0 inbound message(s)" in ti.render(rep)
    assert "~24h" in ti.render(rep)  # the retention caveat must survive


# ----- negative controls: pre-fix, every one of these read as "0 signals" ----


def test_negative_control_webhook_registered_with_real_message(secrets):
    """A webhook blocks getUpdates. A message exists. Pre-fix: '0 signals'."""
    with patch.object(
        ti.requests,
        "get",
        side_effect=[_resp(_hook(url="https://example.test/hook", pending=1)), _resp(_updates(_message()))],
    ):
        rep = ti.read()
    assert not rep.ok and "webhook is registered" in rep.fault
    # The message came back despite the webhook, so the refusal is scoped to the
    # count and the item is still handed over. (This assertion used to demand
    # "NOT zero feedback" here, which is the no-items wording — it passed only
    # because render() discarded the item it was given.)
    rendered = ti.render(rep)
    assert "COUNT is unusable" in rendered
    assert "do the thing" in rendered


def test_negative_control_zero_returned_while_pending_nonzero(secrets):
    """A competing consumer. Pre-fix: '0 signals'."""
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook(pending=3)), _resp(_updates())]):
        rep = ti.read()
    assert not rep.ok and "3 pending" in rep.fault


def test_negative_control_get_updates_401(secrets):
    """Revoked token. Pre-fix: '0 signals'."""
    unauthorized = _resp({"ok": False, "description": "Unauthorized"}, status=401)
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), unauthorized]):
        rep = ti.read()
    assert not rep.ok and "getUpdates failed" in rep.fault


def test_negative_control_network_error(secrets):
    """Timeout/DNS. Pre-fix: '0 signals'."""
    with patch.object(
        ti.requests,
        "get",
        side_effect=[_resp(_hook()), ti.requests.RequestException("timed out")],
    ):
        rep = ti.read()
    assert not rep.ok and "getUpdates failed" in rep.fault


def test_negative_control_witness_401_cannot_be_read_as_silence(secrets):
    """The witness itself dies. Pre-fix: 'ok' + a claim of 'genuine silence'.

    All four original negative controls exercise a `getUpdates` fault; none
    covered `getWebhookInfo` failing. When it does, `result` is absent, so
    `webhook_url` reads "" and `pending` reads None — and the pending
    cross-check is `elif not raw_updates and pending:`, where None is falsy.
    The module then reported the webhook slot as *empty* and pending as None
    while both were in fact UNKNOWN: silence claimed off a dead instrument.
    """
    unauthorized = _resp({"ok": False, "description": "Unauthorized"}, status=401)
    with patch.object(ti.requests, "get", side_effect=[unauthorized, _resp(_updates())]):
        rep = ti.read()
    assert not rep.ok
    assert "getWebhookInfo failed" in rep.fault
    assert "UNKNOWN" in rep.fault
    rendered = ti.render(rep)
    assert "Genuine silence" not in rendered
    assert "0 inbound message(s)" not in rendered


def test_negative_control_witness_network_error_cannot_be_read_as_silence(secrets):
    """Same defect via a raised exception rather than a non-200."""
    with patch.object(
        ti.requests,
        "get",
        side_effect=[ti.requests.RequestException("connection refused"), _resp(_updates())],
    ):
        rep = ti.read()
    assert not rep.ok and "getWebhookInfo failed" in rep.fault
    assert "Genuine silence" not in ti.render(rep)


def test_witness_fault_does_not_mask_a_getupdates_fault(secrets):
    """When both calls fail, the reported fault names getUpdates.

    A dead witness makes the *silence claim* unusable; a dead getUpdates means
    nothing was read at all. The second is the stronger statement and must win,
    or an operator debugs the wrong call.
    """
    unauthorized = _resp({"ok": False, "description": "Unauthorized"}, status=401)
    with patch.object(ti.requests, "get", side_effect=[unauthorized, unauthorized]):
        rep = ti.read()
    assert not rep.ok and "getUpdates failed" in rep.fault


def test_fault_still_renders_items_that_were_genuinely_retrieved(secrets):
    """A fault must invalidate the COUNT, not destroy the messages.

    `render()` returned early on any fault and never printed `report.items`,
    while asserting "Nothing was read" — false whenever a webhook is registered
    AND getUpdates still returned a real message (the shape of negative control
    #1). Given the ~24h retention this module documents, that render was
    potentially the only copy, and dropping it contradicts the file's own
    closing rule: a retrieved reply that is not acted on is the same defect as
    one never retrieved.
    """
    with patch.object(
        ti.requests,
        "get",
        side_effect=[
            _resp(_hook(url="https://example.test/hook", pending=1)),
            _resp(_updates(_message("ship it, but rename the flag"))),
        ],
    ):
        rep = ti.read()
    assert not rep.ok
    assert len(rep.items) == 1

    rendered = ti.render(rep)
    assert "webhook is registered" in rendered           # the fault is still led with
    assert "ship it, but rename the flag" in rendered    # and the reply survives
    assert "Alex Example" in rendered
    assert "Nothing was read" not in rendered            # because something was
    assert "must still be acted on" in rendered
    # The closing obligation has to travel with the items it governs.
    assert "no action needed" in rendered


def test_fault_with_no_items_still_refuses_flatly(secrets):
    """The scoping must not weaken the empty case: nothing read, nothing to act on."""
    unauthorized = _resp({"ok": False, "description": "Unauthorized"}, status=401)
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), unauthorized]):
        rep = ti.read()
    rendered = ti.render(rep)
    assert not rep.items
    assert "NOT zero feedback" in rendered
    assert "Nothing was read" in rendered


def test_token_never_appears_in_a_fault_string(secrets):
    """The URL embeds the token; a 401 is exactly what operators debug live."""
    with patch.object(
        ti.requests,
        "get",
        side_effect=[_resp(_hook()), ti.requests.RequestException(f"boom {FAKE_TOKEN}")],
    ):
        rep = ti.read()
    assert FAKE_TOKEN not in (rep.fault or "")
    assert "<REDACTED>" in rep.fault


# ----- secrets contract -----------------------------------------------------


def test_missing_token_raises_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr("scout.scripts.notify_telegram.secrets_dir", lambda: tmp_path)
    with pytest.raises(ConfigError):
        ti.read()


def test_insecure_token_permissions_raise_config_error(tmp_path, monkeypatch):
    p = tmp_path / "telegram-bot-token"
    p.write_text(FAKE_TOKEN)
    p.chmod(0o644)
    monkeypatch.setattr("scout.scripts.notify_telegram.secrets_dir", lambda: tmp_path)
    with pytest.raises(ConfigError):
        ti.read()


# ----- --since parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "1787996161",
        "2026-08-29T09:36:01+00:00",
        "2026-08-29T09:36:01Z",  # last-fire.json's own form
        "2026-08-29 09:36:01",
        "2026-08-29 09:36",
        "2026-08-29",
    ],
)
def test_since_accepts_every_form_the_caller_possesses(raw):
    assert isinstance(ti.parse_since(raw, TZ), int)


def test_since_rejects_garbage_loudly():
    with pytest.raises(ValueError):
        ti.parse_since("not a time", TZ)


def test_since_filters_output_only_not_the_fetch(secrets):
    """The pending cross-check stays honest regardless of the window asked for."""
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(_message()))]) as g:
        rep = ti.read(since=str(EPOCH + 3600))
    assert rep.fetched == 1 and rep.inbound == 1 and rep.reported == 0
    assert "since" not in g.call_args_list[1].kwargs["params"]


# ----- CLI exit-code contract -----------------------------------------------


def test_cli_exits_0_on_genuine_silence(secrets):
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates())]):
        res = CliRunner().invoke(cli.app, ["notify", "telegram-read"])
    assert res.exit_code == 0
    assert "0 inbound" in res.stdout


def test_cli_exits_1_on_fault(secrets):
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook(pending=3)), _resp(_updates())]):
        res = CliRunner().invoke(cli.app, ["notify", "telegram-read"])
    assert res.exit_code == 1
    assert "NOT zero feedback" in res.stdout


def test_cli_exits_10_on_missing_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr("scout.scripts.notify_telegram.secrets_dir", lambda: tmp_path)
    res = CliRunner().invoke(cli.app, ["notify", "telegram-read"])
    assert res.exit_code == ConfigError.exit_code


def test_cli_bad_since_prints_refusal_to_stdout_not_only_stderr(secrets):
    """A caller piping stdout alone must not read the refusal as '0 inbound'."""
    res = CliRunner().invoke(cli.app, ["notify", "telegram-read", "--since", "garbage"])
    assert res.exit_code == 1
    assert "NOT zero feedback" in res.stdout


def test_cli_json_is_parsable(secrets):
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(_message()))]):
        res = CliRunner().invoke(cli.app, ["notify", "telegram-read", "--json"])
    assert res.exit_code == 0
    assert json.loads(res.stdout)["reported"] == 1


def test_flatten_handles_a_falsy_edited_message():
    """A present-but-empty `edited_message` must not fall through to `message`.

    The guard admits the update on the key alone, so `update.get("edited_message")
    or update["message"]` raised KeyError whenever the value was falsy — an
    update shape Telegram is free to send and the reader cannot refuse.
    """
    flat = ti._flatten({"update_id": 1, "edited_message": {}}, TZ)
    assert flat is not None
    assert flat["kind"] == "edited_message"
    assert flat["update_id"] == 1
    assert flat["text"] == ""
    assert flat["is_bot"] is False


# ----- authorship on a FORWARD (#142) --------------------------------------
#
# Calibrated on the REAL 2026-09-06 18:25:52 payload, captured from getUpdates
# while the defect was found — not on invented text. The originator sits in
# `forward_origin` (Bot API 7.0+) or the legacy `forward_from`, and `from` names
# only the FORWARDER, so an authorship filter reading `from` attributes the
# bot's own prose to the user. Because the harvest phase must classify every
# item it prints, that misattribution lets a run treat its own output as user
# feedback.

_BOT_ORIGIN = {"id": 8946706252, "is_bot": True, "first_name": "Scout", "username": "eshni_scout_bot"}
_HUMAN_ORIGIN = {"id": 111, "is_bot": False, "first_name": "Petr"}
_WRAP_TEXT = "Dreaming wrap - Sat Sep 5, 17:09 to 18:0x CEST\n\nYour one inbox line…"


def _forward(*, origin: dict | None = None, legacy: dict | None = None,
             sender_name: str | None = None, from_chat: dict | None = None,
             text: str = _WRAP_TEXT) -> dict:
    """A forwarded message. Each keyword sets ONE of the four origin shapes."""
    msg = {
        "message_id": 4212,
        "date": EPOCH,
        "from": {"id": 7, "first_name": "Alex", "last_name": "Example", "is_bot": False},
        "forward_date": EPOCH - 89121,
        "text": text,
    }
    if origin is not None:
        msg["forward_origin"] = {"type": "user", "sender_user": origin, "date": EPOCH - 89121}
    if legacy is not None:
        msg["forward_from"] = legacy
    if sender_name is not None:
        msg["forward_sender_name"] = sender_name
    if from_chat is not None:
        msg["forward_from_chat"] = from_chat
    return {"update_id": 1003, "message": msg}


def test_forwarded_bot_message_is_not_counted_as_the_user(secrets):
    """#142's load-bearing assertion, on the real payload: both origin keys present."""
    real = _forward(origin=_BOT_ORIGIN, legacy=_BOT_ORIGIN)
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(real))]):
        rep = ti.read()
    assert rep.fetched == 1
    assert rep.inbound == 0, "Scout's own forwarded prose was counted as the user's"
    assert rep.reported == 0


def test_legacy_forward_from_alone_is_still_detected(secrets):
    """A reader that knows only `forward_origin` is one API rollback from #142."""
    with patch.object(
        ti.requests, "get",
        side_effect=[_resp(_hook()), _resp(_updates(_forward(legacy=_BOT_ORIGIN)))],
    ):
        rep = ti.read()
    assert rep.inbound == 0


def test_modern_forward_origin_alone_is_still_detected(secrets):
    with patch.object(
        ti.requests, "get",
        side_effect=[_resp(_hook()), _resp(_updates(_forward(origin=_BOT_ORIGIN)))],
    ):
        rep = ti.read()
    assert rep.inbound == 0


def test_forward_of_a_human_is_kept_in_the_count_but_labelled(secrets):
    """The filter drops bot-authored text, not forwards. Forwarding is a signal."""
    with patch.object(
        ti.requests, "get",
        side_effect=[_resp(_hook()), _resp(_updates(_forward(origin=_HUMAN_ORIGIN, text="ping")))],
    ):
        rep = ti.read()
    assert rep.inbound == 1
    assert rep.items[0]["forwarded_from"] == "Petr"
    assert rep.items[0]["forward_kind"] == "user"


def test_hidden_user_forward_is_named_not_read_as_the_user(secrets):
    """No id means no `is_bot`: UNKNOWN authorship, reported as such."""
    with patch.object(
        ti.requests, "get",
        side_effect=[_resp(_hook()), _resp(_updates(_forward(sender_name="Someone", text="x")))],
    ):
        rep = ti.read()
    assert rep.items[0]["forwarded_from"] == "Someone"
    assert rep.items[0]["forward_kind"] == "hidden_user"


def test_channel_forward_is_titled_and_typed(secrets):
    chat = {"id": -100, "type": "channel", "title": "Some Channel"}
    with patch.object(
        ti.requests, "get",
        side_effect=[_resp(_hook()), _resp(_updates(_forward(from_chat=chat, text="x")))],
    ):
        rep = ti.read()
    assert rep.items[0]["forwarded_from"] == "Some Channel"
    assert rep.items[0]["forward_kind"] == "chat"


def test_plain_message_carries_no_forward_marker(secrets):
    """Negative control: the fix must not mark ordinary messages as forwards."""
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(_message()))]):
        rep = ti.read()
    assert rep.items[0]["forwarded_from"] is None
    assert rep.items[0]["forward_kind"] is None
    assert rep.items[0]["is_bot"] is False


def test_reaction_carries_the_forward_keys_as_none(secrets):
    """One item shape. A renderer reading `forwarded_from` must not KeyError."""
    rx = {
        "update_id": 1002,
        "message_reaction": {
            "message_id": 42, "date": EPOCH,
            "user": {"id": 7, "first_name": "Alex", "is_bot": False},
            "new_reaction": [{"emoji": "👍"}],
        },
    }
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(rx))]):
        rep = ti.read()
    assert rep.items[0]["forwarded_from"] is None


def test_bot_authored_forward_is_reported_separately_not_dropped(secrets):
    """Excluded from the user count, but surfaced: it says what he pointed at."""
    real = _forward(origin=_BOT_ORIGIN, legacy=_BOT_ORIGIN)
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(real))]):
        rep = ti.read()
    assert len(rep.bot_authored_forwards) == 1
    assert rep.bot_authored_forwards[0]["forwarded_from"] == "Scout"


def test_render_never_claims_every_item_is_the_user_when_a_forward_is_present(secrets):
    """The banner is the defect's public face: it asserted authorship it had not tested."""
    real = _forward(origin=_BOT_ORIGIN, legacy=_BOT_ORIGIN)
    with patch.object(
        ti.requests, "get",
        side_effect=[_resp(_hook()), _resp(_updates(_message("real feedback"), real))],
    ):
        rep = ti.read()
    text = ti.render(rep)
    assert "every one is the user" not in text
    assert "FORWARDED" in text
    assert "Scout" in text


def test_render_mentions_a_forward_even_when_the_user_wrote_nothing(secrets):
    """'0 inbound' with a forwarded wrap unmentioned is true and misleading at once."""
    real = _forward(origin=_BOT_ORIGIN, legacy=_BOT_ORIGIN)
    with patch.object(ti.requests, "get", side_effect=[_resp(_hook()), _resp(_updates(real))]):
        rep = ti.read()
    text = ti.render(rep)
    assert "0 inbound" in text
    assert "FORWARDED" in text


def test_fault_with_only_a_bot_forward_still_hands_it_over(secrets):
    """A fault must not swallow a retrieved forward.

    The refusal is scoped to the COUNT, never to what came back. With a webhook
    registered the queue's emptiness is untrustworthy, but the forward was
    genuinely retrieved — and Telegram drops unconsumed updates after ~24h, so a
    render that drops it can be destroying the only copy.
    """
    real = _forward(origin=_BOT_ORIGIN, legacy=_BOT_ORIGIN)
    with patch.object(
        ti.requests, "get",
        side_effect=[_resp(_hook(url="https://example.test/hook")), _resp(_updates(real))],
    ):
        rep = ti.read()
    assert not rep.ok
    text = ti.render(rep)
    assert "FORWARDED" in text
    assert "do not report a signal count" in text


def test_cli_json_carries_the_forward_split(secrets):
    """The JSON consumer must be able to see both populations separately."""
    real = _forward(origin=_BOT_ORIGIN, legacy=_BOT_ORIGIN)
    with patch.object(
        ti.requests, "get",
        side_effect=[_resp(_hook()), _resp(_updates(_message("real"), real))],
    ):
        res = CliRunner().invoke(cli.app, ["notify", "telegram-read", "--json"])
    assert res.exit_code == 0
    payload = json.loads(res.stdout)
    assert payload["inbound"] == 1
    assert len(payload["bot_authored_forwards"]) == 1
    assert payload["bot_authored_forwards"][0]["forwarded_from"] == "Scout"
