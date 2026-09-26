# Pre-registration: structural model mismatch -- do the FAIR_J wins survive a planner that is wrong about the plant's FORM? (SM; audit Phase 3)

Registered 2026-09-26. Committed and pushed **before** any run of the five
`eval/experiments/matrix_sm_*.yaml` campaigns; the push event is the
timestamp anchor, checked by `scripts/check_prereg_timing.py`. Scorer:
`analysis_structural_mismatch.py`, committed with this file, tested on
synthetic matrices (`test_structural_mismatch.py`), not edited after the run.
It imports its test from the frozen `analysis_fair_j.py`, so this campaign is
judged by exactly the rule `PREREG_FAIR_J` froze.

## Why this exists

The audit's most damaging simulator finding: the joint controller plans with
the very function the simulator scores it with (`controller._project` calls
the engine's `evaluate_step`). It therefore sees 100% of the plant's
congestion, latency, cache, tier and cost model, an advantage no reactive
baseline has. The existing mismatch study (`RESULTS_MODEL_MISMATCH.md`) only
scales three constants by ±25%; it never changes the model's form. And on the
live plane, where the model WAS wrong (about 1,000x pessimistic about the
tier), the published controller lost to its own tier-only ablation (B1).

`belief_form="published"` (commit 740f623) lets the planner believe the
published forms while the plant runs another. `PREREG_FAIR_J` found, with the
planner seeing the plant: a tie with `hpa_fair`, a small win over `keda_fair`
(−7%), and a large win over the tier-capable layered stack (−60%). This
campaign asks which of those wins survive when the planner is wrong about the
plant's form.

**Disclosed:** this protocol is written after `RESULTS_FAIR_J.md` was known.
No campaign run of these arms under these plants exists. The harness tests
executed three test-sized smoke runs of the new arms under altered forms
(`test_the_blind_arm_plans_with_the_published_form_whatever_the_plant`,
`test_the_calibrated_blind_arm_...`); their values were checked only for
"differs from the oracle" and "positive", and were not examined.

## Design (frozen)

Five simulator campaigns, one per plant form, each 60 design points (the
headline cells) x 5 reps x 6 arms = 1,800 runs, 9,000 in all; published
economy; 120 steps; `shared_seeds: true`; a distinct experiment name per
plant, so all seeds are fresh.

| plant | `model_form` | source |
|---|---|---|
| `latency` | congestion exponent 0.86, p95 tail f0 1.6909, b 0.1303 | `RESULTS_LM_ADOPTION.md` |
| `mixture` | true hit/miss mixture p95 for AI traffic | `RESULTS_MIXTURE_P95.md` |
| `tierwu` | tier work units 1 : 1.475 : 1.518 | the corrected two-GPU ratio (`research/calibration/TIER_BENCH.md`); the 1 : 1.516 : 16.64 of `RESULTS_TIER_WU.md` is the CPU-offload artifact |
| `live` | replica capacity 1123 wu; no AI load on replicas; tier latencies 261.7 / 751.2 / 2156 ms; uniform cacheability; crud_base_scale 0.013175 | the plant fitted to the live B1′ evidence (`live_plant_b1prime.json`); the scale is the median of its four per-cell fits |
| `combined` | `latency` + `mixture` + `tierwu` together | |

| arm | role |
|---|---|
| `jcac_converged_blind` | **treatment**: the converged solver believing the PUBLISHED forms |
| `jcac_calibrated_blind` | secondary treatment: the same, plus the B1′ online calibration (headroom-calibrated capacity, learned tier-latency offset, half-replica switching penalty) |
| `jcac_converged` | the oracle: the same solver seeing the plant |
| `keda_fair` | SM-H1 / SM-S1 comparator (FJ-H2's) |
| `jcac_nojoint_v2_tuned` | SM-H2 / SM-S2 comparator (FJ-H3's): the layered stack holding all three knobs |
| `hpa_fair` | reported beside (FJ-H1 was a tie) |

## Tests (frozen)

The `PREREG_FAIR_J` rule, imported from its scorer: the design point is the
unit (reps averaged; listwise if any arm lacks a rep); a win needs BOTH the
one-sided Wilcoxon p at or below its Holm threshold AND the 95% bootstrap CI
of the mean J difference (10,000 design-point resamples, seed 20260926)
entirely below 0.

- **Confirmatory, SM-H1[p] and SM-H2[p]** for each of the five plants p: the
  blind planner against `keda_fair` and against the layered stack. One Holm
  family of 10, α = 0.05.
- **Secondary, not confirmatory, SM-S1[p] and SM-S2[p]**: the calibrated
  blind planner against the same comparators, in its own Holm family of 10.
- **SLO guard** per plant and confirmatory comparator: `mean_excess`
  non-inferiority of the blind planner (one-sided 95% bootstrap upper bound
  < 0.05).
- **Descriptive**: the price of the wrong belief (J of the blind planner minus
  J of the oracle, per design point); the blind planner against `hpa_fair`;
  per-arm means per plant; the AI-shed share.

## Predictions

- **SM-H2** (against the layered stack) under `latency`, `mixture`,
  `tierwu` and `combined`: expected **PASS**. The FAIR_J effect is large
  (d_z −0.62), and these forms move the model moderately.
- **SM-H1** (against `keda_fair`): **no directional prediction**. The FAIR_J
  effect is small (−7%) and could be erased by a form mismatch.
- **Both, under `live`**: expected **FAIL**. The live plant is about 11x more
  capable than the model believes, which is the mismatch that cost the
  published controller B1.
- **Secondary, calibrated blind planner, `live`**: no directional
  prediction. Calibration rescued the controller live (B1′), but this plant
  differs from the live cells.
- **Price of the wrong belief**: expected small for `latency`, `mixture`,
  `tierwu` and `combined`, and large for `live`.

## What each outcome means (committed before the run)

- **A confirmatory PASS in a plant**: the corresponding FAIR_J win does not
  depend on the planner knowing that plant's form.
- **A confirmatory FAIL**: that win depends on model match for that form, and
  it is never stated without this result beside it.
- **All of `latency`, `mixture`, `tierwu` and `combined` PASS for SM-H2**: the
  model-match objection is answered for the measured forms the project has.
- **The secondary family**: it says whether online calibration recovers what a
  wrong belief loses. It is reported as secondary evidence, never as a
  confirmatory claim.

## Stopping rule

Same as `PREREG_FAIR_J`: one sitting per plant, no re-run, widening, retuning
or change of arms or plant forms after the first run. The harness's two
transient-error retries are allowed; a run still failing is recorded and its
design point dropped for every arm in that plant. A scorer defect found after
the run is fixed in a new scorer and a new record.
