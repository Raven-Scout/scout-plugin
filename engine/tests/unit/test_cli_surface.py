"""Coverage of the `scoutctl` command surface that the other cli tests skip.

`test_cli.py` covers version/manifest/main(); the three `test_cli_*_subapp.py`
files cover the read paths of connectors/schedule/trigger. What is left — and
what this file drives — is:

* the hook/script shims (`scoutctl hook …`, `budget check`, `session cc-cache`,
  `heartbeat run`, `pre-session data`, `connector-health-report`). Each is a
  one-line forward whose only contract is "call that module's main() with these
  arguments and exit with its return code". A silently dropped flag here is
  invisible until a scheduled run misbehaves, so pin the forwarding.
* the installer commands (`schedule install-plist`, `install-wake-schedule`,
  `install-heartbeat-plist`, `install-cron`, `install-all`), whose real bodies
  touch `~/Library/LaunchAgents` and `crontab` — stubbed here, with the CLI's
  own branching (uninstall / FileExistsError / unsupported platform) asserted.
* the snapshot commands' check/write/drift branches.
* `bootstrap {install,upgrade,doctor,migrate-legacy}`, `phases backport`,
  `self-update check` and `tui`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from scout import cli
from scout.errors import ConfigError
from scout.events import Event, now_iso

runner = CliRunner()

PLUGIN_ROOT = Path(cli.__file__).parent.parent.parent


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "Scout"
    for sub in (".scout-logs", ".scout-cache", ".scout-state", "knowledge-base", "action-items"):
        (d / sub).mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(d))
    return d


# ---------------------------------------------------------------------------
# Shims: `scoutctl <cmd>` -> `<module>.main(...)`, exit code = return value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "module"),
    [
        (["hook", "connector-log"], "scout.hooks.connector_log"),
        (["hook", "session-tokens"], "scout.hooks.session_tokens"),
        (["hook", "session-tool-log"], "scout.hooks.session_tool_log"),
        (["connector-health-report"], "scout.scripts.connector_health_report"),
    ],
)
@pytest.mark.parametrize("rc", [0, 1, 2])
def test_zero_arg_shim_forwards_exit_code(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], module: str, rc: int
) -> None:
    monkeypatch.setattr(f"{module}.main", lambda: rc)
    assert runner.invoke(cli.app, argv).exit_code == rc


def test_hook_kb_pre_filter_forwards_session_type(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr("scout.hooks.kb_pre_filter.main", lambda argv: seen.append(argv) or 0)

    assert runner.invoke(cli.app, ["hook", "kb-pre-filter"]).exit_code == 0
    assert seen[-1] == ["dreaming"]  # documented default

    assert runner.invoke(cli.app, ["hook", "kb-pre-filter", "-s", "briefing"]).exit_code == 0
    assert seen[-1] == ["briefing"]


def test_budget_check_forwards_verbose_and_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def fake_run(*, verbose: bool) -> int:
        seen.append(verbose)
        return 2  # documented "backoff"

    monkeypatch.setattr("scout.scripts.budget_check.run", fake_run)

    assert runner.invoke(cli.app, ["budget", "check"]).exit_code == 2
    assert seen[-1] is False
    runner.invoke(cli.app, ["budget", "check", "--verbose"])
    assert seen[-1] is True


def test_session_cc_cache_forwards_all_options(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_main(*, hours: int, instance_name: str, tz_name: str) -> int:
        seen.update(hours=hours, instance_name=instance_name, tz_name=tz_name)
        return 0

    monkeypatch.setattr("scout.scripts.cc_session_cache.main", fake_main)

    assert runner.invoke(cli.app, ["session", "cc-cache"]).exit_code == 0
    # tz_name defaults to None: since the TZ-localization work the zone is
    # resolved from the vault's configured timezone at runtime rather than
    # baked in as a literal default here.
    assert seen == {"hours": 24, "instance_name": "Scout", "tz_name": None}

    runner.invoke(
        cli.app,
        ["session", "cc-cache", "--hours", "6", "--instance-name", "Nightly", "--timezone", "UTC"],
    )
    assert seen == {"hours": 6, "instance_name": "Nightly", "tz_name": "UTC"}


def test_heartbeat_run_forwards_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    monkeypatch.setattr("scout.scripts.heartbeat.main", lambda *, dry_run: seen.append(dry_run) or 0)

    assert runner.invoke(cli.app, ["heartbeat", "run"]).exit_code == 0
    assert seen[-1] is False
    runner.invoke(cli.app, ["heartbeat", "run", "--dry-run"])
    assert seen[-1] is True


def test_pre_session_data_forwards_session_type(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr("scout.scripts.pre_session_data.main", lambda st: seen.append(st) or 0)

    assert runner.invoke(cli.app, ["pre-session", "data"]).exit_code == 0
    assert seen[-1] == "unknown"  # documented default
    runner.invoke(cli.app, ["pre-session", "data", "research"])
    assert seen[-1] == "research"


def test_schedule_tick_forwards_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scout.scripts.schedule_tick.main", lambda: 3)
    assert runner.invoke(cli.app, ["schedule", "tick"]).exit_code == 3


# ---------------------------------------------------------------------------
# connectors list / show / reload
# ---------------------------------------------------------------------------


def test_connectors_list_is_tab_separated_and_sorted() -> None:
    result = runner.invoke(cli.app, ["connectors", "list"])
    assert result.exit_code == 0, result.output
    rows = [ln for ln in result.stdout.splitlines() if ln.strip()]
    assert rows, "roster is empty"
    keys = [ln.split("\t")[0] for ln in rows]
    assert keys == sorted(keys)
    assert all(len(ln.split("\t")) == 3 for ln in rows)


def test_connectors_show_emits_the_full_record() -> None:
    key = runner.invoke(cli.app, ["connectors", "list"]).stdout.splitlines()[0].split("\t")[0]
    result = runner.invoke(cli.app, ["connectors", "show", key])
    assert result.exit_code == 0, result.output
    record = json.loads(result.stdout)
    assert record["key"] == key
    assert set(record) == {
        "key",
        "display_name",
        "tier",
        "capabilities",
        "required_in",
        "required_in_types",
        "remediation",
        "notes",
    }
    assert set(record["remediation"]) == {"first_fix", "detail"}


def test_connectors_show_unknown_key_is_a_config_error() -> None:
    result = runner.invoke(cli.app, ["connectors", "show", "not-a-connector"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
    assert "unknown connector: not-a-connector" in str(result.exception)


def test_connectors_reload_reports_success() -> None:
    result = runner.invoke(cli.app, ["connectors", "reload"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "reloaded"


# ---------------------------------------------------------------------------
# connectors / schedule snapshot
#
# Both commands share one shape: --check compares, no --check writes, and the
# best-effort scout-app dual-write warns rather than failing when that repo
# isn't checked out. HOME is a tmp dir here (conftest), so the app fixture path
# never exists — which is exactly the warn branch.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", ["connectors", "schedule"])
def test_snapshot_check_passes_against_a_freshly_written_file(group: str, tmp_path: Path) -> None:
    target = tmp_path / f"{group}.snapshot.json"
    write = runner.invoke(cli.app, [group, "snapshot", "--target", str(target), "--no-also-write-app-fixture"])
    assert write.exit_code == 0, write.output
    assert f"Wrote: {target}" in write.stdout

    check = runner.invoke(cli.app, [group, "snapshot", "--target", str(target), "--check"])
    assert check.exit_code == 0, check.output
    assert "snapshot OK" in check.stdout


@pytest.mark.parametrize("group", ["connectors", "schedule"])
def test_snapshot_check_detects_drift(group: str, tmp_path: Path) -> None:
    target = tmp_path / f"{group}.snapshot.json"
    runner.invoke(cli.app, [group, "snapshot", "--target", str(target), "--no-also-write-app-fixture"])
    payload = json.loads(target.read_text())
    payload["schema_version"] = 999
    target.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(cli.app, [group, "snapshot", "--target", str(target), "--check"])
    assert result.exit_code == 1
    assert "Drift detected" in result.output


@pytest.mark.parametrize("group", ["connectors", "schedule"])
def test_snapshot_warns_when_app_fixture_repo_is_absent(group: str, tmp_path: Path) -> None:
    """Default is a dual-write; on a machine without ~/scout-app checked out it
    must warn and still exit 0 — a build agent has no second repo."""
    target = tmp_path / f"{group}.snapshot.json"
    result = runner.invoke(cli.app, [group, "snapshot", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert "skipped scout-app fixture write" in result.output


@pytest.mark.parametrize("group", ["connectors", "schedule"])
def test_snapshot_target_pointed_at_app_fixture_writes_once(
    group: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--target == the app fixture path must not double-write (nor warn)."""
    target = tmp_path / "fixture.snapshot.json"
    module = "scout.scripts.connectors_snapshot" if group == "connectors" else "scout.scripts.schedule_snapshot"
    monkeypatch.setattr(f"{module}.app_fixture_snapshot_path", lambda: target)

    result = runner.invoke(cli.app, [group, "snapshot", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert result.stdout.count("Wrote:") == 1
    assert "skipped scout-app fixture write" not in result.output


@pytest.mark.parametrize("group", ["connectors", "schedule"])
def test_snapshot_dual_writes_when_app_fixture_dir_exists(
    group: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fixture_dir = tmp_path / "scout-app" / "ScoutTests" / "Fixtures"
    fixture_dir.mkdir(parents=True)
    fixture = fixture_dir / "snapshot.json"
    module = "scout.scripts.connectors_snapshot" if group == "connectors" else "scout.scripts.schedule_snapshot"
    monkeypatch.setattr(f"{module}.app_fixture_snapshot_path", lambda: fixture)

    target = tmp_path / "canonical.json"
    result = runner.invoke(cli.app, [group, "snapshot", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert fixture.exists()
    assert result.stdout.count("Wrote:") == 2


# ---------------------------------------------------------------------------
# schedule fire-now
# ---------------------------------------------------------------------------


def _event(kind: str, payload: dict[str, object] | None = None) -> Event:
    return Event(id="01HXAAA0000000000000000000", ts=now_iso(), kind=kind, source="test", payload=payload or {})


def test_schedule_fire_now_reports_the_fired_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scout.scripts.schedule_tick.fire_now",
        lambda key: _event("slot.fired", {"slot_key": key}),
    )
    result = runner.invoke(cli.app, ["schedule", "fire-now", "morning-briefing"])
    assert result.exit_code == 0, result.output
    assert "fired: morning-briefing" in result.stdout


def test_schedule_fire_now_surfaces_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scout.scripts.schedule_tick.fire_now",
        lambda key: _event("slot.fire_failed", {"slot_key": key, "error": "runner missing"}),
    )
    result = runner.invoke(cli.app, ["schedule", "fire-now", "morning-briefing"])
    assert result.exit_code == 1
    assert "failed: runner missing" in result.output


def test_schedule_fire_now_failure_without_error_key_says_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scout.scripts.schedule_tick.fire_now",
        lambda key: _event("slot.fire_failed"),
    )
    result = runner.invoke(cli.app, ["schedule", "fire-now", "morning-briefing"])
    assert result.exit_code == 1
    assert "failed: unknown" in result.output


# ---------------------------------------------------------------------------
# Installer commands
#
# The bodies shell out to launchctl / pmset / crontab, so stub the script-level
# entry points and assert only the CLI's own branching and messages.
# ---------------------------------------------------------------------------


def test_install_plist_reports_installed_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_install(*, home: Path, force: bool, bootstrap: bool) -> Path:
        seen.update(force=force, bootstrap=bootstrap)
        return tmp_path / "com.scout.schedule-tick.plist"

    monkeypatch.setattr("scout.scripts.install_schedule_plist.install_plist", fake_install)

    result = runner.invoke(cli.app, ["schedule", "install-plist", "--force", "--no-bootstrap"])
    assert result.exit_code == 0, result.output
    assert "installed:" in result.stdout
    assert seen == {"force": True, "bootstrap": False}


def test_install_plist_existing_file_asks_for_force(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_kwargs: object) -> Path:
        raise FileExistsError("/Users/x/Library/LaunchAgents/com.scout.schedule-tick.plist")

    monkeypatch.setattr("scout.scripts.install_schedule_plist.install_plist", boom)

    result = runner.invoke(cli.app, ["schedule", "install-plist"])
    assert result.exit_code == 1
    assert "use --force to overwrite" in result.output


def test_install_plist_uninstall_passes_bootout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    monkeypatch.setattr(
        "scout.scripts.install_schedule_plist.uninstall_plist",
        lambda *, bootout: seen.append(bootout),
    )
    result = runner.invoke(cli.app, ["schedule", "install-plist", "--uninstall"])
    assert result.exit_code == 0, result.output
    assert "uninstalled com.scout.schedule-tick.plist" in result.stdout
    assert seen == [True]


def test_install_heartbeat_plist_all_three_branches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "scout.scripts.install_heartbeat_plist.install_plist",
        lambda **_k: tmp_path / "com.scout.heartbeat.plist",
    )
    ok = runner.invoke(cli.app, ["schedule", "install-heartbeat-plist"])
    assert ok.exit_code == 0, ok.output
    assert "installed:" in ok.stdout

    seen: list[bool] = []
    monkeypatch.setattr(
        "scout.scripts.install_heartbeat_plist.uninstall_plist",
        lambda *, bootout: seen.append(bootout),
    )
    gone = runner.invoke(cli.app, ["schedule", "install-heartbeat-plist", "--uninstall", "--no-bootstrap"])
    assert gone.exit_code == 0, gone.output
    assert "uninstalled com.scout.heartbeat.plist" in gone.stdout
    assert seen == [False]

    def boom(**_k: object) -> Path:
        raise FileExistsError("/Users/x/Library/LaunchAgents/com.scout.heartbeat.plist")

    monkeypatch.setattr("scout.scripts.install_heartbeat_plist.install_plist", boom)
    clash = runner.invoke(cli.app, ["schedule", "install-heartbeat-plist"])
    assert clash.exit_code == 1
    assert "use --force to overwrite" in clash.output


def test_install_wake_schedule_prints_ac_only_note(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scout.scripts.install_wake_schedule.install_wake_schedule",
        lambda sched, *, dry_run: f"would-wake dry_run={dry_run} slots={len(sched.keys())}",
    )
    result = runner.invoke(cli.app, ["schedule", "install-wake-schedule", "--dry-run"])
    assert result.exit_code == 0, result.output
    # The AC-only caveat is a real operational footgun; keep it in the output.
    assert "AC-only" in result.stdout
    assert "would-wake dry_run=True" in result.stdout


def test_install_wake_schedule_uninstall_skips_the_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scout.scripts.install_wake_schedule.uninstall_wake_schedule",
        lambda *, dry_run: f"removed dry_run={dry_run}",
    )
    result = runner.invoke(cli.app, ["schedule", "install-wake-schedule", "--uninstall"])
    assert result.exit_code == 0, result.output
    assert "removed dry_run=False" in result.stdout
    assert "AC-only" not in result.stdout


