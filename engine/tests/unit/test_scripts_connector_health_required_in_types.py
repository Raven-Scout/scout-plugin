"""Plan 5 Task 6 — verify the chronic-skip rule keys on slot TYPE, not slot key.

After Task 6 rewrote ``connectors.yaml`` to use ``required_in_types`` (the
fixed plugin vocabulary: briefing | consolidation | dreaming | research |
manual), the chronic-skip rule in ``connector_health_report.run()`` must
resolve the current run's slot key through ``scout.schedule`` to its
``SlotType`` and then ask the connector ``required_in_type(slot_type)``.

These tests pin that mapping at the rule layer:

  1. Three weekday morning-briefing runs with ``github`` dark → CRITICAL
     alert (briefing requires github via ``required_in_types`` containing
     ``briefing``).
  2. Three research runs with Granola dark → no Granola alert (research
     does not require Granola).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout.scripts import connector_health_report as chr_mod

# ----- helpers (mirrors test_scripts_connector_health.py shape) -------------


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


def _seed_session(
    log_dir: Path,
    *,
    sid: str,
    mode: str,
    ts: datetime,
    calls: dict[str, tuple[int, int]],
) -> None:
    """Append a synthetic scheduled-run session to a JSONL file."""
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
    out = log_dir / f"connector-calls-{ts.date().isoformat()}.jsonl"
    if out.exists():
        existing = out.read_text().splitlines()
        existing.extend(json.dumps(r) for r in records)
        out.write_text("\n".join(existing) + "\n")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _frozen_now() -> datetime:
    """Deterministic 'now' inside EDT."""
    return datetime(2026, 4, 28, 21, 0, 0, tzinfo=UTC)


# ----- tests ----------------------------------------------------------------


def test_chronic_skip_alert_fires_when_slot_type_requires_connector(fake_data_dir, monkeypatch):
    """github dark in 3 weekday morning-briefing runs → CRITICAL alert.

    The default schedule maps ``morning-briefing`` to ``SlotType.BRIEFING``,
    and ``github`` declares ``required_in_types: [briefing, consolidation,
    research]``. So the chronic-skip rule MUST resolve the slot type and
    fire the alert.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    # Three morning-briefing runs across 3 days.
    # Day 1 has github HEALTHY (so total_ok_ever > 0 — Pattern #48 doesn't
    # suppress). Days 2 + 3 are dark on github but otherwise have OK Slack
    # records, giving us valid scheduled-run sessions.
    base = _frozen_now() - timedelta(days=3)

    _seed_session(
        log_dir,
        sid="briefing_d1",
        mode="morning-briefing",
        ts=base,
        calls={
            "github": (3, 0),
            "mcp:claude_ai_Slack": (5, 0),
            "mcp:claude_ai_Linear": (4, 0),
        },
    )
    for i in range(2):
        _seed_session(
            log_dir,
            sid=f"briefing_d{2 + i}",
            mode="morning-briefing",
            ts=base + timedelta(days=1 + i),
            calls={
                "mcp:claude_ai_Slack": (5, 0),
                "mcp:claude_ai_Linear": (4, 0),
                # github intentionally absent — dark.
            },
        )
    # Current run: another dark-on-github morning-briefing (so the gap is 3).
    _seed_session(
        log_dir,
        sid="briefing_curr",
        mode="morning-briefing",
        ts=_frozen_now() - timedelta(hours=1),
        calls={
            "mcp:claude_ai_Slack": (5, 0),
            "mcp:claude_ai_Linear": (4, 0),
        },
    )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert event is not None

    alert_keys = [a["connector_key"] for a in event.payload["alerts"]]
    assert "github" in alert_keys, f"Expected github CRITICAL alert in briefing slot type; got alerts: {alert_keys}"


def test_chronic_skip_alert_silent_when_slot_type_does_not_require_connector(fake_data_dir, monkeypatch):
    """Granola dark in 3 research runs → no Granola alert.

    Research's slot type is ``SlotType.RESEARCH``; Granola declares
    ``required_in_types: [briefing, consolidation]`` and is NOT required
    for research. The chronic-skip override must therefore stay silent.
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    base = _frozen_now() - timedelta(days=4)

    # Pre-seed: a few morning-briefing runs where Granola IS healthy, so
    # total_ok_ever > 0 (Pattern #48 doesn't suppress) AND Granola has been
    # observed in the window (it's wired, just not relevant in research).
    for i in range(2):
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

    # Three research runs, Granola dark, github exercised heavily.
    research_base = base + timedelta(days=3)
    for i in range(3):
        _seed_session(
            log_dir,
            sid=f"research_{i}",
            mode="research",
            ts=research_base + timedelta(days=i),
            calls={
                "github": (5, 0),
                "mcp:claude_ai_Slack": (3, 0),
                "mcp:claude_ai_Linear": (3, 0),
                # Granola intentionally absent.
            },
        )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert event is not None

    alert_keys = [a["connector_key"] for a in event.payload["alerts"]]
    assert "mcp:claude_ai_Granola" not in alert_keys, (
        f"Granola should NOT alert in research slot type; got alerts: {alert_keys}"
    )


def test_unknown_mode_resolves_to_manual_slot_type(fake_data_dir, monkeypatch):
    """A scheduled run with an unrecognized mode → SlotType.MANUAL → no chronic-skip.

    Manual is intentionally excluded from every connector's
    ``required_in_types``, so even with 3 dark runs, no alert should fire
    from the chronic-skip override. (The mode-aware-baseline rule may still
    fire if there are healthy prior same-mode runs, so we keep this test
    targeted at chronic-skip by giving the unknown mode no prior healthy
    runs.)
    """
    monkeypatch.setattr(chr_mod, "_default_now", _frozen_now)
    log_dir = fake_data_dir / ".scout-logs"

    base = _frozen_now() - timedelta(days=5)

    # Pre-seed: a few morning-briefing runs where github IS healthy so
    # total_ok_ever > 0.
    for i in range(2):
        _seed_session(
            log_dir,
            sid=f"weekday_{i}",
            mode="morning-briefing",
            ts=base + timedelta(days=i),
            calls={
                "github": (3, 0),
                "mcp:claude_ai_Slack": (5, 0),
            },
        )

    # Three runs with an unknown mode key, github dark.
    unknown_base = base + timedelta(days=3)
    for i in range(3):
        _seed_session(
            log_dir,
            sid=f"custom_{i}",
            mode="my-custom-slot",  # not in default schedule
            ts=unknown_base + timedelta(days=i),
            calls={
                "mcp:claude_ai_Slack": (5, 0),
                # github absent.
            },
        )

    event = chr_mod.run(data_dir=fake_data_dir)
    assert event is not None

    alert_keys = [a["connector_key"] for a in event.payload["alerts"]]
    assert "github" not in alert_keys, (
        f"Unknown mode → SlotType.MANUAL → chronic-skip should not fire for github; got alerts: {alert_keys}"
    )
