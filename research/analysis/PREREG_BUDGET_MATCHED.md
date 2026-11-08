# Pre-registration: the audited comparators under the joint controller's budget (BM; 2026-11-09)

Registered 2026-11-09. Committed **before** any run of
`eval/experiments/matrix_budget_{jointness,trace}.yaml` (commit time is the
anchor; the commit stays local until the authors push the paper's branch).
Scorer: `analysis_budget_matched.py`, committed with this file, checked on a
synthetic frame, not edited after the run. Tests use the frozen
`analysis_fair_j.py` rule.

## Why this exists

In every audited campaign only the joint controller (JC) carries the
per-tenant dollar budget: it rejects any candidate whose step bill exceeds
`hourly_budget_usd × 10 s / 3600 s` before the objective is evaluated. The
comparators it is measured against (`hpa_fair_capped`, `keda_fair_capped`,
`layered_small_tuned`) have no budget at all. An external audit of the paper
(2026-11-09) asked for budget-constrained versions of them. `PREREG_BUDGET_PARITY`
already ran uncapped, untuned HPA and KEDA under the same rule (FJ-S1/S2 cite
its tuned twins); the audited, capped, re-tuned comparators have never been.

## The arms (`eval/harness/systems.py`)

| arm | is | budget rule |
|---|---|---|
| `hpa_budget_capped` | `hpa_fair_capped` + filter | `CapEnforced(BudgetFiltered(HPA))` |
| `keda_budget_capped` | `keda_fair_capped` + filter | `CapEnforced(BudgetFiltered(KEDA))` |
| `layered_small_budget` | `layered_small_tuned` + filter | `BudgetFiltered(LayeredSmall)` |

`BudgetFiltered` is `PREREG_BUDGET_PARITY`'s wrapper, unchanged: the same
per-step cap as JC; when the comparator's plan is over it, the affordable
state reachable this interval (±2 replicas, **keeping the comparator's cache
and tier**) closest to its plan, else the cheapest reachable. It moves only
replicas, so a comparator whose tier bill alone exceeds the cap still
overruns; JC's filter covers every knob. Each arm keeps its twin's registered
per-size tuning and is not re-tuned under the filter
(`eval/tests/test_budget_matched.py` pins both).

## What was already seen (disclosed)

All records to date, including JN (JN-B1 FAIL, JN-B4 FAIL, JN-B5 PASS), TA
(TA-H1..H3 FAIL, TA-H4 PASS), BUDGET_PARITY and FJ-S1/S2 (JC beats the
uncapped budget-filtered HPA and KEDA: PASS, PASS). Before registering, one
mechanism probe was run to check that the filter binds: the 60 main-matrix
cells, one seed (11, not a registered seed), 120 steps. It bound in 16 of 60
cells for each capped arm and 20 of 60 for the layered arm; mean over cells
for `hpa_budget_capped` vs `hpa_fair_capped`: overrun share 3.0% vs 3.7%,
cost $2.763 vs $2.785, violation 0.0639 vs 0.0540. No registered seed has
been run with these arms.

## Design

Two campaigns, each under a **registered experiment name**, so the shared
seeds give the new arms exactly the demand the registered JC runs saw. The
treatment and twin rows are the committed registered rows
(`metrics_matrix_jointness.csv.gz`, `metrics_trace_audited.csv.gz`); only the
three new arms are run, into new DBs.

| config | experiment | cells | runs |
|---|---|---|---:|
| `matrix_budget_jointness.yaml` | `matrix_jointness` | 60 design points × 5 reps × 120 steps | 900 |
| `matrix_budget_trace.yaml` | `matrix_trace_audited` | 96 BurstGPT windows × 1 rep × 2,159 steps | 288 |

**Validity gate (BM-V).** Every new run's seed must equal the registered seed
of its design point and rep; a mismatch voids that campaign's tests.

**Unit and score.** JN: the design point (reps averaged); TA: the window;
listwise complete. J per run = cost / (scored steps × 8) / $0.01 + 2·v̄ +
0.5·(1 − Jain), with each run's own scored steps, for every arm alike
(`COST_NORMALISATION.md`).

## Tests (one Holm family of 6)

| id | a | b | prediction |
|---|---|---|---|
| BM-H1[JN] | `jcac_converged` | `hpa_budget_capped` | PASS |
| BM-H2[JN] | `jcac_converged` | `keda_budget_capped` | PASS |
| BM-H3[JN] | `jcac_converged` | `layered_small_budget` | FAIL |
| BM-H1[TA] | `jcac_converged` | `hpa_budget_capped` | FAIL |
| BM-H2[TA] | `jcac_converged` | `keda_budget_capped` | FAIL |
| BM-H3[TA] | `jcac_converged` | `layered_small_budget` | FAIL |

PASS = one-sided Wilcoxon (ΔJ < 0) at its Holm threshold AND the 95%
bootstrap CI (10,000 resamples, seed 20260926) entirely below 0.

**Descriptive (no test):** per-arm J, cost, violation, excess, Jain, budget
overrun share and AI shed share, with `jcac_converged`, `jcac_tier_floor` and
the unbudgeted twins from the registered rows; the filter's effect on each
comparator (budgeted − unbudgeted ΔJ, Δcost, Δviolation, Δoverrun, with 95%
bootstrap CIs).

## Stopping rule

Each campaign runs once to completion (`retries: 2`); units missing a run for
any arm are dropped listwise. No rerun after scoring.
