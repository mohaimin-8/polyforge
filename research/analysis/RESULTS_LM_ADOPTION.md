# Measured latency-model rerun (a 0.86, tail 1.6909·(1−ρ)^−0.1303) — as measured

Campaign of `PREREG_LM_ADOPTION.md` (pushed before the run); database `raw_sim_lm.duckdb`, 1800 valid runs. Tests and pairing are the
frozen ones: 300 per-cell pairs per baseline, one-sample t on paired
differences, alpha 0.01. The v1 headline matrix (published economy) is
quoted beside each number for context, never in place of it.

## LM-H1 composite-J win vs every baseline (primary, metric `J`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -0.1364 | -0.94 | 5.7e-43 | -22.9% | -27.5% | WIN |
| keda | 300 | -0.1611 | -1.07 | 2.5e-51 | -26.0% | -30.8% | WIN |
| firm | 300 | -0.2350 | -1.38 | 2.6e-71 | -33.8% | -38.4% | WIN |
| static | 300 | -2.5872 | -1.08 | 2.6e-52 | -84.9% | -86.7% | WIN |
| gptcache | 300 | -7.8560 | -0.69 | 3.9e-27 | -94.5% | -94.7% | WIN |

**Primary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## LM-H2 cost win vs every baseline (secondary, metric `total_cost_usd`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -1.1674 | -0.67 | 4.9e-26 | -31.5% | -36.1% | WIN |
| keda | 300 | -1.5573 | -0.93 | 1.6e-42 | -38.0% | -42.2% | WIN |
| firm | 300 | -1.3540 | -0.80 | 9.5e-34 | -34.7% | -39.2% | WIN |
| static | 300 | -26.2289 | -1.12 | 4.3e-55 | -91.2% | -91.8% | WIN |
| gptcache | 300 | -75.4276 | -0.69 | 3.5e-27 | -96.7% | -96.6% | WIN |

**Secondary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## LM-H3 violation non-inferiority vs tuned reactive scalers

PASS per baseline iff the one-sided 99% upper confidence bound of the
paired `mean_violation` diff (jcac − baseline) stays within the v1
paired diff + 0.02 (margin frozen in the prereg). The v1
reference is recomputed live from the committed `raw_sim.duckdb`.

| baseline | pairs | paired diff | 99% UB (one-sided) | v1 paired diff | bound (v1 + margin) | verdict |
|---|---|---|---|---|---|---|
| hpa | 300 | -0.0057 | +0.0025 | -0.0045 | +0.0155 | PASS |
| keda | 300 | +0.0010 | +0.0094 | +0.0010 | +0.0210 | PASS |

**LM-H3: PASS** (2/2 within the margin).

## System-level context (descriptive)

| system | mean cost/run | mean violation | mean Jain | cache hit rate | v1 cache hit rate |
|---|---|---|---|---|---|
| jcac | 2.544 | 0.0862 | 0.9598 | 0.107 | 0.106 |
| hpa | 3.711 | 0.0918 | 0.9549 | 0.058 | 0.058 |
| keda | 4.101 | 0.0852 | 0.9609 | 0.058 | 0.058 |
| firm | 3.898 | 0.1230 | 0.9215 | 0.058 | 0.058 |
| static | 28.773 | 0.0110 | 0.9951 | 0.140 | 0.140 |
| gptcache | 77.971 | 0.0541 | 0.9657 | 0.139 | 0.139 |

## Tier / cache posture, rep-0 rows (descriptive)

| system | steps @ none | steps @ small | steps @ mid | steps @ large | mean cache MB |
|---|---|---|---|---|---|
| jcac | 6.2% | 91.2% | 2.7% | 0.0% | 370 |
| hpa | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| keda | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| firm | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| static | 0.0% | 0.0% | 100.0% | 0.0% | 1024 |
| gptcache | 0.0% | 72.6% | 10.3% | 17.1% | 608 |
