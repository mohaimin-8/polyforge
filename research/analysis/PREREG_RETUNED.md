# Pre-registration: the composite objective against comparators re-tuned per cluster size (RT; audit Phase 3)

Registered 2026-09-26. Committed and pushed **before** any run of
`eval/experiments/matrix_retuned.yaml`; the push event is the timestamp
anchor, checked by `scripts/check_prereg_timing.py`. Scorer:
`analysis_retuned.py`, committed with this file, tested on synthetic matrices
(`test_retuned.py`), not edited after the run. It imports its test from the
frozen `analysis_fair_j.py`, so this campaign is judged by exactly the rule
`PREREG_FAIR_J` froze.

## Why this exists

`PREREG_FAIR_J` compared the joint controller with comparators at their
published best-of-grid parameters (`eval/baselines/tuned.yaml`). Those
optima were all grid edges. `tune.py` bounded its grids on the stated ground
that J falls monotonically as utilization targets fall, with the limit being
static over-provisioning, "which the `static` baseline already covers".
`TUNING_EXTENDED.md` (exploratory, 2026-09-26) tested that ground on the same
tuning slice, seeds and objective, and found it false. J turns at interior
targets well below the published bounds (HPA: ρ ≈ 0.05, J −9.9%), while
`static`, scored as the harness runs it, is over seven times worse than any of them (J 3.70–3.80 against
0.50–0.51). So every published
comparison, FAIR_J included, ran against comparators tuned short of their
own optimum on the objective they are graded on.

This campaign asks whether the FAIR_J results survive comparators tuned to
their interior optimum, per cluster size, as the harness runs them.

## What was already seen (disclosed)

This protocol is not blind.

- **Known before writing, from `RESULTS_FAIR_J.md`:**
  - FJ-H1: a tie with `hpa_fair`.
  - FJ-H2 PASS: `keda_fair`, −7%.
  - FJ-H3 PASS: the layered stack, −60%.
- **Known from the tuning slice (`TUNING_EXTENDED.md`, arm table).** These
  are 12 runs per setting, on seeds disjoint from every campaign. The
  re-tuned arm minus `jcac_converged`, as a share of JCAC's J:

  | cluster | `hpa_fair` | `keda_fair` | layered stack |
  |---|---:|---:|---:|
  | small | −15.0% | −13.9% | +132% |
  | medium | +1.3% | +5.0% | +187% |
  | large | +13.1% | +26.6% | +219% |

  The tuning slice covers 3 of the 5 workloads and 2 of the 4 mixes.
- **The structural-mismatch campaigns** (`PREREG_STRUCTURAL_MISMATCH`) were
  running while this was written; none of their results had been examined.

No run of the re-tuned arms on any campaign seed exists.

## Design (frozen)

`eval/experiments/matrix_retuned.yaml`:
- simulator backend, with the published economy and model forms;
- 120 steps, 5 reps, `shared_seeds: true`;
- a new experiment name, so all seeds are fresh;
- the headline cells: workloads `crud_bursty`, `crud_steady`,
  `ai_cacheable`, `ai_uncacheable`, `agentic`; mixes `uniform`,
  `premium_heavy`, `besteffort_heavy`, `whale`; cluster sizes `small`,
  `medium`, `large`.

That is 60 design points x 5 reps x 6 arms = 1,800 runs.

| arm | role |
|---|---|
| `jcac_converged` | **treatment** (as in PREREG_FAIR_J) |
| `hpa_fair_retuned` | RT-H1 comparator: `hpa_fair`, target_rho re-tuned per size |
| `keda_fair_retuned` | RT-H2 comparator: `keda_fair`, rps/replica re-tuned per size |
| `jcac_nojoint_v2_retuned` | RT-H3 comparator: the layered stack holding all three knobs, replica layer re-tuned per size |
| `hpa_fair`, `keda_fair` | the as-published comparators, reported beside (descriptive) |

**The re-tuning (frozen; committed with this file).**
- `eval/baselines/tune_arms.py` replays `sim_backend.execute`'s arm settings
  on `tune.py`'s slice, seeds, jitter and objective: controller, starting
  cache, miss-cost factor and parameters. It checks itself against
  `tune.score_combo` before running.
