"""Regression tests for the config-read fix (#207 part 2, closes #202).

The user-override layer must read the file /scout-setup actually writes —
``<vault>/scout-config.yaml`` (no dot) — and must understand the key shapes
bootstrap writes into it (top-level ``timezone``, identity under
``connectors.inputs``), normalizing them onto the canonical schema on read
without rewriting the file. Explicit canonical ``user.*`` keys always win.

The undotted file legitimately holds bootstrap state (version stamps,
connectors, schedule) alongside user overrides — those keys must pass
through untouched and never warn.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scout import config, paths
from scout.scripts import budget_check, heartbeat


def _write_vault_config(vault: Path, data: dict) -> Path:
    p = vault / "scout-config.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return p


# ----- filename: the layer must read the undotted file ----------------------


def test_config_path_is_the_undotted_vault_file(tmp_path: Path) -> None:
    assert paths.config_path(tmp_path) == tmp_path / "scout-config.yaml"


def test_load_config_reads_vault_scout_config(clean_env: None, fake_data_dir: Path) -> None:
    """#207 part 1: the override layer pointed at a dotfile no code path
    writes, so every scoutctl caller ran on packaged defaults."""
    _write_vault_config(fake_data_dir, {"user": {"timezone": "Europe/Prague"}})
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["timezone"] == "Europe/Prague"
    assert config.resolve_timezone(fake_data_dir).key == "Europe/Prague"


# ----- key shape: normalize what bootstrap actually writes ------------------


def test_legacy_top_level_timezone_reaches_user_timezone(clean_env: None, fake_data_dir: Path) -> None:
    """bootstrap._stage_version_stamp persists ``timezone`` at the TOP level;
    consumers read ``user.timezone``. Deep-merge alone leaves the packaged
    zone in place (#207 §2) — the loader must normalize on read."""
    _write_vault_config(fake_data_dir, {"timezone": "Europe/Prague", "user": {"name": "Alex"}})
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["timezone"] == "Europe/Prague"
    # The legacy key is not erased — the file is bootstrap state too.
    assert cfg["timezone"] == "Europe/Prague"


def test_explicit_user_timezone_wins_over_legacy_top_level(clean_env: None, fake_data_dir: Path) -> None:
    _write_vault_config(
        fake_data_dir,
        {"timezone": "America/New_York", "user": {"timezone": "Europe/Prague"}},
    )
    assert config.load_config(fake_data_dir)["user"]["timezone"] == "Europe/Prague"


def test_connector_inputs_identity_normalized(clean_env: None, fake_data_dir: Path) -> None:
    _write_vault_config(
        fake_data_dir,
        {
            "connectors": {
                "enabled": ["github", "slack"],
                "inputs": {"github_username": "alex-example", "user_slack_id": "U0123456789"},
            }
        },
    )
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["github_username"] == "alex-example"
    assert cfg["user"]["slack_user_id"] == "U0123456789"
    # Bootstrap state passes through untouched.
    assert cfg["connectors"]["enabled"] == ["github", "slack"]


def test_env_override_still_beats_vault_file(
    clean_env: None, fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_vault_config(fake_data_dir, {"timezone": "Europe/Prague"})
    monkeypatch.setenv("SCOUT_USER_TIMEZONE", "Pacific/Auckland")
    assert config.load_config(fake_data_dir)["user"]["timezone"] == "Pacific/Auckland"


# ----- #202: the budget governor must see the configured budget -------------


def test_budget_governor_reads_configured_budget(clean_env: None, fake_data_dir: Path) -> None:
    """#202's repro: /scout-setup renders the budget block into
    scout-config.yaml, but budget_check read the dotted file — so every
    configured budget was silently ignored and the governor enforced
    50 USD / 5 h / 80% regardless."""
    _write_vault_config(
        fake_data_dir,
        {
            "plan": {"type": "claude-max", "daily_budget_estimate_usd": 999, "rate_limit_window_hours": 7},
            "thresholds": {"skip_threshold_pct": 55, "failure_backoff_minutes": 11},
        },
    )
    cfg = budget_check.load_config(paths.config_path(fake_data_dir))
    assert cfg.daily_budget_usd == 999.0
    assert cfg.window_hours == 7
    assert cfg.skip_threshold_pct == 55.0
    assert cfg.failure_backoff_min == 11
    assert cfg.window_budget_usd == 291.38
    assert cfg.skip_threshold_usd == 160.26


def test_bootstrap_install_config_reaches_governor_and_loader(tmp_path: Path) -> None:
    """#202's asked-for integration test, against what bootstrap ACTUALLY
    writes: the Python install pipeline stamps scout-config.yaml via
    _stage_version_stamp (identity + timezone + connectors + versions) and
    does NOT render the budget template — only legacy bash-installed vaults
    carry a plan/thresholds block. So: a fresh vault must resolve its
    configured timezone through the loader, and a vault whose file carries
    the budget block (legacy render or hand calibration, as the template
    header instructs) must have the governor see those exact numbers."""
    from scout.scripts.bootstrap import BootstrapConfig, install

    plugin_root = Path(__file__).parent.parent.parent.parent  # repo root
    vault = tmp_path / "Scout"
    install(
        BootstrapConfig(
            vault=vault,
            plugin_root=plugin_root,
            instance_name="TestScout",
            instance_name_lower="testscout",
            user_name="Alex Example",
            user_email="alex@example.com",
            timezone="Europe/Prague",
            platform="macos",
            plugin_version="0.0.0",
            enabled_connectors=set(),
            connector_inputs={"max_budget": "5.00"},
            skip_jobs=True,
            skip_claude=True,
        )
    )

    # The stamp's top-level `timezone` reaches user.timezone via read-side
    # normalization — the activation every real vault gets from #207.
    assert config.resolve_timezone(vault).key == "Europe/Prague"

    # Fresh Python-bootstrap vaults carry no budget block: engine defaults.
    fresh = budget_check.load_config(paths.config_path(vault))
    assert fresh.daily_budget_usd == budget_check.DEFAULT_DAILY_BUDGET_USD

    # A vault whose file carries the template's budget block (legacy installs;
    # calibrated vaults): the governor must see the configured numbers.
    cfg_file = paths.config_path(vault)
    cfg_file.write_text(
        cfg_file.read_text(encoding="utf-8")
        + (
            "plan:\n"
            "  type: claude-max\n"
            "  daily_budget_estimate_usd: 150\n"
            "  rate_limit_window_hours: 3\n"
            "thresholds:\n"
            "  skip_threshold_pct: 90\n"
            "  failure_backoff_minutes: 30\n"
        ),
        encoding="utf-8",
    )
    calibrated = budget_check.load_config(cfg_file)
    assert calibrated.daily_budget_usd == 150.0
    assert calibrated.window_hours == 3
    assert calibrated.skip_threshold_pct == 90.0
    assert calibrated.failure_backoff_min == 30


def test_budget_check_verbose_names_the_config_file(
    clean_env: None, fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#202: the silent-defaults fallback is what made this invisible —
    --verbose must say which file it read and whether it existed."""
    budget_check.run(verbose=True, data_dir=fake_data_dir)
    out = capsys.readouterr().out
    assert str(paths.config_path(fake_data_dir)) in out
    assert "missing" in out

    _write_vault_config(fake_data_dir, {"plan": {"daily_budget_estimate_usd": 999}})
    budget_check.run(verbose=True, data_dir=fake_data_dir)
    out = capsys.readouterr().out
    assert str(paths.config_path(fake_data_dir)) in out
    assert "missing" not in out


# ----- heartbeat: its own hardcoded filename (#207 thread) ------------------


def test_heartbeat_reads_vault_config_off_peak(clean_env: None, fake_data_dir: Path) -> None:
    """heartbeat.py carried its own hardcoded '.scout-config.yaml' literal, so
    a paths.py-only fix would have left it behind (#207 comment thread). It
    must read the vault file — including the template's NESTED off_peak
    shape, which its flat line-scan never matched."""
    _write_vault_config(fake_data_dir, {"off_peak": {"start": 22, "end": 5}})
    cfg = heartbeat.load_config(paths.config_path(fake_data_dir))
    assert cfg.off_peak_start == 22
    assert cfg.off_peak_end == 5


def test_heartbeat_flat_keys_still_parse(tmp_path: Path) -> None:
    """Hand-made flat keys (the shape the old dotted workaround used) keep
    working."""
    p = tmp_path / "scout-config.yaml"
    p.write_text("off_peak_start: 21\noff_peak_end: 4\n", encoding="utf-8")
    cfg = heartbeat.load_config(p)
    assert cfg.off_peak_start == 21
    assert cfg.off_peak_end == 4


def test_heartbeat_run_uses_paths_config_path(fake_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Path] = []
    real_load = heartbeat.load_config

    def spy(config_path: Path) -> heartbeat.HeartbeatConfig:
        seen.append(config_path)
        return real_load(config_path)

    monkeypatch.setattr(heartbeat, "load_config", spy)
    monkeypatch.setattr(heartbeat, "scout_session_running", lambda *_, **__: False)
    monkeypatch.setattr(heartbeat, "vault_has_uncommitted_changes", lambda *_: False)
    monkeypatch.setattr(heartbeat, "run_budget_check", lambda *_, **__: 0)
    assert heartbeat.run(data_dir=fake_data_dir, dry_run=True) == 0
    assert seen == [paths.config_path(fake_data_dir)]


# ----- graceful degradation: this layer was dead, now it is live ------------


def test_invalid_yaml_warns_and_falls_back(
    clean_env: None, fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Turning on a config layer that was dead for every existing vault must
    not convert a stale/corrupt file into a crash: warn and run on defaults."""
    (fake_data_dir / "scout-config.yaml").write_text("key: [unclosed", encoding="utf-8")
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["timezone"] == config.DEFAULT_TIMEZONE
    assert "scout-config" in capsys.readouterr().err


def test_non_mapping_yaml_warns_and_falls_back(
    clean_env: None, fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (fake_data_dir / "scout-config.yaml").write_text("- a\n- b\n", encoding="utf-8")
    cfg = config.load_config(fake_data_dir)
    assert cfg["schema_version"] == 1
    assert "scout-config" in capsys.readouterr().err


def test_binary_corrupted_file_warns_and_falls_back(
    clean_env: None, fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Invalid UTF-8 (disk corruption, wrong file) must degrade, not raise."""
    (fake_data_dir / "scout-config.yaml").write_bytes(b"\xff\xfe\x00\x01 not yaml")
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["timezone"] == config.DEFAULT_TIMEZONE
    assert "scout-config" in capsys.readouterr().err


def test_type_mismatched_section_warns_and_keeps_defaults(
    clean_env: None, fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A scalar where the defaults define a mapping (e.g. ``user: oops``)
    would otherwise clobber the whole subtree for every consumer."""
    _write_vault_config(fake_data_dir, {"user": "oops", "budgets": 5})
    cfg = config.load_config(fake_data_dir)
    assert cfg["user"]["timezone"] == config.DEFAULT_TIMEZONE
    assert cfg["budgets"]["daily_budget_estimate_usd"] == 150
    err = capsys.readouterr().err
    assert "user" in err and "budgets" in err


def test_unknown_bootstrap_keys_pass_through_silently(
    clean_env: None, fake_data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The undotted file legitimately holds bootstrap state (#202) — version
    stamps, schedule, plan — which must merge through without noise."""
    _write_vault_config(
        fake_data_dir,
        {
            "instance": {"name": "TestScout"},
            "plugin": {"version_at_last_setup": "0.8.0"},
            "schedule": {"briefing": "07:30"},
            "plan": {"daily_budget_estimate_usd": 150},
        },
    )
    cfg = config.load_config(fake_data_dir)
    assert cfg["plugin"]["version_at_last_setup"] == "0.8.0"
    assert cfg["schedule"]["briefing"] == "07:30"
    assert capsys.readouterr().err == ""
