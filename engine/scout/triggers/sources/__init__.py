"""Trigger source registry.

v1 ships three pollers: ``slack``, ``github``, ``scout_internal``.
``linear``, ``gmail``, ``gcal``, and ``file`` are spec'd but deferred —
adding one means writing the module and appending its name here.
"""

from __future__ import annotations

import importlib

from scout.errors import ConfigError
from scout.triggers.sources.base import ConnectorEvent, TriggerSource

__all__ = ["ConnectorEvent", "TriggerSource", "SOURCE_NAMES", "get_source", "supported_match_types"]

# Module name == source name under scout.triggers.sources.
SOURCE_NAMES: tuple[str, ...] = ("slack", "github", "scout_internal")


def get_source(name: str, *, vault) -> TriggerSource:
    """Construct one trigger source wired to the given vault path."""
    if name == "slack":
        from scout.triggers.sources.slack import SlackSource

        return SlackSource.for_vault(vault)
    if name == "github":
        from scout.triggers.sources.github import GitHubSource

        return GitHubSource()
    if name == "scout_internal":
        from scout import paths
        from scout.triggers.sources.scout_internal import ScoutInternalSource

        return ScoutInternalSource(paths.logs_dir(vault))
    raise ConfigError(f"unknown trigger source: {name!r}; supported: {', '.join(SOURCE_NAMES)}")


def supported_match_types(source: str) -> list[str]:
    """Return the source module's ``SUPPORTED_MATCH_TYPES`` constant.

    Lazy import so config validation doesn't pay for every source's
    dependencies. Raises ``ConfigError`` for unknown sources.
    """
    if source not in SOURCE_NAMES:
        raise ConfigError(f"unknown trigger source: {source!r}; supported: {', '.join(SOURCE_NAMES)}")
    module = importlib.import_module(f"scout.triggers.sources.{source}")
    return list(module.SUPPORTED_MATCH_TYPES)
