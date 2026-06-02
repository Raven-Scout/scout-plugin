from __future__ import annotations

import pytest

from scout.scripts import self_update


def test_compare_detects_update():
    r = self_update.compare(installed="0.4.0", available="0.5.0")
    assert r.update_available is True
    assert r.installed == "0.4.0" and r.available == "0.5.0"


def test_compare_no_update_when_equal():
    assert self_update.compare(installed="0.5.0", available="0.5.0").update_available is False


def test_compare_no_update_when_installed_ahead():
    assert self_update.compare(installed="0.6.0", available="0.5.0").update_available is False


def test_check_uses_injected_fetchers():
    r = self_update.check(
        installed_fetcher=lambda: "0.4.0",
        available_fetcher=lambda: "0.5.0",
    )
    assert r.update_available is True


@pytest.mark.parametrize(
    "raw,expected",
    [("0.5.0", (0, 5, 0)), ("0.5", (0, 5, 0)), ("0.5.0-beta.1", (0, 5, 0)), ("1.2.3+build.7", (1, 2, 3))],
)
def test_semver_tuple_parsing(raw, expected):
    assert self_update._semver_tuple(raw) == expected
