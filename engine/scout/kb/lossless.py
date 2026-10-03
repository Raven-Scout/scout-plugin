"""Lossless check for KB splits: every tag, issue id, URL and wikilink target
in the old version of a file must still exist somewhere in the vault's
markdown. Used by the one-time migration and by every dreaming shrink pass."""

from __future__ import annotations

import re
from pathlib import Path

from scout.kb.git_io import git, tracked_files
from scout.kb.lint_rules import wikilink_targets

_TAG = re.compile(r"(?<![\w#/])#[A-Z][A-Z0-9]{2,}\b")
_ISSUE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b")
_URL = re.compile(r"https?://[^\s)\]>|`\"']+")


def _plain_tokens(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for rx in (_TAG, _ISSUE):
        for m in rx.finditer(text):
            seen.setdefault(m.group(0), None)
    for m in _URL.finditer(text):
        seen.setdefault(m.group(0).rstrip(".,;:"), None)
    return list(seen)


def lossless_check(repo: Path, old_rev: str, rel: str) -> list[str]:
    old = git(repo, "show", f"{old_rev}:{rel}").stdout
    parts: list[str] = []
    for p in tracked_files(repo):
        if p.endswith(".md") and (repo / p).is_file():
            parts.append((repo / p).read_text(encoding="utf-8", errors="replace"))
    corpus = "\n".join(parts)
    corpus_links = {t.lower() for t in wikilink_targets(corpus)}
    corpus_tokens = set(_plain_tokens(corpus))
    missing = [tok for tok in _plain_tokens(old) if tok not in corpus_tokens]
    seen_links: dict[str, None] = {}
    for t in wikilink_targets(old):
        seen_links.setdefault(t, None)
    missing += [f"[[{t}]]" for t in seen_links if t.lower() not in corpus_links]
    return missing
