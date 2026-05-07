"""Unit tests for scout.scripts.schedule_snapshot.

Plan 5 Task 8 — JSON snapshot of the default schedule registry, consumed by
scout-app's schedule strip UI + dispatcher behavior labeling.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from scout.scripts import schedule_snapshot as snap

# ----- helpers --------------------------------------------------------------

_EXPECTED_SLOT_KEYS = [
    "morning-briefing",
    "weekend-briefing",
    "morning-consolidation",
    "midday-consolidation",
    "afternoon-consolidation",
    "evening-consolidation",
    "dreaming-evening",
    "dreaming-nightly",
    "dreaming-weekend-morning",
    "research",
]

_EXPECTED_SLOT_FIELDS = {"key", "type", "runner", "fires_at_local", "weekdays", "on_miss"}


def _yaml_slot_keys() -> list[str]:
    """Read schedule.yaml directly to confirm insertion order.

    Tests must NOT trust scout.schedule with the same logic the snapshot
    uses — read raw YAML so a bug in either layer fails the test, not both.
    """
    import yaml

    yaml_path = Path(__file__).resolve().parents[2] / "scout" / "defaults" / "schedule.yaml"
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return list(data["slots"].keys())


# ----- tests ----------------------------------------------------------------


def test_build_snapshot_has_v1_schema_and_all_slots():
    s = snap.build_snapshot()
    assert s["schema_version"] == 1
    assert "generated_from" in s
    assert s["generated_from"].startswith("scout-plugin@")
    assert isinstance(s["slots"], list)
    keys = [slot["key"] for slot in s["slots"]]
    assert keys == _yaml_slot_keys()


def test_build_snapshot_contains_all_10_slots():
    """Lock in the exact 10-slot roster that scout-app must reflect."""
    s = snap.build_snapshot()
    keys = [slot["key"] for slot in s["slots"]]
    assert len(s["slots"]) == 10
    assert keys == _EXPECTED_SLOT_KEYS


def test_build_snapshot_slot_field_shape():
    """Each slot record must have exactly the 6 fields in the v1 contract.

    The snapshot is intentionally lean: key, type, runner, fires_at_local,
    weekdays, on_miss. Dispatcher-internal fields (missed_window_hours,
    cooldown_minutes, budget_usd, tz) are excluded.
    """
    s = snap.build_snapshot()
    for slot in s["slots"]:
        assert set(slot.keys()) == _EXPECTED_SLOT_FIELDS, f"slot {slot['key']} has wrong fields: {set(slot.keys())}"


def test_build_snapshot_slot_field_types():
    """Spot-check value types for a sample slot (morning-briefing)."""
    s = snap.build_snapshot()
    mb = next(sl for sl in s["slots"] if sl["key"] == "morning-briefing")
    assert isinstance(mb["key"], str)
    assert isinstance(mb["type"], str)
    assert isinstance(mb["runner"], str)
    assert isinstance(mb["fires_at_local"], str)
    assert isinstance(mb["weekdays"], list)
    assert isinstance(mb["on_miss"], str)
    # Weekdays should be a list of strings
    assert all(isinstance(d, str) for d in mb["weekdays"])
    # fires_at_local should be HH:MM format
    assert len(mb["fires_at_local"]) == 5
    assert mb["fires_at_local"][2] == ":"


def test_build_snapshot_preserves_yaml_insertion_order():
    """Order is YAML insertion order — a YAML reorder shows up as drift."""
    s = snap.build_snapshot()
    keys = [slot["key"] for slot in s["slots"]]
    assert keys[0] == "morning-briefing"
    assert keys[-1] == "research"
    assert keys == _yaml_slot_keys()


def test_build_snapshot_enum_values_are_strings():
    """SlotType and OnMissPolicy enums must be serialized to their .value strings."""
    s = snap.build_snapshot()
    for slot in s["slots"]:
        # type and on_miss come from enums; must be plain strings in the JSON
        assert slot["type"] in {"briefing", "consolidation", "dreaming", "research", "manual"}
        assert slot["on_miss"] in {"fire", "skip", "collapse"}


def test_serialize_is_idempotent(tmp_path):
    """Writing twice produces byte-identical output (the SHA stays the same
    within one git state, so no false drift)."""
    target = tmp_path / "snap.json"
    text1 = snap.write_snapshot(target)
    text2 = snap.write_snapshot(target)
    assert text1 == text2
    assert target.read_text(encoding="utf-8") == text1


def test_serialize_format_indent_two_with_trailing_newline():
    """Stable serialization: indent=2, terminating newline. Both writer and
    --check rely on this exact format for byte comparisons."""
    s = snap.build_snapshot()
    text = snap.serialize(s)
    assert text.endswith("\n")
    # Spot-check indent: the second line should start with two spaces.
    assert text.split("\n")[1].startswith('  "')


def test_check_snapshot_passes_when_on_disk_matches(tmp_path):
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    ok, diff = snap.check_snapshot(target)
    assert ok is True
    assert diff == ""


def test_check_snapshot_fails_with_diff_when_on_disk_differs(tmp_path):
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    # Tamper: rewrite with a missing slot.
    bad = json.loads(target.read_text())
    bad["slots"] = bad["slots"][:-1]  # drop research
    target.write_text(json.dumps(bad, indent=2) + "\n", encoding="utf-8")

    ok, diff = snap.check_snapshot(target)
    assert ok is False
    assert "research" in diff
    # Unified diff format.
    assert "@@" in diff or "---" in diff


def test_check_snapshot_fails_when_target_missing(tmp_path):
    target = tmp_path / "does-not-exist.json"
    ok, diff = snap.check_snapshot(target)
    assert ok is False
    assert "does not exist" in diff


def test_check_snapshot_ignores_generated_from_sha_drift(tmp_path):
    """Two writes from different SHAs should still match content-wise.

    Reasoning: the snapshot is committed in scout-plugin AND scout-app at
    different times. If --check fails the moment anyone advances HEAD, the
    sync workflow is unworkable. We compare schema + slot list, not the
    embedded SHA.
    """
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    # Simulate the file being committed at an older SHA — rewrite the
    # generated_from line by hand.
    text = target.read_text(encoding="utf-8")
    munged = text.replace('"scout-plugin@', '"scout-plugin@deadbee')
    target.write_text(munged, encoding="utf-8")

    ok, diff = snap.check_snapshot(target)
    assert ok is True, f"unexpected drift in diff:\n{diff}"


def test_generated_from_uses_short_sha_when_in_git_repo():
    """The `generated_from` field surfaces the SHA so downstream consumers
    can correlate snapshots back to the plugin's git history."""
    s = snap.build_snapshot()
    val = s["generated_from"]
    assert val.startswith("scout-plugin@")
    sha = val.split("@", 1)[1]
    # Either a real short SHA (7-12 hex chars) or the unknown sentinel.
    assert sha == "unknown" or (len(sha) >= 4 and all(c in "0123456789abcdef" for c in sha))


