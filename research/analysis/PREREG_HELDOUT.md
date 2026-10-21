# Pre-registration: the JOINTNESS comparisons on held-out, non-periodic workloads (HO; 2026-10-16)

Registered 2026-10-16. Committed and pushed **before** any run of
`eval/experiments/matrix_heldout.yaml`; the push event is the timestamp anchor,
checked by `scripts/check_prereg_timing.py`. Scorer: `analysis_heldout.py`,
committed with this file, tested on synthetic matrices (`test_heldout.py`), not
edited after the run. It imports its test from the frozen `analysis_fair_j.py`.

## Why this exists

A statistics review (2026-10-16) found that the post-audit "confirmatory"
campaigns re-measure the same 60 design points the exploratory readings came
from: with an intraclass correlation near 0.98, fresh seeds barely change a
result, so a registered prediction there is close to foregone. The fix it named
is new design points registered before any run. It also found that every matrix
workload is a deterministic periodic curve — the shape a forecasting controller is
built to exploit. This campaign asks whether the JOINTNESS verdicts hold on
demand no tuning slice, campaign or probe has seen, and that has no period.

## The held-out workloads (frozen; `eval/harness/workloads.py`)

- **`mmpp_crud`**: crud_read 25, crud_write 7, batch 1.0 rps at scale 1; CRUD base
  45 ms.
- **`mmpp_mixed`**: chat 3, embed 2, agent 0.8, crud_read 8, crud_write 2 rps; CRUD
  base 55 ms.
- **Shape `mmpp`**: per tenant, a two-state Markov chain over control steps — quiet
  at 0.4×, burst at 2.5× (the `bursty` class's peak) — entering a burst with
  probability 0.08 per step and leaving with 0.25 (mean burst 4 steps, mean gap
  12.5, burst share 0.242, mean level 0.91×), times mean-one lognormal noise
  (σ = 0.15). It is drawn from its own stream keyed by (seed, tenant), so no other
  class's demand changes (golden hashes pass).
- `test_heldout_workloads.py` pins: deterministic in (seed, tenant); designed
  level and burst share; no autocorrelation above 0.1 at any lag 8–48 (no cycle
  for the seasonal detector); and no other experiment YAML or tuning cell uses
  either class.

## What was already seen (disclosed)

- Every result of JOINTNESS, ACTUATION and QUALITY.
- The generator's own statistics: mean level 0.912 and burst share 0.244 over 200
  seeds, computed while writing it.

**No run of any arm on either held-out workload exists.** No comparator was
tuned on them.

## Design (frozen)

`eval/experiments/matrix_heldout.yaml`: simulator, published economy and model
forms, instant actuation (as JOINTNESS), 120 steps, 5 reps, `shared_seeds: true`,
a new experiment name (fresh seeds); workloads `mmpp_crud`, `mmpp_mixed`; mixes
`uniform`, `premium_heavy`, `besteffort_heavy`, `whale`; cluster sizes `small`,
`medium`, `large`. That is 24 design points × 5 reps × 7 arms = 840 runs.

| arm | role |
|---|---|
| `jcac_converged` | **treatment** |
| `jcac_decoupled` | HO-C1 (replicates JN-C1) |
| `layered_small_tuned` | HO-B1 (JN-B1) |
| `static_share` | HO-B3 (JN-B3) |
| `hpa_fair_capped` | HO-B4 (JN-B4) |
| `keda_fair_capped` | HO-B5 (JN-B5) |
| `jcac_tier_floor` | descriptive: the treatment that never sheds AI |

Arms, parameters and tuning are PREREG_JOINTNESS's, unchanged. Nothing is
re-tuned for the new workloads: generalising to them is the question.

## Tests (frozen)

- The PREREG_FAIR_J rule: the design point is the unit (listwise completeness);
  a win needs the one-sided Wilcoxon p at or below its Holm threshold AND the 95%
  bootstrap CI of the mean ΔJ (10,000 design-point resamples, seed 20260926)
  entirely below 0.
- **Confirmatory, HO-C1, HO-B1, HO-B3, HO-B4, HO-B5:** the treatment against each.
  One Holm family of 5, α = 0.05. With 24 design points the smallest detectable
  difference is larger than JOINTNESS's: at power 0.8, using JOINTNESS's
  design-point spread, 0.037–0.059 J against the four baselines and 0.010–0.013 J
  against the decoupled search (α 0.05 to 0.01). A FAIL against a baseline is
  therefore weak evidence of no difference, and is read that way.
- **Coordination equivalence (HO-C1-EQ):** EQUIVALENT when the 95% CI of the mean
  ΔJ lies strictly inside ±0.025.
- **SLO guard:** `mean_excess` non-inferiority (one-sided 95% bound < 0.05).
- **Direction (registered reading):** a CI entirely above 0 is "comparator
  better"; a FAIL is never called a loss unless that holds.
- **Descriptive:** per-arm means and guards; the never-shedding treatment against
  the treatment, capped HPA and the no-tier layered stack; every comparison per
  workload and cluster size.

## Predictions

The JOINTNESS verdicts, each with low confidence: HO-C1 PASS and EQUIVALENT;
HO-B1 FAIL; HO-B3 PASS; HO-B4 FAIL; HO-B5 PASS. On demand with no period, the
treatment's forecaster has nothing to lock onto, so every margin may shrink.

## What each outcome means (committed before the run)

- **A verdict that matches JOINTNESS:** that finding holds on unseen, non-periodic
  demand and may be stated without the "on the periodic matrix" qualifier.
- **A verdict that differs:** that JOINTNESS comparison depends on the matrix's
  periodic workloads, and any claim from it is scoped to them.
- **HO-C1 EQUIVALENT:** coordination between one tenant's knobs is practically
  negligible on held-out demand as well.

## Stopping rule

As `PREREG_FAIR_J`: one sitting; no re-run, widening, re-tuning or change of arms
or workloads after the first run; two transient-error retries; a failing run is
recorded and its design point dropped for every arm; a scorer defect found after
the run is fixed in a new scorer and a new record.

## Status note (2026-10-22, appended after the registered text)

The sitting started 2026-10-16 12:51 UTC and stopped at about 125 of 840 runs
when the operator's session ended; no run had failed and nothing was changed.
It was resumed on 2026-10-22 with the harness's resume rule: runs already
recorded valid are skipped, so every remaining run executes once, exactly as
registered. No arm, workload, seed, parameter or scorer was changed, and no
result was looked at in between.
