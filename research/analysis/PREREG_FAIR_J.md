# Pre-registration: the composite objective against fair and tier-capable comparators, on fresh shared seeds (FJ; audit Phase 3)

Registered 2026-09-26. Committed and pushed **before** any run of
`eval/experiments/matrix_fair_j.yaml`; the push event is the timestamp anchor,
and `scripts/check_prereg_timing.py` compares this file's first commit with
the campaign's first recorded run. Scorer: `analysis_fair_j.py`, committed
with this file, tested on synthetic matrices (`test_fair_j.py`), and not
edited after the run.

## Why this exists, and what was already seen

The paper's headline, "joint control wins the composite objective in every
campaign", was scored against comparators that the project's own audit later
found asymmetric: every reactive comparator carried a 1.4581x LRU inference
charge and a pinned 128 MB cache that no joint arm paid
(`RESULTS_EVICTION_PARITY.md`), and only the joint controller was subject to
the per-tenant budget filter (`RESULTS_BUDGET_PARITY.md`). The cost and SLO
components were re-scored; the composite objective J never was.

**This protocol is not blind, and says so.** It is written after an
exploratory re-analysis of the committed data (`REANALYSIS_MATRIX.md`,
2026-09-26) that found, at the design-point level: against `hpa_fair`
ΔJ = −0.4% with a CI crossing zero (a tie); against `keda_fair` −6.4% with a
CI below zero (a small win); on the Azure trace a loss to both fair arms; on
BurstGPT no win. Per workload against `hpa_fair`, the joint controller lost on
both AI workloads and won only on `crud_bursty`.

What this campaign adds is a confirmatory test with the defects that
analysis could not remove, removed:

- **Fresh seeds**: a new experiment name, so no run of this campaign shares a
  seed with any earlier campaign.
- **Shared seeds** (`shared_seeds: true`): every arm in a cell sees identical
  demand phases and arrival jitter; the published matrices did not.
- **The corrected solver**: `jcac_converged` sweeps to a fixed point, so every
  plan is a best response for every tenant (Proposition 1 holds by
  construction; `eval/tests/test_solver_convergence.py`).
- **Tuned comparators** (`systems.AS_RUN_UNTUNED`): the budget-carrying and
  layered arms run at their bases' best-of-grid parameters; the published
  ones ran constructor defaults.
- **A tier-capable comparator never measured at its tuned target**:
  `jcac_nojoint_v2_tuned`, the layered stack that holds all three knobs.
- **The independent unit**: the design point, not the (design point x rep)
  pair.

## Design (frozen)

`eval/experiments/matrix_fair_j.yaml`: simulator backend, published economy
and model forms, 120 steps, 5 reps, `shared_seeds: true`, the headline cells
(workloads `crud_bursty`, `crud_steady`, `ai_cacheable`, `ai_uncacheable`,
`agentic`; mixes `uniform`, `premium_heavy`, `besteffort_heavy`, `whale`;
cluster sizes `small`, `medium`, `large`): 60 design points x 5 reps x 7 arms
= 2,100 runs.

| arm | role |
|---|---|
| `jcac_converged` | **treatment**: anchored moves, sweeps to a fixed point |
| `jcac_anchored` | the published quotable controller, reported beside (descriptive) |
| `hpa_fair` | FJ-H1 comparator: HPA at its tuned target, no LRU charge, 512 MB cache |
| `keda_fair` | FJ-H2 comparator: KEDA at its tuned target, no LRU charge, 512 MB cache |
| `jcac_nojoint_v2_tuned` | FJ-H3 comparator: layered stack holding replicas (HPA rule, tuned), cache and tier (latency-ranked) |
| `hpa_budget_tuned` | FJ-S1 comparator: `hpa_fair` plus the treatment's per-tenant budget rule, tuned |
| `keda_budget_tuned` | FJ-S2 comparator: `keda_fair` plus the budget rule, tuned |

## Tests (frozen)

J is `stats.composite_objective`, unchanged: cost per tenant-step / 0.01 +
2 x violation + 0.5 x (1 − Jain).

- **Unit.** The design point. Each arm's 5 reps are averaged within a design
  point; differences are treatment − comparator, paired by design point.
  **Listwise rule:** a design point is kept only if every arm has all 5 reps
  valid there; the number dropped is reported.
- **Confirmatory family, FJ-H1..H3** (one Holm family of 3, α = 0.05). The
  treatment beats the comparator on J iff BOTH (a) the one-sided Wilcoxon
  signed-rank p (differences below zero) is at or below its Holm threshold
  AND (b) the 95% percentile bootstrap CI of the mean difference (10,000
  resamples of design points, seed 20260926) lies entirely below 0.
  Requiring both closes the gap the audit found between a rank test and the
  mean.
- **SLO guard**, one per confirmatory comparator: `mean_excess`
  non-inferiority under the unified rule (`REANALYSIS_NONINFERIORITY.md`):
  the one-sided 95% bootstrap upper bound of the mean difference is below
  0.05. A guard does not change its J verdict; it decides whether the J
  result may be stated without a severity regression beside it.
- **Secondary, not confirmatory, FJ-S1/S2**: the same test against the
  budget-carrying arms, in their own Holm family of 2.
- **Descriptive only**: `jcac_anchored` against the same comparators;
  per-workload differences; per-arm means including the AI-shed
  (`tier = none`) share.

## Predictions (written knowing the exploratory re-analysis)

- **FJ-H1** (`hpa_fair`): expected **FAIL** (the exploratory reading is a tie).
- **FJ-H2** (`keda_fair`): expected **PASS** with a small effect (exploratory
  d_z ≈ −0.30).
- **FJ-H3** (the tier-capable layered stack): **no directional prediction**.
  It has never been measured at its tuned replica target; the published
  −joint-control ablation margin (+318.5% cost) was against an untuned stack.
- **Guards**: expected PASS against `hpa_fair` (exploratory severity
  difference −0.21); no prediction for the others.

## What each outcome means (committed before the run)

- **FJ-H3 PASS**: joint control beats a competent per-layer stack that holds
  all three knobs; the jointness claim stands, stated at the measured effect.
  **FJ-H3 FAIL**: jointness is not shown better than a competent per-layer
  stack in this simulator, and the paper does not claim it.
- **FJ-H1 / FJ-H2 PASS**: a composite advantage over that fair single-knob
  scaler stands at the measured size. **FAIL**: no composite advantage over
  it is claimed.
- **A guard FAIL**: the corresponding J result is never stated without the
  severity regression beside it.
- These are the only claims this campaign can support. Everything else it
  prints is descriptive.

## Stopping rule

One sitting. No re-run, widening, retuning or change of arms after the first
run. The harness's transient-error retries (2) are allowed; a run still
failing after them is recorded as failed and its design point is dropped for
every arm under the listwise rule. If the scorer is found defective after the
run, the defect is fixed in a new scorer and a new record, and this record
stands as committed.

## Substrate, stated

This is the simulator. The planner still plans with the function the
simulator scores it with (the model-match objection), which this campaign
does not address; a structural-mismatch campaign is registered separately.
The objective weights are the published ones. Absolute numbers are
model-scale; the claims are the paired differences.
