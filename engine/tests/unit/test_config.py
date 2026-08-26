"""Unit tests for scout.config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout import config
from scout.errors import ConfigError


def _write(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data))


def test_load_config_returns_defaults_when_no_user_override(clean_env: None, fake_data_dir: Path) -> None:
    cfg = config.load_config(fake_data_dir)
    assert "budgets" in cfg
    assert "thresholds" in cfg
    assert cfg["schema_version"] == 1


def test_user_config_overrides_defaults(clean_env: None, fake_data_dir: Path) -> None:
    _write(
        fake_data_dir / "scout-config.yaml",
        {"budgets": {"daily_budget_estimate_usd": 999}},
    )
    cfg = config.load_config(fake_data_dir)
    assert cfg["budgets"]["daily_budget_estimate_usd"] == 999
    # Other default keys preserved
    assert "max_per_session_usd" in cfg["budgets"]


def test_deep_merge_preserves_sibling_keys(clean_env: None, fake_data_dir: Path) -> None:
    _write(
        fake_data_dir / "scout-config.yaml",
        {"user": {"email": "test@example.com"}},
    )
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["email"] == "test@example.com"
    # Defaults for other user keys preserved
    assert "timezone" in cfg["user"]


def test_env_var_overrides_user_config(clean_env: None, fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(
        fake_data_dir / "scout-config.yaml",
        {"user": {"email": "user@example.com"}},
    )
    monkeypatch.setenv("SCOUT_USER_EMAIL", "env@example.com")
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["email"] == "env@example.com"


def test_invalid_yaml_degrades_to_defaults(
    clean_env: None, fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#207: the vault layer warns and degrades instead of raising — a stale
    file must not block a run now that the layer is actually live. (_read_yaml
    itself still raises ConfigError; see test_config_vault_file.py.)"""
    (fake_data_dir / "scout-config.yaml").write_text("key: [unclosed")
    cfg = config.load_config(fake_data_dir)
    assert cfg["schema_version"] == 1
    assert "Invalid YAML" in capsys.readouterr().err
    with pytest.raises(ConfigError, match="Invalid YAML"):
        config._read_yaml(fake_data_dir / "scout-config.yaml")


def test_non_mapping_yaml_degrades_to_defaults(
    clean_env: None, fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (fake_data_dir / "scout-config.yaml").write_text("- a\n- b\n")
    cfg = config.load_config(fake_data_dir)
    assert cfg["schema_version"] == 1
    assert "YAML mapping" in capsys.readouterr().err
    with pytest.raises(ConfigError, match="YAML mapping"):
        config._read_yaml(fake_data_dir / "scout-config.yaml")
