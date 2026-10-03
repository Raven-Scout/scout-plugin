"""`scoutctl kb lint` — the pre-commit ratchet that keeps the vault from
growing mega-files.

Blocking (in ``block`` mode): an over-budget file that grew, a new file over
budget, an added run-diary heading outside log paths, an added line over the
line limit. Warnings: a topic-note paragraph with no citation, a wikilink that
resolves nowhere. Only ADDED lines are line-checked, so existing debt never
blocks a commit; it drains through the dreaming shrink pass instead.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import TextIO

from scout import paths
from scout.kb.git_io import (
    added_lines,
    head_size,
    main_root,
    staged_paths,
    staged_size,
    staged_text,
    tracked_files,
)
from scout.kb.lint_config import LintConfig, load_lint_config
from scout.kb.lint_rules import has_citation, is_diary_heading, is_heading, wikilink_targets

OVERRIDE_ENV = "SCOUT_LINT_OVERRIDE"


@dataclass(frozen=True)
class Finding:
    path: str
    check: str
    blocking: bool
    message: str
    line: int | None = None


@dataclass
class LintResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.blocking]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if not f.blocking]


def _kb(n: int) -> str:
    return f"{n / 1024:.1f} KB"


def _check_size(repo: Path, rel: str, head_rel: str | None, cfg: LintConfig) -> list[Finding]:
    budget = cfg.budget_for(rel)
    if budget is None:
        return []
    new = staged_size(repo, rel)
    if new <= budget:
        return []
    old = head_size(repo, head_rel) if head_rel else None
    if old is None:
        return [
            Finding(
                rel,
                "size",
                True,
                f"new file is {_kb(new)}, over its {_kb(budget)} budget — split it into smaller linked notes",
            )
        ]
    if new > old:
        return [
            Finding(
                rel,
                "size",
                True,
                f"{_kb(new)} is over its {_kb(budget)} budget and grew by {_kb(new - old)} — "
                "move content into topic/source notes (or the log's monthly shard) instead of adding here",
            )
        ]
    return []


def _check_lines(rel: str, added: list[tuple[int, str]], cfg: LintConfig) -> list[Finding]:
    limit = cfg.line_limit(rel)
    diary_scope = not cfg.is_log(rel)
    out: list[Finding] = []
    for n, text in added:
        if len(text) > limit:
            out.append(
                Finding(
                    rel,
                    "mega-line",
                    True,
                    f"line {n} is {len(text)} chars (limit {limit}) — put the detail in a linked note",
                    n,
                )
            )
        if diary_scope and is_diary_heading(text):
            out.append(
                Finding(
                    rel,
                    "diary",
                    True,
                    f"line {n} adds a run-diary heading {text[:100]!r} — edit the current state in place; "
                    "the commit message carries what this run did",
                    n,
                )
            )
    return out


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    """(first_line, last_line, text) for prose paragraphs, skipping frontmatter,
    fenced code, headings and tables. Line numbers are 1-based."""
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        i += 1
    out: list[tuple[int, int, str]] = []
    buf: list[str] = []
    start = 0
    in_fence = False

    def flush(end: int) -> None:
        if buf:
            out.append((start, end, "\n".join(buf)))
            buf.clear()

    for idx in range(i, len(lines)):
        ln = lines[idx]
        n = idx + 1
        if ln.lstrip().startswith(("```", "~~~")):
            flush(n - 1)
            in_fence = not in_fence
            continue
        if in_fence or not ln.strip() or is_heading(ln) or ln.lstrip().startswith("|"):
            flush(n - 1)
            continue
        if not buf:
            start = n
        buf.append(ln)
    flush(len(lines))
    return out


def _check_citations(rel: str, text: str, added_nums: set[int]) -> list[Finding]:
    out: list[Finding] = []
    for first, last, para in _paragraphs(text):
        if any(first <= n <= last for n in added_nums) and not has_citation(para):
            out.append(
                Finding(
                    rel,
                    "citation",
                    False,
                    f"paragraph at line {first} has no [[source]] or URL citation",
                    first,
                )
            )
    return out


def _link_targets(repo: Path) -> set[str]:
    known: set[str] = set()
    for p in tracked_files(repo):
        pp = PurePosixPath(p)
        for form in (p, str(pp.with_suffix("")), pp.name, pp.stem):
            known.add(form.lower())
        if p.startswith("knowledge-base/"):
            kbrel = PurePosixPath(p[len("knowledge-base/") :])
            known.add(str(kbrel).lower())
            known.add(str(kbrel.with_suffix("")).lower())
    return known


def _check_links(rel: str, added: list[tuple[int, str]], known: set[str]) -> list[Finding]:
    out: list[Finding] = []
    for n, text in added:
        for target in wikilink_targets(text):
            t = target.lower()
            if t not in known and PurePosixPath(t).name not in known:
                out.append(Finding(rel, "dangling-link", False, f"line {n}: [[{target}]] resolves to no file", n))
    return out


def lint_staged(repo: Path, cfg: LintConfig) -> LintResult:
    result = LintResult()
    known: set[str] | None = None
    for sp in staged_paths(repo):
        if not cfg.in_scope(sp.rel):
            continue
        result.findings += _check_size(repo, sp.rel, sp.head_rel, cfg)
        added = added_lines(repo, sp.rel, sp.head_rel)
        result.findings += _check_lines(sp.rel, added, cfg)
        if cfg.is_topic(sp.rel):
            result.findings += _check_citations(sp.rel, staged_text(repo, sp.rel), {n for n, _ in added})
        if known is None:
            known = _link_targets(repo)
        result.findings += _check_links(sp.rel, added, known)
    return result


def over_budget(repo: Path, cfg: LintConfig) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    for rel in tracked_files(repo):
        if not cfg.in_scope(rel):
            continue
        budget = cfg.budget_for(rel)
        path = repo / rel
        if budget is None or not path.is_file():
            continue
        size = path.stat().st_size
        if size > budget:
            rows.append((rel, size, budget))
    rows.sort(key=lambda r: r[1] - r[2], reverse=True)
    return rows


def format_findings(result: LintResult, cfg: LintConfig) -> str:
    lines = [f"kb-lint: {len(result.blocking)} blocking, {len(result.warnings)} warning(s) (mode: {cfg.mode})"]
    for f in result.findings:
        mark = "✗" if f.blocking else "!"
        where = f"{f.path}:{f.line}" if f.line else f.path
        lines.append(f"  {mark} {where} [{f.check}] {f.message}")
    if result.blocking and cfg.mode == "block":
        lines.append(
            "Commit blocked. Fix the files above — or, only after two failed attempts, re-run with "
            f'{OVERRIDE_ENV}="<reason>" (logged; the next dreaming run must clean it up).'
        )
    elif result.blocking:
        lines.append("(report mode — not blocking; findings logged to .scout-logs/kb-lint.jsonl)")
    return "\n".join(lines)


def _append_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_hook(
    repo: Path,
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
    out: TextIO | None = None,
) -> int:
    env = os.environ if env is None else env
    out = out or sys.stderr
    cfg = load_lint_config(repo)
    result = lint_staged(repo, cfg)
    if not result.findings:
        return 0
    logs = paths.logs_dir(main_root(repo))
    ts = (now or datetime.now(UTC)).isoformat()
    run_mode = env.get("SCOUT_MODE", "interactive")
    _append_jsonl(
        logs / "kb-lint.jsonl",
        [{"ts": ts, "mode": run_mode, "lint_mode": cfg.mode, **asdict(f)} for f in result.findings],
    )
    print(format_findings(result, cfg), file=out)
    if not result.blocking or cfg.mode == "report":
        return 0
    reason = env.get(OVERRIDE_ENV, "").strip()
    if reason:
        files = sorted({f.path for f in result.blocking})
        _append_jsonl(logs / "lint-overrides.log", [{"ts": ts, "mode": run_mode, "files": files, "reason": reason}])
        print(f"kb-lint: override accepted ({reason}) — logged to .scout-logs/lint-overrides.log", file=out)
        return 0
    return 1


def override_count(logs_dir: Path, *, days: int, now: datetime) -> int:
    log = logs_dir / "lint-overrides.log"
    if not log.exists():
        return 0
    cutoff = now - timedelta(days=days)
    n = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            ts = datetime.fromisoformat(json.loads(line)["ts"])
        except (ValueError, KeyError, TypeError):
            continue
        if ts >= cutoff:
            n += 1
    return n
