# Coordination gap (anchored controller) — clamp-fixed CD vs exact joint optimum

Campaign of `PREREG_MOVE_CLAMP.md` (anchored rerun over the same frozen seeds); mechanics in `coordination_gap.py`, rows in `coord_gap_anchored.csv`.

| N | instances | exact-match | median gap | p95 gap | max gap |
|---|---|---|---|---|---|
| 2 | 60 | 60/60 | 0.0000% | 0.0000% | 0.0000% |
| 3 | 60 | 60/60 | 0.0000% | 0.0000% | 0.0000% |

Discarded instances (infeasible lattice or CD fallback): 0 — listed in the CSV, per the prereg's handling rule.

**CG-H1 (median gap <= 1% and max gap <= 5% at every N): PASS.**
