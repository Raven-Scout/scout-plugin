"""Tests for scout.kb.lint_config — budgets, scopes and mode resolution."""

from __future__ import annotations

from pathlib import Path

from scout.kb.lint_config import glob_match, load_lint_config


def test_glob_single_star_does_not_cross_slash() -> None:
    assert glob_match("knowledge-base/projects/*/*.md", "knowledge-base/projects/widget/widget.md")
    assert not glob_match("knowledge-base/projects/*/*.md", "knowledge-base/projects/widget/ref/x.md")


def test_glob_double_star_matches_any_depth() -> None:
    assert glob_match("knowledge-base/topics/**", "knowledge-base/topics/widget/retrieval.md")
    assert glob_match("**/extracted/**", "knowledge-base/personal/pet-records/extracted/doc1.md")


def test_defaults_budgets(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    assert cfg.mode == "report"
    assert cfg.budget_for("action-items/action-items-2026-09-28.md") == 61440
    assert cfg.budget_for("knowledge-base/sources/2026-09/2026-09-07-acme-daily.md") == 8192
    assert cfg.budget_for("knowledge-base/topics/widget/retrieval.md") == 10240
    assert cfg.budget_for("knowledge-base/projects/acme-pilot/acme-pilot.md") == 15360
    assert cfg.budget_for("knowledge-base/session-log/2026-09.md") == 98304
    assert cfg.budget_for("knowledge-base/people/zoe.md") == 10240
    assert cfg.budget_for("action-items/digests/2026-09-28.md") == 30720


def test_scope_and_exclusions(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    assert cfg.in_scope("knowledge-base/projects/widget/widget.md")
    assert cfg.in_scope("action-items/action-items-2026-09-28.md")
    assert not cfg.in_scope("SKILL.md")
    assert not cfg.in_scope("docs/superpowers/plans/x.md")
    assert not cfg.in_scope("knowledge-base/projects/acme-pilot/sprint-brief-lanes.csv")
    assert not cfg.in_scope("action-items/archive/action-items-2026-08-01.md")
    assert not cfg.in_scope("knowledge-base/personal/pet-records/extracted/doc1.md")


def test_logs_topics_and_line_limits(tmp_path: Path) -> None:
    cfg = load_lint_config(tmp_path)
    assert cfg.is_log("knowledge-base/session-log.md")
    assert cfg.is_log("knowledge-base/scout-mistake-audit/pattern-217-referent.md")
    assert cfg.is_log("action-items/digests/2026-09-28.md")
    assert not cfg.is_log("knowledge-base/projects/acme-pilot/acme-pilot.md")
    assert not cfg.is_log("action-items/action-items-2026-09-28.md")
    assert cfg.is_topic("knowledge-base/topics/widget/retrieval.md")
    assert cfg.line_limit("action-items/action-items-2026-09-28.md") == 500
    assert cfg.line_limit("knowledge-base/projects/widget/widget.md") == 1500


def test_vault_overrides_mode_and_budgets(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text(
        "kb_lint:\n  mode: block\nkb_budgets:\n  'knowledge-base/topics/**': 100\n",
        encoding="utf-8",
    )
    cfg = load_lint_config(tmp_path)
    assert cfg.mode == "block"
    assert cfg.budget_for("knowledge-base/topics/widget/retrieval.md") == 100
    # defaults still present after the override
    assert cfg.budget_for("knowledge-base/sources/2026-09/x.md") == 8192


def test_invalid_mode_falls_back_to_report(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text("kb_lint:\n  mode: strict\n", encoding="utf-8")
    assert load_lint_config(tmp_path).mode == "report"


def test_null_budget_means_unlimited(tmp_path: Path) -> None:
    (tmp_path / "scout-config.yaml").write_text("kb_budgets:\n  'knowledge-base/big/**': null\n", encoding="utf-8")
    assert load_lint_config(tmp_path).budget_for("knowledge-base/big/x.md") is None
