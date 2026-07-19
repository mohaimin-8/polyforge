# End-to-end tenant scale — results (pre-registered, 32 and 64 tenants)

Protocol frozen in `PREREG_TENANT_SCALE.md` (pushed before any run). Per-tenant world identical to the 8-tenant matrix; cluster caps scaled linearly; monolithic planning, wall time recorded. Rerun: `python analysis_tenant_scale.py`.

## Per-system summary — 32 tenants

| system | J | total_cost_usd | mean_violation | mean_jain |
|---|---|---|---|---|
| PolyForge (anchored) | 0.3339 | 11.7 | 0.01182 | 0.994 |
| HPA | 0.561 | 19.64 | 0.01818 | 0.9819 |
| KEDA | 0.5618 | 21.38 | 0.0001798 | 0.9999 |
| Concurrency (KPA shape) | 0.5635 | 19.77 | 0.01772 | 0.9824 |

## Per-system summary — 64 tenants

| system | J | total_cost_usd | mean_violation | mean_jain |
|---|---|---|---|---|
| PolyForge (anchored) | 0.3134 | 23.83 | 0.0001863 | 0.9999 |
| HPA | 0.5288 | 36.31 | 0.02083 | 0.9792 |
| KEDA | 0.5266 | 40.11 | 0 | 1 |
| Concurrency (KPA shape) | 0.5307 | 36.61 | 0.01999 | 0.98 |

## TS-H1a (confirmatory) — 32 tenants

| baseline | n | J diff (jcac−base) | p | d_z | violation diff | violation p | verdict |
|---|---|---|---|---|---|---|---|
| HPA | 20 | -0.2271 | 4.062e-05 | -1.186 | -0.006359 | 0.3969 | PASS |
| KEDA | 20 | -0.2279 | 0.0002218 | -1.016 | 0.01164 | 0.01075 | PASS |
| Concurrency (KPA shape) | 20 | -0.2296 | 3.051e-05 | -1.215 | -0.005906 | 0.4244 | PASS |

**TS-H1a: PASS.**

## TS-H1b (confirmatory, large-effects power) — 64 tenants

| baseline | n | J diff (jcac−base) | p | d_z | violation diff | violation p | verdict |
|---|---|---|---|---|---|---|---|
| HPA | 10 | -0.2154 | 0.002388 | -1.321 | -0.02065 | 0.01631 | PASS |
| KEDA | 10 | -0.2132 | 0.01023 | -1.023 | 0.0001863 | 0.02131 | FAIL |
| Concurrency (KPA shape) | 10 | -0.2173 | 0.002172 | -1.341 | -0.0198 | 0.01635 | PASS |

**TS-H1b: FAIL.**

## TS-D1 (descriptive) — J margin vs portfolio width

| baseline | tenants | J diff | d_z | n |
|---|---|---|---|---|
| HPA | 8 (matrix_concurrency ref) | -0.154 | -0.983 | 300 |
| HPA | 32 | -0.2271 | -1.186 | 20 |
| HPA | 64 | -0.2154 | -1.321 | 10 |
| Concurrency (KPA shape) | 8 (matrix_concurrency ref) | -0.1493 | -0.9632 | 300 |
| Concurrency (KPA shape) | 32 | -0.2296 | -1.215 | 20 |
| Concurrency (KPA shape) | 64 | -0.2173 | -1.341 | 10 |

Cross-experiment reference (different seeds): trend description only, never a test (prereg §2).

## TS-D2 (descriptive) — fairness at width

| tenants | system | mean Jain | min Jain |
|---|---|---|---|
| 32 | PolyForge (anchored) | 0.994 | 0.9795 |
| 32 | HPA | 0.9819 | 0.9583 |
| 32 | KEDA | 0.9999 | 0.9991 |
| 32 | Concurrency (KPA shape) | 0.9824 | 0.9591 |
| 64 | PolyForge (anchored) | 0.9999 | 0.9997 |
| 64 | HPA | 0.9792 | 0.9583 |
| 64 | KEDA | 1 | 1 |
| 64 | Concurrency (KPA shape) | 0.98 | 0.9594 |

## Notes

- Stopping rule honored: one execution per experiment file, one analysis pass; 128+ tenants stays with the planner-cells program.
- Sim-backend decision quality; matrix substrate rules apply.
