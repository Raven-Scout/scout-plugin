"""Bootstrap pipeline — install/upgrade orchestrator for /scout-setup and /scout-update.

8 stages, behavior varies by command:
1. Pre-flight       — vault state checks, lock acquisition
2. Schema migrations — empty in 0.4.0
3. Managed files    — plugin-owned scripts, hooks, runners, render.py, parser.py;
                      a vault's edits are kept, merged or parked (vault_drift)
4. .gitignore       — append-only merge
5. Cat 4 assembled  — SKILL/DREAMING/RESEARCH (3-way merge on upgrade; see brain_merge)
6. Job lifecycle    — launchd / cron
7. Version stamp    — scout-config.yaml plugin.version_*
8. Doctor smoke     — runs bootstrap_doctor.run_doctor

See docs/superpowers/specs/2026-05-09-plan-8-scout-setup-repair-design.md.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scout import config as scout_config
from scout.scripts import brain_merge, vault_drift
from scout.scripts.bootstrap_doctor import DoctorReport, run_doctor
from scout.scripts.bootstrap_lock import (
    acquire_lock_with_wait,
    release_lock,
)
from scout.scripts.connector_probes import normalize_connector_keys
from scout.scripts.install_schedule_plist import resolve_scoutctl_bin
from scout.scripts.migrate_perfile import migrate_perfile
from scout.scripts.phase_assembly import (
    parse_phase_file,
    render_template,
    select_sections,
)
from scout.scripts.three_way_merge import three_way_merge
from scout.scripts.vault_drift import VaultEdit


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
    managed_by: str = "unknown"
    # Install-time auto-update preference from /scout-setup. None = not asked:
    # leave scout-config.yaml's auto_update block untouched.
    auto_update: bool | None = None

    def __post_init__(self) -> None:
        # Vaults configured before a probe-key rename (gmail → email) carry the
        # legacy key in connectors.enabled; normalizing at construction covers
        # every entrypoint (install / upgrade / migrate-legacy / backport) and
        # lets upgrade persist the canonical key back into scout-config.yaml.
        self.enabled_connectors = normalize_connector_keys(self.enabled_connectors)


@dataclass
class InstallResult:
    vault: Path
    doctor: DoctorReport
    pointer: Path | None = None


@dataclass
class UpgradeResult:
    vault: Path
    doctor: DoctorReport
    # <file>.proposed-merge sidecars this upgrade wrote.
    conflicts: list[str] = field(default_factory=list)
    # Vault copies an upgrade replaced, parked under .scout-state/drift/.
    backups: list[str] = field(default_factory=list)
    # Brain-file sidecars already pending: the upgrade left that file alone.
    skipped: list[str] = field(default_factory=list)
    # Every vault edit to a managed file the upgrade kept, merged, or parked.
    vault_edits: list[VaultEdit] = field(default_factory=list)
    pointer: Path | None = None


@dataclass
class MigrateLegacyResult:
    vault: Path
    doctor: DoctorReport
    backups: list[str] = field(default_factory=list)
    snapshots_recorded: list[str] = field(default_factory=list)
    vault_edits: list[VaultEdit] = field(default_factory=list)
    pointer: Path | None = None


# ---------- shared helpers ----------

_CAT1_DIR_LAYOUT = (
    "knowledge-base/projects",
    "knowledge-base/ontology/entities",
    "knowledge-base/people",
    "knowledge-base/personal",
    "knowledge-base/recurring-tasks",
    "action-items/archive",
    "action-items/meeting-prep",
    "meetings",
    "docs",
    "scripts",
    "hooks",
    ".scout-logs",
    ".scout-cache",
    ".scout-state/last-assembled",
)

_CAT1_FILES_FROM_PLUGIN = {
    "knowledge-base/ontology/__init__.py": "templates/knowledge-base/ontology/__init__.py",
    "action-items/render.py": "templates/action-items/render.py",
    "scripts/recurring-task-status.py": "templates/scripts/recurring-task-status.py",
    # Optional helpers the plugin-owned scripts call, guarded: the git-truth
    # staleness ranking (hooks/kb-pre-filter.sh) and the session-lane watchdog
    # (scripts/heartbeat.sh). Standard-library Python, run with python3.
    "scripts/vault-freshness.py": "templates/scripts/vault-freshness.py",
    "scripts/session-lane-liveness.py": "templates/scripts/session-lane-liveness.py",
}

# Plugin-owned files the vault is known to extend, not just patch: parser.py is
# grown by dreaming sessions in the vault (Pattern #68). They are managed files
# like the rest, with one difference: when there is no recorded base to merge
# against, the vault's version stays live and the plugin's is parked. Engines
# before last-rendered/ merged these into a blocking `<file>.proposed-merge`
# sidecar; one an older engine left behind still blocks the upgrade
# (_refuse_pending_sidecars), so the name stays.
_CAT_MERGE_FILES = {
    "knowledge-base/ontology/parser.py": "templates/knowledge-base/ontology/parser.py",
}

_CAT1_TEMPLATES = (
    # scout-tz.sh first: it is the runtime timezone resolver every other script
    # (and the assembled brain files) call via TZ="$(scripts/scout-tz.sh)".
    ("scripts/scout-tz.sh", "templates/scripts/scout-tz.sh.tmpl"),
    ("scripts/budget-check.sh", "templates/scripts/budget-check.sh.tmpl"),
    ("scripts/heartbeat.sh", "templates/scripts/heartbeat.sh.tmpl"),
    ("scripts/pre-session-data.sh", "templates/scripts/pre-session-data.sh.tmpl"),
    ("scripts/cc-session-cache.sh", "templates/scripts/cc-session-cache.sh.tmpl"),
    ("scripts/write-session-cost.sh", "templates/scripts/write-session-cost.sh.tmpl"),
    ("scripts/rate-limit-detect.sh", "templates/scripts/rate-limit-detect.sh.tmpl"),
    ("scripts/claude-with-retry.sh", "templates/scripts/claude-with-retry.sh.tmpl"),
    ("scripts/post-session-backfill.sh", "templates/scripts/post-session-backfill.sh.tmpl"),
    ("scripts/materialize-daily-file.sh", "templates/scripts/materialize-daily-file.sh.tmpl"),
    # The runners' out-of-band record of how each run ended (run-outcomes.jsonl).
    ("scripts/run-outcome.sh", "templates/scripts/run-outcome.sh.tmpl"),
    ("hooks/kb-pre-filter.sh", "templates/hooks/kb-pre-filter.sh.tmpl"),
    (".gitignore", "templates/.gitignore.tmpl"),
)

# Cat-1 templates the vault also edits, so an upgrade merges them into the live
# file instead of overwriting it (see merge_gitignore). A vault's .gitignore can
# carry lines that keep secrets out of git; the sessions auto-commit the vault,
# so dropping one of those lines on upgrade can commit a live credential.
_CAT1_APPEND_ONLY = frozenset({".gitignore"})

_INSTALL_ONLY_TEMPLATES = (
    # Vault-owned files seeded once on install (cat 2). Never overwritten on upgrade.
    ("dreaming-proposals.md", "templates/dreaming-proposals.md.tmpl"),
    ("knowledge-base/scout-mistake-audit.md", "templates/scout-mistake-audit.md.tmpl"),
    ("knowledge-base/review-queue.md", "templates/review-queue.md.tmpl"),
    ("inbox.md", "templates/inbox.md.tmpl"),
    ("meetings/meetings.md", "templates/meetings/meetings.md.tmpl"),
)

_CAT1B_RUNNERS = (
    ("run-scout.sh", "templates/run-scout.sh.tmpl"),
    ("run-dreaming.sh", "templates/run-dreaming.sh.tmpl"),
    ("run-research.sh", "templates/run-research.sh.tmpl"),
)


def resolve_claude_bin(explicit: str = "", *, home: Path | None = None) -> str:
    """Absolute path to the ``claude`` CLI the scheduled runners should call.

    An explicit value (``--claude-bin``, or one persisted in scout-config.yaml)
    always wins. Otherwise: the first executable among ``which claude``, the
    native installer's ``~/.local/bin/claude``, Homebrew, then ``/usr/local/bin``
    — the same order Scout.app's ClaudeLauncher uses. When none is executable,
    return the native-installer location (the most likely place it will land)
    rather than a path no current installer uses; the doctor flags it either way.
    """
    if explicit:
        return explicit
    home = home or Path.home()
    native = str(home / ".local" / "bin" / "claude")
    candidates = [shutil.which("claude"), native, "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return native


@dataclass(frozen=True)
class _ManagedFile:
    """A plugin-owned file the upgrade rewrites, keeping any vault edit to it."""

    vault_rel: str
    plugin_rel: str
    rendered: bool  # template variables substituted
    executable: bool
    placeholder_if_missing: bool  # else skipped when the plugin lacks the source
    vault_developed: bool = False  # see _CAT_MERGE_FILES


_MANAGED_FILES = (
    *(_ManagedFile(v, p, False, False, True) for v, p in _CAT1_FILES_FROM_PLUGIN.items()),
    *(_ManagedFile(v, p, False, False, False, vault_developed=True) for v, p in _CAT_MERGE_FILES.items()),
    *(_ManagedFile(v, p, True, True, True) for v, p in _CAT1_TEMPLATES if v not in _CAT1_APPEND_ONLY),
    *(_ManagedFile(v, p, True, True, False) for v, p in _CAT1B_RUNNERS),
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
        "SCOUTCTL_BIN": str(resolve_scoutctl_bin()),
        "TIMEZONE": cfg.timezone,
        "PLATFORM": cfg.platform,
        "MAX_BUDGET": cfg.connector_inputs.get("max_budget", "5.00"),
        "CLAUDE_BIN": resolve_claude_bin(cfg.connector_inputs.get("claude_bin", "")),
        # Today in the timezone being installed (NOT the host clock, and not
        # config.today(): during a fresh install the vault's scout-config.yaml
        # does not exist yet, so the merged config cannot answer). #207.
        "TODAY_DATE": _dt.datetime.now(scout_config.timezone_or_default(cfg.timezone)).date().isoformat(),
        "AUTO_UPDATE_ENABLED": cfg.connector_inputs.get("auto_update_enabled", "false"),
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def merge_gitignore(vault_text: str, template_text: str) -> str:
    """The vault's .gitignore plus any template pattern it lacks. Append-only.

    Every vault line is kept in place and in order; nothing is ever removed, so a
    line the vault added (a secrets file, a local venv) survives any upgrade.
    Template patterns the vault is missing are appended after a blank line,
    together with the comment lines directly above them in the template.
    Patterns compare with trailing whitespace stripped, which git ignores; leading
    whitespace is part of a git pattern, so it counts. When the vault already
    has every template pattern, its text comes back byte-identical.
    """
    present = {s for line in vault_text.splitlines() if (s := line.rstrip()) and not s.startswith("#")}
    additions: list[str] = []
    comments: list[str] = []
    for line in template_text.splitlines():
        s = line.rstrip()
        if not s:
            comments = []
        elif s.startswith("#"):
            comments.append(line)
        else:
            if s not in present:
                additions += comments + [line]
                present.add(s)
            comments = []
    if not additions:
        return vault_text
    if not vault_text.strip():
        return template_text
    base = vault_text if vault_text.endswith("\n") else vault_text + "\n"
    return base + "\n" + "\n".join(additions) + "\n"


# ---------- stages ----------


def _stage_create_dirs(cfg: BootstrapConfig) -> None:
    for rel in _CAT1_DIR_LAYOUT:
        (cfg.vault / rel).mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class ManagedRender:
    file: _ManagedFile
    template: str  # the plugin's source (a placeholder when the source is missing)
    text: str  # what this plugin version writes into the vault


# Template variables whose value changes without any edit to the vault.
_UNPINNED_VARS = frozenset({"SCOUTCTL_BIN", "TODAY_DATE"})


def managed_renders(cfg: BootstrapConfig) -> list[ManagedRender]:
    """This plugin version's render of every managed file, in write order.

    A file whose source is missing gets its placeholder, or is left out when
    it has none (the runners, parser.py).
    """
    vars_ = _template_vars(cfg)
    out: list[ManagedRender] = []
    for m in _MANAGED_FILES:
        src = cfg.plugin_root / m.plugin_rel
        if src.exists():
            raw = src.read_text(encoding="utf-8")
            out.append(ManagedRender(m, raw, render_template(raw, vars_) if m.rendered else raw))
        elif m.placeholder_if_missing:
            placeholder = f"# placeholder: {m.plugin_rel}\n"
            out.append(ManagedRender(m, placeholder, placeholder))
    return out


def _stage_managed_files(cfg: BootstrapConfig) -> list[VaultEdit]:
    """Stage 3: write every managed file without losing a vault edit to it.

    Each file goes through vault_drift.reconcile: an unedited file takes the
    plugin's render, an edited one is kept, merged, or parked, and nothing it
    parks can block the next upgrade. Returns the edits worth reporting.
    """
    history = vault_drift.load_render_history()
    # With no recorded base, a file only counts as an unedited render if every
    # template variable has this upgrade's value — a hand-fixed path or budget
    # is an edit. The exceptions move on their own: the plugin root, the date.
    pinned = {k: v for k, v in _template_vars(cfg).items() if k not in _UNPINNED_VARS}
    edits: list[VaultEdit] = []
    for r in managed_renders(cfg):
        m = r.file
        # Older releases' templates, and the current one: a render of it with
        # other variable values (the plugin root moved) is not a vault edit.
        signatures = [*history.get(m.vault_rel, ()), vault_drift.signature(r.template, rendered=m.rendered)]
        try:
            edit = vault_drift.reconcile(
                cfg.vault,
                m.vault_rel,
                r.text,
                signatures=signatures,
                pinned=pinned,
                vault_developed=m.vault_developed,
            )
            if m.executable:
                (cfg.vault / m.vault_rel).chmod(0o755)
        except OSError as e:
            # One unreadable or locked file must not stop the stage: the other
            # files still upgrade and this one is reported, untouched.
            edit = VaultEdit(m.vault_rel, "error", detail=f"{type(e).__name__}: {e}")
        if edit is not None:
            edits.append(edit)
    vault_drift.prune_snapshots(cfg.vault, (m.vault_rel for m in _MANAGED_FILES))
    return edits


def _parked_copies(edits: list[VaultEdit]) -> list[str]:
    """The vault copies an upgrade replaced and set aside — its backups."""
    return [p for e in edits if e.outcome == "replaced" for p in e.parked]


def _stage_gitignore(cfg: BootstrapConfig) -> None:
    """Stage 4: the _CAT1_APPEND_ONLY files, merged into the vault's copy."""
    vars_ = _template_vars(cfg)
    for vault_rel, tmpl_rel in _CAT1_TEMPLATES:
        if vault_rel not in _CAT1_APPEND_ONLY:
            continue
        src = cfg.plugin_root / tmpl_rel
        target = cfg.vault / vault_rel
        merge = target.exists()
        if not src.exists():
            # A placeholder over an append-only file would drop every vault line.
            if not merge:
                _atomic_write(target, f"# placeholder: {tmpl_rel}\n")
            continue
        rendered = render_template(src.read_text(encoding="utf-8"), vars_)
        if merge:
            try:
                current = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                # Leave it as it is: overwriting would drop its lines, and
                # raising would leave the vault half-upgraded on every retry.
                print(f"warning: left {vault_rel} unchanged, could not read it: {e}", file=sys.stderr)
                continue
            rendered = merge_gitignore(current, rendered)
        _atomic_write(target, rendered)
        target.chmod(0o755)


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


