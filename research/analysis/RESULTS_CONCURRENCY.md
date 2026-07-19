# Concurrency/queue-depth baseline — results (pre-registered)

Protocol frozen in `PREREG_CONCURRENCY.md` (pushed before the tuning sweep or any matrix run). The 2026 serving-stack reactive arm (Knative-KPA / AIBrix shape: in-flight-work signal, stable-window scale-down), grid-tuned on the paper's own J per the W34 protocol (`grids/concurrency.csv`; winner c_t=0.5, stable window 6 intervals — tuning-slice J 0.543, *stronger* than tuned HPA 0.555 and KEDA 0.573, so the arm is not a straw man). 900-run matrix, matched-cells pairing. Rerun: `python analysis_concurrency.py`.

## Per-system summary (mean over the 300 matrix cells)

| system | J | total_cost_usd | mean_violation | mean_jain |
|---|---|---|---|---|
| PolyForge (anchored) | 0.4006 | 2.362 | 0.06843 | 0.9688 |
| Concurrency (KPA/AIBrix shape) | 0.5499 | 3.756 | 0.06889 | 0.9648 |
| HPA | 0.5546 | 3.708 | 0.07324 | 0.9628 |

## CQ-H1 / CQ-H2 (confirmatory) — jcac_anchored vs tuned concurrency

| reading | n | diff (jcac−conc) | p | d_z | verdict |
|---|---|---|---|---|---|
| CQ-H1: composite J | 300 | -0.1493 | 1.255e-44 | -0.9632 | PASS |
| CQ-H2: cost (USD) | 300 | -1.393 | 7.674e-31 | -0.7491 | PASS |
| violation (disclosure rule) | 300 | -0.0004624 | 0.8794 | -0.008765 | no large-effect regression |

**CQ-H1: PASS** — the joint controller beats the tuned 2026-stack reactive arm on the composite objective.
**CQ-H2: PASS.**

## CQ-D1 (descriptive, no gate) — the modern arm vs tuned HPA

| metric | concurrency mean | hpa mean | diff (conc−hpa) | p | d_z |
|---|---|---|---|---|---|
| J | 0.5499 | 0.5546 | -0.004735 | 5.735e-07 | -0.2951 |
| cost | 3.756 | 3.708 | 0.04744 | 6.033e-31 | 0.7508 |
| violation | 0.06889 | 0.07324 | -0.00435 | 1.415e-17 | -0.5248 |

Per workload class (J diff, concurrency − hpa):

| class | J diff | p | d_z | n |
|---|---|---|---|---|
| agentic | -0.02674 | 4.792e-13 | -1.191 | 60 |
| ai_cacheable | 0.0005105 | 0.4132 | 0.1064 | 60 |
| ai_uncacheable | -0.0008161 | 0.5896 | -0.07002 | 60 |
| crud_bursty | 0.003817 | 2.781e-11 | 1.055 | 60 |
| crud_steady | -0.0004451 | 3.239e-38 | -4.003 | 60 |

## Notes

- Same substrate rules as every matrix campaign: sim-backend decision quality, blocked-factorial matched cells, never mixed with replay tables (ground rules 3-4, 6).
- Stopping rule §5 honored: one tuning sweep, one matrix execution, one analysis pass; SLO confirmatory attempts remain closed (v3 rule).
