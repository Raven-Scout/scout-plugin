"""Connector key-namespace invariant (#172, connector-catalog spec §7).

`connectors.enabled` in scout-config.yaml is populated from the keys of
templates/connector-probes.yaml, and phase sections are gated on their
`requires:` frontmatter matching one of those keys exactly. A phase whose
`requires:` does not resolve to a probe key is *silently un-enableable*:
select_sections drops its sections with no error, no warning — just missing
brain-file content. That is exactly what happened to the mail connector
(probe key `gmail` vs phase key `email` dropped every email section from
assembled SKILL.md), and this suite exists so the mismatch class fails CI
instead of shipping.
"""

from __future__ import annotations

from pathlib import Path

from scout.scripts.bootstrap import BootstrapConfig
from scout.scripts.connector_probes import (
    CONNECTOR_KEY_ALIASES,
    load_registry,
    normalize_connector_keys,
)
from scout.scripts.phase_assembly import parse_phase_file, select_sections

REPO_ROOT = Path(__file__).parent.parent.parent.parent
PHASES_ROOT = REPO_ROOT / "phases"
SHIPPED_PROBES = REPO_ROOT / "templates" / "connector-probes.yaml"


def _shipped_probe_keys() -> set[str]:
    return set(load_registry(SHIPPED_PROBES))


def _phase_requires() -> dict[str, set[Path]]:
    """Every non-null `requires:` key in shipped phases → the files using it."""
    out: dict[str, set[Path]] = {}
    for pf in sorted(PHASES_ROOT.rglob("*.md")):
        for section in parse_phase_file(pf):
            if section.requires is not None:
                out.setdefault(section.requires, set()).add(pf.relative_to(PHASES_ROOT))
    return out


def test_every_phase_requires_resolves_to_a_shipped_probe_key():
    """A `requires:` key with no probe entry can never be enabled by /scout-setup."""
    probe_keys = _shipped_probe_keys()
    unresolved = {key: sorted(map(str, files)) for key, files in _phase_requires().items() if key not in probe_keys}
    assert not unresolved, (
        f"Phase `requires:` keys that resolve to no connector-probes.yaml entry (silently un-enableable): {unresolved}"
    )


def test_phases_use_canonical_keys_not_aliases():
    """Aliases exist only to migrate legacy configs; phases must gate on canonical keys."""
    aliased = set(_phase_requires()) & set(CONNECTOR_KEY_ALIASES)
    assert not aliased, f"Phase `requires:` uses legacy alias keys: {sorted(aliased)}"


def test_aliases_map_onto_shipped_probe_keys_without_colliding():
    probe_keys = _shipped_probe_keys()
    for legacy, canonical in CONNECTOR_KEY_ALIASES.items():
        assert canonical in probe_keys, f"alias {legacy!r} → {canonical!r} which is not a shipped probe key"
        assert legacy not in probe_keys, f"alias {legacy!r} is still also a shipped probe key (ambiguous)"


def test_normalize_maps_legacy_gmail_and_is_idempotent():
    assert normalize_connector_keys({"gmail", "slack"}) == {"email", "slack"}
    assert normalize_connector_keys({"email", "slack"}) == {"email", "slack"}
    assert normalize_connector_keys(set()) == set()
    # Unknown keys (e.g. from a user probe overlay) pass through untouched.
    assert normalize_connector_keys({"devin"}) == {"devin"}


def test_legacy_gmail_config_selects_the_real_email_phase():
    """The #172 regression, end to end on the shipped email phase.

    A pre-rename vault has `gmail` in connectors.enabled. Un-normalized, that
    key matches nothing in phases/connectors/email.md and the whole phase is
    dropped; normalized, every section survives selection.
    """
    sections = parse_phase_file(PHASES_ROOT / "connectors" / "email.md")
    assert sections, "email phase parsed to zero sections"

    dropped = select_sections(sections, enabled_connectors={"gmail"})
    assert dropped == [], "raw legacy key should not match — otherwise the alias is redundant"

    kept = select_sections(sections, enabled_connectors=normalize_connector_keys({"gmail"}))
    assert len(kept) == len(sections)


def test_bootstrap_config_normalizes_enabled_connectors(tmp_path):
    """Every bootstrap entrypoint funnels through BootstrapConfig, so
    normalization there migrates legacy configs on the next /scout-update."""
    cfg = BootstrapConfig(
        vault=tmp_path,
        plugin_root=REPO_ROOT,
        instance_name="Scout",
        instance_name_lower="scout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.0.0",
        enabled_connectors={"gmail", "slack"},
        connector_inputs={},
    )
    assert cfg.enabled_connectors == {"email", "slack"}
