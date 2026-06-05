"""Integration test for templates/scripts/post-session-backfill.sh.tmpl.

Renders the template (substituting SCOUT_DIR + SCOUTCTL_BIN), runs it against
a temp git vault, and asserts the backfill prefixes open tasks and commits
exactly once — idempotently. Mirrors the deterministic session-end guarantee
behind scout-app issue #10.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/scout-plugin
TEMPLATE = REPO_ROOT / "templates" / "scripts" / "post-session-backfill.sh.tmpl"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _render(tmpl: Path, scout_dir: Path, scoutctl_bin: str) -> Path:
    text = tmpl.read_text(encoding="utf-8")
    text = text.replace("{{SCOUT_DIR}}", str(scout_dir)).replace("{{SCOUTCTL_BIN}}", scoutctl_bin)
    out = scout_dir / "scripts" / "post-session-backfill.sh"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    out.chmod(0o755)
    return out


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    scout_dir = tmp_path / "Scout"
    (scout_dir / "action-items").mkdir(parents=True)
    daily = scout_dir / "action-items" / "action-items-2026-06-04.md"
    daily.write_text(
        "# Tuesday, June 4\n\n"
        "## 🔴 Urgent\n\n"
        "- [ ] **Unprefixed urgent task** — needs an id\n"
        "- [ ] [#AB12] **Already prefixed** — leave me\n",
        encoding="utf-8",
    )
    _git(scout_dir, "init", "-q")
    _git(scout_dir, "config", "user.email", "test@example.com")
    _git(scout_dir, "config", "user.name", "test")
    _git(scout_dir, "add", "-A")
    _git(scout_dir, "commit", "-q", "-m", "seed")
    return scout_dir


def test_backfill_adds_prefix_and_commits_once(vault: Path) -> None:
    scoutctl = shutil.which("scoutctl")
    assert scoutctl, "scoutctl must be on PATH for this integration test"
    script = _render(TEMPLATE, vault, scoutctl)

    # The wrapper defaults (no arg) to ET-local "today"; the seeded daily file is
    # dated 2026-06-04 so the test passes its explicit path as the $1 override to
    # stay deterministic regardless of the real wall-clock date.
    daily_path = str(vault / "action-items" / "action-items-2026-06-04.md")
    env = {**os.environ, "SCOUT_DATA_DIR": str(vault)}
    before = _git(vault, "rev-list", "--count", "HEAD").strip()

    r1 = subprocess.run([str(script), daily_path], env=env, capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr

    daily = (vault / "action-items" / "action-items-2026-06-04.md").read_text()

    assert re.search(r"- \[ \] \[#[0-9A-HJKMNP-TV-Z]{4}\] \*\*Unprefixed urgent task\*\*", daily)
    assert "[#AB12]" in daily

    after = _git(vault, "rev-list", "--count", "HEAD").strip()
    assert int(after) == int(before) + 1, "exactly one backfill commit expected"

    r2 = subprocess.run([str(script), daily_path], env=env, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    after2 = _git(vault, "rev-list", "--count", "HEAD").strip()
    assert after2 == after, "second run must be a no-op"
