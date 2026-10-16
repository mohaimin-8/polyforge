# Pre-registration: the post-audit comparisons under reconfiguration realism (AC; 2026-10-16)

Registered 2026-10-16, together with `PREREG_JOINTNESS.md` and before any run
of either. Committed and pushed **before** any run of
`eval/experiments/matrix_actuation.yaml`; the push event is the timestamp
anchor, checked by `scripts/check_prereg_timing.py`. Scorer:
`analysis_actuation.py`, committed with this file, tested on synthetic
matrices (`test_actuation.py`), not edited after the run. It imports its test
from the frozen `analysis_fair_j.py`.

## Why this exists

Every post-audit matrix (FAIR_J, RETUNED, the structural-mismatch campaigns,
JOINTNESS) actuates instantly: a replica added this interval serves this
interval, a grown cache serves full-warm, and a tier switch is free. A final
review (2026-10-16) rated this HIGH: a forecasting controller and a reactive
one are hurt differently by actuation lag, so a ranking measured without lag
may not survive it. The simulator's reconfiguration-realism mode
(`simulate.effective_state`, `transition_costs: true`) models the part of this
it can: replicas added in an interval serve one interval later (10 s), a
grown cache serves half-warm for one interval, shrinking is instant, and
billing follows the nominal configuration at once. The only campaign that
used it (`realism.yaml`, 175 runs, one mix, one size) predates the audit and
compared the pre-audit comparators.

What this mode does NOT model, and so this campaign cannot speak to: tier
switches and model loads are still instant and free; there is no queue that
carries a backlog across intervals.

## What was already seen (disclosed)

Everything `PREREG_JOINTNESS.md` lists. No run of any arm below with
`transition_costs` on exists; JOINTNESS has not been run either.

## Design (frozen)

`eval/experiments/matrix_actuation.yaml`: simulator backend, published
economy and model forms, `transition_costs: true`, 120 steps, 5 reps,
`shared_seeds: true`, a new experiment name (fresh seeds), the headline cells
(5 workloads x 4 mixes x 3 cluster sizes = 60 design points). 6 arms, 1,800
runs.

| arm | role |
|---|---|
| `jcac_converged` | **treatment** |
| `layered_small_tuned` | AC-H1 |
| `static_share` | AC-H2 |
| `hpa_fair_capped` | AC-H3 |
| `keda_fair_capped` | AC-H4 |
| `jcac_tier_floor` | descriptive: the treatment that never sheds AI |

The arms, their parameters and their tuning are `PREREG_JOINTNESS`'s,
unchanged. Nothing is re-tuned under lag: the comparators keep the optima
found without lag and the treatment has no grid, so neither side is fitted to
the lagged plant.

## Tests (frozen)

The `PREREG_FAIR_J` rule, imported from its scorer: the design point is the
unit (reps averaged, listwise completeness); a win needs the one-sided
Wilcoxon p at or below its Holm threshold AND the 95% bootstrap CI of the
mean J difference (10,000 design-point resamples, seed 20260926) entirely
below 0.

- **Confirmatory, AC-H1..H4:** `jcac_converged` against each comparator. One
  Holm family of 4, α = 0.05.
- **SLO guard:** per comparator, `mean_excess` non-inferiority of the
  treatment (one-sided 95% bootstrap upper bound < 0.05).
- **Direction (a registered reading, not a test):** a CI entirely above 0 is
  "comparator better"; a FAIL is never called a loss unless that holds.
- **Descriptive:** per-arm means and guard metrics; the never-shedding arm
  against each comparator and against the treatment; per cluster size; per
  workload.

## Predictions

- **AC-H3, AC-H4 (capped HPA / KEDA): PASS, low confidence.** Reactive
  scalers buy replicas that arrive after the burst they were bought for; the
  treatment plans on a forecast.
- **AC-H1 (layered, no tier layer): PASS, low confidence**, for the same
  reason: its replica layer is HPA's.
- **AC-H2 (static share): FAIL.** A configuration that never moves pays no
  lag; the treatment does.

## What each outcome means (committed before the run)

- A JOINTNESS confirmatory verdict that differs here (PASS there, FAIL here,
  or the reverse) means that comparison depends on instant actuation, and any
  claim from it is scoped to it.
- AC-H3/AC-H4 PASS where JN-B4/JN-B5 FAIL: under one-interval actuation lag
  the treatment beats capped reactive replica control on J, quoted with its
  CI and with this mode's limits (no tier-switch lag, no backlog).
- AC-H2 FAIL: control does not beat a static equal-share configuration once
  actuation lags; no value-of-control claim is made under lag.

## Stopping rule

Same as `PREREG_FAIR_J`: one sitting; no re-run, widening, re-tuning, or
change of arms after the first run; the harness's two transient-error retries
are allowed; a run still failing is recorded and its design point dropped for
every arm; a scorer defect found after the run is fixed in a new scorer and a
new record.
