"""How an upgrade treats a vault's edits to the plugin-owned files it rewrites.

``scoutctl bootstrap upgrade`` re-renders every managed file (scripts, hooks,
runners, ``render.py``, ``parser.py``, …) from the plugin. This module decides,
per file, whether the vault edited it since the plugin last wrote it, and keeps
the edit: left alone when the plugin's version didn't change, merged when both
changed and the merge is clean, left running when the merge conflicts, and
parked where it can be found when there is no record to tell an edit from a
template change. Nothing here can make a later upgrade refuse to run.

Layout inside the vault::

    .scout-state/last-rendered/<rel>   the plugin's last render of <rel> — the merge base
    .scout-state/drift/<rel>.plugin    conflict: the plugin's update, not applied
    .scout-state/drift/<rel>.merge     conflict: a conflict-marked merge to start from
    .scout-state/drift/<rel>.vault     first baseline: the vault's copy that was replaced

Everything the doctor and ``scoutctl bootstrap drift`` report is derived from
these files, so there is no second record to drift out of step with them.

Design: docs/superpowers/specs/2026-09-30-upgrade-keeps-vault-edits-design.md
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout.scripts.three_way_merge import MergeResult, MergeUnavailable, three_way_merge

SNAPSHOT_DIR = Path(".scout-state") / "last-rendered"
DRIFT_DIR = Path(".scout-state") / "drift"
# Where parser.py's base lived under the sidecar merge policy it had before
# last-rendered/ existed; carried over on the first upgrade that finds it.
LEGACY_SNAPSHOT_DIR = Path(".scout-state") / "last-assembled"

PLUGIN_SUFFIX = ".plugin"
MERGE_SUFFIX = ".merge"
VAULT_SUFFIX = ".vault"
_VAULT_COPY_RE = re.compile(r"\.vault(?:-\d+)?$")

MERGE_LABELS = ("plugin", "last render", "vault")

# Outcomes the user is told about. The others (written, unchanged, updated)
# are the plugin doing its job on a file the vault never touched.
REPORTED = ("kept", "merged", "conflict", "replaced")

RENDER_HISTORY = Path(__file__).resolve().parent.parent / "defaults" / "render-history.json"

# Managed files the vault grows on purpose (bootstrap._CAT_MERGE_FILES, which
# the read-only doctor can't import): their edits are vault content, so they
# are never offered as a plugin patch.
VAULT_DEVELOPED_FILES = frozenset({"knowledge-base/ontology/parser.py"})

Merge = Callable[..., MergeResult]


# ---------------------------------------------------------------- decide ----


@dataclass(frozen=True)
class Decision:
    """What to do with one managed file. ``None`` fields mean "leave as is"."""

    outcome: str
    live: str | None = None  # write this to the vault's file
    snapshot: str | None = None  # record this as the base for the next upgrade
    merge_draft: str | None = None  # conflict: the conflict-marked merge, when git produced one
    reason: str | None = None  # conflict: why there is no merge, when it isn't an overlap


def decide(
    *,
    new: str,
    live: str | None,
    base: str | None,
    known_render: bool = False,
    vault_developed: bool = False,
    merge: Merge = three_way_merge,
) -> Decision:
    """The upgrade's decision for one managed file.

    ``new`` is this plugin version's render, ``live`` the vault's file (None if
    absent), ``base`` the last render the plugin recorded (None if no record).
    ``known_render`` says ``live`` is an unedited render of some plugin version
    (see ``matches``); it only matters when there is no base. A
    ``vault_developed`` file is one the vault is known to extend (parser.py):
    with no base to merge against, its version is the one kept running.
    """
    if live is None:
        return Decision("written", live=new, snapshot=new)
    if live == new:
        return Decision("unchanged", snapshot=new)
    if base is None:
        if known_render:
            return Decision("updated", live=new, snapshot=new)
        if vault_developed:
            return Decision("conflict", reason="no record of the last render to merge against")
        return Decision("replaced", live=new, snapshot=new)
    if live == base:
        return Decision("updated", live=new, snapshot=new)
    if new == base:
        return Decision("kept", snapshot=new)
    try:
        result = merge(base=base, ours=new, theirs=live, labels=MERGE_LABELS)
    except MergeUnavailable as e:
        # No git, or git failed: the vault's working file is the safe choice.
        return Decision("conflict", reason=f"could not merge — {e}")
    if result.conflicts:
        return Decision("conflict", merge_draft=result.content)
    if result.content == new:
        # The vault's change was already part of the plugin's: nothing of its own is left.
        return Decision("updated", live=new, snapshot=new)
    return Decision("merged", live=result.content, snapshot=new)


# ------------------------------------------------------------ signatures ----

# The token phase_assembly.render_template substitutes.
_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Signature:
    """Recognises an unedited render of one template version, whatever values
    its template variables were rendered with.

    ``sha256`` hashes the raw template text; ``var_lines`` holds the raw text of
    each line carrying a ``{{VAR}}`` token. A verbatim (unrendered) file has no
    var lines, so its signature is a plain hash of the file.
    """

    sha256: str
    lines: int
    var_lines: tuple[tuple[int, str], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "lines": self.lines,
            "var_lines": {str(i): raw for i, raw in self.var_lines},
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Signature:
        var_lines = tuple(sorted((int(i), raw) for i, raw in (data.get("var_lines") or {}).items()))
        return cls(sha256=data["sha256"], lines=int(data["lines"]), var_lines=var_lines)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def signature(text: str, *, rendered: bool) -> Signature:
    """The signature of a template (``rendered``) or of a verbatim file."""
    lines = text.split("\n")
    var_lines = tuple((i, line) for i, line in enumerate(lines) if _VAR_RE.search(line)) if rendered else ()
    return Signature(sha256=_sha256(text), lines=len(lines), var_lines=var_lines)


def _line_pattern(raw: str, pinned: Mapping[str, str]) -> tuple[re.Pattern[str], list[str]]:
    """``raw`` as a regex: a pinned ``{{VAR}}`` must be its value, any other
    token is a capture group. Returns the pattern and the captured names in order."""
    parts = _VAR_RE.split(raw)  # literal, name, literal, name, …, literal
    pattern, free = re.escape(parts[0]), []
    for name, literal in zip(parts[1::2], parts[2::2], strict=True):
        if name in pinned:
            pattern += re.escape(pinned[name])
        else:
            pattern += "(.*)"  # values are single-line
            free.append(name)
        pattern += re.escape(literal)
    return re.compile(pattern), free


def matches(text: str, sig: Signature, *, pinned: Mapping[str, str] | None = None) -> bool:
    """True iff ``text`` is the template behind ``sig`` rendered with some values.

    A variable in ``pinned`` must have exactly that value; any other may have
    any value, but the same one everywhere it appears.
    """
    lines = text.split("\n")
    if len(lines) != sig.lines:
        return False
    seen: dict[str, str] = {}
    for i, raw in sig.var_lines:
        pattern, free = _line_pattern(raw, pinned or {})
        m = pattern.fullmatch(lines[i])
        if m is None:
            return False
        for name, value in zip(free, m.groups(), strict=True):
            if seen.setdefault(name, value) != value:
                return False
        lines[i] = raw
    return _sha256("\n".join(lines)) == sig.sha256


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def load_render_history(path: Path | None = None) -> dict[str, list[Signature]]:
    """Signatures of the managed files older releases shipped, by vault path.

    Releases before last-rendered/ existed recorded no base, so a vault's first
    upgrade uses these to recognise a file it never edited. An unreadable file
    degrades to no history, and a malformed entry to one fewer signature: those
    files are then parked and reported, never overwritten silently. Either is
    warned about on stderr, since it turns unedited files into false reports.
    """
    source = path or RENDER_HISTORY
    try:
        files = json.loads(source.read_text(encoding="utf-8")).get("files")
        if not isinstance(files, dict):
            raise ValueError("no 'files' mapping")
    except (OSError, ValueError, AttributeError) as e:
        _warn(f"render history unreadable ({source}: {e}); unedited files from older releases will be parked")
        return {}
    history: dict[str, list[Signature]] = {}
    for rel, entries in files.items():
        for entry in entries if isinstance(entries, list) else [entries]:
            try:
                history.setdefault(rel, []).append(Signature.from_json(entry))
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                _warn(f"render history: skipping a malformed signature for {rel} ({type(e).__name__}: {e})")
    return history


# ------------------------------------------------------------- reconcile ----


@dataclass(frozen=True)
class VaultEdit:
    """A vault edit an upgrade found, and what it did with it."""

    path: str  # vault-relative
    outcome: str  # one of REPORTED, or "error" when the file could not be processed
    parked: tuple[str, ...] = ()  # vault-relative paths of what was set aside
    detail: str = ""  # why, when the outcome needs one

    def describe(self) -> str:
        if self.outcome == "error":
            return f"left untouched, could not process it: {self.detail}"
        if self.outcome == "kept":
            return "kept as is (the plugin's version did not change)"
        if self.outcome == "merged":
            return "merged with the plugin's update (review: scoutctl bootstrap drift --diff)"
        if self.outcome == "conflict":
            where = self.parked[0] if self.parked else str(DRIFT_DIR / f"{self.path}{PLUGIN_SUFFIX}")
            why = self.detail or "overlaps the plugin's update"
            fix = (
                "Fix git and upgrade again, or merge by hand"
                if self.detail.startswith("could not merge")
                else "Merge by hand"
            )
            return (
                f"{why}; your version is still in place and the update is parked at {where}. "
                f"{fix}, then: scoutctl bootstrap drift --resolve {self.path}"
            )
        where = self.parked[0] if self.parked else str(DRIFT_DIR)
        return (
            f"no record of the last render and no release matches this copy; the plugin's version is "
            f"installed and yours is parked at {where}"
        )


def snapshot_path(vault: Path, rel: str) -> Path:
    return vault / SNAPSHOT_DIR / rel


def _drift_path(vault: Path, rel: str, suffix: str) -> Path:
    return vault / DRIFT_DIR / f"{rel}{suffix}"


def _rel(vault: Path, path: Path) -> str:
    return path.relative_to(vault).as_posix()


# Vault files are read and written byte-transparently: a stray non-UTF-8 byte
# in a hand-edited script round-trips unchanged instead of raising
# UnicodeDecodeError, which would fail every upgrade until someone found it.
_ERRORS = "surrogateescape"


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8", errors=_ERRORS) if path.is_file() else None


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", errors=_ERRORS)
    tmp.replace(path)


def printable(text: str) -> str:
    """``text`` safe to print: undecodable bytes shown as U+FFFD."""
    return text.encode("utf-8", _ERRORS).decode("utf-8", "replace")


def _remove(vault: Path, path: Path) -> None:
    """Delete a parked file, then any directories under drift/ it leaves empty."""
    path.unlink(missing_ok=True)
    root = vault / DRIFT_DIR
    parent = path.parent
    while parent.is_dir() and parent.is_relative_to(root) and not any(parent.iterdir()):
        parent.rmdir()
        if parent == root:
            break
        parent = parent.parent


def _park(vault: Path, rel: str, content: str) -> Path:
    """Set the vault's copy aside, never overwriting an earlier parked copy (#62)."""
    n = 0
    while True:
        target = _drift_path(vault, rel, VAULT_SUFFIX if n == 0 else f"{VAULT_SUFFIX}-{n}")
        existing = _read(target)
        if existing is None:
            _write(target, content)
            return target
        if existing == content:
            return target
        n += 1