def _assemble(cfg: BootstrapConfig, kind: str) -> str:
    """Assemble SKILL/DREAMING/RESEARCH from phase files."""
    vars_ = _template_vars(cfg)
    phases_root = cfg.plugin_root / "phases"
    bodies: list[str] = [brain_merge.assembly_header(kind, cfg.vault)]
    # Map assembly target → which modes this assembly is consumed by.
    # SKILL.md is read by BOTH briefing- and consolidation-type runs (run-scout.sh
    # auto-detects which); DREAMING.md by dreaming runs only; RESEARCH.md by
    # research runs only. Phases declaring `mode: [...]` get filtered to only
    # those whose mode list intersects the target. Phases with no `mode:`
    # (legacy / cross-cutting) land in every target.
    if kind == "SKILL":
        sources = [phases_root / "core", phases_root / "connectors"]
        target_modes = {"briefing", "consolidation"}
    elif kind == "DREAMING":
        sources = [phases_root / "core", phases_root / "modes"]
        target_modes = {"dreaming"}
    else:  # RESEARCH
        sources = [phases_root / "core", phases_root / "research"]
        target_modes = {"research"}
    for src_dir in sources:
        if not src_dir.exists():
            continue
        for phase_file in sorted(src_dir.glob("*.md")):
            try:
                sections = parse_phase_file(phase_file)
            except (ValueError, yaml.YAMLError) as e:
                # Phase file failed to parse — skip it (graceful degradation) but warn
                # loudly so the dropped phase can't vanish invisibly. The bundled phase
                # files all parse today; body horizontal rules use '***' (not bare '---',
                # which parse_phase_file treats as a frontmatter delimiter). This guard
                # exists to surface any future regression instead of silently dropping
                # a whole phase from the assembled SKILL/DREAMING/RESEARCH.
                print(
                    f"warning: skipping unparseable phase file {phase_file}: {e}",
                    file=sys.stderr,
                )
                continue
            kept = select_sections(
                sections,
                enabled_connectors=cfg.enabled_connectors,
                modes=target_modes,
            )
            for s in kept:
                bodies.append(render_template(s.body, vars_))
    return "\n\n".join(bodies)


