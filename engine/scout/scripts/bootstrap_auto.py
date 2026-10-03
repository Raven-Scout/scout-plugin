"""``scoutctl bootstrap auto`` — detect vault state, dispatch, report (spec E3, #26).

The state → action table is the one in docs/specs/scoutctl-bootstrap-auto.md.
This module is the machine-readable face of the bootstrap pipeline: Scout.app
decodes ``result_dict`` and never parses the human text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from scout.scripts.bootstrap import (
    BootstrapConfig,
    InstallResult,
    MigrateLegacyResult,
    UpgradeResult,
    _is_legacy_vault,
    _vault_exists,
    blocking_sidecars,
    install,
    install_incomplete,
    migrate_legacy,
    upgrade,
)
from scout.scripts.bootstrap_doctor import DoctorReport
from scout.scripts.bootstrap_lock import LockBusyError

RESULT_SCHEMA_VERSION = 1

# Finder leaves these behind just from opening/viewing a folder — they don't
# count as user content when deciding whether a vault directory is "empty".
_FINDER_METADATA = frozenset({".DS_Store", ".localized"})


class AutoAction(Enum):
    INSTALL = "install"
    UPGRADE = "upgrade"
    MIGRATE_LEGACY = "migrate-legacy"
    REFUSED = "refused"


@dataclass(frozen=True)
class Plan:
    action: AutoAction
    reason: str


def _is_effectively_empty(vault: Path) -> bool:
    """True iff every entry in `vault` is Finder-authored metadata.

    A `~/Scout` folder that Finder has merely opened (never anything a user
    put there) leaves a `.DS_Store` behind; that alone shouldn't make
    `detect()` treat the directory as occupied and refuse to install. Any
    other entry — dotfiles included, e.g. `.git` — is real content.
    """
    return all(p.name in _FINDER_METADATA for p in vault.iterdir())


def detect(vault: Path) -> Plan:
    if not vault.exists() or (vault.is_dir() and _is_effectively_empty(vault)):
        return Plan(AutoAction.INSTALL, "no vault: directory missing or empty")
    if not vault.is_dir():
        return Plan(AutoAction.REFUSED, f"{vault} exists and is not a directory")
    # Only these make `upgrade` refuse; a pending brain-file sidecar just
    # skips that file and is reported as `skipped`.
    sidecars = blocking_sidecars(vault)
    if sidecars:
        return Plan(
            AutoAction.REFUSED,
            f"unresolved proposed-merge sidecar(s): {sidecars} — edit each, `mv X.proposed-merge X`, then re-run",
        )
    if install_incomplete(vault):
        return Plan(AutoAction.INSTALL, "resuming an interrupted install")
    if _is_legacy_vault(vault):
        return Plan(AutoAction.MIGRATE_LEGACY, ".scout-state/ present without scout-config.yaml (pre-Plan-8 vault)")
    if _vault_exists(vault):
        return Plan(AutoAction.UPGRADE, "scout-config.yaml present")
    return Plan(
        AutoAction.REFUSED,
        f"{vault} is non-empty but is not a Scout vault (no scout-config.yaml or .scout-state/) — pick an empty folder",
    )


def doctor_dict(report: DoctorReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    # getattr-defensive: some CLI-surface tests monkeypatch install/upgrade/
    # migrate_legacy with lightweight doctor stand-ins that only set the
    # fields their (pre-E3) text-mode branch actually read — e.g. upgrade's
    # never printed warnings/errors, so its test doubles omit them.
    return {
        "severity": report.severity.value,
        "errors": list(getattr(report, "errors", None) or []),
        "warnings": list(getattr(report, "warnings", None) or []),
        "notes": list(getattr(report, "notes", None) or []),
    }


def result_dict(
    *,
    action: AutoAction,
    vault: Path,
    plugin_version: str,
    result: InstallResult | UpgradeResult | MigrateLegacyResult | None,
    error: str | None = None,
    dry_run: bool = False,
    reason: str = "",
    mutated: bool = False,
) -> dict[str, Any]:
    """The BootstrapResult JSON. ``mutated`` is only meaningful on a refusal:
    True when the run failed partway through dispatch, so the vault may have
    been modified; False when it was refused before any stage ran."""
    pointer = getattr(result, "pointer", None)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "action": action.value,
        "reason": reason,
        "dry_run": dry_run,
        "vault": str(vault),
        "plugin_version": plugin_version,
        "error": error,
        "doctor": doctor_dict(getattr(result, "doctor", None)),
        "conflicts": list(getattr(result, "conflicts", None) or []),
        "backups": list(getattr(result, "backups", None) or []),
        "snapshots_recorded": list(getattr(result, "snapshots_recorded", None) or []),
        "skipped": list(getattr(result, "skipped", None) or []),
        "vault_edits": [
            {
                "path": e.path,
                "outcome": e.outcome,
                "parked": list(e.parked),
                "detail": e.detail,
                "message": e.describe(),
            }
            for e in getattr(result, "vault_edits", None) or []
        ],
        "pointer": str(pointer) if pointer else None,
        "mutated": mutated,
    }


def run(cfg: BootstrapConfig, *, plan: Plan | None = None, dry_run: bool = False) -> tuple[dict[str, Any], int]:
    """Dispatch ``plan`` (detected here when None) and return ``(result_dict, exit_code)``.

    Exit codes: the doctor's 0/1/2 after a run; 2 when refused; 0 for dry-run.
    FileExistsError / FileNotFoundError / LockBusyError are the entrypoints'
    pre-flight refusals (vault state, lock contention), raised before any
    stage runs, so they report ``mutated: false``; a RuntimeError or any other
    OSError is treated as a stage failing partway, so ``mutated: true``. Any
    other exception type propagates (exit 70).
    """
    if plan is None:
        plan = detect(cfg.vault)
    common: dict[str, Any] = {"vault": cfg.vault, "plugin_version": cfg.plugin_version, "reason": plan.reason}
    if dry_run:
        return result_dict(action=plan.action, result=None, dry_run=True, **common), 0
    if plan.action is AutoAction.REFUSED:
        return result_dict(action=plan.action, result=None, error=plan.reason, **common), 2
    try:
        if plan.action is AutoAction.INSTALL:
            res: InstallResult | UpgradeResult | MigrateLegacyResult = install(cfg)
        elif plan.action is AutoAction.MIGRATE_LEGACY:
            res = migrate_legacy(cfg)
        else:
            res = upgrade(cfg)
    except (FileExistsError, FileNotFoundError, LockBusyError) as e:
        # Before RuntimeError/OSError: FileExistsError and FileNotFoundError
        # are OSError subclasses.
        return result_dict(action=AutoAction.REFUSED, result=None, error=str(e), **common), 2
    except (RuntimeError, OSError) as e:
        return result_dict(action=AutoAction.REFUSED, result=None, error=str(e), mutated=True, **common), 2
    return result_dict(action=plan.action, result=res, **common), res.doctor.exit_code
