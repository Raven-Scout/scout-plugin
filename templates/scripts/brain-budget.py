#!/usr/bin/env python3
"""brain-budget.py — the cost rule for Scout's self-editing brains.

Scout's dreaming lane edits its own instruction set (SKILL.md / DREAMING.md /
RESEARCH.md). Nothing has ever measured what that costs. Measured 2026-09-25:
SKILL.md went 1073 -> 1615 lines between 2026-04-20 and 2026-09-25 and **never
once shrank** on any sampled date. Every line is re-read by every scheduled run,
forever, so an unpruned rule is a permanent per-run tax.

This is the driver for the "cost rule" and "pruning" halves of RRSI
(Regularized Recursive Self-Improvement, Xia et al., arXiv:2609.24972), adapted
to Scout in dreaming-proposals/2026-09-25-rrsi-regularized-self-improvement.md.

It does not edit anything. It reports:
  1. Current size of each brain vs its declared budget.
  2. Growth since a baseline commit (default: 30 days back).
  3. Per-section sizes, so pruning candidates are named rather than guessed.

Exit codes:
  0 = every brain within budget
  3 = at least one brain over budget (the dreaming run must prune before adding)
  4 = soft-skip (not a git repo / brains missing) -- never fails a run
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent

# Declared budgets. These are a policy choice, not a measurement -- they are set
# at roughly the 2026-09-25 size so the rule bites on the NEXT growth, not
# retroactively. Raising a budget is allowed; doing it silently is the failure.
BUDGETS: dict[str, int] = {
    "SKILL.md": 1650,
    "DREAMING.md": 850,
    "RESEARCH.md": 260,
}


@dataclass
class Section:
    heading: str
    start: int
    lines: int = 0


@dataclass
class BrainReport:
    name: str
    path: Path
    current: int
    budget: int
    baseline: int | None
    baseline_date: str | None
    sections: list[Section] = field(default_factory=list)

    @property
    def over(self) -> int:
        return self.current - self.budget

    @property
    def growth(self) -> int | None:
        return None if self.baseline is None else self.current - self.baseline


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(VAULT), *args],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def baseline_line_count(filename: str, before: str) -> tuple[int | None, str | None]:
    """Line count of `filename` at the last commit before `before` (YYYY-MM-DD)."""
    rev = _git("rev-list", "-1", f"--before={before}", "HEAD")
    if not rev or not rev.strip():
        return None, None
    sha = rev.strip()
    blob = _git("show", f"{sha}:{filename}")
    if blob is None:
        return None, None
    return len(blob.splitlines()), before


def split_sections(text: str) -> list[Section]:
    """Top-level (## / #) sections with their line counts."""
    sections: list[Section] = []
    current: Section | None = None
    for i, line in enumerate(text.splitlines(), start=1):
        if re.match(r"^#{1,2} \S", line):
            if current:
                current.lines = i - current.start
                sections.append(current)
            current = Section(heading=line.strip()[:78], start=i)
        elif current:
            continue
    if current:
        current.lines = len(text.splitlines()) - current.start + 1
        sections.append(current)
    return sections


def build_report(filename: str, before: str) -> BrainReport | None:
    path = VAULT / filename
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    base, base_date = baseline_line_count(filename, before)
    return BrainReport(
        name=filename,
        path=path,
        current=len(text.splitlines()),
        budget=BUDGETS.get(filename, 10**9),
        baseline=base,
        baseline_date=base_date,
        sections=split_sections(text),
    )


def render(reports: list[BrainReport], top: int) -> str:
    out: list[str] = ["# Brain budget — Scout's self-editing instruction set", ""]
    total_now = sum(r.current for r in reports)
    out.append(
        f"Scanned {len(reports)} brain(s) · **{total_now} lines total**, "
        "re-read in full by every scheduled run."
    )
    out.append("")
    out.append("| Brain | Lines | Budget | Over | Since baseline | Status |")
    out.append("|---|---|---|---|---|---|")
    for r in reports:
        growth = "—" if r.growth is None else f"{r.growth:+d} (since {r.baseline_date})"
        status = "🔴 OVER BUDGET" if r.over > 0 else "✅ within budget"
        over = f"+{r.over}" if r.over > 0 else "—"
        out.append(
            f"| `{r.name}` | {r.current} | {r.budget} | {over} | {growth} | {status} |"
        )
    out.append("")

    offenders = [r for r in reports if r.over > 0]
    if offenders:
        out.append("## 🔴 Prune before you add")
        out.append("")
        out.append(
            "RRSI's cost rule: extra tokens must be paid for by measured improvement. "
            "A brain over budget may still be edited — but the edit must be **net "
            "non-increasing**, or the budget must be raised explicitly in this file "
            "with a stated reason. Silently growing past it is the failure mode."
        )
        out.append("")
        for r in offenders:
            out.append(f"### `{r.name}` — largest sections (pruning candidates)")
            out.append("")
            ranked = sorted(r.sections, key=lambda s: s.lines, reverse=True)[:top]
            out.append("| Lines | Section |")
            out.append("|---|---|")
            for s in ranked:
                out.append(f"| {s.lines} | {s.heading} |")
            out.append("")
            out.append(
                "_Size is not guilt._ Rank by size to find candidates, then apply the "
                "actual test: does this section still earn its per-run cost, or was it "
                "written to one incident that has not recurred?"
            )
            out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--since",
        default=(date.today() - timedelta(days=30)).isoformat(),
        help="Baseline date for growth comparison (YYYY-MM-DD, default: 30d ago)",
    )
    ap.add_argument("--top", type=int, default=10, help="Pruning candidates to list")
    ap.add_argument("--quiet", action="store_true", help="Print only when over budget")
    args = ap.parse_args()

    if not (VAULT / ".git").exists():
        print("brain-budget: not a git repo — soft-skip", file=sys.stderr)
        return 4

    reports = [r for f in BUDGETS if (r := build_report(f, args.since))]
    if not reports:
        print("brain-budget: no brains found — soft-skip", file=sys.stderr)
        return 4

    over = any(r.over > 0 for r in reports)
    if over or not args.quiet:
        print(render(reports, args.top))
    return 3 if over else 0


if __name__ == "__main__":
    raise SystemExit(main())
