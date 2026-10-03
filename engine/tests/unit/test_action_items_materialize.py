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


def test_banner_describes_carry_forward_not_a_verbatim_promise(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_daily(vault, "2026-07-05", "# Action Items — Sunday, Jul 5, 2026\n- [ ] item\n")
    text = materialize(data_dir=vault, date=TODAY).read_text(encoding="utf-8")
    assert "items carried forward, not re-verified" in text
    assert "verbatim, items not re-verified" not in text


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


NARRATED = (
    "# Action Items — Sunday, Jul 5, 2026\n"
    "**Weekend briefing** — Last updated: 10:30 AM\n"
    "\n"
    "Long narrative paragraph about what this run did.\n"
    "\n"
    "## 🔴 Urgent\n"
    "\n"
    "- [ ] [#AAAA] 🔴 **call the bank** — due 2026-07-07 · → [[personal/task-bank]]\n"
    "  - owner: they close at 5\n"
    "\n"
    "### ⬇️ Demoted 🔴 → 🟡 by the Tue Sep 8 8:0x PM `evening-consolidation` — rows\n"
    "\n"
    "Explanation of the demotion.\n"
    "\n"
    "## 🟡 To Do\n"
    "\n"
    "- [ ] [#BBBB] 🟡 **reply to the thread**\n"
    "\n"
    "| Meeting | Time |\n"
    "|---|---|\n"
    "| Standup | 9:00 |\n"
    "\n"
    "## 📋 Scout Digest — Jul 5 (10:30)\n"
    "\n"
    "**Scout ran 3 sessions today.**\n"
)


def test_compact_drops_narration_keeps_items_and_comments(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    _write_daily(vault, "2026-07-05", NARRATED)
    text = materialize(data_dir=vault, date=TODAY).read_text(encoding="utf-8")
    assert "[#AAAA]" in text and "[#BBBB]" in text
    assert "  - owner: they close at 5" in text
    assert "Long narrative paragraph" not in text
    assert "Demoted" not in text and "Explanation of the demotion" not in text
    assert "| Standup |" not in text
    assert "Scout Digest" not in text
    assert "## 🔴 Urgent" in text and "## 🟡 To Do" in text


def test_compact_falls_back_to_verbatim_when_status_would_change(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    body = (
        "# Action Items — Sunday, Jul 5, 2026\n"
        "## Items\n"
        "### ✅ Done today by the Sun Jul 5 `weekend-briefing` — closed\n"
        "- call the bank\n"
    )
    _write_daily(vault, "2026-07-05", body)
    text = materialize(data_dir=vault, date=TODAY).read_text(encoding="utf-8")
    # Hoisting the item out of the diary H3 would flip it from done → open,
    # so the verbatim copy is kept.
    assert "### ✅ Done today" in text


def test_compact_function_is_idempotent() -> None:
    from scout.action_items.materialize import compact

    once = compact(NARRATED)
    assert compact(once) == once


def test_signature_change_in_sub_bullets_triggers_verbatim_fallback(tmp_path: Path, monkeypatch) -> None:
    """A compact() bug that silently dropped an item's comment sub-bullet must
    still be caught by the verbatim fallback — the signature has to cover
    ActionItem.details, not just the item line itself."""
    import scout.action_items.materialize as materialize_mod

    body = "## 🔴 Urgent\n- [ ] [#AAAA] 🔴 **call the bank**\n  - owner: they close at 5\n"
    _write_daily(_vault(tmp_path), "2026-07-05", "# Action Items — Sunday, Jul 5, 2026\n" + body)

    def _broken_compact(_body: str) -> str:
        # Drops the comment sub-bullet — same items/status/priority, different details.
        return "## 🔴 Urgent\n- [ ] [#AAAA] 🔴 **call the bank**\n"

    monkeypatch.setattr(materialize_mod, "compact", _broken_compact)

    created = materialize_mod.materialize(data_dir=tmp_path, date=TODAY)
    text = created.read_text(encoding="utf-8")
    assert "  - owner: they close at 5" in text
