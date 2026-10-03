"""Unit tests for engine/scout/scripts/bootstrap_auto.py (scout-plugin#26, spec E3)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scout.scripts.bootstrap import BootstrapConfig
from scout.scripts.bootstrap_auto import AutoAction, detect, result_dict, run

PLUGIN = Path(__file__).resolve().parents[3]


def _cfg(vault: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=PLUGIN,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Alex",
        user_email="alex@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.10.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,
        skip_claude=True,
        managed_by="scout-app",
    )


def test_detect_missing_dir_is_install(tmp_path):
    assert detect(tmp_path / "Scout").action is AutoAction.INSTALL


def test_detect_empty_dir_is_install(tmp_path):
    (tmp_path / "Scout").mkdir()
    assert detect(tmp_path / "Scout").action is AutoAction.INSTALL


def test_detect_legacy_vault_is_migrate(tmp_path):
    (tmp_path / "Scout" / ".scout-state").mkdir(parents=True)
    assert detect(tmp_path / "Scout").action is AutoAction.MIGRATE_LEGACY


def test_detect_configured_vault_is_upgrade(tmp_path):
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "scout-config.yaml").write_text("instance_name: x\n")
    assert detect(tmp_path / "Scout").action is AutoAction.UPGRADE


def test_detect_pending_parser_sidecar_is_refused(tmp_path):
    """Only a parser.py sidecar an older engine left still blocks the upgrade."""
    vault = tmp_path / "Scout"
    (vault / "knowledge-base" / "ontology").mkdir(parents=True)
    (vault / "scout-config.yaml").write_text("instance_name: x\n")
    (vault / "knowledge-base" / "ontology" / "parser.py.proposed-merge").write_text("<<<<<<<\n")
    plan = detect(vault)
    assert plan.action is AutoAction.REFUSED and "parser.py.proposed-merge" in plan.reason


def test_detect_pending_brain_sidecar_still_upgrades(tmp_path):
    """A pending brain-file sidecar only skips that file, so it doesn't stop
    an unattended upgrade."""
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "scout-config.yaml").write_text("instance_name: x\n")
    (tmp_path / "Scout" / "SKILL.md.proposed-merge").write_text("<<<<<<<\n")
    assert detect(tmp_path / "Scout").action is AutoAction.UPGRADE


def test_run_reports_a_skipped_brain_file(tmp_path):
    vault = tmp_path / "Scout"
    run(_cfg(vault))
    (vault / "DREAMING.md.proposed-merge").write_text("pending\n")
    result, code = run(_cfg(vault))
    assert result["action"] == "upgrade"
    assert result["skipped"] == ["DREAMING.md.proposed-merge"]
    assert code == 1  # the pending sidecar keeps the doctor yellow


def test_detect_nonempty_non_vault_is_refused(tmp_path):
    (tmp_path / "Scout").mkdir()
    (tmp_path / "Scout" / "notes.txt").write_text("hi")
    plan = detect(tmp_path / "Scout")
    assert plan.action is AutoAction.REFUSED and "not a Scout vault" in plan.reason


def test_run_installs_then_upgrades(tmp_path):
    vault = tmp_path / "Scout"
    first, code = run(_cfg(vault))
    assert first["action"] == "install" and code in (0, 1)
    assert first["doctor"]["severity"] in ("green", "yellow")
    assert first["pointer"] is None  # skip_jobs: no plists/shim, so no pointer either
    assert (vault / "scout-config.yaml").exists()
    second, _ = run(_cfg(vault))
    assert second["action"] == "upgrade"
    assert second["conflicts"] == []


def test_run_dry_run_mutates_nothing(tmp_path):
    vault = tmp_path / "Scout"
    d, code = run(_cfg(vault), dry_run=True)
    assert code == 0 and d["dry_run"] is True and d["action"] == "install" and d["doctor"] is None
    assert not vault.exists()


def test_run_refused_returns_exit_2(tmp_path):
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "notes.txt").write_text("hi")
    d, code = run(_cfg(vault))
    assert code == 2 and d["action"] == "refused" and d["error"]
    assert d["mutated"] is False  # decided before dispatch


def test_result_dict_has_the_contract_keys(tmp_path):
    d = result_dict(action=AutoAction.INSTALL, vault=tmp_path, plugin_version="0.10.0", result=None)
    assert set(d) == {
        "schema_version",
        "action",
        "reason",
        "dry_run",
        "vault",
        "plugin_version",
        "error",
        "doctor",
        "conflicts",
        "backups",
        "snapshots_recorded",
        "skipped",
        "pointer",
        "vault_edits",
        "mutated",
    }
    assert d["mutated"] is False


# --- Additional branch coverage (not in the brief; closes gaps the coverage
# gate's branch-coverage requirement flagged in `detect`/`run`) --------------


def test_detect_file_at_vault_path_is_refused(tmp_path):
    """`detect()`'s `not vault.is_dir()` branch: a plain file sitting where the
    vault directory should be."""
    vault_path = tmp_path / "Scout"
    vault_path.write_text("not a directory")
    plan = detect(vault_path)
    assert plan.action is AutoAction.REFUSED and "not a directory" in plan.reason


def _populate_legacy_vault(vault: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / ".scout-state").mkdir()
    (vault / "knowledge-base").mkdir()
    (vault / "action-items").mkdir()
    (vault / "scripts").mkdir()
    (vault / "hooks").mkdir()
    (vault / ".scout-logs").mkdir()
    (vault / "SKILL.md").write_text("# SKILL\n")


def test_run_dispatches_migrate_legacy(tmp_path):
    """`run()`'s MIGRATE_LEGACY dispatch branch — exercised via `detect()`
    identifying a legacy (.scout-state/ without scout-config.yaml) vault."""
    vault = tmp_path / "Scout"
    _populate_legacy_vault(vault)
    d, code = run(_cfg(vault))
    assert d["action"] == "migrate-legacy"
    assert code in (0, 1, 2)


def test_run_dispatch_exception_becomes_refused(tmp_path, monkeypatch):
    """Defensive: if install()/upgrade()/migrate_legacy() raises despite
    detect() predicting a runnable state (e.g. a TOCTOU race between the two),
    run() must surface a refused/exit-2 payload rather than propagate."""
    import scout.scripts.bootstrap_auto as bootstrap_auto

    def _raise(cfg: BootstrapConfig) -> None:
        raise FileExistsError("raced with another process")

    monkeypatch.setattr(bootstrap_auto, "install", _raise)
    vault = tmp_path / "Scout"
    d, code = run(_cfg(vault))
    assert code == 2 and d["action"] == "refused" and "raced" in d["error"]
    assert d["mutated"] is False  # the entrypoints raise these before any stage runs


# --- Fix round 1 (reviewer findings 1 & 2) -----------------------------------


def test_detect_ds_store_only_dir_is_install(tmp_path):
    """Finder leaves .DS_Store behind just from opening/viewing ~/Scout —
    that alone must not count as vault content (finding 1)."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / ".DS_Store").write_bytes(b"\x00\x00")
    assert detect(vault).action is AutoAction.INSTALL