def park_vault_copy(vault: Path, rel: str, content: str) -> Path:
    """Park a vault copy an upgrade is about to replace, for a file outside the
    managed set (an assembled brain file). ``scan`` reports it as *replaced*
    and ``resolve`` dismisses it, like any other parked ``.vault`` copy."""
    return _park(vault, rel, content)


def reconcile(
    vault: Path,
    rel: str,
    new: str,
    *,
    signatures: Iterable[Signature] = (),
    pinned: Mapping[str, str] | None = None,
    vault_developed: bool = False,
    merge: Merge = three_way_merge,
) -> VaultEdit | None:
    """Bring one managed file to the plugin's ``new`` render without losing a vault edit.

    Returns what happened when it is worth reporting (one of ``REPORTED``),
    else None. ``signatures`` recognise unedited renders of older plugin
    versions; they are consulted only when the vault has no recorded base, with
    the template variables in ``pinned`` held to this upgrade's values.
    """
    live_path, snap = vault / rel, snapshot_path(vault, rel)
    live, base = _read(live_path), _read(snap)
    legacy = vault / LEGACY_SNAPSHOT_DIR / rel
    carried = base is None and legacy.is_file()
    if carried:
        base = _read(legacy)
    known = base is None and live is not None and any(matches(live, s, pinned=pinned) for s in signatures)
    d = decide(new=new, live=live, base=base, known_render=known, vault_developed=vault_developed, merge=merge)

    parked: tuple[str, ...] = ()
    if d.outcome == "replaced" and live is not None:
        parked = (_rel(vault, _park(vault, rel, live)),)
    if d.live is not None:
        _write(live_path, d.live)
    snapshot = d.snapshot if d.snapshot is not None else (base if carried else None)
    if snapshot is not None:
        _write(snap, snapshot)
    if carried:
        legacy.unlink()

    plugin, draft = _drift_path(vault, rel, PLUGIN_SUFFIX), _drift_path(vault, rel, MERGE_SUFFIX)
    if d.outcome == "conflict":
        _write(plugin, new)
        parked = (_rel(vault, plugin),)
        if d.merge_draft is not None:
            _write(draft, d.merge_draft)
            parked += (_rel(vault, draft),)
        else:
            _remove(vault, draft)
    else:
        # Draft first: a crash in between leaves the .plugin, which still reads
        # as a conflict and is settled again next time, never a hidden draft.
        _remove(vault, draft)
        _remove(vault, plugin)
    return VaultEdit(rel, d.outcome, parked, detail=d.reason or "") if d.outcome in REPORTED else None


