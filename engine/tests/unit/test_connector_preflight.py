"""Unit tests for scout.scripts.connector_preflight (Layers 1-2 of the
connector-resilience design, docs/superpowers/specs/2026-07-01).

Exit-code contract (consumed by the run-*.sh runner templates):
  0 = proceed, 3 = policy skip (degraded), 4 = inconclusive (runner fails open).
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from scout.connectors import load_registry
from scout.schedule import SlotType
from scout.scripts import connector_preflight as cp
from scout.scripts.connector_preflight import (
    EXIT_INCONCLUSIVE,
    EXIT_PROCEED,
    EXIT_SKIP_DEGRADED,
    INCONCLUSIVE_ALERT_STREAK,
    OnDegraded,
    ProbeStatus,
    evaluate,
    parse_mcp_list,
    resolve_policy,
    resolve_slot_type,
    run,
)

# Anonymized `claude mcp list` output (Claude Code 2.1.185 format): a
# "Checking..." preamble line, then one `<name>: <target> - <marker>` row per
# server. Covers all four status markers plus a plugin-scoped name whose
# colons must not confuse the name capture.
MCP_LIST_OUTPUT = dedent(
    """\
    Checking MCP server health…

    claude.ai Slack: https://mcp.slack.com/mcp - ✔ Connected
    claude.ai Linear: https://mcp.linear.app/mcp - ✘ Failed to connect
    claude.ai Notion: https://mcp.notion.com/mcp - ! Needs authentication
    claude.ai Acme Tools: https://mcp.acme.example/mcp - ⏸ Pending approval
    plugin:example:tools: npx @example/mcp-server - ✔ Connected
    """
)


# ----- parse_mcp_list -------------------------------------------------------


def test_parse_mcp_list_parses_every_status_marker() -> None:
    statuses = parse_mcp_list(MCP_LIST_OUTPUT)
    assert statuses["claude.ai Slack"] is ProbeStatus.CONNECTED
    assert statuses["claude.ai Linear"] is ProbeStatus.FAILED
    assert statuses["claude.ai Notion"] is ProbeStatus.NEEDS_AUTH
    assert statuses["claude.ai Acme Tools"] is ProbeStatus.PENDING


def test_parse_mcp_list_handles_plugin_scoped_names_verbatim() -> None:
    """Plugin-scoped server names contain colons; the name is everything
    before the first ': ' separator."""
    statuses = parse_mcp_list(MCP_LIST_OUTPUT)
    assert statuses["plugin:example:tools"] is ProbeStatus.CONNECTED


def test_parse_mcp_list_skips_preamble_and_blank_lines() -> None:
    statuses = parse_mcp_list(MCP_LIST_OUTPUT)
    assert len(statuses) == 5


def test_parse_mcp_list_unparseable_output_yields_nothing() -> None:
    """A CLI format change must yield an empty parse (→ inconclusive
    upstream), never a bogus degraded verdict."""
    assert parse_mcp_list("error: unknown command 'mcp'\n") == {}
    assert parse_mcp_list("") == {}


# ----- policy resolution ----------------------------------------------------


def test_resolve_policy_defaults_to_run_when_block_missing() -> None:
    assert resolve_policy({}, SlotType.BRIEFING) is OnDegraded.RUN


def test_resolve_policy_global_default() -> None:
    cfg = {"connector_policy": {"on_degraded": "skip"}}
    assert resolve_policy(cfg, SlotType.BRIEFING) is OnDegraded.SKIP


def test_resolve_policy_per_slot_override_wins() -> None:
    cfg = {
        "connector_policy": {
            "on_degraded": "run",
            "overrides": {"briefing": "skip", "dreaming": "warn"},
        }
    }
    assert resolve_policy(cfg, SlotType.BRIEFING) is OnDegraded.SKIP
    assert resolve_policy(cfg, SlotType.DREAMING) is OnDegraded.WARN
    assert resolve_policy(cfg, SlotType.RESEARCH) is OnDegraded.RUN


def test_resolve_policy_malformed_block_falls_back_to_run(capsys) -> None:
    assert resolve_policy({"connector_policy": "yes please"}, SlotType.BRIEFING) is OnDegraded.RUN
    assert "connector_policy" in capsys.readouterr().err


def test_resolve_policy_unknown_value_falls_back(capsys) -> None:
    cfg = {"connector_policy": {"on_degraded": "maybe"}}
    assert resolve_policy(cfg, SlotType.BRIEFING) is OnDegraded.RUN
    assert "on_degraded" in capsys.readouterr().err


def test_resolve_policy_bad_override_falls_back_to_global(capsys) -> None:
    cfg = {"connector_policy": {"on_degraded": "warn", "overrides": {"briefing": "nope"}}}
    assert resolve_policy(cfg, SlotType.BRIEFING) is OnDegraded.WARN
    assert "overrides" in capsys.readouterr().err


# ----- mode → slot type resolution ------------------------------------------


def test_resolve_slot_type_from_default_schedule_key(tmp_path: Path) -> None:
    assert resolve_slot_type("morning-briefing", data_dir=tmp_path) is SlotType.BRIEFING
    assert resolve_slot_type("midday-consolidation", data_dir=tmp_path) is SlotType.CONSOLIDATION


def test_resolve_slot_type_manual_variants(tmp_path: Path) -> None:
    assert resolve_slot_type("manual", data_dir=tmp_path) is SlotType.MANUAL
    assert resolve_slot_type("dreaming-manual", data_dir=tmp_path) is SlotType.MANUAL
    assert resolve_slot_type("research-manual", data_dir=tmp_path) is SlotType.MANUAL


def test_resolve_slot_type_accepts_bare_type_value(tmp_path: Path) -> None:
    assert resolve_slot_type("briefing", data_dir=tmp_path) is SlotType.BRIEFING


def test_resolve_slot_type_unknown_key_falls_back_to_manual(tmp_path: Path) -> None:
    assert resolve_slot_type("acme-run", data_dir=tmp_path) is SlotType.MANUAL


# ----- classification -------------------------------------------------------


def _overlay_registry(tmp_path: Path, body: str):
    """Build a registry whose only critical connectors are the overlay's."""
    state = tmp_path / ".scout-state"
    state.mkdir(parents=True, exist_ok=True)
    # Neutralize every seed connector so the test roster is exactly the overlay.
    seed_off = "\n".join(f"  {key}:\n    required_in_types: []" for key in load_registry(data_dir=tmp_path).keys())
    (state / "connectors.local.yaml").write_text(f"connectors:\n{seed_off}\n{body}")
    return load_registry(data_dir=tmp_path)


