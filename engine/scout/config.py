"""Layered configuration loader for Scout.

Precedence (low → high, later overrides earlier):
  1. Engine defaults (scout/defaults/scout-config.yaml, shipped with package)
  2. The vault's scout-config.yaml ($SCOUT_DATA_DIR/scout-config.yaml — the
     file /scout-setup and bootstrap write; NO dot, see #207/#202)
  3. SCOUT_* environment variables (whitelisted keys)

The vault file doubles as bootstrap state (version stamps, connectors,
schedule, plan) and predates the canonical schema, so layer 2 is read
tolerantly: legacy key shapes are normalized on read (never rewritten on
disk), unreadable YAML degrades to defaults with a stderr warning, and a
scalar where the defaults define a mapping is ignored with a warning instead
of clobbering the subtree. This layer was silently dead for every existing
vault until #207 — tolerance keeps switching it on from turning a stale or
hand-mangled file into a crash.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
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


def _warn(msg: str) -> None:
    """One-line stderr warning. The silent-defaults fallback is what kept
    #202 invisible for so long — degradation must be loud enough to spot in
    --verbose output and run logs, but must never raise."""
    print(f"scout-config: {msg}", file=sys.stderr)


def _normalize_legacy_keys(overrides: dict[str, Any]) -> dict[str, Any]:
    """Map the key shapes bootstrap actually writes onto the canonical schema
    (#207 §2). Read-side only — the vault file is never rewritten, so
    existing vaults need no migration. Explicit canonical ``user.*`` keys
    always win; legacy keys only fill gaps.

      timezone (top level)               → user.timezone
      connectors.inputs.github_username  → user.github_username
      connectors.inputs.user_slack_id    → user.slack_user_id
    """
    out = dict(overrides)
    raw_user = out.get("user")
    user: dict[str, Any] = dict(raw_user) if isinstance(raw_user, dict) else {}

    def fill(canonical: str, value: object) -> None:
        if isinstance(value, str) and value and not user.get(canonical):
            user[canonical] = value

    fill("timezone", out.get("timezone"))
    connectors = out.get("connectors")
    inputs = connectors.get("inputs") if isinstance(connectors, dict) else None
    if isinstance(inputs, dict):
        fill("github_username", inputs.get("github_username"))
        fill("slack_user_id", inputs.get("user_slack_id"))

    if user:
        out["user"] = user
    return out


def _merge_user_layer(defaults: dict[str, Any], overrides: dict[str, Any], _path: str = "") -> dict[str, Any]:
    """Deep merge with a guard: where the DEFAULTS define a mapping, a
    non-mapping override is ignored with a warning instead of replacing the
    subtree (a stale ``user: oops`` must not take user.timezone down with
    it). Keys unknown to the defaults — bootstrap state like ``plugin`` or
    ``schedule`` — merge through silently."""
    result = dict(defaults)
    for key, value in overrides.items():
        where = f"{_path}{key}"
        if key in result and isinstance(result[key], dict):
            if isinstance(value, dict):
                result[key] = _merge_user_layer(result[key], value, f"{where}.")
            else:
                _warn(f"ignoring '{where}': expected a mapping, got {type(value).__name__} — using defaults")
        else:
            result[key] = value
    return result


def load_config(data_dir: Path | None = None) -> dict[str, Any]:
    """Load the three-layer merged config.

    Never raises on a bad VAULT file — the packaged defaults must scream
    (a broken wheel is a bug), but the user layer warns and degrades so a
    stale or hand-mangled scout-config.yaml cannot block a run.
    """
    defaults = _read_packaged_defaults()
    user_path = paths.config_path(data_dir)
    try:
        user_overrides = _read_yaml(user_path)
    except ConfigError as e:
        _warn(f"ignoring unreadable {user_path.name}: {e} — running on packaged defaults")
        user_overrides = {}
    env_overrides = _env_overrides()

    merged = _merge_user_layer(defaults, _normalize_legacy_keys(user_overrides))
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
