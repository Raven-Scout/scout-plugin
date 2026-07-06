"""Event-trigger engine (v1 polling) — fires on events, not time.

Spec: docs/specs/event-triggers.md. The schedule dispatcher fires on time;
this package adds the second fire-condition source: external events (Slack
mentions, GitHub notifications) and internal engine events, declared in the
vault's ``.scout-state/triggers.yaml`` and evaluated at the top of every
5-minute ``schedule_tick``.
"""

from __future__ import annotations
