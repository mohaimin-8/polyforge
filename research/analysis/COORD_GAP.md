# Coordination gap — two-sweep coordinate descent vs exact joint optimum

Campaign of `PREREG_COORD_GAP.md` (pushed before the run); mechanics in
`coordination_gap.py`, rows in `coord_gap.csv`.

## Scored instances (the pre-registered gate)

| N | instances | exact-match | median gap | p95 gap | max gap |
|---|---|---|---|---|---|
| 2 | 37 | 37/37 | 0.0000% | 0.0000% | 0.0000% |
| 3 | 46 | 46/46 | 0.0000% | 0.0000% | 0.0000% |

**CG-H1 (median gap ≤ 1% and max gap ≤ 5% at every N): PASS** — and
maximally so: on every scored instance the two-sweep coordinate descent
found the exact joint optimum of the one-move lattice.

## What the 37 discarded instances actually were (the audit's real finding)

The prereg's discard rule labeled out-of-lattice CD answers
"shed-cost fallback". Diagnosis of every one of the 37 flagged rows
(23 at N=2, 14 at N=3) shows **none of them was the fallback** — they are
plans whose final state is *two moves* from the interval start:

- 24 tenant-plans moved the cache **two levels** in one interval
  (e.g. 64 → 256 MB);
- 15 tenant-plans moved replicas **beyond ±2** in one interval
  (worst observed: ±4).

Mechanism: `_best_for_tenant` anchors its candidate lattice at
`chosen[tid]`, so the **second coordination sweep re-anchors the move
clamps at its own sweep-1 choice**. The docstring's contract — "cache may
move at most one level per interval", the ±2 replica clamp the v3
overload cells' arithmetic calls "the shared actuation clamp" — is
enforced per *sweep*, not per *interval*. Every baseline applies its
delta once and is genuinely clamped; the published jcac was not. Every
committed jcac run in the record contains this behavior.

This mischaracterization in the prereg's discard label is itself
disclosed here: the rule quarantined exactly the right rows for the
scored gap statistic (states outside the one-move lattice cannot be
scored against the one-move optimum), but for the wrong stated reason.

## Consequence and closure

The finding is adjudicated the project's own way — a pre-registered
rerun, not an in-place rewrite of history:

- `controller.py` gains `anchor_moves=True` (candidate moves anchored at
  the interval-start state across both sweeps; property-tested). The
  default remains the published behavior so every committed campaign
  stays bit-reproducible.
- `PREREG_MOVE_CLAMP.md` (pushed before its run) reruns the full
  headline matrix with the clamp-fixed controller as treatment
  (`jcac_anchored`) and re-runs this audit's frozen seeds anchored
  (`COORD_GAP_ANCHORED.md`), where the two-move class must vanish.

Scope note, unchanged from the prereg: N ≤ 3, uniform fairness weights,
sorted tenant order (the deployed behavior); order-dependence at fixed N
remains unvaried.
