"""Last-mile branches across the two Stop-hook helpers, the schedule loader,
and the phase/probe/cron/plist/merge scripts.

Everything here is a failure or fallback path the mainline test files don't
reach. Two themes:

* **Hooks must never raise.** `session_tokens` and `kb_pre_filter` run at the
  end of / start of every session, so an unreadable transcript, an unwritable
  tracker, or one malformed KB file must degrade to an empty value.
* **Loaders must fail loudly.** `schedule.py` and the phase/probe parsers gate
  what the scheduler and the assembled brain files contain, so a malformed
  YAML has to raise a named error rather than silently yield an empty config.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scout.errors import ConfigError
from scout.hooks import connector_log, kb_pre_filter
from scout.hooks import session_tokens as stok
from scout.schedule import load_default_schedule, load_schedule, next_fires

# ---------------------------------------------------------------------------
# session_tokens helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["", None])
def test_an_empty_model_is_not_a_known_family(model: str | None) -> None:
    assert stok._is_known_model(model or "") is False
    # ...and prices conservatively as Opus.
    assert stok._model_family(model) == "claude-opus"


def test_an_unknown_model_prices_as_opus() -> None:
    """Under-charging a session would let it slip past the budget gate, so an
    unrecognized model falls back to the most expensive family."""
    assert stok._model_family("claude-experimental-42") == "claude-opus"
    assert stok._is_known_model("claude-experimental-42") is False


def test_the_tracker_path_defaults_to_the_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_TOKENS_TRACKER", raising=False)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    assert stok._tracker_path() == tmp_path / ".scout-logs" / "session-tokens.jsonl"


def test_the_tracker_path_honours_the_override_and_expands_a_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", "~/custom-tracker.jsonl")
    assert stok._tracker_path() == tmp_path / "custom-tracker.jsonl"


def test_reading_usage_turns_skips_blank_and_malformed_lines(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        "\n".join(
            [
                "",
                "   ",
                "{torn",
                json.dumps({"message": {"role": "assistant"}}),  # no usage
                json.dumps({"message": {"usage": None}}),  # explicit null usage
                json.dumps({"message": "not a dict"}),
                json.dumps({"message": {"model": "claude-opus-4", "usage": {"input_tokens": 5}}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    turns = stok._read_usage_turns(transcript)
    assert len(turns) == 1
    assert turns[0]["message"]["usage"]["input_tokens"] == 5


def test_reading_usage_turns_is_empty_for_an_unreadable_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"message": {"usage": {}}}) + "\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert stok._read_usage_turns(transcript) == []


def test_polling_gives_up_after_the_configured_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude Code can fire Stop before the final assistant turn lands on disk;
    the poll gives that write a moment, then reports no usage."""
    transcript = tmp_path / "missing.jsonl"
    slept: list[float] = []
    monkeypatch.setattr(stok.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(stok, "_POLL_ATTEMPTS", 3)
    monkeypatch.setattr(stok, "_POLL_INTERVAL_S", 0.05)

    assert stok._poll_for_usage_turns(transcript) == []
    # Sleeps between attempts only.
    assert slept == [0.05, 0.05]


def test_polling_returns_on_the_first_hit_without_sleeping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"message": {"model": "claude-opus-4", "usage": {"input_tokens": 1}}}) + "\n",
        encoding="utf-8",
    )

    def fail(_s: float) -> None:
        raise AssertionError("the happy path must not sleep")

    monkeypatch.setattr(stok.time, "sleep", fail)
    assert len(stok._poll_for_usage_turns(transcript)) == 1


def test_the_primary_model_is_empty_when_no_turn_names_one() -> None:
    assert stok._primary_model([]) == ""
    assert stok._primary_model([{"message": {"usage": {}}}]) == ""


def test_the_primary_model_is_the_most_frequent() -> None:
    turns = [
        {"message": {"model": "claude-opus-4"}},
        {"message": {"model": "claude-sonnet-4"}},
        {"message": {"model": "claude-opus-4"}},
    ]
    assert stok._primary_model(turns) == "claude-opus-4"


def test_the_first_unknown_model_is_reported_in_transcript_order() -> None:
    turns = [
        {"message": {"model": "claude-opus-4"}},
        {"message": {"model": "claude-experimental-42"}},
        {"message": {"model": "claude-experimental-99"}},
    ]
    assert stok._first_unknown_model(turns) == "claude-experimental-42"
    assert stok._first_unknown_model([{"message": {"model": "claude-haiku-4-5"}}]) == ""


