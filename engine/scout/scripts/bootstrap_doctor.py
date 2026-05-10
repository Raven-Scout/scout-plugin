"""Read-only health check for the bootstrap pipeline.

Used as pipeline stage 8 (post-install/upgrade smoke) and as a standalone
diagnostic via `scoutctl bootstrap doctor`. Never mutates the vault.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class Severity(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True)
class DoctorReport:
    severity: Severity
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {Severity.GREEN: 0, Severity.YELLOW: 1, Severity.RED: 2}[self.severity]


_REQUIRED_CAT1_FILES = (
    "scripts/heartbeat.sh",
    "knowledge-base/ontology/parser.py",
    "action-items/render.py",
    "hooks/kb-pre-filter.sh",
)


def run_doctor(*, vault: Path, check_jobs: bool = True) -> DoctorReport:
    """Run all doctor checks against ``vault``. Pure read."""
    errors: list[str] = []
    warnings: list[str] = []

    if not vault.exists():
        errors.append(f"vault directory missing: {vault}")
        return DoctorReport(severity=Severity.RED, errors=errors)

    # schedule.yaml must exist and parse.
    schedule_path = vault / ".scout-state" / "schedule.yaml"
    if not schedule_path.exists():
        errors.append(f"missing schedule.yaml at {schedule_path}")
    else:
        try:
            yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"schedule.yaml invalid: {e}")

    # scout-config.yaml must record version stamps.
    config_path = vault / "scout-config.yaml"
    if not config_path.exists():
        errors.append(f"missing scout-config.yaml at {config_path}")
    else:
        try:
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            plugin = cfg.get("plugin") or {}
            if not plugin.get("version_at_last_setup"):
                errors.append("scout-config.yaml: plugin.version_at_last_setup missing")
            if not plugin.get("version_at_last_update"):
                errors.append("scout-config.yaml: plugin.version_at_last_update missing")
        except yaml.YAMLError as e:
            errors.append(f"scout-config.yaml invalid: {e}")

    # Cat-1 files must exist with non-zero content.
    for rel in _REQUIRED_CAT1_FILES:
        path = vault / rel
        if not path.exists():
            errors.append(f"cat-1 file missing: {rel}")
        elif path.stat().st_size == 0:
            errors.append(f"cat-1 file empty: {rel}")

    # Snapshots present?
    snapshot_dir = vault / ".scout-state" / "last-assembled"
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        snap = snapshot_dir / f"{name}.md"
        if not snap.exists():
            warnings.append(f"snapshot missing: {snap.relative_to(vault)}")

    # Sidecar conflict files (yellow).
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        sidecar = vault / f"{name}.md.proposed-merge"
        if sidecar.exists():
            warnings.append(
                f"unresolved merge conflict in {sidecar.name} — resolve and "
                f"`mv {sidecar.name} {name}.md` before re-running /scout-update"
            )

    # Hand-edit backups (yellow but informational).
    for bak in vault.glob("run-*.sh.bak.*"):
        warnings.append(f"runner backup present: {bak.name} (hand-edit detected on prior update)")

    # Live launchd jobs.
    if check_jobs and os.name == "posix":
        try:
            proc = subprocess.run(
                ["launchctl", "list"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            if "com.scout.schedule-tick" not in proc.stdout:
                errors.append("launchd: com.scout.schedule-tick not registered")
            if "com.scout.heartbeat" not in proc.stdout:
                errors.append("launchd: com.scout.heartbeat not registered")
        except (subprocess.SubprocessError, FileNotFoundError):
            warnings.append("launchctl unavailable — skipped job registration check")

    if errors:
        return DoctorReport(severity=Severity.RED, errors=errors, warnings=warnings)
    if warnings:
        return DoctorReport(severity=Severity.YELLOW, warnings=warnings)
    return DoctorReport(severity=Severity.GREEN)
