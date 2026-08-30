"""The test suite must not be able to reach the network.

A unit test that escaped the hermetic-HOME fixture read the developer's real
Telegram credentials and delivered a live push to their phone. Blocking egress
at the transport makes that class of leak fail loudly instead of silently
succeeding.
"""

from __future__ import annotations

import pytest
import requests


def test_outbound_http_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="outbound HTTP"):
        requests.post("http://127.0.0.1:9/blocked", json={}, timeout=1)


def test_outbound_http_via_session_is_blocked() -> None:
    with pytest.raises(RuntimeError, match="outbound HTTP"):
        requests.Session().get("http://127.0.0.1:9/blocked", timeout=1)


@pytest.mark.allow_network
def test_marker_lifts_the_block() -> None:
    """The escape hatch stays available for a test that genuinely needs egress."""
    with pytest.raises(requests.RequestException):
        requests.get("http://127.0.0.1:9/blocked", timeout=1)
