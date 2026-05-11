"""Bootstrap pipeline — install/upgrade orchestrator for /scout-setup and /scout-update.

8 stages, behavior varies by command:
1. Pre-flight       — vault state checks, lock acquisition
2. Schema migrations — empty in 0.4.0
3. Cat 1 file writes — plists, ontology, render.py, scripts, hooks
4. Cat 1b runner writes — with hand-edit detection (upgrade only)
5. Cat 4 assembled  — SKILL/DREAMING/RESEARCH (3-way merge on upgrade)
6. Job lifecycle    — launchd / cron
7. Version stamp    — scout-config.yaml plugin.version_*
8. Doctor smoke     — runs bootstrap_doctor.run_doctor

See docs/superpowers/specs/2026-05-09-plan-8-scout-setup-repair-design.md.
"""

from __future__ import annotations

import datetime as _dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scout.scripts.bootstrap_doctor import DoctorReport, run_doctor
from scout.scripts.bootstrap_lock import (
    acquire_lock_with_wait,
    release_lock,
)
from scout.scripts.phase_assembly import (
    parse_phase_file,
    render_template,
    select_sections,
)
from scout.scripts.three_way_merge import three_way_merge


@dataclass
class BootstrapConfig:
    vault: Path
    plugin_root: Path
    instance_name: str
    instance_name_lower: str
    user_name: str
    user_email: str
    timezone: str
    platform: str  # "macos" | "linux"
    plugin_version: str
    enabled_connectors: set[str]
    connector_inputs: dict[str, str]
    skip_jobs: bool = False
    skip_claude: bool = False


@dataclass
class InstallResult:
    vault: Path
    doctor: DoctorReport


@dataclass
class UpgradeResult:
    vault: Path
    doctor: DoctorReport
    conflicts: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)


@dataclass
class MigrateLegacyResult:
    vault: Path
    doctor: DoctorReport
    backups: list[str] = field(default_factory=list)
    snapshots_recorded: list[str] = field(default_factory=list)


# ---------- shared helpers ----------

_CAT1_DIR_LAYOUT = (
    "knowledge-base/projects",
    "knowledge-base/ontology/entities",
    "knowledge-base/people",
    "knowledge-base/personal",
    "action-items/archive",
    "action-items/meeting-prep",
    "docs",
    "scripts",
    "hooks",
    ".scout-logs",
    ".scout-cache",
    ".scout-state/last-assembled",
)

_CAT1_FILES_FROM_PLUGIN = {
    "knowledge-base/ontology/parser.py": "templates/knowledge-base/ontology/parser.py",
    "knowledge-base/ontology/__init__.py": "templates/knowledge-base/ontology/__init__.py",
    "action-items/render.py": "templates/action-items/render.py",
}

_CAT1_TEMPLATES = (
    ("scripts/budget-check.sh", "templates/scripts/budget-check.sh.tmpl"),
    ("scripts/heartbeat.sh", "templates/scripts/heartbeat.sh.tmpl"),
    ("scripts/pre-session-data.sh", "templates/scripts/pre-session-data.sh.tmpl"),
    ("scripts/cc-session-cache.sh", "templates/scripts/cc-session-cache.sh.tmpl"),
    ("scripts/write-session-cost.sh", "templates/scripts/write-session-cost.sh.tmpl"),
    ("scripts/rate-limit-detect.sh", "templates/scripts/rate-limit-detect.sh.tmpl"),
    ("hooks/kb-pre-filter.sh", "templates/hooks/kb-pre-filter.sh.tmpl"),
    (".gitignore", "templates/.gitignore.tmpl"),
)

_INSTALL_ONLY_TEMPLATES = (
    # Vault-owned files seeded once on install (cat 2). Never overwritten on upgrade.
    ("dreaming-proposals.md", "templates/dreaming-proposals.md.tmpl"),
    ("knowledge-base/scout-mistake-audit.md", "templates/scout-mistake-audit.md.tmpl"),
    ("knowledge-base/review-queue.md", "templates/review-queue.md.tmpl"),
)

