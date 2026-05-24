"""Render a Scout action-items markdown file into the HTML dashboard.

Port of ~/Scout/action-items/render.py into the scout-plugin engine.
Pure stdlib — no heavy imports (no rich, jinja2, or watchdog).

Public API
----------
render(path: Path) -> str
    Parse *path* and return a complete HTML string.  Raises
    ``scout.errors.ActionItemError`` if the file is not found.

parse(md_path: Path) -> tuple[str, list[str], list[Section]]
    Lower-level entry point: returns (title, preamble paragraphs,
    sections) for callers that want the structured form (e.g., a TUI
    dashboard or the Plan 6 EngineClient). The dataclasses
    ``Comment``, ``Task``, ``BulletLine``, ``Table``, ``Section``
    describe the parsed shape.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from scout.errors import ActionItemError

if TYPE_CHECKING:
    from scout.action_items.diff import ChangeEvent

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


@dataclass
class Comment:
    """A conversation turn attached to a task.

    Serialized in the markdown as an indented quote line:
        - [ ] Task — body
          > scout (2026-04-18 10:20 AM ET): text here
          > scout (2026-04-18 11:00 AM ET): reply here

    The indentation under the task bullet is what binds the comment to that task.
    See ``action-items/README.md`` for the full schema.
    """

    author: str
    timestamp: str
    text: str


@dataclass
class Task:
    done: bool
    subject: str
    body: str
    raw: str
    comments: list[Comment] = field(default_factory=list)


@dataclass
class BulletLine:
    """A non-task bullet (used in Today's Focus, Scout Digest, etc.)."""

    text: str


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]


@dataclass
class Section:
    emoji: str
    title: str
    subtitle: str | None = None
    tasks: list[Task] = field(default_factory=list)
    bullets: list[BulletLine] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    subheads: list[str] = field(default_factory=list)  # ### subheads


