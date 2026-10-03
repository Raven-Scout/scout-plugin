# Upgrades never lose a vault's edits to plugin-owned files

**Status:** implemented on `fix/upgrade-never-loses-vault-edits` (stacked on #251)
**Date:** 2026-09-30

## Problem

`scoutctl bootstrap upgrade` rewrites the plugin-owned files in a vault from
`templates/`. `/scout-update` runs it, and an unattended auto-update will run it
too. What happens to a vault's own edit depends on which table the file is in:

| Files | Today | Edit survives? |
|---|---|---|
| `_CAT1_FILES_FROM_PLUGIN`, `_CAT1_TEMPLATES` (scripts, hooks, `render.py`, …) | overwritten | **no**, and nothing says so |
| `_CAT1B_RUNNERS` (`run-*.sh`) | overwritten, old copy kept as `run-*.sh.bak.<date>` | only in the backup; the live runner loses it |
| `_CAT_MERGE_FILES` (`parser.py`) | 3-way merged; a conflict writes `<file>.proposed-merge` | yes, but the sidecar blocks the next upgrade |

The runner backup can't tell a hand edit from an ordinary template change,
because no record of the previous render is kept. One vault lost the same local
fixes on two upgrades in a row and restored them by hand both times. #251 moved
those fixes into the templates, but any other patch to a plugin-owned script is
still lost on the next upgrade, with no warning.

## Goals

1. An upgrade never deletes a vault edit without saying so. An edit the plugin
   didn't touch is kept as is. An edit that merges cleanly with the plugin's
   change is merged. An edit that conflicts keeps the vault's version running.
2. Nothing this adds can block a later upgrade. An unattended upgrade must be
   able to run again the next day.
3. Every kept, merged or conflicting edit is reported in `UpgradeResult`, the
   CLI, `bootstrap doctor` and `/scout-update`.
4. A vault-local fix can be turned into a plugin PR (`scoutctl bootstrap drift --patch`).
5. The first upgrade after this ships, when no vault has a snapshot yet, must
   not flag every file as edited.

Out of scope:
- The assembled `SKILL.md`, `DREAMING.md` and `RESEARCH.md` keep their sidecar
  policy, and a pending sidecar still blocks the upgrade. `scoutctl phases backport`
  covers these files. (Superseded 2026-10-02: a pending brain-file sidecar only
  skips that file; see `2026-10-02-brain-sidecars-never-block-upgrade-design.md`.)
- `.gitignore` stays append-only merged (#251).
- The install-only seeds are never overwritten, as before.

## Managed files

Every file in these four tables is a **managed file**, and all of them go
through one stage, `_stage_managed_files`:

| Table | Rendered | chmod 755 | Missing source |
|---|---|---|---|
| `_CAT1_FILES_FROM_PLUGIN` | no | no | placeholder |
| `_CAT_MERGE_FILES` (`parser.py`) | no | no | skip |
| `_CAT1_TEMPLATES` minus `.gitignore` | yes | yes | placeholder |
| `_CAT1B_RUNNERS` | yes | yes | skip |

The chmod and missing-source columns match today's behaviour. `parser.py` joins
the other managed files, so a conflict in it no longer writes a blocking sidecar.
The `_CAT_MERGE_FILES` name stays for two reasons: a sidecar an older engine left
behind still blocks the upgrade until it is resolved, and #244's
`bootstrap_auto.pending_sidecars` imports the name.

## State in the vault

```
.scout-state/last-rendered/<rel>   what the plugin last wrote, or accepted as the baseline, for <rel>
.scout-state/drift/<rel>.plugin    conflict: the plugin's new version, not applied
.scout-state/drift/<rel>.merge     conflict: the 3-way merge with conflict markers, as a starting point
.scout-state/drift/<rel>.vault     first baseline: the vault's copy that was replaced
```

Everything the doctor and `drift` report is worked out from these files, so no
separate manifest can fall out of step with them. Both stay tracked by the
vault's git. Losing `last-rendered/` on a re-clone or `git clean -X` would send
every edited file back through a first baseline. Tracking `drift/` puts a
parked edit in the vault's git history too.

## The decision per file

Terms: *new* is the render for this plugin version, *live* is the vault's file,
*base* is `last-rendered/<rel>`.

| Case | Action | Snapshot | Reported as |
|---|---|---|---|
| live missing | write new | new | — |
| live == new | nothing | new | — |
| live == base | write new (a plugin or template-variable change) | new | — |
| new == base, live differs | keep live | new | **kept** |
| both differ, merge clean | write the merge | new | **merged** |
| both differ, merge conflicts | keep live; park new as `.plugin` and the draft as `.merge` | unchanged (base) | **conflict** |
| no base, live a known render | write new | new | — |
| no base, live unknown | write new; park live as `.vault` (vault-developed file: keep live, park new as `.plugin`) | new (none) | **replaced** (**conflict**) |

Two rules hold across the table:

- **The snapshot advances only past changes the vault has absorbed.** After a
  conflict the base stays where it was, so the next upgrade tries the same merge
  again with the next plugin version. The conflict is reported until it is
  resolved, and the upgrade is never blocked.
- **Parked conflict files go away once the conflict does.** If a later upgrade
  merges cleanly, or finds live already equal to new, it removes `.plugin` and
  `.merge`. `.vault` copies stay until the user dismisses them.

If `git merge-file` itself fails (git missing, timeout), the file is treated as
a conflict: the vault's version keeps running.

Merges are line-based through `git merge-file`, the same tool the brain files
use. A clean merge can still be wrong in meaning (two edits that each work
alone). That is why every merge is reported, and `drift --diff` shows the
result.

### First baseline

A vault with no `last-rendered/<rel>` gives no way to tell a vault edit from a
template change between versions. Options considered:

- **Treat the live file as the base.** An edited file then looks unedited and is
  overwritten silently, as it is today. Rejected.
- **Keep the live file and flag it.** On the first upgrade after this ships,
  every file the plugin changed since the vault's last upgrade (usually several
  per release) would be flagged as edited. None of those fixes would land, and
  an old script could be left calling engine commands that no longer exist.
  Rejected; the brief rules it out explicitly.
- **Rebuild the old render from the plugin's git history.** This only works for
  git checkouts, and fails on shallow clones. It is also wrong whenever a
  template variable changed since the last render, for example `SCOUTCTL_BIN`
  moving with the plugin root: the merge would then treat the old path as a
  vault edit and put it back. Rejected.
- **Plugin wins, the vault's copy is parked and reported.** This is safe, but
  between releases 0 to 8 managed templates change (v0.8→v0.9: 8,
  v0.11→this branch: 6). So a vault that never edited anything would still get
  up to 8 false "replaced" reports, which is the flood the brief rules out.
- **Chosen: recognise a known render first; only an unknown file is parked.**
  Rules in order:
  1. live == new: record the baseline, report nothing.
  2. `parser.py`: the snapshot the old merge policy kept at
     `.scout-state/last-assembled/<rel>` is exactly the base. Move it to
     `last-rendered/` and decide as usual.
  3. **Known render:** live is an unedited render of the current template or of
     any template a release before this one shipped. Update it silently, as if
     live == base. The check uses a **signature** per template version: the
     sha256 of the raw template text, its line count, and the raw text of the
     lines holding a `{{VAR}}` token. To compare, each of those lines in live
     must match its raw line with each token replaced by this upgrade's value,
     and is then put back to the raw text; the result must hash to the
     signature. Only `SCOUTCTL_BIN` (it moves with the plugin root on a
     marketplace version bump) and `TODAY_DATE` may hold any value, the same
     one wherever the token repeats. A hand-fixed value on a variable line (a
     claude path, a budget) is therefore an edit, never a silent overwrite.
     Verbatim (`.py`) files are
     whole-file hashes. Signatures for v0.4.0–v0.11.0 ship in
     `engine/scout/defaults/render-history.json` (≈40 KB, generated from the
     release tags by `scripts/gen-render-history.py`). It is **frozen**:
     releases from this one on record their own snapshots, so only vaults last
     rendered by an older release ever read it.
  4. Otherwise the file is truly unknown: a vault edit on top of some render,
     or a render from an unreleased checkout.
     - A plugin-owned file: install new, park live as `drift/<rel>.vault`,
       report **replaced** once, and keep a doctor note while the copy exists.
       The plugin's fixes land.
     - A vault-developed file (`_CAT_MERGE_FILES`, i.e. `parser.py`, which
       dreaming sessions extend in the vault): keep live, park new as
       `.plugin`, report **conflict**. This is what the old sidecar did, minus
       the blocking.

  A vault that never edited anything upgrades silently. A vault that did edit is
  told where its edit is and loses nothing. From the next upgrade on every file
  has a base. `migrate-legacy` uses the same path. The `run-*.sh.bak.<date>`
  backups are retired in favour of `.vault` copies; the doctor still reports old
  `.bak` files.

## Resolving

- **Conflict:** edit the live file (the `.merge` file is a starting point), then
  run `scoutctl bootstrap drift --resolve <rel>`. This records `.plugin` as the
  base, so the upgrade treats the plugin's change as absorbed and what is left as
  the vault's edit, and it removes the parked files. It refuses while the live
  file still has conflict markers, or while it lacks part of the update, which
  means nobody merged the update yet. Without that check, clearing a yellow
  doctor by reflex would drop the plugin's change for good. The check compares
  whole lines, trailing whitespace ignored: each block of lines the update adds
  (base → `.plugin`) must occur in the file as a contiguous run at least as
  often as in the update, so a short line such as `fi` that the file already
  had elsewhere is not taken as merged; each block the update deletes must
  occur no more often than in the update. With no recorded base (a
  `parser.py` conflict on its first upgrade) there is nothing to check
  against, so a plain `--resolve` refuses. `--drop-update` records the file as
  it is: it keeps your version on purpose, and settles a hand merge the check
  can't confirm (a merge that rewrote the update's lines).