_CAT1B_RUNNERS = (
    ("run-scout.sh", "templates/run-scout.sh.tmpl"),
    ("run-dreaming.sh", "templates/run-dreaming.sh.tmpl"),
    ("run-research.sh", "templates/run-research.sh.tmpl"),
)


def _template_vars(cfg: BootstrapConfig) -> dict[str, str]:
    return {
        "INSTANCE_NAME": cfg.instance_name,
        "INSTANCE_NAME_LOWER": cfg.instance_name_lower,
        "USER_NAME": cfg.user_name,
        "USER_EMAIL": cfg.user_email,
        "USER_SLACK_ID": cfg.connector_inputs.get("user_slack_id", ""),
        "GITHUB_USERNAME": cfg.connector_inputs.get("github_username", ""),
        "GITHUB_REPOS": cfg.connector_inputs.get("github_repos", ""),
        "SCOUT_DIR": str(cfg.vault),
        "TIMEZONE": cfg.timezone,
        "PLATFORM": cfg.platform,
        "MAX_BUDGET": cfg.connector_inputs.get("max_budget", "5.00"),
        "CLAUDE_BIN": cfg.connector_inputs.get("claude_bin", "/usr/local/bin/claude"),
        "TODAY_DATE": _dt.date.today().isoformat(),
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ---------- stages ----------

def _stage_create_dirs(cfg: BootstrapConfig) -> None:
    for rel in _CAT1_DIR_LAYOUT:
        (cfg.vault / rel).mkdir(parents=True, exist_ok=True)


def _stage_cat1_writes(cfg: BootstrapConfig) -> None:
    """Stage 3: cat 1 file overwrites (always)."""
    vars_ = _template_vars(cfg)
    for vault_rel, plugin_rel in _CAT1_FILES_FROM_PLUGIN.items():
        src = cfg.plugin_root / plugin_rel
        if not src.exists():
            _atomic_write(cfg.vault / vault_rel, f"# placeholder: {plugin_rel}\n")
            continue
        _atomic_write(cfg.vault / vault_rel, src.read_text(encoding="utf-8"))
    for vault_rel, tmpl_rel in _CAT1_TEMPLATES:
        src = cfg.plugin_root / tmpl_rel
        if not src.exists():
            _atomic_write(cfg.vault / vault_rel, f"# placeholder: {tmpl_rel}\n")
            continue
        rendered = render_template(src.read_text(encoding="utf-8"), vars_)
        _atomic_write(cfg.vault / vault_rel, rendered)
        (cfg.vault / vault_rel).chmod(0o755)


def _stage_install_only_seeds(cfg: BootstrapConfig) -> None:
    """Seed cat-2 vault-owned files on install only (never overwritten)."""
    vars_ = _template_vars(cfg)
    for vault_rel, tmpl_rel in _INSTALL_ONLY_TEMPLATES:
        target = cfg.vault / vault_rel
        if target.exists():
            continue  # never overwrite
        src = cfg.plugin_root / tmpl_rel
        if not src.exists():
            continue
        rendered = render_template(src.read_text(encoding="utf-8"), vars_)
        _atomic_write(target, rendered)


def _stage_cat1b_runners(cfg: BootstrapConfig, *, is_upgrade: bool) -> list[str]:
    """Stage 4: cat 1b runner writes."""
    vars_ = _template_vars(cfg)
    backups: list[str] = []
    for vault_rel, tmpl_rel in _CAT1B_RUNNERS:
        src = cfg.plugin_root / tmpl_rel
        target = cfg.vault / vault_rel
        if not src.exists():
            continue
        rendered = render_template(src.read_text(encoding="utf-8"), vars_)
        if is_upgrade and target.exists():
            current = target.read_text(encoding="utf-8")
            if current != rendered:
                today = _dt.date.today().isoformat()
                bak = cfg.vault / f"{vault_rel}.bak.{today}"
                # Overwrites same-day backup if present — only the most recent
                # hand-edit-vs-template divergence is preserved per day.
                shutil.copy2(target, bak)
                backups.append(bak.name)
        _atomic_write(target, rendered)
        target.chmod(0o755)
    return backups


def _assemble(cfg: BootstrapConfig, kind: str) -> str:
    """Assemble SKILL/DREAMING/RESEARCH from phase files."""
    vars_ = _template_vars(cfg)
    phases_root = cfg.plugin_root / "phases"
    bodies: list[str] = [f"# {kind}\n\n**BASE_DIR:** `{cfg.vault}`\n"]
    if kind == "SKILL":
        sources = [phases_root / "core", phases_root / "connectors"]
    elif kind == "DREAMING":
        sources = [phases_root / "core", phases_root / "modes"]
    else:  # RESEARCH
        sources = [phases_root / "core", phases_root / "research"]
    for src_dir in sources:
        if not src_dir.exists():
            continue
        for phase_file in sorted(src_dir.glob("*.md")):
            try:
                sections = parse_phase_file(phase_file)
            except (ValueError, yaml.YAMLError):
                # Phase file failed to parse — skip rather than abort the assembly.
                # Known limitation: phase_assembly.parse_phase_file is fooled by bare
                # '---' horizontal rules in markdown bodies (e.g., kb-management.md).
                # Tracked as a Plan 8 followup to harden A5's parser.
                continue
            kept = select_sections(sections, enabled_connectors=cfg.enabled_connectors)
            for s in kept:
                bodies.append(render_template(s.body, vars_))
    return "\n\n".join(bodies)


def _stage_cat4_install(cfg: BootstrapConfig) -> None:
    """Stage 5 (install): assemble + write live + write snapshot."""
    snapshot_dir = cfg.vault / ".scout-state" / "last-assembled"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        content = _assemble(cfg, kind)
        _atomic_write(cfg.vault / f"{kind}.md", content)
        _atomic_write(snapshot_dir / f"{kind}.md", content)


def _stage_cat4_upgrade(cfg: BootstrapConfig) -> list[str]:
    """Stage 5 (upgrade): 3-way merge with sidecar policy."""
    snapshot_dir = cfg.vault / ".scout-state" / "last-assembled"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    conflicts: list[str] = []
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        ours = _assemble(cfg, kind)
        live = cfg.vault / f"{kind}.md"
        theirs = live.read_text(encoding="utf-8") if live.exists() else ours
        snap = snapshot_dir / f"{kind}.md"
        base = snap.read_text(encoding="utf-8") if snap.exists() else theirs
        result = three_way_merge(base=base, ours=ours, theirs=theirs)
        if not result.conflicts:
            _atomic_write(live, result.content)
            _atomic_write(snap, ours)
        else:
            sidecar = cfg.vault / f"{kind}.md.proposed-merge"
            _atomic_write(sidecar, result.content)
            conflicts.append(sidecar.name)
    return conflicts


def _stage_jobs_install(cfg: BootstrapConfig) -> None:
    """Stage 6: install schedule-tick + heartbeat (or cron block)."""
    if cfg.skip_jobs:
        return
    if cfg.platform == "macos":
        from scout.scripts.install_heartbeat_plist import install_plist as install_hb
        from scout.scripts.install_schedule_plist import install_plist as install_st

        install_st(home=Path.home(), force=True, bootstrap=True)
        install_hb(home=Path.home(), force=True, bootstrap=True)
    elif cfg.platform == "linux":
        from scout.scripts.install_cron import install_cron

        install_cron(home=Path.home())


def _stage_seed_schedule(cfg: BootstrapConfig) -> None:
    """Seed .scout-state/schedule.yaml from plugin defaults (install only)."""
    src = cfg.plugin_root / "engine" / "scout" / "defaults" / "schedule.yaml"
    target = cfg.vault / ".scout-state" / "schedule.yaml"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, target)
    else:
        target.write_text("schema_version: 1\nslots: {}\n", encoding="utf-8")