BRIEFING_ROSTER = """\
  mcp:acme_chat:
    display_name: Acme Chat
    tier: community
    capabilities: [inbound]
    required_in_types: [briefing]
    harness_server_name: "claude.ai Acme Chat"
    remediation: {first_fix: "reconnect", detail: "reconnect"}
  mcp:acme_tracker:
    display_name: Acme Tracker
    tier: community
    capabilities: [inbound]
    required_in_types: [briefing]
    harness_server_name: "claude.ai Acme Tracker"
    remediation: {first_fix: "reconnect", detail: "reconnect"}
  unprobed:
    display_name: Unprobed Source
    tier: community
    capabilities: [inbound]
    required_in_types: [briefing]
    remediation: {first_fix: "n/a", detail: "n/a"}
"""


def test_evaluate_all_connected_is_healthy(tmp_path: Path) -> None:
    reg = _overlay_registry(tmp_path, BRIEFING_ROSTER)
    statuses = {
        "claude.ai Acme Chat": ProbeStatus.CONNECTED,
        "claude.ai Acme Tracker": ProbeStatus.CONNECTED,
    }
    result = evaluate(reg, SlotType.BRIEFING, statuses)
    assert not result.degraded
    assert not result.inconclusive
    # The unprobed connector has neither probe field — it is simply not probed.
    assert {p.key for p in result.probes} == {"mcp:acme_chat", "mcp:acme_tracker"}


def test_evaluate_any_critical_down_is_degraded(tmp_path: Path) -> None:
    reg = _overlay_registry(tmp_path, BRIEFING_ROSTER)
    statuses = {
        "claude.ai Acme Chat": ProbeStatus.NEEDS_AUTH,
        "claude.ai Acme Tracker": ProbeStatus.CONNECTED,
    }
    result = evaluate(reg, SlotType.BRIEFING, statuses)
    assert result.degraded
    assert [p.key for p in result.dark] == ["mcp:acme_chat"]


def test_evaluate_server_missing_from_list_is_degraded(tmp_path: Path) -> None:
    """A critical connector whose harness_server_name is absent from the
    `claude mcp list` output is unusable by the session — degraded."""
    reg = _overlay_registry(tmp_path, BRIEFING_ROSTER)
    statuses = {"claude.ai Acme Chat": ProbeStatus.CONNECTED}
    result = evaluate(reg, SlotType.BRIEFING, statuses)
    assert result.degraded
    assert [p.key for p in result.dark] == ["mcp:acme_tracker"]
    assert result.dark[0].status is ProbeStatus.MISSING


