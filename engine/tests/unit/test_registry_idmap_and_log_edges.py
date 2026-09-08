"""Remaining branches in the connector registry, the id-map, and the
PostToolUse connector log.

Each of these has a happy-path test file; this one closes the branches those
files don't reach:

* `ConnectorRegistry`'s mapping protocol and the deprecated
  `required_in`/`critical_in_mode` pair (still honoured for vault overlays that
  haven't migrated to `required_in_types`).
* `_load_yaml` / `_build_connector`'s malformed-input errors — a typo in an
  overlay must be a named `ConfigError`, not a `KeyError` out of a loader.
* `IdMap`'s schema-version gate, atomic-write rollback, and `reattach`'s
  cross-file fallback (the path that recovers a task whose `[#XXXX]` prefix was
  hand-deleted).
* `connector_log`'s nested-content error detection, the no-fcntl fallback, and
  `main()`'s swallow-everything contract.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path

import pytest

from scout import connectors as conn
from scout import id_map as idm
from scout.connectors import Capability, Connector, ConnectorRegistry, Remediation, Tier, load_registry
from scout.errors import ConfigError
from scout.hooks import connector_log
from scout.id_map import IdMap, IdMapEntry
from scout.schedule import SlotType


def _connector(key: str, **overrides) -> Connector:
    base = {
        "key": key,
        "display_name": key.title(),
        "tier": Tier.OFFICIAL,
        "capabilities": (Capability.INBOUND,),
        "required_in": (),
        "required_in_types": (),
        "remediation": Remediation(first_fix="fix it", detail="details"),
    }
    base.update(overrides)
    return Connector(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ConnectorRegistry — the mapping protocol
# ---------------------------------------------------------------------------


def test_registry_supports_the_mapping_protocol() -> None:
    a, b = _connector("slack"), _connector("github")
    reg = ConnectorRegistry({"slack": a, "github": b})

    assert "slack" in reg
    assert "carrier_pigeon" not in reg
    assert reg["slack"] is a
    assert list(reg) == ["slack", "github"]
    assert list(reg.keys()) == ["slack", "github"]
    assert list(reg.values()) == [a, b]
    assert dict(reg.items()) == {"slack": a, "github": b}


def test_registry_getitem_raises_for_an_unknown_key() -> None:
    with pytest.raises(KeyError):
        ConnectorRegistry({})["nope"]


# ---------------------------------------------------------------------------
# The deprecated required_in / critical_in_mode pair
# ---------------------------------------------------------------------------


def test_required_in_all_matches_every_mode() -> None:
    """An overlay can still say `required_in: all`; that must mean "every
    slot", not the literal string."""
    c = _connector("slack", required_in="all")
    assert c.required_in_mode("morning-briefing") is True
    assert c.required_in_mode("anything-at-all") is True


def test_required_in_matches_only_the_listed_modes() -> None:
    c = _connector("slack", required_in=("morning-briefing", "consolidation"))
    assert c.required_in_mode("morning-briefing") is True
    assert c.required_in_mode("dreaming") is False


def test_required_in_is_empty_by_default() -> None:
    assert _connector("slack").required_in_mode("morning-briefing") is False


def test_critical_in_mode_lists_the_legacy_matches() -> None:
    reg = ConnectorRegistry(
        {
            "slack": _connector("slack", required_in=("morning-briefing",)),
            "github": _connector("github", required_in="all"),
            "telegram": _connector("telegram"),
        }
    )
    assert sorted(reg.critical_in_mode("morning-briefing")) == ["github", "slack"]
    assert reg.critical_in_mode("dreaming") == ["github"]


def test_required_in_type_is_the_canonical_check() -> None:
    c = _connector("slack", required_in_types=(SlotType.BRIEFING,))
    assert c.required_in_type(SlotType.BRIEFING) is True
    assert c.required_in_type(SlotType.DREAMING) is False
    # Outbound-only connectors declare none.
    assert _connector("telegram").required_in_type(SlotType.BRIEFING) is False


