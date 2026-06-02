from __future__ import annotations

from scout.scripts import self_update


def test_compare_detects_update():
    r = self_update.compare(installed="0.4.0", available="0.5.0")
    assert r.update_available is True
    assert r.installed == "0.4.0" and r.available == "0.5.0"


def test_compare_no_update_when_equal():
    assert self_update.compare(installed="0.5.0", available="0.5.0").update_available is False


def test_compare_no_update_when_installed_ahead():
    assert self_update.compare(installed="0.6.0", available="0.5.0").update_available is False


def test_check_uses_injected_fetchers(monkeypatch):
    r = self_update.check(
        installed_fetcher=lambda: "0.4.0",
        available_fetcher=lambda: "0.5.0",
    )
    assert r.update_available is True
