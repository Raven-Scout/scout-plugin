"""Per-file migration: split single-file Wishlist + Research Queue into per-file items.

Self-contained engine module. The pure parsing/rendering helpers are ported verbatim
from the unit-tested prototype; the idempotent driver (`needs_migration`,
`migrate_perfile`, and the thin run-log builder) is engine-specific so the migration
is safe to run on every upgrade.
"""

from __future__ import annotations

import datetime as _dt
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Exact run-log header (matches the fresh-install template). After migration,
# knowledge-base/research-queue.md is reduced to this header + a continuity note.
RUN_LOG_HEADER = (
    "# Research Queue — run log\n"
    "\n"
    "Per-topic research items live as files in [[research-queue/]]. This file is the thin "
    'run log: the research session records its latest "Last verified …" continuity note here.\n'
    "\n"
    "---\n"
    "\n"
)

# First line of the run-log header (no trailing newline) — used as the migrated-marker
# short-circuit so `needs_migration` can't be fooled by checklist lines inside a preserved
# continuity note. Derived from RUN_LOG_HEADER so the two can never drift.
_RUN_LOG_HEADER_FIRST_LINE = RUN_LOG_HEADER.split("\n", 1)[0]


@dataclass
class Item:
    title: str
    status: str
    priority: str
    date: str | None
    source: str | None
    body: str
    area: str | None = None


def _strip_markers(text: str) -> tuple[str, str, str]:
    status = "open"
    priority = "medium"
    t = text.strip()
    m = re.match(r"^\[(in progress|done)\]\s*", t, re.I)
    if m:
        status = "in-progress" if m.group(1).lower() == "in progress" else "done"
        t = t[m.end() :]
    m = re.match(r"^(HIGH|MEDIUM|LOW)\b\s*(—|-|–)?\s*", t)
    if m:
        priority = m.group(1).lower()
        t = t[m.end() :]
    return status, priority, t.strip()


def parse_wishlist_item(bullet: str, in_done_file: bool = False) -> Item:
    text = bullet.strip()
    m = re.match(r"\*\*(.+?)\*\*(.*)$", text, re.S)
    lead, rest = (m.group(1), m.group(2)) if m else (text, "")
    status, priority, title_seg = _strip_markers(lead)
    title = title_seg.strip()
    if in_done_file:
        status = "done"
    date = None
    source = None
    pm = re.match(r"\s*\((.+?)\)", rest, re.S)
    if pm:
        paren = pm.group(1)
        dm = DATE_RE.search(paren)
        if dm:
            date = dm.group(1)
        src = re.sub(r"^\d{4}-\d{2}-\d{2}\s*(—|-|–)?\s*", "", paren).strip()
        source = src or None
        rest = rest[pm.end() :]
        rest = rest.lstrip(". \t")
    body = rest.strip()
    return Item(title=title, status=status, priority=priority, date=date, source=source, body=body)


def parse_research_item(line: str, area: str | None = None) -> Item:
    t = line.strip()
    m = re.match(r"^[-*]\s*\[( |x|X)\]\s*", t)
    status = "open"
    if m:
        status = "done" if m.group(1).lower() == "x" else "open"
        t = t[m.end() :]
    priority = "medium"
    if t.startswith("🔴"):
        priority = "urgent"
    elif t.startswith("🟢"):
        priority = "low"
    elif t.startswith("🟡"):
        priority = "medium"
    t = re.sub(r"^(🔴|🟡|🟢|🔵)\s*", "", t)
    bm = re.match(r"\*\*(.+?)\*\*(.*)$", t, re.S)
    lead, rest = (bm.group(1), bm.group(2)) if bm else (t, "")
    lead_stripped = lead.strip()
    if lead_stripped.upper().startswith("START IMMEDIATELY"):
        priority = "urgent"
    title = re.sub(r"^START IMMEDIATELY\s*(—|-|–)\s*", "", lead_stripped, flags=re.I).strip()
    date = None
    dm = DATE_RE.search(rest)
    if dm:
        date = dm.group(1)
    return Item(title=title, status=status, priority=priority, date=date, source=None, body=rest.strip(), area=area)


def slugify(title: str, max_words: int = 8) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    words = [w for w in re.split(r"[\s-]+", s) if w]
    return "-".join(words[:max_words])


