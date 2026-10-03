"""Tests for scout.kb.lossless — nothing identifiable is dropped by a split."""

from __future__ import annotations

from scout.kb.lossless import lossless_check

OLD = (
    "# Acme\n"
    "Config drift `#ACMEPIN` tracked in PROJ-123, see https://github.com/example/widget/pull/12.\n"
    "Owner [[people|Alex Rivera]], context [[topics/widget/retrieval]].\n"
)


def test_all_tokens_moved_is_clean(kb_repo) -> None:
    kb_repo.stage("knowledge-base/projects/acme-pilot/acme-pilot.md", OLD)
    kb_repo.commit()
    kb_repo.write("knowledge-base/projects/acme-pilot/acme-pilot.md", "# Acme\nOwner [[people|Alex Rivera]].\n")
    kb_repo.write(
        "knowledge-base/topics/infra/config-demo.md",
        "`#ACMEPIN` PROJ-123 https://github.com/example/widget/pull/12 [[topics/widget/retrieval]]\n",
    )
    assert lossless_check(kb_repo.root, "HEAD", "knowledge-base/projects/acme-pilot/acme-pilot.md") == []


def test_dropped_tokens_are_reported(kb_repo) -> None:
    kb_repo.stage("knowledge-base/projects/acme-pilot/acme-pilot.md", OLD)
    kb_repo.commit()
    kb_repo.write("knowledge-base/projects/acme-pilot/acme-pilot.md", "# Acme\n")
    missing = lossless_check(kb_repo.root, "HEAD", "knowledge-base/projects/acme-pilot/acme-pilot.md")
    assert missing == [
        "#ACMEPIN",
        "PROJ-123",
        "https://github.com/example/widget/pull/12",
        "[[people]]",
        "[[topics/widget/retrieval]]",
    ]


def test_wikilink_match_is_case_insensitive(kb_repo) -> None:
    kb_repo.stage("knowledge-base/a.md", "[[Topics/Widget/Retrieval]]\n")
    kb_repo.commit()
    kb_repo.write("knowledge-base/a.md", "[[topics/widget/retrieval|retrieval]]\n")
    assert lossless_check(kb_repo.root, "HEAD", "knowledge-base/a.md") == []


def test_issue_id_prefix_is_not_a_false_match(kb_repo) -> None:
    kb_repo.stage("knowledge-base/a.md", "PROJ-123\n")
    kb_repo.commit()
    kb_repo.write("knowledge-base/a.md", "# gone\n")
    kb_repo.write("knowledge-base/b.md", "PROJ-1234\n")
    assert lossless_check(kb_repo.root, "HEAD", "knowledge-base/a.md") == ["PROJ-123"]


def test_url_prefix_is_not_a_false_match(kb_repo) -> None:
    kb_repo.stage("knowledge-base/a.md", "https://github.com/example/widget/pull/12\n")
    kb_repo.commit()
    kb_repo.write("knowledge-base/a.md", "# gone\n")
    kb_repo.write("knowledge-base/b.md", "https://github.com/example/widget/pull/123\n")
    assert lossless_check(kb_repo.root, "HEAD", "knowledge-base/a.md") == ["https://github.com/example/widget/pull/12"]
