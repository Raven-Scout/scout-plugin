from __future__ import annotations

from scout.config import load_config


def test_auto_update_defaults_present(tmp_path):
    # With no vault config, packaged defaults must supply auto_update (disabled).
    cfg = load_config(data_dir=tmp_path)
    assert cfg["auto_update"]["enabled"] is False
    assert cfg["auto_update"]["channel"] == "stable"
