"""Read-only 'is a newer plugin version available?' check.

Auto-APPLY is intentionally out of scope here (deferred). This module only
reports installed-vs-available so /scout-status and the /scout-update nudge
can use it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

RAW_MARKETPLACE_URL = (
    "https://raw.githubusercontent.com/jordanrburger/scout-plugin/main/.claude-plugin/marketplace.json"
)


@dataclass(frozen=True)
class UpdateStatus:
    installed: str
    available: str
    update_available: bool


def _semver_tuple(v: str) -> tuple[int, int, int]:
    # strip pre-release (-...) and build metadata (+...) before parsing
    core = v.split("+")[0].split("-")[0]
    parts = core.split(".")[:3]
    nums = [int(p) for p in parts] + [0, 0, 0]
    return (nums[0], nums[1], nums[2])


def compare(*, installed: str, available: str) -> UpdateStatus:
    return UpdateStatus(
        installed=installed,
        available=available,
        update_available=_semver_tuple(available) > _semver_tuple(installed),
    )


def _installed_version() -> str:
    from scout import __version__

    return __version__


def _available_version() -> str:
    try:
        with urllib.request.urlopen(RAW_MARKETPLACE_URL, timeout=10) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"could not reach marketplace at {RAW_MARKETPLACE_URL}: {e}") from e
    try:
        return json.loads(raw)["plugins"][0]["version"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"could not parse marketplace.json from {RAW_MARKETPLACE_URL}: {e}") from e


def check(
    *,
    installed_fetcher: Callable[[], str] = _installed_version,
    available_fetcher: Callable[[], str] = _available_version,
) -> UpdateStatus:
    return compare(installed=installed_fetcher(), available=available_fetcher())