def test_appending_a_row_never_raises_on_an_unwritable_tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tmp_path / "logs" / "session-tokens.jsonl"))

    def boom(*_a: object, **_k: object):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "open", boom)
    stok._append_row({"ts": "2026-05-28T14:00:00Z"})  # must not raise


def test_run_returns_none_for_a_non_object_payload() -> None:
    import io

    assert stok.run(stdin=io.StringIO('"just a string"')) is None
    assert stok.run(stdin=io.StringIO("[1, 2]")) is None


def test_run_reads_real_stdin_when_none_is_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert stok.run() is None


def test_main_returns_zero_even_when_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Stop hook's exit code gates the session; it must always be 0."""

    def boom(**_k: object):
        raise RuntimeError("unreachable state")

    monkeypatch.setattr(stok, "run", boom)
    assert stok.main() == 0


def test_main_returns_zero_on_the_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    tracker = tmp_path / "session-tokens.jsonl"
    monkeypatch.setenv("SESSION_TOKENS_TRACKER", str(tracker))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"message": {"model": "claude-opus-4", "usage": {"input_tokens": 10}}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "s1", "transcript_path": str(transcript)})))
    assert stok.main() == 0
    assert json.loads(tracker.read_text().strip())["input_tokens"] == 10


# ---------------------------------------------------------------------------
# kb_pre_filter helpers
# ---------------------------------------------------------------------------