def _snapshot_dir(cfg: BootstrapConfig) -> Path:
    return cfg.vault / ".scout-state" / "last-assembled"


def _write_provenance(snapshot_dir: Path, records: dict[str, brain_merge.Provenance]) -> None:
    _atomic_write(snapshot_dir / brain_merge.PROVENANCE_FILE, brain_merge.dumps_provenance(records))


def _stage_cat4_install(cfg: BootstrapConfig) -> None:
    """Stage 5 (install): assemble + write live + write snapshot."""
    snapshot_dir = _snapshot_dir(cfg)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, brain_merge.Provenance] = {}
    for kind in brain_merge.BRAIN_KINDS:
        content = _assemble(cfg, kind)
        _atomic_write(cfg.vault / f"{kind}.md", content)
        _atomic_write(snapshot_dir / f"{kind}.md", content)
        records[f"{kind}.md"] = brain_merge.Provenance.assembled(content)
    _write_provenance(snapshot_dir, records)


def _proposed_path(snapshot_dir: Path, name: str) -> Path:
    return snapshot_dir / brain_merge.PROPOSED_DIR / name


@dataclass
class _Cat4Outcome:
    conflicts: list[str] = field(default_factory=list)  # sidecars written this run
    skipped: list[str] = field(default_factory=list)  # already-pending sidecars
    backups: list[str] = field(default_factory=list)  # parked copies of live files replaced on the fingerprint alone


