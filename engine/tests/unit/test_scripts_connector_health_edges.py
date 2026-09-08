"""Tolerance, cleanup and notification branches of the connector health report.

`test_scripts_connector_health.py` covers the alerting rules against
well-formed logs. This file covers the surrounding I/O:

* `load_records`'s skip rules — the JSONL is appended by a live hook, so torn
  lines, out-of-window rows and interactive-session rows must all be dropped
  rather than skewing the matrix or crashing the report.
* `cleanup_old_jsonl`'s retention pass, which deletes files. A greedy or
  crashing cleanup either loses audit history or wedges the report.
* `fire_macos_notification`'s argv-not-source AppleScript construction (#51):
  connector names come from user-editable YAML, so interpolating them into the
  script source would allow AppleScript injection.
* `run()`'s pending-file lifecycle and `main()`'s exit semantics.

Payloads are anonymized per CLAUDE.md.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scout.scripts import connector_health_report as chr_mod
from scout.scripts.connector_health_report import Alert

NOW = datetime(2026, 5, 28, 14, 0, tzinfo=UTC)


def _row(ts: datetime, sid: str = "s1", mode: str = "morning-briefing", **fields) -> dict:
    rec = {
        "ts": ts.isoformat().replace("+00:00", "Z"),
        "session_id": sid,
        "mode": mode,
        "tool": "Bash",
        "connector": "slack",
        "error": False,
    }
    rec.update(fields)
    return rec


def _log(log_dir: Path, date: str, *rows: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / f"connector-calls-{date}.jsonl"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _default_now
# ---------------------------------------------------------------------------


def test_the_default_clock_is_tz_aware_utc() -> None:
    """Every window comparison is against this; a naive value would raise on
    the first `<` against a tz-aware record timestamp."""
    assert chr_mod._default_now().tzinfo is UTC


# ---------------------------------------------------------------------------
# load_records
# ---------------------------------------------------------------------------


def test_records_are_loaded_from_every_matching_file(tmp_path: Path) -> None:
    _log(tmp_path, "2026-05-27", json.dumps(_row(NOW - timedelta(days=1), sid="s0")))
    _log(tmp_path, "2026-05-28", json.dumps(_row(NOW, sid="s1")))
    records = chr_mod.load_records(tmp_path, window_days=14, now=NOW)
    assert {r["session_id"] for r in records} == {"s0", "s1"}
    assert all(isinstance(r["_ts"], datetime) for r in records)


def test_records_outside_the_window_are_dropped(tmp_path: Path) -> None:
    _log(
        tmp_path,
        "2026-05-28",
        json.dumps(_row(NOW - timedelta(days=30), sid="ancient")),
        json.dumps(_row(NOW, sid="recent")),
    )
    records = chr_mod.load_records(tmp_path, window_days=14, now=NOW)
    assert {r["session_id"] for r in records} == {"recent"}


@pytest.mark.parametrize("mode", ["interactive", "unknown"])
def test_interactive_and_unknown_mode_rows_are_dropped(tmp_path: Path, mode: str) -> None:
    """The report measures *scheduled* runs; a human's interactive session has
    no expected connector profile and would poison the baseline."""
    _log(
        tmp_path,
        "2026-05-28",
        json.dumps(_row(NOW, sid="human", mode=mode)),
        json.dumps(_row(NOW, sid="scheduled")),
    )
    records = chr_mod.load_records(tmp_path, window_days=14, now=NOW)
    assert {r["session_id"] for r in records} == {"scheduled"}


def test_a_row_with_no_mode_defaults_to_interactive_and_is_dropped(tmp_path: Path) -> None:
    row = _row(NOW, sid="modeless")
    del row["mode"]
    _log(tmp_path, "2026-05-28", json.dumps(row), json.dumps(_row(NOW, sid="scheduled")))
    records = chr_mod.load_records(tmp_path, window_days=14, now=NOW)
    assert {r["session_id"] for r in records} == {"scheduled"}


def test_unusable_rows_are_skipped(tmp_path: Path) -> None:
    """The log is appended by a PostToolUse hook under an flock; a crash can
    still leave a torn final line."""
    bad_ts = _row(NOW, sid="bad-ts")
    bad_ts["ts"] = "not-a-date"
    no_ts = _row(NOW, sid="no-ts")
    del no_ts["ts"]
    non_str_ts = _row(NOW, sid="non-str-ts")
    non_str_ts["ts"] = 1700000000

    _log(
        tmp_path,
        "2026-05-28",
        "{torn",
        "",
        json.dumps(bad_ts),
        json.dumps(no_ts),
        json.dumps(non_str_ts),
        json.dumps(_row(NOW, sid="good")),
    )
    records = chr_mod.load_records(tmp_path, window_days=14, now=NOW)
    assert {r["session_id"] for r in records} == {"good"}


def test_an_unreadable_log_file_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = _log(tmp_path, "2026-05-27", json.dumps(_row(NOW, sid="unreadable")))
    _log(tmp_path, "2026-05-28", json.dumps(_row(NOW, sid="good")))

    import builtins

    real_open = builtins.open

    def maybe_boom(path, *a, **k):
        if str(path) == str(bad):
            raise OSError("permission denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", maybe_boom)
    records = chr_mod.load_records(tmp_path, window_days=14, now=NOW)
    assert {r["session_id"] for r in records} == {"good"}


def test_no_log_files_yields_no_records(tmp_path: Path) -> None:
    assert chr_mod.load_records(tmp_path, window_days=14, now=NOW) == []


# ---------------------------------------------------------------------------
# cleanup_old_jsonl
# ---------------------------------------------------------------------------


def test_cleanup_deletes_only_files_older_than_the_retention_window(tmp_path: Path) -> None:
    old = _log(tmp_path, "2026-01-01", json.dumps(_row(NOW)))
    recent = _log(tmp_path, "2026-05-28", json.dumps(_row(NOW)))

    chr_mod.cleanup_old_jsonl(tmp_path, retain_days=30, now=NOW)
    assert not old.exists()
    assert recent.exists()


def test_cleanup_ignores_a_file_whose_name_is_not_a_date(tmp_path: Path) -> None:
    """A hand-renamed or partially-written filename must be left alone, not
    crash the retention pass mid-way and skip the rest."""
    weird = tmp_path / "connector-calls-backup.jsonl"
    weird.write_text("{}\n", encoding="utf-8")
    old = _log(tmp_path, "2026-01-01", json.dumps(_row(NOW)))

    chr_mod.cleanup_old_jsonl(tmp_path, retain_days=30, now=NOW)
    assert weird.exists()
    assert not old.exists()


def test_cleanup_survives_a_failed_unlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _log(tmp_path, "2026-01-01", json.dumps(_row(NOW)))
    older = _log(tmp_path, "2026-01-02", json.dumps(_row(NOW)))

    import os

    real_remove = os.remove

    def maybe_boom(path, *a, **k):
        if str(path) == str(old):
            raise OSError("permission denied")
        return real_remove(path, *a, **k)

    monkeypatch.setattr(os, "remove", maybe_boom)
    chr_mod.cleanup_old_jsonl(tmp_path, retain_days=30, now=NOW)
    assert old.exists()  # the failure is swallowed...
    assert not older.exists()  # ...and the loop continues


def test_cleanup_on_an_empty_dir_is_a_no_op(tmp_path: Path) -> None:
    chr_mod.cleanup_old_jsonl(tmp_path, retain_days=30, now=NOW)  # must not raise


# ---------------------------------------------------------------------------
# fmt_ts / recent_error_sample / last_healthy_ts
# ---------------------------------------------------------------------------


def test_a_missing_timestamp_renders_as_never() -> None:
    assert chr_mod.fmt_ts(None) == "never"


def test_a_timestamp_renders_in_eastern_time() -> None:
    out = chr_mod.fmt_ts(datetime(2026, 5, 28, 16, 30, tzinfo=UTC))
    # The real zone abbreviation, not a literal "ET" — so it reads EDT in
    # summer and EST in winter rather than being wrong for half the year.
    assert out.endswith("EDT")
    assert "12:30" in out  # 16:30 UTC is 12:30 EDT


def test_the_error_sample_is_the_most_recent_non_empty_snippet() -> None:
    records = [
        {"connector": "slack", "error": True, "err": "first failure"},
        {"connector": "slack", "error": True, "err": ""},  # blank -> keep looking back
        {"connector": "github", "error": True, "err": "wrong connector"},
        {"connector": "slack", "error": False, "err": "not an error"},
    ]
    assert chr_mod.recent_error_sample(records, "slack") == "first failure"


def test_the_error_sample_is_truncated() -> None:
    records = [{"connector": "slack", "error": True, "err": "x" * 500}]
    assert len(chr_mod.recent_error_sample(records, "slack", limit=140)) == 140


def test_the_error_sample_is_empty_when_nothing_matches() -> None:
    assert chr_mod.recent_error_sample([], "slack") == ""
    assert chr_mod.recent_error_sample([{"connector": "slack", "error": False}], "slack") == ""


def test_last_healthy_ts_is_none_when_no_prior_run_was_healthy() -> None:
    stats = {"slack": {"s1": {"ok": 0, "err": 3}, "s2": {"ok": 1, "err": 0}}}
    by_id: dict[str, list[dict]] = {
        "s1": [{"_ts": NOW - timedelta(days=2)}],
        "s2": [{"_ts": NOW - timedelta(days=1)}],
    }
    prior = [("s1", by_id["s1"]), ("s2", by_id["s2"])]
    assert chr_mod.last_healthy_ts(stats, "slack", prior, by_id, threshold=3) is None


def test_last_healthy_ts_finds_the_most_recent_healthy_prior_run() -> None:
    stats = {"slack": {"s1": {"ok": 5, "err": 0}, "s2": {"ok": 0, "err": 2}}}
    by_id: dict[str, list[dict]] = {
        "s1": [{"_ts": NOW - timedelta(days=2)}],
        "s2": [{"_ts": NOW - timedelta(days=1)}],
    }
    prior = [("s1", by_id["s1"]), ("s2", by_id["s2"])]
    assert chr_mod.last_healthy_ts(stats, "slack", prior, by_id, threshold=3) == NOW - timedelta(days=2)


def test_no_sessions_yields_no_critical_alerts() -> None:
    assert chr_mod.compute_critical_alerts({}, [], {}, {}, [], data_dir=Path("/nonexistent")) == []


# ---------------------------------------------------------------------------
# fire_macos_notification
# ---------------------------------------------------------------------------


def _alert(name: str) -> Alert:
    return Alert(
        level="CRITICAL",
        connector_key=name.lower(),
        name=name,
        reason="dark for 1 run",
        err_sample="",
    )


def test_no_alerts_means_no_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a: object, **_k: object):
        raise AssertionError("must not shell out with no alerts")

    monkeypatch.setattr(subprocess, "run", fail)
    chr_mod.fire_macos_notification([])


def test_the_notification_passes_names_as_argv_not_as_script_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#51: connector names come from user-editable YAML. Interpolating them
    into the AppleScript source would let a name containing quotes or newlines
    inject arbitrary AppleScript; as argv they are pure data."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.update(argv=argv, **k) or None)

    hostile = 'Slack" & (do shell script "rm -rf ~") & "'
    chr_mod.fire_macos_notification([_alert(hostile)])

    argv = seen["argv"]
    assert isinstance(argv, list)
    assert argv[0] == "osascript"
    assert argv[1] == "-", "the script must come from stdin, not the command line"
    assert argv[2] == "Scout: connector degradation"
    assert argv[3] == hostile, "the name rides as data in argv"

    script = seen["input"]
    assert isinstance(script, str)
    assert "on run argv" in script
    assert "item 2 of argv" in script
    assert hostile not in script, "the name must never appear in the script source"


def test_the_notification_summarizes_at_most_three_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.update(argv=argv) or None)

    chr_mod.fire_macos_notification([_alert(f"conn{n}") for n in range(5)])
    body = seen["argv"][3]  # type: ignore[index]
    assert body == "conn0; conn1; conn2 (+2 more)"


def test_the_notification_lists_all_alerts_when_there_are_three_or_fewer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: seen.update(argv=argv) or None)

    chr_mod.fire_macos_notification([_alert("slack"), _alert("github")])
    assert seen["argv"][3] == "slack; github"  # type: ignore[index]
    assert "more)" not in str(seen["argv"][3])  # type: ignore[index]


def test_a_notification_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The notification is a nicety; on a Linux host `osascript` doesn't exist
    and the report must still complete."""

    def boom(*_a: object, **_k: object):
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(subprocess, "run", boom)
    chr_mod.fire_macos_notification([_alert("slack")])  # must not raise


