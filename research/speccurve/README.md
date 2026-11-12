# research/speccurve — programme 2, work package WP5 step 2

The specification curve: every combination of the evaluation's defensible
choices, the paired effect of one arm against another in each, sorted, with
the choices marked beneath (Simonsohn, Simmons and Nelson, Nature Human
Behaviour 4, 2020).

| file | what it is |
|---|---|
| `curve.py` | `enumerate_specs` (full factorial of the choices), `paired_matrix` (per specification and workload: score of arm B minus arm A), `curve_from_matrix` (sorted effects with 95% intervals over workloads), `sign_flip_test` (whole-curve inference), `report` (markdown), `plot` (the curve over its choice matrix) |
| `test_curve.py` | a real effect is detected, no effect is not, an effect that depends on one choice splits the sorted curve, incomplete pairs are refused |

Inference for the whole curve, as the plan states it: the sign of each
workload's paired difference is flipped at random, the same flip in every
specification so that the dependence between specifications is kept, and
the curve is recomputed 2,000 times. The observed median effect and the
share of specifications significant in each direction are compared with
those null distributions (one-sided p-values, counting the observed draw).

Input is a long table, one row per (choices..., workload, arm) with a score
where lower is better. The arms and the re-runs per specification come from
WP9 (`research/comparators/`); the curve itself is not run yet.

```bash
python -m pytest -q
```
