"""Pre-session connector preflight — Layers 1-2 of the connector-resilience
design (docs/superpowers/specs/2026-07-01-connector-resilience-design.md).

Probes the health of every connector critical for the upcoming slot type
*before* the main Claude session launches, at zero model-token cost:

  * MCP connectors — parsed out of one ``claude mcp list`` invocation and
    matched verbatim by the connector's ``harness_server_name``.
  * Bash-probed connectors — the connector's ``preflight_command`` run
    directly (e.g. ``gh auth status``), harness-independently.
  * Connectors with neither field are not preflight-checkable and are
    simply not probed.

The run is **degraded** iff any probed critical connector is determinably
not connected (there is deliberately no quorum knob — tolerance is tuned by
which connectors are marked critical in ``required_in_types``). The slot
type's ``on_degraded`` policy (``connector_policy`` in the vault's
scout-config.yaml) then decides: ``skip`` (alert + exit 3), ``warn`` (write
``.scout-cache/connector-degradation-pending.md`` for the session to
consume, exit 0), or ``run`` (exit 0 — the default, today's behavior).

Exit-code contract consumed by the run-*.sh runner templates:
  0 = proceed, 3 = policy skip (runner converts to an orderly ``exit 0``),
  4 = inconclusive (probe errored/unparseable — the runner FAILS OPEN).

Error handling per the spec: a broken probe must never block runs, but
because fail-open + glyph parsing means a routine CLI format change would
silently disable the whole protection, an inconclusive-streak counter is
persisted and an alert fires when it crosses ``INCONCLUSIVE_ALERT_STREAK``.
"""

from __future__ import annotations

import enum
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scout import paths
from scout.connectors import Connector, ConnectorRegistry, load_registry
from scout.events import now_iso
from scout.schedule import SlotType, load_default_schedule, load_schedule

EXIT_PROCEED = 0
EXIT_SKIP_DEGRADED = 3
EXIT_INCONCLUSIVE = 4

DEFAULT_TIMEOUT_SECONDS = 60.0
INCONCLUSIVE_ALERT_STREAK = 3

STATE_FILENAME = "connector-preflight-state.json"
DEGRADATION_PENDING_FILENAME = "connector-degradation-pending.md"


class OnDegraded(enum.Enum):
    SKIP = "skip"
    WARN = "warn"
    RUN = "run"


class ProbeStatus(enum.Enum):
    CONNECTED = "connected"
    NEEDS_AUTH = "needs_auth"
    FAILED = "failed"
    PENDING = "pending"
    # harness_server_name not present in `claude mcp list` output at all —
    # the harness has no such server configured, so the session can't use it.
    MISSING = "missing"
    # The probe itself errored/timed out — health undeterminable. Unknowns
    # never count toward degraded (fail open); they count toward inconclusive.
    UNKNOWN = "unknown"


_STATUS_HUMAN = {
    ProbeStatus.CONNECTED: "connected",
    ProbeStatus.NEEDS_AUTH: "needs authentication",
    ProbeStatus.FAILED: "failed to connect",
    ProbeStatus.PENDING: "pending approval",
    ProbeStatus.MISSING: "not configured in `claude mcp list`",
    ProbeStatus.UNKNOWN: "probe inconclusive",
}

# `claude mcp list` status markers (Claude Code 2.1.185). NOT a stable CLI
# contract — an unrecognized format parses to nothing, which classifies as
# inconclusive (never degraded) and feeds the inconclusive-streak alert.
_MARKER_TO_STATUS = {
    "✔ Connected": ProbeStatus.CONNECTED,
    "! Needs authentication": ProbeStatus.NEEDS_AUTH,
    "✘ Failed to connect": ProbeStatus.FAILED,
    "⏸ Pending approval": ProbeStatus.PENDING,
}

# One row per server: `<name>: <url-or-command> - <marker>`. The name is
# everything before the first ": " (plugin-scoped names contain colons but
# never colon-space); the marker is anchored to end-of-line so " - " inside
# the target column can't confuse the split.
_LINE_RE = re.compile(
    r"^(?P<name>.+?): .* - (?P<marker>" + "|".join(re.escape(m) for m in _MARKER_TO_STATUS) + r")\s*$"
)


