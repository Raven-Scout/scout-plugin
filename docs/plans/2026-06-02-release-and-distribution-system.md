# Release & Distribution System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the public `scout-plugin` a reliable release pipeline, a one-command updater that coordinates both distribution surfaces, a `curl|bash` installer, guided auto-update configuration, and a read-only auto-update check (auto-apply deferred).

**Architecture:** Canonical version in `.claude-plugin/plugin.json`, propagated to three derived files by a tested Python module; a release script + CI guard make drift impossible; `/scout-update` refreshes the plugin then upgrades the vault against the new plugin by absolute path; `install.sh` brings up plugin+engine then hands off to interactive `/scout-setup`.

**Tech Stack:** Python 3.11+ (uv venv, typer CLI, pytest, ruff, mypy), Bash, GitHub Actions, Claude Code plugin marketplace.

**Spec:** `docs/specs/2026-06-02-release-and-distribution-system.md`

**The four version-carrying files** (all relative to plugin root):
| File | Location of version |
|---|---|
| `.claude-plugin/plugin.json` | `["version"]` — **CANONICAL** |
| `.claude-plugin/marketplace.json` | `["plugins"][0]["version"]` |
| `engine/pyproject.toml` | `[project]` → `version = "X"` |
| `engine/scout/__init__.py` | `__version__ = "X"` |

**Working directory for all commands:** `/Users/jordanburger/scout-plugin` (the `engine/.venv` is the test venv; run pytest/ruff/mypy from `engine/`).

---

## PHASE 1 — Release Pipeline (own PR)

### Task 1: `versioning.py` module — read / assert-in-sync / bump / propagate

**Files:**
- Create: `engine/scout/scripts/versioning.py`
- Test: `engine/tests/unit/test_versioning.py`

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/unit/test_versioning.py
"""Unit tests for the single-source-of-truth versioning module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.scripts import versioning


def _fake_plugin(tmp_path: Path, version: str = "1.2.3") -> Path:
    """Build a minimal plugin tree with all four version files at `version`."""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "scout", "version": version}, indent=2) + "\n"
    )
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {"name": "scout-plugin", "plugins": [{"name": "scout", "version": version}]},
            indent=2,
        )
        + "\n"
    )
    (tmp_path / "engine").mkdir()
    (tmp_path / "engine" / "pyproject.toml").write_text(
        f'[project]\nname = "scout-engine"\nversion = "{version}"\n'
    )
    (tmp_path / "engine" / "scout").mkdir()
    (tmp_path / "engine" / "scout" / "__init__.py").write_text(
        f'"""scout."""\n\n__version__ = "{version}"\n'
    )
    return tmp_path


def test_read_versions_returns_all_four(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versions = versioning.read_versions(root)
    assert set(versions.values()) == {"1.2.3"}
    assert len(versions) == 4


def test_assert_in_sync_passes_when_equal(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versioning.assert_in_sync(root)  # must not raise


def test_assert_in_sync_raises_on_drift(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    mk = root / ".claude-plugin" / "marketplace.json"
    mk.write_text(mk.read_text().replace("1.2.3", "1.2.2"))
    with pytest.raises(ValueError, match="version drift"):
        versioning.assert_in_sync(root)


def test_bump_levels():
    assert versioning.bump("1.2.3", "patch") == "1.2.4"
    assert versioning.bump("1.2.3", "minor") == "1.3.0"
    assert versioning.bump("1.2.3", "major") == "2.0.0"
    assert versioning.bump("1.2.3", "9.9.9") == "9.9.9"  # explicit passthrough


def test_set_version_writes_all_four_and_preserves_format(tmp_path):
    root = _fake_plugin(tmp_path, "1.2.3")
    versioning.set_version(root, "1.3.0")
    assert set(versioning.read_versions(root).values()) == {"1.3.0"}
    # plugin.json stays valid JSON
    json.loads((root / ".claude-plugin" / "plugin.json").read_text())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd engine && .venv/bin/python -m pytest tests/unit/test_versioning.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scout.scripts.versioning'`

- [ ] **Step 3: Write the implementation**

```python
# engine/scout/scripts/versioning.py
"""Single-source-of-truth versioning for the scout plugin.

