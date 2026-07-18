# Mixture-percentile rerun (ai_p95 = true hit/miss mixture p95) — as measured

Campaign of `PREREG_MIXTURE_P95.md` (pushed before the run); database `raw_sim_mixp95.duckdb`, 1800 valid runs. Tests and pairing are the
frozen ones: 300 per-cell pairs per baseline, one-sample t on paired
differences, alpha 0.01. The v1 headline matrix (published economy) is
quoted beside each number for context, never in place of it.

## MX-H1 composite-J win vs every baseline (primary, metric `J`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -0.1451 | -0.99 | 2.4e-46 | -22.2% | -27.5% | WIN |
| keda | 300 | -0.1647 | -1.11 | 2.4e-54 | -24.4% | -30.8% | WIN |
| firm | 300 | -0.2160 | -1.33 | 4.9e-68 | -29.8% | -38.4% | WIN |
| static | 300 | -2.5476 | -1.07 | 7.3e-52 | -83.3% | -86.7% | WIN |
| gptcache | 300 | -9.5283 | -0.77 | 3.2e-32 | -94.9% | -94.7% | WIN |

**Primary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## MX-H2 cost win vs every baseline (secondary, metric `total_cost_usd`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -1.2150 | -0.68 | 8.8e-27 | -32.8% | -36.1% | WIN |
| keda | 300 | -1.6077 | -0.94 | 3e-43 | -39.2% | -42.2% | WIN |
| firm | 300 | -1.4212 | -0.82 | 2.8e-35 | -36.3% | -39.2% | WIN |
| static | 300 | -26.2776 | -1.12 | 3.7e-55 | -91.3% | -91.8% | WIN |
| gptcache | 300 | -91.8349 | -0.77 | 2.2e-32 | -97.4% | -96.6% | WIN |

**Secondary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## MX-H3 violation non-inferiority vs tuned reactive scalers

PASS per baseline iff the one-sided 99% upper confidence bound of the
paired `mean_violation` diff (jcac − baseline) stays within the v1
paired diff + 0.02 (margin frozen in the prereg). The v1
reference is recomputed live from the committed `raw_sim.duckdb`.

| baseline | pairs | paired diff | 99% UB (one-sided) | v1 paired diff | bound (v1 + margin) | verdict |
|---|---|---|---|---|---|---|
| hpa | 300 | -0.0078 | +0.0011 | -0.0045 | +0.0155 | PASS |
| keda | 300 | +0.0013 | +0.0103 | +0.0010 | +0.0210 | PASS |

**MX-H3: PASS** (2/2 within the margin).

## System-level context (descriptive)

| system | mean cost/run | mean violation | mean Jain | cache hit rate | v1 cache hit rate |
|---|---|---|---|---|---|
| jcac | 2.494 | 0.1136 | 0.9589 | 0.102 | 0.106 |
| hpa | 3.709 | 0.1214 | 0.9551 | 0.058 | 0.058 |
| keda | 4.102 | 0.1123 | 0.9619 | 0.058 | 0.058 |
| firm | 3.915 | 0.1386 | 0.9255 | 0.058 | 0.058 |
| static | 28.772 | 0.0163 | 0.9949 | 0.140 | 0.140 |
| gptcache | 94.329 | 0.0562 | 0.9656 | 0.139 | 0.139 |

## Tier / cache posture, rep-0 rows (descriptive)

| system | steps @ none | steps @ small | steps @ mid | steps @ large | mean cache MB |
|---|---|---|---|---|---|
| jcac | 6.7% | 90.9% | 2.4% | 0.0% | 329 |
| hpa | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| keda | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| firm | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| static | 0.0% | 0.0% | 100.0% | 0.0% | 1024 |
| gptcache | 0.0% | 71.7% | 8.2% | 20.1% | 608 |