- **Take the plugin's version:** `cp .scout-state/drift/<rel>.plugin <rel>`. The
  next upgrade sees live == new and clears the conflict by itself.
- **Replaced copy:** once reviewed, `--resolve <rel>` deletes the `.vault`
  copies. To keep the old edit instead, copy the `.vault` file back first. From
  then on it is a normal vault edit and protected as one.

## Surfacing

- **`UpgradeResult.vault_edits`**: a list of `VaultEdit(path, outcome, parked, detail)`
  entries for kept, merged, conflict and replaced files. There is also an
  `error` outcome: a file the stage could not read or write (permissions, a
  locked file) is left untouched and reported, and the other files still
  upgrade. Vault files are read and written with `surrogateescape`, so a byte
  that is not valid UTF-8 never stops an upgrade. A conflict caused by `git
  merge-file` being unable to run (`MergeUnavailable`) says so, instead of
  asking for a hand merge. `backups` now lists the
  parked `.vault` copies. For `migrate-legacy`, `MigrateLegacyResult` gets the
  same two fields.
- **`bootstrap upgrade` / `migrate-legacy` CLI**: one line per edit, e.g.
  `vault edit conflict: scripts/heartbeat.sh — …`.
- **`bootstrap doctor`** (still read-only and vault-only):
  - A pending conflict is a *warning*, so the doctor turns yellow and the upgrade
    exits 1: the vault is running an older version of a plugin-owned file.
  - Kept edits and parked `.vault` copies are *notes*, a new
    `DoctorReport.notes` field: one line for all kept edits and one for all
    parked copies, since a vault with several hand fixes parks several. They are printed but leave the severity alone.
    Carrying an edit on purpose is not a health problem, and a yellow doctor that
    never clears teaches people to ignore it.