def test_evaluate_no_statuses_is_inconclusive_not_degraded(tmp_path: Path) -> None:
    reg = _overlay_registry(tmp_path, BRIEFING_ROSTER)
    result = evaluate(reg, SlotType.BRIEFING, None)
    assert not result.degraded
    assert result.inconclusive


def test_evaluate_bash_probe_success_and_failure(tmp_path: Path) -> None:
    reg = _overlay_registry(
        tmp_path,
        """\
  clitool:
    display_name: CLI Tool
    tier: community
    capabilities: [inbound]
    required_in_types: [briefing]
    preflight_command: "true"
    remediation: {first_fix: "n/a", detail: "n/a"}
""",
    )
    ok = evaluate(reg, SlotType.BRIEFING, None, run_bash=lambda cmd: ProbeStatus.CONNECTED)
    assert not ok.degraded and not ok.inconclusive
    down = evaluate(reg, SlotType.BRIEFING, None, run_bash=lambda cmd: ProbeStatus.FAILED)
    assert down.degraded


def test_evaluate_real_bash_probe_runs_command(tmp_path: Path) -> None:
    reg = _overlay_registry(
        tmp_path,
        """\
  goodcli:
    display_name: Good CLI
    tier: community
    capabilities: [inbound]
    required_in_types: [briefing]
    preflight_command: "true"
    remediation: {first_fix: "n/a", detail: "n/a"}
  badcli:
    display_name: Bad CLI
    tier: community
    capabilities: [inbound]
    required_in_types: [briefing]
    preflight_command: "false"
    remediation: {first_fix: "n/a", detail: "n/a"}
""",
    )
    result = evaluate(reg, SlotType.BRIEFING, None)
    assert result.degraded
    assert [p.key for p in result.dark] == ["badcli"]


def test_evaluate_degraded_wins_over_inconclusive(tmp_path: Path) -> None:
    """MCP list probe failed (unknown) but a bash probe found a hard down —
    the run IS determinably degraded; unknowns alone fail open."""
    reg = _overlay_registry(
        tmp_path,
        BRIEFING_ROSTER
        + """\
  badcli:
    display_name: Bad CLI
    tier: community
    capabilities: [inbound]
    required_in_types: [briefing]
    preflight_command: "false"
    remediation: {first_fix: "n/a", detail: "n/a"}
""",
    )
    result = evaluate(reg, SlotType.BRIEFING, None)
    assert result.degraded
    assert not result.inconclusive


# ----- run() ----------------------------------------------------------------


def _vault_with_policy(tmp_path: Path, policy_yaml: str) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "scout-config.yaml").write_text(policy_yaml)
    return vault


