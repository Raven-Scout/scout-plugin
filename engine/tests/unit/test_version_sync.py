"""CI guard: the four version-carrying manifests must never drift."""

from __future__ import annotations

from scout.scripts import versioning


def test_repo_versions_are_in_sync():
    # Uses the real plugin root (versioning.PLUGIN_ROOT). Fails loudly if any
    # of the four manifests disagree — this is the permanent drift backstop.
    versioning.assert_in_sync()