The canonical version lives in .claude-plugin/plugin.json. Three derived files
must always carry the SAME version. This module reads, asserts-in-sync, bumps,
and propagates that version, and promotes the CHANGELOG on release.

Writes use targeted regex so existing file formatting (JSON indentation, TOML
layout) is preserved — minimal diffs, no reserialization churn.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# engine/scout/scripts/versioning.py -> parents[3] == plugin root
PLUGIN_ROOT = Path(__file__).resolve().parents[3]

# (label, relative path, compiled regex capturing the version in group 'v')
_TARGETS = [
    ("plugin.json", ".claude-plugin/plugin.json", re.compile(r'("version":\s*")(?P<v>[^"]+)(")')),
    ("marketplace.json", ".claude-plugin/marketplace.json", re.compile(r'("version":\s*")(?P<v>[^"]+)(")')),
    ("pyproject.toml", "engine/pyproject.toml", re.compile(r'(?m)^(version\s*=\s*")(?P<v>[^"]+)(")')),
    ("__init__.py", "engine/scout/__init__.py", re.compile(r'(?m)^(__version__\s*=\s*")(?P<v>[^"]+)(")')),
]


def read_versions(root: Path = PLUGIN_ROOT) -> dict[str, str]:
    out: dict[str, str] = {}
    for label, rel, rx in _TARGETS:
        text = (root / rel).read_text(encoding="utf-8")
        m = rx.search(text)
        if not m:
            raise ValueError(f"no version field found in {rel}")
        out[label] = m.group("v")
    return out


def assert_in_sync(root: Path = PLUGIN_ROOT) -> str:
    versions = read_versions(root)
    distinct = set(versions.values())
    if len(distinct) != 1:
        raise ValueError(f"version drift across manifests: {versions}")
    return distinct.pop()