SECTION_RE = re.compile(r"^## (?P<emoji>\S+?)\s+(?P<title>.+?)(?:\s*\(.*?\))?\s*$")
SIMPLE_SECTION_RE = re.compile(r"^## (?P<title>.+?)\s*$")
TASK_RE = re.compile(r"^\s*- \[(?P<mark>[ xX])\]\s+(?P<rest>.+?)\s*$")
BULLET_RE = re.compile(r"^\s*-\s+(?P<rest>.+?)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
# Indented quote-line comment bound to the preceding task:
#   "  > scout (2026-04-18 10:20 AM ET): text"
# Author/timestamp are captured; loose format tolerated (timestamp optional).
COMMENT_RE = re.compile(
    r"^(?P<indent>\s+)>\s+(?P<author>[A-Za-z][A-Za-z0-9._-]*)"
    r"(?:\s+\((?P<timestamp>[^)]+)\))?\s*:\s*(?P<text>.+?)\s*$"
)


def parse(md_path: Path) -> tuple[str, list[str], list[Section]]:
    """Return (title, preamble_paragraphs, sections)."""

    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    title = ""
    preamble: list[str] = []
    sections: list[Section] = []
    current: Section | None = None
    in_table = False
    table: Table | None = None

    # Find title
    for i, line in enumerate(lines):
        if line.startswith("# ") and not title:
            title = line[2:].strip()
            start = i + 1
            break
    else:
        start = 0

    i = start
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Horizontal rules are separators
        if stripped in ("---", "***"):
            in_table = False
            table = None
            i += 1
            continue

        # Section header. Try "## <emoji> <title>" first; fall back to plain "## <title>".
        if line.startswith("## "):
            m = SECTION_RE.match(line)
            if m:
                emoji = m.group("emoji")
                rest = m.group("title")
                if not re.match(r"^[\u2600-\u27BF\U0001F300-\U0001FAFF✅🔴🟡🟢💡📋📅]", emoji):
                    # First token wasn't an emoji — treat whole thing as plain title.
                    emoji = ""
                    rest = line[3:].strip()
            else:
                emoji = ""
                rest = line[3:].strip()
            current = Section(emoji=emoji, title=rest)
            sections.append(current)
            in_table = False
            i += 1
            continue

        # Sub-heading
        if line.startswith("### ") and current is not None:
            current.subheads.append(line[4:].strip())
            in_table = False
            i += 1
            continue

        # Preamble (before first section)
        if current is None:
            if stripped:
                preamble.append(stripped)
            i += 1
            continue

        # Table detection
        if TABLE_ROW_RE.match(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # Separator row: ignore
            if all(re.match(r"^:?-+:?$", c) for c in cells):
                i += 1
                continue
            if not in_table:
                in_table = True
                table = Table(headers=cells, rows=[])
                current.tables.append(table)
            else:
                assert table is not None
                table.rows.append(cells)
            i += 1
            continue
        else:
            in_table = False
            table = None

        # Task line
        t = TASK_RE.match(line)
        if t and current is not None:
            done = t.group("mark").lower() == "x"
            rest = t.group("rest")
            # Split subject/body: first " — " or " – " after optional bold/strike
            subject, body = _split_subject(rest)
            current.tasks.append(Task(done=done, subject=subject, body=body, raw=rest))
            i += 1
            continue

        # Comment line bound to the preceding task (indented quote).
        c = COMMENT_RE.match(line)
        if c and current is not None and current.tasks:
            current.tasks[-1].comments.append(
                Comment(
                    author=c.group("author"),
                    timestamp=(c.group("timestamp") or "").strip(),
                    text=c.group("text"),
                )
            )
            i += 1
            continue

        # Regular bullet line (section-level)
        b = BULLET_RE.match(line)
        if b and current is not None:
            current.bullets.append(BulletLine(text=b.group("rest")))
            i += 1
            continue

        # Paragraph line inside a section
        if stripped and current is not None:
            current.bullets.append(BulletLine(text=stripped))

        i += 1

    return title, preamble, sections


def _split_subject(rest: str) -> tuple[str, str]:
    """Split a task line into (subject, body-remainder).

    The dash separator must be *outside* of bold (`**...**`), strike (`~~...~~`),
    inline code (`` ` ``), wikilinks (`[[...]]`), and markdown links (`[...](...)`).
    Otherwise splits land in the middle of a markdown token and break rendering.
    """
    dashes = (" — ", " – ", " - ")

    def find_outside_tokens(text: str) -> int:
        in_bold = False
        in_strike = False
        in_code = False
        bracket_depth = 0  # inside [[ or [
        paren_depth = 0  # inside (...) of a link
        i = 0
        n = len(text)
        while i < n:
            two = text[i : i + 2]
            ch = text[i]
            if ch == "`" and not in_bold and not in_strike:
                in_code = not in_code
                i += 1
                continue
            if in_code:
                i += 1
                continue
            if two == "**":
                in_bold = not in_bold
                i += 2
                continue
            if two == "~~":
                in_strike = not in_strike
                i += 2
                continue
            if two == "[[":
                bracket_depth += 1
                i += 2
                continue
            if two == "]]" and bracket_depth > 0:
                bracket_depth -= 1
                i += 2
                continue
            if ch == "[" and bracket_depth == 0:
                bracket_depth = 1
                i += 1
                continue
            if ch == "]" and bracket_depth > 0 and text[i : i + 2] != "]]":
                bracket_depth = 0
                if i + 1 < n and text[i + 1] == "(":
                    paren_depth = 1
                    i += 2
                    continue
                i += 1
                continue
            if ch == ")" and paren_depth > 0:
                paren_depth -= 1
                i += 1
                continue
            # Clean spot — is this a separator?
            if not in_bold and not in_strike and bracket_depth == 0 and paren_depth == 0:
                for d in dashes:
                    if text[i : i + len(d)] == d:
                        return i
            i += 1
        return -1

    idx = find_outside_tokens(rest)
    if idx != -1:
        # Determine which dash matched
        for d in dashes:
            if rest[idx : idx + len(d)] == d:
                return rest[:idx].rstrip(), rest[idx + len(d) :].lstrip()

    # Fallback: a colon split outside tokens
    idx = -1
    in_bold = False
    in_strike = False
    in_code = False
    i = 0
    while i < len(rest):
        two = rest[i : i + 2]
        if rest[i] == "`" and not in_bold and not in_strike:
            in_code = not in_code
        elif not in_code and two == "**":
            in_bold = not in_bold
            i += 2
            continue
        elif not in_code and two == "~~":
            in_strike = not in_strike
            i += 2
            continue
        elif not in_code and not in_bold and not in_strike and rest[i : i + 2] == ": ":
            idx = i
            break
        i += 1
    if idx != -1:
        return rest[:idx].rstrip(), rest[idx + 2 :].lstrip()
    return rest, ""


# ---------------------------------------------------------------------------
# Inline markdown → HTML
# ---------------------------------------------------------------------------


WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|([^\]]+))?\]\]")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
STRIKE_RE = re.compile(r"~~(.+?)~~")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<![\*_])\*(?!\*)([^*]+?)\*(?!\*)")
CODE_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Render inline markdown to HTML."""
    # Escape first, then re-substitute for markdown tokens. Tokens are chosen
    # so we can safely find them after escaping.
    s = html.escape(text, quote=False)
    # Wikilinks become pills
    s = WIKILINK_RE.sub(lambda m: _wiki_pill(m.group(1), m.group(2)), s)
    # Regular links
    s = LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
        s,
    )
    s = CODE_RE.sub(r"<code>\1</code>", s)
    s = STRIKE_RE.sub(r"<s>\1</s>", s)
    s = BOLD_RE.sub(r"<strong>\1</strong>", s)
    s = ITALIC_RE.sub(r"<em>\1</em>", s)
    return s


def _wiki_pill(target: str, label: str | None) -> str:
    display = label or target.split("/")[-1]
    return f'<span class="wiki">{html.escape(display)}</span>'


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


SECTION_STYLES = {
    "🔴": ("urgent", "Urgent / Time-sensitive"),
    "🟡": ("todo", "To do"),
    "🟢": ("watching", "Watching"),
    "💡": ("focus", "Today's focus"),
    "📅": ("meetings", "Today's meetings"),
    "✅": ("done", "Recently completed"),
    "📋": ("digest", "Scout digest"),
}


def _section_kind(section: Section) -> str:
    if section.emoji in SECTION_STYLES:
        return SECTION_STYLES[section.emoji][0]
    t = section.title.lower()
    if "personal" in t:
        return "personal"
    return "neutral"


def _render_html(title: str, preamble: list[str], sections: list[Section], source_stem: str = "") -> str:
    # Compute summary stats from task counts
    def count(kind: str) -> int:
        return sum(sum(1 for t in s.tasks if not t.done) for s in sections if _section_kind(s) == kind)

    n_urgent = count("urgent")
    n_todo = count("todo")
    n_watching = count("watching")
    n_personal = count("personal")
    n_done = sum(sum(1 for t in s.tasks if t.done) for s in sections if _section_kind(s) == "done")

    stats = [
        ("urgent", n_urgent, "🔥 Urgent"),
        ("warn", n_todo, "📋 To do"),
        ("info", n_watching, "👀 Watching"),
        ("muted", n_personal, "🏡 Personal"),
        ("ok", n_done, "✅ Done"),
    ]

    body_sections: list[str] = []

    for s in sections:
        kind = _section_kind(s)
        if kind == "focus":
            body_sections.append(_render_focus(s))
        elif kind in ("urgent", "todo", "watching", "personal"):
            body_sections.append(_render_cards(s, kind, source_stem))
        elif kind == "meetings":
            body_sections.append(_render_meetings(s))
        elif kind == "done":
            body_sections.append(_render_completed(s))
        elif kind == "digest":
            body_sections.append(_render_digest(s))
        else:
            body_sections.append(_render_cards(s, "neutral", source_stem))

    preamble_html = "".join(f"<p>{_inline(p)}</p>" for p in preamble)

    stat_cards = "".join(
        f'<div class="stat {cls}"><div class="num">{n}</div><div class="label">{label}</div></div>'
        for cls, n, label in stats
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<header class="site-header">
  <div>
    <h1>{html.escape(title)}</h1>
    <div class="preamble">{preamble_html}</div>
  </div>
</header>

<section class="summary">{stat_cards}</section>

{"".join(body_sections)}

<footer>
  <div>Rendered from <code>{html.escape(source_stem + ".md") if source_stem else "action-items.md"}</code>. Markdown is the source of truth — edit it and re-run <code>render.py</code>.</div>
</footer>
<script>{CLIPBOARD_JS}</script>
</body>
</html>"""


