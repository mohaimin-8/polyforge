# Clamp-fixed controller rerun (moves anchored at interval start) — as measured

Campaign of `PREREG_MOVE_CLAMP.md` (pushed before the run); database `raw_sim_clamp.duckdb`, 1800 valid runs. Tests and pairing are the
frozen ones: 300 per-cell pairs per baseline, one-sample t on paired
differences, alpha 0.01. The v1 headline matrix (published economy) is
quoted beside each number for context, never in place of it.

## MC-H1 composite-J win vs every baseline (clamp-fixed jcac) (primary, metric `J`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -0.1552 | -1.00 | 2.8e-47 | -27.9% | -27.5% | WIN |
| keda | 300 | -0.1818 | -1.14 | 4.3e-56 | -31.2% | -30.8% | WIN |
| firm | 300 | -0.2537 | -1.46 | 4.8e-76 | -38.8% | -38.4% | WIN |
| static | 300 | -2.6340 | -1.09 | 5.7e-53 | -86.8% | -86.7% | WIN |
| gptcache | 300 | -6.9906 | -0.64 | 2.3e-24 | -94.6% | -94.7% | WIN |

**Primary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## MC-H2 cost win vs every baseline (secondary, metric `total_cost_usd`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -1.3443 | -0.72 | 6.3e-29 | -36.2% | -36.1% | WIN |
| keda | 300 | -1.7350 | -0.97 | 3.6e-45 | -42.3% | -42.2% | WIN |
| firm | 300 | -1.5302 | -0.84 | 2.3e-36 | -39.3% | -39.2% | WIN |
| static | 300 | -26.3851 | -1.13 | 3.6e-55 | -91.8% | -91.8% | WIN |
| gptcache | 300 | -67.0086 | -0.64 | 2.2e-24 | -96.6% | -96.6% | WIN |

**Secondary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## MC-H3 violation non-inferiority vs tuned reactive scalers

PASS per baseline iff the one-sided 99% upper confidence bound of the
paired `mean_violation` diff (jcac − baseline) stays within the v1
paired diff + 0.02 (margin frozen in the prereg). The v1
reference is recomputed live from the committed `raw_sim.duckdb`.

| baseline | pairs | paired diff | 99% UB (one-sided) | v1 paired diff | bound (v1 + margin) | verdict |
|---|---|---|---|---|---|---|
| hpa | 300 | -0.0052 | +0.0020 | -0.0045 | +0.0155 | PASS |
| keda | 300 | +0.0004 | +0.0079 | +0.0010 | +0.0210 | PASS |

**MC-H3: PASS** (2/2 within the margin).

## System-level context (descriptive)

| system | mean cost/run | mean violation | mean Jain | cache hit rate | v1 cache hit rate |
|---|---|---|---|---|---|
| jcac_anchored | 2.366 | 0.0682 | 0.9695 | 0.106 | 0.106 |
| hpa | 3.710 | 0.0734 | 0.9626 | 0.058 | 0.058 |
| keda | 4.101 | 0.0678 | 0.9690 | 0.058 | 0.058 |
| firm | 3.896 | 0.1045 | 0.9290 | 0.058 | 0.058 |
| static | 28.751 | 0.0063 | 0.9972 | 0.140 | 0.140 |
| gptcache | 69.374 | 0.0445 | 0.9711 | 0.139 | 0.139 |

## Tier / cache posture, rep-0 rows (descriptive)

| system | steps @ none | steps @ small | steps @ mid | steps @ large | mean cache MB |
|---|---|---|---|---|---|
| jcac_anchored | 7.3% | 91.0% | 1.7% | 0.0% | 352 |
| hpa | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| keda | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| firm | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| static | 0.0% | 0.0% | 100.0% | 0.0% | 1024 |
| gptcache | 0.0% | 78.6% | 5.6% | 15.8% | 608 |
