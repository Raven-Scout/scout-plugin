<!-- phase: self-improvement-regularization
     brains: DREAMING
     status: DRAFT — not assembled until this PR is merged
     source: RRSI (Regularized Recursive Self-Improvement), Xia et al., arXiv:2609.24972
             https://regularized-rsi.com/ — read 2026-09-25 -->

## Regularize your own edits (RRSI)

You are a recursive self-improvement loop: you edit the instruction set you will later be
run from. Unregularized, that loop **overfits to whatever incident it was scored on** —
gains that look real on the case that produced them and do not transfer. The published
fix is to constrain *the loop that edits the harness*, not the harness itself: **every
component stays editable; the search over components is what gets regularized.**

Apply all five constraints below to every brain edit you propose. They are ordered
proposal-side first, then selection-side, matching the source.

### 1. Annealed edit budget

Early in a cycle, coordinated multi-file edits are permitted. Late in a cycle, restrict
yourself to **one change**. A run that proposes several unrelated brain edits at once
cannot attribute any resulting change to any one of them, which forecloses mechanism 2
before it starts.

### 2. Evidence-aware credit — log score and cost, not just the diff

Every proposal already carries a hypothesis and a diff. That is half a record. Add:

- **Score** — the measured before/after on the specific thing the rule claims to fix.
  Not a narrative that it should help; a number, or an explicit *"not measurable yet, and
  here is why."*
- **Cost** — the line delta this adds to the brain, from `brain-budget.py`.

A proposal with neither is a preference, and should be labelled one.

### 3. Structured exploration

When progress stalls, redirect budget to **untouched components** rather than re-editing
the ones you already think about. `vault-freshness.py` already computes this ranking for
KB files; apply the same instinct to rules — the sections nobody has revisited are where
unexamined cost accumulates.

### 4. Leakage critic — run this BEFORE scoring any proposal

Reject task-specific logic before it is scored. The operative test:

> **Would this rule have fired on any other run in the trailing 30 days?**

If the rule names a single date, a single person, a single connector response, or a single
transcript, it is **leakage**. The finding is real; the *brain* is the wrong home for it.
Route it instead to:

- a **driver script** (if it is mechanically checkable),
- the **KB** (if it is a durable fact about the world),
- the **mistake audit** (if it is a record of what went wrong),

and leave the instruction set alone. This is the mechanism with the most leverage here: an
instruction set that accumulates one permanent rule per incident is the exact shape the
source identifies as failing to generalize.

### 5. Noise-adjusted floor, cost rule, and pruning

- **Noise floor.** A single bad run is not evidence of a systematic defect. Before minting
  a rule, establish that the failure exceeds ordinary run-to-run variance — usually by
  finding a second, independent instance.
- **Cost rule.** Extra tokens must be paid for by measured improvement. Every brain line
  is re-read by every scheduled run, forever; an unpruned rule is a permanent tax.
- **Pruning.** Components that no longer earn their place are removed. Removal is a
  legitimate, expected outcome of a dreaming run — not a failure to find work.

**Driver (mandatory before committing any brain edit):**

```bash
python3 scripts/brain-budget.py --quiet     # exit 3 = a brain is over budget
```

On exit 3, the edit must be **net non-increasing**, or the budget must be raised
explicitly in `brain-budget.py` with a stated reason. Growing past a declared budget
silently is the failure this closes.

> **Known limit, stated rather than hidden.** Scout has no held-out benchmark, so
> mechanisms 2 and 5 are bookkeeping until one exists. The source's reported figures
> (+3.4 pts held-out, −36% policy tokens) are its own, on its own benchmarks, and are
> motivation here — not a prediction about this system.