def render(path: Path) -> str:
    """Parse *path* and return a complete HTML string.

    This is the primary public entry point for the renderer.

    Raises
    ------
    ActionItemError
        If *path* does not exist.
    """
    if not path.exists():
        raise ActionItemError(f"render: file not found: {path}")
    title, preamble, sections = parse(path)
    return _render_html(title, preamble, sections, source_stem=path.stem)


def _render_focus(s: Section) -> str:
    items = "".join(f"<li>{_inline(b.text)}</li>" for b in s.bullets)
    return f"""
<section class="focus-box">
  <h2>{html.escape(s.emoji)} {html.escape(s.title)}</h2>
  <ul>{items}</ul>
</section>
"""


def _render_cards(s: Section, kind: str, source_stem: str = "") -> str:
    date_slug = source_stem.replace("action-items-", "") if source_stem else ""
    cards: list[str] = []
    for t in s.tasks:
        subj = _inline(t.subject)
        body = _inline(t.body) if t.body else ""
        done_cls = " done" if t.done else ""
        stamp = '<span class="stamp">✅ Done</span>' if t.done else ""
        comments_html = _render_comments(t)
        # Subject slug doubles as the identifier for write-back: add_comment.py
        # locates the task by matching --subject against this same text.
        slug = html.escape(t.subject, quote=True)
        textarea_html = (
            f'<div class="add-comment">'
            f'<textarea data-subject="{slug}" rows="2" '
            f'placeholder="Add comment — run: ./action-items/add_comment.py '
            f'&lt;date&gt; --subject &quot;…&quot; --text &quot;…&quot;"></textarea>'
            f"</div>"
        )
        actions_html = _render_task_actions(t, date_slug)
        links_html = _render_task_links(t)
        cards.append(
            f"""
<article class="card kind-{kind}{done_cls}">
  {stamp}
  <h3>{subj}</h3>
  {f'<div class="body">{body}</div>' if body else ""}
  {comments_html}
  {links_html}
  {actions_html}
  {textarea_html}
</article>
"""
        )
    # Also render non-task bullets if any
    if s.bullets:
        bullets_html = "".join(f"<li>{_inline(b.text)}</li>" for b in s.bullets)
        cards.append(f'<div class="extra-notes"><ul>{bullets_html}</ul></div>')
    grid = "".join(cards) if cards else '<p class="muted">Nothing here.</p>'
    return f"""
<section class="section section-{kind}">
  <h2>{html.escape(s.emoji)} {html.escape(s.title)}</h2>
  <div class="grid">{grid}</div>
</section>
"""


