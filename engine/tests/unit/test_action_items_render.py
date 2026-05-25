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
