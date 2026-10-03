"""A re-run of /scout-update or install.sh must switch the registered version.

On an existing install `claude plugin install` only unpacks the new cache and
keeps the old version registered, so the plugin-root resolver that runs next
returns the old plugin. Both entry points therefore follow the install with
`claude plugin update` (#234, stale-registry half).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent.parent.parent


@pytest.mark.parametrize(
    ("relpath", "install", "update"),
    [
        (
            "commands/scout-update.md",
            "claude plugin install scout@scout-plugin",
            "claude plugin update scout@scout-plugin",
        ),
        ("install.sh", 'claude plugin install "$PLUGIN_ID"', 'claude plugin update "$PLUGIN_ID"'),
    ],
)
def test_update_follows_install(relpath: str, install: str, update: str) -> None:
    text = (_REPO / relpath).read_text(encoding="utf-8")
    assert install in text
    assert update in text
    assert text.index(update) > text.index(install), f"{relpath}: update must run after install"