def parse_mcp_list(text: str) -> dict[str, ProbeStatus]:
    """Parse ``claude mcp list`` output into {server_name: status}.

    Unrecognized lines (preamble, blanks, future format changes) are
    skipped; a wholly unparseable output yields an empty dict, which the
    caller treats as inconclusive — never as degraded.
    """
    statuses: dict[str, ProbeStatus] = {}
    for line in text.splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            statuses[m.group("name")] = _MARKER_TO_STATUS[m.group("marker")]
    return statuses


@dataclass(frozen=True)
class ConnectorProbe:
    key: str
    display_name: str
    status: ProbeStatus

    @property
    def healthy(self) -> bool:
        return self.status is ProbeStatus.CONNECTED

    @property
    def down(self) -> bool:
        """Determinably not connected (unknown is neither healthy nor down)."""
        return self.status not in (ProbeStatus.CONNECTED, ProbeStatus.UNKNOWN)


@dataclass(frozen=True)
class PreflightResult:
    slot_type: SlotType
    probes: tuple[ConnectorProbe, ...]

    @property
    def dark(self) -> list[ConnectorProbe]:
        return [p for p in self.probes if p.down]

    @property
    def degraded(self) -> bool:
        """Degraded iff ANY probed critical connector is determinably down."""
        return bool(self.dark)

    @property
    def inconclusive(self) -> bool:
        """No determinable outage, but at least one probe couldn't decide."""
        return not self.degraded and any(p.status is ProbeStatus.UNKNOWN for p in self.probes)


# ----- probes ---------------------------------------------------------------


