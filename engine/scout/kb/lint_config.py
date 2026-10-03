"""kb-lint configuration: size budgets, path scopes and blocking mode.

Defaults ship in scout/defaults/scout-config.yaml under ``kb_lint``. A vault
may add budgets with a top-level ``kb_budgets: {glob: bytes}`` map, which is
consulted before the defaults (first match wins), and may set
``kb_lint.mode`` to ``block`` once report-mode tuning is done.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from scout.config import load_config

_MODES = frozenset({"report", "block"})
_SCOPE_PREFIXES = ("knowledge-base/", "action-items/")


@lru_cache(maxsize=256)
def _glob_re(glob: str) -> re.Pattern[str]:
    """Translate a path glob to a regex: ``*`` stays within one segment,
    ``**`` crosses segments. (fnmatch's ``*`` crosses ``/``, which would make
    ``projects/*/*.md`` match arbitrarily deep files.)"""
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(glob: str, rel: str) -> bool:
    return bool(_glob_re(glob).match(rel))


def _any(globs: tuple[str, ...], rel: str) -> bool:
    return any(glob_match(g, rel) for g in globs)


@dataclass(frozen=True)
class Budget:
    glob: str
    bytes: int | None


@dataclass(frozen=True)
class LintConfig:
    mode: str
    max_line_chars: int
    max_line_chars_strict: int
    budgets: tuple[Budget, ...]
    log_globs: tuple[str, ...]
    strict_line_globs: tuple[str, ...]
    exclude_globs: tuple[str, ...]
    topic_globs: tuple[str, ...]

    def in_scope(self, rel: str) -> bool:
        return rel.endswith(".md") and rel.startswith(_SCOPE_PREFIXES) and not _any(self.exclude_globs, rel)

    def budget_for(self, rel: str) -> int | None:
        for b in self.budgets:
            if glob_match(b.glob, rel):
                return b.bytes
        return None

    def is_log(self, rel: str) -> bool:
        return _any(self.log_globs, rel)

    def is_topic(self, rel: str) -> bool:
        return _any(self.topic_globs, rel)

    def line_limit(self, rel: str) -> int:
        return self.max_line_chars_strict if _any(self.strict_line_globs, rel) else self.max_line_chars


def _bytes(value: Any) -> int | None:
    return None if value is None else int(value)


def _globs(raw: dict[str, Any], key: str) -> tuple[str, ...]:
    return tuple(str(g) for g in (raw.get(key) or []))


def load_lint_config(data_dir: Path | None = None) -> LintConfig:
    cfg = load_config(data_dir)
    raw = cfg.get("kb_lint") or {}
    if not isinstance(raw, dict):
        raw = {}
    user_budgets = cfg.get("kb_budgets") or {}
    budgets = [Budget(str(g), _bytes(b)) for g, b in user_budgets.items()] if isinstance(user_budgets, dict) else []
    budgets += [Budget(str(d["glob"]), _bytes(d.get("bytes"))) for d in raw.get("budgets") or []]
    mode = str(raw.get("mode", "report"))
    return LintConfig(
        mode=mode if mode in _MODES else "report",
        max_line_chars=int(raw.get("max_line_chars", 1500)),
        max_line_chars_strict=int(raw.get("max_line_chars_strict", 500)),
        budgets=tuple(budgets),
        log_globs=_globs(raw, "log_globs"),
        strict_line_globs=_globs(raw, "strict_line_globs"),
        exclude_globs=_globs(raw, "exclude_globs"),
        topic_globs=_globs(raw, "topic_globs"),
    )