def prune_snapshots(vault: Path, managed: Iterable[str]) -> None:
    """Drop the base of any file the plugin no longer manages."""
    root = vault / SNAPSHOT_DIR
    if not root.is_dir():
        return
    keep = set(managed)
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and path.relative_to(root).as_posix() not in keep:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


# ------------------------------------------------------------ scan / resolve ----


@dataclass(frozen=True)
class DriftEntry:
    """A managed file that differs from what the plugin last wrote."""

    path: str
    status: str  # "edited" | "conflict" | "replaced" | "missing"
    parked: tuple[str, ...] = ()


def _parked_files(vault: Path) -> list[Path]:
    root = vault / DRIFT_DIR
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []


def _vault_copies(vault: Path, rel: str) -> list[Path]:
    prefix = DRIFT_DIR / rel
    return [p for p in _parked_files(vault) if _VAULT_COPY_RE.sub("", _rel(vault, p)) == prefix.as_posix()]


def scan(vault: Path) -> list[DriftEntry]:
    """Every managed file that differs from its last render, plus what is parked.

    Read-only. Needs nothing but the vault, so the doctor can call it.
    """
    drift_root = vault / DRIFT_DIR
    conflicts: dict[str, list[str]] = {}
    copies: dict[str, list[str]] = {}
    for path in _parked_files(vault):
        name = path.relative_to(drift_root).as_posix()
        if name.endswith(PLUGIN_SUFFIX):
            conflicts.setdefault(name.removesuffix(PLUGIN_SUFFIX), []).insert(0, _rel(vault, path))
        elif name.endswith(MERGE_SUFFIX):
            conflicts.setdefault(name.removesuffix(MERGE_SUFFIX), []).append(_rel(vault, path))
        elif _VAULT_COPY_RE.search(name):
            copies.setdefault(_VAULT_COPY_RE.sub("", name), []).append(_rel(vault, path))

    # A .merge draft without its .plugin is a leftover, not a conflict.
    conflicts = {rel: p for rel, p in conflicts.items() if p[0].endswith(PLUGIN_SUFFIX)}
    entries: list[DriftEntry] = []
    snap_root = vault / SNAPSHOT_DIR
    if snap_root.is_dir():
        for snap in sorted(p for p in snap_root.rglob("*") if p.is_file()):
            rel = snap.relative_to(snap_root).as_posix()
            if rel in conflicts:
                continue
            live = _read(vault / rel)
            if live is None:
                entries.append(DriftEntry(rel, "missing"))
            elif live != _read(snap):
                entries.append(DriftEntry(rel, "edited"))
    entries += [DriftEntry(rel, "conflict", tuple(p)) for rel, p in conflicts.items()]
    entries += [DriftEntry(rel, "replaced", tuple(p)) for rel, p in copies.items()]
    return sorted(entries, key=lambda e: (e.path, e.status))


