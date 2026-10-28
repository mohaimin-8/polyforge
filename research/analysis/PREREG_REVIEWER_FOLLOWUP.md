# Pre-registration: reviewer follow-up — wide-step joint control against the baselines, and the main comparisons on the live-fitted plant (RV; 2026-10-28)

Registered 2026-10-28. Committed **before** any run of
`eval/experiments/matrix_reviewer_{wide,live}.yaml` (commit time is the anchor;
the commit is local and is pushed together with the paper when the authors
decide, because the paper is under double-blind review). Scorer:
`analysis_reviewer.py`, committed with this file, checked on a synthetic frame,
not edited after the run. Every test is the frozen `analysis_fair_j.py` rule.

## Why this exists

A reviewer of the IEEE paper asked (Q4, Q5, weakness 1c, 2b):

- how joint control with **wide** steps compares with the autoscaler run with
  **Kubernetes' default behavior** and with the other baselines (WIDTH compared
  wide-step joint control only with decoupled search);
- what the main comparisons look like on the plant **fitted to the deployed
  system** (replicas serve 1,123 units/s, AI work never loads them), where only
  coordination (WD-H3) and a planner–plant mismatch (SM-H2) were measured.

## What was already seen (disclosed)

All registered results to date. In particular: WD-H5 (wide steps raise the joint
controller's own J on every plant: +0.0301 live, +0.0152 published, +0.0859
coupled); JN-B4 (rate-limited joint control ties capped HPA, −0.0104); JN-B1
(ties the cheapest-tier stack, +0.0178); SM-H2 (with the published model on the
live plant, the layered stack wins by 0.1052). One 6-run timing smoke
(agentic, uniform, large, 20 steps, 1 rep; scratch DB, not analysed) checked
that every arm runs.

## New arm

`hpa_k8s_capped`: `hpa_fair_capped` (same tuned target per cluster size, same
cluster caps) with Kubernetes' default HPA behavior instead of the ±2 clamp:
scale up by max(100%, 4 pods) per interval, no up-stabilization; scale down to
the highest recommendation of the last 300 s. One 10 s interval is treated as
one 15 s policy period (slightly faster than the default). Not re-tuned for the
new policy. Code: `research/jcac_sim/baselines.py` (`HPAController`,
`policy="k8s_default"`), unit-checked.

## Design

Both campaigns: the 60-point main matrix (5 workloads × 4 eight-tenant mixes ×
3 cluster sizes), 120 steps, 5 reps, shared seeds, new experiment names (fresh
seeds). Unit: the design point, reps averaged; listwise completeness.

**Campaign W** (`matrix_reviewer_wide`, published plant): `jcac_converged`,
`jcac_joint_wide_tracked`, `hpa_fair_capped`, `hpa_k8s_capped`,
`layered_small_tuned`, `static_share`.

**Campaign L** (`matrix_reviewer_live`, live-fitted plant, the `model_form` of
`matrix_width_live`; planner and plant share it): `jcac_converged`,
`jcac_decoupled`, `jcac_tier_floor`, `hpa_fair_capped`, `keda_fair_capped`,
`layered_small_tuned`, `static_share`. Comparators keep their published-plant
tuning.

## Tests

PASS = one-sided Wilcoxon (a better, i.e. ΔJ = J_a − J_b < 0) at its Holm
threshold within the campaign's family **and** the 95% bootstrap CI of the mean
ΔJ (10,000 resamples) entirely below 0. Predictions are stated; a FAIL is a
result.

| id | a | b | prediction |
|---|---|---|---|
| RV-W1 | `jcac_joint_wide_tracked` | `hpa_k8s_capped` | FAIL |
| RV-W2 | `jcac_joint_wide_tracked` | `hpa_fair_capped` | FAIL |
| RV-W3 | `jcac_joint_wide_tracked` | `layered_small_tuned` | FAIL |
| RV-W4 | `jcac_converged` | `jcac_joint_wide_tracked` | PASS (replicates WD-H5 on all 60 points) |
| RV-L1 | `jcac_converged` | `hpa_fair_capped` | FAIL |
| RV-L2 | `jcac_converged` | `keda_fair_capped` | FAIL |
| RV-L3 | `jcac_converged` | `layered_small_tuned` | FAIL |
| RV-L4 | `jcac_converged` | `static_share` | FAIL |

Holm families: {RV-W1..W4} and {RV-L1..L4}, α = 0.05 each.

**Equivalence (RV-L-EQ):** `jcac_converged` − `jcac_decoupled` on the live plant;
EQUIVALENT if the 95% CI lies inside ±0.025 J (the JOINTNESS margin).
Prediction: EQUIVALENT.

**Descriptive (no test):** `hpa_k8s_capped` − `hpa_fair_capped`;
`jcac_converged` − `hpa_k8s_capped`; on the live plant `jcac_tier_floor` −
`jcac_converged` and `jcac_tier_floor` − `hpa_fair_capped`; per-arm means of J,
cost, violation, excess and AI shed share.

**Exploratory, from existing data (JOINTNESS):** the shed price κ at which each
remaining instant-actuation difference changes sign, with J' = J + κ·s (s = AI
shed step share), per design point with bootstrap CIs. Not a test.

## Amendment 1 (2026-10-28, during the runs, before any scoring)

Two scorer changes, neither touching a test: (a) the scorer reads the committed
`metrics_reviewer_{wide,live}.csv.gz` exports when the gitignored duckdbs are
absent (the gate's convention); (b) after code review, the exploratory κ*
bootstrap drops resamples whose mean Δs ≤ 0 instead of flooring the
denominator at 1e-12, and reports the count (0 of 10,000 for every comparator
on the JOINTNESS data, so the κ* values are unchanged). The review also noted
that RV-W4 shares the Holm family with RV-W1..W3; the family is kept as
registered.

## Stopping rule

Each campaign runs to completion once; failed runs are retried by the runner
(`retries: 2`) and design points missing a rep for any arm are dropped
listwise. No rerun after scoring.