def test_detect_ds_store_plus_content_is_refused(tmp_path):
    """.DS_Store alongside real content is still a non-empty, non-vault
    directory — the Finder-metadata carve-out must not swallow real files."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / ".DS_Store").write_bytes(b"\x00\x00")
    (vault / "notes.txt").write_text("hi")
    plan = detect(vault)
    assert plan.action is AutoAction.REFUSED and "not a Scout vault" in plan.reason


def test_detect_dotgit_only_dir_is_refused(tmp_path):
    """A dotfile that isn't Finder metadata (e.g. .git/) still counts as
    content — only the exact names .DS_Store/.localized are ignored."""
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / ".git").mkdir()
    plan = detect(vault)
    assert plan.action is AutoAction.REFUSED and "not a Scout vault" in plan.reason


def test_run_lock_busy_becomes_refused(tmp_path, monkeypatch):
    """LockBusyError (lock contention on .scout-logs/.scout-session.lock,
    raised by acquire_lock_with_wait after its poll timeout) must produce a
    refused/exit-2 payload like the other caught exceptions, not an uncaught
    traceback (finding 2)."""
    import scout.scripts.bootstrap_auto as bootstrap_auto
    from scout.scripts.bootstrap_lock import LockBusyError

    lock_path = tmp_path / "Scout" / ".scout-logs" / ".scout-session.lock"

    def _raise(cfg: BootstrapConfig) -> None:
        raise LockBusyError(lock_path, 4242)

    monkeypatch.setattr(bootstrap_auto, "install", _raise)
    vault = tmp_path / "Scout"
    d, code = run(_cfg(vault))
    assert code == 2
    assert d["action"] == "refused"
    assert "4242" in d["error"]
    assert d["mutated"] is False


# --- Final review: interrupted installs resume (Ruling 16) ------------------


def test_interrupted_install_resumes_as_install(tmp_path, monkeypatch):
    """install() creates .scout-state/ in its first stage but writes
    scout-config.yaml at the end. A failure in between used to make the
    retry look like a legacy vault → migrate-legacy, which never writes
    SKILL/DREAMING/RESEARCH or parser.py → doctor red forever."""
    import scout.scripts.bootstrap as bootstrap

    vault = tmp_path / "Scout"
    marker = vault / ".scout-state" / "install-incomplete"

    def disk_full(cfg: BootstrapConfig) -> None:
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(bootstrap, "_stage_cat4_install", disk_full)
        with pytest.raises(OSError, match="No space"):
            bootstrap.install(_cfg(vault))
    assert marker.exists()
    assert (vault / ".scout-state").is_dir() and not (vault / "scout-config.yaml").exists()
    plan = detect(vault)
    assert plan.action is AutoAction.INSTALL
    assert plan.reason == "resuming an interrupted install"

    d, code = run(_cfg(vault))
    assert d["action"] == "install", d
    assert d["doctor"]["severity"] != "red", d["doctor"]
    assert code in (0, 1)
    assert not marker.exists()
    assert (vault / "SKILL.md").exists()
    assert (vault / "knowledge-base" / "ontology" / "parser.py").exists()


@pytest.mark.parametrize("interrupted", [False, True], ids=["fresh", "resumed"])
def test_auto_install_records_a_last_rendered_base_for_every_managed_file(tmp_path, interrupted, monkeypatch):
    """`auto` installs through install()'s shared managed-files stage, so the
    vault starts with a base for each plugin-owned file and no drift — also
    when it resumes an interrupted install (#263's drift policy relies on it)."""
    import scout.scripts.bootstrap as bootstrap
    from scout.scripts import vault_drift

    vault = tmp_path / "Scout"
    if interrupted:

        def disk_full(cfg: BootstrapConfig) -> None:
            raise OSError(28, "No space left on device")

        with monkeypatch.context() as m:
            m.setattr(bootstrap, "_stage_cat4_install", disk_full)
            with pytest.raises(OSError):
                bootstrap.install(_cfg(vault))
    d, code = run(_cfg(vault))
    assert d["action"] == "install" and code in (0, 1), d
    renders = bootstrap.managed_renders(_cfg(vault))
    assert renders
    for r in renders:
        assert vault_drift.snapshot_path(vault, r.file.vault_rel).is_file(), r.file.vault_rel
    assert vault_drift.report(vault, {r.file.vault_rel: r.text for r in renders}) == []


def test_upgrade_json_reports_vault_edits(tmp_path):
    """The JSON result carries each vault edit an upgrade found (#263), so
    Scout.app can show what was kept, merged or parked."""
    vault = tmp_path / "Scout"
    run(_cfg(vault))
    heartbeat = vault / "scripts" / "heartbeat.sh"
    heartbeat.write_text(heartbeat.read_text(encoding="utf-8") + "# a local fix\n", encoding="utf-8")
    d, _ = run(_cfg(vault))
    assert d["action"] == "upgrade"
    edits = {e["path"]: e for e in d["vault_edits"]}
    assert edits["scripts/heartbeat.sh"]["outcome"] == "kept"
    assert edits["scripts/heartbeat.sh"]["message"]


def test_install_that_waited_for_the_lock_refuses_the_vault_finished_meanwhile(tmp_path, monkeypatch):
    """Two installs of one folder: the second passes the up-front check (the
    first's marker hides its half-built vault), then blocks on the lock while
    the first runs every stage. Once it gets the lock it must refuse, not
    re-run the stages over the finished vault."""
    import scout.scripts.bootstrap as bootstrap

    vault = tmp_path / "Scout"
    real_acquire = bootstrap.acquire_lock_with_wait
    stage_runs: list[str] = []
    real_managed = bootstrap._stage_managed_files

    def counting_managed(cfg: BootstrapConfig) -> list:
        stage_runs.append(cfg.user_name)
        return real_managed(cfg)

    def wait_while_the_first_install_runs(lock: Path, **kwargs: object) -> None:
        monkeypatch.setattr(bootstrap, "acquire_lock_with_wait", real_acquire)
        bootstrap.install(_cfg(vault))  # the first install, start to finish
        real_acquire(lock, **kwargs)

    monkeypatch.setattr(bootstrap, "_stage_managed_files", counting_managed)
    monkeypatch.setattr(bootstrap, "acquire_lock_with_wait", wait_while_the_first_install_runs)
    second = _cfg(vault)
    second.user_name = "Second Caller"
    with pytest.raises(FileExistsError, match="another install finished"):
        bootstrap.install(second)

    assert stage_runs == [_cfg(vault).user_name]  # only the first install ran the stages
    assert not (vault / ".scout-state" / "install-incomplete").exists()
    assert "Second Caller" not in (vault / "scout-config.yaml").read_text()
    assert not (vault / ".scout-logs" / ".scout-session.lock").exists()  # the lock was released
    assert detect(vault).action is AutoAction.UPGRADE


def test_install_clears_a_stale_marker_on_a_stamped_vault(tmp_path):
    """A crash between the version stamp and the marker's removal leaves a
    finished vault that still reads as an interrupted install. Resuming it
    clears the marker and refuses, so the next run upgrades."""
    from scout.scripts.bootstrap import install

    vault = tmp_path / "Scout"
    install(_cfg(vault))
    (vault / ".scout-state" / "install-incomplete").touch()
    assert detect(vault).action is AutoAction.INSTALL
    d, code = run(_cfg(vault))
    assert d["action"] == "refused" and code == 2
    assert d["mutated"] is False
    assert detect(vault).action is AutoAction.UPGRADE


def test_upgrade_and_migrate_legacy_refuse_an_interrupted_install(tmp_path):
    """A marker-bearing vault is not a vault: only install may touch it."""
    from scout.scripts.bootstrap import migrate_legacy, upgrade

    vault = tmp_path / "Scout"
    (vault / ".scout-state").mkdir(parents=True)
    (vault / ".scout-state" / "install-incomplete").touch()
    with pytest.raises(FileNotFoundError, match="interrupted install"):
        upgrade(_cfg(vault))
    with pytest.raises(FileNotFoundError, match="interrupted install"):
        migrate_legacy(_cfg(vault))
    # Even if the crash landed after the version stamp, the marker still wins.
    (vault / "scout-config.yaml").write_text("instance: {name: Scout}\n")
    assert detect(vault).action is AutoAction.INSTALL
    with pytest.raises(FileNotFoundError, match="interrupted install"):
        upgrade(_cfg(vault))


# --- Final review: OS errors keep the JSON contract (Ruling 17) -------------


@pytest.mark.parametrize(
    "exc",
    [OSError(28, "No space left on device"), RuntimeError("template render failed")],
    ids=["OSError", "RuntimeError"],
)
def test_stage_failure_is_refused_and_flagged_mutated(tmp_path, monkeypatch, exc):
    """A stage failing mid-dispatch has already written to the vault, so the
    refusal says so (`mutated: true`) instead of propagating (exit 70, empty
    stdout) or claiming nothing happened."""
    import scout.scripts.bootstrap as bootstrap

    def boom(cfg: BootstrapConfig) -> None:
        raise exc

    monkeypatch.setattr(bootstrap, "_stage_managed_files", boom)
    vault = tmp_path / "Scout"
    d, code = run(_cfg(vault))
    assert code == 2
    assert d["action"] == "refused"
    assert d["mutated"] is True
    assert str(exc) in d["error"]
    assert d["reason"] == "no vault: directory missing or empty"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_vault_parent_is_refused_not_raised(tmp_path):
    """The reviewer's repro: the vault's parent cannot be written (read-only
    volume, TCC-denied folder) — install()'s mkdir raises PermissionError."""
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        d, code = run(_cfg(parent / "Scout"))
    finally:
        parent.chmod(0o755)
    assert code == 2 and d["action"] == "refused" and "Permission denied" in d["error"]


def test_run_uses_the_callers_plan_instead_of_redetecting(tmp_path, monkeypatch):
    """The CLI already detected (and guarded that call); run() must not
    detect a second time when handed the Plan."""
    import scout.scripts.bootstrap_auto as bootstrap_auto
    from scout.scripts.bootstrap_auto import Plan

    def no_detect(vault: Path) -> Plan:
        raise AssertionError("run() re-detected despite being handed a plan")

    monkeypatch.setattr(bootstrap_auto, "detect", no_detect)
    d, code = run(_cfg(tmp_path / "Scout"), plan=Plan(AutoAction.REFUSED, "decided by the caller"))
    assert code == 2 and d["error"] == "decided by the caller" and d["mutated"] is False
