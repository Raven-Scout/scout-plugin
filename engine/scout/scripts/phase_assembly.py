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

        fm = yaml.safe_load(fm_text) or {}

        # Validate and normalise ``mode``: must be a list of strings (or absent).
        raw_mode = fm.get("mode")
        if raw_mode is None:
            mode: list[str] = []
        elif isinstance(raw_mode, list):
            mode = [str(m) for m in raw_mode]
        elif isinstance(raw_mode, str):
            raise ValueError(
                f"{path}: 'mode' must be a YAML list, got a plain string {raw_mode!r}. "
                "Wrap in brackets: [briefing]"
            )
        else:
            raise ValueError(
                f"{path}: 'mode' must be a list, got {type(raw_mode).__name__}"
            )

        # Validate ``requires``: must be a string or null, not a list.
        raw_requires = fm.get("requires")
        if raw_requires is None:
            requires: str | None = None
        elif isinstance(raw_requires, str):
            requires = raw_requires
        else:
            raise ValueError(
                f"{path}: 'requires' must be a string or null, "
                f"got {type(raw_requires).__name__}"
            )

        sections.append(
            PhaseSection(
                phase=str(fm.get("phase", "")),
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
) -> list[PhaseSection]:
    """Filter sections: keep when requires is null OR connector enabled.

    Optionally narrow to a specific slot (e.g., "outbound-scan").
    """
    out: list[PhaseSection] = []
    for s in sections:
        if s.requires is not None and s.requires not in enabled_connectors:
            continue
        if slot is not None and s.slot != slot:
            continue
        out.append(s)
    return out


_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def render_template(text: str, variables: dict[str, str]) -> str:
    """Replace ``{{VAR}}`` with ``variables[VAR]``; unknown vars become ""."""
    return _VAR_RE.sub(lambda m: variables.get(m.group(1), ""), text)
