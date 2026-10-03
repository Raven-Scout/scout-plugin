"""Tests for the `scoutctl kb` command group."""

from __future__ import annotations

from typer.testing import CliRunner

from scout import cli

runner = CliRunner()


def test_kb_lint_staged_blocks_in_block_mode(kb_repo) -> None:
    kb_repo.write("scout-config.yaml", "kb_lint:\n  mode: block\n")
    kb_repo.stage("knowledge-base/big.md", "x" * 20000 + "\n")
    result = runner.invoke(cli.app, ["kb", "lint", "--staged", "--repo", str(kb_repo.root)])
    assert result.exit_code == 1


def test_kb_lint_report_lists_over_budget(kb_repo) -> None:
    kb_repo.write("knowledge-base/big.md", "x" * 20000 + "\n")
    result = runner.invoke(cli.app, ["kb", "lint", "--report", "--repo", str(kb_repo.root)])
    assert result.exit_code == 0
    assert "knowledge-base/big.md" in result.output


def test_kb_lint_lossless_exit_codes(kb_repo) -> None:
    kb_repo.stage("knowledge-base/a.md", "tracked in PROJ-9\n")
    kb_repo.commit()
    kb_repo.write("knowledge-base/a.md", "gone\n")
    args = [
        "kb",
        "lint",
        "--lossless-rev",
        "HEAD",
        "--lossless-path",
        "knowledge-base/a.md",
        "--repo",
        str(kb_repo.root),
    ]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 1 and "PROJ-9" in result.output
    kb_repo.write("knowledge-base/b.md", "PROJ-9\n")
    assert runner.invoke(cli.app, args).exit_code == 0


def test_kb_install_hook(kb_repo) -> None:
    result = runner.invoke(cli.app, ["kb", "install-hook", "--repo", str(kb_repo.root)])
    assert result.exit_code == 0
    assert "pre-commit" in result.output