@pytest.fixture
def alerts(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(cp, "_send_telegram_alert", lambda body: sent.append(body))
    return sent


@pytest.fixture
def degraded_probe(monkeypatch):
    monkeypatch.setattr(
        cp, "_run_mcp_list", lambda claude_bin, timeout: {"claude.ai Acme Chat": ProbeStatus.NEEDS_AUTH}
    )


def _briefing_registry(vault: Path):
    return _overlay_registry(
        vault,
        """\
  mcp:acme_chat:
    display_name: Acme Chat
    tier: community
    capabilities: [inbound]
    required_in_types: [briefing]
    harness_server_name: "claude.ai Acme Chat"
    remediation: {first_fix: "reconnect", detail: "reconnect"}
""",
    )


def test_run_skip_policy_exits_3_and_alerts(tmp_path: Path, alerts, degraded_probe) -> None:
    vault = _vault_with_policy(tmp_path, "connector_policy:\n  on_degraded: skip\n")
    rc = run(slot_type="briefing", data_dir=vault, registry=_briefing_registry(vault))
    assert rc == EXIT_SKIP_DEGRADED
    assert len(alerts) == 1
    assert "Acme Chat" in alerts[0]
    log = (vault / ".scout-logs" / "connector-alerts.log").read_text()
    assert "Acme Chat" in log
    # skip must NOT leave a warn-mode pending file
    assert not (vault / ".scout-cache" / "connector-degradation-pending.md").exists()


def test_run_warn_policy_exits_0_and_writes_pending_file(tmp_path: Path, alerts, degraded_probe) -> None:
    vault = _vault_with_policy(tmp_path, "connector_policy:\n  on_degraded: warn\n")
    rc = run(slot_type="briefing", data_dir=vault, registry=_briefing_registry(vault))
    assert rc == EXIT_PROCEED
    pending = vault / ".scout-cache" / "connector-degradation-pending.md"
    assert pending.exists()
    body = pending.read_text()
    assert "Acme Chat" in body
    assert "needs authentication" in body
    assert alerts == []


def test_run_run_policy_exits_0_without_side_effects(tmp_path: Path, alerts, degraded_probe) -> None:
    vault = _vault_with_policy(tmp_path, "connector_policy:\n  on_degraded: run\n")
    rc = run(slot_type="briefing", data_dir=vault, registry=_briefing_registry(vault))
    assert rc == EXIT_PROCEED
    assert alerts == []
    assert not (vault / ".scout-cache" / "connector-degradation-pending.md").exists()


def test_run_default_policy_is_run(tmp_path: Path, alerts, degraded_probe) -> None:
    """No connector_policy block at all → today's behavior (no gate)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    rc = run(slot_type="briefing", data_dir=vault, registry=_briefing_registry(vault))
    assert rc == EXIT_PROCEED


def test_run_healthy_stamps_last_healthy_run_and_resets_streak(tmp_path: Path, alerts, monkeypatch) -> None:
    vault = _vault_with_policy(tmp_path, "connector_policy:\n  on_degraded: skip\n")
    monkeypatch.setattr(cp, "_run_mcp_list", lambda claude_bin, timeout: {"claude.ai Acme Chat": ProbeStatus.CONNECTED})
    state_path = vault / ".scout-state" / "connector-preflight-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"inconclusive_streak": 2, "last_healthy_run": {}}))

    rc = run(slot_type="briefing", data_dir=vault, registry=_briefing_registry(vault))
    assert rc == EXIT_PROCEED
    state = json.loads(state_path.read_text())
    assert state["inconclusive_streak"] == 0
    assert state["last_healthy_run"]["briefing"]  # ISO timestamp recorded


def test_run_degraded_under_warn_does_not_stamp_healthy(tmp_path: Path, alerts, degraded_probe) -> None:
    """Spec Layer 1 step 6: a degraded run that proceeds under warn/run is
    NOT a healthy run."""
    vault = _vault_with_policy(tmp_path, "connector_policy:\n  on_degraded: warn\n")
    run(slot_type="briefing", data_dir=vault, registry=_briefing_registry(vault))
    state_path = vault / ".scout-state" / "connector-preflight-state.json"
    if state_path.exists():
        assert "briefing" not in json.loads(state_path.read_text()).get("last_healthy_run", {})


def test_run_inconclusive_exits_4_and_alerts_at_streak_threshold(tmp_path: Path, alerts, monkeypatch) -> None:
    vault = _vault_with_policy(tmp_path, "connector_policy:\n  on_degraded: skip\n")
    monkeypatch.setattr(cp, "_run_mcp_list", lambda claude_bin, timeout: None)
    reg = _briefing_registry(vault)
    for i in range(INCONCLUSIVE_ALERT_STREAK):
        rc = run(slot_type="briefing", data_dir=vault, registry=reg)
        assert rc == EXIT_INCONCLUSIVE
        # Alert fires exactly once, when the streak crosses the threshold.
        assert len(alerts) == (1 if i + 1 >= INCONCLUSIVE_ALERT_STREAK else 0)
    assert "inconclusive" in alerts[0]


def test_run_manual_slot_type_is_a_no_op(tmp_path: Path, alerts, monkeypatch) -> None:
    """Manual sessions have no critical connectors; the probe must not run."""

    def _boom(claude_bin, timeout):  # pragma: no cover - would fail the test
        raise AssertionError("probe must not be invoked for manual runs")

    monkeypatch.setattr(cp, "_run_mcp_list", _boom)
    vault = _vault_with_policy(tmp_path, "connector_policy:\n  on_degraded: skip\n")
    rc = run(slot_type="manual", data_dir=vault)
    assert rc == EXIT_PROCEED


def test_run_resolves_mode_to_slot_type(tmp_path: Path, alerts, degraded_probe) -> None:
    vault = _vault_with_policy(
        tmp_path,
        "connector_policy:\n  on_degraded: run\n  overrides:\n    briefing: skip\n",
    )
    rc = run(mode="morning-briefing", data_dir=vault, registry=_briefing_registry(vault))
    assert rc == EXIT_SKIP_DEGRADED


def test_run_unknown_slot_type_string_fails_open(tmp_path: Path, alerts) -> None:
    vault = _vault_with_policy(tmp_path, "connector_policy:\n  on_degraded: skip\n")
    rc = run(slot_type="not-a-type", data_dir=vault)
    assert rc == EXIT_INCONCLUSIVE


def test_run_malformed_vault_config_falls_back_to_run_policy(tmp_path: Path, alerts, degraded_probe) -> None:
    vault = _vault_with_policy(tmp_path, "connector_policy: [broken\n")
    rc = run(slot_type="briefing", data_dir=vault, registry=_briefing_registry(vault))
    assert rc == EXIT_PROCEED
