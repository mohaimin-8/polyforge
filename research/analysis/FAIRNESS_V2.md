# Fairness γ-ablation under interference injection (v2 Phase 4)

Source: `eval/results/fairness_v2.duckdb` (200 valid runs): `jcac` (γ=0.5) vs `jcac_nofairness` (γ=0), medium cluster, 5 workloads × 4 tenant mixes × 5 reps, noisy-neighbor interference injected (executed-work share > 2× fair share ⇒ victims lose up to 50% serving capacity; the controller sees the observable detector score). This is the signal v1 documented as absent (RESULTS.md Notes) — constants are fixed by the W32 signal shape, not tuned.

## Headline metrics (paired by cell, removal = γ=0 minus full)

| metric | full (γ=0.5) | ablated (γ=0) | removal change | p | d_z (removal) | removal hurts | significant (p<0.01) |
|---|---|---|---|---|---|---|---|
| cost (USD/run) | 2.395 | 2.401 | +0.2% | 0.4353 | 0.07833 | True | False |
| SLO violation | 0.0684 | 0.06874 | +0.5% | 0.8162 | 0.02331 | True | False |
| violation incidence | 0.1976 | 0.1988 | +0.6% | 0.4668 | 0.07306 | True | False |
| Jain fairness | 0.9689 | 0.9692 | +0.0% | 0.8563 | 0.01816 | False | False |
| cache hit rate | 0.1076 | 0.1068 | -0.7% | 0.3519 | -0.09352 | True | False |

## Worst-tenant metrics (from per-tenant timeseries, all reps)

The γ-term's job is bounding how bad the *worst* tenant's experience gets while a neighbor bursts; cluster-mean metrics can hide that entirely.

| metric | full (γ=0.5) | ablated (γ=0) | removal change | p | d_z (removal) | removal hurts | significant (p<0.01) |
|---|---|---|---|---|---|---|---|
| worst_tenant_p95_ms | 5.077e+04 | 5.014e+04 | -1.2% | 0.9376 | -0.007852 | False | False |
| worst_tenant_violation | 0.1471 | 0.142 | -3.5% | 0.4215 | -0.08072 | False | False |

## Exploratory subgroup — whale cells only (where injection fires)

Not pre-registered. The 2×-fair-share gate only trips under the `whale` mix; the other three mixes inject nothing and dilute the aggregate. This isolates the 25 cells where the γ-term's signal is present.

| metric | full (γ=0.5) | ablated (γ=0) | removal change | p | d_z (removal) | removal hurts | significant (p<0.01) |
|---|---|---|---|---|---|---|---|
| cost (USD/run) | 2.419 | 2.437 | +0.7% | 0.16 | 0.29 | True | False |
| SLO violation | 0.05288 | 0.04996 | -5.5% | 0.3297 | -0.199 | False | False |
| violation incidence | 0.1394 | 0.1321 | -5.2% | 0.1068 | -0.3351 | False | False |
| Jain fairness | 0.9705 | 0.9729 | +0.2% | 0.4581 | 0.1508 | False | False |
| cache hit rate | 0.112 | 0.11 | -1.7% | 0.2383 | -0.2419 | True | False |

| metric | full (γ=0.5) | ablated (γ=0) | removal change | p | d_z (removal) | removal hurts | significant (p<0.01) |
|---|---|---|---|---|---|---|---|
| worst_tenant_p95_ms | 5.264e+04 | 4.998e+04 | -5.1% | 0.3931 | -0.1739 | False | False |
| worst_tenant_violation | 0.1329 | 0.1234 | -7.2% | 0.4953 | -0.1385 | False | False |

## Verdict

**Negative result, reported as measured**: even under injected interference the γ-term shows no significant contribution at p<0.01. Per V2_README Phase 4 acceptance, the term is dropped from the paper's claims (it remains in the system as a configurable weight); the segment is resolved either way.

Interference constants (gate 2× fair share, max 50% capacity cut) are committed in `research/jcac_sim/simulate.py`; they were fixed before this experiment ran and not swept.
