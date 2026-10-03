"""Tests for scout.kb.lint — the pre-commit ratchet."""

from __future__ import annotations

import io
import json
import subprocess
from datetime import UTC, datetime, timedelta

from scout.kb.lint import OVERRIDE_ENV, lint_staged, override_count, run_hook
from scout.kb.lint_config import load_lint_config

TOPIC = "knowledge-base/topics/widget/retrieval.md"
PROJECT = "knowledge-base/projects/acme-pilot/acme-pilot.md"


def _small_budgets(kb_repo, mode: str = "block") -> None:
    kb_repo.write(
        "scout-config.yaml",
        f"kb_lint:\n  mode: {mode}\nkb_budgets:\n"
        "  'knowledge-base/projects/**': 100\n  'knowledge-base/topics/**': 100\n",
    )


def _checks(result) -> list[tuple[str, str, bool]]:
    return sorted((f.path, f.check, f.blocking) for f in result.findings)


def test_under_budget_passes(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "short ([[x]])\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking == []


def test_new_file_over_budget_blocks(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    assert ("knowledge-base/projects/acme-pilot/acme-pilot.md", "size", True) in _checks(
        lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    )


def test_over_budget_file_growing_blocks_shrinking_passes(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "a\n" * 100)
    kb_repo.commit()
    cfg = load_lint_config(kb_repo.root)
    kb_repo.stage(PROJECT, "a\n" * 101)
    assert [f.check for f in lint_staged(kb_repo.root, cfg).blocking] == ["size"]
    kb_repo.stage(PROJECT, "a\n" * 99)
    assert lint_staged(kb_repo.root, cfg).blocking == []


def test_rename_without_growth_passes(kb_repo) -> None:
    _small_budgets(kb_repo)
    diary = "## §59 · 2026-09-07 (1:0x AM ET, `overnight-research`)\n"
    kb_repo.stage("knowledge-base/projects/old/old.md", diary + "x" * 2000 + "\n" + "a\n" * 100)
    kb_repo.commit()
    kb_repo.git("mv", "knowledge-base/projects/old/old.md", "knowledge-base/projects/old/renamed.md")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking == []


def test_added_diary_heading_blocks_existing_one_does_not(kb_repo) -> None:
    diary = "## §59 · 🔴 2026-09-07 (1:0x AM ET, `overnight-research`): finding\n"
    kb_repo.stage(PROJECT, "# Acme\n" + diary)
    kb_repo.commit()
    cfg = load_lint_config(kb_repo.root)
    kb_repo.stage(PROJECT, "# Acme\n" + diary + "status: live\n")
    assert lint_staged(kb_repo.root, cfg).blocking == []
    kb_repo.stage(PROJECT, "# Acme\n" + diary + "status: live\n## ✅ 2026-09-28 (8:0x AM ET, `morning-briefing`)\n")
    blocking = lint_staged(kb_repo.root, cfg).blocking
    assert [(f.check, f.line) for f in blocking] == [("diary", 4)]


def test_diary_heading_allowed_in_log_paths(kb_repo) -> None:
    kb_repo.stage("knowledge-base/session-log/2026-09.md", "## 2026-09-28 (8:0x AM ET, `morning-briefing`)\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking == []


def test_diary_heading_blocked_in_daily_action_items(kb_repo) -> None:
    kb_repo.stage(
        "action-items/action-items-2026-09-28.md",
        "### ⬇️ Re-tiered Sat Aug 29 (`weekend-briefing`) — 92 rows\n",
    )
    assert [f.check for f in lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).blocking] == ["diary"]


def test_mega_line_limits(kb_repo) -> None:
    cfg = load_lint_config(kb_repo.root)
    kb_repo.stage(PROJECT, "x" * 1501 + "\n")
    assert "mega-line" in [f.check for f in lint_staged(kb_repo.root, cfg).blocking]
    kb_repo.git("reset", "-q")
    kb_repo.stage("action-items/action-items-2026-09-28.md", "- [ ] " + "y" * 600 + "\n")
    assert [f.check for f in lint_staged(kb_repo.root, cfg).blocking] == ["mega-line"]


def test_out_of_scope_files_ignored(kb_repo) -> None:
    kb_repo.stage("SKILL.md", "## §1 · 2026-09-28 (8:0x AM, `morning-briefing`)\n" + "x" * 5000 + "\n")
    assert lint_staged(kb_repo.root, load_lint_config(kb_repo.root)).findings == []


def test_topic_citation_and_dangling_link_are_warnings(kb_repo) -> None:
    kb_repo.stage("knowledge-base/sources/2026-09/2026-09-07-acme-daily.md", "# Acme daily\n")
    kb_repo.commit()
    kb_repo.stage(
        TOPIC,
        "# Retrieval\n\nWidget chunks PDFs.\n\nIt uses BM25 ([[2026-09-07-acme-daily]]).\n\nSee [[no-such-note]].\n",
    )
    result = lint_staged(kb_repo.root, load_lint_config(kb_repo.root))
    assert result.blocking == []
    assert sorted((f.check, f.line) for f in result.warnings) == [("citation", 3), ("dangling-link", 7)]


def test_lint_staged_no_head(tmp_path) -> None:
    repo = tmp_path / "fresh"
    (repo / "knowledge-base").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "knowledge-base" / "a.md").write_text("x" * 20000 + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    result = lint_staged(repo, load_lint_config(repo))
    assert ("size", True) in [(f.check, f.blocking) for f in result.findings]


def test_lint_staged_in_worktree(kb_repo, tmp_path) -> None:
    wt = tmp_path / "wt"
    kb_repo.git("worktree", "add", "-q", "-b", "wt-branch", str(wt))
    (wt / "scout-config.yaml").write_text("kb_budgets:\n  'knowledge-base/projects/**': 100\n", encoding="utf-8")
    p = wt / PROJECT
    p.parent.mkdir(parents=True)
    p.write_text("x" * 200 + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", PROJECT], cwd=wt, check=True)
    assert [f.check for f in lint_staged(wt, load_lint_config(wt)).blocking] == ["size"]


def test_run_hook_report_mode_logs_and_allows(kb_repo) -> None:
    _small_budgets(kb_repo, mode="report")
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 0
    assert "report mode" in out.getvalue()
    rows = (kb_repo.root / ".scout-logs" / "kb-lint.jsonl").read_text().splitlines()
    assert json.loads(rows[0])["check"] == "size"


def test_run_hook_block_mode_blocks(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    out = io.StringIO()
    assert run_hook(kb_repo.root, env={}, out=out) == 1
    assert "Commit blocked" in out.getvalue()


def test_run_hook_override_from_worktree_logs_to_main_vault(kb_repo, tmp_path) -> None:
    wt = tmp_path / "wt"
    kb_repo.git("worktree", "add", "-q", "-b", "wt-branch", str(wt))
    (wt / "scout-config.yaml").write_text(
        "kb_lint:\n  mode: block\nkb_budgets:\n  'knowledge-base/projects/**': 100\n",
        encoding="utf-8",
    )
    p = wt / PROJECT
    p.parent.mkdir(parents=True)
    p.write_text("x" * 200 + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", PROJECT], cwd=wt, check=True)
    env = {OVERRIDE_ENV: "two fix attempts failed", "SCOUT_MODE": "morning-briefing"}
    assert run_hook(wt, env=env, out=io.StringIO()) == 0
    main_log = kb_repo.root / ".scout-logs" / "lint-overrides.log"
    assert main_log.exists()
    row = json.loads(main_log.read_text().splitlines()[0])
    assert row["reason"] == "two fix attempts failed"
    assert not (wt / ".scout-logs").exists()


def test_run_hook_override_allows_and_logs(kb_repo) -> None:
    _small_budgets(kb_repo)
    kb_repo.stage(PROJECT, "x" * 200 + "\n")
    env = {OVERRIDE_ENV: "two fix attempts failed", "SCOUT_MODE": "morning-briefing"}
    assert run_hook(kb_repo.root, env=env, out=io.StringIO()) == 0
    row = json.loads((kb_repo.root / ".scout-logs" / "lint-overrides.log").read_text().splitlines()[0])
    assert row["reason"] == "two fix attempts failed"
    assert row["mode"] == "morning-briefing"
    assert row["files"] == [PROJECT]


def test_run_hook_clean_commit_writes_nothing(kb_repo) -> None:
    kb_repo.stage(PROJECT, "fine\n")
    assert run_hook(kb_repo.root, env={}, out=io.StringIO()) == 0
    assert not (kb_repo.root / ".scout-logs" / "kb-lint.jsonl").exists()


def test_override_count_window(tmp_path) -> None:
    now = datetime(2026, 9, 28, 12, tzinfo=UTC)
    log = tmp_path / "lint-overrides.log"
    log.write_text(
        json.dumps({"ts": (now - timedelta(days=1)).isoformat()})
        + "\n"
        + json.dumps({"ts": (now - timedelta(days=20)).isoformat()})
        + "\n"
        + "not json\n",
        encoding="utf-8",
    )
    assert override_count(tmp_path, days=14, now=now) == 1
    assert override_count(tmp_path / "missing", days=14, now=now) == 0


def test_over_budget_ranks_by_excess(kb_repo) -> None:
    from scout.kb.lint import over_budget

    kb_repo.write("scout-config.yaml", "kb_budgets:\n  'knowledge-base/**': 100\n")
    kb_repo.write("knowledge-base/a.md", "x" * 150)
    kb_repo.write("knowledge-base/b.md", "x" * 900)
    kb_repo.write("knowledge-base/c.md", "x" * 50)
    kb_repo.write("SKILL.md", "x" * 9000)
    rows = over_budget(kb_repo.root, load_lint_config(kb_repo.root))
    assert rows == [("knowledge-base/b.md", 900, 100), ("knowledge-base/a.md", 150, 100)]
