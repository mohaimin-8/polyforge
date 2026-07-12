# Powered replay on real BurstGPT demand — results (pre-registered, second sample)

Source: `burstgpt_real.csv.gz` (10,632,194 real requests), 2 contiguous segments, 96 windows x 6 h, demand scale k = 75.1; independent jitter seeds (2000+i). Protocol frozen in `PREREG_TRACE2.md` before the run; the first sample (`RESULTS_TRACE.md`, n=16) stands as measured and is never pooled with this one. Rerun analysis: `python trace_matrix2.py --analyze`.

## Per-system summary (mean over windows)

| system | total_cost_usd | mean_violation | violation_step_share | mean_jain | cache_hit_rate | J |
|---|---|---|---|---|---|---|
| PolyForge (v1 trend) | 32.58 | 0.08162 | 0.09429 | 0.9316 | 0.1703 | 0.3861 |
| PolyForge v2 (Holt) | 31.53 | 0.07976 | 0.09187 | 0.9342 | 0.1613 | 0.375 |
| HPA | 110.2 | 0.006734 | 0.008684 | 0.994 | 0.1133 | 0.6547 |
| KEDA | 109.8 | 0.006765 | 0.008721 | 0.994 | 0.1133 | 0.6523 |
| FIRM | 112.4 | 0.01385 | 0.01672 | 0.9871 | 0.1133 | 0.6848 |

## HT2 (confirmatory, PREREG_TRACE2 §3) — composite J, paired by window

| baseline | n | J diff (jcac−base) | p | d_z | cost diff (USD) | cost p | violation diff | violation p | verdict |
|---|---|---|---|---|---|---|---|---|---|
| HPA | 96 | -0.2686 | 4.909e-05 | -0.4342 | -77.65 | 3.974e-06 | 0.07489 | 9.354e-07 | PASS |
| KEDA | 96 | -0.2661 | 5.543e-05 | -0.4309 | -77.22 | 4.326e-06 | 0.07485 | 9.406e-07 | PASS |
| FIRM | 96 | -0.2987 | 1.063e-05 | -0.4748 | -79.8 | 1.789e-06 | 0.06777 | 2.784e-06 | PASS |

**HT2: PASS with disclosure** — the J wins vs HPA, KEDA, FIRM come with a large-effect violation regression (p<0.01, |d_z|≥0.5): PolyForge trades some attainment for its cost advantage on real demand, stated prominently per §3.

## Declared secondary (estimates)

| baseline | segment | J diff | cost diff (USD) |
|---|---|---|---|
| HPA | segment 1 | -0.2681 | -84.76 |
| HPA | segment 2 | -0.269 | -70.53 |
| KEDA | segment 1 | -0.266 | -84.38 |
| KEDA | segment 2 | -0.2663 | -70.05 |
| FIRM | segment 1 | -0.3018 | -87.58 |
| FIRM | segment 2 | -0.2957 | -72.03 |

ET1 replication (`jcac_v2` − `jcac` on J): diff -0.01113, p=7.18e-06, d_z=-0.485.

## Notes

- Same substrate rules as the first sample: model-scale dollars, ranking is the claim, tables never mixed with matrix results (ground rule 4).
- Latency metrics are p95, not p99 (documented deviation).
