"""The trigger sources' real I/O layer: secret reading, config lookup, the
`gh` subprocess wrapper, and the malformed-input guards.

`test_triggers_sources.py` drives each source through its injected seams
(`token_reader`, `http_get`, `run_gh`) — which is right for the normalization
logic but leaves the *default* implementations untested. Those defaults are
what run in production: `_read_token` (mode-600 enforcement),
`_user_id_from_config` (YAML lookup), `_default_run_gh` (missing/hung `gh`),
`_default_http_get` (requests). Each has a failure path an operator hits on a
half-finished install.

Payloads are anonymized per CLAUDE.md.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scout.errors import ConfigError
from scout.triggers.sources import github as gh_source
from scout.triggers.sources import slack as slack_source
from scout.triggers.sources.github import GitHubSource
from scout.triggers.sources.scout_internal import ScoutInternalSource
from scout.triggers.sources.slack import SlackSource

SINCE = "2026-07-01T12:00:00Z"


# ---------------------------------------------------------------------------
# slack._read_token
# ---------------------------------------------------------------------------


@pytest.fixture
def secrets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / ".scout-secrets"
    d.mkdir()
    monkeypatch.setattr(slack_source, "SECRETS_DIR", d)
    return d


def test_read_token_returns_the_stripped_value(secrets_dir: Path) -> None:
    token = secrets_dir / slack_source.TOKEN_FILENAME
    token.write_text("xoxp-test-token\n", encoding="utf-8")
    token.chmod(0o600)
    assert slack_source._read_token() == "xoxp-test-token"


def test_read_token_names_the_file_when_absent(secrets_dir: Path) -> None:
    with pytest.raises(ConfigError, match="Missing secret:.*slack-search-token"):
        slack_source._read_token()
    # The message must say what scope the token needs.
    with pytest.raises(ConfigError, match="search:read"):
        slack_source._read_token()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666, 0o755])
def test_read_token_rejects_anything_looser_than_600(secrets_dir: Path, mode: int) -> None:
    """A token saved with the default umask is readable by every process on the
    host; refusing to read it is the safe default."""
    token = secrets_dir / slack_source.TOKEN_FILENAME
    token.write_text("xoxp-test-token\n", encoding="utf-8")
    token.chmod(mode)
    with pytest.raises(ConfigError, match="insecure permissions"):
        slack_source._read_token()


def test_read_token_rejects_an_empty_file(secrets_dir: Path) -> None:
    token = secrets_dir / slack_source.TOKEN_FILENAME
    token.write_text("  \n", encoding="utf-8")
    token.chmod(0o600)
    with pytest.raises(ConfigError, match="Secret file is empty"):
        slack_source._read_token()


# ---------------------------------------------------------------------------
# slack._user_id_from_config / SlackSource.for_vault
# ---------------------------------------------------------------------------


def test_user_id_is_read_from_the_vault_config(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text(
        "connectors:\n  inputs:\n    user_slack_id: U0123456789\n", encoding="utf-8"
    )
    assert slack_source._user_id_from_config(tmp_path) == "U0123456789"
    assert SlackSource.for_vault(tmp_path)._user_id == "U0123456789"


@pytest.mark.parametrize(
    "body",
    [
        "",  # empty file -> `or {}`
        "connectors: {}\n",  # no inputs
        "connectors:\n  inputs: {}\n",  # no user_slack_id
        "connectors:\n  inputs:\n    user_slack_id: ''\n",  # blank value
        "connectors:\n  inputs: null\n",  # explicit null
        "connectors: null\n",
    ],
)
def test_user_id_is_none_when_the_config_does_not_declare_one(tmp_path: Path, body: str) -> None:
    (tmp_path / "scout-config.yaml").write_text(body, encoding="utf-8")
    assert slack_source._user_id_from_config(tmp_path) is None


def test_user_id_is_none_without_a_config_file(tmp_path: Path) -> None:
    assert slack_source._user_id_from_config(tmp_path) is None


def test_user_id_is_none_for_a_malformed_config(tmp_path: Path) -> None:
    """A broken scout-config.yaml must degrade to "slack not configured", which
    the health check reports cleanly — not raise out of the source factory."""
    (tmp_path / "scout-config.yaml").write_text("connectors: [unclosed\n", encoding="utf-8")
    assert slack_source._user_id_from_config(tmp_path) is None


def test_user_id_is_none_for_an_unreadable_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "scout-config.yaml"
    cfg.write_text("connectors:\n  inputs:\n    user_slack_id: U1\n", encoding="utf-8")

    real_read_text = Path.read_text

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == cfg:
            raise OSError("permission denied")
        return real_read_text(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", maybe_boom)
    assert slack_source._user_id_from_config(tmp_path) is None


# ---------------------------------------------------------------------------
# slack scan/health edges
# ---------------------------------------------------------------------------


def test_slack_scan_without_a_user_id_raises_configerror() -> None:
    src = SlackSource(user_id=None, token_reader=lambda: "t", http_get=lambda *a, **k: {"ok": True})
    with pytest.raises(ConfigError, match="user_slack_id is not configured"):
        src.scan_since(SINCE)


def test_slack_scan_skips_a_match_with_an_unparseable_timestamp() -> None:
    """Slack's `ts` is a float-as-string; a row we can't parse is skipped rather
    than crashing the whole tick."""
    payload = {
        "ok": True,
        "messages": {
            "matches": [
                {"ts": "not-a-float", "user": "U2", "text": "bad row"},
                {"ts": "", "user": "U2", "text": "missing ts"},
                {"user": "U2", "text": "no ts key at all"},
                {
                    "ts": "1782910800.000200",
                    "user": "U0000000002",
                    "username": "priya",
                    "text": "ping <@U0123456789>",
                    "channel": {"id": "C0123456789", "name": "general"},
                },
            ]
        },
    }
    src = SlackSource(user_id="U0123456789", token_reader=lambda: "t", http_get=lambda *a, **k: payload)
    events = src.scan_since(SINCE)
    assert [e.normalized_match_fields["text"] for e in events] == ["ping <@U0123456789>"]


@pytest.mark.parametrize(
    "payload",
    [{"ok": True}, {"ok": True, "messages": {}}, {"ok": True, "messages": None}],
)
def test_slack_scan_tolerates_a_response_with_no_matches(payload: dict) -> None:
    src = SlackSource(user_id="U1", token_reader=lambda: "t", http_get=lambda *a, **k: payload)
    assert src.scan_since(SINCE) == []


def test_slack_scan_flags_self_authored_mentions() -> None:
    payload = {
        "ok": True,
        "messages": {
            "matches": [
                {"ts": "1782910900.000300", "user": "U0123456789", "text": "note to self"},
                {"ts": "1782910800.000200", "user": "U0000000002", "text": "ping"},
            ]
        },
    }
    src = SlackSource(user_id="U0123456789", token_reader=lambda: "t", http_get=lambda *a, **k: payload)
    by_self = {e.source_event_id: e.normalized_match_fields["is_self"] for e in src.scan_since(SINCE)}
    assert by_self == {"1782910900.000300": True, "1782910800.000200": False}


def test_slack_health_check_reports_a_missing_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack_source, "SECRETS_DIR", tmp_path / "nope")
    healthy, reason = SlackSource(user_id="U1").health_check()
    assert healthy is False
    assert "Missing secret" in reason


def test_slack_default_http_get_uses_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            seen["raised_for_status"] = True

        def json(self) -> dict:
            return {"ok": True}

    def fake_get(url: str, **kwargs: object):
        seen.update(url=url, **kwargs)
        return FakeResponse()

    monkeypatch.setattr("requests.get", fake_get)
    out = slack_source._default_http_get(
        slack_source.SEARCH_URL, params={"query": "x"}, headers={"Authorization": "Bearer t"}, timeout=1.0
    )
    assert out == {"ok": True}
    assert seen["url"] == slack_source.SEARCH_URL
    assert seen["timeout"] == 1.0
    # A non-2xx must surface, not be swallowed into an "ok: false" dict.
    assert seen["raised_for_status"] is True


def test_slack_parse_iso_z_handles_both_offset_forms() -> None:
    with_z = slack_source._parse_iso_z("2026-07-01T12:00:00Z")
    with_offset = slack_source._parse_iso_z("2026-07-01T12:00:00+00:00")
    assert with_z == with_offset


# ---------------------------------------------------------------------------
# github._default_run_gh
# ---------------------------------------------------------------------------


def test_default_run_gh_returns_the_completed_process_triple(monkeypatch: pytest.MonkeyPatch) -> None:
    class Proc:
        returncode = 0
        stdout = "[]"
        stderr = ""

    seen: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.append(argv) or Proc())
    assert gh_source._default_run_gh(["api", "notifications"]) == (0, "[]", "")
    assert seen == [["gh", "api", "notifications"]]


def test_default_run_gh_reports_a_missing_gh_as_127(monkeypatch: pytest.MonkeyPatch) -> None:
    """127 is the shell's "command not found"; the health check surfaces it as
    an actionable "install gh" rather than a traceback."""

    def boom(*_a: object, **_k: object):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(subprocess, "run", boom)
    rc, stdout, stderr = gh_source._default_run_gh(["auth", "status"])
    assert (rc, stdout) == (127, "")
    assert "command not found" in stderr


def test_default_run_gh_reports_a_timeout_as_124(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object):
        raise subprocess.TimeoutExpired("gh", gh_source.GH_TIMEOUT_SECONDS)

    monkeypatch.setattr(subprocess, "run", boom)
    rc, _stdout, stderr = gh_source._default_run_gh(["api", "notifications"])
    assert rc == 124
    assert f"timed out after {gh_source.GH_TIMEOUT_SECONDS}s" in stderr


def test_github_source_uses_the_default_runner_when_none_is_injected() -> None:
    assert GitHubSource()._run_gh is gh_source._default_run_gh


# ---------------------------------------------------------------------------
# github scan guards
# ---------------------------------------------------------------------------


def _gh(stdout: str, rc: int = 0, stderr: str = "") -> GitHubSource:
    return GitHubSource(run_gh=lambda _args: (rc, stdout, stderr))


def test_github_scan_rejects_non_json_output() -> None:
    with pytest.raises(ConfigError, match="non-JSON output"):
        _gh("<html>rate limited</html>").scan_since(SINCE)


def test_github_scan_rejects_a_json_object() -> None:
    """`gh api` returns an object for an error body; treating it as a thread
    list would silently yield zero events instead of surfacing the failure."""
    with pytest.raises(ConfigError, match="expected a JSON array"):
        _gh('{"message": "Bad credentials"}').scan_since(SINCE)


def test_github_scan_treats_empty_output_as_no_threads() -> None:
    assert _gh("").scan_since(SINCE) == []


def test_github_scan_skips_non_dict_and_stale_threads() -> None:
    threads = [
        "not a dict",
        {"id": "1", "updated_at": "2026-07-01T11:00:00Z", "reason": "mention"},  # older than SINCE
        {"id": "2", "reason": "mention"},  # no updated_at
        {"id": "3", "updated_at": SINCE, "reason": "mention"},  # exactly SINCE -> excluded
        {
            "id": "4",
            "updated_at": "2026-07-01T13:00:00Z",
            "reason": "review_requested",
            "subject": {"title": "Fix the parser", "type": "PullRequest", "url": "https://api.github.com/x"},
            "repository": {"full_name": "example-org/widgets"},
        },
    ]
    events = _gh(json.dumps(threads)).scan_since(SINCE)
    assert [e.source_event_id for e in events] == ["4:2026-07-01T13:00:00Z"]
    assert events[0].normalized_match_fields["repo"] == "example-org/widgets"
    assert events[0].normalized_match_fields["type"] == "review_requested"


def test_github_scan_tolerates_threads_with_no_subject_or_repository() -> None:
    threads = [{"id": "5", "updated_at": "2026-07-01T13:00:00Z", "reason": "subscribed"}]
    fields = _gh(json.dumps(threads)).scan_since(SINCE)[0].normalized_match_fields
    assert fields["title"] == "" and fields["repo"] == "" and fields["subject_type"] == ""


def test_github_scan_failure_message_falls_back_to_stdout() -> None:
    """`gh` sometimes writes the error to stdout; the raised message must carry
    whichever stream has content."""
    with pytest.raises(ConfigError, match="gh: not logged in"):
        _gh("gh: not logged in", rc=1, stderr="").scan_since(SINCE)


def test_github_events_sort_by_timestamp() -> None:
    threads = [
        {"id": "b", "updated_at": "2026-07-01T15:00:00Z", "reason": "mention"},
        {"id": "a", "updated_at": "2026-07-01T13:00:00Z", "reason": "mention"},
    ]
    assert [e.normalized_match_fields["thread_id"] for e in _gh(json.dumps(threads)).scan_since(SINCE)] == [
        "a",
        "b",
    ]


# ---------------------------------------------------------------------------
# scout_internal scan guards
# ---------------------------------------------------------------------------


def _log(log_dir: Path, date: str, *rows: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / f"schedule-events-{date}.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def _row(event_id: str, ts: str, kind: str, **payload: object) -> str:
    return json.dumps({"id": event_id, "ts": ts, "kind": kind, "source": "test", "payload": payload})


def test_scout_internal_skips_log_files_dated_before_the_scan_window(tmp_path: Path) -> None:
    """Filenames are UTC-dated, so a whole earlier file can be skipped without
    reading it — this is the optimization that keeps a tick cheap on a mature
    vault with months of logs."""
    _log(tmp_path, "2026-06-30", _row("old", "2026-06-30T23:59:00Z", "slot.fired"))
    _log(tmp_path, "2026-07-01", _row("new", "2026-07-01T13:00:00Z", "slot.fired"))

    events = ScoutInternalSource(tmp_path).scan_since(SINCE)
    assert [e.source_event_id for e in events] == ["new"]


@pytest.mark.parametrize(
    "row",
    [
        "",
        "   ",
        "{not json",
        '"a bare string"',
        "[1, 2]",
        json.dumps({"ts": "2026-07-01T13:00:00Z", "kind": "slot.fired"}),  # no id
        json.dumps({"id": "x", "kind": "slot.fired"}),  # no ts
        json.dumps({"id": "x", "ts": "2026-07-01T13:00:00Z"}),  # no kind
        json.dumps({"id": 1, "ts": "2026-07-01T13:00:00Z", "kind": "slot.fired"}),  # non-str id
    ],
)
def test_scout_internal_skips_unusable_rows(tmp_path: Path, row: str) -> None:
    _log(tmp_path, "2026-07-01", row, _row("good", "2026-07-01T13:00:00Z", "slot.fired"))
    events = ScoutInternalSource(tmp_path).scan_since(SINCE)
    assert [e.source_event_id for e in events] == ["good"]


def test_scout_internal_excludes_rows_at_or_before_the_scan_timestamp(tmp_path: Path) -> None:
    _log(
        tmp_path,
        "2026-07-01",
        _row("before", "2026-07-01T11:00:00Z", "slot.fired"),
        _row("exactly", SINCE, "slot.fired"),
        _row("after", "2026-07-01T13:00:00Z", "slot.fired"),
    )
    events = ScoutInternalSource(tmp_path).scan_since(SINCE)
    assert [e.source_event_id for e in events] == ["after"]


def test_scout_internal_survives_an_unreadable_log_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = _log(tmp_path, "2026-07-01", _row("a", "2026-07-01T13:00:00Z", "slot.fired"))
    _log(tmp_path, "2026-07-02", _row("b", "2026-07-02T13:00:00Z", "slot.fired"))

    real_read_text = Path.read_text

    def maybe_boom(self: Path, *a: object, **k: object):
        if self == bad:
            raise OSError("permission denied")
        return real_read_text(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", maybe_boom)
    assert [e.source_event_id for e in ScoutInternalSource(tmp_path).scan_since(SINCE)] == ["b"]


def test_scout_internal_normalizes_kind_over_a_payload_type_key(tmp_path: Path) -> None:
    """`type` in the match fields must be the event *kind*; a payload that also
    carries a `type` key must not shadow it, or the matcher fires on the wrong
    events."""
    _log(
        tmp_path,
        "2026-07-01",
        _row("a", "2026-07-01T13:00:00Z", "slot.fired", type="something-else", slot_key="morning-briefing"),
    )
    fields = ScoutInternalSource(tmp_path).scan_since(SINCE)[0].normalized_match_fields
    assert fields["type"] == "slot.fired"
    assert fields["slot_key"] == "morning-briefing"
    assert fields["event_source"] == "test"


def test_scout_internal_tolerates_a_row_with_a_non_dict_payload(tmp_path: Path) -> None:
    _log(
        tmp_path,
        "2026-07-01",
        json.dumps({"id": "a", "ts": "2026-07-01T13:00:00Z", "kind": "slot.fired", "payload": "oops"}),
    )
    fields = ScoutInternalSource(tmp_path).scan_since(SINCE)[0].normalized_match_fields
    assert fields["type"] == "slot.fired"
    assert fields["event_source"] == ""


def test_scout_internal_events_sort_by_timestamp_across_files(tmp_path: Path) -> None:
    _log(tmp_path, "2026-07-02", _row("second", "2026-07-02T09:00:00Z", "slot.fired"))
    _log(tmp_path, "2026-07-01", _row("first", "2026-07-01T13:00:00Z", "slot.fired"))
    events = ScoutInternalSource(tmp_path).scan_since(SINCE)
    assert [e.source_event_id for e in events] == ["first", "second"]


def test_scout_internal_is_healthy_with_a_log_dir(tmp_path: Path) -> None:
    assert ScoutInternalSource(tmp_path).health_check() == (True, "ok")


# ---------------------------------------------------------------------------
# Registry / ConnectorEvent
# ---------------------------------------------------------------------------


def test_supported_match_types_returns_each_sources_constant() -> None:
    from scout.triggers.sources import supported_match_types

    assert supported_match_types("slack") == slack_source.SUPPORTED_MATCH_TYPES
    assert supported_match_types("github") == gh_source.SUPPORTED_MATCH_TYPES
    # The returned list is a copy — a caller mutating it must not corrupt the
    # module constant that config validation reads on every load.
    got = supported_match_types("github")
    got.append("mutated")
    assert "mutated" not in gh_source.SUPPORTED_MATCH_TYPES


def test_connector_event_match_type_reads_the_type_field() -> None:
    from scout.triggers.sources.base import ConnectorEvent

    with_type = ConnectorEvent(
        source="github", source_event_id="1", ts=SINCE, normalized_match_fields={"type": "mention"}
    )
    assert with_type.match_type == "mention"
    # No `type` key at all -> empty string, not a KeyError, so the matcher can
    # compare it like any other event.
    assert ConnectorEvent(source="github", source_event_id="1", ts=SINCE).match_type == ""
