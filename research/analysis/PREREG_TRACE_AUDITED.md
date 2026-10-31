# Pre-registration: real BurstGPT demand against the audited comparators (TA; 2026-10-31)

Registered 2026-10-31. Committed **before** any run of
`eval/experiments/matrix_trace_audited.yaml` (commit time is the anchor; the
commit is local and is pushed with the paper when the authors decide, because
the paper is under double-blind review). Scorer: `analysis_trace_audited.py`,
committed with this file, checked on a synthetic frame, not edited after the
run. Every test uses the frozen `analysis_fair_j.py` rule.

## Why this exists

A second external review of the paper (2026-10-31) noted that every audited
campaign uses synthetic demand, and that the real-trace replays (TRACE, TRACE2,
TRACE_AZURE, TRACE_PARITY) ran against comparators that predate the
2026-09-26 audit. TRACE_PARITY removed the eviction surcharge and the 128 MB
pin, but not the later fixes: per-size re-tuning, the cluster caps, the
convergent solver, the cheapest-tier stack and the decoupled ablation. This
campaign replays the same real windows against the audited arms.

## What was already seen (disclosed)

All records to date, in particular: TRACE2 (96 windows; its cost headline
was withdrawn); TRACE_PARITY (against fair HPA/KEDA, JC was 52% cheaper,
TP-H1 failed at its Holm threshold, and TP-H3 failed: JC overshot heavily in
the high-demand windows; mean J 0.386 for JC against 0.403 for fair HPA,
descriptive); the audited synthetic campaigns FAIR_J..WIDTH; and the reviewer
follow-up RV (2026-10-29). One timing smoke ran window `burstgpt96_w00` × the
seven arms on a scratch database; only its validity report was read. An
earlier smoke attempt was rejected by the runner's CRUD-latency validity check
(the demand is chat-only), which led to the runner change below. The code
reviewer ran a further validation smoke (two windows × three arms, scratch
database) and reported only validity fields (latencies non-zero where
expected, steps scored).

## Substrate

- **Demand.** The 96 six-hour windows of PREREG_TRACE2 (two coverage segments
  × 48), projected by the committed `trace_matrix.window_buckets` at scale
  k = 75.1 (600 s rate buckets, eight tenants, chat only). The projections are
  committed as `eval/harness/trace_cache/burstgpt96_w*.json.gz` and checked
  against the raw trace by `eval/tests/test_trace_workloads.py`.
- **Runs.** 2,159 scored ten-second steps (the whole window), one rep per
  window, Poisson arrival jitter from the run seed, seeds shared across arms.
- **Tenants.** The `uniform` mix: eight standard-SLO tenants with a $5/h
  budget each. TRACE2 cycled SLO classes; this campaign uses the main matrix's
  uniform tenants instead.
- **Cluster.** `medium`: 4,096 MB of cache, 48 replicas, per-tenant cap 10
  (TRACE2's limits plus the audited per-tenant cap). Published plant.
- **Arms**, each built by `sim_backend.execute` exactly as in FAIR_J..WIDTH:
  `jcac_converged`, `jcac_decoupled`, `jcac_tier_floor` (never sheds),
  `hpa_fair_capped` and `keda_fair_capped` (re-tuned for the medium size,
  capped), `layered_small_tuned` (the cheapest-tier stack), `static_share`.
- **Harness changes** (additive; every published run id is unchanged): trace
  windows become workloads (`eval/harness/trace_workloads.py`,
  `workloads.build`, the workload check in `config.py`), and the runner skips
  its CRUD-latency validity check for them, as it already does for live trace
  runs.

## Unit, score and tests

**Unit:** the window (n = 96). A window counts only if every arm has a valid
run there.

**Score:** per run, J = cost / (scored steps × 8) / $0.01 + 2·v̄ + 0.5·(1 − Jain),
with the run's own scored steps (2,159). This is the formula of
`trace_matrix.run_one`. The matrix scorer's `composite_objective` assumes 119
scored steps, so it is not used here.

**PASS** = one-sided Wilcoxon (ΔJ = J_a − J_b < 0) at its Holm threshold
**and** a 95% bootstrap CI of the mean ΔJ (10,000 resamples) entirely below 0.
Predictions follow the audited synthetic results (JN-B4 FAIL, JN-B5 PASS,
JN-B1 FAIL, JN-B3 PASS); a FAIL is a result.

| id | a | b | prediction |
|---|---|---|---|
| TA-H1 | `jcac_converged` | `hpa_fair_capped` | FAIL |
| TA-H2 | `jcac_converged` | `keda_fair_capped` | PASS |
| TA-H3 | `jcac_converged` | `layered_small_tuned` | FAIL |
| TA-H4 | `jcac_converged` | `static_share` | PASS |

Holm family: {TA-H1..H4}, α = 0.05.

**Equivalence (TA-EQ):** `jcac_converged` − `jcac_decoupled` is EQUIVALENT if
its 95% CI lies inside ±0.025 J. Prediction: EQUIVALENT.

**Guard (reported with every superiority test, no verdict):** the mean
difference in `mean_excess` (JC − comparator) with its 95% CI, and each arm's
AI shed step share. A PASS bought with higher excess is reported as a
cost-for-SLO trade, as RT-H1 was.

**Descriptive (no test):** per-arm means of J, cost, violation, excess, Jain
and shed share; `jcac_tier_floor` − `jcac_converged`; `jcac_tier_floor` −
`hpa_fair_capped`; and the four primary contrasts split by coverage segment
(windows 0–47 and 48–95).

## Stopping rule

The campaign runs once to completion (`retries: 2`); windows missing an arm
are dropped listwise. No rerun after scoring. Whatever the outcome, the
paper's sentence "Earlier real-trace replays ran only against pre-audit
comparators" is replaced by this result.
