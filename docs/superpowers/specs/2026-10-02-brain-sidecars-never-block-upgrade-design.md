# Brain-file sidecars never block an upgrade

**Status:** implemented on `fix/brain-sidecars-never-block-upgrade`
**Date:** 2026-10-02
**Related:** #263 (`fix/upgrade-never-loses-vault-edits`, the non-blocking
policy for plugin-owned scripts, open), #244 (`bootstrap auto`, open)

## Problem

`scoutctl bootstrap upgrade` 3-way merges the assembled brain files (`SKILL.md`,
`DREAMING.md`, `RESEARCH.md`) against `.scout-state/last-assembled/`
(`_stage_cat4_upgrade`). Two rules combine to stall upgrades:

1. **A pending sidecar blocks everything.** A conflict writes
   `<KIND>.md.proposed-merge`, and `_refuse_pending_sidecars` makes the *next*
   upgrade raise before any stage runs. The scripts, runners, jobs and version
   stamp stay on the old plugin too, not only the brain file. An unattended
   auto-update can never run again until a person resolves the sidecar, and
   #244's `bootstrap auto` refuses the same way. After the 0.11.0 upgrade a
   real vault had to move its sidecars aside by hand.
2. **Every phase change sidecars an unedited vault.** When the vault never
   edited a file (`base == theirs`) but the plugin's assembly changed
   (`ours != theirs`), the upgrade writes `ours` to a sidecar instead of
   applying it. A routine release therefore produces a sidecar, and by rule 1
   a blocked upgrade, on a vault that did nothing.

Rule 2 exists because of the **M3 incident**: `migrate-legacy` seeds the
snapshot by copying the live file. For a legacy vault, `base == theirs` then
means "no edit history", not "no edits", and the original fast-forward wiped a
hand-grown brain (85 KB of `SKILL.md`, plus `DREAMING.md` and `RESEARCH.md`).

## Goals

1. A pending brain-file sidecar never aborts the upgrade. Only that file's
   merge is skipped, the sidecar is left exactly as the user left it, and every
   other stage runs. The skip is reported in `UpgradeResult`, the CLI,
   `bootstrap doctor` and `/scout-update`.
2. A plugin change to a brain file the vault never edited is applied, with no
   sidecar.
3. A legacy-migrated (M3-shaped) vault never has its live brain overwritten or
   merged away. This holds both when it has been edited since migration and
   when it hasn't.
4. `scoutctl phases backport` keeps working. It diffs the snapshot against the
   live file, so the snapshot must still be the assembly the live file descends
   from.

Out of scope:
- `knowledge-base/ontology/parser.py` (`_CAT_MERGE_FILES`). Its sidecar still
  blocks, as before. The drift branch moves it to the non-blocking
  `.scout-state/drift/` policy.
- Keeping a vault's own brain for good (pinning). Deleting a sidecar means
  "decide later": the next upgrade proposes the same change again, as before.
- Restoring a deleted brain file. A missing live file is still treated as
  `theirs = ours` (the snapshot advances, nothing is written), as before.

## Rule 1: a pending sidecar skips only its own file

Options weighed:

- **Skip that file's merge; touch nothing for it** (chosen). Live, snapshot,
  sidecar and provenance stay as they are, and every other stage runs. Nothing
  the user is halfway through resolving can be overwritten. The sidecar goes
  stale as later plugin versions land, but that costs little: once the user
  resolves it, the next upgrade merges against the plugin of that day.
- **Rewrite the sidecar on every upgrade.** This would clobber a resolution in
  progress. Rejected.
- **Rewrite the sidecar only while it is byte-identical to what the engine
  wrote.** This keeps the proposal current but needs another piece of state.
  Deferred: it's an improvement on top of the chosen option, not a fix it needs.
- **Move brain conflicts into `.scout-state/drift/`** like the scripts. The
  `.proposed-merge` sidecar is the documented brain-file workflow (README,
  `/scout-update`, `phases backport` spec), so changing it is a separate
  decision. Rejected here.