def _has_conflict_markers(text: str) -> bool:
    return any(line.startswith(("<<<<<<<", ">>>>>>>")) for line in text.splitlines())


def _lines(text: str) -> list[str]:
    """Lines compared with trailing whitespace stripped, as a hand merge may leave it."""
    return [ln.rstrip() for ln in text.split("\n")]


def _runs(lines: list[str], block: list[str]) -> int:
    """How many times ``block`` occurs in ``lines`` as a contiguous run of whole lines."""
    n = len(block)
    return sum(1 for i in range(len(lines) - n + 1) if lines[i : i + n] == block)


def _update_changes_missing(base: str, update: str, live: str) -> list[str]:
    """The changes ``update`` made to ``base`` that ``live`` doesn't have, one
    line describing each (empty: the update looks merged).

    Each block of lines the update adds must be in ``live`` as a run of whole
    lines, at least as often as it is in the update, so a short line (``fi``)
    the file already had elsewhere doesn't count. Each block it deletes must
    occur in ``live`` no more often than in the update. Blank-only blocks are
    ignored. A line-level check, not a proof: a merge that rewrote the update's
    lines is refused, and --drop-update records it anyway.
    """
    b_lines, u_lines, l_lines = _lines(base), _lines(update), _lines(live)
    matcher = difflib.SequenceMatcher(a=b_lines, b=u_lines, autojunk=False)
    missing: list[str] = []

    def first(block: list[str]) -> str:
        return next(ln for ln in block if ln.strip()).strip()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        added, deleted = u_lines[j1:j2], b_lines[i1:i2]
        if tag in ("insert", "replace") and any(ln.strip() for ln in added):
            if _runs(l_lines, added) < _runs(u_lines, added):
                missing.append(f"adds {first(added)!r}")
        if tag in ("delete", "replace") and any(ln.strip() for ln in deleted):
            if _runs(l_lines, deleted) > _runs(u_lines, deleted):
                missing.append(f"removes {first(deleted)!r}")
    return missing


