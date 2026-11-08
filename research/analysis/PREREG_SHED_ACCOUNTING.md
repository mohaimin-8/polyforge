# Protocol: how many requests does shedding drop? (SA; 2026-11-09)

Registered 2026-11-09. Committed **before** any run of
`eval/experiments/matrix_shed_{jointness,trace}.yaml` (commit time is the
anchor; the commit stays local until the authors push the paper's branch).
Scorer: `analysis_shed_accounting.py`, committed with this file, not edited
after the run. **Descriptive: no hypothesis is tested and no verdict is
issued.**

## Why this exists

Every record reports shedding as `tier_none_step_share`, the share of
tenant-steps whose model tier is "none" (JN: 7.2% for `jcac_converged`; TA:
8.3%). That counts steps, not requests: it does not say how many AI requests
went unanswered, nor what the remaining cost buys. An external audit of the
paper (2026-11-09) asked for both. The simulator now counts them
(`eval/tests/test_shed_accounting.py`): per tenant-step, the requests offered,
the AI requests among them, and the AI requests answered by no model (cache
misses on a step whose tier is "none"; a cache hit is answered by a stored
answer, and CRUD is always served). The counters are reporting-only: they
enter no objective, plan or score.

## What was already seen (disclosed)

All records to date, including the step-level shed shares above. No
request-level count has been computed before.

## Design

Two re-measurements of registered runs. Each YAML keeps the registered
experiment name, so run ids and seeds are the registered ones and every arm
replays the same demand; only the output DB is new.

| config | experiment (registered under) | arms | runs |
|---|---|---|---:|
| `matrix_shed_jointness.yaml` | `matrix_jointness` (`PREREG_JOINTNESS.md`) | `jcac_converged`, `jcac_decoupled`, `jcac_tier_floor`, `hpa_fair_capped`, `keda_fair_capped`, `layered_small_tuned`, `static_share` | 2,100 |
| `matrix_shed_trace.yaml` | `matrix_trace_audited` (`PREREG_TRACE_AUDITED.md`) | the same seven | 672 |

The JN run uses 60 design points × 5 reps × 120 steps; the TA run uses 96
BurstGPT windows × 1 rep × 2,159 steps, published plant.

## Validity gate (SA-V), checked before anything is reported

For every rerun, each registered metric (`total_cost_usd`, `mean_violation`,
`violation_step_share`, `mean_jain`, `cache_hit_rate`, `crud_p95_ms`,
`ai_p95_ms`, `mean_excess`, `tier_none_step_share`, `steps`) must equal the
committed row of the same `run_id` exactly (`metrics_matrix_jointness.csv.gz`,
`metrics_trace_audited.csv.gz`, read round-trip). Any mismatch means the
counters changed what ran: the record then reports the mismatches and quotes
no counter from that campaign.

## Reported (descriptive)

Per arm and campaign, means over the design point (JN; reps averaged) or the
window (TA):

- AI requests shed / AI requests offered (`ai_shed_request_share`);
- AI requests shed / all requests offered (`shed_request_share`);
- total cost / requests served, in $ per 1,000 served requests
  (`cost_per_served_request`), beside the total cost the records report;
- the step-level `tier_none_step_share`, for comparison.

For `jcac_converged` also: the AI-request shed share per workload class (JN)
and its median and maximum over design points and windows.

## Stopping rule

Each rerun runs once to completion (`retries: 2`). No rerun after scoring.