def _render_comments(t: Task) -> str:
    if not t.comments:
        return ""
    items: list[str] = []
    for c in t.comments:
        ts = f" <time>{html.escape(c.timestamp)}</time>" if c.timestamp else ""
        author_cls = "scout" if c.author.lower() == "scout" else "human"
        items.append(
            f'<li class="comment comment-{author_cls}">'
            f'<span class="author">{html.escape(c.author)}</span>{ts}'
            f'<div class="text">{_inline(c.text)}</div>'
            f"</li>"
        )
    return f'<ul class="comments">{"".join(items)}</ul>'


_MD_TOKEN_STRIPPER = [
    (re.compile(r"~~(.+?)~~"), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+)?\]\]"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
]


def _plain_subject(subject: str) -> str:
    """Mirror mark_done.py / snooze.py _strip_markdown_tokens so the --subject
    substring we render actually matches the stripped comparison those CLIs do.
    """
    s = subject
    for pat, repl in _MD_TOKEN_STRIPPER:
        s = pat.sub(repl, s)
    return s


# Deep-link detection ------------------------------------------------------
# Linear: `AI-2619`, `ST-3853`, `SUPPORT-15915`, `LDRS-321`, `KAI-12`. The
# allowed prefixes below are the ones seen in real action items / KB; adding
# more is cheap. Note that a Linear ID also appears inside `[[AI-XXXX]]`
# wikilinks — those render as plain text, so the same regex finds both.
_LINEAR_ID_RE = re.compile(r"\b(AI|ST|SUPPORT|LDRS|KAI|DATA)-\d+\b")
_GITHUB_PR_RE = re.compile(r"https://github\.com/([\w.\-]+)/([\w.\-]+)/pull/(\d+)")
_SLACK_LINK_RE = re.compile(r"https://[\w.\-]+\.slack\.com/archives/[A-Z0-9]+/p\d+(?:\?[^\s)\"']+)?")


