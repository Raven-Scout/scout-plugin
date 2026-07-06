"""Trigger source pollers: slack, github, scout_internal.

All fixture payloads are synthetic/anonymized per CLAUDE.md (people
Alex/Priya/Sam, GitHub example-org/<repo>, Slack acme-co workspace).
"""

from __future__ import annotations

import json

import pytest

from scout.errors import ConfigError
from scout.triggers.sources import get_source, supported_match_types
from scout.triggers.sources.github import GitHubSource
from scout.triggers.sources.scout_internal import ScoutInternalSource
from scout.triggers.sources.slack import SlackSource

SINCE = "2026-07-01T12:00:00Z"  # epoch 1782648000... (only relative order matters)

# ----- registry ---------------------------------------------------------------


def test_supported_match_types_unknown_source_raises():
    with pytest.raises(ConfigError):
        supported_match_types("carrier_pigeon")


def test_get_source_returns_each_v1_source(tmp_path):
    for name in ("slack", "github", "scout_internal"):
        src = get_source(name, vault=tmp_path)
        assert src.name == name
        assert src.SUPPORTED_MATCH_TYPES
        assert src.supports_webhook() is False


def test_get_source_unknown_raises(tmp_path):
    with pytest.raises(ConfigError):
        get_source("carrier_pigeon", vault=tmp_path)


# ----- slack --------------------------------------------------------------------


def _slack_api_payload() -> dict:
    def msg(ts: str, user: str, username: str, text: str) -> dict:
        return {
            "ts": ts,
            "user": user,
            "username": username,
            "text": text,
            "channel": {"id": "C0123456789", "name": "general"},
            "permalink": f"https://acme-co.slack.com/archives/C0123456789/p{ts.replace('.', '')}",
        }

    return {
        "ok": True,
        "messages": {
            "matches": [
                # 2026-07-01T13:00:00Z — after SINCE (12:00Z = epoch 1782907200).
                msg("1782910800.000200", "U0000000002", "priya", "ping <@U0123456789> can you review?"),
                # Authored by the Scout user themselves.
                msg("1782910900.000300", "U0123456789", "alex", "note to self <@U0123456789>"),
                # 2026-07-01T11:00:00Z — before SINCE; must be filtered out.
                msg("1782903600.000100", "U0000000003", "sam", "old mention <@U0123456789>"),
            ]
        },
    }


def _slack_source(payload: dict, calls: list | None = None) -> SlackSource:
    def http_get(url: str, *, params: dict, headers: dict, timeout: float):
        if calls is not None:
            calls.append((url, params, headers))
        return payload

    return SlackSource(
        user_id="U0123456789",
        token_reader=lambda: "xoxp-test-token",
        http_get=http_get,
    )


def test_slack_scan_since_filters_and_normalizes():
    calls: list = []
    events = _slack_source(_slack_api_payload(), calls).scan_since(SINCE)

    # One combined query for all slack triggers.
    assert len(calls) == 1
    url, params, headers = calls[0]
    assert "search.messages" in url
    assert "<@U0123456789>" in params["query"]
    assert headers["Authorization"] == "Bearer xoxp-test-token"

    # The pre-SINCE message is dropped.
    assert len(events) == 2
    by_priya, by_self = events
    assert by_priya.source == "slack"
    assert by_priya.source_event_id == "1782910800.000200"
    f = by_priya.normalized_match_fields
    assert f["type"] == "mention"
    assert f["author"] == "U0000000002"
    assert f["channel"] == "C0123456789"
    assert f["is_self"] is False
    assert "can you review" in f["text"]
    assert by_self.normalized_match_fields["is_self"] is True


def test_slack_api_error_raises():
    src = _slack_source({"ok": False, "error": "invalid_auth"})
    with pytest.raises(ConfigError, match="invalid_auth"):
        src.scan_since(SINCE)


def test_slack_health_check_reports_missing_user_id():
    src = SlackSource(user_id=None, token_reader=lambda: "xoxp-test-token", http_get=lambda *a, **k: {})
    healthy, reason = src.health_check()
    assert healthy is False
    assert "user" in reason.lower()


