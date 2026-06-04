"""Parse Scout action items markdown files into structured data.

Handles the actual action items format:
- Section headers (## Completed Today, ## In Progress, ## To Do, ## Watching)
- Checkbox items: [x] done, [ ] open
- Priority emojis: 🔴 urgent, 🟡 medium, 🟢 low
- Strikethrough ~~text~~ as done
- Sub-bullets with context links and details
- Section-prefixed items (### 🔴 URGENT: ..., ### ✅ ... — Done)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from scout.ids import short_prefix_pattern


@dataclass
class ActionItem:
    """A single action item parsed from the markdown file."""

    priority: str  # "🔴", "🟡", "🟢", or ""
    title: str
    status: str  # "open", "done", "in_progress", "watching"
    section: str  # which markdown section this came from
    context_links: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    raw_line: str = ""
    line_number: int = 0
    short_prefix: str | None = None


PRIORITY_MAP = {
    "🔴": "urgent",
    "🟡": "medium",
    "🟢": "low",
}

# Patterns
CHECKBOX_DONE = re.compile(r"^\s*-\s*\[x\]\s*", re.IGNORECASE)
CHECKBOX_OPEN = re.compile(r"^\s*-\s*\[\s*\]\s*")
PRIORITY_EMOJI = re.compile(r"(🔴|🟡|🟢)")
URL_PATTERN = re.compile(r"https?://[^\s\)>]+")
WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")
STRIKETHROUGH = re.compile(r"~~(.+?)~~")
NOTE_PATTERN = re.compile(r"^\s*-\s*\*\*\[TUI note")
SECTION_H2 = re.compile(r"^##\s+(.+)")
SECTION_H3 = re.compile(r"^###\s+(.+)")
STATUS_EMOJI = re.compile(r"^(✅|🔄|❓|⬜)")
BULLET_LINE = re.compile(r"^(\s*)-\s+(.+)")

# Section name → default status mapping
SECTION_STATUS_MAP = {
    "completed today": "done",
    "completed": "done",
    "done": "done",
    "in progress": "in_progress",
    "to do": "open",
    "todo": "open",
    "watching": "watching",
    "upcoming": "open",
}


def parse_file(path: Path) -> list[ActionItem]:
    """Parse `path` into a list of ActionItem records.

    Entry point for parsing action items markdown files.
    """
    return parse_action_items(path)


def parse_action_items(filepath: Path) -> list[ActionItem]:
    """Parse an action items markdown file into a list of ActionItem objects.

    Returns items sorted by: in_progress first, then open, then watching, then done.
    Within each status group, sorted by priority (🔴 > 🟡 > 🟢 > none).
    """
    if not filepath.exists():
        return []

    items: list[ActionItem] = []
    lines = filepath.read_text(encoding="utf-8").splitlines()

    current_section = ""
    current_subsection = ""
    current_item: ActionItem | None = None
    in_fence = False

    for i, line in enumerate(lines, start=1):
        # Fenced code blocks are not content: a ``` / ~~~ line toggles the
        # fence and everything inside is skipped. Without this, example task
        # lines in docs (README, format guides) get parsed as real items —
        # backfill then inserts `[#XXXX]` into the code block and mark-done/
        # snooze operate on documentation examples (#40). Checked first so a
        # `## Heading` or `- [ ] ...` *inside* a fence isn't misread.
        fence = line.lstrip()
        if fence.startswith("```") or fence.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        # Track section headers
        h2_match = SECTION_H2.match(line)
        if h2_match:
            current_section = h2_match.group(1).strip()
            current_subsection = ""
            current_item = None
            continue

        h3_match = SECTION_H3.match(line)
        if h3_match:
            current_subsection = h3_match.group(1).strip()
            # H3 sections like "### ✅ Security Plugin — Done" are status markers
            if "✅" in current_subsection or "done" in current_subsection.lower():
                # Items under this are done
                pass
            current_item = None
            continue

        # Skip non-content lines
        stripped = line.strip()
        if not stripped or stripped.startswith("|") or stripped.startswith(">"):
            continue

        # Check for TUI note (belongs to current item)
        if current_item and NOTE_PATTERN.match(line):
            current_item.notes.append(stripped)
            continue

        # Parse bullet items
        bullet_match = BULLET_LINE.match(line)
        if not bullet_match:
            continue

        indent = len(bullet_match.group(1))
        content = bullet_match.group(2)

        # Sub-bullets (indented) belong to current item
        if indent >= 2 and current_item:
            # Extract links from sub-bullets
            urls = URL_PATTERN.findall(content)
            current_item.context_links.extend(urls)
            wikilinks = WIKILINK_PATTERN.findall(content)
            current_item.context_links.extend([f"kb://{wl}" for wl in wikilinks])
            current_item.details.append(stripped)
            continue

        # Top-level bullet — this is an action item
        item = _parse_item_line(line, i, current_section, current_subsection)
        items.append(item)
        current_item = item

    return _sort_items(items)


def _parse_item_line(
    line: str,
    line_number: int,
    section: str,
    subsection: str,
) -> ActionItem:
    """Parse a single action item line."""
    stripped = line.strip()

    # Determine status
    status = _infer_status(stripped, section, subsection)

    # Extract priority emoji
    priority_match = PRIORITY_EMOJI.search(stripped)
    priority = priority_match.group(1) if priority_match else ""

    # Infer priority from subsection or section header if not in the line itself
    if not priority and subsection:
        sub_priority = PRIORITY_EMOJI.search(subsection)
        if sub_priority:
            priority = sub_priority.group(1)
    if not priority and section:
        sec_priority = PRIORITY_EMOJI.search(section)
        if sec_priority:
            priority = sec_priority.group(1)

    # Clean up title
    title = stripped
    # Remove checkbox markers (must come before lstrip to match regex properly)
    title = CHECKBOX_DONE.sub("", title)
    title = CHECKBOX_OPEN.sub("", title)
    # Remove leading dash
    title = title.lstrip("- ")
    # Remove status emojis at start
    title = STATUS_EMOJI.sub("", title).strip()
    # Extract `[#XXXX]` short prefix (if present) and strip from title.
    # raw_line is preserved untouched so substring matching still works.
    _short_prefix: str | None = None
    _prefix_match = short_prefix_pattern().search(title)
    if _prefix_match is not None:
        _short_prefix = _prefix_match.group(1)
        # Remove the bracketed prefix from the title.
        title = title[: _prefix_match.start()] + title[_prefix_match.end() :]
        # Collapse any double-space left behind, then strip outer whitespace.
        title = title.replace("  ", " ").strip()
    # Remove strikethrough markers but keep text for context
    title = STRIKETHROUGH.sub(r"\1", title)
    # Remove bold markers
    title = title.replace("**", "")
    # Remove priority emojis
    title = PRIORITY_EMOJI.sub("", title).strip()
    # Trim leading/trailing dashes and whitespace
    title = title.strip("- ").strip()

    # Extract URLs
    context_links = URL_PATTERN.findall(line)
    wikilinks = WIKILINK_PATTERN.findall(line)
    context_links.extend([f"kb://{wl}" for wl in wikilinks])

    section_label = subsection if subsection else section

    return ActionItem(
        priority=priority,
        title=title,
        status=status,
        section=section_label,
        context_links=context_links,
        raw_line=line,
        line_number=line_number,
        short_prefix=_short_prefix,
    )


def _infer_status(line: str, section: str, subsection: str) -> str:
    """Infer item status from line content, section, and subsection."""
    # Explicit checkbox
    if CHECKBOX_DONE.match(line):
        return "done"
    if CHECKBOX_OPEN.match(line):
        return "open"

    # Status emoji prefix
    if line.lstrip("- ").startswith("✅"):
        return "done"
    if line.lstrip("- ").startswith("🔄"):
        return "in_progress"

    # Strikethrough = done
    if "~~" in line:
        return "done"

    # Check for "Done" or "Completed" in the line
    lower = line.lower()
    if "— done" in lower or "— completed" in lower or "✅ done" in lower:
        return "done"

    # Infer from subsection
    if subsection:
        sub_lower = subsection.lower()
        if "✅" in subsection or "done" in sub_lower:
            return "done"

    # Infer from parent section
    section_lower = section.lower()
    for key, default_status in SECTION_STATUS_MAP.items():
        if key in section_lower:
            return default_status

    return "open"


def _sort_items(items: list[ActionItem]) -> list[ActionItem]:
    """Sort items: in_progress > open > watching > done, then by priority."""
    status_order = {"in_progress": 0, "open": 1, "watching": 2, "done": 3}
    priority_order = {"🔴": 0, "🟡": 1, "🟢": 2, "": 3}

    return sorted(
        items,
        key=lambda item: (
            status_order.get(item.status, 9),
            priority_order.get(item.priority, 9),
        ),
    )


def filter_actionable(items: list[ActionItem]) -> list[ActionItem]:
    """Return only actionable items (not done, not calendar entries)."""
    return [item for item in items if item.status != "done" and "calendar" not in item.section.lower()]


def items_by_priority(
    items: list[ActionItem],
) -> dict[str, list[ActionItem]]:
    """Group items by priority level."""
    groups: dict[str, list[ActionItem]] = {
        "🔴": [],
        "🟡": [],
        "🟢": [],
        "": [],
    }
    for item in items:
        groups.setdefault(item.priority, []).append(item)
    return groups
