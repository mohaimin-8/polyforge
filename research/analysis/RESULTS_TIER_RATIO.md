# Tier-price ratio rerun (GPU corner 1:1.516:16.64) — as measured

Campaign of `PREREG_TIER_RATIO.md` (pushed before the run); database `raw_sim_gpu_econ.duckdb`, 1800 valid runs. Tests and pairing are the
frozen ones: 300 per-cell pairs per baseline, one-sample t on paired
differences, alpha 0.01. The v1 headline matrix (published economy) is
quoted beside each number for context, never in place of it.

## TR-H1 cost win vs every baseline (primary, metric `total_cost_usd`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -1.3607 | -0.74 | 5.2e-30 | -36.7% | -36.1% | WIN |
| keda | 300 | -1.7503 | -1.00 | 6.8e-47 | -42.7% | -42.2% | WIN |
| firm | 300 | -1.5469 | -0.86 | 1.1e-37 | -39.7% | -39.2% | WIN |
| static | 300 | -3.1780 | -1.38 | 1.9e-71 | -57.5% | -91.8% | WIN |
| gptcache | 300 | -11.1434 | -0.67 | 3.9e-26 | -82.6% | -96.6% | WIN |

**Primary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## TR-H2 composite-J win vs every baseline (secondary, metric `J`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -0.1893 | -1.07 | 1.5e-51 | -34.1% | -27.5% | WIN |
| keda | 300 | -0.2158 | -1.20 | 2.9e-60 | -37.1% | -30.8% | WIN |
| firm | 300 | -0.2880 | -1.48 | 9.7e-78 | -44.1% | -38.4% | WIN |
| static | 300 | -0.2288 | -0.94 | 3.4e-43 | -38.5% | -86.7% | WIN |
| gptcache | 300 | -1.1549 | -0.67 | 4.8e-26 | -75.9% | -94.7% | WIN |

**Secondary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## Decision response vs the accounting prediction (descriptive)

The prereg froze BREAKEVEN_TIER.md's fixed-decision aggregate deltas at
this corner; the gap to the rerun's aggregate is the measured effect of
controllers re-planning under the new prices.

| baseline | accounting (decisions frozen) | rerun (decisions live) | gap |
|---|---|---|---|
| hpa | -40.2% | -36.7% | +3.5% |
| keda | -45.9% | -42.7% | +3.2% |
| firm | -43.1% | -39.7% | +3.4% |
| static | -60.0% | -57.5% | +2.5% |
| gptcache | -83.0% | -82.6% | +0.4% |

## System-level context (descriptive)

| system | mean cost/run | mean violation | mean Jain | cache hit rate | v1 cache hit rate |
|---|---|---|---|---|---|
| jcac | 2.350 | 0.0524 | 0.9716 | 0.106 | 0.106 |
| hpa | 3.711 | 0.0733 | 0.9627 | 0.058 | 0.058 |
| keda | 4.101 | 0.0677 | 0.9690 | 0.058 | 0.058 |
| firm | 3.897 | 0.1044 | 0.9288 | 0.058 | 0.058 |
| static | 5.528 | 0.0063 | 0.9973 | 0.140 | 0.140 |
| gptcache | 13.494 | 0.0444 | 0.9711 | 0.139 | 0.139 |

## Tier / cache posture, rep-0 rows (descriptive)

| system | steps @ none | steps @ small | steps @ mid | steps @ large | mean cache MB |
|---|---|---|---|---|---|
| jcac | 7.3% | 79.0% | 13.7% | 0.0% | 362 |
| hpa | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| keda | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| firm | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| static | 0.0% | 0.0% | 100.0% | 0.0% | 1024 |
| gptcache | 0.0% | 78.6% | 5.5% | 15.9% | 608 |