# ---------------------------------------------------------------------------
# run() — pending-file lifecycle and write tolerance
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "Scout"
    for sub in (".scout-logs", ".scout-cache", "knowledge-base", ".scout-state"):
        (v / sub).mkdir(parents=True)
    return v


def _seed_healthy_run(vault: Path) -> None:
    _log(
        vault / ".scout-logs",
        "2026-05-28",
        *[json.dumps(_row(NOW, connector="slack")) for _ in range(4)],
    )


def test_a_malformed_vault_schedule_falls_back_to_the_plugin_defaults(vault: Path) -> None:
    """The health report must not crash over a broken schedule.yaml — it only
    needs the slot *types* to decide which connectors are required."""
    (vault / ".scout-state" / "schedule.yaml").write_text("slots: [unclosed\n", encoding="utf-8")
    _seed_healthy_run(vault)

    event = chr_mod.run(data_dir=vault, now=NOW)
    assert event is not None
    assert (vault / "knowledge-base" / "connector-health.md").exists()


def test_a_run_with_no_alerts_clears_a_stale_pending_file(vault: Path) -> None:
    """The pending file is what the next session announces; leaving a resolved
    alert in it re-announces a problem that already fixed itself."""
    pending = vault / ".scout-cache" / "connector-alerts-pending.md"
    pending.write_text("# stale alert from a previous run\n", encoding="utf-8")
    _seed_healthy_run(vault)

    event = chr_mod.run(data_dir=vault, now=NOW)
    assert event is not None
    assert event.payload["alerts"] == []
    assert not pending.exists()


