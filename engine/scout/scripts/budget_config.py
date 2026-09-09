"""Read/write support for the vault's canonical ``budget:`` block.

Split from :mod:`scout.scripts.budget_check` on purpose. That module sits on the
pre-run gate path and stays minimal — one regex scanner, no pyyaml (#74).
Everything here serves interactive callers instead: ``scoutctl budget show`` and
``set``, and through them the macOS app's Settings pane.

The writer is line-based rather than a YAML round-trip because
``scout-config.yaml`` is not single-purpose — it doubles as bootstrap state
(version stamps, connectors, schedule) written by several producers. A pyyaml
round-trip would strip every comment in it, and a whole-file rewrite risks
dropping a subtree a newer bootstrap wrote.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scout import paths
from scout.scripts.budget_check import load_config_with_source

# Dataclass field name -> canonical YAML key under `budget:`.
FIELD_TO_YAML_KEY = {
    "daily_budget_usd": "daily_usd",
    "window_hours": "window_hours",
    "skip_threshold_pct": "skip_at_pct",
    "failure_backoff_min": "failure_backoff_minutes",
}

# Keys whose presence in a dotted `.scout-config.yaml` means someone believes
# that file configures the budget. It does not — see warn_if_legacy_dotfile.
_DOTFILE_BUDGET_MARKERS = (
    "daily_budget_estimate_usd",
    "rate_limit_window_hours",
    "skip_threshold_pct",
    "failure_backoff_minutes",
    "daily_usd",
    "skip_at_pct",
)


def show_payload(data_dir: Path | None = None) -> dict[str, Any]:
    """The effective budget config plus the gate it computes to.

    This dict IS the `scoutctl budget show --json` contract that scout-app's
    BudgetSettingsService decodes. The two derived fields are served from here
    so the app has an authoritative value to check its own arithmetic against.
    """
    config_path = paths.config_path(data_dir)
    config, source = load_config_with_source(config_path)
    return {
        "config_path": str(config_path),
        "source": source,
        "daily_usd": config.daily_budget_usd,
        "window_hours": config.window_hours,
        "skip_at_pct": config.skip_threshold_pct,
        "failure_backoff_minutes": config.failure_backoff_min,
        "window_budget_usd": config.window_budget_usd,
        "skip_threshold_usd": config.skip_threshold_usd,
    }


def warn_if_legacy_dotfile(data_dir: Path | None = None) -> str | None:
    """Warn when a dotted ``.scout-config.yaml`` carries budget keys.

    Nothing reads that file — ``paths.config_path`` documents it as a dotfile no
    code path ever wrote, dead since #207/#202 — but it reads like live
    configuration, which is how a far tighter gate than anyone intended stayed
    invisible for months. Returns the warning text, or None when there's nothing
    to say.
    """
    dotted = (data_dir or paths.data_dir()) / ".scout-config.yaml"
    try:
        text = dotted.read_text(encoding="utf-8")
    except OSError:
        return None
    if not any(marker in text for marker in _DOTFILE_BUDGET_MARKERS):
        return None
    return (
        f"{dotted} carries budget keys but is read by nothing — the live file is "
        "scout-config.yaml (no dot). Values in the dotted file have no effect; "
        "`scoutctl budget set` writes the file that does."
    )


__all__ = ["FIELD_TO_YAML_KEY", "show_payload", "warn_if_legacy_dotfile"]
