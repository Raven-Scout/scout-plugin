"""Tests for scout.kb.git_io."""

from __future__ import annotations

from scout.kb.git_io import (
    StagedPath,
    added_lines,
    head_size,
    main_root,
    repo_root,
    staged_paths,
    staged_size,
    staged_text,
    tracked_files,
)


def test_staged_paths_new_and_modified(kb_repo) -> None:
    kb_repo.stage("knowledge-base/a.md", "one\n")
    kb_repo.commit()
    kb_repo.stage("knowledge-base/a.md", "one\ntwo\n")
    kb_repo.stage("knowledge-base/b.md", "new\n")
    got = sorted(staged_paths(kb_repo.root), key=lambda s: s.rel)
    assert got == [
        StagedPath("knowledge-base/a.md", "knowledge-base/a.md"),
        StagedPath("knowledge-base/b.md", None),
    ]


def test_staged_paths_rename_maps_to_head_source(kb_repo) -> None:
    kb_repo.stage("knowledge-base/old.md", "x" * 500 + "\n")
    kb_repo.commit()
    kb_repo.git("mv", "knowledge-base/old.md", "knowledge-base/new.md")
    assert staged_paths(kb_repo.root) == [StagedPath("knowledge-base/new.md", "knowledge-base/old.md")]


def test_staged_paths_unicode_and_spaces(kb_repo) -> None:
    kb_repo.stage("knowledge-base/topics/café notes.md", "hi\n")
    assert staged_paths(kb_repo.root) == [StagedPath("knowledge-base/topics/café notes.md", None)]
    assert staged_size(kb_repo.root, "knowledge-base/topics/café notes.md") == 3


def test_deleted_files_not_listed(kb_repo) -> None:
    kb_repo.stage("knowledge-base/a.md", "one\n")
    kb_repo.commit()
    kb_repo.git("rm", "-q", "knowledge-base/a.md")
    assert staged_paths(kb_repo.root) == []


def test_sizes_and_text(kb_repo) -> None:
    kb_repo.stage("knowledge-base/a.md", "abc\n")
    kb_repo.commit()
    kb_repo.stage("knowledge-base/a.md", "abcdef\n")
    assert head_size(kb_repo.root, "knowledge-base/a.md") == 4
    assert staged_size(kb_repo.root, "knowledge-base/a.md") == 7
    assert staged_text(kb_repo.root, "knowledge-base/a.md") == "abcdef\n"
    assert head_size(kb_repo.root, "knowledge-base/missing.md") is None


def test_added_lines_numbers_and_plus_prefixed_content(kb_repo) -> None:
    kb_repo.stage("knowledge-base/a.md", "l1\nl2\nl3\n")
    kb_repo.commit()
    kb_repo.stage("knowledge-base/a.md", "l1\n++ not a header\nl2\nl3\nnew last\n")
    assert added_lines(kb_repo.root, "knowledge-base/a.md") == [(2, "++ not a header"), (5, "new last")]


def test_added_lines_new_file(kb_repo) -> None:
    kb_repo.stage("knowledge-base/n.md", "a\nb\n")
    assert added_lines(kb_repo.root, "knowledge-base/n.md") == [(1, "a"), (2, "b")]


def test_added_lines_pure_rename_is_empty(kb_repo) -> None:
    kb_repo.stage("knowledge-base/old.md", "l1\nl2\nl3\n")
    kb_repo.commit()
    kb_repo.git("mv", "knowledge-base/old.md", "knowledge-base/new.md")
    assert added_lines(kb_repo.root, "knowledge-base/new.md", head_rel="knowledge-base/old.md") == []


def test_added_lines_rename_plus_one_line(kb_repo) -> None:
    kb_repo.stage("knowledge-base/old.md", "l1\nl2\nl3\n")
    kb_repo.commit()
    kb_repo.git("mv", "knowledge-base/old.md", "knowledge-base/new.md")
    (kb_repo.root / "knowledge-base/new.md").write_text("l1\nl2\nl3\nl4\n", encoding="utf-8")
    kb_repo.git("add", "--", "knowledge-base/new.md")
    assert added_lines(kb_repo.root, "knowledge-base/new.md", head_rel="knowledge-base/old.md") == [(4, "l4")]


def test_repo_root_and_tracked_files(kb_repo) -> None:
    sub = kb_repo.root / "knowledge-base"
    assert repo_root(sub) == kb_repo.root.resolve()
    kb_repo.write("knowledge-base/untracked.md", "u\n")
    files = tracked_files(kb_repo.root)
    assert "README.md" in files and "knowledge-base/untracked.md" in files


def test_main_root_is_itself_for_a_normal_repo(kb_repo) -> None:
    assert main_root(kb_repo.root) == kb_repo.root.resolve()


def test_main_root_resolves_to_the_main_repo_from_a_worktree(kb_repo, tmp_path) -> None:
    wt = tmp_path / "wt"
    kb_repo.git("worktree", "add", "-q", "-b", "wt-branch", str(wt))
    assert main_root(wt) == kb_repo.root.resolve()


def test_main_root_falls_back_to_repo_when_git_fails(tmp_path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    assert main_root(not_a_repo) == not_a_repo