def _stage_cat4_upgrade(cfg: BootstrapConfig) -> _Cat4Outcome:
    """Stage 5 (upgrade): reconcile each brain file with its fresh assembly.

    ``brain_merge.decide`` picks the action per file; see its module docstring
    and docs/superpowers/specs/2026-10-02-brain-sidecars-never-block-upgrade-design.md.
    In short: a pending sidecar skips only that file; live is fast-forwarded
    or merged only over a snapshot the plugin is known to have written;
    anything else gets the plugin's version as a ``<KIND>.md.proposed-merge``
    sidecar with live and snapshot untouched (the M3-incident guard). The
    snapshot advances only once live has absorbed the assembly, which is what
    ``phases backport`` diffs against.

    Provenance is persisted after each file, so a failure on a later file
    can't leave an earlier, already-advanced snapshot without its record.
    """
    snapshot_dir = _snapshot_dir(cfg)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    records = brain_merge.load_provenance(snapshot_dir)
    out = _Cat4Outcome()
    for kind in brain_merge.BRAIN_KINDS:
        name = f"{kind}.md"
        ours = _assemble(cfg, kind)
        live = cfg.vault / name
        theirs = live.read_text(encoding="utf-8") if live.exists() else ours
        snap = snapshot_dir / name
        base = snap.read_text(encoding="utf-8") if snap.exists() else None
        proposed_path = _proposed_path(snapshot_dir, name)
        proposed = proposed_path.read_text(encoding="utf-8") if proposed_path.exists() else None
        sidecar = cfg.vault / brain_merge.sidecar_name(kind)
        prov = records.get(name, brain_merge.Provenance())

        action = brain_merge.decide(
            kind, ours=ours, theirs=theirs, base=base, prov=prov, proposed=proposed, sidecar_pending=sidecar.exists()
        )
        if action is brain_merge.Action.SKIP:
            out.skipped.append(sidecar.name)
            continue
        new_live = theirs
        proposal: str | None = None  # what goes to the sidecar, if anything
        if action is brain_merge.Action.FAST_FORWARD:
            new_live = ours
        elif action is brain_merge.Action.PROPOSE:
            proposal = ours
        elif action is brain_merge.Action.MERGE:
            assert base is not None  # decide() merges only over a snapshot that exists
            result = three_way_merge(base=base, ours=ours, theirs=theirs)
            if result.conflicts:
                proposal = result.content
            else:
                new_live = result.content
        # ADVANCE: live already equals ours.

        if proposal is not None:
            # The assembly is kept so `bootstrap resolve` can make it the base.
            _atomic_write(proposed_path, ours)
            _atomic_write(sidecar, proposal)
            out.conflicts.append(sidecar.name)
            continue
        if new_live != theirs:
            if prov.snapshot is None and theirs != proposed:
                # Only the fingerprint vouches for the base (a vault from before
                # provenance): park the file being replaced, once, like any
                # vault copy an upgrade replaces.
                parked = vault_drift.park_vault_copy(cfg.vault, name, theirs)
                out.backups.append(parked.relative_to(cfg.vault).as_posix())
            _atomic_write(live, new_live)
        # Live has absorbed ``ours``: it becomes the merge base.
        _atomic_write(snap, ours)
        records[name] = brain_merge.Provenance.assembled(ours)
        _write_provenance(snapshot_dir, records)
        proposed_path.unlink(missing_ok=True)
    return out