def resolve(vault: Path, rel: str, *, drop_update: bool = False) -> list[str]:
    """Settle what an upgrade parked for ``rel``; returns what was done.

    A conflict: the vault's file is taken as merged by hand, so the parked
    plugin version becomes the base. The next upgrade then sees only the
    vault's own edit on top of the plugin's, which it keeps. That would drop
    the plugin's update for good if it was never merged in, so unless
    ``drop_update`` says that is the intent, every block of lines the update
    adds must be in the file and every block it removes gone from it (see
    ``_update_changes_missing``). With no recorded base there is nothing to
    check that against, so only ``drop_update`` settles it. Parked ``.vault``
    copies are deleted. Raises ``ValueError`` when nothing is parked, the file
    is missing, still holds conflict markers, or lacks the update.
    """
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError(f"{rel}: give the file's vault-relative path, e.g. scripts/heartbeat.sh")
    plugin = _drift_path(vault, rel, PLUGIN_SUFFIX)
    copies = _vault_copies(vault, rel)
    parked_update = _read(plugin)
    if parked_update is None and not copies:
        raise ValueError(f"nothing to resolve for {rel}: no parked update or copy under {DRIFT_DIR}/")
    done: list[str] = []
    if parked_update is not None:
        live = _read(vault / rel)
        if live is None:
            raise ValueError(f"{rel} is missing; put the merged file back first")
        if _has_conflict_markers(live):
            raise ValueError(f"{rel} still has conflict markers; finish the merge first")
        base = _read(snapshot_path(vault, rel))
        if base is None and not drop_update:
            raise ValueError(
                f"{rel} has no record of the plugin's last render, so there is no telling whether the parked "
                f"update ({_rel(vault, plugin)}) is merged into it. Merge it in, or take it with "
                f"`cp {_rel(vault, plugin)} {rel}`; then, to record your file as it is, add --drop-update"
            )
        missing = [] if drop_update or base is None else _update_changes_missing(base, parked_update, live)
        if missing:
            raise ValueError(
                f"{rel} doesn't have the parked update yet ({len(missing)} change(s) missing, e.g. the update "
                f"{missing[0]}). Merge it in (draft: {_rel(vault, _drift_path(vault, rel, MERGE_SUFFIX))}), or "
                f"take it with `cp {_rel(vault, plugin)} {rel}`; to keep your file as it is and drop what it "
                f"lacks of the update, add --drop-update"
            )
        _write(snapshot_path(vault, rel), parked_update)
        _remove(vault, _drift_path(vault, rel, MERGE_SUFFIX))
        _remove(vault, plugin)
        done.append(f"{rel}: the plugin's update is recorded as merged; your file is now your edit on top of it")
    for copy in copies:
        _remove(vault, copy)
        done.append(f"{rel}: dismissed {_rel(vault, copy)}")
    return done


