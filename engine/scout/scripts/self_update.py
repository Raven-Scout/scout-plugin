"""Read-only 'is a newer plugin version available?' check.

Auto-APPLY is intentionally out of scope here (deferred). This module only
reports installed-vs-available so /scout-status and the /scout-update nudge
can use it.
"""

from __future__ import annotations

import json
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
    parts = (v.split("-")[0].split("."))[:3]
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
    with urllib.request.urlopen(RAW_MARKETPLACE_URL, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    return data["plugins"][0]["version"]


def check(
    *,
    installed_fetcher: Callable[[], str] = _installed_version,
    available_fetcher: Callable[[], str] = _available_version,
) -> UpdateStatus:
    return compare(installed=installed_fetcher(), available=available_fetcher())
