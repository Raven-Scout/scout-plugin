"""Connector roster: typed loader for connectors.yaml + optional vault overlay.

Single source of truth for which connectors Scout tracks, which modes they're
critical in, and how to remediate them when they go dark. Consumed by:
  - scout.hooks.connector_log         (classifies tool calls into connector keys)
  - scout.scripts.connector_health_report  (alerting + connector-health.md rendering)
  - scout-app's ConnectorHealthService  (default roster for the rail card)
  - v0.8 `scoutctl connectors` sub-app  (discover/enable/disable)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from scout import paths
from scout.errors import ConfigError
from scout.schedule import SlotType


class Tier(enum.Enum):
    OFFICIAL = "official"
    AUTO_DISCOVERED = "auto_discovered"
    COMMUNITY = "community"


class Capability(enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    META = "meta"


@dataclass(frozen=True)
class Remediation:
    first_fix: str  # ≤ 180 chars — fits in DM truncation budget
    detail: str  # multi-line; rendered in connector-health.md


@dataclass(frozen=True)
class Connector:
    key: str
    display_name: str
    tier: Tier
    capabilities: tuple[Capability, ...]
    # DEPRECATED (kept for one transitional version): tuple of mode strings, or
    # "all". Plan 5 Task 6 introduced ``required_in_types`` as the canonical
    # vocabulary; ``required_in`` is now empty for new YAML rows and only
    # populated when an overlay still uses the old shape. Prefer
    # ``required_in_type(slot_type)`` over ``required_in_mode(mode)``.
    required_in: tuple[str, ...] | str
    # v0.5+ canonical: which slot TYPES this connector is required for.
    # Replaces ``required_in`` (which keyed on slot KEY names — fragile when
    # users rename slots). Outbound-only connectors keep this empty.
    required_in_types: tuple[SlotType, ...]
    remediation: Remediation
    notes: str = ""

    def required_in_mode(self, mode: str) -> bool:
        """DEPRECATED: keyed on slot key. Prefer ``required_in_type``.

        Retained for one transitional version so any caller still passing
        slot-key strings keeps working. Returns True iff ``mode`` matches
        the legacy ``required_in`` field.
        """
        if self.required_in == "all":
            return True
        return mode in self.required_in

    def required_in_type(self, slot_type: SlotType) -> bool:
        """v0.5+ canonical: is this connector required in any slot of the given type?

        Returns False for outbound-only connectors (empty ``required_in_types``).
        """
        return slot_type in self.required_in_types


class ConnectorRegistry:
    """Indexed view over loaded connectors. Use load_registry() to construct."""

    def __init__(self, connectors: dict[str, Connector]):
        self._connectors = connectors

    def __contains__(self, key: str) -> bool:
        return key in self._connectors

    def __getitem__(self, key: str) -> Connector:
        return self._connectors[key]

    def __iter__(self):
        return iter(self._connectors)

    def items(self):
        return self._connectors.items()

    def keys(self):
        return self._connectors.keys()

    def values(self):
        return self._connectors.values()

    def critical_in_mode(self, mode: str) -> list[str]:
        """DEPRECATED: keyed on slot key. Prefer ``critical_for_slot_type``.

        Returns connector keys that are required in `mode` per the legacy
        ``required_in`` field. After Plan 5 Task 6 rewrote connectors.yaml
        to use ``required_in_types`` exclusively, this returns an empty list
        for the seed YAML — only overlays still using the old shape can
        produce non-empty results here.
        """
        return [key for key, c in self._connectors.items() if c.required_in_mode(mode)]

    def critical_for_slot_type(self, slot_type: SlotType) -> list[str]:
        """Connector keys that are required for any slot of the given type."""
        return [key for key, c in self._connectors.items() if c.required_in_type(slot_type)]


def load_registry(data_dir: Path | None = None) -> ConnectorRegistry:
    """Load seed connectors.yaml from the package; layer optional vault overlay on top.

    Overlay path: `<data_dir>/.scout-state/connectors.local.yaml`. v0.4 ships
    no writer for the overlay; respecting it keeps v0.8's discover/enable
    flow a small additive change.
    """
    seed_path = Path(__file__).parent / "connectors.yaml"
    seed = _load_yaml(seed_path)

    merged = dict(seed.get("connectors", {}))
    overlay_data_dir = data_dir if data_dir is not None else paths.data_dir()
    overlay_path = overlay_data_dir / ".scout-state" / "connectors.local.yaml"
    if overlay_path.exists():
        overlay = _load_yaml(overlay_path)
        for key, override in overlay.get("connectors", {}).items():
            if key in merged:
                merged[key] = _deep_merge_dict(merged[key], override)
            else:
                merged[key] = override

    connectors: dict[str, Connector] = {}
    for key, raw in merged.items():
        connectors[key] = _build_connector(key, raw)
    return ConnectorRegistry(connectors)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"connectors yaml at {path} is malformed: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"connectors yaml at {path} is not a mapping")
    return data


def _deep_merge_dict(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge with one level of nested-dict merging for `remediation`."""
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def _build_connector(key: str, raw: dict[str, Any]) -> Connector:
    try:
        tier = Tier(raw.get("tier", "official"))
        capabilities = tuple(Capability(c) for c in raw.get("capabilities", []))
        required_in_raw = raw.get("required_in", [])
        required_in: tuple[str, ...] | str
        if required_in_raw == "all":
            required_in = "all"
        else:
            required_in = tuple(required_in_raw)
        rit_raw = raw.get("required_in_types")
        if rit_raw is None:
            required_in_types: tuple[SlotType, ...] = ()
        else:
            required_in_types = tuple(SlotType(t) for t in rit_raw)
        rem_raw = raw.get("remediation", {})
        remediation = Remediation(
            first_fix=rem_raw.get("first_fix", ""),
            detail=rem_raw.get("detail", ""),
        )
        return Connector(
            key=key,
            display_name=raw["display_name"],
            tier=tier,
            capabilities=capabilities,
            required_in=required_in,
            required_in_types=required_in_types,
            remediation=remediation,
            notes=raw.get("notes", "") or "",
        )
    except (KeyError, ValueError) as e:
        raise ConfigError(f"connector {key} entry is malformed: {e}") from e