`_refuse_pending_sidecars` now covers only `_CAT_MERGE_FILES`. Two helpers make
the split explicit: `brain_merge.pending_brain_sidecars(vault)` and
`bootstrap.blocking_sidecars(vault)`. `bootstrap auto` (#244) refuses only on
`blocking_sidecars`, and its `result_dict` (the JSON Scout.app decodes) carries
`skipped`.

## Rule 2: fast-forward only over the plugin's own assembly

The question `base == theirs` was really asking is whether the live file is
exactly something the plugin wrote. If it is, replacing it loses nothing. If
the snapshot was seeded from live, or changed outside the engine, nobody knows
what the live file contains.

Options weighed:

- **Always sidecar** (today). Stalls every routine release. Rejected.
- **Always fast-forward** (before M3). Wipes legacy vaults. Rejected.
- **Fingerprint only.** Every bootstrap assembly starts with
  ``# KIND\n\n**BASE_DIR:** ` `` (since the pipeline's first commit), while
  Plan-5-era brain files start with YAML frontmatter (`---`). This needs no
  state, but it can't see a snapshot that was overwritten by hand. Copying live
  over the snapshot to silence a sidecar is a plausible workaround, and
  fast-forwarding after it would wipe the vault's edits. Not enough on its own.
- **Fast-forward and park the replaced live copy.** This doesn't lose bytes,
  but a legacy vault's working brain is swapped for one with a different
  structure, which is the M3 outcome. Rejected.
- **Chosen: a provenance record, with the fingerprint as a one-time fallback.**

### Provenance

Two pieces of state live next to the snapshots under
`.scout-state/last-assembled/` (gitignored like them). The first is
`provenance.json`:

```json
{
  "version": 1,
  "files": {
    "SKILL.md": {"snapshot": "assembled", "sha256": "<sha256 of the snapshot as the engine wrote it>"}
  }
}
```

The second is `proposed/<KIND>.md`, the assembly a pending sidecar was built
from.

- `install`, and every upgrade outcome that advances the snapshot, records
  `"snapshot": "assembled"` with the snapshot's hash and removes
  `proposed/<KIND>.md`. Provenance is written after each file, not once at the
  end, so a failure on a later file can't leave an earlier, already-advanced
  snapshot without its record.
- `migrate-legacy` records `"snapshot": "seeded"`.
- Writing a sidecar writes `proposed/<KIND>.md` = `ours`. The snapshot and its
  record stay as they are.

The base counts as a **plugin assembly** when:
- the entry records the snapshot: it is `assembled` and its hash matches the
  snapshot file. A `seeded` snapshot, one changed outside the engine, or a
  `snapshot` value that can't be understood does not count. An unknown value
  fails closed rather than falling back to the weaker fingerprint.
- the entry has no snapshot record (a vault last upgraded before this change):
  the snapshot starts with the assembly fingerprint. This fallback is used until
  the first upgrade that advances the snapshot.
- in any case, the snapshot file exists.

The fingerprint fallback can't see a snapshot someone copied over by hand. So
whenever it is the only thing vouching for the base, and the upgrade is about
to change the live file (row 5 or a clean row 6), the replaced file is kept as
`.scout-state/drift/<KIND>.md.vault`, the same place `vault_drift` parks any vault copy an upgrade replaces. It is reported in `UpgradeResult.backups` and as a doctor note, and `scoutctl bootstrap drift --resolve <KIND>.md` dismisses it. This happens at
most once per file.

A hard kill between writing a snapshot and writing `provenance.json` fails the
hash check next time, so the file is proposed rather than overwritten, and
`bootstrap resolve` gets it back on track. A `provenance.json` that can't be
parsed is renamed to `provenance.json.corrupt`, with a warning, so the next
write can't destroy what it held. Every brain file then goes back to the
fingerprint fallback, with its backup, until its next snapshot write.

### The decision per brain file

*ours* = fresh assembly, *theirs* = live file (`ours` if missing),
*base* = snapshot.

| # | Case | Action | Snapshot | Reported |
|---|---|---|---|---|
| 1 | sidecar pending | **skip**: touch nothing | unchanged | `skipped` |
| 2 | `ours == theirs` | nothing to write | → ours | — |
| 3 | live is a proposal adopted verbatim (`theirs == proposed/<KIND>.md`) | **fast-forward**: live → ours | → ours | — |
| 4 | base isn't a plugin assembly | **propose**: sidecar = ours; live untouched | unchanged | `conflicts` |
| 5 | `base == theirs` | **fast-forward**: live → ours | → ours | — |
| 6 | both changed, merge clean | live → merge | → ours | — |
| 7 | both changed, merge conflicts | sidecar = conflict-marked merge; live untouched | unchanged | `conflicts` |

Row 4 replaces the merge as well as the fast-forward. With a seeded base, the
seed is not a common ancestor of the plugin's assembly, so a "clean" merge would
apply "delete the legacy brain, add the plugin's" to the vault. That is the M3
loss along another path. So the vault gets the plugin's version as a proposal
and decides, as the brain-structure ADR asks: Phase 2 adoption is deliberate.

Row 3 lets a plain `mv SKILL.md.proposed-merge SKILL.md` converge. The vault
then holds a file byte-identical to an assembly the plugin produced, so the next
upgrade can fast-forward it even though the snapshot is still the seed. It only
covers a file nobody touched after the `mv`. Dreaming runs edit brain files when
they apply approved proposals, and an edited adoption can't be told apart from
any other edit. For that case there is `bootstrap resolve`.

## Resolving: `scoutctl bootstrap resolve <KIND>.md`

Once the live file is the version the user wants, `resolve` makes
`proposed/<KIND>.md` the snapshot (recorded `assembled`) and removes the sidecar.
That works whether the user moved the sidecar into place, merged parts of it by
hand, or kept their own version. The next upgrade then merges only *later*
plugin changes into the resolution. This makes three things converge:

- an adopted proposal that was edited before the next upgrade: the Phase-2 path
  for an M3-shaped vault;
- a conflict resolved by hand: without `resolve`, the next merge still diffs
  against the pre-conflict base and conflicts on the same lines again;
- a file stuck proposing after the hard-kill case above.

`resolve` refuses while the live file still holds `<<<<<<<` / `>>>>>>>` lines.
A bare `=======` doesn't count, because it is also a Markdown heading underline.
It also refuses when there's no sidecar and no recorded proposal. A sidecar
left by an older engine has no recorded proposal: `resolve` removes it and
leaves the base alone, so the next upgrade merges the file again. `resolve`
takes the same session lock as `upgrade`.

Deleting a sidecar without `resolve` is "decide later". The doctor flags a
recorded proposal whose sidecar is gone while the live file differs from it,
because the next upgrade will propose or merge from the old base again.

The snapshot still advances only when the live file has absorbed `ours` (rows
2, 3, 5, 6). `phases backport` therefore still sees exactly the vault's edits:
none after a fast-forward, the edits on top of the last absorbed assembly
otherwise, and the edits since migration for a seeded vault.

## Surfacing

- **`UpgradeResult.skipped`**: the sidecar names whose file was skipped.
  `conflicts` still lists the sidecars this upgrade wrote.
- **CLI** (`bootstrap upgrade`): ``skipped (sidecar pending): SKILL.md.proposed-merge — SKILL.md left as is until it is resolved``
  next to the existing `conflict (sidecar):` lines.
- **Doctor**: a pending brain sidecar is still a warning, so the doctor shows
  yellow. A vault whose brain isn't receiving plugin changes needs attention.
  The message now says upgrades skip the file and points to
  `bootstrap resolve`. A sidecar removed without `resolve` is also a warning.
- **`/scout-update`**: step 0 no longer refuses on brain sidecars. It names
  them and continues, and still refuses on the blocking `parser.py` sidecar.
  Step 3 explains conflict, proposal, skipped and backup rows, and `resolve`.

## Testing

- `test_brain_merge.py`: the decision table as a pure function, plus
  provenance read/write and the fingerprint fallback.
- `test_upgrade_brain_sidecars.py`: whole upgrades against `tmp_path` vaults
  with a `tmp_path` plugin root, so the phases can change between versions:
  - A pending sidecar: the upgrade completes, the other brain files and the
    stamp update, the skipped file, its snapshot and the sidecar are
    byte-identical, the skip is reported, and a second upgrade also runs.
  - An unedited vault takes two routine phase changes in a row with no sidecar.
    A vault from before provenance existed (no `provenance.json`) does too.
  - An M3-shaped legacy vault, unedited or edited since migration, with or
    without a provenance record, keeps its live brain byte-identical across
    upgrades. Adopting the proposal converges on the next upgrade, and so does
    adopting, editing and resolving it.
  - A snapshot overwritten by hand is never fast-forwarded over. Without a
    record, a write that rests only on the fingerprint keeps a backup. A merge
    that changes nothing writes nothing.
  - An upgrade that fails on a later file keeps the earlier files' records.
  - `resolve`: a resolved conflict merges the next change cleanly; it refuses
    on conflict markers or with nothing pending; it removes an older engine's
    sidecar without touching the base.
  - `phases backport` still maps a vault edit after a skipped upgrade, and sees
    no edits after a fast-forward.