def bump(current: str, level: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", level):
        return level  # explicit version passthrough
    major, minor, patch = (int(p) for p in current.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"invalid bump level: {level!r} (use major|minor|patch|X.Y.Z)")


def set_version(root: Path = PLUGIN_ROOT, version: str | None = None) -> None:
    assert version is not None
    for _label, rel, rx in _TARGETS:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        new_text, n = rx.subn(rf'\g<1>{version}\g<3>', text, count=1)
        if n != 1:
            raise ValueError(f"failed to rewrite version in {rel}")
        path.write_text(new_text, encoding="utf-8")


def promote_changelog(root: Path = PLUGIN_ROOT, *, version: str, date: str) -> None:
    """Move the `## [Unreleased]` block into a dated `## [version]` section,
    leaving a fresh empty Unreleased on top."""
    path = root / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    marker = "## [Unreleased]"
    if marker not in text:
        raise ValueError("CHANGELOG.md has no '## [Unreleased]' section")
    fresh = f"{marker}\n\n## [{version}] - {date}\n"
    path.write_text(text.replace(marker, fresh, 1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: versioning.py {check|current|bump <level>|set <X.Y.Z>}", file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "check":
        v = assert_in_sync()
        print(v)
        return 0
    if cmd == "current":
        print(read_versions()["plugin.json"])
        return 0
    if cmd in ("bump", "set"):
        if len(argv) < 2:
            print(f"{cmd} requires an argument", file=sys.stderr)
            return 2
        current = read_versions()["plugin.json"]
        new = bump(current, argv[1]) if cmd == "bump" else argv[1]
        set_version(version=new)
        print(new)
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd engine && .venv/bin/python -m pytest tests/unit/test_versioning.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint + typecheck**

Run: `cd engine && .venv/bin/ruff check scout/scripts/versioning.py tests/unit/test_versioning.py && .venv/bin/ruff format scout/scripts/versioning.py tests/unit/test_versioning.py && .venv/bin/mypy scout/scripts/versioning.py`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add engine/scout/scripts/versioning.py engine/tests/unit/test_versioning.py
git commit -m "feat(versioning): single-source-of-truth version module + tests"
```

---

### Task 2: Fix the live drift + version-sync CI guard

**Files:**
- Modify: `.claude-plugin/marketplace.json` (0.3.0 → 0.4.0, via the new tool)
- Create: `engine/tests/unit/test_version_sync.py`
- Modify: `.github/workflows/lint.yml`

- [ ] **Step 1: Write the failing guard test**

```python
# engine/tests/unit/test_version_sync.py
"""CI guard: the four version-carrying manifests must never drift."""

from __future__ import annotations

from scout.scripts import versioning


def test_repo_versions_are_in_sync():
    # Uses the real plugin root (versioning.PLUGIN_ROOT). Fails loudly if any
    # of the four manifests disagree — this is the permanent drift backstop.
    versioning.assert_in_sync()
```

- [ ] **Step 2: Run it — expect FAIL (because marketplace.json is still 0.3.0)**

Run: `cd engine && .venv/bin/python -m pytest tests/unit/test_version_sync.py -q`
Expected: FAIL — `ValueError: version drift across manifests: {... 'marketplace.json': '0.3.0' ...}`

- [ ] **Step 3: Fix the drift using the new tool**

Run: `cd engine && .venv/bin/python -m scout.scripts.versioning set 0.4.0`
Expected output: `0.4.0`
This rewrites `marketplace.json` to 0.4.0 (the other three were already 0.4.0).

- [ ] **Step 4: Run the guard test — expect PASS**

Run: `cd engine && .venv/bin/python -m pytest tests/unit/test_version_sync.py -q`
Expected: PASS

- [ ] **Step 5: Add the fast lint-job guard**

In `.github/workflows/lint.yml`, after the existing `Mypy` step, add:

```yaml
      - name: Version sync guard
        run: .venv/bin/python -m scout.scripts.versioning check
        working-directory: engine
```

(Match the existing steps' `working-directory`/venv convention in that file — read it first and mirror exactly.)

- [ ] **Step 6: Commit**

```bash
git add .claude-plugin/marketplace.json engine/tests/unit/test_version_sync.py .github/workflows/lint.yml
git commit -m "fix: sync marketplace.json to 0.4.0 + add version-drift CI guard"
```

---

### Task 3: `CHANGELOG.md`

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Create the file**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG (Keep a Changelog)"
```

---

### Task 4: `scripts/release.sh`

**Files:**
- Create: `scripts/release.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Cut a release: bump the canonical version, propagate to all four manifests,
# promote the CHANGELOG, run the full check suite, then commit + tag + push.
#
# Usage: scripts/release.sh [patch|minor|major|X.Y.Z]   (default: patch)
set -euo pipefail

LEVEL="${1:-patch}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/engine/.venv/bin/python"
cd "$ROOT"

# --- preconditions ---
[ -x "$PY" ] || { echo "error: engine venv missing — run scripts/install-venv.sh" >&2; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "error: releases are cut from main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "error: working tree not clean" >&2; exit 1; }
git fetch -q origin
[ "$(git rev-list --count origin/main..HEAD)" = "0" ] && [ "$(git rev-list --count HEAD..origin/main)" = "0" ] \
    || { echo "error: local main not in sync with origin/main" >&2; exit 1; }

# --- bump + propagate + changelog ---
"$PY" -m scout.scripts.versioning check >/dev/null   # refuse if already drifted
NEW="$("$PY" -m scout.scripts.versioning bump "$LEVEL")"
TODAY="$(TZ=UTC date '+%Y-%m-%d')"
"$PY" - "$NEW" "$TODAY" <<'EOF'
import sys
from scout.scripts import versioning
versioning.promote_changelog(version=sys.argv[1], date=sys.argv[2])
EOF
echo "Releasing v$NEW"

# --- never tag a red tree ---
( cd engine && .venv/bin/ruff check scout tests && .venv/bin/ruff format --check scout tests \
    && .venv/bin/mypy scout && .venv/bin/python -m pytest -q )

# --- commit + tag + push ---
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json \
        engine/pyproject.toml engine/scout/__init__.py CHANGELOG.md
git commit -m "release: v$NEW"
git tag "v$NEW"
git push origin main
git push origin "v$NEW"
echo "Pushed v$NEW + tag. Release workflow will publish the GitHub Release."
```

- [ ] **Step 2: Make it executable + sanity-check syntax**

Run: `chmod +x scripts/release.sh && bash -n scripts/release.sh && echo "syntax ok"`
Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add scripts/release.sh
git commit -m "feat: scripts/release.sh — one-command release cut"
```

---

### Task 5: `.github/workflows/release.yml`

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Read `.github/workflows/test.yml`** to copy its exact setup (uv install, venv path, Python versions) so the release workflow reuses the same steps.

- [ ] **Step 2: Write the workflow**

```yaml
name: release
on:
  push:
    tags: ["v*"]

jobs:
  test:
    uses: ./.github/workflows/test.yml

  publish:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - name: Extract changelog section for this tag
        id: notes
        run: |
          VERSION="${GITHUB_REF_NAME#v}"
          awk -v v="$VERSION" '
            $0 ~ "^## \\[" v "\\]" {grab=1; next}
            grab && /^## \[/ {exit}
            grab {print}
          ' CHANGELOG.md > RELEASE_NOTES.md
          echo "Extracted notes for $VERSION:"; cat RELEASE_NOTES.md
      - name: Create GitHub Release
        run: gh release create "$GITHUB_REF_NAME" --title "$GITHUB_REF_NAME" --notes-file RELEASE_NOTES.md
        env:
          GH_TOKEN: ${{ github.token }}
```

> Note: `test.yml` must expose `on: workflow_call` for the `uses:` reuse to work. If it doesn't, add `workflow_call: {}` to its `on:` block in this step.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml .github/workflows/test.yml
git commit -m "ci: release workflow — GitHub Release from CHANGELOG on v* tag"
```

- [ ] **Step 4: PHASE 1 PR** — push branch, open PR, confirm CI green (lint incl. the new guard + tests incl. version-sync). Merge.

```bash
git push -u origin feat/release-distribution-system
gh pr create --base main --title "feat: release pipeline (versioning, CI drift guard, release.sh, GitHub Releases)" --body "Implements Phase 1 of docs/specs/2026-06-02-release-and-distribution-system.md"
```

---

## PHASE 2 — Install + Update + Guided (own PR, branched from updated main)

### Task 6: `scoutctl self-update --check` (read-only version check)

**Files:**
- Create: `engine/scout/scripts/self_update.py`
- Modify: `engine/scout/cli.py` (register a `self-update` subcommand)
- Test: `engine/tests/unit/test_self_update.py`

- [ ] **Step 1: Write the failing test**

```python
# engine/tests/unit/test_self_update.py
from __future__ import annotations

from scout.scripts import self_update


def test_compare_detects_update():
    r = self_update.compare(installed="0.4.0", available="0.5.0")
    assert r.update_available is True
    assert r.installed == "0.4.0" and r.available == "0.5.0"


def test_compare_no_update_when_equal():
    assert self_update.compare(installed="0.5.0", available="0.5.0").update_available is False


def test_compare_no_update_when_installed_ahead():
    assert self_update.compare(installed="0.6.0", available="0.5.0").update_available is False


def test_check_uses_injected_fetchers(monkeypatch):
    r = self_update.check(
        installed_fetcher=lambda: "0.4.0",
        available_fetcher=lambda: "0.5.0",
    )
    assert r.update_available is True
```

- [ ] **Step 2: Run — expect FAIL** (`No module named 'scout.scripts.self_update'`)

Run: `cd engine && .venv/bin/python -m pytest tests/unit/test_self_update.py -q`

- [ ] **Step 3: Write the implementation**

```python
# engine/scout/scripts/self_update.py
"""Read-only 'is a newer plugin version available?' check.

Auto-APPLY is intentionally out of scope here (see spec §9, deferred). This
module only reports installed-vs-available so /scout-status and the
/scout-update nudge can use it.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

RAW_MARKETPLACE_URL = (
    "https://raw.githubusercontent.com/jordanrburger/scout-plugin/main/.claude-plugin/marketplace.json"
)


@dataclass(frozen=True)
class UpdateStatus:
    installed: str
    available: str
    update_available: bool


def _semver_tuple(v: str) -> tuple[int, int, int]:
    parts = (v.split("-")[0].split("."))[:3]
    return tuple(int(p) for p in (parts + ["0", "0", "0"])[:3])  # type: ignore[return-value]


def compare(*, installed: str, available: str) -> UpdateStatus:
    return UpdateStatus(
        installed=installed,
        available=available,
        update_available=_semver_tuple(available) > _semver_tuple(installed),
    )


def _installed_version() -> str:
    from scout import __version__

    return __version__


def _available_version() -> str:
    with urllib.request.urlopen(RAW_MARKETPLACE_URL, timeout=10) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    return data["plugins"][0]["version"]


def check(
    *,
    installed_fetcher: Callable[[], str] = _installed_version,
    available_fetcher: Callable[[], str] = _available_version,
) -> UpdateStatus:
    return compare(installed=installed_fetcher(), available=available_fetcher())
```

- [ ] **Step 4: Register the CLI subcommand** in `engine/scout/cli.py`. After the `app = typer.Typer(...)` block and near the other `app.add_typer(...)`/`@app.command` registrations, add:

```python
self_update_app = typer.Typer(help="Plugin self-update (check only in v0.4).")
app.add_typer(self_update_app, name="self-update")


@self_update_app.command("check")
def self_update_check(json_out: bool = typer.Option(False, "--json")) -> None:
    """Report installed-vs-available plugin version (read-only)."""
    import json as _json

    from scout.scripts.self_update import check as _check

    status = _check()
    if json_out:
        typer.echo(_json.dumps(status.__dict__))
    else:
        msg = (
            f"update available: {status.installed} -> {status.available}"
            if status.update_available
            else f"up to date ({status.installed})"
        )
        typer.echo(msg)
```

- [ ] **Step 5: Run tests + lint + mypy**

Run: `cd engine && .venv/bin/python -m pytest tests/unit/test_self_update.py -q && .venv/bin/ruff check scout/scripts/self_update.py scout/cli.py tests/unit/test_self_update.py && .venv/bin/ruff format scout/scripts/self_update.py tests/unit/test_self_update.py && .venv/bin/mypy scout`
Expected: PASS + clean

- [ ] **Step 6: Smoke-test the CLI**

Run: `cd engine && .venv/bin/scoutctl self-update check --json`
Expected: JSON like `{"installed": "0.4.0", "available": "0.4.0", "update_available": false}` (network-dependent; if offline it errors — acceptable for the smoke check).

- [ ] **Step 7: Commit**

```bash
git add engine/scout/scripts/self_update.py engine/scout/cli.py engine/tests/unit/test_self_update.py
git commit -m "feat(self-update): read-only installed-vs-available version check"
```

---

### Task 7: `auto_update` config block

**Files:**
- Modify: `templates/scout-config.yaml.tmpl`
- Modify: `engine/scout/defaults/scout-config.yaml` (packaged defaults read by `config.py`)
- Test: `engine/tests/unit/test_config_auto_update.py`

- [ ] **Step 1: Confirm the packaged-defaults path** — `grep -n "scout-config" engine/scout/config.py` and open `_read_packaged_defaults`. Use whatever file it loads as the "packaged defaults" target below (the plan assumes `engine/scout/defaults/scout-config.yaml`).

- [ ] **Step 2: Write the failing test**

```python
# engine/tests/unit/test_config_auto_update.py
from __future__ import annotations

from scout.config import load_config


def test_auto_update_defaults_present(tmp_path):
    # With no vault config, packaged defaults must supply auto_update (disabled).
    cfg = load_config(data_dir=tmp_path)
    assert cfg["auto_update"]["enabled"] is False
    assert cfg["auto_update"]["channel"] == "stable"
```

- [ ] **Step 3: Run — expect FAIL** (`KeyError: 'auto_update'`)

Run: `cd engine && .venv/bin/python -m pytest tests/unit/test_config_auto_update.py -q`

- [ ] **Step 4: Add the block to packaged defaults** (`engine/scout/defaults/scout-config.yaml`), appended at top level:

```yaml
auto_update:
  enabled: false
  channel: stable
```

- [ ] **Step 5: Add to the vault template** (`templates/scout-config.yaml.tmpl`), appended at the end:

```yaml

# Whether Scout keeps itself up to date. Set during /scout-setup; toggle via
# /scout-update. When enabled, a scheduled run applies sidecar-clean upgrades
# and notifies you on conflict (auto-apply ships in a later version).
auto_update:
  enabled: {{AUTO_UPDATE_ENABLED}}
  channel: stable
```

- [ ] **Step 6: Wire `AUTO_UPDATE_ENABLED` into the template vars.** In `engine/scout/scripts/bootstrap.py` `_template_vars()`, add an entry (default `"false"`; `/scout-setup` overrides it):

```python
        "AUTO_UPDATE_ENABLED": cfg.connector_inputs.get("auto_update_enabled", "false"),
```

- [ ] **Step 7: Run the test + the bootstrap suite + lint/mypy**

Run: `cd engine && .venv/bin/python -m pytest tests/unit/test_config_auto_update.py tests/unit/test_bootstrap_install.py -q && .venv/bin/ruff check scout && .venv/bin/mypy scout`
Expected: PASS + clean

- [ ] **Step 8: Commit**

```bash
git add templates/scout-config.yaml.tmpl engine/scout/defaults/scout-config.yaml engine/scout/scripts/bootstrap.py engine/tests/unit/test_config_auto_update.py
git commit -m "feat(config): auto_update block (disabled by default) + template var"
```

---

### Task 8: Two-surface `/scout-update`

**Files:**
- Modify: `commands/scout-update.md`

- [ ] **Step 1: Add a new Step 0.5 (before the existing vault pre-flight)** that refreshes the plugin and resolves the new plugin root. Insert this section into `commands/scout-update.md` immediately after the "Locating scoutctl" section:

````markdown
## Step 0.5: Refresh the plugin (Surface A) before upgrading the vault (Surface B)

`/scout-update` updates BOTH surfaces. Refresh the plugin first, then upgrade the
vault **against the refreshed plugin** — resolved by absolute path so the stale
`$CLAUDE_PLUGIN_ROOT` of the current session is not used.

Detect the marketplace type and refresh accordingly:

```bash
bash <<'EOF'
set -e
# Directory marketplace (maintainer machine): pull the source checkout.
if [ -d "$HOME/scout-plugin/.git" ]; then
  git -C "$HOME/scout-plugin" pull --ff-only && echo "PULLED_DIRECTORY:$HOME/scout-plugin"
else
  # GitHub marketplace (typical user): refresh + reinstall.
  claude plugin marketplace update scout-plugin || true
  claude plugin install scout@scout-plugin || true
  echo "REFRESHED_MARKETPLACE"
fi
EOF
```

Then resolve the plugin root the upgrade should run from. Prefer the maintainer
checkout if present, else the installed cache path from `claude plugin list`:

```bash
NEW_ROOT="$HOME/scout-plugin"
[ -d "$NEW_ROOT/.git" ] || NEW_ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c "import sys,json;print(next(p['installPath'] for m in json.load(sys.stdin).get('plugins',{}).values() for p in m if 'scout-plugin' in p['installPath']))" 2>/dev/null)"
echo "Upgrading vault against plugin root: $NEW_ROOT"
```

Ensure that root's engine venv exists, then use **its** scoutctl for the rest of
this command (overriding the `$SCOUTCTL` resolved earlier):

```bash
[ -x "$NEW_ROOT/.venv/bin/scoutctl" ] || bash "$NEW_ROOT/scripts/install-venv.sh"
SCOUTCTL="$NEW_ROOT/.venv/bin/scoutctl"
```
````

- [ ] **Step 2: Verify the rest of the command uses `"$SCOUTCTL"`** (it already does per the existing doc) so the override in Step 0.5 flows through to the `bootstrap upgrade` invocation.

- [ ] **Step 3: Add a closing nudge.** At the end of the command (after the doctor/report section), add:

```markdown
## Auto-update nudge

After reporting the result, read `auto_update.enabled` from `~/Scout/scout-config.yaml`.
If it is `false`, tell the user once: "Auto-updates are off — I can turn them on so
Scout keeps itself current (sidecar-clean upgrades only; you'll be pinged on conflict).
Want me to enable it?" If they agree, set `auto_update.enabled: true` in that file.
```

- [ ] **Step 4: Commit**

```bash
git add commands/scout-update.md
git commit -m "feat(scout-update): refresh plugin (Surface A) then upgrade vault against it"
```

---

### Task 9: `/scout-setup` auto-update prompt + `/scout-status` update-available

**Files:**
- Modify: `commands/scout-setup.md`
- Modify: `commands/scout-status.md`

- [ ] **Step 1: Add the auto-update question to `/scout-setup`.** Find the section in `commands/scout-setup.md` where user details/preferences are collected (before the `scoutctl bootstrap install` handoff) and insert:

```markdown
### Auto-update preference

Ask: "Should Scout keep itself up to date automatically? When on, scheduled runs
apply sidecar-clean upgrades and ping you if a change needs manual review. (You can
change this later via /scout-update.)"

Pass the answer into bootstrap install as a connector input:
`--connector-input auto_update_enabled=true` (or `false`). This populates the
`auto_update:` block in the generated `scout-config.yaml`.
```

(Match the exact `scoutctl bootstrap install` invocation flags already used in this file — read it and mirror the `--connector-input` syntax.)

- [ ] **Step 2: Add update-status to `/scout-status`.** In `commands/scout-status.md`, in the section that reports installed state, add:

```markdown
### Update status

Run the read-only version check and show the result:

```bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$HOME/scout-plugin}"
"$PLUGIN_ROOT/.venv/bin/scoutctl" self-update check
```

Also read `auto_update.enabled` from `~/Scout/scout-config.yaml` and display it
("Auto-update: on/off"). If an update is available, tell the user to run
`/scout-update`.
```

- [ ] **Step 3: Commit**

```bash
git add commands/scout-setup.md commands/scout-status.md
git commit -m "feat(guided): /scout-setup auto-update prompt + /scout-status update check"
```

---

### Task 10: `install.sh` (`curl|bash`) + README

**Files:**
- Create: `install.sh` (repo root)
- Modify: `README.md`

- [ ] **Step 1: Write the installer**

```bash
#!/usr/bin/env bash
# Scout one-command installer.
#   curl -fsSL https://raw.githubusercontent.com/jordanrburger/scout-plugin/main/install.sh | bash
# Sets up the PLUGIN + ENGINE. The interactive vault is then created with /scout-setup.
#
# Flags: --check  (verify preconditions only; make no changes)
set -euo pipefail

MARKETPLACE="jordanrburger/scout-plugin"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

have() { command -v "$1" >/dev/null 2>&1; }
fail() { echo "error: $*" >&2; exit 1; }

# --- preconditions ---
have claude || fail "Claude Code CLI not found. Install it first: https://docs.claude.com/claude-code (then re-run this)."
have git    || fail "git is required."
if ! have uv; then
  echo "uv not found — installing (https://docs.astral.sh/uv)…"
  [ "$CHECK_ONLY" = 1 ] || curl -fsSL https://astral.sh/uv/install.sh | sh
fi

if [ "$CHECK_ONLY" = 1 ]; then
  echo "preconditions OK (claude, git present; uv $(have uv && echo present || echo 'will-install'))"
  exit 0
fi

# --- plugin + engine ---
echo "Adding the Scout marketplace…"
claude plugin marketplace add "$MARKETPLACE" 2>/dev/null || claude plugin marketplace update scout-plugin
echo "Installing the Scout plugin…"
claude plugin install scout@scout-plugin

# Resolve the installed plugin root and build its engine venv.
ROOT="$(claude plugin list --json 2>/dev/null \
  | python3 -c "import sys,json;print(next(p['installPath'] for m in json.load(sys.stdin).get('plugins',{}).values() for p in m if 'scout-plugin' in p['installPath']))" 2>/dev/null || true)"
if [ -n "$ROOT" ] && [ -f "$ROOT/scripts/install-venv.sh" ]; then
  echo "Setting up the engine venv…"
  bash "$ROOT/scripts/install-venv.sh"
fi

cat <<'DONE'

✅ Scout plugin + engine installed.

Next step — create your vault (interactive: detects your connectors, collects
your details, sets the schedule):

    Open Claude Code and run:  /scout-setup

DONE
```

- [ ] **Step 2: Make executable + syntax + dry-run check**

Run: `chmod +x install.sh && bash -n install.sh && bash install.sh --check`
Expected: `syntax` ok and a "preconditions OK" line (claude present in this env).

- [ ] **Step 3: Add the one-liner to `README.md`** under a new "## Install" section near the top:

```markdown
## Install

```bash
curl -fsSL https://raw.githubusercontent.com/jordanrburger/scout-plugin/main/install.sh | bash
```

Then open Claude Code and run `/scout-setup` to create your vault.
**Updating later:** run `/scout-update` (refreshes the plugin and upgrades your vault).
```

- [ ] **Step 4: Commit**

```bash
git add install.sh README.md
git commit -m "feat: curl|bash install.sh + README install/update docs"
```

- [ ] **Step 5: PHASE 2 PR** — push, open PR, confirm CI green, merge.

```bash
git push -u origin feat/release-dist-phase-2
gh pr create --base main --title "feat: installer + two-surface updater + guided auto-update config" --body "Implements Phase 2 of docs/specs/2026-06-02-release-and-distribution-system.md"
```

---

## Post-implementation verification

- [ ] Cut a real test release from a clean main: `./scripts/release.sh patch` → confirm the `release.yml` workflow publishes a GitHub Release with CHANGELOG notes, and `scoutctl self-update check` reports the new version as available from a not-yet-updated machine.
- [ ] On a second machine (or fresh `~/Scout` after backing up), run the `curl|bash` one-liner end-to-end, then `/scout-setup`, then `/scout-update`.

## Out of scope (seam ready, deferred)

- Auto-*apply* on schedule: a heartbeat/scheduled-run path that, when `auto_update.enabled`, runs the coordinated upgrade only if sidecar-clean and notifies on conflict. The `self-update check` primitive + config flag from this plan are the seam; wiring the apply is a separate future plan.