- **`/scout-update`**: step 3 explains each outcome and how to resolve it.
- **Auto-update**: the engine has no auto-apply runner yet; the unattended entry
  point is #244's `bootstrap auto`. The contract for any unattended caller:
  - Vault edits never make the upgrade refuse.
  - Conflicts make the doctor yellow and show in `vault_edits`.
  - `scoutctl bootstrap drift --json` is the notifier's data source. A notifier
    must not parse the human-readable output.

  #244's `result_dict` should add `"vault_edits"` once both are merged.

## Back-port: `scoutctl bootstrap drift`

```
scoutctl bootstrap drift            # one line per managed file that isn't clean
scoutctl bootstrap drift --diff     # plugin render vs live, template variables rendered
scoutctl bootstrap drift --patch    # a git-apply-able patch against templates/ for the edited files
scoutctl bootstrap drift --json     # machine-readable: status, +/- counts, parked paths, stale flag
scoutctl bootstrap drift --resolve REL [--resolve REL …]
```

`--patch` reuses `phase_backport`'s ideas. Rendering substitutes single-line
values, so line *i* of a template is line *i* of its render. The patch diffs
the render against the live file, then maps unchanged lines back to the raw
template lines and re-templatizes the added lines (`retemplatize`: the
`SAFE_VARS` values go back to `{{VAR}}`). The result applies to
`templates/<file>.tmpl` in a plugin checkout (`git apply`). Added lines that
still hold an instance-specific value (`RISKY_VARS`, or any variable value in a
verbatim `.py`) are listed on stderr. The plugin repo is public, so these must
be made generic before a PR. Only variable values can be detected, so every
`--patch` run also warns on stderr to review each added line for private vault
content (names, companies, issue IDs, channels, paths).

Some files are skipped, each with a reason:
- **Vault-developed files** (`parser.py`): the vault grows them on purpose, so
  their edits are vault content, not a plugin fix. Neither `drift` nor the
  doctor suggests `--patch` for them.
- **Stale files**, where the plugin has changes the vault hasn't taken yet
  (render != snapshot): the diff would revert them, so upgrade first.
- **Conflicts:** resolve them first.
- Files whose render changes the line count.

A dreaming session can run `drift --json` to find vault-local fixes worth
upstreaming.

## Testing

`engine/tests/unit/test_vault_drift.py` covers the decision table as a pure
function. `test_upgrade_keeps_vault_edits.py` runs whole upgrades against tmp
vaults with a tmp plugin root, so the plugin's version can change:

- A hand edit to `scripts/heartbeat.sh`, with no plugin change, is kept and
  reported.
- The same edit plus a plugin change elsewhere merges cleanly.
- An overlapping change conflicts: the vault's version stays live, the plugin's
  is parked, the doctor is yellow, and a second upgrade runs without error.
- A vault with no edits reports nothing and gets a green doctor.
- Repeated upgrades leave the files byte-identical.
- The first baseline of a no-snapshot vault parks only the files that differ;
  `parser.py`'s legacy snapshot is carried over.
- `drift --resolve` and `drift --patch` round-trip: the patch applied to the
  template renders exactly the live file.