def _run_mcp_list(claude_bin: str, timeout: float) -> dict[str, ProbeStatus] | None:
    """Health-check every configured MCP server via ``claude mcp list``.

    Returns None when the probe is inconclusive: the binary is missing, the
    command errors or times out, or the output parses to no statuses at all.
    """
    try:
        proc = subprocess.run(
            [claude_bin, "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[connector-preflight] `{claude_bin} mcp list` failed: {e}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(
            f"[connector-preflight] `{claude_bin} mcp list` exited {proc.returncode}",
            file=sys.stderr,
        )
        return None
    statuses = parse_mcp_list(proc.stdout)
    return statuses or None


def _run_bash_probe(command: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> ProbeStatus:
    """Run a connector's ``preflight_command``; exit 0 = healthy.

    A non-zero exit is a determinable "down" (that's what the probe command
    is for); failure to launch or a timeout is UNKNOWN (fail open).
    """
    try:
        proc = subprocess.run(command, shell=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[connector-preflight] probe `{command}` errored: {e}", file=sys.stderr)
        return ProbeStatus.UNKNOWN
    return ProbeStatus.CONNECTED if proc.returncode == 0 else ProbeStatus.FAILED


def evaluate(
    registry: ConnectorRegistry,
    slot_type: SlotType,
    mcp_statuses: Mapping[str, ProbeStatus] | None,
    *,
    run_bash: Callable[[str], ProbeStatus] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> PreflightResult:
    """Classify the upcoming run from probe results.

    ``mcp_statuses`` is the parsed ``claude mcp list`` map, or None when
    that probe was inconclusive — MCP-matched connectors then read UNKNOWN.
    """

    def _default_bash(command: str) -> ProbeStatus:
        return _run_bash_probe(command, timeout=timeout)

    bash = run_bash if run_bash is not None else _default_bash

    probes: list[ConnectorProbe] = []
    for key in registry.critical_for_slot_type(slot_type):
        c: Connector = registry[key]
        if c.harness_server_name:
            if mcp_statuses is None:
                status = ProbeStatus.UNKNOWN
            else:
                status = mcp_statuses.get(c.harness_server_name, ProbeStatus.MISSING)
        elif c.preflight_command:
            status = bash(c.preflight_command)
        else:
            continue  # not preflight-checkable — simply not probed
        probes.append(ConnectorProbe(key=key, display_name=c.display_name, status=status))
    return PreflightResult(slot_type=slot_type, probes=tuple(probes))


# ----- policy ---------------------------------------------------------------


def resolve_policy(config: Mapping[str, Any], slot_type: SlotType) -> OnDegraded:
    """Resolve the ``on_degraded`` policy for a slot type from the vault config.

    Malformed values fall back (bad override → the global default; bad
    global → ``run``) with a stderr warning rather than crashing the runner.
    """
    block = config.get("connector_policy")
    if block is None:
        return OnDegraded.RUN
    if not isinstance(block, Mapping):
        print(
            "[connector-preflight] connector_policy is not a mapping — falling back to on_degraded: run",
            file=sys.stderr,
        )
        return OnDegraded.RUN

    try:
        default = OnDegraded(block.get("on_degraded", "run"))
    except ValueError:
        print(
            f"[connector-preflight] unknown connector_policy.on_degraded value "
            f"{block.get('on_degraded')!r} — falling back to run",
            file=sys.stderr,
        )
        default = OnDegraded.RUN

    overrides = block.get("overrides")
    if not isinstance(overrides, Mapping):
        if overrides is not None:
            print(
                "[connector-preflight] connector_policy.overrides is not a mapping — ignoring",
                file=sys.stderr,
            )
        return default
    raw = overrides.get(slot_type.value)
    if raw is None:
        return default
    try:
        return OnDegraded(raw)
    except ValueError:
        print(
            f"[connector-preflight] unknown connector_policy.overrides.{slot_type.value} value "
            f"{raw!r} — falling back to {default.value}",
            file=sys.stderr,
        )
        return default


def _load_vault_config(data_dir: Path) -> dict[str, Any]:
    """Tolerantly read the vault's scout-config.yaml (where the
    ``connector_policy`` block lives). Missing/malformed → {} (policy
    defaults to ``run``) with a stderr note, never a crash."""
    path = data_dir / "scout-config.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        print(f"[connector-preflight] could not read {path}: {e} — using defaults", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def resolve_slot_type(mode: str, data_dir: Path | None = None) -> SlotType:
    """Resolve the dispatcher's slot key (``SCOUT_FORCE_MODE``) to a slot type.

    Order: manual variants (never gated) → exact slot-key match in the vault
    schedule (falling back to plugin defaults) → a bare slot-type value →
    MANUAL (unknown keys are not gated rather than guessed at).
    """
    if mode == "manual" or mode.endswith("-manual"):
        return SlotType.MANUAL

    target = data_dir if data_dir is not None else paths.data_dir()
    vault_schedule = target / ".scout-state" / "schedule.yaml"
    try:
        sched = load_schedule(vault_schedule) if vault_schedule.exists() else load_default_schedule()
    except Exception as e:  # malformed schedule must not break the gate
        print(f"[connector-preflight] schedule load failed: {e}", file=sys.stderr)
        sched = None
    if sched is not None and mode in sched:
        return sched[mode].type

    try:
        return SlotType(mode)
    except ValueError:
        print(
            f"[connector-preflight] unknown mode {mode!r} — treating as manual (no gate)",
            file=sys.stderr,
        )
        return SlotType.MANUAL


# ----- state (inconclusive streak + last healthy run) ------------------------


def _state_path(data_dir: Path) -> Path:
    return paths.state_dir(data_dir) / STATE_FILENAME


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("inconclusive_streak", 0)
    if not isinstance(data.get("last_healthy_run"), dict):
        data["last_healthy_run"] = {}
    return data


def _save_state(path: Path, state: dict[str, Any]) -> None:
    """Best-effort persist — a state write failure must never fail the gate."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        print(f"[connector-preflight] could not write {path}: {e}", file=sys.stderr)


# ----- alerting --------------------------------------------------------------


def _send_telegram_alert(body: str) -> None:
    """Best-effort Telegram DM; missing secrets / network failures are logged
    and swallowed (module-level indirection so tests can capture)."""
    try:
        from scout.scripts.notify_telegram import send

        send(tier="action_required", body=body)
    except Exception as e:
        print(f"[connector-preflight] telegram alert failed: {e}", file=sys.stderr)


def _alert(data_dir: Path, body: str) -> None:
    """Fire the degradation notification channel: append to the connector
    alerts log (same file the health report uses) + best-effort Telegram."""
    log_path = paths.logs_dir(data_dir) / "connector-alerts.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{now_iso()} [preflight] {body}\n")
    except OSError as e:
        print(f"[connector-preflight] could not append {log_path}: {e}", file=sys.stderr)
    _send_telegram_alert(body)


def _write_degradation_pending(data_dir: Path, slot_type: SlotType, dark: list[ConnectorProbe]) -> None:
    """Warn-mode seam: the pre-session file the session phases consume (the
    preflight cannot export env vars into the session — see #121)."""
    lines = "\n".join(f"- {p.display_name} — {_STATUS_HUMAN[p.status]}" for p in dark)
    body = (
        f"# Connector degradation notice — preflight {now_iso()}\n\n"
        f"The pre-session connector preflight found these connectors critical for this "
        f"`{slot_type.value}` run NOT connected:\n\n"
        f"{lines}\n\n"
        "Instructions for this session:\n\n"
        "1. Prepend a degradation banner to your output naming these connectors.\n"
        '2. Do NOT record "nothing found"-style negative signals for them — the absence\n'
        "   of signal from a dark connector is not the absence of data.\n"
        "3. Delete this file once consumed so it does not leak into the next run.\n"
    )
    path = paths.cache_dir(data_dir) / DEGRADATION_PENDING_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    except OSError as e:
        print(f"[connector-preflight] could not write {path}: {e}", file=sys.stderr)


# ----- top-level entry --------------------------------------------------------


def run(
    *,
    slot_type: str | None = None,
    mode: str | None = None,
    data_dir: Path | None = None,
    claude_bin: str = "claude",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    registry: ConnectorRegistry | None = None,
) -> int:
    """Execute the preflight and return the runner exit code.

    Exactly one of ``slot_type`` (a SlotType value) or ``mode`` (the
    dispatcher's slot key) selects the slot type. ``data_dir`` and
    ``registry`` are injectable for tests; production callers leave them None.
    """
    target = data_dir if data_dir is not None else paths.data_dir()

    if slot_type is not None:
        try:
            st = SlotType(slot_type)
        except ValueError:
            print(f"[connector-preflight] unknown slot type {slot_type!r} — failing open", file=sys.stderr)
            return EXIT_INCONCLUSIVE
    elif mode is not None:
        st = resolve_slot_type(mode, data_dir=target)
    else:
        raise ValueError("pass slot_type or mode")

    reg = registry if registry is not None else load_registry(data_dir=target)
    criticals = [reg[k] for k in reg.critical_for_slot_type(st)]
    probeable = [c for c in criticals if c.harness_server_name or c.preflight_command]
    if not probeable:
        print(f"[connector-preflight] {st.value}: no preflight-checkable critical connectors — proceeding")
        return EXIT_PROCEED

    policy = resolve_policy(_load_vault_config(target), st)

    mcp_statuses: dict[str, ProbeStatus] | None = {}
    if any(c.harness_server_name for c in probeable):
        mcp_statuses = _run_mcp_list(claude_bin, timeout)
    result = evaluate(reg, st, mcp_statuses, timeout=timeout)

    state_path = _state_path(target)
    state = _load_state(state_path)

    if result.degraded:
        state["inconclusive_streak"] = 0
        _save_state(state_path, state)
        dark_desc = ", ".join(f"{p.display_name} ({_STATUS_HUMAN[p.status]})" for p in result.dark)
        print(f"[connector-preflight] {st.value}: DEGRADED — {dark_desc}; policy: {policy.value}")
        if policy is OnDegraded.SKIP:
            _alert(
                target,
                f"Preflight: skipping {st.value} run — critical connectors down: {dark_desc}",
            )
            return EXIT_SKIP_DEGRADED
        if policy is OnDegraded.WARN:
            _write_degradation_pending(target, st, result.dark)
        return EXIT_PROCEED

    if result.inconclusive:
        state["inconclusive_streak"] = int(state.get("inconclusive_streak", 0)) + 1
        _save_state(state_path, state)
        streak = state["inconclusive_streak"]
        print(f"[connector-preflight] {st.value}: inconclusive probe (streak: {streak}) — failing open")
        if streak == INCONCLUSIVE_ALERT_STREAK:
            _alert(
                target,
                f"Preflight has been inconclusive for {streak} consecutive runs — "
                "the probe may be broken (e.g. `claude mcp list` output format changed). "
                "Connector-degradation protection is effectively disabled until this is fixed.",
            )
        return EXIT_INCONCLUSIVE

    state["inconclusive_streak"] = 0
    state["last_healthy_run"][st.value] = now_iso()
    _save_state(state_path, state)
    print(f"[connector-preflight] {st.value}: all critical connectors healthy — proceeding")
    return EXIT_PROCEED


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "DEGRADATION_PENDING_FILENAME",
    "EXIT_INCONCLUSIVE",
    "EXIT_PROCEED",
    "EXIT_SKIP_DEGRADED",
    "INCONCLUSIVE_ALERT_STREAK",
    "STATE_FILENAME",
    "ConnectorProbe",
    "OnDegraded",
    "PreflightResult",
    "ProbeStatus",
    "evaluate",
    "parse_mcp_list",
    "resolve_policy",
    "resolve_slot_type",
    "run",
]
