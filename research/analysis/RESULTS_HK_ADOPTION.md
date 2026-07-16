# Empirical cache-curve rerun (hmax 0.285, half 19 MB) — as measured

Campaign of `PREREG_HK_ADOPTION.md` (pushed before the run); database `raw_sim_hk.duckdb`, 1800 valid runs. Tests and pairing are the
frozen ones: 300 per-cell pairs per baseline, one-sample t on paired
differences, alpha 0.01. The v1 headline matrix (published economy) is
quoted beside each number for context, never in place of it.

## HK-H1 composite-J win vs every baseline (primary, metric `J`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -0.1093 | -0.97 | 6.7e-45 | -19.4% | -27.5% | WIN |
| keda | 300 | -0.1353 | -1.14 | 6.9e-56 | -23.0% | -30.8% | WIN |
| firm | 300 | -0.2080 | -1.50 | 1.6e-78 | -31.5% | -38.4% | WIN |
| static | 300 | -3.2227 | -1.02 | 2.8e-48 | -87.7% | -86.7% | WIN |
| gptcache | 300 | -9.1670 | -0.69 | 4.7e-27 | -95.3% | -94.7% | WIN |

**Primary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## HK-H2 cost win vs every baseline (secondary, metric `total_cost_usd`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -1.1635 | -0.70 | 9.3e-28 | -30.9% | -36.1% | WIN |
| keda | 300 | -1.5487 | -0.98 | 7.7e-46 | -37.3% | -42.2% | WIN |
| firm | 300 | -1.3488 | -0.83 | 3.7e-36 | -34.1% | -39.2% | WIN |
| static | 300 | -32.2309 | -1.05 | 2.5e-50 | -92.5% | -91.8% | WIN |
| gptcache | 300 | -87.9053 | -0.69 | 3.8e-27 | -97.1% | -96.6% | WIN |

**Secondary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## System-level context (descriptive)

| system | mean cost/run | mean violation | mean Jain | cache hit rate | v1 cache hit rate |
|---|---|---|---|---|---|
| jcac | 2.602 | 0.0804 | 0.9620 | 0.051 | 0.106 |
| hpa | 3.766 | 0.0740 | 0.9624 | 0.051 | 0.058 |
| keda | 4.151 | 0.0684 | 0.9686 | 0.051 | 0.058 |
| firm | 3.951 | 0.1052 | 0.9288 | 0.051 | 0.058 |
| static | 34.833 | 0.0076 | 0.9967 | 0.057 | 0.140 |
| gptcache | 90.508 | 0.0487 | 0.9688 | 0.057 | 0.139 |

## Tier / cache posture, rep-0 rows (descriptive)

| system | steps @ none | steps @ small | steps @ mid | steps @ large | mean cache MB |
|---|---|---|---|---|---|
| jcac | 7.5% | 90.8% | 1.7% | 0.0% | 136 |
| hpa | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| keda | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| firm | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| static | 0.0% | 0.0% | 100.0% | 0.0% | 1024 |
| gptcache | 0.0% | 75.0% | 8.6% | 16.4% | 608 |
