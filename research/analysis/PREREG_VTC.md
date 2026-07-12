# Pre-registration — VTC-replica fairness comparison (closing the RELATED_WORK gap)

Dated 2026-07-12 (session 15). Committed **before** any `vtc_fairness` run. Closes
the "no VTC-style empirical baseline" gap identified in `docs/RELATED_WORK.md` §3:
the V2_README target scoreboard names VTC's *empirical side* as the fairness
segment's published anchor, and until now the matrix carried no scheduler in VTC's
family.

## §1 The baseline (frozen before this document)

`vtc_replica` (`research/jcac_sim/baselines.py`): re-implementation of VTC's
mechanism (Sheng et al., OSDI '24 — serve the client with the smallest accumulated
weighted-service counter first) at simulator scale, the same reduction shape as
FIRM-replica: the fixed capacity being divided is the shared cluster replica pool,
service is executed work, weights are tenant budgets (the pricing analog of VTC's
client weights), moves obey the shared ±2 clamp and `apply_action` guardrails.
Cache and tier stay fixed — VTC allocates serving capacity and has no concept of
either. Tuned per the W34 protocol on the standard tuning slice
(`tune.py --only vtc_replica`, grid committed to `grids/vtc_replica.csv`; best
`target_rho = 0.3`, J = 0.555 — the same SLO-generous floor HPA tunes to), with
every previously committed tuned value untouched (merge, not resweep). Unit tests
pin the mechanism: least-served-first under contention, clamp compliance,
budget-weighted counter accrual.

## §2 Design

`eval/experiments/vtc_fairness.yaml`: systems `[jcac, vtc_replica]` on the exact
Phase 4 fairness slice — 5 workload classes × 4 tenant mixes × 5 reps, medium
cluster, 120 steps, **interference injection on**, all reps keep timeseries so
worst-tenant metrics are computable. 200 runs, output
`eval/results/vtc_fairness.duckdb`. Engine semantics otherwise identical to every
headline experiment.

## §3 Hypotheses (confirmatory), declared before any run

**HV1 (cost of fairness-only control):** paired by cell, `jcac` shows composite
J = cost_norm + 2·violation + 0.5·(1−Jain) significantly below `vtc_replica`
(p < 0.01). VTC's rule divides capacity fairly but sees neither dollars, nor the
cache, nor the tier; joint control should dominate the joint objective.

**HV2 (the risky one, stated symmetrically):** on Jain fairness and on
worst-tenant violation, `vtc_replica` does **not** beat `jcac` at p < 0.01 with
|d_z| ≥ 0.5. If dedicated token-fair division *does* deliver a large fairness win
over joint control, HV2 fails and the thesis quotes it as the honest boundary of
the fairness claim ("a dedicated fair scheduler buys more raw fairness; PolyForge
holds the joint objective") — that outcome is publishable and will be published.

**Declared exploratory:** whale-mix subgroup (where the interference gate fires);
worst-tenant p95; per-metric table vs the v1 gate thresholds; cost decomposition
of VTC's allocation posture.

## §4 Stopping rule

One run of the 200-cell set (crash retries only), one analysis pass
(`analysis_vtc.py`, committed in this same pre-registration commit, output
`VTC_FAIRNESS.md`), both verdicts published as measured. No re-tuning of either
system afterward; widening requires a new pre-registration file.