def _stage_version_stamp(cfg: BootstrapConfig, *, is_upgrade: bool) -> None:
    """Stage 7: write/update plugin.version_at_last_{setup,update}."""
    config_path = cfg.vault / "scout-config.yaml"
    if config_path.exists():
        existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        existing = {}
    existing.setdefault("user", {})
    existing["user"]["name"] = cfg.user_name
    existing["user"]["email"] = cfg.user_email
    existing["instance"] = {
        "name": cfg.instance_name,
        "name_lower": cfg.instance_name_lower,
    }
    plugin = existing.setdefault("plugin", {})
    if not is_upgrade:
        plugin["version_at_last_setup"] = cfg.plugin_version
    plugin["version_at_last_update"] = cfg.plugin_version
    plugin.setdefault("applied_migrations", [])
    _atomic_write(config_path, yaml.safe_dump(existing, sort_keys=False))


# ---------- entry points ----------

_VAULT_MARKERS = ("scout-config.yaml", ".scout-state")


def _vault_exists(vault: Path) -> bool:
    if not vault.exists():
        return False
    return any((vault / m).exists() for m in _VAULT_MARKERS)


def _refuse_pending_sidecars(vault: Path) -> None:
    pending = [
        f"{n}.md.proposed-merge"
        for n in ("SKILL", "DREAMING", "RESEARCH")
        if (vault / f"{n}.md.proposed-merge").exists()
    ]
    if pending:
        raise RuntimeError(
            f"Unresolved proposed-merge sidecar(s): {pending}. "
            f"Edit each to remove conflict markers, then "
            f"`mv X.md.proposed-merge X.md`, then re-run /scout-update."
        )