def filename_for(item: Item, default_date: str) -> str:
    date = item.date or default_date
    return f"{date}-{slugify(item.title)}.md"


def _unique_path(out_dir: Path, name: str) -> Path:
    """Return a non-colliding path in out_dir, suffixing ``-2``, ``-3``, … on collision.

    Migration is a destructive one-shot: two items sharing a date+slug must NOT overwrite each
    other (silent data loss). Disambiguate instead so both are preserved. FIX 1 prevents re-runs,
    so suffixes never accumulate across upgrades.
    """
    p = out_dir / name
    if not p.exists():
        return p
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 2
    while (out_dir / f"{stem}-{i}{suffix}").exists():
        i += 1
    return out_dir / f"{stem}-{i}{suffix}"


def _yq(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_item(item: Item) -> str:
    fm = ["---", f"title: {_yq(item.title)}", f"status: {item.status}", f"priority: {item.priority}"]
    if item.date:
        fm.append(f"date: {item.date}")
    if item.source:
        fm.append(f"source: {_yq(item.source)}")
    if item.area:
        fm.append(f"area: {_yq(item.area)}")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {item.title}\n\n{item.body}\n"


def split_bullets(text: str) -> list[str]:
    """Split markdown into top-level bullet chunks (each starting with ``* `` or ``- ``).

    CommonMark-style continuation: a flush-left (non-indented) non-bullet line CLOSES the current
    bullet and is NOT captured into it — continuation/body lines must be blank or indented. This
    matches the prototype already validated against the real vault; git preserves the
    pre-migration originals.
    """
    items: list[str] = []
    cur: list[str] | None = None
    for line in text.splitlines():
        if re.match(r"^[*-]\s+\S", line):
            if cur is not None:
                items.append("\n".join(cur).strip())
            cur = [re.sub(r"^[*-]\s+", "", line)]
        elif cur is not None and (line.startswith((" ", "\t")) or line.strip() == ""):
            cur.append(line)
        elif cur is not None:
            items.append("\n".join(cur).strip())
            cur = None
    if cur is not None:
        items.append("\n".join(cur).strip())
    return [i for i in items if i]


def migrate_wishlist_file(src: Path, out_dir: Path, in_done_file: bool, default_date: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for bullet in split_bullets(src.read_text()):
        item = parse_wishlist_item(bullet, in_done_file=in_done_file)
        if not item.title:
            continue
        dest = _unique_path(out_dir, filename_for(item, default_date))
        dest.write_text(render_item(item))
        count += 1
    return count


def _heading_area(heading: str) -> str | None:
    core = heading.strip()
    core = re.sub(r"^(🔴|🟡|🟢|🔵|🛌|✅|🎯)\s*", "", core).strip()
    parts = re.split(r"\s+(?:—|–|-)\s+", core, maxsplit=1)
    if len(parts) == 2 and re.match(r"^(🔴|🟡|🟢|🔵|🛌|✅|🎯|done\b|wip\b|in[\s-]?progress\b)", parts[1].strip(), re.I):
        core = parts[0].strip()
    if core.lower() == "queue":
        return None
    return slugify(core) or None


def split_research_items(text: str):
    h2_area = None
    h3_area = None
    for line in text.splitlines():
        h2 = re.match(r"^##\s+(.+)$", line)
        h3 = re.match(r"^###\s+(.+)$", line)
        if h2:
            h2_area = _heading_area(h2.group(1))
            h3_area = None
            continue
        if h3:
            h3_area = _heading_area(h3.group(1))
            continue
        if re.match(r"^[-*]\s*\[( |x|X)\]", line):
            yield line, (h3_area or h2_area)


def migrate_research_file(src: Path, out_dir: Path, default_date: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for line, area in split_research_items(src.read_text()):
        item = parse_research_item(line, area=area)
        if not item.title:
            continue
        dest = _unique_path(out_dir, filename_for(item, default_date))
        dest.write_text(render_item(item))
        count += 1
    return count


# --- Engine-specific idempotent driver -------------------------------------

_WISHLIST_FILES = ("Wishlist.md", "Wishlist-in-progress.md", "Wishlist-done.md")
_RESEARCH_QUEUE_REL = ("knowledge-base", "research-queue.md")


def _research_queue_has_items(text: str) -> bool:
    """True iff the research-queue.md still holds live items (a Queue heading or a checklist).

    A migrated run-log always starts with the exact run-log header. Short-circuit on it so a
    preserved continuity note containing checklist lines or a "## Queue" string never causes a
    false-positive re-migration on the next upgrade. The header literal is derived from
    ``RUN_LOG_HEADER`` so the two can't drift.
    """
    if text.startswith(_RUN_LOG_HEADER_FIRST_LINE):
        return False
    for line in text.splitlines():
        if re.match(r"^##\s+Queue\b", line):
            return True
        if re.match(r"^[-*]\s*\[( |x|X)\]", line):
            return True
    return False


def needs_migration(vault: Path) -> bool:
    """True iff the vault is still in legacy (single-file) format.

    Legacy if ANY single-file wishlist exists, OR research-queue.md still holds items
    (a ``## Queue`` heading or a ``- [ ]``/``- [x]`` checklist line) rather than being
    the thin run-log. False on a fully-migrated vault and on a vault that never had
    these files.
    """
    docs = vault / "docs"
    for name in _WISHLIST_FILES:
        if (docs / name).exists():
            return True
    rq = vault.joinpath(*_RESEARCH_QUEUE_REL)
    if rq.exists() and _research_queue_has_items(rq.read_text()):
        return True
    return False


def _last_verified_body(text: str) -> str:
    """Return the LAST paragraph beginning with ``**Last verified`` (continuity note).

    A paragraph is a run of consecutive non-blank lines. Falls back to ``_No runs yet._``
    when no such paragraph is present.
    """
    paragraphs: list[str] = []
    cur: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            if cur:
                paragraphs.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        paragraphs.append("\n".join(cur))

    for para in reversed(paragraphs):
        if para.lstrip().startswith("**Last verified"):
            return para.strip()
    return "_No runs yet._"


def _build_run_log(preserved_body: str) -> str:
    """Assemble the thin run-log: exact header + preserved continuity note + trailing newline."""
    return RUN_LOG_HEADER + preserved_body.rstrip("\n") + "\n"


def migrate_perfile(vault: Path, default_date: str | None = None) -> dict:
    """Idempotently migrate a vault's single-file Wishlist + Research Queue to per-file items.

    No-ops (and touches nothing) when the vault is already in per-file format.
    """
    if not needs_migration(vault):
        return {"migrated": False}

    default_date = default_date or _dt.date.today().isoformat()

    docs = vault / "docs"
    wishlist_out = docs / "wishlist"
    rq = vault.joinpath(*_RESEARCH_QUEUE_REL)
    research_out = vault / "knowledge-base" / "research-queue"

    # Migrate wishlist files (count items across all that exist).
    wishlist_count = 0
    for name, in_done_file in (("Wishlist.md", False), ("Wishlist-in-progress.md", False), ("Wishlist-done.md", True)):
        src = docs / name
        if src.exists():
            wishlist_count += migrate_wishlist_file(src, wishlist_out, in_done_file, default_date)

    # Migrate research items; capture the preserved continuity note from the ORIGINAL
    # research-queue.md BEFORE rewriting it.
    research_count = 0
    preserved_body: str | None = None
    if rq.exists():
        original = rq.read_text()
        preserved_body = _last_verified_body(original)
        research_count = migrate_research_file(rq, research_out, default_date)

    # Delete the old single-file wishlist artifacts.
    for name in _WISHLIST_FILES:
        src = docs / name
        if src.exists():
            src.unlink()

    # Rewrite research-queue.md to the thin run-log (only if it existed).
    if preserved_body is not None:
        rq.write_text(_build_run_log(preserved_body))

    return {"migrated": True, "wishlist": wishlist_count, "research": research_count}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print("usage: migrate_perfile.py <vault>", file=sys.stderr)
        return 2
    result = migrate_perfile(Path(args[0]))
    print(result)
    return 0


__all__ = [
    "DATE_RE",
    "RUN_LOG_HEADER",
    "Item",
    "filename_for",
    "main",
    "migrate_perfile",
    "migrate_research_file",
    "migrate_wishlist_file",
    "needs_migration",
    "parse_research_item",
    "parse_wishlist_item",
    "render_item",
    "slugify",
    "split_bullets",
    "split_research_items",
]


if __name__ == "__main__":
    raise SystemExit(main())
