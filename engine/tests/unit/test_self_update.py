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


def test_check_propagates_runtime_error_from_available_fetcher():
    """check() must surface RuntimeError from available_fetcher (e.g. network failure)."""

    def failing_fetcher() -> str:
        raise RuntimeError("could not reach marketplace at https://example.com: <urlopen error ...>")

    with pytest.raises(RuntimeError, match="could not reach marketplace"):
        self_update.check(
            installed_fetcher=lambda: "0.5.0",
            available_fetcher=failing_fetcher,
        )


# --- default fetchers -------------------------------------------------------
#
# check()'s injected seams are covered above; these exercise the fetchers that
# actually run in production. `_available_version` reaches the network, so the
# urlopen is stubbed — the point is the error mapping, which is what
# /scout-status and the /scout-update nudge surface to the user.


def test_installed_version_reads_the_package_version():
    from scout import __version__

    assert self_update._installed_version() == __version__


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_available_version_reads_the_first_plugins_entry(monkeypatch):
    import json

    body = json.dumps({"plugins": [{"name": "scout", "version": "0.9.0"}]}).encode()
    seen: list[str] = []

    def fake_urlopen(url, timeout=None):  # noqa: ARG001
        seen.append(url)
        return _FakeResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert self_update._available_version() == "0.9.0"
    assert seen == [self_update.RAW_MARKETPLACE_URL]


@pytest.mark.parametrize(
    "exc",
    [
        __import__("urllib.error", fromlist=["URLError"]).URLError("dns failure"),
        TimeoutError("timed out"),
        OSError("connection reset"),
    ],
)
def test_available_version_maps_network_failure_to_a_named_runtime_error(monkeypatch, exc):
    """The message must name the URL so an operator behind a proxy knows what
    to allow — a bare URLError traceback doesn't say that."""

    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(RuntimeError, match="could not reach marketplace at"):
        self_update._available_version()


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"{}",  # no "plugins"
        b'{"plugins": []}',  # empty list
        b'{"plugins": [{"name": "scout"}]}',  # no "version"
        b'{"plugins": "scout"}',  # wrong type
    ],
)
def test_available_version_maps_a_malformed_marketplace_to_a_runtime_error(monkeypatch, body):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResponse(body))
    with pytest.raises(RuntimeError, match="could not parse marketplace.json"):
        self_update._available_version()


def test_check_defaults_use_both_real_fetchers(monkeypatch):
    import json

    from scout import __version__

    body = json.dumps({"plugins": [{"version": "99.0.0"}]}).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: _FakeResponse(body))

    status = self_update.check()
    assert status.installed == __version__
    assert status.available == "99.0.0"
    assert status.update_available is True
