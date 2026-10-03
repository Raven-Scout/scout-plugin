#!/usr/bin/env python3
"""generate-enrichment-questions.py — proactive KB-enrichment question generator.

The *pull*-capture half of the capture-surface family (the inverse of a *push*-capture
notepad): instead of waiting for the user to dump notes, the system proactively asks a
small, ranked set of questions about gaps that NO connector can fill — facts that live
only in the user's head or in in-person / verbal events the system never sees.

Sources scanned, in priority order (all things only the user can resolve):
  0. Explicit `[needs: …]` inline gap-flags                          — rank 0. A deliberately
     placed connector-blind marker ("ask me this"), so it outranks every heuristic source.
  1. review-queue.md "Question for the user:" / "Ask the user:" lines — rank 1. Explicitly
     user-directed by a prior run.
  2. Entity / KB "Open Question(s)" sections                         — rank 2. An open thread
     that names a head-fact (personal-scoped only; see ENRICHMENT_EXCLUDED_DIRS).
  3. [single-source] / [unverified] tagged claims                   — rank 3. The second
     source is often the user.
  4. Thin / stub entity files (frontmatter present, body near-empty) — rank 4. A connector can
     create the node, but only the user knows the substance.

Output: a short ranked list (default 5) that a dreaming/briefing run can paste into a
DM, or `--json` for tooling. This is a read-only scanner — it never writes to the KB.

Usage:
    python3 scripts/generate-enrichment-questions.py [--limit N] [--json] [--kb DIR]
                                                     [--thin-threshold CHARS]
                                                     [--exclude SUBSTR ...]
                                                     [--reject-file PATH | --no-reject-file]

`--exclude` (repeatable) suppresses any question whose topic/question/source contains the
substring (case-insensitive). The dreaming run passes the prior run's surfaced fingerprints
so the "🧠 Help me remember" block never repeats a question two runs in a row — making the
recall step's rotation rule mechanical rather than compose-time discipline.

`--reject-file` is a *persistent* stoplist (default: scripts/enrichment-stoplist.txt) loaded
on every run. Unlike --exclude (a one-run rotation), its substrings suppress questions forever.
It carries two classes of never-ask topics:
  (a) capabilities the system already instruments — asking reads as "the assistant doesn't
      know its own system", and
  (b) questions the user has explicitly dismissed as irrelevant — an in-thread "I don't care"
      / "you should already know this" is a permanent suppression.
When the user rejects a question in-thread, the dreaming run appends its keyword to that file so
the same dead question can never resurface. Pass --no-reject-file to disable for debugging.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_KB = Path(__file__).resolve().parent.parent / "knowledge-base"
DEFAULT_REJECT_FILE = Path(__file__).resolve().parent / "enrichment-stoplist.txt"

# Files that only *discuss* markers in prose (narrative), never carry live ones to ask about.
NARRATIVE_SKIP = {"scout-mistake-audit.md", "review-queue.md"}  # review-queue handled separately

# Open-questions phrased as *research/connector* actions are answerable WITHOUT the user — they
# belong to the research session, not the "🧠 Help me remember" enrichment block, which exists
# only for facts no connector can see. Conservative phrasing match — only drops questions that
# explicitly point at a non-user source. The classes below are the generic, tenant-agnostic
# kernel of "a connector/research pass can resolve this":
#   · research/investigation actions (web search, contact form, case study, "should confirm")
#   · attribution ("who introduced / routed / brought in X") — discoverable from connector
#     history (who created the issue / opened the thread), not a user-held fact
#   · customer / contract / account status — CRM- or project-lookup facts, not head-facts
#   · an answer attributed to a *named non-user person* ("X should know / may have / likely has")
#   · connector- or profile-answerable identity facts (title, handle, direct contact)
# NOTE (the subtle part — flag for review): this guard was generalized from an employer-specific
# alternation to employer-agnostic phrasing. It must keep the connector-answerable-vs-head-fact
# discrimination without hardcoding any org/product vocabulary.
RESEARCH_ANSWERABLE = re.compile(
    r"research pass|contact form|\bping\b|\bweb search\b|should confirm|records? should|"
    r"public confirmation|case study|audit first|next .{0,20}research|"
    # attribution — who did/connected/introduced something (connector-history-answerable)
    r"who routed|who introduced|who brought|who connected|who referred|how was .{0,30}introduced|"
    # customer / contract / account status — CRM- or project-lookup, not a head-fact
    r"current paying|paying .{0,15}customer|customer status|customer vs|customer or a|"
    r"stalled prospect|contract scope|\baccount id\b|crm record|deal stage|renewal date|"
    # an answer attributed to a named non-user person ("X should know / may have / likely has")
    r"should know|may have|likely has|"
    # connector- / profile-answerable identity facts
    r"exact title|title at|slack handle|direct contact",
    re.I,
)

# Entity files under ontology/entities/ are ORGANIZATION + TECHNOLOGY research artifacts, and
# project files are workstream-investigation logs. Their "Open Question" sections are
# overwhelmingly research/connector-answerable — confirm an external fact, a customer/product
# status, a product capability, or a design/decision backlog item — i.e. research-session work,
# not the connector-blind head-facts the "🧠 Help me remember" block exists for. So both dirs are
# scoped OUT of the enrichment surface here (they still drive the *research* session via the
# research queue, which this generator does not feed). people/ + personal/ open-questions stay:
# relationship and family facts skew genuinely user-only. Deliberate project decisions still reach
# enrichment via rank-0 [needs:] markers and rank-1 review-queue asks.
ENRICHMENT_EXCLUDED_DIRS = ("ontology/entities/", "projects/")


def _clean(text: str) -> str:
    """Strip markdown emphasis / wikilink brackets for a readable question."""
    text = re.sub(r"\*\*?|__?|`", "", text)
    text = re.sub(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", r"\1", text)
    return text.strip()


# Explicit inline gap-flag convention: `[needs: <what user-only fact is missing>]`.
# Deliberately placed (by the user or a run that identified a connector-blind gap) to mean
# "ask me this" — so it outranks every heuristic source. The body after the colon becomes the
# question verbatim. Lives anywhere in the KB; NARRATIVE_SKIP files are excluded (they discuss
# the convention in prose). A resolved flag is removed, not marked — its absence is the close.
NEEDS_MARKER = re.compile(r"\[needs:\s*(.+?)\]", re.I)


def scan_needs_markers(kb: Path) -> list[dict]:
    """Explicit `[needs: …]` inline flags — the highest-intent enrichment signal (rank 0)."""
    out = []
    for path in sorted(kb.rglob("*.md")):
        if path.name in NARRATIVE_SKIP:
            continue
        rel = path.relative_to(kb).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for m in NEEDS_MARKER.finditer(text):
            want = _clean(m.group(1)).rstrip(". ")
            if len(want) < 3:
                continue
            out.append({
                "rank": 0,
                "source": rel,
                "topic": rel.rsplit("/", 1)[-1].replace(".md", ""),
                "question": f"{want}?" if "?" not in want else want,
            })
    return out


def scan_review_queue(kb: Path) -> list[dict]:
    """Pending-review items with an explicit question for the user — highest heuristic priority."""
    out = []
    rq = kb / "review-queue.md"
    if not rq.exists():
        return out
    lines = rq.read_text(encoding="utf-8").splitlines()
    # Only items under "## Pending Review" (stop at "## Recently Processed" / "## Reviewed").
    in_pending = False
    current_title = None
    for line in lines:
        if line.startswith("## "):
            in_pending = line.strip().lower().startswith("## pending")
            continue
        if not in_pending:
            continue
        if line.startswith("### "):
            current_title = _clean(line[4:])
            continue
        m = re.match(r"\s*\*\*(?:Question for the user|Ask the user)[:：]\*\*\s*(.+)", line, re.I)
        if m and current_title:
            out.append({
                "rank": 1,
                "source": "review-queue.md",
                "topic": current_title,
                "question": _clean(m.group(1)),
            })
    return out


def scan_open_questions(kb: Path) -> list[dict]:
    """'Open Question' headers / lines across KB files (entity & project open threads)."""
    out = []
    for path in sorted(kb.rglob("*.md")):
        if path.name in NARRATIVE_SKIP:
            continue
        rel = path.relative_to(kb).as_posix()
        # org/tech entity + project open-questions are research-session work, not head-facts —
        # scoped out of the enrichment surface (see ENRICHMENT_EXCLUDED_DIRS).
        if any(rel.startswith(d) for d in ENRICHMENT_EXCLUDED_DIRS):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines):
            # Headed open-question lines: "Open Question #N — ..." or "### Open Questions"
            m = re.match(r"\s*#{2,5}\s*Open Question[s]?\s*(?:#\d+)?\s*[—\-:]?\s*(.*)", line, re.I)
            if not m:
                continue
            # Strip a trailing parenthetical scope-note ("(for the user to resolve …)") off the header.
            text = re.sub(r"^\(.*?\)\s*", "", m.group(1).strip()).strip("() ")
            # A real question contains "?"; otherwise the header is just a section label —
            # fall through to the first substantive bullet under it.
            if "?" not in text:
                for nxt in lines[i + 1:i + 8]:
                    if re.match(r"\s*#{1,5}\s", nxt):  # next header ⇒ stop
                        break
                    b = re.match(r"\s*[-*\d.]+\s+(.+)", nxt)
                    if b and (("?" in b.group(1)) or len(b.group(1).strip()) > 40):
                        text = b.group(1)
                        break
            # Skip questions already answered in-place: a resolution sentinel (✅ / RESOLVED /
            # MOOT / DONE) or a struck-through (~~…~~) line is a closed gap, not something to
            # re-ask the user.
            if re.search(r"✅|~~|\bRESOLVED\b|\bMOOT\b|\bDONE\b", text, re.I):
                continue
            # Research/connector-answerable → not a user-only enrichment gap.
            if RESEARCH_ANSWERABLE.search(text):
                continue
            # A line carrying an explicit [needs: …] flag is already surfaced at rank 0 by
            # scan_needs_markers — don't also emit the raw-marker text as a rank-2 question.
            if NEEDS_MARKER.search(text):
                continue
            if text and len(text.strip()) > 8:
                out.append({
                    "rank": 2,
                    "source": rel,
                    "topic": rel.rsplit("/", 1)[-1].replace(".md", ""),
                    "question": _clean(text)[:240],
                })
    return out


def scan_unverified(kb: Path) -> list[dict]:
    """[single-source] / [unverified] claims — a second source is often only the user."""
    out = []
    pat = re.compile(r"\[(single-source|unverified)\]", re.I)
    for path in sorted(kb.rglob("*.md")):
        if path.name in NARRATIVE_SKIP:
            continue
        rel = path.relative_to(kb).as_posix()
        # Same scoping as the open-question scan: projects/ + ontology/entities/ single-source
        # claims are research/connector-answerable (confirm against docs/code/connector history),
        # not connector-blind head-facts.
        if any(rel.startswith(d) for d in ENRICHMENT_EXCLUDED_DIRS):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for line in lines:
            stripped = line.strip()
            # Skip table rows: a [single-source]/[unverified] marker inside a `|`-delimited row
            # tags the row's data, and slicing the cell text yields an unreadable fragment
            # ("| Vendor — direct API (admin) | …") — not an askable claim.
            if stripped.startswith("|"):
                continue
            # Skip resolved-in-place claims + research/connector-answerable phrasing, mirroring
            # the rank-2 open-question guards so rank-3 holds the same user-only bar.
            if re.search(r"✅|~~|\bRESOLVED\b|\bMOOT\b|\bDONE\b", line, re.I):
                continue
            if RESEARCH_ANSWERABLE.search(line):
                continue
            if pat.search(line) and len(stripped) > 25:
                claim = _clean(pat.sub("", line))[:200]
                out.append({
                    "rank": 3,
                    "source": rel,
                    "topic": rel.rsplit("/", 1)[-1].replace(".md", ""),
                    "question": f"Can you confirm or correct this single-sourced claim? — {claim}",
                })
    return out


def scan_thin_entities(kb: Path, threshold: int = 200) -> list[dict]:
    """Entity files (YAML frontmatter with a `type:`) whose body is near-empty — stubs that
    exist as a graph node but were never enriched. The body-text floor (non-whitespace chars
    after the closing frontmatter fence, excluding the Relations block) is the 'never enriched'
    signal: a connector can create the node but only the user knows the substance.
    Scoped to the entity folders (ontology/entities, people, personal) so prose KB files and
    long-form project notes are never mistaken for stubs."""
    out = []
    entity_dirs = ("ontology/entities", "people", "personal")
    for path in sorted(kb.rglob("*.md")):
        rel = path.relative_to(kb).as_posix()
        if not any(rel.startswith(d + "/") for d in entity_dirs):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # Require YAML frontmatter delimited by leading '---'.
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        front, body = parts[1], parts[2]
        mtype = re.search(r"^\s*type:\s*(.+)$", front, re.M)
        name = re.search(r"^\s*name:\s*(.+)$", front, re.M)
        if not mtype:
            continue
        etype = mtype.group(1).strip().strip("\"'")
        ename = _clean(name.group(1)) if name else rel.rsplit("/", 1)[-1].replace(".md", "")
        # Body length excluding a Relations/relationships block and whitespace — that's
        # graph plumbing, not human-readable substance.
        body_wo_rels = re.split(r"(?im)^#{1,4}\s*relation", body)[0]
        substance = re.sub(r"\s+", "", body_wo_rels)
        if len(substance) < threshold:
            out.append({
                "rank": 4,
                "source": rel,
                "topic": ename,
                "question": (f"The {ename} ({etype}) entity is a stub "
                             f"({len(substance)} chars of body) — what's the substance here? "
                             f"(role, why it matters to you, key facts)"),
            })
    return out


def load_reject_file(path: Path) -> list[str]:
    """Read a persistent stoplist file — one suppression substring per line, `#` comments
    and blank lines ignored. Returns [] if the file is missing (default-on but optional)."""
    if not path or not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.split("#", 1)[0].strip()
            if s:
                out.append(s)
    except OSError:
        return []
    return out


def collect(kb: Path, thin_threshold: int = 200,
            exclude: list[str] | None = None) -> list[dict]:
    items = (scan_needs_markers(kb) + scan_review_queue(kb) + scan_open_questions(kb)
             + scan_unverified(kb) + scan_thin_entities(kb, thin_threshold))
    # Dedupe on (source, question), keep best (lowest) rank.
    seen: dict[tuple, dict] = {}
    for it in items:
        key = (it["source"], it["question"][:80])
        if key not in seen or it["rank"] < seen[key]["rank"]:
            seen[key] = it
    ranked = sorted(seen.values(), key=lambda x: (x["rank"], x["source"]))
    # Rotation guard: drop any item whose topic/question/source contains an
    # excluded substring (case-insensitive). The dreaming run passes the prior
    # run's surfaced fingerprints here so the "🧠 Help me remember" block never
    # repeats a question two runs in a row — the recall step's rotate rule made
    # mechanical rather than compose-time discipline.
    if exclude:
        needles = [s.lower() for s in exclude if s and s.strip()]
        if needles:
            ranked = [
                it for it in ranked
                if not any(
                    n in (it["topic"] + " " + it["question"] + " " + it["source"]).lower()
                    for n in needles
                )
            ]
    return ranked


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate KB-enrichment questions (facts only the user can answer).")
    ap.add_argument("--limit", type=int, default=5, help="Max questions to emit (default 5).")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    ap.add_argument("--kb", type=Path, default=DEFAULT_KB, help="knowledge-base directory.")
    ap.add_argument("--thin-threshold", type=int, default=200,
                    help="Body-char floor below which an entity file counts as a thin stub (default 200).")
    ap.add_argument("--exclude", action="append", default=[], metavar="SUBSTR",
                    help="Suppress questions whose topic/question/source contains SUBSTR "
                         "(case-insensitive, repeatable). Used by the dreaming run to pass the "
                         "prior run's surfaced questions so the recall block never repeats two runs "
                         "in a row.")
    ap.add_argument("--reject-file", type=Path, default=DEFAULT_REJECT_FILE, metavar="PATH",
                    help="Persistent stoplist file (default: scripts/enrichment-stoplist.txt) — its "
                         "substrings suppress questions on EVERY run (known-answer / instrumented "
                         "topics + user-rejected questions). One substring per line, # comments OK.")
    ap.add_argument("--no-reject-file", action="store_true",
                    help="Ignore the persistent stoplist (debugging — surfaces suppressed questions).")
    args = ap.parse_args()

    if not args.kb.exists():
        print(f"error: KB directory not found: {args.kb}", file=sys.stderr)
        return 2

    rejects = [] if args.no_reject_file else load_reject_file(args.reject_file)
    ranked = collect(args.kb, args.thin_threshold, args.exclude + rejects)
    picked = ranked[: args.limit] if args.limit and args.limit > 0 else ranked

    if args.json:
        print(json.dumps({"count": len(picked), "total_available": len(ranked),
                          "questions": picked}, indent=2, ensure_ascii=False))
        return 0

    if not picked:
        print("No enrichment questions found — the KB has no open gaps to ask about.")
        return 0

    tier = {0: "explicit [needs:] flag", 1: "explicit review-queue ask", 2: "open question",
            3: "single-source claim", 4: "thin/stub entity"}
    print(f"🧠 {len(picked)} enrichment questions "
          f"({len(ranked)} available; user-only-answerable gaps):\n")
    for n, it in enumerate(picked, 1):
        print(f"{n}. [{tier[it['rank']]} · {it['source']}]")
        print(f"   {it['question']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