def _render_task_links(t: Task) -> str:
    """Emit deep-link buttons (Linear / GitHub PR / Slack) for the task.

    Links come from scanning the subject + body for well-known URL shapes and
    Linear issue IDs. De-duplicated per-card so `[[AI-2619]]` mentioned three
    times still yields one button.
    """
    text = f"{t.subject} {t.body}"
    links: list[tuple[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for m in _LINEAR_ID_RE.finditer(text):
        iid = m.group(0)
        key: tuple[str, ...] = ("linear", iid)
        if key in seen:
            continue
        seen.add(key)
        links.append((f"Linear {iid}", f"https://linear.app/keboola/issue/{iid}"))
    for m in _GITHUB_PR_RE.finditer(text):
        repo = f"{m.group(1)}/{m.group(2)}"
        pr = m.group(3)
        key = ("gh", repo, pr)
        if key in seen:
            continue
        seen.add(key)
        links.append((f"PR {repo}#{pr}", m.group(0)))
    for m in _SLACK_LINK_RE.finditer(text):
        url = m.group(0)
        key = ("slack", url)
        if key in seen:
            continue
        seen.add(key)
        links.append(("Slack thread", url))
    if not links:
        return ""
    anchors = "".join(
        f'<a class="task-link" href="{html.escape(url, quote=True)}" '
        f'target="_blank" rel="noopener noreferrer" '
        f'title="{html.escape(url, quote=True)}">{html.escape(label)}</a>'
        for label, url in links
    )
    return f'<div class="task-links">{anchors}</div>'


def _render_task_actions(t: Task, date_slug: str) -> str:
    """Emit click-to-copy CLI commands for mark-done/snooze on each task.

    The commands mirror the subject-substring contract in ``mark_done.py`` and
    ``snooze.py`` — use a distinctive slice of the subject so the match is
    unambiguous without the user re-reading the file.
    """
    # Quote-safe substring: first ~40 chars of the plain-text subject.
    needle = _plain_subject(t.subject).strip().replace('"', r"\"")
    if len(needle) > 40:
        needle = needle[:40].rstrip()
    if t.done:
        # No actionable affordances for completed tasks; reopen flow is not
        # part of the v0.4 CLI surface. Editing the source markdown remains
        # the canonical way to reopen a task.
        return ""
    date_arg = f"{date_slug} " if date_slug else ""
    mark_cmd = f'./action-items/mark_done.py {date_arg}--subject "{needle}"'
    snooze_tmpl = f'./action-items/snooze.py {date_arg}--subject "{needle}" --until +1d'
    launch_cmd = f'cd ~/Scout && claude "Help me make progress on this action item: {needle}"'
    chips = [("Mark done", mark_cmd), ("Snooze", snooze_tmpl), ("Launch Claude", launch_cmd)]
    buttons = "".join(
        f'<button type="button" class="task-action" data-cmd="{html.escape(cmd, quote=True)}" '
        f'title="Click to copy: {html.escape(cmd, quote=True)}">{html.escape(label)}</button>'
        for label, cmd in chips
    )
    return f'<div class="task-actions">{buttons}</div>'


def _render_meetings(s: Section) -> str:
    parts: list[str] = [f"<h2>{html.escape(s.emoji)} {html.escape(s.title)}</h2>"]
    for idx, table in enumerate(s.tables):
        subhead = s.subheads[idx - 1] if idx > 0 and idx - 1 < len(s.subheads) else ""
        if subhead:
            parts.append(f"<h3>{html.escape(subhead)}</h3>")
        head = "".join(f"<th>{_inline(h)}</th>" for h in table.headers)
        rows_html = []
        for row in table.rows:
            cells = "".join(f"<td>{_inline(c)}</td>" for c in row)
            rows_html.append(f"<tr>{cells}</tr>")
        parts.append(f'<table class="mtg"><thead><tr>{head}</tr></thead><tbody>{"".join(rows_html)}</tbody></table>')
    return f'<section class="section section-meetings">{"".join(parts)}</section>'


def _render_completed(s: Section) -> str:
    items = []
    for t in s.tasks:
        if not t.done:
            continue
        items.append(f"<li><strong>{_inline(t.subject)}</strong>{' — ' + _inline(t.body) if t.body else ''}</li>")
    return f"""
<details class="completed" open>
  <summary><h2>{html.escape(s.emoji)} {html.escape(s.title)} <span class="count">({len(items)})</span></h2></summary>
  <ul>{"".join(items)}</ul>
</details>
"""


def _render_digest(s: Section) -> str:
    blocks: list[str] = []
    buffer: list[str] = []

    def flush():
        if buffer:
            blocks.append('<div class="digest-block">' + "".join(buffer) + "</div>")
            buffer.clear()

    for b in s.bullets:
        t = b.text
        # Bold-only lines act as subheads within the digest
        if re.match(r"^\*\*[^*]+\*\*:?\s*$", t):
            flush()
            blocks.append(f"<h3>{_inline(t)}</h3>")
        else:
            buffer.append(f"<p>{_inline(t)}</p>")
    flush()
    return f"""
<details class="digest">
  <summary><h2>{html.escape(s.emoji)} {html.escape(s.title)}</h2></summary>
  {"".join(blocks)}
</details>
"""


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


CSS = """
:root {
  --bg: #0f1115;
  --panel: #171a21;
  --panel-2: #1f2430;
  --border: #2a2f3c;
  --text: #e7ecf3;
  --muted: #9aa3b2;
  --accent: #6ea8ff;
  --urgent: #ff5370;
  --warn: #f7b955;
  --ok: #50c878;
  --info: #59c7ff;
  --purple: #b085f5;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background:
    radial-gradient(1200px 600px at 10% -10%, #1a2033 0%, transparent 60%),
    radial-gradient(900px 500px at 110% 0%, #24183a 0%, transparent 55%),
    var(--bg);
  color: var(--text);
  padding: 32px;
  line-height: 1.5;
  max-width: 1200px;
  margin: 0 auto;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code {
  font-family: ui-monospace, Menlo, monospace;
  background: var(--panel-2);
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 0.9em;
  color: #c7cedb;
}
s { color: var(--muted); }
.site-header {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
}
h1 { font-size: 28px; margin: 0 0 6px 0; letter-spacing: -0.4px; }
.preamble { color: var(--muted); font-size: 13px; }
.preamble p { margin: 4px 0; }

.summary {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
  margin-bottom: 28px;
}
.stat {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
  text-align: center;
}
.stat .num { font-size: 28px; font-weight: 700; line-height: 1; }
.stat .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 6px; }
.stat.urgent .num { color: var(--urgent); }
.stat.warn   .num { color: var(--warn); }
.stat.info   .num { color: var(--info); }
.stat.ok     .num { color: var(--ok); }
.stat.muted  .num { color: var(--muted); }

.focus-box {
  background: var(--panel);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  padding: 16px 20px;
  margin-bottom: 28px;
}
.focus-box h2 { margin: 0 0 10px 0; font-size: 16px; }
.focus-box ul { margin: 0; padding-left: 18px; }
.focus-box li { margin: 6px 0; font-size: 13px; color: #c7cedb; }

.section { margin-bottom: 28px; }
.section > h2 { font-size: 18px; margin: 0 0 14px 0; }
.section-urgent   > h2 { color: var(--urgent); }
.section-todo     > h2 { color: var(--warn); }
.section-watching > h2 { color: var(--info); }
.section-personal > h2 { color: var(--muted); }

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 12px;
}
.card {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
  position: relative;
  transition: border-color 0.15s, transform 0.15s;
}
.card:hover { border-color: var(--accent); transform: translateY(-1px); }
.card.kind-urgent   { border-left: 3px solid var(--urgent); }
.card.kind-todo     { border-left: 3px solid var(--warn); }
.card.kind-watching { border-left: 3px solid var(--info); }
.card.kind-personal { border-left: 3px solid var(--muted); }
.card.kind-neutral  { border-left: 3px solid var(--border); }
.card h3 { margin: 0 0 6px 0; font-size: 14px; font-weight: 600; }
.card .body { font-size: 13px; color: #c7cedb; }
.card .body p { margin: 4px 0; }

.card.done {
  background: linear-gradient(90deg, rgba(80,200,120,0.08), var(--panel) 60%);
  opacity: 0.85;
}
.card.done h3 {
  text-decoration: line-through;
  text-decoration-color: rgba(80,200,120,0.5);
  color: var(--muted);
}
.card .stamp {
  position: absolute;
  top: 10px;
  right: 12px;
  background: rgba(80,200,120,0.12);
  color: var(--ok);
  border: 1px solid rgba(80,200,120,0.3);
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}

.wiki {
  display: inline-block;
  background: var(--panel-2);
  color: var(--muted);
  border: 1px solid var(--border);
  padding: 0 6px;
  margin: 0 2px;
  border-radius: 4px;
  font-size: 0.82em;
  font-family: ui-monospace, Menlo, monospace;
}

.mtg {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 12px;
}
.mtg th, .mtg td {
  text-align: left;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
}
.mtg th { background: var(--panel-2); color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
.mtg tr:last-child td { border-bottom: none; }

details.completed, details.digest {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0 18px;
  margin-bottom: 20px;
}
details.completed summary, details.digest summary {
  list-style: none;
  cursor: pointer;
  padding: 14px 0;
  display: flex;
  align-items: center;
}
details.completed summary::-webkit-details-marker,
details.digest summary::-webkit-details-marker { display: none; }
details.completed summary h2, details.digest summary h2 {
  display: inline;
  margin: 0;
  font-size: 16px;
}
details.completed .count { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 6px; }
details.completed ul { margin: 0 0 16px 0; padding-left: 20px; color: var(--muted); font-size: 13px; }
details.completed ul li { margin: 4px 0; }
details.digest h3 { color: var(--muted); font-size: 13px; margin: 14px 0 6px 0; }
details.digest p { font-size: 12px; color: var(--muted); margin: 4px 0; }
details.digest .digest-block { margin-bottom: 10px; }

footer {
  margin-top: 40px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}
.extra-notes {
  grid-column: 1 / -1;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 16px;
}
.extra-notes ul { margin: 4px 0; padding-left: 20px; }
.extra-notes li { font-size: 12px; color: var(--muted); margin: 3px 0; }

.comments {
  list-style: none;
  margin: 10px 0 0 0;
  padding: 0;
  border-top: 1px dashed var(--border);
  padding-top: 8px;
}
.comments .comment {
  font-size: 12px;
  margin: 6px 0;
  padding: 6px 10px;
  border-left: 2px solid var(--border);
  background: var(--panel-2);
  border-radius: 0 6px 6px 0;
}
.comments .comment .author {
  color: var(--accent);
  font-weight: 600;
  font-size: 11px;
  text-transform: lowercase;
}
.comments .comment time {
  color: var(--muted);
  font-size: 10px;
  margin-left: 6px;
}
.comments .comment-scout .author { color: var(--purple); }
.comments .comment .text { margin-top: 2px; color: #c7cedb; }
.add-comment { margin-top: 8px; }
.add-comment textarea {
  width: 100%;
  background: var(--panel-2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
  font-family: inherit;
  font-size: 12px;
  resize: vertical;
  min-height: 28px;
}
.add-comment textarea:focus {
  outline: none;
  border-color: var(--accent);
}
.add-comment textarea::placeholder { color: var(--muted); font-size: 11px; }

.task-actions {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.task-action {
  background: var(--panel-2);
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 3px 9px;
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.task-action:hover {
  background: var(--panel);
  color: var(--text);
  border-color: var(--accent);
}
.task-action.copied {
  background: rgba(80,200,120,0.15);
  color: var(--ok);
  border-color: rgba(80,200,120,0.4);
}
.card.done .task-action {
  opacity: 0.7;
}

.task-links {
  display: flex;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.task-link {
  background: var(--panel-2);
  color: var(--accent);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 3px 9px;
  font-size: 11px;
  font-family: inherit;
  text-decoration: none;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.task-link::after {
  content: " ↗";
  opacity: 0.6;
  font-size: 10px;
}
.task-link:hover {
  background: var(--panel);
  border-color: var(--accent);
  text-decoration: none;
}
.card.done .task-link {
  opacity: 0.7;
}

@media (max-width: 760px) {
  .summary { grid-template-columns: repeat(2, 1fr); }
}
"""


# ---------------------------------------------------------------------------
# Clipboard JS — click a .task-action button to copy its data-cmd to clipboard
# ---------------------------------------------------------------------------


CLIPBOARD_JS = """
document.addEventListener('click', function(e) {
  const btn = e.target.closest('.task-action');
  if (!btn) return;
  const cmd = btn.getAttribute('data-cmd');
  if (!cmd) return;
  const done = () => {
    const prev = btn.textContent;
    btn.classList.add('copied');
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.classList.remove('copied'); btn.textContent = prev; }, 1200);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(cmd).then(done, () => {});
  } else {
    const ta = document.createElement('textarea');
    ta.value = cmd; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); done(); } catch(_) {}
    document.body.removeChild(ta);
  }
});
"""


# ---------------------------------------------------------------------------
# Change-event line formatter (Plan 3, Task 2)
# ---------------------------------------------------------------------------


# Symbol + label + Rich style per kind.
# Defined here (module level) so render_changes doesn't rebuild it per call.
_SYMBOL_STYLE: dict[str, tuple[str, str, str]] = {
    "added": ("+", "added", "green"),
    "removed": ("-", "removed", "red"),
    "completed": ("✓", "completed", "green"),
    "reopened": ("↻", "reopened", "yellow"),
    "title_changed": ("✎", "renamed", "yellow"),
}


def render_changes(
    events: list[ChangeEvent],
    *,
    now: dt.datetime,
    color: bool,
) -> list[str]:
    """Format ChangeEvent records as one line each.

    Returns a list of strings; caller is responsible for emitting them
    (typically `for line in lines: print(line)`).

    `color=True` injects ANSI escapes via Rich. `color=False` is plain
    text — use this when stdout is not a TTY or the user passed `--no-color`.
    """
    # Local imports keep render_html's stdlib-only top-level imports intact;
    # rich is in the [full] optional-dependencies, not core.
    from io import StringIO

    from rich.console import Console
    from rich.text import Text

    out: list[str] = []
    ts = now.strftime("[%H:%M:%S]")
    # 9-char-wide kind column so all rows align.
    KIND_WIDTH = 9
    for ev in events:
        symbol, kind_label, style = _SYMBOL_STYLE.get(ev.kind, ("?", ev.kind, "white"))
        prefix_part = f"[#{ev.item_id}] " if ev.item_id else ""
        if ev.kind == "title_changed":
            body = f'{prefix_part}"{ev.extras["old_title"]}" → "{ev.extras["new_title"]}"'
        else:
            body = f"{prefix_part}{ev.title} ({ev.section})"
        plain = f"{ts} {symbol} {kind_label.ljust(KIND_WIDTH)} {body}"
        if not color:
            out.append(plain)
            continue
        # Use a temporary Console capturing into a StringIO so we get
        # ANSI escapes regardless of the surrounding stdout's TTY status.
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, color_system="truecolor", legacy_windows=False)
        console.print(
            Text(f"{ts} ", style="dim")
            + Text(symbol + " ", style=style)
            + Text(kind_label.ljust(KIND_WIDTH), style=style)
            + Text(" " + body),
            end="",
        )
        out.append(buf.getvalue())
    return out