def test_generated_from_falls_back_to_unknown_outside_git(tmp_path):
    """If the repo lookup fails (not a git repo, no git binary), fall back to 'unknown'."""
    s = snap.build_snapshot(repo_dir=tmp_path)
    assert s["generated_from"] == "scout-plugin@unknown"


def test_generated_from_falls_back_to_unknown_when_git_missing():
    """If `git` binary is missing entirely, the helper still returns unknown."""
    with patch.object(subprocess, "run", side_effect=FileNotFoundError("git")):
        s = snap.build_snapshot()
    assert s["generated_from"] == "scout-plugin@unknown"


def test_main_writes_target_with_flag(tmp_path):
    """The `--target` flag overrides the default path."""
    target = tmp_path / "fixtures" / "snap.json"
    rc = snap.main(["--target", str(target), "--no-also-write-app-fixture"])
    assert rc == 0
    assert target.exists()
    parsed = json.loads(target.read_text())
    assert parsed["schema_version"] == 1
    assert len(parsed["slots"]) == 10


def test_main_check_mode_returns_zero_when_in_sync(tmp_path):
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    rc = snap.main(["--target", str(target), "--check"])
    assert rc == 0


def test_main_check_mode_returns_one_on_drift(tmp_path):
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    # Rewrite with empty slots to force drift.
    bad = json.loads(target.read_text())
    bad["slots"] = []
    target.write_text(json.dumps(bad, indent=2) + "\n", encoding="utf-8")

    rc = snap.main(["--target", str(target), "--check"])
    assert rc == 1


def test_canonical_snapshot_path_points_at_engine_scout():
    """The canonical snapshot lives at engine/scout/schedule.snapshot.json.
    CI verifies this exact file with `--check`."""
    canonical = snap.canonical_snapshot_path()
    parts = canonical.parts
    # Last three components must be engine/scout/schedule.snapshot.json.
    assert parts[-3:] == ("engine", "scout", "schedule.snapshot.json")


