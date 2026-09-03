"""Shared two-level scanner for the vault's ``scout-config.yaml``.

Both :mod:`scout.scripts.budget_check` and :mod:`scout.scripts.heartbeat` read a
handful of scalar knobs out of the vault config on a hot path where importing
pyyaml is not worth the startup cost, so each carried its own ``grep``-shaped
line scanner. Keeping two copies let them drift: heartbeat learned the nested
``off_peak: {start, end}`` shape the template writes while budget_check stayed
flat, and a flat scanner is **parent-blind** — it matches ``key: value`` at any
indentation under any parent, so an unrelated subtree can set the budget and a
duplicate key silently wins by being last.

That blindness matters because ``scout-config.yaml`` is not a single-purpose
override file: it doubles as bootstrap state (version stamps, connectors,
schedule, plan), written by several producers, with many subtrees.

So the rule here is explicit about parentage:

* **nested keys** match only as ``(section, key)`` — the key indented directly
  under its expected top-level section. ``plan.rate_limit_window_hours`` is set
  by ``plan:`` and by nothing else.
* **flat keys** match only at top level (no indent), for hand-written override
  files that predate the nested shape.

An indented key under the wrong parent matches nothing, which is the whole
point. Values are returned as raw strings; each caller casts and validates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Captures indent, key, and an OPTIONAL value — a key with no value is a
# section header, which is how the section context is tracked.
_CONFIG_LINE_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([^#\s][^#]*?)?\s*(?:#.*)?$")


def scan_overrides(
    text: str,
    *,
    flat_keys: Mapping[str, str] | None = None,
    nested_keys: Mapping[tuple[str, str], str] | None = None,
) -> dict[str, str]:
    """Map config text to ``{field_name: raw_value}``.

    ``flat_keys`` maps a top-level ``yaml_key`` to a field name; ``nested_keys``
    maps ``(section, yaml_key)`` to a field name. Keys that match neither rule —
    including a nested key found under the wrong parent — are ignored.
    """
    flat = flat_keys or {}
    nested = nested_keys or {}
    out: dict[str, str] = {}
    section: str | None = None

    for line in text.splitlines():
        m = _CONFIG_LINE_RE.match(line)
        if not m:
            continue
        indent, yaml_key, raw_value = m.group(1), m.group(2), m.group(3)

        if raw_value is None:
            # `key:` with no value. Only a top-level one opens a section; a
            # nested one closes the context so its children can't be mistaken
            # for direct children of the enclosing section.
            section = yaml_key if not indent else None
            continue

        if not indent:
            section = None
            field_name = flat.get(yaml_key)
        else:
            field_name = nested.get((section, yaml_key)) if section else None

        if field_name is None:
            continue
        out[field_name] = raw_value.strip().strip("\"'")

    return out
