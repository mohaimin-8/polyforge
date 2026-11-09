# PREREG_LEVEL_LAG — one changed factor after LEVEL's lag failure

**Status:** registered 2026-11-10, before any run of the two experiments
below; committed and pushed to the public branch `level-campaign` before the
first run (the push and its CI run are the timestamp anchor). Scorer
`research/analysis/analysis_level_lag.py` frozen with this file. Any later
change is a dated note appended after this text.

## 1. Why

LEVEL (`RESULTS_LEVEL.md`) scored its treatment `jcac_level` worse than the
budget-gated stack (LV-L1, +0.0335) and capped HPA (LV-L2, +0.0518) when
replicas arrive one interval late, while under instant actuation it tied
them. Descriptively, under lag its J_L rose by 0.043 and the published
controller's by 0.001. The diagnosis: `jcac_level` uses the half-replica
switching penalty (`SWITCH_PENALTY_HALF_REPLICA`), so it moves replicas down
and up again and pays for every replica that arrives late, whereas the
published penalty (0.05 per changed knob) ratchets replicas upward and holds
them. This campaign changes exactly that one factor.

## 2. Design

**Arms** (LEVEL's, unchanged; no re-tuning):

| arm | role |
|---|---|
| `jcac_level_ratchet` | **treatment**: `jcac_level` with the published switching penalty and nothing else changed |
| `jcac_level` | LEVEL's treatment (half-replica penalty) |
| `layered_small_level_gated` | LEVEL's budget-gated cheapest-tier stack |
| `hpa_level_capped` | LEVEL's capped HPA |

**Experiments** (fresh seeds: the run identity includes the experiment name):
`matrix_level_lag2` (main matrix, replicas one interval late and grown caches
half-warm, as ACTUATION) — primary; `matrix_level_inst2` (main matrix,
instant actuation) — control. 60 design points x 5 replications x 4 arms =
1,200 runs each, 2,400 in all. Published plant, J_L as in LEVEL.

## 3. Tests (one Holm family of 5, alpha 0.05; LEVEL's FAIR_J rule)

| id | experiment | treatment vs | prediction |
|---|---|---|---|
| LL-H1 | lag | `jcac_level` | **PASS** (the diagnosis: the ratchet is the hedge) |
| LL-H2 | lag | `layered_small_level_gated` | FAIL (at best a tie: the published controller tied it under lag in LEVEL) |
| LL-H3 | lag | `hpa_level_capped` | FAIL (capped HPA led the published controller under lag in LEVEL) |
| LL-H4 | instant | `jcac_level` | FAIL (no difference expected under instant actuation) |
| LL-H5 | instant | `layered_small_level_gated` | FAIL |

**Meaning, fixed now.** LL-H1 PASS confirms the diagnosis (hysteresis is a
hedge against actuation lag); FAIL rejects it. LL-H2/H3 say whether the
lag-robust variant is competitive with the strongest comparators under lag;
LL-H4/H5 whether it gives anything up under instant actuation. Descriptive:
per-arm means and per-metric deltas.

## 4. Rules

As LEVEL §5: no change after the first run; harness retries a failed run up
to twice and a still-failing design point drops out listwise; every verdict
reported as scored; no re-run or re-scoring under another rule.
