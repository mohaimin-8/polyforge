# Tier-price ratio rerun at the CORRECTED GPU corner (1:1.475:1.518) — as measured

Campaign of `PREREG_TIER_RATIO_V2.md` (pushed before the run); database `raw_sim_gpu_econ_v2.duckdb`, 1800 valid runs. Tests and pairing are the
frozen ones: 300 per-cell pairs per baseline, one-sample t on paired
differences, alpha 0.01. The v1 headline matrix (published economy) is
quoted beside each number for context, never in place of it.

## TR2-H1 cost win vs every baseline (primary, metric `total_cost_usd`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -1.3588 | -0.74 | 3.8e-30 | -36.6% | -36.1% | WIN |
| keda | 300 | -1.7512 | -1.00 | 3e-47 | -42.7% | -42.2% | WIN |
| firm | 300 | -1.5428 | -0.86 | 1.2e-37 | -39.6% | -39.2% | WIN |
| static | 300 | -3.0689 | -1.39 | 3.8e-72 | -56.6% | -91.8% | WIN |
| gptcache | 300 | -1.1877 | -0.91 | 4.1e-41 | -33.6% | -96.6% | WIN |

**Primary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## TR2-H2 composite-J win vs every baseline (secondary, metric `J`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -0.1935 | -1.07 | 1e-51 | -34.9% | -27.5% | WIN |
| keda | 300 | -0.2206 | -1.20 | 6e-60 | -37.9% | -30.8% | WIN |
| firm | 300 | -0.2906 | -1.47 | 6.9e-77 | -44.6% | -38.4% | WIN |
| static | 300 | -0.2217 | -0.95 | 5.7e-44 | -38.0% | -86.7% | WIN |
| gptcache | 300 | -0.1137 | -0.92 | 7.9e-42 | -23.9% | -94.7% | WIN |

**Secondary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## Decision response vs the accounting prediction (descriptive)

The prereg froze BREAKEVEN_TIER.md's fixed-decision aggregate deltas at
this corner; the gap to the rerun's aggregate is the measured effect of
controllers re-planning under the new prices.

| baseline | accounting (decisions frozen) | rerun (decisions live) | gap |
|---|---|---|---|
| hpa | -40.3% | -36.6% | +3.7% |
| keda | -45.9% | -42.7% | +3.2% |
| firm | -43.1% | -39.6% | +3.5% |
| static | -59.1% | -56.6% | +2.5% |
| gptcache | -37.2% | -33.6% | +3.6% |

## System-level context (descriptive)

| system | mean cost/run | mean violation | mean Jain | cache hit rate | v1 cache hit rate |
|---|---|---|---|---|---|
| jcac | 2.349 | 0.0506 | 0.9729 | 0.106 | 0.106 |
| hpa | 3.708 | 0.0734 | 0.9626 | 0.058 | 0.058 |
| keda | 4.100 | 0.0679 | 0.9689 | 0.058 | 0.058 |
| firm | 3.892 | 0.1039 | 0.9293 | 0.058 | 0.058 |
| static | 5.418 | 0.0063 | 0.9972 | 0.140 | 0.140 |
| gptcache | 3.537 | 0.0446 | 0.9710 | 0.139 | 0.139 |

## Tier / cache posture, rep-0 rows (descriptive)

| system | steps @ none | steps @ small | steps @ mid | steps @ large | mean cache MB |
|---|---|---|---|---|---|
| jcac | 7.2% | 78.8% | 14.1% | 0.0% | 364 |
| hpa | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| keda | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| firm | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| static | 0.0% | 0.0% | 100.0% | 0.0% | 1024 |
| gptcache | 0.0% | 78.9% | 5.3% | 15.9% | 608 |
