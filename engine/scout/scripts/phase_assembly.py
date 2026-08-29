"""Phase file parsing, selection, and template rendering.

Phase files (under ``~/scout-plugin/phases/{core,connectors,modes,research}/``)
have YAML frontmatter and may contain multiple sections separated by ``---``
fences with their own frontmatter blocks. The bootstrap pipeline uses this
module to assemble SKILL.md / DREAMING.md / RESEARCH.md from phase files
based on which connectors the user has enabled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PhaseSection:
    """One frontmatter+body section of a phase file."""

    phase: str
    name: str
    slot: str
    mode: list[str]
    requires: str | None
    body: str


_FRONTMATTER_FENCE = "---"

# ``requires: any-of(slack, notify:telegram)`` — the section is kept when AT
# LEAST ONE member is enabled.
#
# The plain string form is unchanged and still means "this one connector". The
# disjunction exists for phases that need *a* surface rather than *the* surface:
# feedback processing needs somewhere to read replies from, and a vault that has
# moved its wrap to Telegram has one even with Slack absent. Under the old
# single-string rule that vault silently lost its entire self-improvement loop —
# the phase was dropped at assembly time, so nothing at runtime could report the
# absence.
_ANY_OF_RE = re.compile(r"^\s*any-of\s*\((?P<members>.*)\)\s*$", re.IGNORECASE | re.DOTALL)


def _any_of_members(requires: str) -> list[str]:
    """Members of an ``any-of(...)`` expression; empty list if not one."""
    m = _ANY_OF_RE.match(requires)
    if not m:
        return []
    return [p.strip() for p in m.group("members").split(",") if p.strip()]


def requires_members(requires: str | None) -> list[str]:
    """The connector keys a ``requires:`` expression names, in order.

    ``None`` → ``[]``; a bare string → that one key; ``any-of(a, b)`` → both.
    Callers that validate keys (the connector-key invariant, tooling that maps
    phases to probes) must iterate this rather than the raw string, or a
    disjunction's members go unchecked and a typo inside one becomes silently
    un-enableable — the exact failure the invariant exists to prevent.
    """
    if requires is None:
        return []
    return _any_of_members(requires) or [requires]


def _requirement_met(requires: str, enabled_connectors: set[str]) -> bool:
    """Is a section's ``requires`` satisfied by the enabled connector set?

    A bare string must be enabled. An ``any-of(...)`` needs one member enabled.
    An ``any-of()`` with no members is rejected at parse time, so an empty
    disjunction cannot reach here and silently evaluate to False.
    """
    members = _any_of_members(requires)
    if members:
        return any(m in enabled_connectors for m in members)
    return requires in enabled_connectors


def parse_phase_file(path: Path) -> list[PhaseSection]:
    """Return all sections in a phase file (single or multi).

    Raises ``ValueError`` if the file does not start with a frontmatter fence,
    if a frontmatter block is malformed, or if ``mode`` / ``requires`` have
    unexpected types.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith(_FRONTMATTER_FENCE):
        raise ValueError(f"{path}: phase file must start with '---' frontmatter fence")

    # Split on lines that are exactly "---" (frontmatter delimiter).
    # Multi-section files have alternating frontmatter blocks and bodies:
    #   ---\n<frontmatter>\n---\n<body>\n---\n<frontmatter>\n---\n<body>\n...
    parts = re.split(r"^---\s*$", text, flags=re.MULTILINE)
    # parts[0] is the leading empty string before the first '---'.
    # Then alternating: frontmatter, body, frontmatter, body, ...
    sections: list[PhaseSection] = []
    i = 1
    while i < len(parts):
        fm_text = parts[i]
        # Body is the next part; may be absent for a corrupt/trailing fence.
        body = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2
        # Skip empty junk sections (trailing '---' fence is a common editor artifact).
        if not fm_text.strip() and not body.strip():
            continue

        fm = yaml.safe_load(fm_text) or {}

        section_id = fm.get("name") or f"section at index {(i // 2)}"

        # Validate and normalise ``mode``: must be a list of strings (or absent).
        raw_mode = fm.get("mode")
        if raw_mode is None:
            mode: list[str] = []
        elif isinstance(raw_mode, list):
            mode = [str(m) for m in raw_mode]
        elif isinstance(raw_mode, str):
            raise ValueError(
                f"{path} [{section_id}]: 'mode' must be a YAML list, got {type(raw_mode).__name__}. "
                "Wrap in brackets: [briefing]"
            )
        else:
            raise ValueError(f"{path} [{section_id}]: 'mode' must be a YAML list, got {type(raw_mode).__name__}")

        # Validate ``requires``: must be a string or null, not a list. The list
        # form stays rejected on purpose — a bare YAML list does not say whether
        # it means all-of or any-of, and the two differ. ``any-of(a, b)`` spells
        # the disjunction out; see _requirement_met.
        raw_requires = fm.get("requires")
        if raw_requires is None:
            requires: str | None = None
        elif isinstance(raw_requires, str):
            requires = raw_requires
            if _ANY_OF_RE.match(requires) and not _any_of_members(requires):
                raise ValueError(
                    f"{path} [{section_id}]: 'requires' any-of(...) is empty; list at least one connector or use null"
                )
        else:
            raise ValueError(
                f"{path} [{section_id}]: 'requires' must be a string or null, got {type(raw_requires).__name__}"
            )

        # Validate ``phase``: must be present and non-empty.
        phase_val = str(fm.get("phase", "")).strip()
        if not phase_val:
            raise ValueError(f"{path} [{section_id}]: 'phase' field is required and must be non-empty")

        sections.append(
            PhaseSection(
                phase=phase_val,
                name=str(fm.get("name", "")),
                slot=str(fm.get("slot", "")),
                mode=mode,
                requires=requires,
                body=body.strip("\n"),
            )
        )
    return sections


def select_sections(
    sections: list[PhaseSection],
    *,
    enabled_connectors: set[str],
    slot: str | None = None,
    modes: set[str] | None = None,
) -> list[PhaseSection]:
    """Filter sections by connector, slot, and mode.

    Kept when ALL of the following hold:
      - ``requires`` is null OR is satisfied per :func:`_requirement_met`
      - ``slot`` is None OR matches the section's slot
      - ``modes`` is None OR the section's mode is empty (applies to every
        target) OR the section's mode list intersects ``modes``

    The ``modes`` filter exists so a phase declared ``mode: [briefing]``
    doesn't leak into a DREAMING.md assembly. Empty/absent mode means
    "applies to every assembly target" (back-compat: phases authored
    before mode filtering was enforced).
    """
    out: list[PhaseSection] = []
    for s in sections:
        if s.requires is not None and not _requirement_met(s.requires, enabled_connectors):
            continue
        if slot is not None and s.slot != slot:
            continue
        if modes is not None and s.mode and not set(s.mode) & modes:
            continue
        out.append(s)
    return out


_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render_template(text: str, variables: dict[str, str]) -> str:
    """Replace ``{{VAR}}`` with ``variables[VAR]``; unknown vars become ""."""
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), ""), text)