@dataclass(frozen=True)
class ResolveResult:
    name: str  # "SKILL.md"
    recorded_base: bool  # the proposal behind the sidecar became the merge base
    removed_sidecar: bool


def resolve_brain_file(vault: Path, kind: str) -> ResolveResult:
    """Record that the vault's ``<KIND>.md`` is now the resolution of its sidecar.

    The user has made the live file the version they want (moved the sidecar
    into place, merged it by hand, or kept their own). The assembly the
    sidecar was built from becomes the merge base, so the next upgrade merges
    only later plugin changes into the resolution, and the sidecar is removed.
    A sidecar an older engine left has no recorded assembly: it is removed and
    the base stays, so the next upgrade merges the file again.

    Raises ``ValueError`` while the live file holds conflict markers, or when
    there is nothing to resolve; ``FileNotFoundError`` if the live file is missing.
    """
    name = f"{kind}.md"
    live = vault / name
    sidecar = vault / brain_merge.sidecar_name(kind)
    snapshot_dir = vault / ".scout-state" / "last-assembled"
    proposed_path = _proposed_path(snapshot_dir, name)
    if not live.exists():
        raise FileNotFoundError(f"{name} is missing from {vault}")
    if brain_merge.has_conflict_markers(live.read_text(encoding="utf-8")):
        raise ValueError(f"{name} still has conflict markers (<<<<<<< / >>>>>>>); finish the merge first")
    if not proposed_path.exists() and not sidecar.exists():
        raise ValueError(f"nothing to resolve for {name}: no pending sidecar or recorded proposal")
    lock = vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock_with_wait(lock)
    try:
        recorded = proposed_path.exists()
        if recorded:
            proposal = proposed_path.read_text(encoding="utf-8")
            _atomic_write(snapshot_dir / name, proposal)
            records = brain_merge.load_provenance(snapshot_dir)
            records[name] = brain_merge.Provenance.assembled(proposal)
            _write_provenance(snapshot_dir, records)
            proposed_path.unlink()
        removed = sidecar.exists()
        sidecar.unlink(missing_ok=True)
    finally:
        release_lock(lock)
    return ResolveResult(name=name, recorded_base=recorded, removed_sidecar=removed)


def _stage_jobs_install(cfg: BootstrapConfig) -> None:
    """Stage 6: install schedule-tick + heartbeat (or cron block)."""
    if cfg.skip_jobs:
        return
    if cfg.platform == "macos":
        from scout.scripts.install_heartbeat_plist import install_plist as install_hb
        from scout.scripts.install_schedule_plist import install_plist as install_st

        install_st(home=Path.home(), force=True, bootstrap=True, vault=cfg.vault)
        install_hb(home=Path.home(), force=True, bootstrap=True, vault=cfg.vault)
    elif cfg.platform == "linux":
        from scout.scripts.install_cron import install_cron

        install_cron(home=Path.home())


def _stage_install_scoutctl_shim(cfg: BootstrapConfig) -> None:
    """Put `scoutctl` on the interactive PATH via a ~/.local/bin wrapper.

    This is what makes the bare `scoutctl` calls in the SKILL.md-driven
    session resolve (#99). Gated by `skip_jobs` alongside the other
    system-level install side-effects (LaunchAgents/cron) so headless/CI
    installs don't touch the real `~/.local/bin`. Best-effort regardless —
    it never fails the install.
    """
    if cfg.skip_jobs:
        return
    from scout.scripts.install_scoutctl_shim import install_scoutctl_shim

    install_scoutctl_shim(home=Path.home())


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