def install(cfg: BootstrapConfig) -> InstallResult:
    """Run the install pipeline. Stage 1 refuses if vault already exists."""
    if _vault_exists(cfg.vault):
        raise FileExistsError(
            f"vault detected at {cfg.vault} — run /scout-update instead, "
            f"or manually remove the vault first (see Plan 8 §4.6 reset snippet)."
        )
    cfg.vault.mkdir(parents=True, exist_ok=True)
    lock = cfg.vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock_with_wait(lock)
    try:
        _stage_create_dirs(cfg)
        _stage_cat1_writes(cfg)
        _stage_install_only_seeds(cfg)   # <-- NEW
        _stage_seed_schedule(cfg)
        _stage_cat1b_runners(cfg, is_upgrade=False)
        _stage_cat4_install(cfg)
        _stage_jobs_install(cfg)
        _stage_version_stamp(cfg, is_upgrade=False)
    finally:
        release_lock(lock)
    report = run_doctor(vault=cfg.vault, check_jobs=not cfg.skip_jobs)
    return InstallResult(vault=cfg.vault, doctor=report)


def _is_legacy_vault(vault: Path) -> bool:
    """Legacy: `.scout-state/` exists but `scout-config.yaml` doesn't.

    Indicates a Plan-5-era vault that pre-dates the Plan 8 config conventions.
    Such vaults need `scoutctl bootstrap migrate-legacy` before `upgrade` works.
    """
    return (vault / ".scout-state").exists() and not (vault / "scout-config.yaml").exists()


def upgrade(cfg: BootstrapConfig) -> UpgradeResult:
    """Run the upgrade pipeline. Refuses if no vault or if vault is legacy (pre-Plan-8)."""
    if not _vault_exists(cfg.vault):
        raise FileNotFoundError(
            f"no vault at {cfg.vault} — run /scout-setup instead."
        )
    if _is_legacy_vault(cfg.vault):
        raise RuntimeError(
            f"legacy vault detected at {cfg.vault} (no scout-config.yaml). "
            f"Run `scoutctl bootstrap migrate-legacy` first to establish a "
            f"Plan 8 baseline before running upgrade."
        )
    _refuse_pending_sidecars(cfg.vault)
    lock = cfg.vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock_with_wait(lock)
    try:
        _stage_cat1_writes(cfg)
        backups = _stage_cat1b_runners(cfg, is_upgrade=True)
        conflicts = _stage_cat4_upgrade(cfg)
        _stage_jobs_install(cfg)
        _stage_version_stamp(cfg, is_upgrade=True)
    finally:
        release_lock(lock)
    report = run_doctor(vault=cfg.vault, check_jobs=not cfg.skip_jobs)
    return UpgradeResult(
        vault=cfg.vault,
        doctor=report,
        conflicts=conflicts,
        backups=backups,
    )


