"""One-time migration: split the single-file Wishlist and Research Queue into
per-file items with YAML frontmatter (see
docs/superpowers/specs/2026-06-16-wishlist-research-queue-per-file-design.md).

Pure parse helpers are unit-tested; the `migrate()` driver does the file I/O."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path

DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


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
    """Pull leading `[in progress]`/`[done]` state + `HIGH`/`MEDIUM` priority
    off the start of a wishlist title segment. Returns (status, priority, rest)."""
    status = "open"
    priority = "medium"
    t = text.strip()
    m = re.match(r"^\[(in progress|done)\]\s*", t, re.I)
    if m:
        status = "in-progress" if m.group(1).lower() == "in progress" else "done"
        t = t[m.end():]
    m = re.match(r"^(HIGH|MEDIUM|LOW)\b\s*(—|-|–)?\s*", t)
    if m:
        priority = m.group(1).lower()
        t = t[m.end():]
    return status, priority, t.strip()


def parse_wishlist_item(bullet: str, in_done_file: bool = False) -> Item:
    """Parse one wishlist bullet (without its leading `* `). The bolded lead
    `**…**` carries state/priority/title; a trailing `(date — source)` is lifted."""
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
        rest = rest[pm.end():]
        rest = rest.lstrip(". \t")
    body = rest.strip()
    return Item(title=title, status=status, priority=priority,
                date=date, source=source, body=body)


def parse_research_item(line: str, area: str | None = None) -> Item:
    """Parse one research-queue checklist line:
    `- [ ] 🔴 **START IMMEDIATELY — Title** body` (emoji = priority)."""
    t = line.strip()
    m = re.match(r"^[-*]\s*\[( |x|X)\]\s*", t)
    status = "open"
    if m:
        status = "done" if m.group(1).lower() == "x" else "open"
        t = t[m.end():]
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
    return Item(title=title, status=status, priority=priority,
                date=date, source=None, body=rest.strip(), area=area)


def slugify(title: str, max_words: int = 8) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)          # drop punctuation/emoji/·
    words = [w for w in re.split(r"[\s-]+", s) if w]
    return "-".join(words[:max_words])


def filename_for(item: Item, default_date: str = "2026-06-16") -> str:
    date = item.date or default_date
    return f"{date}-{slugify(item.title)}.md"


def _yq(s: str) -> str:
    """Double-quote a YAML scalar so colons/brackets/quotes are safe."""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def render_item(item: Item) -> str:
    fm = ["---", f"title: {_yq(item.title)}", f"status: {item.status}",
          f"priority: {item.priority}"]
    if item.date:
        fm.append(f"date: {item.date}")
    if item.source:
        fm.append(f"source: {_yq(item.source)}")
    if item.area:
        fm.append(f"area: {_yq(item.area)}")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {item.title}\n\n{item.body}\n"


def split_bullets(text: str) -> list[str]:
    """Each top-level `* `/`- ` bullet as a block (indented/blank continuation
    lines fold in). Headings and non-bullet prose are skipped."""
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


def migrate_wishlist_file(src: Path, out_dir: Path, in_done_file: bool,
                          default_date: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for bullet in split_bullets(src.read_text()):
        item = parse_wishlist_item(bullet, in_done_file=in_done_file)
        if not item.title:
            continue
        (out_dir / filename_for(item, default_date)).write_text(render_item(item))
        count += 1
    return count


def split_research_items(text: str):
    """Yield (line, area) for each `- [ ]`/`- [x]` line under `## Queue` and its
    `###` subsections. area = slugified nearest `###` heading."""
    area = None
    in_queue = False
    for line in text.splitlines():
        h2 = re.match(r"^##\s+(.+)$", line)
        h3 = re.match(r"^###\s+(.+)$", line)
        if h2:
            in_queue = h2.group(1).strip().lower().startswith("queue")
            area = None
            continue
        if h3:
            area = slugify(h3.group(1))
            continue
        if in_queue and re.match(r"^[-*]\s*\[( |x|X)\]", line):
            yield line, area


def migrate_research_file(src: Path, out_dir: Path, default_date: str) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for line, area in split_research_items(src.read_text()):
        item = parse_research_item(line, area=area)
        if not item.title:
            continue
        (out_dir / filename_for(item, default_date)).write_text(render_item(item))
        count += 1
    return count


def migrate(vault: Path, default_date: str) -> dict:
    counts = {"wishlist": 0}
    wl = vault / "docs" / "wishlist"
    for name, done in [("Wishlist.md", False), ("Wishlist-in-progress.md", False),
                       ("Wishlist-done.md", True)]:
        src = vault / "docs" / name
        if src.exists():
            counts["wishlist"] += migrate_wishlist_file(src, wl, done, default_date)
    rq_src = vault / "knowledge-base" / "research-queue.md"
    rq_dir = vault / "knowledge-base" / "research-queue"
    counts["research"] = migrate_research_file(rq_src, rq_dir, default_date) if rq_src.exists() else 0
    return counts


if __name__ == "__main__":
    import sys
    vault = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "Scout")
    default_date = sys.argv[2] if len(sys.argv) > 2 else "2026-06-16"
    print(migrate(vault, default_date))