def test_app_fixture_snapshot_path_points_at_scout_app():
    """The scout-app fixture path lives at
    ~/scout-app/ScoutTests/Fixtures/schedule.snapshot.json."""
    fixture = snap.app_fixture_snapshot_path()
    parts = fixture.parts
    assert parts[-4:] == (
        "scout-app",
        "ScoutTests",
        "Fixtures",
        "schedule.snapshot.json",
    )


def test_main_default_target_is_canonical_snapshot_path(tmp_path, monkeypatch):
    """When --target is omitted, main() writes the canonical snapshot path.

    Redirect the canonical helper to tmp_path so the test doesn't actually
    touch the repo's committed snapshot.
    """
    fake_canonical = tmp_path / "engine" / "scout" / "schedule.snapshot.json"
    monkeypatch.setattr(snap, "canonical_snapshot_path", lambda: fake_canonical)
    # Disable the dual-write so we only assert on the primary path here.
    rc = snap.main(["--no-also-write-app-fixture"])
    assert rc == 0
    assert fake_canonical.exists()


def test_main_dual_writes_when_app_fixture_dir_exists(tmp_path, monkeypatch):
    """Default behavior: writing to --target ALSO writes to the scout-app
    bundled fixture (best-effort) so a single invocation keeps both repos
    in sync."""
    fake_app_fixture = tmp_path / "scout-app" / "ScoutTests" / "Fixtures" / "schedule.snapshot.json"
    fake_app_fixture.parent.mkdir(parents=True)
    monkeypatch.setattr(snap, "app_fixture_snapshot_path", lambda: fake_app_fixture)

    primary = tmp_path / "primary.json"
    rc = snap.main(["--target", str(primary)])
    assert rc == 0
    assert primary.exists()
    assert fake_app_fixture.exists()
    # Both files should have identical content (same builder, same SHA).
    assert primary.read_text(encoding="utf-8") == fake_app_fixture.read_text(encoding="utf-8")


def test_main_skips_app_fixture_with_warning_when_dir_missing(tmp_path, monkeypatch, capsys):
    """If the scout-app fixture parent doesn't exist, the dual-write is
    skipped with a stderr warning rather than failing."""
    bogus = tmp_path / "nonexistent-scout-app" / "Fixtures" / "schedule.snapshot.json"
    monkeypatch.setattr(snap, "app_fixture_snapshot_path", lambda: bogus)

    primary = tmp_path / "primary.json"
    rc = snap.main(["--target", str(primary)])
    assert rc == 0
    assert primary.exists()
    assert not bogus.exists()
    captured = capsys.readouterr()
    assert "skipped scout-app fixture write" in captured.err
    assert "--no-also-write-app-fixture" in captured.err


def test_main_no_also_write_app_fixture_skips_dual_write(tmp_path, monkeypatch):
    """`--no-also-write-app-fixture` disables the secondary write entirely,
    even when the path is viable."""
    fake_app_fixture = tmp_path / "scout-app" / "ScoutTests" / "Fixtures" / "schedule.snapshot.json"
    fake_app_fixture.parent.mkdir(parents=True)
    monkeypatch.setattr(snap, "app_fixture_snapshot_path", lambda: fake_app_fixture)

    primary = tmp_path / "primary.json"
    rc = snap.main(["--target", str(primary), "--no-also-write-app-fixture"])
    assert rc == 0
    assert primary.exists()
    assert not fake_app_fixture.exists()


def test_main_dual_write_no_op_when_target_equals_app_fixture(tmp_path, monkeypatch, capsys):
    """If the operator passes --target pointing at the app fixture itself,
    the primary write covers it — no double-write, no warning."""
    fake_app_fixture = tmp_path / "scout-app" / "ScoutTests" / "Fixtures" / "schedule.snapshot.json"
    fake_app_fixture.parent.mkdir(parents=True)
    monkeypatch.setattr(snap, "app_fixture_snapshot_path", lambda: fake_app_fixture)

    rc = snap.main(["--target", str(fake_app_fixture)])
    assert rc == 0
    assert fake_app_fixture.exists()
    captured = capsys.readouterr()
    # Exactly one "Wrote:" line (the primary), no second one for the app fixture.
    assert captured.out.count("Wrote:") == 1
    assert "skipped" not in captured.err