# ---------------------------------------------------------------------------
# _load_yaml / _build_connector errors
# ---------------------------------------------------------------------------


def test_unreadable_yaml_is_a_named_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="could not be read"):
        conn._load_yaml(tmp_path / "nope.yaml")


def test_malformed_yaml_is_a_named_config_error(tmp_path: Path) -> None:
    path = tmp_path / "connectors.yaml"
    path.write_text("connectors: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="is malformed"):
        conn._load_yaml(path)


@pytest.mark.parametrize("body", ["- a\n- list\n", "just a string\n", "42\n"])
def test_non_mapping_yaml_is_a_named_config_error(tmp_path: Path, body: str) -> None:
    path = tmp_path / "connectors.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match="is not a mapping"):
        conn._load_yaml(path)


def test_a_connector_missing_display_name_is_a_named_config_error() -> None:
    with pytest.raises(ConfigError, match="connector slack entry is malformed"):
        conn._build_connector("slack", {"tier": "official"})


@pytest.mark.parametrize(
    "raw",
    [
        {"display_name": "Slack", "tier": "imaginary"},
        {"display_name": "Slack", "capabilities": ["telepathy"]},
        {"display_name": "Slack", "required_in_types": ["nap"]},
    ],
)
def test_an_unknown_enum_value_is_a_named_config_error(raw: dict) -> None:
    with pytest.raises(ConfigError, match="entry is malformed"):
        conn._build_connector("slack", raw)


def test_build_connector_defaults_every_optional_field() -> None:
    c = conn._build_connector("slack", {"display_name": "Slack"})
    assert c.tier is Tier.OFFICIAL
    assert c.capabilities == ()
    assert c.required_in == ()
    assert c.required_in_types == ()
    assert c.remediation == Remediation(first_fix="", detail="")
    assert c.notes == ""


def test_build_connector_normalizes_a_null_notes_to_empty() -> None:
    """`notes:` with no value parses as None; the dataclass promises str."""
    assert conn._build_connector("slack", {"display_name": "Slack", "notes": None}).notes == ""


def test_build_connector_reads_required_in_all_as_the_sentinel_string() -> None:
    c = conn._build_connector("slack", {"display_name": "Slack", "required_in": "all"})
    assert c.required_in == "all"


# ---------------------------------------------------------------------------
# load_registry overlay merge
# ---------------------------------------------------------------------------


def test_overlay_can_add_a_connector(tmp_path: Path) -> None:
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
    reg = load_registry(tmp_path)
    assert "house-sensor" in reg
    assert reg["house-sensor"].tier is Tier.COMMUNITY


def test_overlay_deep_merges_remediation_onto_a_shipped_connector(tmp_path: Path) -> None:
    """Only `remediation` merges a level deep, so an overlay can replace just
    `first_fix` without restating `detail`."""
    shipped = next(iter(load_registry(tmp_path).keys()))
    original = load_registry(tmp_path)[shipped]

    state = tmp_path / ".scout-state"
    state.mkdir(parents=True)
    (state / "connectors.local.yaml").write_text(
        f"connectors:\n  {shipped}:\n    remediation:\n      first_fix: my custom fix\n",
        encoding="utf-8",
    )
    merged = load_registry(tmp_path)[shipped]
    assert merged.remediation.first_fix == "my custom fix"
    assert merged.remediation.detail == original.remediation.detail
    assert merged.display_name == original.display_name


def test_overlay_replaces_a_scalar_field_outright(tmp_path: Path) -> None:
    shipped = next(iter(load_registry(tmp_path).keys()))
    state = tmp_path / ".scout-state"
    state.mkdir(parents=True)
    (state / "connectors.local.yaml").write_text(
        f"connectors:\n  {shipped}:\n    display_name: Renamed\n", encoding="utf-8"
    )
    assert load_registry(tmp_path)[shipped].display_name == "Renamed"


def test_deep_merge_leaves_the_input_dicts_untouched() -> None:
    a = {"x": 1, "remediation": {"first_fix": "a"}}
    b = {"remediation": {"detail": "b"}}
    out = conn._deep_merge_dict(a, b)
    assert out == {"x": 1, "remediation": {"first_fix": "a", "detail": "b"}}
    assert a == {"x": 1, "remediation": {"first_fix": "a"}}


# ---------------------------------------------------------------------------
# IdMap
# ---------------------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / ".scout-state").mkdir(parents=True)
    return tmp_path


def _entry(ulid: str, prefix: str, title: str, file: str, line: int = 3) -> IdMapEntry:
    return IdMapEntry(ulid=ulid, short_prefix=prefix, last_title=title, last_file=file, last_line=line)


def test_load_returns_an_empty_map_when_the_file_is_absent(vault: Path) -> None:
    assert list(IdMap.load(vault).iter_entries()) == []


def test_load_rejects_a_corrupt_json_file(vault: Path) -> None:
    (vault / ".scout-state" / "id-map.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt id-map at"):
        IdMap.load(vault)


@pytest.mark.parametrize("version", [None, 0, 2, "1", "one"])
def test_load_rejects_an_unknown_schema_version(vault: Path, version: object) -> None:
    """Reading a future schema as if it were v1 would silently drop fields and
    then write them away on the next save()."""
    (vault / ".scout-state" / "id-map.json").write_text(
        json.dumps({"schema_version": version, "entries": {}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown schema_version"):
        IdMap.load(vault)


def test_save_then_load_round_trips(vault: Path) -> None:
    m = IdMap.load(vault)
    entry = _entry("01HXAAA0000000000000000000", "A3F7", "Land the fix", "action-items-2026-04-15.md")
    m.register(entry)
    m.save()

    reloaded = IdMap.load(vault)
    assert reloaded.lookup_by_ulid(entry.ulid) == entry
    assert reloaded.lookup_by_prefix("A3F7") == entry
    assert reloaded.in_use_prefixes() == {"A3F7"}


def test_lookups_return_none_for_a_miss(vault: Path) -> None:
    m = IdMap.load(vault)
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "t", "f.md"))
    assert m.lookup_by_prefix("ZZZZ") is None
    assert m.lookup_by_ulid("01HXBBB0000000000000000000") is None


def test_register_overwrites_an_existing_ulid(vault: Path) -> None:
    m = IdMap.load(vault)
    ulid = "01HXAAA0000000000000000000"
    m.register(_entry(ulid, "A3F7", "old title", "f.md", line=3))
    m.register(_entry(ulid, "A3F7", "new title", "f.md", line=9))
    assert len(list(m.iter_entries())) == 1
    found = m.lookup_by_ulid(ulid)
    assert found is not None and found.last_title == "new title" and found.last_line == 9


def test_save_creates_the_state_dir(tmp_path: Path) -> None:
    m = IdMap(tmp_path, entries={})
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "t", "f.md"))
    m.save()
    assert (tmp_path / ".scout-state" / "id-map.json").is_file()


