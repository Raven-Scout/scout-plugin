"""Unit tests for scout.scripts.connectors_snapshot.

Plan 4 Task 8 — JSON snapshot of the official-tier roster, consumed by
scout-app's ConnectorHealthService.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from scout.scripts import connectors_snapshot as snap

# ----- helpers --------------------------------------------------------------


def _expected_official_keys() -> list[str]:
    """Read the canonical YAML directly to confirm what the snapshot should contain.

    The unit tests must NOT trust scout.connectors with the same logic the snapshot
    uses — read raw YAML so a bug in either layer fails the test, not both.
    """
    import yaml

    yaml_path = Path(__file__).resolve().parents[2] / "scout" / "connectors.yaml"
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    keys = []
    for key, raw in data["connectors"].items():
        if raw.get("tier", "official") == "official":
            keys.append(key)
    return keys


# ----- tests ----------------------------------------------------------------


def test_build_snapshot_has_v1_schema_and_official_connectors():
    s = snap.build_snapshot()
    assert s["schema_version"] == 1
    assert "generated_from" in s
    assert s["generated_from"].startswith("scout-plugin@")
    assert isinstance(s["connectors"], list)
    keys = [c["key"] for c in s["connectors"]]
    assert keys == _expected_official_keys()


def test_build_snapshot_filters_to_official_tier_only():
    """If anything in the YAML is non-official, it must NOT appear in the snapshot.

    Currently every row in the seed is official; this test future-proofs the
    contract by asserting tier=='official' on every emitted record.
    """
    s = snap.build_snapshot()
    for c in s["connectors"]:
        assert c["tier"] == "official", c


def test_build_snapshot_contains_expected_canonical_keys():
    """Lock in the exact roster that scout-app must reflect.

    The YAML defines 10 official-tier connectors (Slack, Linear, Gmail,
    Calendar, Granola, Drive, github, chrome, whatsapp, telegram).
    """
    s = snap.build_snapshot()
    keys = {c["key"] for c in s["connectors"]}
    assert keys == {
        "mcp:claude_ai_Slack",
        "mcp:claude_ai_Linear",
        "mcp:claude_ai_Gmail",
        "mcp:claude_ai_Google_Calendar",
        "mcp:claude_ai_Granola",
        "mcp:claude_ai_Google_Drive",
        "github",
        "mcp:claude-in-chrome",
        "mcp:whatsapp-mcp",
        "notify:telegram",
    }
    assert len(s["connectors"]) == 10


def test_build_snapshot_preserves_yaml_insertion_order():
    """Order is YAML insertion order — a YAML reorder shows up as drift."""
    s = snap.build_snapshot()
    keys = [c["key"] for c in s["connectors"]]
    # Slack is the first official entry in the YAML, and Telegram is the last.
    assert keys[0] == "mcp:claude_ai_Slack"
    assert keys[-1] == "notify:telegram"


def test_serialize_is_idempotent(tmp_path):
    """Writing twice produces byte-identical output (the SHA stays the same
    within one git state, so no false drift)."""
    target = tmp_path / "snap.json"
    text1 = snap.write_snapshot(target)
    text2 = snap.write_snapshot(target)
    assert text1 == text2
    assert target.read_text(encoding="utf-8") == text1


def test_check_snapshot_passes_when_on_disk_matches(tmp_path):
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    ok, diff = snap.check_snapshot(target)
    assert ok is True
    assert diff == ""


def test_check_snapshot_fails_with_diff_when_on_disk_differs(tmp_path):
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    # Tamper: rewrite with a missing connector.
    bad = json.loads(target.read_text())
    bad["connectors"] = bad["connectors"][:-1]  # drop telegram
    target.write_text(json.dumps(bad, indent=2) + "\n", encoding="utf-8")

    ok, diff = snap.check_snapshot(target)
    assert ok is False
    assert "notify:telegram" in diff
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
    sync workflow is unworkable. We compare schema + connector list, not the
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
    # Force the SHA helper to run in a non-git directory.
    s = snap.build_snapshot(repo_dir=tmp_path)
    assert s["generated_from"] == "scout-plugin@unknown"


def test_generated_from_falls_back_to_unknown_when_git_missing():
    """If `git` binary is missing entirely, the helper still returns unknown."""
    with patch.object(subprocess, "run", side_effect=FileNotFoundError("git")):
        s = snap.build_snapshot()
    assert s["generated_from"] == "scout-plugin@unknown"


def test_main_writes_target_default_path_argument(tmp_path):
    """The `--target` flag overrides the default path."""
    target = tmp_path / "fixtures" / "snap.json"
    rc = snap.main(["--target", str(target)])
    assert rc == 0
    assert target.exists()
    parsed = json.loads(target.read_text())
    assert parsed["schema_version"] == 1


def test_main_check_mode_returns_zero_when_in_sync(tmp_path):
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    rc = snap.main(["--target", str(target), "--check"])
    assert rc == 0


def test_main_check_mode_returns_one_on_drift(tmp_path):
    target = tmp_path / "snap.json"
    snap.write_snapshot(target)
    # Rewrite with a missing field to force drift.
    bad = json.loads(target.read_text())
    bad["connectors"] = []
    target.write_text(json.dumps(bad, indent=2) + "\n", encoding="utf-8")

    rc = snap.main(["--target", str(target), "--check"])
    assert rc == 1


def test_serialize_format_indent_two_with_trailing_newline():
    """Stable serialization: indent=2, terminating newline. Both writer and
    --check rely on this exact format for byte comparisons."""
    s = snap.build_snapshot()
    text = snap.serialize(s)
    assert text.endswith("\n")
    # Spot-check indent: the second line should start with two spaces.
    assert text.split("\n")[1].startswith('  "')