def _top_level_blocks(text: str) -> tuple[list[tuple[str, list[str], list[str]]], list[str]] | None:
    """Split a block-style YAML mapping into ``(key, leading, block)`` segments.

    ``leading`` is the run of comment and blank lines directly above the key;
    ``block`` is the key line and everything under it. The second return value
    holds the comment and blank lines after the last block. Returns None for
    anything this line scanner cannot split with confidence — document markers,
    flow collections spanning lines, duplicate keys — so the caller can fall
    back to a plain dump.
    """
    lines = text.splitlines(keepends=True)
    starts: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        if not line.strip() or line[0] in " \t#":
            continue
        if line.startswith(("---", "...")):
            return None
        if line.startswith("- ") or line.rstrip() == "-":
            continue  # a sequence item under the previous key
        try:
            parsed = yaml.safe_load(line)
        except yaml.YAMLError:
            return None
        if not isinstance(parsed, dict) or len(parsed) != 1:
            return None
        starts.append((i, str(next(iter(parsed)))))
    if len({key for _, key in starts}) != len(starts):
        return None

    def is_filler(line: str) -> bool:
        return not line.strip() or line.lstrip().startswith("#")

    segments: list[tuple[str, list[str], list[str]]] = []
    lead_from = 0
    for n, (start, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        # Comments and blank lines just above the next key belong to that key.
        while end > start + 1 and is_filler(lines[end - 1]):
            end -= 1
        segments.append((key, lines[lead_from:start], lines[start:end]))
        lead_from = end
    return segments, lines[lead_from:]


def _dump_keeping_comments(text: str, data: dict) -> str:
    """``yaml.safe_dump(data)``, but written over ``text`` so its comments survive.

    Each top-level block whose value is unchanged comes back byte-for-byte, with
    every comment inside it. A changed block is re-dumped under its original
    leading comments; comments inside a changed block are lost. New keys are
    appended at the end. Falls back to a plain dump whenever the text cannot be
    split safely, or the result would not load back as ``data``, and then warns
    on stderr if that loses comments.
    """
    plain = yaml.safe_dump(data, sort_keys=False)

    def fallback(reason: str) -> str:
        if any(line.lstrip().startswith("#") for line in text.splitlines()):
            print(f"warning: scout-config.yaml comments not preserved ({reason})", file=sys.stderr)
        return plain

    split = _top_level_blocks(text)
    try:
        original = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return fallback("the file does not parse as YAML")
    if split is None or not isinstance(original, dict):
        return fallback("its layout cannot be rewritten block by block")
    segments, trailer = split

    out: list[str] = []
    for key, leading, block in segments:
        out += leading
        if key not in data:
            continue
        if key in original and original[key] == data[key]:
            out += block
        else:
            out.append(yaml.safe_dump({key: data[key]}, sort_keys=False))
    out += trailer
    added = {key: value for key, value in data.items() if key not in {k for k, _, _ in segments}}
    if added:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(yaml.safe_dump(added, sort_keys=False))

    result = "".join(out)
    try:
        if yaml.safe_load(result) != data:
            return fallback("the block-wise rewrite did not load back identically")
    except yaml.YAMLError:
        return fallback("the block-wise rewrite did not parse")
    return result


def _stage_version_stamp(cfg: BootstrapConfig, *, is_upgrade: bool) -> None:
    """Stage 7: write/update plugin.version_at_last_{setup,update} plus persist
    connector_inputs so subsequent upgrades render templates with the same values
    that setup/migration used. Without this, upgrade defaults claude_bin /
    max_budget / user_slack_id back to their fallback values, which makes
    every rendered file look changed by the plugin on every upgrade.
    """
    config_path = cfg.vault / "scout-config.yaml"
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    existing = yaml.safe_load(text) or {}
    existing.setdefault("user", {})
    existing["user"]["name"] = cfg.user_name
    existing["user"]["email"] = cfg.user_email
    existing["instance"] = {
        "name": cfg.instance_name,
        "name_lower": cfg.instance_name_lower,
    }
    existing["timezone"] = cfg.timezone
    existing["platform"] = cfg.platform
    # Persist connectors: enabled list + inputs so upgrade can rebuild
    # BootstrapConfig faithfully without losing claude_bin/max_budget/etc.
    connectors = existing.setdefault("connectors", {})
    connectors["enabled"] = sorted(cfg.enabled_connectors)
    connectors["inputs"] = dict(cfg.connector_inputs)
    if cfg.auto_update is not None:
        auto_update = existing.setdefault("auto_update", {})
        auto_update["enabled"] = cfg.auto_update
        auto_update.setdefault("channel", "stable")
    plugin = existing.setdefault("plugin", {})
    if not is_upgrade:
        plugin["version_at_last_setup"] = cfg.plugin_version
    else:
        # Backfill version_at_last_setup if it's missing — happens for vaults
        # whose first /scout-update predates this field being persisted, and
        # for vaults whose scout-config.yaml had duplicate `plugin:` blocks
        # (PyYAML silently drops the earlier one on read). Doctor requires
        # both fields, so leaving the gap was making upgraded vaults
        # permanently red.
        plugin.setdefault("version_at_last_setup", cfg.plugin_version)
    plugin["version_at_last_update"] = cfg.plugin_version
    plugin.setdefault("applied_migrations", [])
    # Not a plain safe_dump: that round-trip deleted every comment in the file
    # on every upgrade, including the one `scoutctl budget set` writes.
    _atomic_write(config_path, _dump_keeping_comments(text, existing))


def _stage_write_engine_pointer(cfg: BootstrapConfig) -> Path | None:
    """Record where THIS engine lives (~/.local/state/scout/engine.json, §4.2).

    Gated by `skip_jobs` like the plists and `_stage_install_scoutctl_shim`
    (spec §4.2): the pointer must track the plists the doctor compares it
    with. A `--no-jobs` run leaves plists, shim and pointer alone, so a
    scratch install can never repoint Scout.app. migrate-legacy installs no
    shim but still writes the pointer, after its version stamp.
    Returns the pointer path, or None when skipped.
    """
    if cfg.skip_jobs:
        return None
    from scout.scripts.engine_pointer import current_pointer, write_pointer

    return write_pointer(current_pointer(vault=cfg.vault, managed_by=cfg.managed_by), home=Path.home())


# ---------- entry points ----------

_VAULT_MARKERS = ("scout-config.yaml", ".scout-state")

# Written by install() right after it creates the vault directory and removed
# right after the version stamp (which writes scout-config.yaml). While it
# exists the directory is an unfinished install, not a vault: detection sends
# it back to install (which resumes it) and upgrade/migrate-legacy refuse it.
# Without it, a failure between the first stage (.scout-state/) and the last
# (scout-config.yaml) looked like a legacy vault to the retry.
INSTALL_INCOMPLETE_MARKER = ".scout-state/install-incomplete"


def install_incomplete(vault: Path) -> bool:
    return (vault / INSTALL_INCOMPLETE_MARKER).exists()


def _vault_exists(vault: Path) -> bool:
    if not vault.exists() or install_incomplete(vault):
        return False
    return any((vault / m).exists() for m in _VAULT_MARKERS)


def _refuse_interrupted_install(vault: Path) -> None:
    if install_incomplete(vault):
        raise FileNotFoundError(
            f"{vault} holds an interrupted install ({INSTALL_INCOMPLETE_MARKER} present) — "
            f"re-run `scoutctl bootstrap install` (or `scoutctl bootstrap auto`) to finish it."
        )


def blocking_sidecars(vault: Path) -> list[str]:
    """Pending sidecars that make ``upgrade`` refuse: the ``_CAT_MERGE_FILES``
    ones. A pending brain-file sidecar only skips that file
    (``brain_merge.pending_brain_sidecars``)."""
    return [
        f"{vault_rel}.proposed-merge"
        for vault_rel in _CAT_MERGE_FILES
        if (vault / f"{vault_rel}.proposed-merge").exists()
    ]


def _refuse_pending_sidecars(vault: Path) -> None:
    pending = blocking_sidecars(vault)
    if pending:
        raise RuntimeError(
            f"Unresolved proposed-merge sidecar(s): {pending}. "
            f"Edit each to remove conflict markers, then "
            f"`mv <file>.proposed-merge <file>`, then re-run /scout-update."
        )


def install(cfg: BootstrapConfig) -> InstallResult:
    """Run the install pipeline. Stage 1 refuses if vault already exists.

    An interrupted install (INSTALL_INCOMPLETE_MARKER present) is not a vault,
    so re-running install() resumes it: every stage is safe to repeat. The
    existence check is repeated once the lock is held, so a second concurrent
    install of the same folder refuses rather than re-running the stages.
    """
    if _vault_exists(cfg.vault):
        raise FileExistsError(
            f"vault detected at {cfg.vault} — run /scout-update instead, "
            f"or manually remove the vault first (see Plan 8 §4.6 reset snippet)."
        )
    cfg.vault.mkdir(parents=True, exist_ok=True)
    marker = cfg.vault / INSTALL_INCOMPLETE_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    lock = cfg.vault / ".scout-logs" / ".scout-session.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    acquire_lock_with_wait(lock)
    try:
        # Re-check under the lock. A concurrent install of this folder hides
        # behind the marker until its version stamp, so the check above can
        # pass while it is still running; this run then waits here for its
        # lock. Only the version stamp writes scout-config.yaml, so finding it
        # now means an install finished: refuse instead of re-running every
        # stage over it, and drop the marker this run touched (or one a crash
        # right after the stamp left behind) so the vault reads as installed.
        if (cfg.vault / "scout-config.yaml").exists():
            marker.unlink(missing_ok=True)
            raise FileExistsError(
                f"vault detected at {cfg.vault} — another install finished it while this one "
                f"waited for the lock; run /scout-update (or `scoutctl bootstrap upgrade`) instead."
            )
        _stage_create_dirs(cfg)
        _stage_managed_files(cfg)
        _stage_gitignore(cfg)
        _stage_install_only_seeds(cfg)
        _stage_seed_schedule(cfg)
        _stage_cat4_install(cfg)
        _stage_jobs_install(cfg)
        _stage_install_scoutctl_shim(cfg)
        _stage_version_stamp(cfg, is_upgrade=False)
        marker.unlink(missing_ok=True)
        pointer = _stage_write_engine_pointer(cfg)
    finally:
        release_lock(lock)
    report = run_doctor(vault=cfg.vault, check_jobs=not cfg.skip_jobs)
    return InstallResult(vault=cfg.vault, doctor=report, pointer=pointer)


def _is_legacy_vault(vault: Path) -> bool:
    """Legacy: `.scout-state/` exists but `scout-config.yaml` doesn't.

    Indicates a Plan-5-era vault that pre-dates the Plan 8 config conventions.
    Such vaults need `scoutctl bootstrap migrate-legacy` before `upgrade` works.
    An interrupted install has the same shape but is not legacy (see
    INSTALL_INCOMPLETE_MARKER).
    """
    if install_incomplete(vault):
        return False
    return (vault / ".scout-state").exists() and not (vault / "scout-config.yaml").exists()


def _stage_migrations(cfg: BootstrapConfig) -> None:
    """Run idempotent data-format migrations on an existing vault.

    Currently: convert a single-file Wishlist + research-queue into the
    per-file format. ``migrate_perfile`` no-ops on already-migrated vaults,
    so this is safe to run on every upgrade.
    """
    migrate_perfile(cfg.vault)


def upgrade(cfg: BootstrapConfig) -> UpgradeResult:
    """Run the upgrade pipeline. Refuses if no vault, an interrupted install, or a legacy (pre-Plan-8) vault."""
    _refuse_interrupted_install(cfg.vault)
    if not _vault_exists(cfg.vault):
        raise FileNotFoundError(f"no vault at {cfg.vault} — run /scout-setup instead.")
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
        # Migrations run FIRST so later stages (e.g. cat-4 assembly of
        # DREAMING/RESEARCH) land on an already-migrated vault. Idempotent:
        # no-ops on vaults that are already per-file or never had legacy files.
        _stage_migrations(cfg)
        vault_edits = _stage_managed_files(cfg)
        _stage_gitignore(cfg)
        # _stage_seed_schedule is idempotent (returns early if the file
        # exists) so it's safe to call on upgrade. Without it, vaults set
        # up before .scout-state/schedule.yaml was a first-class file
        # never get one written, and the dispatcher silently falls back
        # to the packaged default.
        _stage_seed_schedule(cfg)
        cat4 = _stage_cat4_upgrade(cfg)
        conflicts = cat4.conflicts
        _stage_jobs_install(cfg)
        _stage_install_scoutctl_shim(cfg)
        _stage_version_stamp(cfg, is_upgrade=True)
        pointer = _stage_write_engine_pointer(cfg)
    finally:
        release_lock(lock)
    report = run_doctor(vault=cfg.vault, check_jobs=not cfg.skip_jobs)
    return UpgradeResult(
        vault=cfg.vault,
        doctor=report,
        conflicts=conflicts,
        backups=_parked_copies(vault_edits) + cat4.backups,
        skipped=cat4.skipped,
        vault_edits=vault_edits,
        pointer=pointer,
    )


def migrate_legacy(cfg: BootstrapConfig) -> MigrateLegacyResult:
    """One-time migration of a Plan-5-era vault to Plan 8 format.

    Required: cfg.vault must have ``.scout-state/`` but no ``scout-config.yaml``.

    Actions (in order):
      1. Acquire global lock.
      2. Snapshot current SKILL.md / DREAMING.md / RESEARCH.md to
         ``.scout-state/last-assembled/`` as the merge baseline, recorded as
         *seeded* so no upgrade ever overwrites or merges over them (the M3
         incident). Live files never touched.
      3. Write the managed files (scripts, hooks, runners, …) rendered against
         the user-provided cfg vars. A file no release shipped (a customised
         legacy runner) is parked under ``.scout-state/drift/`` first.
      4. Merge ``.gitignore`` (append-only).
      5. Skip cat-4 merge entirely — snapshots just established, nothing to
         merge.
      6. Job lifecycle (subject to cfg.skip_jobs).
      7. Write version stamps to a fresh scout-config.yaml.
      8. Doctor.

    After this, the vault is Plan 8-compatible and `upgrade()` works normally.
    Refuses an interrupted install, which has a legacy vault's shape.
    """
    _refuse_interrupted_install(cfg.vault)
    if not (cfg.vault / ".scout-state").exists():
        raise FileNotFoundError(
            f"no vault at {cfg.vault} (no .scout-state/ directory) — run /scout-setup for a fresh install."
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
        snapshot_dir = _snapshot_dir(cfg)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        records: dict[str, brain_merge.Provenance] = {}
        for kind in brain_merge.BRAIN_KINDS:
            live = cfg.vault / f"{kind}.md"
            if live.exists():
                content = live.read_text(encoding="utf-8")
                _atomic_write(snapshot_dir / f"{kind}.md", content)
                records[f"{kind}.md"] = brain_merge.Provenance.seeded(content)
                snapshots_recorded.append(f"{kind}.md")
        _write_provenance(snapshot_dir, records)
        # 2. Seed .scout-state/schedule.yaml if missing. Legacy Plan-5-era
        #    vaults never explicitly wrote this file; the live dispatcher
        #    silently falls back to packaged defaults. Make the vault copy
        #    explicit so the doctor reports green and future schedule edits
        #    have a stable home.
        _stage_seed_schedule(cfg)
        # 3. Managed files with the now-correct template vars. A legacy vault
        #    has no record of its last render, so a file no release shipped
        #    (a customised runner) is parked, not overwritten silently.
        vault_edits = _stage_managed_files(cfg)
        _stage_gitignore(cfg)
        # 5. SKIP cat-4 merge: snapshots just established equal current live.
        # 6. Jobs.
        _stage_jobs_install(cfg)
        # 7. Version stamps (is_upgrade=False so both version_at_last_setup and
        #    version_at_last_update are written; setup marks "migrated at this
        #    plugin version", matching how a freshly-installed vault records it).
        _stage_version_stamp(cfg, is_upgrade=False)
        pointer = _stage_write_engine_pointer(cfg)
    finally:
        release_lock(lock)
    report = run_doctor(vault=cfg.vault, check_jobs=not cfg.skip_jobs)
    return MigrateLegacyResult(
        vault=cfg.vault,
        doctor=report,
        backups=_parked_copies(vault_edits),
        snapshots_recorded=snapshots_recorded,
        vault_edits=vault_edits,
        pointer=pointer,
    )