def migrate_legacy(cfg: BootstrapConfig) -> MigrateLegacyResult:
    """One-time migration of a Plan-5-era vault to Plan 8 format.

    Required: cfg.vault must have ``.scout-state/`` but no ``scout-config.yaml``.

    Actions (in order):
      1. Acquire global lock.
      2. Snapshot current SKILL.md / DREAMING.md / RESEARCH.md to
         ``.scout-state/last-assembled/`` as the merge baseline. Live files
         never touched.
      3. Run cat-1 writes — overwrites plugin-owned scripts/hooks/plists with
         templates rendered against the user-provided cfg vars.
      4. Run cat-1b runner regen with hand-edit detection. Legacy runners
         (heavily customized) get backed up; fresh templates installed.
      5. Skip cat-4 merge entirely — snapshots just established, nothing to
         merge.
      6. Job lifecycle (subject to cfg.skip_jobs).
      7. Write version stamps to a fresh scout-config.yaml.
      8. Doctor.

    After this, the vault is Plan 8-compatible and `upgrade()` works normally.
    """
    if not (cfg.vault / ".scout-state").exists():
        raise FileNotFoundError(
            f"no vault at {cfg.vault} (no .scout-state/ directory) — "
            f"run /scout-setup for a fresh install."
        )
    if (cfg.vault / "scout-config.yaml").exists():
        raise FileExistsError(
            f"vault at {cfg.vault} is not a legacy vault (scout-config.yaml "
            f"exists). Use `scoutctl bootstrap upgrade` instead."
        )
    lock = cfg.vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock_with_wait(lock)
    snapshots_recorded: list[str] = []
    try:
        # 1. Establish snapshots from current live cat-4 files.
        snapshot_dir = cfg.vault / ".scout-state" / "last-assembled"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for kind in ("SKILL", "DREAMING", "RESEARCH"):
            live = cfg.vault / f"{kind}.md"
            if live.exists():
                content = live.read_text(encoding="utf-8")
                _atomic_write(snapshot_dir / f"{kind}.md", content)
                snapshots_recorded.append(f"{kind}.md")
        # 2. Seed .scout-state/schedule.yaml if missing. Legacy Plan-5-era
        #    vaults never explicitly wrote this file; the live dispatcher
        #    silently falls back to packaged defaults. Make the vault copy
        #    explicit so the doctor reports green and future schedule edits
        #    have a stable home.
        _stage_seed_schedule(cfg)
        # 3. cat-1 writes with the now-correct template vars.
        _stage_cat1_writes(cfg)
        # 4. cat-1b runner regen — backs up legacy runners.
        backups = _stage_cat1b_runners(cfg, is_upgrade=True)
        # 5. SKIP cat-4 merge: snapshots just established equal current live.
        # 6. Jobs.
        _stage_jobs_install(cfg)
        # 7. Version stamps (is_upgrade=False so both version_at_last_setup and
        #    version_at_last_update are written; setup marks "migrated at this
        #    plugin version", matching how a freshly-installed vault records it).
        _stage_version_stamp(cfg, is_upgrade=False)
    finally:
        release_lock(lock)
    report = run_doctor(vault=cfg.vault, check_jobs=not cfg.skip_jobs)
    return MigrateLegacyResult(
        vault=cfg.vault,
        doctor=report,
        backups=backups,
        snapshots_recorded=snapshots_recorded,
    )