# ---------------------------------------------------------------- report ----


@dataclass(frozen=True)
class DriftRow:
    """One line of ``scoutctl bootstrap drift``: a scan entry with its diff."""

    path: str
    status: str
    parked: tuple[str, ...]
    stale: bool  # the plugin changed this file since the last upgrade
    added: int
    removed: int
    live: str
    diff: str  # unified: the plugin's (or parked) version → the vault's

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "parked": list(self.parked),
            "stale": self.stale,
            "added": self.added,
            "removed": self.removed,
        }

    def describe(self) -> str:
        counts = f"(+{self.added} \u2212{self.removed})"
        if self.status == "edited":
            detail = f"{counts} — kept across upgrades"
        elif self.status == "conflict":
            detail = (
                f"{counts} vs the parked update {self.parked[0]} — merge by hand, then "
                f"`scoutctl bootstrap drift --resolve {self.path}`"
            )
        elif self.status == "replaced":
            detail = (
                f"your previous copy: {', '.join(self.parked)} — compare with --diff, "
                f"dismiss with `--resolve {self.path}`"
            )
        else:
            detail = "— missing; the next upgrade writes it again"
        if self.stale:
            detail += " [the plugin changed this file since the last upgrade — run `scoutctl bootstrap upgrade`]"
        return detail


def report(vault: Path, renders: dict[str, str]) -> list[DriftRow]:
    """``scan`` with diffs, given this plugin version's render of each managed file.

    An edited file is diffed against the plugin's current render, a conflict
    against the parked update, and a replaced file's parked copy against the
    plugin's version now in place.
    """

    def read(rel: str) -> str:
        return _read(vault / rel) or ""

    rows: list[DriftRow] = []
    for entry in scan(vault):
        live = read(entry.path)
        render = renders.get(entry.path)
        base = _read(snapshot_path(vault, entry.path))
        stale = render is not None and base is not None and base != render
        if entry.status == "conflict":
            # The base stays old on purpose after a conflict; stale means a
            # newer update than the parked one is waiting.
            stale = render is not None and render != read(entry.parked[0])
            before, after, labels = read(entry.parked[0]), live, (entry.parked[0], f"vault/{entry.path}")
        elif entry.status == "replaced":
            before, after, labels = live, read(entry.parked[-1]), (f"plugin/{entry.path}", entry.parked[-1])
        else:
            before = render if render is not None else (base or "")
            after, labels = live, (f"plugin/{entry.path}", f"vault/{entry.path}")
        lines = list(
            difflib.unified_diff(
                before.splitlines(keepends=True), after.splitlines(keepends=True), labels[0], labels[1]
            )
        )
        body = [ln for ln in lines if not ln.startswith(("---", "+++"))]
        rows.append(
            DriftRow(
                path=entry.path,
                status=entry.status,
                parked=entry.parked,
                stale=stale,
                added=sum(1 for ln in body if ln.startswith("+")),
                removed=sum(1 for ln in body if ln.startswith("-")),
                live=live,
                diff=printable("".join(ln if ln.endswith("\n") else ln + "\n" for ln in lines)),
            )
        )
    return rows
