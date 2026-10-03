"""Unit tests for engine/scout/scripts/bootstrap.py — install pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.scripts.bootstrap import (
    BootstrapConfig,
    InstallResult,
    install,
    resolve_claude_bin,
)


def _config(vault: Path, *, plugin_root: Path) -> BootstrapConfig:
    return BootstrapConfig(
        vault=vault,
        plugin_root=plugin_root,
        instance_name="TestScout",
        instance_name_lower="testscout",
        user_name="Test User",
        user_email="test@example.com",
        timezone="America/New_York",
        platform="macos",
        plugin_version="0.4.0",
        enabled_connectors=set(),
        connector_inputs={},
        skip_jobs=True,  # don't touch ~/Library/LaunchAgents in tests
        skip_claude=True,  # don't run a real Claude session
    )


def test_install_creates_directory_tree(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent  # repo root: ~/scout-plugin-plan-8
    vault = tmp_path / "Scout"
    result = install(_config(vault, plugin_root=plugin))
    assert isinstance(result, InstallResult)
    assert vault.exists()
    assert (vault / "knowledge-base").is_dir()
    assert (vault / "action-items").is_dir()
    assert (vault / ".scout-state").is_dir()
    assert (vault / "scripts").is_dir()
    assert (vault / "hooks").is_dir()
    assert (vault / "drafts").is_dir()
    assert (vault / "drafts" / "archive").is_dir()


def test_install_seeds_drafts_readme(tmp_path):
    """The drafts/ directory ships a README documenting the draft file contract
    that the plugin writer, /scout-work, and the macOS app all key on."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    readme = vault / "drafts" / "README.md"
    assert readme.exists()
    text = readme.read_text()
    assert "drafts/<TAG>.md" in text
    assert "status: draft" in text
    # The autonomous-never-sends guarantee must be documented for the user.
    assert "never send" in text.lower()


def test_install_writes_scout_config(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    config = (vault / "scout-config.yaml").read_text()
    assert "TestScout" in config
    assert "version_at_last_setup" in config
    assert "0.4.0" in config


def test_install_seeds_schedule_yaml(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    schedule = vault / ".scout-state" / "schedule.yaml"
    assert schedule.exists()
    assert "schema_version" in schedule.read_text()


def test_install_writes_assembled_files_and_snapshots(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    for name in ("SKILL", "DREAMING", "RESEARCH"):
        assert (vault / f"{name}.md").exists()
        assert (vault / ".scout-state" / "last-assembled" / f"{name}.md").exists()


def test_install_refuses_existing_vault(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    vault.mkdir()
    (vault / "scout-config.yaml").write_text("# already here\n")
    with pytest.raises(FileExistsError, match="vault detected"):
        install(_config(vault, plugin_root=plugin))


def test_install_records_plugin_version(tmp_path):
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    config_text = (vault / "scout-config.yaml").read_text()
    import yaml

    cfg = yaml.safe_load(config_text)
    assert cfg["plugin"]["version_at_last_setup"] == "0.4.0"
    assert cfg["plugin"]["version_at_last_update"] == "0.4.0"


def test_install_persists_connector_inputs(tmp_path):
    """Install must persist connector inputs so the next upgrade's
    template renders use the user's real values instead of defaults.

    Regression: cli_bootstrap_install previously hardcoded
    connector_inputs={}, which meant fresh installs left scout-config.yaml
    without `connectors.inputs`. The next /scout-update would then regen
    cat-1b runners with placeholder CLAUDE_BIN / empty USER_SLACK_ID."""
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    cfg = _config(vault, plugin_root=plugin)
    cfg.enabled_connectors = {"slack", "github"}
    cfg.connector_inputs = {
        "user_slack_id": "U123ABC",
        "github_username": "alice",
        "github_repos": "org/repo-a,org/repo-b",
        "claude_bin": "/opt/homebrew/bin/claude",
        "max_budget": "12.50",
    }
    install(cfg)

    import yaml

    persisted = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert persisted["connectors"]["enabled"] == ["github", "slack"]  # sorted
    inputs = persisted["connectors"]["inputs"]
    assert inputs["user_slack_id"] == "U123ABC"
    assert inputs["claude_bin"] == "/opt/homebrew/bin/claude"
    assert inputs["max_budget"] == "12.50"

    # And the rendered runner picked them up rather than falling back to
    # the template defaults — this is the failure mode the friend's vault hit.
    runner_text = (vault / "run-scout.sh").read_text()
    assert "/opt/homebrew/bin/claude" in runner_text


# ---------- #254: CLAUDE_BIN detection ----------


def _fake_claude(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_resolve_claude_bin_explicit_wins(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert resolve_claude_bin("/opt/custom/claude", home=tmp_path) == "/opt/custom/claude"


def test_resolve_claude_bin_prefers_path_lookup(tmp_path, monkeypatch):
    on_path = _fake_claude(tmp_path / "bin" / "claude")
    _fake_claude(tmp_path / ".local" / "bin" / "claude")
    monkeypatch.setattr("shutil.which", lambda _name: str(on_path))
    assert resolve_claude_bin("", home=tmp_path) == str(on_path)


def test_resolve_claude_bin_finds_native_installer_location(tmp_path, monkeypatch):
    """The native installer's ~/.local/bin/claude — not the old /usr/local/bin
    default — when claude is not on the setup shell's PATH."""
    native = _fake_claude(tmp_path / ".local" / "bin" / "claude")
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("os.access", lambda p, _mode: str(p) == str(native))
    assert resolve_claude_bin("", home=tmp_path) == str(native)


def test_resolve_claude_bin_nothing_found_returns_native_path(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr("os.access", lambda _p, _mode: False)
    assert resolve_claude_bin("", home=tmp_path) == str(tmp_path / ".local" / "bin" / "claude")


def test_install_without_claude_bin_never_renders_usr_local_default(tmp_path, monkeypatch):
    import shutil

    native = _fake_claude(tmp_path / "home" / ".local" / "bin" / "claude")
    real_which = shutil.which

    def fake_which(name, *args, **kwargs):
        return str(native) if name == "claude" else real_which(name, *args, **kwargs)

    monkeypatch.setattr("shutil.which", fake_which)
    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    assert f'CLAUDE_BIN="{native}"' in (vault / "run-scout.sh").read_text()


# ---------- #255: auto-update preference recorded by install ----------


@pytest.mark.parametrize("choice", [True, False])
def test_install_records_auto_update_choice(tmp_path, choice):
    import yaml

    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    cfg = _config(vault, plugin_root=plugin)
    cfg.auto_update = choice
    install(cfg)
    persisted = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert persisted["auto_update"] == {"enabled": choice, "channel": "stable"}


def test_install_without_auto_update_choice_leaves_block_absent(tmp_path):
    import yaml

    plugin = Path(__file__).parent.parent.parent.parent
    vault = tmp_path / "Scout"
    install(_config(vault, plugin_root=plugin))
    persisted = yaml.safe_load((vault / "scout-config.yaml").read_text())
    assert "auto_update" not in persisted