- It sweeps target_rho over 0.01–0.8 and rps/replica over 0.02–4.0, at each
  cluster size.
- It refuses any optimum on a grid edge, and writes the per-size optima to
  `eval/baselines/tuned_arms.yaml`. The `*_retuned` arms read only that file:

  | arm | small | medium | large |
  |---|---|---|---|
  | `hpa_fair_retuned` | ρ = 0.05 | ρ = 0.05 | ρ = 0.075 |
  | `keda_fair_retuned` | 0.15 rps | 0.15 rps | 0.15 rps |
  | `jcac_nojoint_v2_retuned` | ρ = 0.05 | ρ = 0.05 | ρ = 0.05 |

- The treatment is not re-tuned: it has no grid, and its weights are the
  objective.
- Per-size tuning of the comparators alone favours the comparators. That is
  deliberate: it is the strongest comparator the tuning protocol can produce.

## Tests (frozen)

The `PREREG_FAIR_J` rule, imported from its scorer:
- The design point is the unit: reps are averaged, and a design point is
  dropped listwise if any arm lacks a rep there.
- A win needs BOTH of these:
  - the one-sided Wilcoxon p is at or below its Holm threshold;
  - the 95% bootstrap CI of the mean J difference (10,000 design-point
    resamples, seed 20260926) lies entirely below 0.

- **Confirmatory, RT-H1..H3:** `jcac_converged` against each re-tuned
  comparator. One Holm family of 3, α = 0.05.
- **SLO guard:** per confirmatory comparator, `mean_excess`
  non-inferiority of the treatment (one-sided 95% bootstrap upper bound
  < 0.05).
- **Direction (a registered reading, not a test):** a comparison whose CI
  lies entirely above 0 is reported as "comparator better". A FAIL is never
  called a loss unless that holds.
- **Descriptive:**
  - per-cluster-size ΔJ with CIs for each primary comparator;
  - what re-tuning bought each comparator (re-tuned minus as-published);
  - the treatment against the as-published comparators;
  - per workload;
  - per-arm means, including the AI-shed share.

## Predictions

These are informed by the tuning slice above, which covers only part of the
matrix.

- **RT-H1** (re-tuned `hpa_fair`): **FAIL**. The FAIR_J tie was at ρ = 0.3,
  and re-tuning improves HPA by about 10% on the tuning slice.
- **RT-H2** (re-tuned `keda_fair`): **FAIL**, with low confidence. The
  FAIR_J margin (−7%) is smaller than what re-tuning buys KEDA on the tuning
  slice.
- **RT-H3** (re-tuned layered stack): **PASS**. On the tuning slice the
  stack stays about twice JCAC's J at every size.
- **Per size:** for both reactive comparators, "comparator better" on
  `small` and "treatment better" on `large`.

## What each outcome means (committed before the run)

- **RT-H1 or RT-H2 PASS:** the composite advantage over that reactive
  comparator survives the strongest tuning the protocol produces.
- **RT-H1 or RT-H2 FAIL:** no composite advantage over reactive replica
  control tuned to its optimum is claimed. If the direction is "comparator
  better", the comparator wins, and that is stated.
  - Any such claim must name the operating point. The re-tuned targets
    (ρ ≈ 0.05, ~20x headroom) are far outside production practice. A
    composite win over the *published* settings (reported beside) is a win
    over production-sane settings, not over the best the objective allows.
- **RT-H3 PASS:** the jointness result (FJ-H3) does not depend on the
  layered stack's replica target.
- **RT-H3 FAIL:** the jointness result depended on it.
- **The per-size split** decides where any claim may be scoped. A claim
  that holds only on some sizes is stated for those sizes only.

## Stopping rule

Same as `PREREG_FAIR_J`:
- one sitting;
- no re-run, widening, re-tuning, or change of arms after the first run;
- the harness's two transient-error retries are allowed;
- a run still failing is recorded, and its design point is dropped for every
  arm;
- a scorer defect found after the run is fixed in a new scorer and a new
  record.

`tuned_arms.yaml` is not regenerated after the first run.
