# VTC-replica fairness comparison — results (pre-registered)

Source: `eval/results/vtc_fairness.duckdb` (200 valid runs): `jcac` vs the tuned `vtc_replica` (least-weighted-service-first pool division, target_rho=0.3 per grids/vtc_replica.csv), Phase 4 fairness slice, interference injection on. Protocol frozen in `PREREG_VTC.md` before the run; analysis executed once. Regenerate: `python analysis_vtc.py`.

## HV1 (confirmatory) — the joint objective

J (jcac) = 0.4006 vs J (vtc_replica) = 0.5648; paired diff -0.1641, p = 3.27e-19, d_z = -1.116 (n = 100).

**HV1: PASS** — reported as measured.

## HV2 (confirmatory, symmetric) — does dedicated fair division buy a large fairness win?

| metric | jcac | vtc_replica | diff (jcac−vtc) | p | d_z | VTC large-effect win |
|---|---|---|---|---|---|---|
| Jain fairness | 0.9705 | 0.9599 | 0.01059 | 1.495e-06 | 0.5122 | no |
| worst-tenant violation | 0.1433 | 0.1557 | -0.01238 | 0.2415 | -0.1178 | no |

**HV2: PASS** — dedicated token-fair division does not deliver a large fairness advantage over joint control that carries fairness as one objective term.

## Full per-metric table (paired by cell)

| metric | jcac | vtc_replica | diff (jcac−vtc) | p | d_z |
|---|---|---|---|---|---|
| cost (USD/run) | 2.403 | 3.719 | -1.316 | 1.235e-10 | -0.7192 |
| SLO violation | 0.06674 | 0.07703 | -0.01029 | 0.02454 | -0.2283 |
| violation incidence | 0.1958 | 0.1845 | 0.0113 | 0.2952 | 0.1052 |
| Jain fairness | 0.9705 | 0.9599 | 0.01059 | 1.495e-06 | 0.5122 |
| cache hit rate | 0.1072 | 0.0582 | 0.04901 | 8.317e-16 | 0.9596 |
| J | 0.4006 | 0.5648 | -0.1641 | 3.272e-19 | -1.116 |

## Worst-tenant metrics (per-tenant timeseries, all reps)

| metric | jcac | vtc_replica | diff (jcac−vtc) | p | d_z |
|---|---|---|---|---|---|
| worst_tenant_p95_ms | 2.95e+04 | 4.641e+04 | -1.69e+04 | 0.01204 | -0.2558 |
| worst_tenant_violation | 0.1433 | 0.1557 | -0.01238 | 0.2415 | -0.1178 |

## Exploratory (declared) — whale cells only (where injection fires)

| metric | jcac | vtc_replica | diff (jcac−vtc) | p | d_z |
|---|---|---|---|---|---|
| cost (USD/run) | 2.427 | 4.141 | -1.714 | 0.0002742 | -0.8515 |
| SLO violation | 0.05001 | 0.05538 | -0.005365 | 0.384 | -0.1774 |
| violation incidence | 0.1343 | 0.1374 | -0.003125 | 0.8564 | -0.03658 |
| Jain fairness | 0.972 | 0.9649 | 0.007043 | 0.07994 | 0.3657 |
| cache hit rate | 0.1114 | 0.05818 | 0.05319 | 0.0001547 | 0.8966 |
| J | 0.369 | 0.5633 | -0.1943 | 7.586e-06 | -1.135 |

| metric | jcac | vtc_replica | diff (jcac−vtc) | p | d_z |
|---|---|---|---|---|---|
| worst_tenant_p95_ms | 4.244e+04 | 6.935e+04 | -2.691e+04 | 0.1281 | -0.3152 |
| worst_tenant_violation | 0.1446 | 0.152 | -0.007345 | 0.6951 | -0.07933 |

## Notes

- `vtc_replica` is a reduction of VTC's scheduler to the replica knob (PREREG_VTC §1), the same shape as FIRM-replica; VTC's own 2× service bound applies to its token-level scheduler and is not claimed or tested here.
- Tuned per TUNING.md on the standard slice; every previously committed tuned value untouched (merge, not resweep).
