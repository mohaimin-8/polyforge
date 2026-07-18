# Tier-scaled work-unit rerun (mid 1.516×, large 16.64×) — as measured

Campaign of `PREREG_TIER_WU.md` (pushed before the run); database `raw_sim_tierwu.duckdb`, 1800 valid runs. Tests and pairing are the
frozen ones: 300 per-cell pairs per baseline, one-sample t on paired
differences, alpha 0.01. The v1 headline matrix (published economy) is
quoted beside each number for context, never in place of it.

## TW-H1 composite-J win vs every baseline (primary, metric `J`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -0.1514 | -0.97 | 6.1e-45 | -27.2% | -27.5% | WIN |
| keda | 300 | -0.1779 | -1.10 | 8.8e-54 | -30.6% | -30.8% | WIN |
| firm | 300 | -0.2506 | -1.42 | 6.6e-74 | -38.3% | -38.4% | WIN |
| static | 300 | -2.6389 | -1.09 | 5.3e-53 | -86.7% | -86.7% | WIN |
| gptcache | 300 | -9.5007 | -0.73 | 8.4e-30 | -95.9% | -94.7% | WIN |

**Primary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## TW-H2 cost win vs every baseline (secondary, metric `total_cost_usd`)

| baseline | pairs | mean paired diff | dz | p | aggregate delta | v1 aggregate delta | verdict |
|---|---|---|---|---|---|---|---|
| hpa | 300 | -1.3484 | -0.72 | 7.7e-29 | -36.3% | -36.1% | WIN |
| keda | 300 | -1.7315 | -0.97 | 6.5e-45 | -42.3% | -42.2% | WIN |
| firm | 300 | -1.5295 | -0.83 | 3.5e-36 | -39.3% | -39.2% | WIN |
| static | 300 | -26.3831 | -1.12 | 4.2e-55 | -91.8% | -91.8% | WIN |
| gptcache | 300 | -88.8230 | -0.73 | 1.1e-29 | -97.4% | -96.6% | WIN |

**Secondary hypothesis: PASS** (5/5 baselines beaten at p < 0.01).

## System-level context (descriptive)

| system | mean cost/run | mean violation | mean Jain | cache hit rate | v1 cache hit rate |
|---|---|---|---|---|---|
| jcac | 2.365 | 0.0699 | 0.9681 | 0.106 | 0.106 |
| hpa | 3.714 | 0.0734 | 0.9627 | 0.058 | 0.058 |
| keda | 4.097 | 0.0681 | 0.9689 | 0.058 | 0.058 |
| firm | 3.895 | 0.1050 | 0.9286 | 0.058 | 0.058 |
| static | 28.748 | 0.0102 | 0.9942 | 0.140 | 0.140 |
| gptcache | 91.188 | 0.1326 | 0.8776 | 0.139 | 0.139 |

## Tier / cache posture, rep-0 rows (descriptive)

| system | steps @ none | steps @ small | steps @ mid | steps @ large | mean cache MB |
|---|---|---|---|---|---|
| jcac | 7.3% | 91.1% | 1.6% | 0.0% | 364 |
| hpa | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| keda | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| firm | 0.0% | 100.0% | 0.0% | 0.0% | 128 |
| static | 0.0% | 0.0% | 100.0% | 0.0% | 1024 |
| gptcache | 0.0% | 76.5% | 5.3% | 18.2% | 608 |
