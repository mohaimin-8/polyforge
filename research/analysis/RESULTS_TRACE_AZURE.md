# Replay on real Azure LLM 2024 demand — results (pre-registered, second real trace)

Source: `azure_llm_2024.csv.gz` (44,107,694 real requests, two production Azure LLM services, 216 h contiguous), 72 windows x 3 h tiling the trace exactly; demand scale k = 0.705; jitter seeds 3000+i. Protocol frozen in `PREREG_TRACE_AZURE.md` (pushed before any run). Round-robin pseudo-tenantization within each real stream, disclosed in §2: within-stream pseudo-tenants are nearly perfectly demand-correlated (the harder packing regime). Never pooled with the BurstGPT samples (ground rules 3-4). Rerun analysis: `python trace_matrix_azure.py --analyze`.

## Per-system summary (mean over windows)

| system | total_cost_usd | mean_violation | violation_step_share | mean_jain | cache_hit_rate | J |
|---|---|---|---|---|---|---|
| PolyForge (v1 trend) | 34.46 | 0.01929 | 0.02737 | 0.9855 | 0.2182 | 0.445 |
| PolyForge v2 (Holt) | 35.99 | 0.006303 | 0.01032 | 0.9961 | 0.1827 | 0.4315 |
| HPA | 59.92 | 0 | 0 | 1 | 0.1133 | 0.6942 |
| KEDA | 59.37 | 9.039e-07 | 3.218e-06 | 1 | 0.1133 | 0.6877 |
| FIRM | 59.97 | 0.01206 | 0.01485 | 0.9888 | 0.1133 | 0.7245 |

## HT-AZ (confirmatory, PREREG_TRACE_AZURE §3) — composite J, paired by window

| baseline | n | J diff (jcac−base) | p | d_z | cost diff (USD) | cost p | violation diff | violation p | verdict |
|---|---|---|---|---|---|---|---|---|---|
| HPA | 72 | -0.2492 | 5.304e-22 | -1.642 | -25.47 | 3.007e-17 | 0.01929 | 1.813e-05 | PASS |
| KEDA | 72 | -0.2428 | 5.291e-22 | -1.642 | -24.91 | 3.696e-17 | 0.01929 | 1.815e-05 | PASS |
| FIRM | 72 | -0.2795 | 1.025e-24 | -1.845 | -25.52 | 4.148e-19 | 0.007228 | 0.05212 | PASS |

**HT-AZ: PASS with disclosure** — the J wins vs HPA, KEDA come with a large-effect violation regression (p<0.01, |d_z|≥0.5): the same attainment-for-cost trade as on BurstGPT, stated prominently per §3.

## Declared secondary (estimates, not gates)

| baseline | stratum | n | J diff | cost diff (USD) |
|---|---|---|---|---|
| HPA | conv-dominant windows | 36 | -0.2238 | -20.43 |
| HPA | code-dominant windows | 36 | -0.2746 | -30.5 |
| KEDA | conv-dominant windows | 36 | -0.2183 | -19.95 |
| KEDA | code-dominant windows | 36 | -0.2673 | -29.87 |
| FIRM | conv-dominant windows | 36 | -0.2549 | -20.83 |
| FIRM | code-dominant windows | 36 | -0.3041 | -30.21 |

ET-AZ1 (`jcac_v2` − `jcac` on J): diff -0.01345, p=0.00393, d_z=-0.351.

## Notes

- Same substrate rules as the BurstGPT replays: model-scale dollars, the ranking is the claim, tables never mixed across substrates.
- Latency metrics are p95, not p99 (documented deviation).
- 3 h windows cannot contain a daily cycle, so `jcac_seasonal` is structurally excluded (§2); its Azure evidence is `FORECAST_AZURE.md`.
