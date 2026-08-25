"""Layered configuration loader for Scout.

Precedence (low → high, later overrides earlier):
  1. Engine defaults (scout/defaults/scout-config.yaml, shipped with package)
  2. User overrides ($SCOUT_DATA_DIR/.scout-config.yaml)
  3. SCOUT_* environment variables (whitelisted keys)
"""

from __future__ import annotations

import datetime as _dt
import os
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from scout import paths
from scout.errors import ConfigError

# Packaged default zone (mirrors user.timezone in defaults/scout-config.yaml).
# Also the terminal fallback when the configured zone is missing or invalid.
DEFAULT_TIMEZONE = "America/New_York"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge. `override` wins on conflicts."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _env_overrides() -> dict[str, Any]:
    """Whitelisted SCOUT_* env vars → config overrides."""
    out: dict[str, Any] = {}
    if v := os.environ.get("SCOUT_USER_EMAIL"):
        out.setdefault("user", {})["email"] = v
    if v := os.environ.get("SCOUT_USER_TIMEZONE"):
        out.setdefault("user", {})["timezone"] = v
    return out


def _read_packaged_defaults() -> dict[str, Any]:
    """Read the shipped scout-config.yaml via importlib.resources.

    Resolving through importlib.resources keeps load_config() working
    when the package is installed from a wheel — Path(__file__).parent
    navigation breaks because the defaults sit under scout/defaults/
    in the installed tree, not relative to a sibling 'engine/' dir.
    """
    resource = files("scout") / "defaults" / "scout-config.yaml"
    with as_file(resource) as path:
        return _read_yaml(path)


def load_config(data_dir: Path | None = None) -> dict[str, Any]:
    """Load the three-layer merged config."""
    defaults = _read_packaged_defaults()
    user_path = paths.config_path(data_dir)
    user_overrides = _read_yaml(user_path)
    env_overrides = _env_overrides()

    merged = _deep_merge(defaults, user_overrides)
    merged = _deep_merge(merged, env_overrides)
    return merged


# ----- day boundary ---------------------------------------------------------
#
# Scout's "today" (daily action-items filename, trigger daily caps, freshness
# math, rendered timestamps) is a civil date in ONE zone: the user's configured
# timezone. Before #207 the codebase had multiple authorities — the configured
# zone, bare host-clock date.today(), and hardcoded America/New_York — which
# agreed only while the config read was broken. Everything below is the single
# Python-side authority; the shell-side twin is templates/scripts/scout-tz.sh,
# which resolves the same config field.


def timezone_or_default(tz_name: object) -> ZoneInfo:
    """ZoneInfo for ``tz_name``, falling back to :data:`DEFAULT_TIMEZONE`.

    The fallback lives INSIDE the resolver on purpose (#207): a missing,
    malformed, or unknown zone shifts every consumer to the same default
    together, instead of each call site inventing its own fallback and
    splitting the day boundary between surfaces.
    """
    if isinstance(tz_name, str) and tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return ZoneInfo(DEFAULT_TIMEZONE)


def resolve_timezone(data_dir: Path | None = None) -> ZoneInfo:
    """The configured day-boundary zone for ``data_dir``'s vault.

    Never raises — config problems degrade to :data:`DEFAULT_TIMEZONE` so a
    bad edit can never make a run timezone-blind (mirrors scout-tz.sh).
    """
    try:
        user = load_config(data_dir).get("user")
        tz_name = user.get("timezone") if isinstance(user, dict) else None
    except Exception:
        tz_name = None
    return timezone_or_default(tz_name)


def now(data_dir: Path | None = None) -> _dt.datetime:
    """Wall-clock now in the configured zone (tz-aware)."""
    return _dt.datetime.now(resolve_timezone(data_dir))


def today(data_dir: Path | None = None) -> _dt.date:
    """THE day boundary: today's civil date in the configured zone.

    Every writer or reader that derives a daily filename, a daily cap, or a
    "today" label must come through here (or :func:`resolve_timezone`) so the
    whole system flips dates at the same instant (#207).
    """
    return now(data_dir).date()
