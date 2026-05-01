"""Unit tests for scout.scripts.connector_health_report.

Covers the 7 minimum cases from Plan 4 Task 5:
  1. Empty .scout-logs/ → silent exit; no connector-health.md.
  2. Single healthy run → matrix renders, no alerts.
  3. Granola 7 runs dark + total_ok_ever == 0 → Pattern #48 suppression.
  4. Slack 1 run dark with 2/2 prior same-mode healthy → CRITICAL fires.
  5. Weekend-only `gh CLI dark` (chronic skip in non-required mode) → no alert.
  6. Warning rule: 4 calls, 3 errors → WARNING fires.
  7. Mode-aware-baseline edge case: 0 prior same-mode runs → no alert.

Plus a few hermeticity tests on the renderer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout.events import Event
from scout.scripts import connector_health_report as chr_mod

# ----- helpers --------------------------------------------------------------


def _make_call(
    ts_utc: datetime,
    sid: str,
    mode: str,
    connector: str,
    *,
    error: bool = False,
    err: str = "",
    tool: str = "Bash",
) -> dict:
    rec: dict = {
        "ts": ts_utc.isoformat().replace("+00:00", "Z"),
        "session_id": sid,
        "mode": mode,
        "tool": tool,
        "connector": connector,
        "error": error,
    }
    if err:
        rec["err"] = err
    return rec


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _et_date_filename(ts: datetime) -> str:
    """Match the bash filename convention `connector-calls-YYYY-MM-DD.jsonl`."""
    # connector-log.sh dates the filename in ET, but for unit tests we
    # only need filenames inside the 14-day window — the rollup loads them all.
    return f"connector-calls-{ts.date().isoformat()}.jsonl"


def _frozen_now() -> datetime:
    """A deterministic 'now' inside EDT (April; UTC-4)."""
    # 2026-04-28 17:00:00 ET == 2026-04-28 21:00:00 UTC.
    return datetime(2026, 4, 28, 21, 0, 0, tzinfo=UTC)


def _seed_session(
    log_dir: Path,
    *,
    sid: str,
    mode: str,
    ts: datetime,
    calls: dict[str, tuple[int, int]],
) -> None:
    """Write a synthetic scheduled-run session to a JSONL file.

    `calls[connector] = (n_ok, n_err)`. Records share the session timestamp.
    """
    records = []
    for connector, (n_ok, n_err) in calls.items():
        for i in range(n_ok):
            records.append(_make_call(ts + timedelta(seconds=i), sid, mode, connector, error=False))
        for i in range(n_err):
            records.append(
                _make_call(
                    ts + timedelta(seconds=100 + i),
                    sid,
                    mode,
                    connector,
                    error=True,
                    err=f"boom {connector}",
                )
            )
    fname = _et_date_filename(ts)
    out = log_dir / fname
    if out.exists():
        # Append rather than overwrite so we can stack multiple sessions on one date.
        existing = out.read_text().splitlines()
        existing.extend(json.dumps(r) for r in records)
        out.write_text("\n".join(existing) + "\n")
    else:
        _write_jsonl(out, records)


# ----- Test 1: empty logs ---------------------------------------------------


def test_empty_logs_short_circuits_no_files(fake_data_dir, monkeypatch):
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    out = chr_mod.run(data_dir=fake_data_dir)
    assert out is None
    # No connector-health.md written.
    health = fake_data_dir / "knowledge-base" / "connector-health.md"
    assert not health.exists()
    # No pending-alerts file either.
    pending = fake_data_dir / ".scout-cache" / "connector-alerts-pending.md"
    assert not pending.exists()


# ----- Test 2: single healthy run ------------------------------------------


def test_single_healthy_run_renders_matrix_no_alerts(fake_data_dir, monkeypatch):
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # One scheduled run with 5 OK Slack calls.
    _seed_session(
        log_dir,
        sid="s1",
        mode="morning-briefing",
        ts=_frozen_now() - timedelta(hours=2),
        calls={
            "mcp:claude_ai_Slack": (5, 0),
            "mcp:claude_ai_Linear": (4, 0),
            "mcp:claude_ai_Gmail": (3, 0),
        },
    )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert isinstance(event, Event)
    assert event.kind == "connector_health.report.generated"

    health = fake_data_dir / "knowledge-base" / "connector-health.md"
    assert health.exists()
    body = health.read_text()
    assert "# Connector Health" in body
    # Matrix rendered.
    assert "Status (last 10 scheduled runs)" in body
    assert "✅ 5" in body
    # No alert section heading.
    assert "Active Alerts" not in body

    # No pending file.
    pending = fake_data_dir / ".scout-cache" / "connector-alerts-pending.md"
    assert not pending.exists()


# ----- Test 3: Granola 7 runs dark, never healthy → suppress ---------------


def test_pattern_48_suppression_never_wired(fake_data_dir, monkeypatch):
    """Connector with total_ok_ever == 0 across all runs should NOT alert."""
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # 7 morning-briefing runs over 7 days, Granola never called (no records),
    # but other connectors all healthy so we have valid sessions to compare to.
    base = _frozen_now() - timedelta(days=7)
    for i in range(7):
        ts = base + timedelta(days=i, hours=8)
        _seed_session(
            log_dir,
            sid=f"s{i}",
            mode="morning-briefing",
            ts=ts,
            calls={
                "mcp:claude_ai_Slack": (5, 0),
                "mcp:claude_ai_Linear": (5, 0),
                "mcp:claude_ai_Gmail": (5, 0),
                "mcp:claude_ai_Google_Calendar": (5, 0),
                "github": (3, 0),
                "mcp:claude-in-chrome": (3, 0),
                # Granola intentionally absent — never wired.
            },
        )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert isinstance(event, Event)

    # Granola is required in morning-briefing mode and has 0 OK calls + gap >= 3,
    # which would normally fire the chronic-skip rule. But total_ok_ever == 0
    # → Pattern #48 suppression.
    alerts = event.payload["alerts"]
    granola_alerts = [a for a in alerts if a["connector_key"] == "mcp:claude_ai_Granola"]
    assert granola_alerts == [], f"Pattern #48 should suppress; got: {granola_alerts}"


# ----- Test 4: Slack 1 run dark, 2/2 prior healthy → CRITICAL --------------


def test_slack_dark_two_prior_healthy_fires_critical(fake_data_dir, monkeypatch):
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # 2 prior morning-briefing runs healthy on Slack.
    base = _frozen_now() - timedelta(days=2)
    for i, sid in enumerate(["s_prev1", "s_prev2"]):
        _seed_session(
            log_dir,
            sid=sid,
            mode="morning-briefing",
            ts=base + timedelta(days=i),
            calls={
                "mcp:claude_ai_Slack": (5, 0),
                "mcp:claude_ai_Linear": (5, 0),
            },
        )

    # Current morning-briefing run: Slack dark (0 calls), Linear healthy.
    _seed_session(
        log_dir,
        sid="s_curr",
        mode="morning-briefing",
        ts=_frozen_now() - timedelta(hours=1),
        calls={
            "mcp:claude_ai_Linear": (4, 0),
            # Slack absent in this run.
        },
    )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert isinstance(event, Event)

    alerts = event.payload["alerts"]
    slack_alerts = [a for a in alerts if a["connector_key"] == "mcp:claude_ai_Slack"]
    assert len(slack_alerts) == 1
    assert slack_alerts[0]["level"] == "CRITICAL"
    assert "morning-briefing" in slack_alerts[0]["reason"]

    # Pending file is written for the next run.
    pending = fake_data_dir / ".scout-cache" / "connector-alerts-pending.md"
    assert pending.exists()
    pending_body = pending.read_text()
    assert "Slack" in pending_body
    assert "Reconnect Slack" in pending_body  # first_fix from yaml

    # Alerts log appended.
    alerts_log = fake_data_dir / ".scout-logs" / "connector-alerts.log"
    assert alerts_log.exists()
    log_body = alerts_log.read_text()
    assert "[CRITICAL]" in log_body
    assert "Slack" in log_body


# ----- Test 5: Weekend-only `gh CLI dark` → no alert if non-required -------


def test_chronic_skip_only_in_required_mode(fake_data_dir, monkeypatch):
    """github is required_in: all → it IS required in weekend-briefing too,
    so the chronic-skip override should fire. To exercise the "non-required mode"
    path, use Granola which is NOT required on weekends (weekday-only).

    Set up: 3 weekend-briefing runs all dark on Granola, but the connector
    DOES have prior OK calls (so Pattern #48 does not suppress). The
    mode-baseline rule sees 0 of 2 prior same-mode runs healthy → no alert.
    The chronic-skip rule sees mode_required=False → no alert.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    base = _frozen_now() - timedelta(days=10)

    # First, 3 weekday morning-briefings where Granola IS healthy — gives
    # total_ok_ever > 0 (so Pattern #48 doesn't suppress).
    for i in range(3):
        _seed_session(
            log_dir,
            sid=f"weekday_{i}",
            mode="morning-briefing",
            ts=base + timedelta(days=i),
            calls={
                "mcp:claude_ai_Granola": (4, 0),
                "mcp:claude_ai_Slack": (5, 0),
            },
        )

    # Then 3 weekend-briefing runs where Granola is dark (legitimately —
    # weekend mode doesn't call it).
    for i in range(3):
        _seed_session(
            log_dir,
            sid=f"weekend_{i}",
            mode="weekend-briefing",
            ts=base + timedelta(days=5 + i),
            calls={
                "mcp:claude_ai_Slack": (5, 0),
                # Granola absent.
            },
        )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert isinstance(event, Event)

    alerts = event.payload["alerts"]
    granola_alerts = [a for a in alerts if a["connector_key"] == "mcp:claude_ai_Granola"]
    assert granola_alerts == [], "Granola is not required in weekend-briefing — should not fire chronic-skip."


# ----- Test 6: WARNING rule (4 calls, 3 errors > 50% → WARNING) -----------


def test_warning_rule_high_error_rate(fake_data_dir, monkeypatch):
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # Single current run: Linear has 1 ok + 3 err = 4 calls, 75% errors.
    # Slack and Gmail healthy so they don't trigger their own alerts.
    _seed_session(
        log_dir,
        sid="s_curr",
        mode="consolidation-1pm",
        ts=_frozen_now() - timedelta(hours=1),
        calls={
            "mcp:claude_ai_Slack": (5, 0),
            "mcp:claude_ai_Linear": (1, 3),
            "mcp:claude_ai_Gmail": (4, 0),
            "mcp:claude_ai_Google_Calendar": (3, 0),
            "github": (3, 0),
        },
    )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert isinstance(event, Event)

    alerts = event.payload["alerts"]
    linear_alerts = [a for a in alerts if a["connector_key"] == "mcp:claude_ai_Linear"]
    assert len(linear_alerts) == 1
    assert linear_alerts[0]["level"] == "WARNING"
    assert "75%" in linear_alerts[0]["reason"]


# ----- Test 7: 0 prior same-mode runs → no alert --------------------------


def test_no_baseline_no_alert(fake_data_dir, monkeypatch):
    """If no prior same-mode run exists, mode-baseline rule does not alert.

    But chronic-skip might. We need to force a mode where the connector is
    NOT required so chronic-skip stays silent too. Pick weekend-briefing +
    Granola (not required) + 0 prior weekend runs.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # First, a few morning-briefing runs where Granola is healthy (so
    # total_ok_ever > 0 and Pattern #48 doesn't fire). These are NOT
    # weekend-briefing, so they don't count as same-mode prior runs.
    base = _frozen_now() - timedelta(days=4)
    for i in range(3):
        _seed_session(
            log_dir,
            sid=f"weekday_{i}",
            mode="morning-briefing",
            ts=base + timedelta(days=i),
            calls={"mcp:claude_ai_Granola": (4, 0), "mcp:claude_ai_Slack": (5, 0)},
        )

    # Current run: weekend-briefing, no Granola. Zero prior weekend-briefing
    # sessions → no baseline → no alert.
    _seed_session(
        log_dir,
        sid="s_curr",
        mode="weekend-briefing",
        ts=_frozen_now() - timedelta(hours=1),
        calls={"mcp:claude_ai_Slack": (5, 0)},
    )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert isinstance(event, Event)

    alerts = event.payload["alerts"]
    granola_alerts = [a for a in alerts if a["connector_key"] == "mcp:claude_ai_Granola"]
    assert granola_alerts == [], "0 prior same-mode runs → no baseline → no alert."


# ----- Renderer hermeticity ------------------------------------------------


def test_render_includes_alert_section_and_remediation(fake_data_dir, monkeypatch):
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # Baseline: 2 healthy morning-briefings on Slack.
    base = _frozen_now() - timedelta(days=2)
    for i, sid in enumerate(["p1", "p2"]):
        _seed_session(
            log_dir,
            sid=sid,
            mode="morning-briefing",
            ts=base + timedelta(days=i),
            calls={"mcp:claude_ai_Slack": (5, 0)},
        )
    # Current: Slack dark.
    _seed_session(
        log_dir,
        sid="curr",
        mode="morning-briefing",
        ts=_frozen_now() - timedelta(hours=1),
        calls={"mcp:claude_ai_Linear": (3, 0)},
    )

    chr_mod.run(data_dir=fake_data_dir)
    body = (fake_data_dir / "knowledge-base" / "connector-health.md").read_text()

    # Alert section + remediation detail rendered.
    assert "Active Alerts" in body
    assert "Slack — CRITICAL" in body
    assert "How to fix" in body
    # Some text from the YAML detail (Slack remediation).
    assert "claude.ai/settings/connectors" in body


def test_pending_file_cleared_when_no_alerts(fake_data_dir, monkeypatch):
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # Pre-populate a stale pending file.
    pending = fake_data_dir / ".scout-cache" / "connector-alerts-pending.md"
    pending.write_text("stale alert from previous run")

    _seed_session(
        log_dir,
        sid="s1",
        mode="morning-briefing",
        ts=_frozen_now() - timedelta(hours=1),
        calls={
            "mcp:claude_ai_Slack": (5, 0),
            "mcp:claude_ai_Linear": (5, 0),
            "mcp:claude_ai_Gmail": (3, 0),
            "mcp:claude_ai_Google_Calendar": (3, 0),
            "mcp:claude_ai_Granola": (3, 0),
            "mcp:claude_ai_Google_Drive": (3, 0),
            "github": (3, 0),
            "mcp:claude-in-chrome": (3, 0),
        },
    )

    chr_mod.run(data_dir=fake_data_dir)
    assert not pending.exists(), "stale pending file should be deleted"


def test_interactive_records_filtered_out(fake_data_dir, monkeypatch):
    """Records with mode in (interactive, unknown) should be skipped."""
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # Only interactive records → filtered → no records → silent exit.
    ts = _frozen_now() - timedelta(hours=1)
    _seed_session(
        log_dir,
        sid="interactive_session",
        mode="interactive",
        ts=ts,
        calls={"mcp:claude_ai_Slack": (5, 0)},
    )

    out = chr_mod.run(data_dir=fake_data_dir)
    assert out is None


def test_session_list_ordered_by_min_ts(fake_data_dir, monkeypatch):
    """current_sid is the LAST session by min(_ts) — verify ordering."""
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # Out-of-order timestamps — session B starts before session A even though
    # we write A first.
    _seed_session(
        log_dir,
        sid="A",
        mode="morning-briefing",
        ts=_frozen_now() - timedelta(hours=1),
        calls={"mcp:claude_ai_Slack": (3, 0)},
    )
    _seed_session(
        log_dir,
        sid="B",
        mode="morning-briefing",
        ts=_frozen_now() - timedelta(hours=5),
        calls={"mcp:claude_ai_Slack": (3, 0)},
    )

    event = chr_mod.run(data_dir=fake_data_dir)
    # The session with the latest min(_ts) is current — that's A.
    assert event.payload["current_sid"] == "A"


def test_cleanup_old_jsonl_only_when_requested(fake_data_dir, monkeypatch):
    """30-day cleanup is gated on the explicit cleanup flag."""
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    old_path = log_dir / "connector-calls-2026-01-01.jsonl"
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text("{}\n")

    # Default run should NOT cleanup (cleanup=False keeps tests deterministic).
    chr_mod.run(data_dir=fake_data_dir, cleanup=False)
    assert old_path.exists(), "without cleanup=True, ancient JSONL stays"

    # Explicit cleanup removes >30-day-old files.
    chr_mod.cleanup_old_jsonl(log_dir, retain_days=30, now=_frozen_now())
    assert not old_path.exists()


# ----- Connector model integration -----------------------------------------


def test_outbound_only_connectors_excluded_from_warnings(fake_data_dir, monkeypatch):
    """notify:telegram is outbound-only — it should never be an alerting subject."""
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # Even with high-error notify:telegram calls, no WARNING should fire.
    _seed_session(
        log_dir,
        sid="s1",
        mode="morning-briefing",
        ts=_frozen_now() - timedelta(hours=1),
        calls={
            "mcp:claude_ai_Slack": (5, 0),
            "mcp:claude_ai_Linear": (5, 0),
            "mcp:claude_ai_Gmail": (3, 0),
            "mcp:claude_ai_Google_Calendar": (3, 0),
            "mcp:claude_ai_Granola": (3, 0),
            "mcp:claude_ai_Google_Drive": (3, 0),
            "github": (3, 0),
            "mcp:claude-in-chrome": (3, 0),
            "notify:telegram": (1, 4),
        },
    )

    event = chr_mod.run(data_dir=fake_data_dir)
    alerts = event.payload["alerts"]
    tg_alerts = [a for a in alerts if a["connector_key"] == "notify:telegram"]
    assert tg_alerts == []