def test_slack_health_check_ok_with_token_and_user():
    src = _slack_source(_slack_api_payload())
    assert src.health_check() == (True, "ok")


# ----- github ---------------------------------------------------------------------


def _gh_notifications() -> list[dict]:
    return [
        {
            "id": "1001",
            "reason": "review_requested",
            "updated_at": "2026-07-01T13:05:00Z",
            "unread": True,
            "repository": {"full_name": "example-org/widget-factory"},
            "subject": {
                "title": "feat: add conveyor belt",
                "type": "PullRequest",
                "url": "https://api.github.com/repos/example-org/widget-factory/pulls/42",
            },
        },
        {
            "id": "1002",
            "reason": "mention",
            "updated_at": "2026-07-01T11:00:00Z",  # before SINCE → filtered
            "unread": True,
            "repository": {"full_name": "example-org/gadget-works"},
            "subject": {
                "title": "bug: gears misaligned",
                "type": "Issue",
                "url": "https://api.github.com/repos/example-org/gadget-works/issues/7",
            },
        },
    ]


def test_github_scan_since_maps_reason_to_match_type():
    calls: list = []

    def run_gh(args: list[str]) -> tuple[int, str, str]:
        calls.append(args)
        return 0, json.dumps(_gh_notifications()), ""

    events = GitHubSource(run_gh=run_gh).scan_since(SINCE)

    assert calls and calls[0][0] == "api"
    assert f"since={SINCE}" in calls[0][1]

    assert len(events) == 1
    (ev,) = events
    assert ev.source == "github"
    assert ev.source_event_id == "1001:2026-07-01T13:05:00Z"
    f = ev.normalized_match_fields
    assert f["type"] == "review_requested"
    assert f["repo"] == "example-org/widget-factory"
    assert f["title"] == "feat: add conveyor belt"
    assert f["subject_type"] == "PullRequest"


def test_github_gh_failure_raises():
    src = GitHubSource(run_gh=lambda args: (1, "", "gh: Not logged in"))
    with pytest.raises(ConfigError, match="gh"):
        src.scan_since(SINCE)


def test_github_health_check_uses_auth_status():
    ok_src = GitHubSource(run_gh=lambda args: (0, "Logged in", ""))
    assert ok_src.health_check() == (True, "ok")
    bad_src = GitHubSource(run_gh=lambda args: (1, "", "not logged in"))
    healthy, _reason = bad_src.health_check()
    assert healthy is False


# ----- scout_internal ------------------------------------------------------------


def _write_events_log(log_dir, date: str, rows: list[dict]) -> None:
    p = log_dir / f"schedule-events-{date}.jsonl"
    with p.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_scout_internal_reads_engine_event_stream(tmp_path):
    log_dir = tmp_path / ".scout-logs"
    log_dir.mkdir()
    _write_events_log(
        log_dir,
        "2026-06-30",
        [
            {
                "id": "01OLD000000000000000000000",
                "ts": "2026-06-30T09:00:00.000Z",
                "kind": "slot.fired",
                "source": "cli:schedule_tick",
                "payload": {"slot_key": "morning-briefing"},
            }
        ],
    )
    _write_events_log(
        log_dir,
        "2026-07-01",
        [
            {
                "id": "01NEW000000000000000000000",
                "ts": "2026-07-01T13:00:00.000Z",
                "kind": "slot.fire_failed",
                "source": "cli:schedule_tick",
                "payload": {"slot_key": "research", "error": "FileNotFoundError: runner"},
            },
            {"not": "a valid row"},  # malformed rows are skipped, not fatal
        ],
    )

    events = ScoutInternalSource(log_dir).scan_since(SINCE)

    assert len(events) == 1
    (ev,) = events
    assert ev.source == "scout_internal"
    assert ev.source_event_id == "01NEW000000000000000000000"
    f = ev.normalized_match_fields
    assert f["type"] == "slot.fire_failed"
    assert f["slot_key"] == "research"
    assert f["event_source"] == "cli:schedule_tick"


def test_scout_internal_missing_log_dir_is_unhealthy_and_empty(tmp_path):
    src = ScoutInternalSource(tmp_path / "missing")
    healthy, _ = src.health_check()
    assert healthy is False
    assert src.scan_since(SINCE) == []
