# Changelog

All notable changes to the Scout plugin are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-06-02

### Added
- Dreaming-proposal backlog ported into the engine (phases, schema, recurring-task primitive).
- `session-tool-log` Stop hook (per-tool accounting reconstructed from the session JSONL).
- 3-way merge for vault-edited `parser.py` on upgrade (Pattern #68).

### Changed
- `connector_health_report`: Pattern #54 cross-mode liveness suppression.
