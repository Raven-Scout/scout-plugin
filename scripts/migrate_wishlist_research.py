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


def _strip_markers(text: str):
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
    title = re.sub(r"^START IMMEDIATELY\s*(—|-|–)\s*", "", lead.strip()).strip()
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


def render_item(item: Item) -> str:
    fm = ["---", f"title: {item.title}", f"status: {item.status}",
          f"priority: {item.priority}"]
    if item.date:
        fm.append(f"date: {item.date}")
    if item.source:
        fm.append(f"source: {item.source}")
    if item.area:
        fm.append(f"area: {item.area}")
    fm.append("---")
    return "\n".join(fm) + f"\n\n# {item.title}\n\n{item.body}\n"
