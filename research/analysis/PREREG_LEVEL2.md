# PREREG_LEVEL2 — the best joint controller found, re-tested against every level comparator

**Status:** registered 2026-11-10, before any run of the six experiments below;
committed and pushed to the public branch `level-campaign` before the first run
(the push and its CI run are the timestamp anchor). Scorer
`research/analysis/analysis_level2.py` frozen with this file; the GPU-ladder
comparators' tuning (`eval/baselines/tuned_arms.yaml`, `grids_arms/*_gpu_*`)
frozen with it too. Later changes only as dated notes after this text.

## 1. Why

LEVEL (`RESULTS_LEVEL.md`) tested the corrected controller `jcac_level` at a
level field: 1 of 11 PASS. LEVEL_LAG (`RESULTS_LEVEL_LAG.md`) changed one
factor, the switching penalty, and the result, `jcac_level_ratchet`, beat
`jcac_level` and the budget-gated stack under lag and under instant
actuation, and tied capped HPA. LEVEL_LAG did not include LEVEL's best arm on
J_L (the ungated capped cheapest-tier stack), the held-out or trace demand, or
the monotone agent row, and no campaign has yet tested a level field under the
measured GPU price ladder. This campaign does all of that on fresh seeds.

The treatment was chosen in sequence after seeing LEVEL and LEVEL_LAG on
other seeds; this is a confirmatory replication of a variant found by that
sequence, not a blind test.

## 2. Design

**Treatment** `jcac_level_ratchet` (= `jcac_level` with the published
switching penalty; outage priced at the small tier in its objective, graceful
degradation, damped Holt). **RQ2 ablation** `jcac_level_ratchet_decoupled`.

**Comparators** — LEVEL's, unchanged: `layered_small_level_gated`,
`layered_small_level_capped`, `hpa_level_capped`, `keda_level_capped`,
`static_share`. Under the GPU ladder (1 : 1.475 : 1.518, where pinning the
cheapest tier is a straw man): `layered_costaware_gpu_gated` and
`layered_costaware_gpu_capped` (cost-aware tier rule, capped, with and
without the gateway budget gate), `layered_small_gpu_gated`, `hpa_gpu_capped`,
`static_share`, each re-tuned per size under J_L at that economy
(`tune_arms.py --level-gpu`; no optimum on a grid edge).

**Experiments** (fresh seeds; run identity includes the experiment name):

| key | experiment | substrate | runs |
|---|---|---|---:|
| M | `matrix_level2` | main matrix, published plant, instant | 2,100 |
| R | `matrix_level2_mono` | main matrix, monotone agent row | 2,100 |
| L | `matrix_level2_lag` | main matrix, replicas one interval late | 2,100 |
| O | `matrix_level2_heldout` | the two held-out MMPP workloads | 840 |
| T | `matrix_level2_trace` | TA's 96 BurstGPT windows | 672 |
| G | `matrix_level2_gpu` | main matrix, measured GPU price ladder | 2,100 |

**Objective** J_L, LEVEL's (every refused request charged $1e-4, the small
tier's price at both ladders).

**Development data seen before registration** (tune.py's slice, J_L):

| | small | medium | large |
|---|---:|---:|---:|
| treatment, published ladder | 0.56324 | 0.43963 | 0.39125 |
| LEVEL comparators, published ladder (best of four) | 0.53108 | 0.43261 | 0.43507 |
| treatment, GPU ladder | 0.51153 | 0.38528 | 0.36002 |
| `layered_costaware_gpu_capped` (GPU) | 0.46655 | 0.39921 | 0.41040 |
| `layered_costaware_gpu_gated` (GPU) | 0.46731 | 0.40328 | 0.41542 |
| `hpa_gpu_capped` (GPU) | 0.53269 | 0.43261 | 0.45389 |

## 3. Tests (one Holm family of 19, alpha 0.05; LEVEL's FAIR_J rule)

| id | exp. | treatment vs | prediction |
|---|---|---|---|
| L2-M1 | M | `layered_small_level_gated` | PASS (LL-H5) |
| L2-M2 | M | `layered_small_level_capped` | FAIL (LEVEL's best arm on J_L) |
| L2-M3 | M | `hpa_level_capped` | FAIL (tie in LL-H3's control) |
| L2-M4 | M | `keda_level_capped` | PASS |
| L2-M5 | M | `static_share` | PASS |
| L2-R1 | R | `layered_small_level_gated` | PASS |
| L2-R2 | R | `layered_small_level_capped` | FAIL |
| L2-L1 | L | `layered_small_level_capped` | FAIL |
| L2-L2 | L | `hpa_level_capped` | FAIL (LL-H3) |
| L2-L3 | L | `keda_level_capped` | PASS |
| L2-O1 | O | `layered_small_level_gated` | FAIL (LEVEL's held-out loss was large) |
| L2-O2 | O | `layered_small_level_capped` | FAIL |
| L2-O3 | O | `hpa_level_capped` | FAIL |
| L2-T1 | T | `layered_small_level_gated` | PASS (LV-T1) |
| L2-T2 | T | `layered_small_level_capped` | FAIL (the ungated arms led on BurstGPT) |
| L2-T3 | T | `hpa_level_capped` | FAIL |
| L2-G1 | G | `layered_costaware_gpu_gated` | FAIL |
| L2-G2 | G | `layered_costaware_gpu_capped` | FAIL |
| L2-G3 | G | `hpa_gpu_capped` | PASS |

**Coordination** (readings, no p-value): L2-C[key] = treatment − decoupled
search on J_L, EQUIVALENT inside ±0.01. Prediction: directional gains of
1–4% as in LEVEL, equivalent only where LEVEL's was.

**Meaning, fixed now.** The joint controller is a better *default* than a
per-layer stack only where it passes against both the gated and the ungated
capped stack on the same substrate; a pass against the gated stack alone means
it is the better *budget-keeping* controller. Failures on O and T bound every
pass to the periodic matrix.

## 4. Rules

As LEVEL §5.
