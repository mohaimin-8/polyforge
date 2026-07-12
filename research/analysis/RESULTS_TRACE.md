# Headline replay on real BurstGPT demand — results (pre-registered)

Source: `burstgpt_real.csv.gz` (10,632,194 real requests), 2 contiguous segments, 16 windows x 6 h at 10s control intervals; demand scale k = 75.1 (PREREG_TRACE.md §2, frozen before the run; real shape, matrix magnitude). Engine, systems, and tuning identical to the committed headline. Own substrate — never mixed into matrix tables (ground rule 4). Rerun analysis: `python trace_matrix.py --analyze`.

## Per-system summary (mean over windows)

| system | total_cost_usd | mean_violation | violation_step_share | mean_jain | cache_hit_rate | J |
|---|---|---|---|---|---|---|
| PolyForge (v1 trend) | 23 | 0.08143 | 0.09413 | 0.9319 | 0.167 | 0.3301 |
| PolyForge v2 (Holt) | 21.57 | 0.08268 | 0.09417 | 0.9313 | 0.1545 | 0.3246 |
| PolyForge (seasonal) | 22.95 | 0.0811 | 0.09363 | 0.9323 | 0.167 | 0.3289 |
| HPA | 96.33 | 0.007566 | 0.01036 | 0.9936 | 0.1133 | 0.5761 |
| KEDA | 96.07 | 0.007611 | 0.0104 | 0.9936 | 0.1133 | 0.5747 |
| FIRM | 99.49 | 0.01379 | 0.01693 | 0.9873 | 0.1133 | 0.6099 |

## HT (confirmatory, PREREG_TRACE §3) — composite J, paired by window

| baseline | n | J diff (jcac−base) | p | d_z | cost diff (USD) | cost p | violation diff | violation p | verdict |
|---|---|---|---|---|---|---|---|---|---|
| HPA | 16 | -0.246 | 0.1064 | -0.4295 | -73.33 | 0.1023 | 0.07386 | 0.09737 | FAIL |
| KEDA | 16 | -0.2446 | 0.1076 | -0.4279 | -73.07 | 0.103 | 0.07382 | 0.09745 | FAIL |
| FIRM | 16 | -0.2799 | 0.07257 | -0.4828 | -76.49 | 0.08533 | 0.06765 | 0.109 | FAIL |

**HT: FAIL** — reported as measured; stopping rule §4 forbids re-running either way.

## Declared exploratory (PREREG_TRACE §3)

| comparison | metric | diff (a−b) | p | d_z |
|---|---|---|---|---|
| ET1: Holt vs trend | J | -0.00553 | 0.0247 | -0.624 |
| ET1: Holt vs trend | total_cost_usd | -1.438 | 0.1156 | -0.4175 |
| ET1: Holt vs trend | mean_violation | 0.001245 | 0.4481 | 0.1948 |
| ET2: seasonal vs trend | J | -0.001189 | 0.03574 | -0.5767 |
| ET2: seasonal vs trend | total_cost_usd | -0.05512 | 0.366 | -0.233 |
| ET2: seasonal vs trend | mean_violation | -0.0003316 | 0.1267 | -0.4043 |

ET3 — per-segment J diff (jcac − baseline), mean over that segment's windows:

| baseline | segment | J diff |
|---|---|---|
| HPA | segment 1 | -0.4759 |
| HPA | segment 2 | -0.01609 |
| KEDA | segment 1 | -0.4737 |
| KEDA | segment 2 | -0.01541 |
| FIRM | segment 1 | -0.515 |
| FIRM | segment 2 | -0.04472 |

## Notes

- Windows are the replication unit; each window's Poisson arrival jitter is drawn once and shared by all six systems (exact pairing).
- The demand scale k maps the trace to the calibrated operating point; the *shape* (real diurnal, real burst structure) is untouched. Absolute dollar values are therefore model-scale, not Azure-scale — the claim is the ranking, per ground rule 4.
- Latency metrics are p95, not p99 (documented deviation).