def test_install_wake_schedule_prefers_the_vault_schedule(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A vault schedule.yaml overrides the plugin defaults."""
    runner.invoke(cli.app, ["schedule", "init"])
    assert (vault / ".scout-state" / "schedule.yaml").exists()

    keys: list[list[str]] = []
    monkeypatch.setattr(
        "scout.scripts.install_wake_schedule.install_wake_schedule",
        lambda sched, *, dry_run: keys.append(sorted(sched)) or "ok",
    )
    result = runner.invoke(cli.app, ["schedule", "install-wake-schedule", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert keys and keys[0]


def test_install_cron_install_and_uninstall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scout.scripts.install_cron.install_cron", lambda *, home: None)
    monkeypatch.setattr("scout.scripts.install_cron.uninstall_cron", lambda *, home: None)

    added = runner.invoke(cli.app, ["schedule", "install-cron"])
    assert added.exit_code == 0, added.output
    assert "installed scout-managed crontab block" in added.stdout

    removed = runner.invoke(cli.app, ["schedule", "install-cron", "--uninstall"])
    assert removed.exit_code == 0, removed.output
    assert "removed scout-managed crontab block" in removed.stdout


def test_install_cron_surfaces_apply_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts.install_cron import CrontabApplyError

    def boom(*, home: Path) -> None:
        raise CrontabApplyError("crontab: no crontab for user")

    monkeypatch.setattr("scout.scripts.install_cron.install_cron", boom)
    result = runner.invoke(cli.app, ["schedule", "install-cron"])
    assert result.exit_code == 1
    assert "crontab apply failed: crontab: no crontab for user" in result.output


def test_install_all_on_darwin_installs_both_plists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    calls: list[str] = []
    monkeypatch.setattr(
        "scout.scripts.install_schedule_plist.install_plist",
        lambda **_k: calls.append("tick") or tmp_path / "t.plist",
    )
    monkeypatch.setattr(
        "scout.scripts.install_heartbeat_plist.install_plist",
        lambda **_k: calls.append("heartbeat") or tmp_path / "h.plist",
    )

    result = runner.invoke(cli.app, ["schedule", "install-all"])
    assert result.exit_code == 0, result.output
    assert "installed launchd plists" in result.stdout
    assert calls == ["tick", "heartbeat"]


def test_install_all_on_darwin_uninstalls_both_plists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    calls: list[str] = []
    monkeypatch.setattr(
        "scout.scripts.install_schedule_plist.uninstall_plist",
        lambda *, bootout: calls.append(f"tick:{bootout}"),
    )
    monkeypatch.setattr(
        "scout.scripts.install_heartbeat_plist.uninstall_plist",
        lambda *, bootout: calls.append(f"heartbeat:{bootout}"),
    )

    result = runner.invoke(cli.app, ["schedule", "install-all", "--uninstall"])
    assert result.exit_code == 0, result.output
    assert "uninstalled launchd plists" in result.stdout
    assert calls == ["tick:True", "heartbeat:True"]


def test_install_all_on_linux_uses_cron(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    calls: list[str] = []
    monkeypatch.setattr("scout.scripts.install_cron.install_cron", lambda *, home: calls.append("install"))
    monkeypatch.setattr("scout.scripts.install_cron.uninstall_cron", lambda *, home: calls.append("uninstall"))

    added = runner.invoke(cli.app, ["schedule", "install-all"])
    assert added.exit_code == 0, added.output
    assert "installed scout-managed crontab block" in added.stdout

    removed = runner.invoke(cli.app, ["schedule", "install-all", "--uninstall"])
    assert removed.exit_code == 0, removed.output
    assert "uninstalled scout-managed crontab block" in removed.stdout
    assert calls == ["install", "uninstall"]


def test_install_all_rejects_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    result = runner.invoke(cli.app, ["schedule", "install-all"])
    assert result.exit_code == 2
    assert "unsupported platform: Windows" in result.output


# ---------------------------------------------------------------------------
# trigger — the error/edge branches the subapp test file doesn't reach
# ---------------------------------------------------------------------------


def test_trigger_validate_target_ok(vault: Path, tmp_path: Path) -> None:
    target = tmp_path / "triggers.yaml"
    target.write_text("schema_version: 1\ntriggers: []\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["trigger", "validate", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert f"triggers OK: {target}" in result.stdout


def test_trigger_validate_target_missing_exits_one(vault: Path, tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["trigger", "validate", "--target", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1
    assert "target does not exist" in result.output


def test_trigger_validate_target_invalid_exits_one(vault: Path, tmp_path: Path) -> None:
    target = tmp_path / "triggers.yaml"
    target.write_text("schema_version: 1\ntriggers: [{id: x}]\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["trigger", "validate", "--target", str(target)])
    assert result.exit_code == 1


def test_trigger_validate_vault_file_invalid_exits_one(vault: Path) -> None:
    (vault / ".scout-state" / "triggers.yaml").write_text("schema_version: 99\ntriggers: []\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["trigger", "validate"])
    assert result.exit_code == 1


def test_trigger_stats_json_and_empty_text(vault: Path) -> None:
    empty_json = runner.invoke(cli.app, ["trigger", "stats", "--json"])
    assert empty_json.exit_code == 0, empty_json.output
    assert json.loads(empty_json.stdout) == {"days": 7, "by_trigger": {}, "by_day": {}}

    empty_text = runner.invoke(cli.app, ["trigger", "stats", "--days", "3"])
    assert empty_text.exit_code == 0, empty_text.output
    assert "no trigger fires in the last 3 day(s)" in empty_text.stdout


def test_trigger_stats_skips_blank_and_unparseable_log_lines(vault: Path) -> None:
    """Fire logs are append-only JSONL written by a live dispatcher; a torn
    write must degrade to "skip that line", never crash the roll-up."""
    import datetime as dt

    from scout.triggers.dispatcher import FIRE_LOG_PREFIX

    today = dt.datetime.now(tz=dt.UTC).date().isoformat()
    log = vault / ".scout-logs" / f"{FIRE_LOG_PREFIX}{today}.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"trigger_id": "t1", "status": "ok"}),
                "",
                "   ",
                "{not json",
                json.dumps({"trigger_id": "t1", "status": "error"}),
                json.dumps({"status": "ok"}),  # no trigger_id -> "?"
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(cli.app, ["trigger", "stats", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["by_trigger"]["t1"] == {"total": 2, "ok": 1, "error": 1}
    assert payload["by_trigger"]["?"] == {"total": 1, "ok": 1, "error": 0}
    assert payload["by_day"][today] == 3

    text = runner.invoke(cli.app, ["trigger", "stats"])
    assert "t1\ttotal=2\tok=1\terror=1" in text.stdout
    assert f"{today}\t3" in text.stdout


def test_trigger_test_rejects_unhealthy_source(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (vault / ".scout-state" / "triggers.yaml").write_text(
        "schema_version: 1\n"
        "triggers:\n"
        "  - id: t1\n"
        "    source: scout_internal\n"
        "    match: {type: slot.fire_failed}\n"
        "    action: {kind: notify, tier: info, body: hi}\n"
        "    daily_fire_cap: 3\n",
        encoding="utf-8",
    )

    class Unhealthy:
        def health_check(self) -> tuple[bool, str]:
            return False, "log dir missing"

    monkeypatch.setattr("scout.triggers.sources.get_source", lambda name, *, vault: Unhealthy())

    result = runner.invoke(cli.app, ["trigger", "test", "t1"])
    assert result.exit_code == 1
    assert "is not healthy: log dir missing" in result.output


# ---------------------------------------------------------------------------
# notify telegram — the error-mapping branches (the point of the command)
# ---------------------------------------------------------------------------


def test_notify_telegram_emits_event_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scout.scripts.notify_telegram.send",
        lambda *, tier, body, dry_run: _event("notify.sent", {"tier": tier, "dry_run": dry_run}),
    )
    result = runner.invoke(cli.app, ["notify", "telegram", "--body", "hello", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["kind"] == "notify.sent"
    assert payload["payload"] == {"tier": "info", "dry_run": True}


def test_notify_telegram_missing_secrets_uses_configerror_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runner keys off this exit code to know secrets are missing."""

    def boom(**_k: object):
        raise ConfigError("Secret file is empty: ~/.scout/telegram-token")

    monkeypatch.setattr("scout.scripts.notify_telegram.send", boom)
    result = runner.invoke(cli.app, ["notify", "telegram", "--body", "hi"])
    assert result.exit_code == ConfigError.exit_code
    assert "scoutctl notify telegram: Secret file is empty" in result.output


def test_notify_telegram_bad_tier_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_k: object):
        raise ValueError("unknown tier 'shout'")

    monkeypatch.setattr("scout.scripts.notify_telegram.send", boom)
    result = runner.invoke(cli.app, ["notify", "telegram", "--body", "hi", "--tier", "shout"])
    assert result.exit_code == 1
    assert "unknown tier 'shout'" in result.output


