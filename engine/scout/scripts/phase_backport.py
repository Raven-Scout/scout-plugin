"""Reverse-map vault brain-file edits back into phase fragments.

`scoutctl phases backport` diffs a vault's assembled SKILL/DREAMING/RESEARCH
against the ``.scout-state/last-assembled/`` snapshot, locates each divergence
in its source ``phases/`` fragment, conservatively re-templatizes the added
text, and — gated on a round-trip check — writes the safe edits back into the
phase fragments so a future ``/scout-update`` re-render carries them forward
instead of sidecaring (Scout Open Question #10).

Design: docs/superpowers/specs/2026-06-16-scoutctl-phases-backport-design.md

The core functions here are pure (no disk I/O) so the reverse-mapping logic is
unit-testable; the CLI command (`scout.cli`) does config resolution, section
assembly, file writes, and reporting.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scout.scripts.phase_assembly import parse_phase_file, render_template, select_sections

# Assembly source dirs + consumed-modes per brain file — mirrors
# bootstrap._assemble so back-port selection matches what assembly produced.
_ASSEMBLY_MAP: dict[str, tuple[tuple[str, ...], set[str]]] = {
    "SKILL": (("core", "connectors"), {"briefing", "consolidation"}),
    "DREAMING": (("core", "modes"), {"dreaming"}),
    "RESEARCH": (("core", "research"), {"research"}),
}

# Template vars whose values are long/unique enough to reverse without false
# positives in prose. Reversing these is safe and keeps the fragment generic.
SAFE_VARS: tuple[str, ...] = (
    "USER_EMAIL",
    "USER_SLACK_ID",
    "GITHUB_USERNAME",
    "SCOUT_DIR",
    "SCOUTCTL_BIN",
)

# Short/common values (an instance name, a user's first name, a date, a
# timezone) that appear legitimately in prose. Auto-replacing them would
# corrupt the fragment, so we
# never rewrite them; we only REPORT their presence so a human genericizes the
# line before it lands in the (public) engine. A hunk carrying any of these is
# downgraded to needs-review — never auto-written (which would leak instance data).
RISKY_VARS: tuple[str, ...] = (
    "USER_NAME",
    "INSTANCE_NAME",
    "INSTANCE_NAME_LOWER",
    "TIMEZONE",
    "PLATFORM",
    "TODAY_DATE",
    "MAX_BUDGET",
    "AUTO_UPDATE_ENABLED",
    "GITHUB_REPOS",
)


@dataclass
class Hunk:
    """A contiguous block of lines added in ``live`` vs ``snapshot``."""

    added: list[str]
    anchor: str | None  # nearest non-empty preceding line — the insertion anchor


@dataclass
class RenderedSection:
    """A phase section paired with its rendered (var-substituted) body."""

    phase_file: Path
    section_name: str
    raw_body: str       # source body, with {{VARS}}
    rendered_body: str  # what assembly produced (vars substituted)


@dataclass
class HunkResult:
    """Outcome of trying to back-port one hunk."""

    status: str  # "applied" | "needs-review" | "unmapped"
    added: list[str]
    phase_file: Path | None = None
    section_name: str | None = None
    anchor: str | None = None
    retemplatized: list[str] | None = None
    risky_hits: list[str] = field(default_factory=list)
    reason: str = ""


def retemplatize(
    lines: list[str],
    vars_: dict[str, str],
    safe: tuple[str, ...] = SAFE_VARS,
    risky: tuple[str, ...] = RISKY_VARS,
) -> tuple[list[str], list[str]]:
    """Reverse safe template vars in ``lines``; report (don't rewrite) risky ones.

    Returns ``(rewritten_lines, risky_var_names_present)``. Safe values are
    replaced longest-first so a value that is a substring of another doesn't
    get partially clobbered.
    """
    safe_pairs = sorted(
        ((k, vars_[k]) for k in safe if vars_.get(k)),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    out: list[str] = []
    for line in lines:
        for name, value in safe_pairs:
            line = line.replace(value, "{{%s}}" % name)
        out.append(line)
    risky_hits = [k for k in risky if vars_.get(k) and any(vars_[k] in ln for ln in out)]
    return out, risky_hits


def build_rendered_sections(
    phases_root: Path, kind: str, vars_: dict[str, str], enabled_connectors: set[str]
) -> list[RenderedSection]:
    """Assemble the per-section provenance for ``kind``, mirroring ``_assemble``.

    Same source dirs, mode filter, sorted glob, and connector gating as
    bootstrap's assembly, but it keeps each section's source file + raw and
    rendered bodies (which ``_assemble`` discards) so a diff hunk can be traced
    back to its fragment. Phase files that fail to parse are skipped, exactly as
    assembly skips them.
    """
    try:
        src_dirs, modes = _ASSEMBLY_MAP[kind]
    except KeyError:
        raise ValueError(f"unknown brain-file kind: {kind!r} (expected SKILL/DREAMING/RESEARCH)")
    out: list[RenderedSection] = []
    for dirname in src_dirs:
        src = phases_root / dirname
        if not src.exists():
            continue
        for phase_file in sorted(src.glob("*.md")):
            try:
                sections = parse_phase_file(phase_file)
            except (ValueError, yaml.YAMLError):
                continue
            for s in select_sections(sections, enabled_connectors=enabled_connectors, modes=modes):
                out.append(
                    RenderedSection(
                        phase_file=phase_file,
                        section_name=s.name,
                        raw_body=s.body,
                        rendered_body=render_template(s.body, vars_),
                    )
                )
    return out


def apply_to_phase_text(file_text: str, old_body: str, new_body: str) -> str:
    """Replace a section's ``old_body`` with ``new_body`` in a phase file's text.

    Section bodies are large, contiguous, unique blocks, so a single literal
    replace preserves frontmatter and every other section. Raises ``ValueError``
    if the body isn't present verbatim (guards against a stale match).
    """
    if old_body not in file_text:
        raise ValueError("section body not found verbatim in phase file — refusing to write")
    return file_text.replace(old_body, new_body, 1)


def diff_hunks(snapshot: str, live: str) -> list[Hunk]:
    """Return the added/changed line blocks in ``live`` relative to ``snapshot``."""
    a = snapshot.splitlines()
    b = live.splitlines()
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    hunks: list[Hunk] = []
    for tag, _i1, _i2, j1, j2 in sm.get_opcodes():
        if tag not in ("insert", "replace"):
            continue
        anchor = next((ln for ln in reversed(b[:j1]) if ln.strip()), None)
        hunks.append(Hunk(added=b[j1:j2], anchor=anchor))
    return hunks


def _unique_anchor_index(anchor: str, body: str) -> int | None:
    """Index of ``anchor`` in ``body`` iff it appears exactly once, else None."""
    lines = body.splitlines()
    hits = [i for i, ln in enumerate(lines) if ln == anchor]
    return hits[0] if len(hits) == 1 else None


def insert_after_anchor(
    raw_body: str, rendered_body: str, anchor: str, new_lines: list[str]
) -> str:
    """Insert ``new_lines`` into ``raw_body`` after the line matching ``anchor``.

    The anchor is located in the *rendered* body; because ``render_template``
    substitutes single-line values it preserves a 1:1 line correspondence, so
    the same index applies to the raw body.
    """
    rendered_lines = rendered_body.splitlines()
    raw_lines = raw_body.splitlines()
    idx = rendered_lines.index(anchor)
    return "\n".join(raw_lines[: idx + 1] + new_lines + raw_lines[idx + 1 :])


def plan_backport(
    snapshot: str, live: str, sections: list[RenderedSection], vars_: dict[str, str]
) -> list[HunkResult]:
    """Map each divergence hunk to a phase section and classify its outcome.

    Status:
      - ``applied``      — mapped to one section, no risky vars, round-trip ✓
      - ``needs-review`` — ambiguous anchor, risky var present, or round-trip miss
      - ``unmapped``     — anchor in no phase section (vault-only drift)
    """
    results: list[HunkResult] = []
    for hunk in diff_hunks(snapshot, live):
        if not hunk.anchor:
            results.append(
                HunkResult("needs-review", hunk.added, reason="no anchor (insertion at section start)")
            )
            continue

        matches = [
            s for s in sections if _unique_anchor_index(hunk.anchor, s.rendered_body) is not None
        ]
        if not matches:
            results.append(
                HunkResult("unmapped", hunk.added, reason="anchor not found in any phase section (vault-only drift?)")
            )
            continue
        if len(matches) > 1:
            results.append(
                HunkResult("needs-review", hunk.added, reason=f"ambiguous anchor (matches {len(matches)} sections)")
            )
            continue

        sec = matches[0]
        retempl, risky = retemplatize(hunk.added, vars_)
        if risky:
            results.append(
                HunkResult(
                    "needs-review", hunk.added,
                    phase_file=sec.phase_file, section_name=sec.section_name, anchor=hunk.anchor,
                    retemplatized=retempl, risky_hits=risky,
                    reason="risky template var(s) in added text — genericize before writing",
                )
            )
            continue

        edited_raw = insert_after_anchor(sec.raw_body, sec.rendered_body, hunk.anchor, retempl)
        if "\n".join(hunk.added) in render_template(edited_raw, vars_):
            results.append(
                HunkResult(
                    "applied", hunk.added,
                    phase_file=sec.phase_file, section_name=sec.section_name, anchor=hunk.anchor,
                    retemplatized=retempl, reason="round-trip ✓",
                )
            )
        else:
            results.append(
                HunkResult(
                    "needs-review", hunk.added,
                    phase_file=sec.phase_file, section_name=sec.section_name, anchor=hunk.anchor,
                    retemplatized=retempl, reason="round-trip mismatch — would not re-assemble to the vault line",
                )
            )
    return results


def apply_section_edits(
    raw_body: str, rendered_body: str, edits: list[tuple[str, list[str]]]
) -> str:
    """Apply multiple ``(anchor, new_lines)`` inserts to one section's raw body.

    Inserts are applied in descending anchor-index order so each insertion does
    not shift the indices of edits still to be applied.
    """
    rendered_lines = rendered_body.splitlines()
    raw_lines = raw_body.splitlines()
    resolved = sorted(
        ((rendered_lines.index(anchor), new_lines) for anchor, new_lines in edits),
        key=lambda t: t[0],
        reverse=True,
    )
    for idx, new_lines in resolved:
        raw_lines[idx + 1 : idx + 1] = new_lines
    return "\n".join(raw_lines)
