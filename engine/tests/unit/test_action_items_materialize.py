"""Unit tests for scout.action_items.materialize (daily-file completeness invariant)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from scout.action_items.materialize import materialize

TODAY = _dt.date(2026, 7, 6)


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "action-items").mkdir()
    return tmp_path


def _write_daily(vault: Path, date: str, body: str) -> Path:
    f = vault / "action-items" / f"action-items-{date}.md"
    f.write_text(body, encoding="utf-8")
    return f


def test_creates_full_copy_from_yesterday(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_daily(
        vault,
        "2026-07-05",
        "# Action Items — Sunday, Jul 5, 2026\n"
        "**Weekend briefing** — Last updated: 10:30 AM\n"
        "\n"
        "## 🔴 Urgent\n"
        "- [ ] [#AAAA] **call the bank**\n"
        "- [ ] [#BBBB] **reply to the thread**\n",
    )

    created = materialize(data_dir=vault, date=TODAY)

    assert created == vault / "action-items" / "action-items-2026-07-06.md"
    text = created.read_text(encoding="utf-8")
    # Fresh H1 for today, provisional banner referencing the source day.
    assert text.startswith("# Action Items — Monday, Jul 6, 2026\n")
    assert "Mechanical carry-forward" in text
    assert "[[action-items-2026-07-05]]" in text
    # Old H1 + old "Last updated" header dropped; every item carried verbatim.
    assert "Sunday, Jul 5" not in text
    assert "Weekend briefing" not in text
    assert "- [ ] [#AAAA] **call the bank**" in text
    assert "- [ ] [#BBBB] **reply to the thread**" in text


def test_noop_when_today_exists(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_daily(vault, "2026-07-05", "# old\n- [ ] item\n")
    today = _write_daily(vault, "2026-07-06", "# already here\n")

    assert materialize(data_dir=vault, date=TODAY) is None
    assert today.read_text(encoding="utf-8") == "# already here\n"


def test_noop_when_no_prior_file_in_lookback(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_daily(vault, "2026-06-01", "# ancient\n- [ ] stale\n")  # 35 days back

    assert materialize(data_dir=vault, date=TODAY) is None
    assert not (vault / "action-items" / "action-items-2026-07-06.md").exists()


def test_skips_gap_days_to_most_recent(tmp_path: Path) -> None:
    """A holiday gap (no file yesterday) falls back to the newest file in range."""
    vault = _vault(tmp_path)
    _write_daily(vault, "2026-07-03", "# Action Items — Friday\n- [ ] [#CCCC] **friday item**\n")

    created = materialize(data_dir=vault, date=TODAY)

    assert created is not None
    text = created.read_text(encoding="utf-8")
    assert "[[action-items-2026-07-03]]" in text
    assert "- [ ] [#CCCC] **friday item**" in text


def test_keeps_second_line_when_not_a_bold_header(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_daily(vault, "2026-07-05", "# H1\n## 🔴 Urgent\n- [ ] [#DDDD] **x**\n")

    created = materialize(data_dir=vault, date=TODAY)

    text = created.read_text(encoding="utf-8")
    # Only the H1 was dropped — the section heading on line 2 survives.
    assert "## 🔴 Urgent" in text
    assert "- [ ] [#DDDD] **x**" in text


def test_noop_when_action_items_dir_missing(tmp_path: Path) -> None:
    # Vault without an action-items/ dir (fresh install edge): quiet no-op.
    assert materialize(data_dir=tmp_path, date=TODAY) is None