def test_clearing_an_absent_pending_file_is_a_no_op(vault: Path) -> None:
    _seed_healthy_run(vault)
    event = chr_mod.run(data_dir=vault, now=NOW)
    assert event is not None
    assert not (vault / ".scout-cache" / "connector-alerts-pending.md").exists()


def test_an_unremovable_pending_file_does_not_fail_the_run(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pending = vault / ".scout-cache" / "connector-alerts-pending.md"
    pending.write_text("# stale\n", encoding="utf-8")
    _seed_healthy_run(vault)

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "unlink", boom)
    assert chr_mod.run(data_dir=vault, now=NOW) is not None


def test_run_short_circuits_when_every_record_is_interactive(vault: Path) -> None:
    _log(vault / ".scout-logs", "2026-05-28", json.dumps(_row(NOW, mode="interactive")))
    assert chr_mod.run(data_dir=vault, now=NOW) is None


def test_run_cleans_up_old_logs_when_asked(vault: Path) -> None:
    old = _log(vault / ".scout-logs", "2026-01-01", json.dumps(_row(NOW - timedelta(days=200))))
    _seed_healthy_run(vault)

    chr_mod.run(data_dir=vault, now=NOW, cleanup=True)
    assert not old.exists()


def test_run_leaves_old_logs_alone_by_default(vault: Path) -> None:
    old = _log(vault / ".scout-logs", "2026-01-01", json.dumps(_row(NOW - timedelta(days=200))))
    _seed_healthy_run(vault)

    chr_mod.run(data_dir=vault, now=NOW)
    assert old.exists()


