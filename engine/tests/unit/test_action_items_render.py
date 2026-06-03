"""Unit tests for scout.action_items.render.

Smoke-level: render a fixture file and verify the output references the
tasks the parser extracted. Pixel-perfect Rich output is intentionally
not asserted — that would be brittle.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scout.action_items.render import render

FIXTURE = Path(__file__).parent.parent / "fixtures" / "action-items-sample.md"


def test_render_runs_on_fixture_without_error() -> None:
    out = render(FIXTURE)
    assert isinstance(out, str)
    assert len(out) > 0


def test_render_includes_open_task_titles() -> None:
    out = render(FIXTURE)
    assert "Submit Lever feedback" in out
    assert "Reply to Q2 budget thread" in out


def test_render_missing_file_raises(tmp_path: Path) -> None:
    from scout.errors import ActionItemError

    missing = tmp_path / "no-such-file.md"
    with pytest.raises(ActionItemError, match="not found"):
        render(missing)


# Regression: parse() inside render must specify encoding="utf-8" so non-UTF-8
# locales don't silently mis-decode emoji headers (silently dropping sections).
# Issue #33.


def test_render_reads_with_explicit_utf8_encoding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """render() must read source markdown with explicit utf-8 encoding so
    that emoji section headers and unicode titles survive the parse step on
    a non-UTF-8 default locale."""
    md = tmp_path / "scout.md"
    md.write_text("# Title\n\n## 📌 Section\n\n- [ ] 🔴 Task with emoji\n", encoding="utf-8")

    captured: list[str | None] = []
    real_read_text = Path.read_text

    def spy(self: Path, *args: object, **kwargs: object) -> str:
        captured.append(kwargs.get("encoding"))  # type: ignore[arg-type]
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", spy)
    render(md)

    assert "utf-8" in captured, f"read_text was called without encoding=utf-8: {captured}"


# ---------------------------------------------------------------------------
# Comment binding (scout-plugin#100)
#
# The `add-comment` writer emits `  - <author>: <text>` dash bullets. The
# parser must bind those to the preceding task as comments — not drop them
# into section bullets / extra-notes — while leaving the `- Source:` /
# `- Context:` metadata vocabulary as plain bullets.
# ---------------------------------------------------------------------------


def test_parse_binds_dash_comment_to_task(tmp_path: Path) -> None:
    from scout.action_items.render import parse

    md = tmp_path / "scout.md"
    md.write_text(
        "# Title\n\n## 📌 Section\n\n"
        "- [ ] 🔴 Submit Lever feedback\n"
        "  - scout: pinged the hiring manager\n"
        "  - Vaclav Nosek: looks good to me\n",
        encoding="utf-8",
    )

    _title, _preamble, sections = parse(md)
    task = sections[0].tasks[0]
    assert [(c.author, c.text) for c in task.comments] == [
        ("scout", "pinged the hiring manager"),
        ("Vaclav Nosek", "looks good to me"),  # multi-word author binds
    ]
    # Comments are not leaked into section-level bullets.
    assert not any("hiring manager" in b.text for b in sections[0].bullets)


def test_parse_keeps_metadata_subbullets_out_of_comments(tmp_path: Path) -> None:
    from scout.action_items.render import parse

    md = tmp_path / "scout.md"
    md.write_text(
        "# Title\n\n## 📌 Section\n\n"
        "- [ ] 🔴 Ship the thing\n"
        "  - Source: Linear (AI-3325)\n"
        "  - Context: [[kai-backend]]\n"
        "  - scout: actually started it\n",
        encoding="utf-8",
    )

    _title, _preamble, sections = parse(md)
    task = sections[0].tasks[0]
    # Only the real comment binds; Source/Context stay metadata, not comments.
    assert [(c.author, c.text) for c in task.comments] == [("scout", "actually started it")]


def test_add_comment_render_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: a comment written by add_comment renders under its task."""
    import datetime as dt

    from scout.action_items.add_comment import add_comment
    from scout.action_items.render import parse

    data_dir = tmp_path
    daily = data_dir / "action-items" / "action-items-2026-04-26.md"
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_text("# Action Items\n\n## To Do\n\n- [ ] 🔴 Followup with vendor\n")
    monkeypatch.setattr("scout.action_items.add_comment._today", lambda: dt.date(2026, 4, 26))

    add_comment(by_subject="vendor", comment="left a voicemail", data_dir=data_dir)

    _title, _preamble, sections = parse(daily)
    task = sections[0].tasks[0]
    assert [(c.author, c.text) for c in task.comments] == [("scout", "left a voicemail")]
    # And it surfaces in the rendered HTML, not buried in extra-notes.
    html_out = render(daily)
    assert "left a voicemail" in html_out
