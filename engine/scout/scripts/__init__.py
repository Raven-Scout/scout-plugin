"""Scheduled-run scripts (run AFTER a session ends; not hooks).

Currently:
    - connector_health_report: rolls up connector-calls JSONL into the
      `knowledge-base/connector-health.md` matrix and fires alerts.
"""
