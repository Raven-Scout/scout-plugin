"""Read-only health check for the bootstrap pipeline.

Used as pipeline stage 8 (post-install/upgrade smoke) and as a standalone
diagnostic via `scoutctl bootstrap doctor`. Never mutates the vault.
"""

from __future__ import annotations

import os
import platform
import plistlib
import re
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

# macOS TCC-protected directories — launchd-spawned processes cannot read files
# inside these without Full Disk Access granted to the executable.
_MACOS_TCC_PROTECTED_DIRS = ("Documents", "Desktop", "Downloads")


def _check_macos_plist_scoutctl_bin(*, home: Path) -> tuple[list[str], list[str]]:
    """Inspect the installed schedule-tick plist's scoutctl path.

    Reports as error if the file doesn't exist or isn't executable. Reports
    as error if the resolved path falls under a macOS TCC-protected directory
    (launchd cannot read those without Full Disk Access). Returns empty if
    the plist isn't installed — that case is already covered by the
    launchctl-list check upstream.
    """
    errors: list[str] = []
    warnings: list[str] = []
    plist_path = home / "Library" / "LaunchAgents" / "com.scout.schedule-tick.plist"
    if not plist_path.exists():
        return errors, warnings
    try:
        with plist_path.open("rb") as f:
            data = plistlib.load(f)
    except (plistlib.InvalidFileException, OSError) as e:
        warnings.append(f"could not parse {plist_path.name}: {e}")
        return errors, warnings
    args = data.get("ProgramArguments") or []
    if not args:
        warnings.append(f"{plist_path.name}: ProgramArguments empty")
        return errors, warnings
    scoutctl_bin = Path(args[0])
    fix_hint = (
        "Fix: scoutctl schedule install-plist --force (re-derives the canonical path from the loaded plugin's venv)"
    )
    if not scoutctl_bin.exists():
        errors.append(f"plist references non-existent scoutctl: {scoutctl_bin}. {fix_hint}")
        return errors, warnings
    if not os.access(scoutctl_bin, os.X_OK):
        errors.append(f"plist references non-executable scoutctl: {scoutctl_bin}. {fix_hint}")
        return errors, warnings
    # TCC dir check: macOS sandbox blocks launchd-spawned processes from reading
    # files in protected dirs. We compare RESOLVED paths so a symlinked plugin
    # that lands in ~/Documents is still caught.
    resolved = scoutctl_bin.resolve()
    home_resolved = home.resolve()
    for protected in _MACOS_TCC_PROTECTED_DIRS:
        protected_path = home_resolved / protected
        try:
            resolved.relative_to(protected_path)
        except ValueError:
            continue
        errors.append(
            f"scoutctl resolves into ~/{protected} ({resolved}). macOS TCC blocks "
            f"launchd from reading this directory without Full Disk Access. Move the "
            f"plugin out of ~/{protected}, then re-install: "
            f"scoutctl schedule install-plist --force"
        )
        break
    return errors, warnings


def _check_linux_cron_scoutctl_bin() -> tuple[list[str], list[str]]:
    """Inspect the Linux scout-managed cron block for the scoutctl path."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        return errors, warnings
    if proc.returncode != 0:
        return errors, warnings
    in_block = False
    scoutctl_bin: Path | None = None
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if stripped == "# >>> scout-managed >>>":
            in_block = True
            continue
        if stripped == "# <<< scout-managed <<<":
            break
        if not in_block:
            continue
        if "scoutctl schedule tick" not in line:
            continue
        # Standard cron line: 5 time fields + command. Token at index 5 is the
        # executable path (assumes no whitespace in the path — same assumption
        # baked into install_cron's template). Defensive sanity check: the
        # token must look like a scoutctl path. If the user hand-edited the
        # managed block to use `@daily` shorthand (1 cron token instead of 5)
        # or otherwise broke the layout, parts[5] would point at the wrong
        # token — better to bail than to falsely accuse a different binary.
        parts = line.split()
        if len(parts) >= 6 and parts[5].endswith("/scoutctl"):
            scoutctl_bin = Path(parts[5])
        break
    if scoutctl_bin is None:
        return errors, warnings
    fix_hint = "Fix: scoutctl schedule install-cron (re-derives the canonical path from the loaded plugin's venv)"
    if not scoutctl_bin.exists():
        errors.append(f"crontab references non-existent scoutctl: {scoutctl_bin}. {fix_hint}")
    elif not os.access(scoutctl_bin, os.X_OK):
        errors.append(f"crontab references non-executable scoutctl: {scoutctl_bin}. {fix_hint}")
    return errors, warnings


def _check_scheduler_bin_path(*, home: Path) -> tuple[list[str], list[str]]:
    """Dispatch to the platform-specific scoutctl-bin-path check."""
    system = platform.system()
    if system == "Darwin":
        return _check_macos_plist_scoutctl_bin(home=home)
    if system == "Linux":
        return _check_linux_cron_scoutctl_bin()
    return [], []


def _check_scoutctl_shim(*, home: Path) -> tuple[list[str], list[str]]:
    """Warn (never error) if the managed scoutctl shim is dangling.

    The SKILL.md-driven session relies on the ~/.local/bin shim to resolve
    bare `scoutctl` (#99). The realistic post-install failure is a shim left
    pointing at a plugin venv that a later update removed. We flag only that
    dangling case — deterministically, from the shim's own contents — rather
    than guessing reachability from the ambient PATH (which differs between
    the doctor process and the session, and would make the result
    environment-dependent). A missing shim isn't flagged: install and upgrade
    always (re)write it, so absence resolves itself on the next run.
    """
    from scout.scripts.install_scoutctl_shim import SHIM_MARKER

    warnings: list[str] = []
    shim = home / ".local" / "bin" / "scoutctl"
    if not shim.is_file():
        return [], []
    try:
        body = shim.read_text(encoding="utf-8")
    except OSError:
        return [], []
    if SHIM_MARKER in body:
        m = re.search(r'exec "([^"]+)"', body)
        if m and not Path(m.group(1)).exists():
            warnings.append(
                f"scoutctl shim at {shim} points at a missing target ({m.group(1)}) — "
                f"re-run `scoutctl bootstrap upgrade`."
            )
    return [], warnings


def run_doctor(*, vault: Path, check_jobs: bool = True, home: Path | None = None) -> DoctorReport:
    """Run all doctor checks against ``vault``. Pure read."""
    errors: list[str] = []
    warnings: list[str] = []
    home = home or Path.home()

    if not vault.is_dir():
        if vault.exists():
            errors.append(f"vault path is not a directory: {vault}")
        else:
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

    # Scheduler scoutctl path — verify the path baked into the installed plist
    # (macOS) or cron block (Linux) actually points to an executable scoutctl
    # and isn't trapped in a TCC-protected directory.
    if check_jobs:
        bin_errors, bin_warnings = _check_scheduler_bin_path(home=home)
        errors.extend(bin_errors)
        warnings.extend(bin_warnings)
        # Interactive/session scoutctl reachability (separate from the plist).
        _, shim_warnings = _check_scoutctl_shim(home=home)
        warnings.extend(shim_warnings)

    if errors:
        return DoctorReport(severity=Severity.RED, errors=errors, warnings=warnings)
    if warnings:
        return DoctorReport(severity=Severity.YELLOW, warnings=warnings)
    return DoctorReport(severity=Severity.GREEN)