def test_an_unwritable_alerts_log_does_not_fail_the_run(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rendered health doc is the durable surface; the append-only alert
    log and the pending file are conveniences."""
    _seed_healthy_run(vault)
    monkeypatch.setattr(chr_mod, "compute_warning_alerts", lambda *a, **k: [_alert("slack")])
    monkeypatch.setattr(chr_mod, "fire_macos_notification", lambda _alerts: None)

    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.name == "connector-alerts.log":
            raise OSError("read-only filesystem")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    event = chr_mod.run(data_dir=vault, now=NOW)
    assert event is not None
    assert len(event.payload["alerts"]) == 1


def test_an_unwritable_pending_file_does_not_fail_the_run(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_healthy_run(vault)
    monkeypatch.setattr(chr_mod, "compute_warning_alerts", lambda *a, **k: [_alert("slack")])
    monkeypatch.setattr(chr_mod, "fire_macos_notification", lambda _alerts: None)

    real_write_text = Path.write_text

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.name == "connector-alerts-pending.md":
            raise OSError("read-only filesystem")
        return real_write_text(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", maybe_boom)
    event = chr_mod.run(data_dir=vault, now=NOW)
    assert event is not None
    assert (vault / ".scout-logs" / "connector-alerts.log").exists()


def test_run_resolves_the_vault_from_the_environment(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(vault))
    _seed_healthy_run(vault)
    assert chr_mod.run(now=NOW) is not None


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_reports_the_no_records_short_circuit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(chr_mod, "run", lambda **_k: None)
    assert chr_mod.main() == 0
    assert "no scheduled-run records yet" in capsys.readouterr().out


def test_main_summarizes_the_report(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from scout.events import Event

    monkeypatch.setattr(
        chr_mod,
        "run",
        lambda **_k: Event(
            id="01HXAAA0000000000000000000",
            ts="2026-05-28T14:00:00.000Z",
            kind="connector_health.report.generated",
            source="script:connector_health_report",
            payload={"sessions_in_window": 7, "alerts": [{"name": "slack"}, {"name": "github"}]},
        ),
    )
    assert chr_mod.main() == 0
    assert "7 sessions in window, 2 alert(s)" in capsys.readouterr().out


def test_main_requests_the_cleanup_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI entry point is the retention pass's only caller; if it stopped
    asking for cleanup, the JSONL log would grow without bound."""
    seen: dict[str, object] = {}
    monkeypatch.setattr(chr_mod, "run", lambda **k: seen.update(k) or None)
    chr_mod.main()
    assert seen == {"cleanup": True}
