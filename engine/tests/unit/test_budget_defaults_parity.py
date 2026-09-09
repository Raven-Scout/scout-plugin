"""The packaged defaults' ``budget:`` block must equal ``BudgetConfig()``.

``budget_check.py`` deliberately does not load the packaged-defaults layer — it
regex-scans the vault file to keep pyyaml off the pre-run gate path (#74). So the
shipped YAML does not *govern*; it documents. This test is what makes the
documentation trustworthy: if the two drift, CI goes red instead of the file
quietly becoming a lie, which is exactly what happened to the ``budgets:`` block
it replaced (shipped 150, real default 50, nothing reading either).
"""

from __future__ import annotations

from importlib.resources import as_file, files
from typing import Any

import yaml

from scout.scripts.budget_check import BudgetConfig


def _packaged_defaults() -> dict[str, Any]:
    resource = files("scout") / "defaults" / "scout-config.yaml"
    with as_file(resource) as path:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "packaged defaults must be a YAML mapping"
    return data


def _packaged_budget_block() -> dict[str, Any]:
    block = _packaged_defaults().get("budget")
    assert isinstance(block, dict), "packaged defaults must define a `budget:` mapping"
    return block


def test_packaged_defaults_match_budget_config_dataclass() -> None:
    block = _packaged_budget_block()
    defaults = BudgetConfig()
    assert block["daily_usd"] == defaults.daily_budget_usd
    assert block["window_hours"] == defaults.window_hours
    assert block["skip_at_pct"] == defaults.skip_threshold_pct
    assert block["failure_backoff_minutes"] == defaults.failure_backoff_min


def test_packaged_budget_block_holds_exactly_the_enforced_knobs() -> None:
    """A knob in the shipped file that budget_check does not read is the defect
    this design removed — `budgets.max_per_session_usd` was one. Keep the block
    to exactly the four keys the gate enforces."""
    assert set(_packaged_budget_block()) == {
        "daily_usd",
        "window_hours",
        "skip_at_pct",
        "failure_backoff_minutes",
    }


def test_old_budgets_block_is_gone() -> None:
    """The plural `budgets:` key was read by nothing under scout/. Its removal
    is the point; a re-added copy would resurrect the two-schema problem."""
    assert "budgets" not in _packaged_defaults()