def test_save_removes_its_tempfile_when_the_write_fails(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed atomic write must not leave a `.id-map.*.json.tmp` behind —
    those accumulate silently in the vault's state dir."""
    m = IdMap.load(vault)
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "t", "f.md"))

    def boom(*_a: object, **_k: object):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fdopen", boom)
    with pytest.raises(OSError, match="disk full"):
        m.save()

    state = vault / ".scout-state"
    assert list(state.glob(".id-map.*.json.tmp")) == []
    assert not (state / "id-map.json").exists()


def test_save_removes_its_tempfile_on_a_keyboard_interrupt(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rollback catches BaseException, not Exception, so a Ctrl-C mid-save
    also cleans up."""
    m = IdMap.load(vault)
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "t", "f.md"))

    real_replace = os.replace

    def boom(*_a: object, **_k: object):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        m.save()
    monkeypatch.setattr(os, "replace", real_replace)

    assert list((vault / ".scout-state").glob(".id-map.*.json.tmp")) == []


def test_save_writes_deterministic_sorted_json(vault: Path) -> None:
    """Sorted keys keep the file diffable — it lives in a git-tracked vault."""
    m = IdMap.load(vault)
    m.register(_entry("01HXBBB0000000000000000000", "B5K2", "second", "f.md"))
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "first", "f.md"))
    m.save()

    text = (vault / ".scout-state" / "id-map.json").read_text()
    assert text.index("01HXAAA") < text.index("01HXBBB")
    assert text.endswith("\n")


def test_reattach_returns_none_without_a_title_match(vault: Path) -> None:
    m = IdMap.load(vault)
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "Land the fix", "today.md"))
    assert m.reattach(title="Something else", file="today.md") is None


def test_reattach_prefers_a_same_file_match(vault: Path) -> None:
    m = IdMap.load(vault)
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "Land the fix", "yesterday.md"))
    m.register(_entry("01HXBBB0000000000000000000", "B5K2", "Land the fix", "today.md"))

    found = m.reattach(title="Land the fix", file="today.md")
    assert found is not None and found.short_prefix == "B5K2"


def test_reattach_falls_back_to_a_cross_file_match(vault: Path) -> None:
    """The carry-forward case: the task moved to today's file and lost its
    prefix, so the only candidate is filed under yesterday."""
    m = IdMap.load(vault)
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "Land the fix", "yesterday.md"))

    found = m.reattach(title="Land the fix", file="today.md")
    assert found is not None and found.short_prefix == "A3F7"


# ---------------------------------------------------------------------------
# connector_log
# ---------------------------------------------------------------------------


@pytest.fixture
def log_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "Scout"
    (d / ".scout-logs").mkdir(parents=True)
    monkeypatch.setenv("SCOUT_DATA_DIR", str(d))
    monkeypatch.setenv("SCOUT_MODE", "dreaming")
    return d


def _rows(log_vault: Path) -> list[dict]:
    out = next((log_vault / ".scout-logs").glob("connector-calls-*.jsonl"))
    return [json.loads(ln) for ln in out.read_text().splitlines()]


def test_a_nested_content_block_flagged_is_error_marks_the_row(log_vault: Path) -> None:
    """MCP tools report failure inside a content block rather than at the top
    level; missing this under-reports every MCP outage."""
    payload = {
        "tool_name": "mcp__slack__slack_read_channel",
        "tool_input": {},
        "tool_response": {"content": [{"isError": True, "text": "not_in_channel"}]},
        "session_id": "s1",
    }
    connector_log.run(stdin=io.StringIO(json.dumps(payload)))

    row = _rows(log_vault)[0]
    assert row["error"] is True
    assert row["err"] == "not_in_channel"
    assert row["connector"] == "mcp:slack"


def test_a_nested_error_block_with_no_text_yields_no_snippet(log_vault: Path) -> None:
    payload = {"tool_name": "Read", "tool_response": {"content": [{"isError": True}]}, "session_id": "s1"}
    connector_log.run(stdin=io.StringIO(json.dumps(payload)))
    row = _rows(log_vault)[0]
    assert row["error"] is True
    assert "err" not in row


def test_a_top_level_error_snippet_wins_over_a_nested_one(log_vault: Path) -> None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "gh pr list"},
        "tool_response": {"error": "outer", "content": [{"isError": True, "text": "inner"}]},
        "session_id": "s1",
    }
    connector_log.run(stdin=io.StringIO(json.dumps(payload)))
    row = _rows(log_vault)[0]
    assert row["err"] == "outer"
    assert row["connector"] == "github"


def test_non_dict_content_items_are_ignored(log_vault: Path) -> None:
    payload = {"tool_name": "Read", "tool_response": {"content": ["a string", None, 7]}, "session_id": "s1"}
    connector_log.run(stdin=io.StringIO(json.dumps(payload)))
    assert _rows(log_vault)[0]["error"] is False


def test_a_non_dict_tool_response_is_treated_as_no_error(log_vault: Path) -> None:
    payload = {"tool_name": "Read", "tool_response": "just a string", "session_id": "s1"}
    connector_log.run(stdin=io.StringIO(json.dumps(payload)))
    assert _rows(log_vault)[0]["error"] is False


def test_locking_is_skipped_where_fcntl_is_unavailable(log_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The import guard exists for non-POSIX hosts; the append must still
    happen there, just unserialized."""
    monkeypatch.setattr(connector_log, "fcntl", None)
    connector_log.run(stdin=io.StringIO(json.dumps({"tool_name": "Read", "session_id": "s1"})))
    assert _rows(log_vault)[0]["tool"] == "Read"


def test_locking_uses_flock_when_available(log_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class FakeFcntl:
        LOCK_EX = 2

        @staticmethod
        def flock(fd: int, op: int) -> None:
            calls.append(op)

    monkeypatch.setattr(connector_log, "fcntl", FakeFcntl)
    connector_log.run(stdin=io.StringIO(json.dumps({"tool_name": "Read", "session_id": "s1"})))
    assert calls == [FakeFcntl.LOCK_EX]


def test_an_mcp_tool_name_with_no_server_segment_falls_back_to_lowercase(log_vault: Path) -> None:
    connector_log.run(stdin=io.StringIO(json.dumps({"tool_name": "mcp__", "session_id": "s1"})))
    # "mcp__".split("__") == ["mcp", ""] -> len 2, so the server segment is "".
    assert _rows(log_vault)[0]["connector"] == "mcp:"


def test_a_bare_mcp_prefix_without_separators_lowercases(log_vault: Path) -> None:
    assert connector_log.classify("mcp__x", {}) == "mcp:x"
    assert connector_log.classify("MCP_NOT_PREFIXED", {}) == "mcp_not_prefixed"


def test_a_write_failure_is_reported_to_stderr_and_still_returns_an_event(
    log_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dropped row is an unsignalled gap in the audit log, so it warns — but
    the hook still must not fail the session."""
    real_open = Path.open

    def maybe_boom(self: Path, *a: object, **k: object):
        if self.suffix == ".jsonl":
            raise OSError("read-only filesystem")
        return real_open(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", maybe_boom)
    event = connector_log.run(stdin=io.StringIO(json.dumps({"tool_name": "Read", "session_id": "s1"})))

    assert event is not None
    assert event.kind == "tool.call.logged"
    assert "connector-log: failed to append row" in capsys.readouterr().err


def test_run_reads_real_stdin_when_none_is_passed(log_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Read", "session_id": "s1"})))
    event = connector_log.run()
    assert event is not None
    assert event.payload["tool"] == "Read"


def test_main_returns_zero_even_when_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_k: object):
        raise RuntimeError("unreachable state")

    monkeypatch.setattr(connector_log, "run", boom)
    assert connector_log.main() == 0


def test_main_returns_zero_on_the_happy_path(log_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_name": "Read", "session_id": "s1"})))
    assert connector_log.main() == 0
    assert _rows(log_vault)[0]["tool"] == "Read"


def test_idmap_tempfile_lives_beside_the_target(vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tempfile must be in the same directory as the target, or os.replace
    can cross a filesystem boundary and stop being atomic."""
    seen: dict[str, object] = {}
    real_mkstemp = tempfile.mkstemp

    def spy(*a: object, **k: object):
        seen.update(k)
        return real_mkstemp(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    m = IdMap.load(vault)
    m.register(_entry("01HXAAA0000000000000000000", "A3F7", "t", "f.md"))
    m.save()

    assert seen["dir"] == str(vault / ".scout-state")
    assert idm.paths.id_map_path(vault).is_file()
