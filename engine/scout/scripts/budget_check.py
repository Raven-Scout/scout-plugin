"""Pre-run budget check — decides whether a Scout session should proceed.

Port of ``~/Scout/scripts/budget-check.sh``. Same exit-code semantics:
  - 0 = proceed (budget available)
  - 1 = skip (budget exhausted or near limit)
  - 2 = backoff (recent rate-limit event or recent failure)

Per #74, the bash version shells out to ``python3 -c`` five+ times for trivial
JSON / datetime arithmetic, paying ~150-300 ms of interpreter startup each.
Folding the logic into a single ``scoutctl budget check`` invocation means
one cold start per session-launch instead of five.

The check is intentionally tolerant of malformed config / tracker rows:
unparseable rows are silently skipped (same as the bash original).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scout import paths

# Defaults — mirror budget-check.sh defaults so behavior is preserved when no
# .scout-config.yaml is present.
DEFAULT_WINDOW_HOURS = 5
DEFAULT_DAILY_BUDGET_USD = 50.0
DEFAULT_SKIP_THRESHOLD_PCT = 80.0
DEFAULT_FAILURE_BACKOFF_MIN = 60

EXIT_PROCEED = 0
EXIT_SKIP_OVER_BUDGET = 1
EXIT_BACKOFF = 2


@dataclass(frozen=True)
class BudgetConfig:
    daily_budget_usd: float = DEFAULT_DAILY_BUDGET_USD
    window_hours: int = DEFAULT_WINDOW_HOURS
    skip_threshold_pct: float = DEFAULT_SKIP_THRESHOLD_PCT
    failure_backoff_min: int = DEFAULT_FAILURE_BACKOFF_MIN

    @property
    def window_budget_usd(self) -> float:
        return round(self.daily_budget_usd * self.window_hours / 24, 2)

    @property
    def skip_threshold_usd(self) -> float:
        return round(self.window_budget_usd * self.skip_threshold_pct / 100, 2)


@dataclass(frozen=True)
class BudgetDecision:
    exit_code: int
    reason: str  # human-readable explanation for --verbose / event logs

    @property
    def should_proceed(self) -> bool:
        return self.exit_code == EXIT_PROCEED


# Lightweight YAML reader for the four flat scalar keys this script needs.
# Avoids importing pyyaml on the hot path — and the bash version uses grep+awk
# for parity reasons. Anything more structured falls through to the default.
_CONFIG_KEYS = {
    "daily_budget_estimate_usd": ("daily_budget_usd", float),
    "rate_limit_window_hours": ("window_hours", int),
    "skip_threshold_pct": ("skip_threshold_pct", float),
    "failure_backoff_minutes": ("failure_backoff_min", int),
}

_CONFIG_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^#\s][^#]*?)\s*(?:#.*)?$")


def load_config(config_path: Path) -> BudgetConfig:
    """Parse the four scalar keys this check cares about from .scout-config.yaml.

    Missing file or unparseable rows fall back to defaults — matches the bash
    `grep ... | awk` pattern which silently no-ops on missing keys.
    """
    overrides: dict[str, Any] = {}
    if not config_path.exists():
        return BudgetConfig()
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return BudgetConfig()
    for line in text.splitlines():
        m = _CONFIG_LINE_RE.match(line)
        if not m:
            continue
        yaml_key, raw_value = m.group(1), m.group(2).strip().strip("\"'")
        mapping = _CONFIG_KEYS.get(yaml_key)
        if mapping is None:
            continue
        field_name, caster = mapping
        try:
            overrides[field_name] = caster(raw_value)
        except (TypeError, ValueError):
            continue
    return BudgetConfig(**overrides)


def _iter_tracker_rows(tracker_path: Path) -> Iterable[dict]:
    """Yield decoded JSON rows from the tracker; skip malformed lines silently."""
    try:
        with tracker_path.open("r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def decide(
    tracker_path: Path,
    config: BudgetConfig,
    *,
    now: datetime | None = None,
) -> BudgetDecision:
    """Return the budget decision for *now* given the tracker history.

    Walks the tracker exactly once and computes everything the bash script
    used three separate ``python3 -c`` invocations to extract: window-cost
    sum, last exit_code + ts, and whether any rate_limit event landed within
    the failure-backoff window (×2).
    """
    if not tracker_path.exists():
        return BudgetDecision(EXIT_PROCEED, "no tracker — first run")

    now = now or datetime.now(UTC)
    window_start = now - timedelta(hours=config.window_hours)
    rate_limit_cutoff = now - timedelta(minutes=config.failure_backoff_min * 2)

    total_cost = 0.0
    last_exit: int | None = None
    last_ts: datetime | None = None
    saw_recent_rate_limit = False

    for row in _iter_tracker_rows(tracker_path):
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        # Rate-limit gate uses the wider 2× backoff window from bash:86-103.
        if row.get("type") == "rate_limit" and ts >= rate_limit_cutoff:
            saw_recent_rate_limit = True

        if ts < window_start:
            continue

        try:
            total_cost += float(row.get("budget_spent", 0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            last_exit = int(row.get("exit_code", 0))
        except (TypeError, ValueError):
            pass
        last_ts = ts

    if saw_recent_rate_limit:
        return BudgetDecision(
            EXIT_BACKOFF,
            f"rate_limit event in last {config.failure_backoff_min * 2}m — backing off",
        )

    if last_exit not in (0, None) and last_ts is not None:
        minutes_since = int((now - last_ts).total_seconds() / 60)
        if minutes_since < config.failure_backoff_min:
            return BudgetDecision(
                EXIT_BACKOFF,
                f"recent failure {minutes_since}m ago (backoff: {config.failure_backoff_min}m)",
            )

    if total_cost >= config.skip_threshold_usd:
        return BudgetDecision(
            EXIT_SKIP_OVER_BUDGET,
            f"window cost ${total_cost:.2f} >= skip threshold ${config.skip_threshold_usd:.2f}",
        )

    return BudgetDecision(
        EXIT_PROCEED,
        f"budget OK — ${total_cost:.2f} spent (threshold: ${config.skip_threshold_usd:.2f})",
    )


def run(*, verbose: bool = False, data_dir: Path | None = None) -> int:
    """Execute the check and return the appropriate exit code.

    ``data_dir`` is exposed so tests can target a tmp vault without setting
    the environment variable. Production callers leave it None.
    """
    target = data_dir or paths.data_dir()
    tracker_path = paths.logs_dir(target) / "usage-tracker.jsonl"
    config = load_config(paths.config_path(target))
    decision = decide(tracker_path, config)
    if verbose:
        print(f"[budget-check] {decision.reason}")
        print(
            f"[budget-check] window: {config.window_hours}h, "
            f"daily: ${config.daily_budget_usd:.2f}, "
            f"window budget: ${config.window_budget_usd:.2f}, "
            f"skip at: ${config.skip_threshold_usd:.2f}"
        )
    return decision.exit_code


__all__ = [
    "BudgetConfig",
    "BudgetDecision",
    "DEFAULT_DAILY_BUDGET_USD",
    "DEFAULT_FAILURE_BACKOFF_MIN",
    "DEFAULT_SKIP_THRESHOLD_PCT",
    "DEFAULT_WINDOW_HOURS",
    "EXIT_BACKOFF",
    "EXIT_PROCEED",
    "EXIT_SKIP_OVER_BUDGET",
    "decide",
    "load_config",
    "run",
]
