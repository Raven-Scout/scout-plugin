"""Unit tests for scout.action_items.backfill."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scout.action_items.backfill import backfill_prefixes
from scout.id_map import IdMap


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


def test_backfill_registers_prefixes_written_before_a_mid_loop_failure(tmp_path, monkeypatch):
    """#42: if add_prefix_to_line raises partway through, every prefix already
    written to the file must still be registered+saved in the id-map, so a
    retry never re-mints a prefix that's already on disk."""
    data_dir = tmp_path
    items_dir = data_dir / "action-items"
    items_dir.mkdir()
    target = items_dir / "action-items-2026-06-15.md"
    target.write_text("## To Do\n- [ ] first task\n- [ ] second task\n")

    import scout.action_items.backfill as bf

    real_add = bf.add_prefix_to_line
    calls = {"n": 0}

    def flaky_add(target, *, line_number, prefix):
        calls["n"] += 1
        if calls["n"] == 2:  # second write blows up
            raise OSError("disk gremlin")
        return real_add(target, line_number=line_number, prefix=prefix)

    monkeypatch.setattr(bf, "add_prefix_to_line", flaky_add)

    with pytest.raises(OSError):
        bf.backfill_prefixes(target=target, data_dir=data_dir)

    on_disk_prefixes = {
        m.group(1) for line in target.read_text().splitlines() if (m := re.search(r"\[#([A-Z0-9]{2,8})\]", line))
    }
    id_map = IdMap.load(data_dir)
    registered = id_map.in_use_prefixes()
    assert on_disk_prefixes, "expected at least one prefix written before the failure"
    assert on_disk_prefixes <= registered, f"prefixes on disk {on_disk_prefixes} not all registered {registered}"


def test_backfill_uses_single_file_read(tmp_path, monkeypatch):
    """#41: candidate selection must parse the same bytes it filters against —
    one read, not a parse_file read plus a separate read_text."""
    data_dir = tmp_path
    items_dir = data_dir / "action-items"
    items_dir.mkdir()
    target = items_dir / "action-items-2026-06-15.md"
    target.write_text("## To Do\n- [ ] only task\n")

    reads = {"n": 0}
    real_read_text = Path.read_text

    def counting_read_text(self, *a, **k):
        if self == target:
            reads["n"] += 1
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    from scout.action_items.backfill import backfill_prefixes

    bf_plan = backfill_prefixes(target=target, data_dir=data_dir, dry_run=True)
    assert len(bf_plan) == 1
    assert reads["n"] == 1, f"expected a single read of the target, got {reads['n']}"