def test_reading_a_head_is_empty_for_an_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "kb.md"
    f.write_text("Last updated: 2026-04-15\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "open", boom)
    assert kb_pre_filter._read_head(f) == []


def test_reading_a_head_stops_at_the_scan_limit(tmp_path: Path) -> None:
    """The date line lives near the top; scanning a long KB file in full would
    blow the session-start budget."""
    f = tmp_path / "kb.md"
    f.write_text("\n".join(f"line {n}" for n in range(200)) + "\n", encoding="utf-8")
    assert len(kb_pre_filter._read_head(f)) == kb_pre_filter.HEAD_SCAN_LINES
    assert kb_pre_filter._read_head(f, n=3) == ["line 0", "line 1", "line 2"]


def test_the_freshness_override_falls_back_to_the_default(tmp_path: Path) -> None:
    """A file with no `freshness:` marker in its head uses the global default."""
    f = tmp_path / "kb.md"
    f.write_text("# People\n\nno freshness marker here\n", encoding="utf-8")
    assert kb_pre_filter.freshness_hours_for(f) == kb_pre_filter.DEFAULT_FRESHNESS_HOURS


def test_the_kb_walk_is_empty_without_a_knowledge_base_dir(tmp_path: Path) -> None:
    assert kb_pre_filter.discover_kb_files(tmp_path / "nope") == []


def test_the_kb_walk_takes_only_markdown_files(tmp_path: Path) -> None:
    kb = tmp_path / "knowledge-base"
    kb.mkdir()
    (kb / "people.md").write_text("# People\n", encoding="utf-8")
    (kb / "notes.txt").write_text("not markdown\n", encoding="utf-8")
    # A *directory* named like a markdown file must not be walked as one.
    (kb / "a-directory.md").mkdir()

    names = [p.name for p in kb_pre_filter.discover_kb_files(tmp_path)]
    assert names == ["people.md"]


def test_one_unclassifiable_file_does_not_block_the_rest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter runs at session start over the whole KB; a single bad file
    must degrade to NO_DATE rather than losing the report."""
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    kb = tmp_path / "knowledge-base"
    kb.mkdir()
    bad = kb / "bad.md"
    bad.write_text("# Bad\n", encoding="utf-8")
    (kb / "good.md").write_text("Last updated: 2026-04-15\n", encoding="utf-8")

    real_classify = kb_pre_filter.classify

    def maybe_boom(path: Path, now, scout_dir):
        if path == bad:
            raise RuntimeError("classifier blew up")
        return real_classify(path, now, scout_dir)

    monkeypatch.setattr(kb_pre_filter, "classify", maybe_boom)

    event = kb_pre_filter.run("dreaming")
    assert event is not None
    # Both files are accounted for; the bad one lands in the NO_DATE bucket.
    assert event.payload["stale"] + event.payload["no_date"] + event.payload["fresh"] == 2
    assert event.payload["no_date"] >= 1


def test_run_returns_none_without_a_knowledge_base_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    assert kb_pre_filter.run("dreaming") is None


def test_writing_the_cache_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOUT_DATA_DIR", str(tmp_path))
    kb = tmp_path / "knowledge-base"
    kb.mkdir()
    (kb / "people.md").write_text("Last updated: 2026-04-15\n", encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", boom)
    event = kb_pre_filter.run("dreaming")  # must not raise
    assert event is not None


# ---------------------------------------------------------------------------
# schedule loader
# ---------------------------------------------------------------------------


def test_loading_a_missing_schedule_is_a_named_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_schedule(tmp_path / "nope.yaml")


def test_loading_malformed_schedule_yaml_is_a_named_config_error(tmp_path: Path) -> None:
    p = tmp_path / "schedule.yaml"
    p.write_text("slots: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="is malformed"):
        load_schedule(p)


@pytest.mark.parametrize("body", ["- a\n- list\n", "just a string\n", "42\n"])
def test_a_non_mapping_schedule_is_a_named_config_error(tmp_path: Path, body: str) -> None:
    p = tmp_path / "schedule.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match="is not a mapping"):
        load_schedule(p)


def test_a_slot_naming_an_unknown_timezone_is_rejected(tmp_path: Path) -> None:
    """A slot's `tz` decides when it fires; an unloadable zone must fail the
    load rather than silently resolve to UTC."""
    p = tmp_path / "schedule.yaml"
    p.write_text(
        "schema_version: 1\n"
        "slots:\n"
        "  morning-briefing:\n"
        "    type: briefing\n"
        "    runner: run-scout.sh\n"
        "    fires_at_local: '08:30'\n"
        "    weekdays: [Mon]\n"
        "    tz: Mars/Olympus_Mons\n"
        "    on_miss: fire\n"
        "    missed_window_hours: 4\n"
        "    cooldown_minutes: 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown tz 'Mars/Olympus_Mons'"):
        load_schedule(p)


def test_target_today_requires_a_tz_aware_now() -> None:
    """A naive `now` would compare against a tz-aware target and silently shift
    the whole schedule."""
    slot = next(iter(load_default_schedule().values()))
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        slot.target_today(now=_dt.datetime(2026, 5, 28, 14, 0))


def test_next_fires_requires_a_tz_aware_now() -> None:
    with pytest.raises(ValueError, match="now must be timezone-aware"):
        next_fires(load_default_schedule(), now=_dt.datetime(2026, 5, 28, 14, 0), window_hours=24)


def test_next_fires_finds_the_upcoming_target_for_every_slot() -> None:
    sched = load_default_schedule()
    now = _dt.datetime(2026, 5, 28, 6, 0, tzinfo=ZoneInfo("America/New_York"))
    fires = dict(next_fires(sched, now=now, window_hours=24 * 8))
    assert set(fires) == set(sched)
    assert all(dt > now for dt in fires.values())


def test_next_fires_is_empty_for_a_zero_window() -> None:
    now = _dt.datetime(2026, 5, 28, 6, 0, tzinfo=ZoneInfo("America/New_York"))
    assert next_fires(load_default_schedule(), now=now, window_hours=0) == []


def test_an_overlay_with_a_bad_schema_version_is_rejected(tmp_path: Path) -> None:
    """The overlay is a user-editable file; loading it as v1 when it declares
    something else would silently drop whatever the new shape added."""
    import shutil

    from scout import cli

    canonical = tmp_path / "schedule.yaml"
    shutil.copy2(Path(cli.__file__).parent / "defaults" / "schedule.yaml", canonical)
    overlay = tmp_path / "schedule.local.yaml"
    overlay.write_text("schema_version: 99\nslots: {}\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="schema_version 99; engine supports 1"):
        load_schedule(canonical, overlay=overlay)


# ---------------------------------------------------------------------------
# connector_log.classify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected"),
    [
        ("Bash", {"command": "gh pr list"}, "github"),
        ("Bash", {"command": "  git status  "}, "bash:git"),
        ("Bash", {"command": ""}, "bash"),
        ("Bash", {}, "bash"),
        ("Bash", {"command": None}, "bash"),
        ("mcp__slack__slack_read_channel", {}, "mcp:slack"),
        ("mcp__linear__list_issues", {}, "mcp:linear"),
        # No `__` separator at all -> plain lowercase.
        ("mcpsomething", {}, "mcpsomething"),
        ("Read", {}, "read"),
        ("WebFetch", {}, "webfetch"),
    ],
)
def test_classify_maps_a_tool_call_to_a_connector_key(tool_name: str, tool_input: dict, expected: str) -> None:
    """These keys are the join column between the tool log and the connector
    roster; a drift here silently orphans a connector's health row."""
    assert connector_log.classify(tool_name, tool_input) == expected


def test_fcntl_is_optional_at_import_time() -> None:
    """The module guards the fcntl import for non-POSIX hosts. On POSIX it is
    present; the guard's other arm is exercised by monkeypatching it to None
    (see test_registry_idmap_and_log_edges.py)."""
    assert hasattr(connector_log, "fcntl")


# ---------------------------------------------------------------------------
# phase_assembly / phase_backport / connector_probes parsers
# ---------------------------------------------------------------------------


def test_a_phase_file_without_frontmatter_is_rejected(tmp_path: Path) -> None:
    from scout.scripts.phase_assembly import parse_phase_file

    p = tmp_path / "10-intro.md"
    p.write_text("# No frontmatter here\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must start with '---' frontmatter fence"):
        parse_phase_file(p)


def test_a_phase_section_with_a_non_list_mode_is_rejected(tmp_path: Path) -> None:
    from scout.scripts.phase_assembly import parse_phase_file

    p = tmp_path / "10-intro.md"
    p.write_text(
        "---\nid: intro\nmode: briefing\n---\n\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="'mode' must be a YAML list, got str"):
        parse_phase_file(p)


def test_backport_rejects_an_unknown_brain_file_kind(tmp_path: Path) -> None:
    from scout.scripts.phase_backport import build_rendered_sections

    with pytest.raises(ValueError, match="unknown brain-file kind: 'SKILLZ'"):
        build_rendered_sections(tmp_path, "SKILLZ", {}, set())


def test_backport_skips_a_phase_file_it_cannot_parse(tmp_path: Path) -> None:
    """One hand-broken fragment must not abort the whole back-port scan."""
    from scout.scripts.phase_backport import _ASSEMBLY_MAP, build_rendered_sections

    src_dirs, _modes = _ASSEMBLY_MAP["SKILL"]
    phase_dir = tmp_path / src_dirs[0]
    phase_dir.mkdir(parents=True)
    (phase_dir / "10-broken.md").write_text("no frontmatter at all\n", encoding="utf-8")

    assert build_rendered_sections(tmp_path, "SKILL", {}, set()) == []


def test_a_probe_registry_entry_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    from scout.scripts.connector_probes import load_registry

    p = tmp_path / "connector-probes.yaml"
    # Top level is the connector map itself (no "connectors:" wrapper).
    p.write_text("slack: just-a-string\n", encoding="utf-8")
    with pytest.raises(ValueError, match="connector 'slack': expected mapping, got str"):
        load_registry(p)


def test_an_invalid_shipped_probe_registry_is_a_named_config_error(tmp_path: Path) -> None:
    """The shipped registry is a plugin asset; an invalid one is a packaging
    bug, and the error must name the file rather than surface a bare
    ValueError from the YAML layer."""
    from scout.scripts import connector_probes

    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "connector-probes.yaml").write_text("slack: just-a-string\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="shipped connector-probes.yaml is invalid"):
        connector_probes.resolve_registry(plugin_root=tmp_path, data_dir=tmp_path)


def test_a_missing_shipped_probe_registry_is_a_named_config_error(tmp_path: Path) -> None:
    from scout.scripts import connector_probes

    with pytest.raises(ConfigError, match="shipped connector-probes.yaml not found"):
        connector_probes.resolve_registry(plugin_root=tmp_path, data_dir=tmp_path)


def test_the_connectors_snapshot_holds_only_official_connectors(tmp_path: Path) -> None:
    """The snapshot is scout-app's bundled default roster; community/
    auto-discovered rows are per-machine and must not ship in it."""
    from scout.connectors import Tier, load_registry
    from scout.scripts.connectors_snapshot import build_snapshot

    state = tmp_path / ".scout-state"
    state.mkdir(parents=True)
    (state / "connectors.local.yaml").write_text(
        "connectors:\n"
        "  house-sensor:\n"
        "    display_name: House Sensor\n"
        "    tier: community\n"
        "    capabilities: [inbound]\n",
        encoding="utf-8",
    )
    # Sanity: the overlay really is visible to the registry loader.
    assert load_registry(tmp_path)["house-sensor"].tier is Tier.COMMUNITY

    snapshot = build_snapshot()
    keys = {row["key"] for row in snapshot["connectors"]}
    assert "house-sensor" not in keys


# ---------------------------------------------------------------------------
# three_way_merge / install_cron / launchd bootout
# ---------------------------------------------------------------------------


def test_a_signal_killed_git_merge_file_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git merge-file returns the conflict count (0-127); anything outside that
    means the process died, and treating it as "0 conflicts" would write a
    silently-wrong merge."""
    from scout.scripts import three_way_merge

    class Proc:
        returncode = -9
        stderr = "Killed"
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(RuntimeError, match="git merge-file exited -9"):
        three_way_merge.three_way_merge(base="a\n", ours="b\n", theirs="c\n")


def test_a_backup_write_failure_does_not_mask_a_successful_crontab_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scout.scripts import install_cron

    monkeypatch.setattr(install_cron, "_list_crontab", lambda: "# existing\n")
    applied: list[str] = []
    monkeypatch.setattr(install_cron, "_apply_crontab", lambda content: applied.append(content))

    def boom(*_a: object, **_k: object):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", boom)

    install_cron.install_cron(home=tmp_path, backup_dir=tmp_path / "backups")
    assert applied, "the crontab apply must still happen"
    assert "warning: failed to write crontab backup" in capsys.readouterr().err


def test_the_managed_block_is_newline_terminated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """crontab(1) rejects a file whose last line has no newline."""
    from scout.scripts import install_cron

    monkeypatch.setattr(install_cron, "_list_crontab", lambda: "# existing without newline")
    applied: list[str] = []
    monkeypatch.setattr(install_cron, "_apply_crontab", lambda content: applied.append(content))

    install_cron.install_cron(home=tmp_path, backup_dir=tmp_path / "backups")
    assert applied[0].endswith("\n")
    assert install_cron.BLOCK_OPEN in applied[0]


def test_uninstalling_the_cron_block_leaves_a_newline_terminated_crontab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scout.scripts import install_cron

    existing = f"# mine\n{install_cron.BLOCK_OPEN}\n* * * * * scout\n{install_cron.BLOCK_CLOSE}\n# also mine\n"
    monkeypatch.setattr(install_cron, "_list_crontab", lambda: existing)
    applied: list[str] = []
    monkeypatch.setattr(install_cron, "_apply_crontab", lambda content: applied.append(content))

    install_cron.uninstall_cron(home=tmp_path, backup_dir=tmp_path / "backups")
    assert install_cron.BLOCK_OPEN not in applied[0]
    # crontab(1) rejects a file whose last line has no newline.
    assert applied[0].endswith("\n")
    assert "# mine" in applied[0] and "# also mine" in applied[0]


@pytest.mark.parametrize(
    ("module", "label"),
    [
        ("scout.scripts.install_schedule_plist", "com.scout.schedule-tick"),
        ("scout.scripts.install_heartbeat_plist", "com.scout.heartbeat"),
    ],
)
def test_uninstalling_a_plist_boots_the_job_out_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str, label: str
) -> None:
    """Removing the file without a `launchctl bootout` leaves the job loaded
    until the next login — it keeps firing from a plist that no longer exists."""
    import importlib

    mod = importlib.import_module(module)
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    (agents / mod.PLIST_NAME).write_text("<plist/>", encoding="utf-8")

    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: calls.append(argv) or None)

    mod.uninstall_plist(agents_dir=agents, bootout=True)
    assert calls == [["launchctl", "bootout", f"gui/{os.getuid()}/{label}"]]
    assert not (agents / mod.PLIST_NAME).exists()


@pytest.mark.parametrize(
    "module",
    ["scout.scripts.install_schedule_plist", "scout.scripts.install_heartbeat_plist"],
)
def test_uninstalling_a_plist_can_skip_the_bootout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    import importlib

    mod = importlib.import_module(module)
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    (agents / mod.PLIST_NAME).write_text("<plist/>", encoding="utf-8")

    def fail(*_a: object, **_k: object):
        raise AssertionError("bootout=False must not shell out to launchctl")

    monkeypatch.setattr(subprocess, "run", fail)
    mod.uninstall_plist(agents_dir=agents, bootout=False)
    assert not (agents / mod.PLIST_NAME).exists()


def test_the_crontab_reader_cleans_up_its_tempfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_apply_crontab` writes a tempfile and hands it to crontab(1); the
    `finally` must remove it even on a failed apply."""
    from scout.scripts import install_cron

    seen: list[str] = []

    class Proc:
        returncode = 0
        stderr = ""

    def spy(argv, **_k):
        seen.append(argv[-1])
        return Proc()

    monkeypatch.setattr(subprocess, "run", spy)
    install_cron._apply_crontab("* * * * * scout\n")

    assert seen and not Path(seen[0]).exists()
