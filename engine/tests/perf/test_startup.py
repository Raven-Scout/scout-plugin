"""Startup-latency tests for scoutctl.

These assert that import + help + version paths stay fast so the
scout-app UI doesn't feel laggy when invoking scoutctl on user actions.

Budgets have CI headroom; local Macs hit them in half the listed time.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

HELP_BUDGET_MS = 250
VERSION_BUDGET_MS = 150
_RUNS = 5


def _best_latency_ms(args: list[str]) -> tuple[float, str]:
    """Best-of-N startup latency: the minimum elapsed over ``_RUNS`` invocations.

    A single subprocess sample includes OS scheduling / cold-disk / shared-runner
    noise (the source of past CI flakes — a lone 192ms spike against a 150ms
    budget). The *minimum* reflects the achievable floor, which is what actually
    regresses when a heavy top-level import is added — so the guard stays
    meaningful while no longer flaking on one noisy sample. Returns
    ``(best_ms, last_stdout)``.
    """
    best = float("inf")
    stdout = ""
    for _ in range(_RUNS):
        start = time.perf_counter()
        result = subprocess.run(
            [sys.executable, "-m", "scout.cli", *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert result.returncode == 0
        stdout = result.stdout
        best = min(best, elapsed_ms)
    return best, stdout


@pytest.mark.perf
def test_scoutctl_help_latency() -> None:
    best_ms, _ = _best_latency_ms(["--help"])
    assert best_ms < HELP_BUDGET_MS, (
        f"scoutctl --help took {best_ms:.0f}ms (best of {_RUNS}, budget: {HELP_BUDGET_MS}ms). "
        "Check for heavy top-level imports."
    )


@pytest.mark.perf
def test_scoutctl_version_latency() -> None:
    best_ms, stdout = _best_latency_ms(["version"])
    assert stdout.strip(), "version should emit to stdout"
    assert best_ms < VERSION_BUDGET_MS, (
        f"scoutctl version took {best_ms:.0f}ms (best of {_RUNS}, budget: {VERSION_BUDGET_MS}ms)."
    )