def test_notify_telegram_http_error_redacts_the_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """`str(HTTPError)` embeds the request URL, and the Telegram URL carries the
    bot token in its path. A 401 must not print it."""
    import requests

    class FakeResponse:
        status_code = 401
        reason = "Unauthorized"

    def boom(**_k: object):
        raise requests.HTTPError(
            "401 Client Error: Unauthorized for url: https://api.telegram.org/botSUPERSECRET123/sendMessage",
            response=FakeResponse(),  # type: ignore[arg-type]
        )

    monkeypatch.setattr("scout.scripts.notify_telegram.send", boom)
    result = runner.invoke(cli.app, ["notify", "telegram", "--body", "hi"])
    assert result.exit_code == 2
    assert "HTTP 401 Unauthorized (token redacted in URL)" in result.output
    assert "SUPERSECRET123" not in result.output


def test_notify_telegram_http_error_without_response_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    monkeypatch.setattr(
        "scout.scripts.notify_telegram.send",
        lambda **_k: (_ for _ in ()).throw(requests.HTTPError("boom", response=None)),  # type: ignore[arg-type]
    )
    result = runner.invoke(cli.app, ["notify", "telegram", "--body", "hi"])
    assert result.exit_code == 2
    assert "HTTP ? Unknown" in result.output


