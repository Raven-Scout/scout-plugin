"""Canary: the suite must never see the developer's real HOME or SCOUT_* env.

paths.data_dir() falls back to Path.home()/Scout when SCOUT_DATA_DIR is
unset, so without isolation any test that exercises default path resolution
reads the developer's live vault (e.g. the schedule CLI tests picked up the
live schedule.yaml overlay and failed on slot count). The autouse
_hermetic_env fixture in conftest.py points HOME at a pytest tmp dir and
scrubs SCOUT_* vars; these tests fail loudly if that ever regresses.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_home_is_isolated() -> None:
    # tmp_path_factory dirs always contain a "pytest-<N>" path segment.
    assert "pytest-" in str(Path.home()), (
        f"Path.home() leaked the real home: {Path.home()}"
    )


def test_no_scout_env_leaks() -> None:
    leaked = sorted(k for k in os.environ if k.startswith("SCOUT_"))
    assert leaked == [], f"SCOUT_* env vars leaked into the test env: {leaked}"
