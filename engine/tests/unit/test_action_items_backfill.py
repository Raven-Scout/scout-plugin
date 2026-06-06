"""Unit tests for scout.action_items.backfill."""

from __future__ import annotations

from pathlib import Path

from scout.action_items.backfill import backfill_prefixes


def test_backfill_skips_lines_with_semantic_tag(fake_data_dir: Path, tmp_path: Path) -> None:
    """A line already carrying a variable-length [#TAG] must NOT get a second
    prefix prepended (the double-prefix hazard that motivated #117). Only
    genuinely bare task lines should be backfilled."""
    f = tmp_path / "action-items-2026-06-06.md"
    f.write_text(
        "# T\n\n## 🔴 Urgent\n\n"
        "- [ ] [#MIRO] **Miro 1:1** — sends\n"  # semantic tag: skip
        "- [ ] [#AI3026] **Validate tracing**\n"  # 6-char tag: skip
        "- [ ] **Bare unprefixed task** — needs id\n",  # bare: gets a prefix
        encoding="utf-8",
    )
    # backfill_prefixes returns (line_number, prefix, title) tuples for the
    # lines it would prefix. dry_run avoids touching the file or id-map.
    added = backfill_prefixes(target=f, data_dir=fake_data_dir, dry_run=True)
    titles = {title for _, _, title in added}
    assert all("Miro" not in t and "Validate tracing" not in t for t in titles)
    assert len(added) == 1  # only the bare line
