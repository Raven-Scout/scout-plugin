"""Unit tests for tools/migrate-mode-names.py."""

from __future__ import annotations

import importlib.util as _ilu
import json
import shutil
import sys
from pathlib import Path

# Tools dir is outside the engine package; add to sys.path for import.
TOOLS_DIR = Path(__file__).parent.parent.parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

# Import the hyphen-named script via importlib (Python module names can't have
# hyphens, but the script filename intentionally does).
_spec = _ilu.spec_from_file_location("migrate_mode_names", TOOLS_DIR / "migrate-mode-names.py")
assert _spec is not None and _spec.loader is not None
migrate_mode_names = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(migrate_mode_names)

MODE_RENAME_MAP = migrate_mode_names.MODE_RENAME_MAP
migrate_jsonl_file = migrate_mode_names.migrate_jsonl_file
migrate_data_dir = migrate_mode_names.migrate_data_dir


FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_mode_rename_map_covers_all_old_names():
    expected = {
        "consolidation-11am": "morning-consolidation",
        "consolidation-1pm": "midday-consolidation",
        "consolidation-5pm": "afternoon-consolidation",
        "consolidation-7pm": "evening-consolidation",
        "dreaming-nightly-10pm": "dreaming-nightly",
        "dreaming-weekend-6am": "dreaming-weekend-morning",
        "dreaming-weekend-7am": "dreaming-weekend-morning",
        # Unchanged: morning-briefing, weekend-briefing, manual.
    }
    for old, new in expected.items():
        assert MODE_RENAME_MAP[old] == new


def test_migrate_jsonl_rewrites_mode_field(tmp_path):
    src = tmp_path / "connector-calls-2026-04-30.jsonl"
    shutil.copy(FIXTURES / "connector-calls-pre-rename.jsonl", src)
    n_changed = migrate_jsonl_file(src, mode_field="mode")
    assert n_changed == 4  # 4 of 5 lines had old names; morning-briefing unchanged
    rows = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]
    modes = [r["mode"] for r in rows]
    assert "consolidation-11am" not in modes
    assert "morning-consolidation" in modes
    assert "morning-briefing" in modes


def test_migrate_jsonl_is_idempotent(tmp_path):
    src = tmp_path / "connector-calls-2026-04-30.jsonl"
    shutil.copy(FIXTURES / "connector-calls-pre-rename.jsonl", src)
    migrate_jsonl_file(src, mode_field="mode")
    n_changed_second_pass = migrate_jsonl_file(src, mode_field="mode")
    assert n_changed_second_pass == 0


def test_migrate_data_dir_creates_backup(tmp_path):
    log_dir = tmp_path / ".scout-logs"
    log_dir.mkdir()
    shutil.copy(
        FIXTURES / "connector-calls-pre-rename.jsonl",
        log_dir / "connector-calls-2026-04-30.jsonl",
    )
    migrate_data_dir(tmp_path)
    backup = log_dir / ".pre-plan-5-backup" / "connector-calls-2026-04-30.jsonl"
    assert backup.exists()
    backup_rows = [json.loads(line) for line in backup.read_text().splitlines() if line.strip()]
    assert any(r["mode"] == "consolidation-11am" for r in backup_rows)