def test_notify_telegram_transport_error_exits_two(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    monkeypatch.setattr(
        "scout.scripts.notify_telegram.send",
        lambda **_k: (_ for _ in ()).throw(requests.ConnectTimeout("connect timed out")),
    )
    result = runner.invoke(cli.app, ["notify", "telegram", "--body", "hi"])
    assert result.exit_code == 2
    assert "HTTP error: connect timed out" in result.output


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------


def _install_argv(*extra: str) -> list[str]:
    return [
        "bootstrap",
        "install",
        "--user-name",
        "Alex",
        "--user-email",
        "alex@example.com",
        "--instance-name",
        "TestScout",
        "--no-jobs",
        "--skip-claude",
        *extra,
    ]


def test_bootstrap_install_creates_the_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """install() refuses a pre-existing vault, so point SCOUT_DATA_DIR at a
    path it gets to create."""
    target = tmp_path / "FreshScout"
    monkeypatch.setenv("SCOUT_DATA_DIR", str(target))

    result = runner.invoke(cli.app, _install_argv("--connectors", "github, slack ,"))
    # Exit code is the doctor's — a fresh test vault may legitimately warn.
    assert result.exit_code in (0, 1), result.output
    assert f"installed: {target}" in result.stdout
    assert "doctor:" in result.stdout

    config = (target / "scout-config.yaml").read_text()
    assert "TestScout" in config
    # Comma-separated connectors are split and stripped, blanks dropped.
    assert "github" in config and "slack" in config


def test_bootstrap_install_forwards_connector_inputs(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The "regen ships placeholders" bug was a dropped connector_inputs dict —
    assert every flag lands in the BootstrapConfig."""
    seen: dict[str, object] = {}

    def fake_install(cfg):
        seen["inputs"] = dict(cfg.connector_inputs)
        seen["instance_name_lower"] = cfg.instance_name_lower
        seen["plugin_root"] = cfg.plugin_root

        class R:
            vault = cfg.vault

            class doctor:
                class severity:
                    value = "ok"

                warnings: list[str] = []
                errors: list[str] = []
                exit_code = 0

        return R()

    monkeypatch.setattr("scout.scripts.bootstrap.install", fake_install)

    result = runner.invoke(
        cli.app,
        _install_argv(
            "--user-slack-id",
            "U123",
            "--github-username",
            "alex",
            "--github-repos",
            "example-org/repo",
            "--claude-bin",
            "/opt/claude",
            "--max-budget",
            "9.50",
        ),
    )
    assert result.exit_code == 0, result.output
    assert seen["inputs"] == {
        "user_slack_id": "U123",
        "github_username": "alex",
        "github_repos": "example-org/repo",
        "claude_bin": "/opt/claude",
        "max_budget": "9.50",
    }
    # "TestScout" -> "testscout"; spaces become dashes.
    assert seen["instance_name_lower"] == "testscout"
    assert seen["plugin_root"] == PLUGIN_ROOT


def test_bootstrap_install_reports_doctor_warnings_and_errors(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_install(cfg):
        class R:
            vault = cfg.vault

            class doctor:
                class severity:
                    value = "error"

                warnings = ["schedule.yaml not seeded"]
                errors = ["claude binary not found"]
                exit_code = 1

        return R()

    monkeypatch.setattr("scout.scripts.bootstrap.install", fake_install)
    result = runner.invoke(cli.app, _install_argv())
    assert result.exit_code == 1
    assert "warning: schedule.yaml not seeded" in result.output
    assert "error: claude binary not found" in result.output


def test_bootstrap_upgrade_reads_existing_config(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (vault / "scout-config.yaml").write_text(
        "instance:\n"
        "  name: TestScout\n"
        "  name_lower: testscout\n"
        "user:\n"
        "  name: Alex\n"
        "  email: alex@example.com\n"
        "timezone: Europe/Prague\n"
        "platform: linux\n"
        "connectors:\n"
        "  enabled: [github]\n"
        "  inputs: {github_username: alex}\n",
        encoding="utf-8",
    )
    seen: dict[str, object] = {}

    def fake_upgrade(cfg):
        seen.update(
            instance_name=cfg.instance_name,
            timezone=cfg.timezone,
            platform=cfg.platform,
            connectors=set(cfg.enabled_connectors),
            inputs=dict(cfg.connector_inputs),
            skip_jobs=cfg.skip_jobs,
            skip_claude=cfg.skip_claude,
        )

        class R:
            vault = cfg.vault
            conflicts = ["SKILL.md"]
            backups = ["SKILL.md.bak.2026-04-15"]

            class doctor:
                class severity:
                    value = "warn"

                exit_code = 0

        return R()

    monkeypatch.setattr("scout.scripts.bootstrap.upgrade", fake_upgrade)

    result = runner.invoke(cli.app, ["bootstrap", "upgrade", "--no-jobs", "--skip-claude"])
    assert result.exit_code == 0, result.output
    assert f"upgraded: {vault}" in result.stdout
    assert "conflict (sidecar): SKILL.md" in result.output
    assert "backup: SKILL.md.bak.2026-04-15" in result.output
    assert "doctor: warn" in result.stdout
    assert seen["timezone"] == "Europe/Prague"
    assert seen["platform"] == "linux"
    assert seen["connectors"] == {"github"}
    assert seen["inputs"] == {"github_username": "alex"}
    assert seen["skip_jobs"] is True and seen["skip_claude"] is True


def test_bootstrap_upgrade_without_a_vault_exits_two(vault: Path) -> None:
    result = runner.invoke(cli.app, ["bootstrap", "upgrade"])
    assert result.exit_code == 2
    assert "run /scout-setup" in result.output


def test_bootstrap_upgrade_empty_config_falls_back_to_defaults(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty (but parseable) scout-config.yaml must not crash — `or {}`."""
    (vault / "scout-config.yaml").write_text("", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_upgrade(cfg):
        seen.update(instance_name=cfg.instance_name, user_name=cfg.user_name, timezone=cfg.timezone)

        class R:
            vault = cfg.vault
            conflicts: list[str] = []
            backups: list[str] = []

            class doctor:
                class severity:
                    value = "ok"

                exit_code = 0

        return R()

    monkeypatch.setattr("scout.scripts.bootstrap.upgrade", fake_upgrade)
    result = runner.invoke(cli.app, ["bootstrap", "upgrade"])
    assert result.exit_code == 0, result.output
    assert seen == {"instance_name": "Scout", "user_name": "", "timezone": "America/New_York"}


def test_bootstrap_doctor_reports_severity_and_findings(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_run_doctor(*, vault: Path, check_jobs: bool):
        seen.update(vault=vault, check_jobs=check_jobs)

        class R:
            class severity:
                value = "warn"

            warnings = ["no recent session"]
            errors = ["missing knowledge-base/"]
            exit_code = 0

        return R()

    monkeypatch.setattr("scout.scripts.bootstrap_doctor.run_doctor", fake_run_doctor)

    result = runner.invoke(cli.app, ["bootstrap", "doctor"])
    assert result.exit_code == 0, result.output
    assert "severity: warn" in result.stdout
    assert "warning: no recent session" in result.stdout
    assert "error: missing knowledge-base/" in result.output
    assert seen["check_jobs"] is True

    runner.invoke(cli.app, ["bootstrap", "doctor", "--no-jobs"])
    assert seen["check_jobs"] is False


def test_bootstrap_migrate_legacy_reports_snapshots_and_backups(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_migrate(cfg):
        seen.update(skip_jobs=cfg.skip_jobs, skip_claude=cfg.skip_claude, inputs=dict(cfg.connector_inputs))

        class R:
            vault = cfg.vault
            snapshots_recorded = ["SKILL", "DREAMING"]
            backups = ["run-briefing.sh.bak.2026-04-15"]

            class doctor:
                class severity:
                    value = "ok"

                warnings = ["legacy runner backed up"]
                errors: list[str] = []
                exit_code = 0

        return R()

    monkeypatch.setattr("scout.scripts.bootstrap.migrate_legacy", fake_migrate)

    result = runner.invoke(
        cli.app,
        ["bootstrap", "migrate-legacy", "--user-name", "Alex", "--user-email", "alex@example.com"],
    )
    assert result.exit_code == 0, result.output
    assert f"migrated: {vault}" in result.stdout
    assert "snapshots recorded: SKILL, DREAMING" in result.stdout
    assert "backup: run-briefing.sh.bak.2026-04-15" in result.stdout
    assert "warning: legacy runner backed up" in result.output
    # migrate-legacy defaults to --no-jobs and never runs Claude.
    assert seen["skip_jobs"] is True
    assert seen["skip_claude"] is True


def test_bootstrap_migrate_legacy_rebootstrap_jobs_flips_skip_jobs(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    def fake_migrate(cfg):
        seen["skip_jobs"] = cfg.skip_jobs

        class R:
            vault = cfg.vault
            snapshots_recorded: list[str] = []
            backups: list[str] = []

            class doctor:
                class severity:
                    value = "ok"

                warnings: list[str] = []
                errors: list[str] = []
                exit_code = 0

        return R()

    monkeypatch.setattr("scout.scripts.bootstrap.migrate_legacy", fake_migrate)
    result = runner.invoke(
        cli.app,
        [
            "bootstrap",
            "migrate-legacy",
            "--user-name",
            "Alex",
            "--user-email",
            "alex@example.com",
            "--rebootstrap-jobs",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["skip_jobs"] is False
    assert "snapshots recorded: none" in result.stdout


# ---------------------------------------------------------------------------
# phases backport
# ---------------------------------------------------------------------------


def _write_vault_config(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text(
        "instance:\n  name: TestScout\n  name_lower: testscout\n"
        "user:\n  name: Alex\n  email: alex@example.com\n"
        "connectors:\n  enabled: []\n  inputs: {}\n",
        encoding="utf-8",
    )


def test_phases_backport_without_a_vault_exits_two(vault: Path) -> None:
    result = runner.invoke(cli.app, ["phases", "backport"])
    assert result.exit_code == 2
    assert "run /scout-setup" in result.output


def test_phases_backport_malformed_config_exits_configerror(vault: Path) -> None:
    (vault / "scout-config.yaml").write_text("instance: [unclosed\n", encoding="utf-8")
    result = runner.invoke(cli.app, ["phases", "backport"])
    assert result.exit_code == ConfigError.exit_code
    assert "scout-config.yaml is malformed" in result.output


def test_phases_backport_skips_kinds_with_no_snapshot(vault: Path) -> None:
    _write_vault_config(vault)
    result = runner.invoke(cli.app, ["phases", "backport", "--kind", "SKILL"])
    assert result.exit_code == 0, result.output
    assert "SKILL: skip — missing snapshot file" in result.output
    assert "dry-run" in result.stdout


def test_phases_backport_skips_when_only_the_live_file_is_missing(vault: Path) -> None:
    _write_vault_config(vault)
    snap_dir = vault / ".scout-state" / "last-assembled"
    snap_dir.mkdir(parents=True)
    (snap_dir / "SKILL.md").write_text("# SKILL\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["phases", "backport", "--kind", "skill"])
    assert result.exit_code == 0, result.output
    assert "SKILL: skip — missing live file" in result.output


def test_phases_backport_all_visits_three_kinds(vault: Path) -> None:
    _write_vault_config(vault)
    result = runner.invoke(cli.app, ["phases", "backport"])
    assert result.exit_code == 0, result.output
    for kind in ("SKILL", "DREAMING", "RESEARCH"):
        assert f"{kind}: skip" in result.output


def test_phases_backport_reports_unchanged_files(vault: Path) -> None:
    """Snapshot == live means zero hunks: a clean 0-hunk report, not a crash."""
    _write_vault_config(vault)
    snap_dir = vault / ".scout-state" / "last-assembled"
    snap_dir.mkdir(parents=True)
    body = "# SKILL\n\nunchanged body\n"
    (snap_dir / "SKILL.md").write_text(body, encoding="utf-8")
    (vault / "SKILL.md").write_text(body, encoding="utf-8")

    result = runner.invoke(cli.app, ["phases", "backport", "--kind", "SKILL"])
    assert result.exit_code == 0, result.output
    assert "phases backport — SKILL (0 hunk(s)" in result.stdout


def test_phases_backport_honours_explicit_vault_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.setenv("SCOUT_DATA_DIR", str(decoy))

    real = tmp_path / "Real"
    (real / ".scout-state").mkdir(parents=True)
    _write_vault_config(real)

    result = runner.invoke(cli.app, ["phases", "backport", "--vault", str(real), "--kind", "SKILL"])
    assert result.exit_code == 0, result.output
    assert "SKILL: skip" in result.output


def test_phases_backport_apply_writes_the_applied_hunks(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--apply must round-trip each applied hunk back into its phase fragment.
    The planner is stubbed so the test pins the CLI's write loop, not the
    re-templatizer (covered by test_phase_backport.py)."""
    _write_vault_config(vault)
    snap_dir = vault / ".scout-state" / "last-assembled"
    snap_dir.mkdir(parents=True)
    (snap_dir / "SKILL.md").write_text("# SKILL\n\nold\n", encoding="utf-8")
    (vault / "SKILL.md").write_text("# SKILL\n\nnew\n", encoding="utf-8")

    fragment = vault / "fragment.md"
    fragment.write_text("## Section\n\nRAW BODY\n", encoding="utf-8")

    class Section:
        phase_file = fragment
        section_name = "Section"
        raw_body = "RAW BODY"
        rendered_body = "RAW BODY"

    class Result:
        status = "applied"
        phase_file = fragment
        section_name = "Section"
        added = ["new"]
        anchor = "old"
        retemplatized = "new"
        reason = ""
        risky_hits: list[str] = []

    monkeypatch.setattr("scout.scripts.phase_backport.build_rendered_sections", lambda *a, **k: [Section()])
    monkeypatch.setattr("scout.scripts.phase_backport.plan_backport", lambda *a, **k: [Result()])
    monkeypatch.setattr("scout.scripts.phase_backport.apply_section_edits", lambda raw, rendered, edits: "EDITED BODY")
    monkeypatch.setattr(
        "scout.scripts.phase_backport.apply_to_phase_text",
        lambda text, raw, edited: text.replace(raw, edited),
    )

    result = runner.invoke(cli.app, ["phases", "backport", "--kind", "SKILL", "--apply"])
    assert result.exit_code == 0, result.output
    assert "✓ applied" in result.stdout
    assert f"→ wrote {fragment}" in result.stdout
    assert "Applied 1 hunk(s) to phases/." in result.stdout
    assert "EDITED BODY" in fragment.read_text()


def test_phases_backport_renders_needs_review_and_unmapped_rows(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_vault_config(vault)
    snap_dir = vault / ".scout-state" / "last-assembled"
    snap_dir.mkdir(parents=True)
    (snap_dir / "SKILL.md").write_text("# SKILL\n\nold\n", encoding="utf-8")
    (vault / "SKILL.md").write_text("# SKILL\n\nnew\n", encoding="utf-8")

    class Review:
        status = "needs-review"
        phase_file = Path("phases/skill/10-intro.md")
        section_name = "Intro"
        reason = "anchor ambiguous"
        risky_hits = ["hardcoded path"]
        added: list[str] = []

    class ReviewNoFile:
        status = "needs-review"
        phase_file = None
        section_name = ""
        reason = "no matching section"
        risky_hits: list[str] = []
        added: list[str] = []

    class Unmapped:
        status = "unmapped"
        phase_file = None
        section_name = ""
        reason = "no fragment owns this text"
        risky_hits: list[str] = []
        added = ["x" * 80]

    monkeypatch.setattr("scout.scripts.phase_backport.build_rendered_sections", lambda *a, **k: [])
    monkeypatch.setattr(
        "scout.scripts.phase_backport.plan_backport",
        lambda *a, **k: [Review(), ReviewNoFile(), Unmapped()],
    )

    result = runner.invoke(cli.app, ["phases", "backport", "--kind", "SKILL"])
    assert result.exit_code == 0, result.output
    assert "⚠ needs-review phases/skill/10-intro.md «Intro» — anchor ambiguous [risky: hardcoded path]" in result.stdout
    assert "⚠ needs-review — — no matching section" in result.stdout
    assert "✗ unmapped     no fragment owns this text" in result.stdout
    # The unmapped snippet is truncated to 60 chars + ellipsis.
    assert "x" * 60 + "…" in result.stdout


def test_phases_backport_apply_with_no_applied_hunks_writes_nothing(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_vault_config(vault)
    snap_dir = vault / ".scout-state" / "last-assembled"
    snap_dir.mkdir(parents=True)
    (snap_dir / "SKILL.md").write_text("# SKILL\n\nold\n", encoding="utf-8")
    (vault / "SKILL.md").write_text("# SKILL\n\nnew\n", encoding="utf-8")

    monkeypatch.setattr("scout.scripts.phase_backport.build_rendered_sections", lambda *a, **k: [])
    monkeypatch.setattr("scout.scripts.phase_backport.plan_backport", lambda *a, **k: [])

    result = runner.invoke(cli.app, ["phases", "backport", "--kind", "SKILL", "--apply"])
    assert result.exit_code == 0, result.output
    assert "Applied 0 hunk(s) to phases/." in result.stdout
    assert "→ wrote" not in result.stdout


# ---------------------------------------------------------------------------
# self-update check / tui
# ---------------------------------------------------------------------------


def test_self_update_check_text_when_update_available(monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts.self_update import UpdateStatus

    monkeypatch.setattr(
        "scout.scripts.self_update.check",
        lambda: UpdateStatus(installed="0.8.0", available="0.9.0", update_available=True),
    )
    result = runner.invoke(cli.app, ["self-update", "check"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "update available: 0.8.0 -> 0.9.0"


def test_self_update_check_text_when_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts.self_update import UpdateStatus

    monkeypatch.setattr(
        "scout.scripts.self_update.check",
        lambda: UpdateStatus(installed="0.8.0", available="0.8.0", update_available=False),
    )
    result = runner.invoke(cli.app, ["self-update", "check"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "up to date (0.8.0)"


def test_self_update_check_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from scout.scripts.self_update import UpdateStatus

    monkeypatch.setattr(
        "scout.scripts.self_update.check",
        lambda: UpdateStatus(installed="0.8.0", available="0.9.0", update_available=True),
    )
    result = runner.invoke(cli.app, ["self-update", "check", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {
        "installed": "0.8.0",
        "available": "0.9.0",
        "update_available": True,
    }


def test_tui_runs_the_textual_app(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patching scout.tui.app imports it, and that needs textual — which lives
    # in the [full] extra, not [dev]. The no-textual path is covered by
    # test_tui_without_textual_raises_actionable_error below.
    pytest.importorskip("textual")

    runs: list[str] = []

    class FakeApp:
        def run(self) -> None:
            runs.append("ran")

    monkeypatch.setattr("scout.tui.app.ScoutApp", FakeApp)
    result = runner.invoke(cli.app, ["tui"])
    assert result.exit_code == 0, result.output
    assert runs == ["ran"]


def test_tui_without_textual_raises_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """textual lives in the [full] extra; a [dev]-only install must get an
    install hint, not a bare ImportError traceback."""
    import builtins

    from scout.errors import ActionItemError

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "scout.tui.app":
            raise ImportError("No module named 'textual'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = runner.invoke(cli.app, ["tui"])
    assert result.exit_code != 0
    assert isinstance(result.exception, ActionItemError)
    assert ".[full]" in str(result.exception)
