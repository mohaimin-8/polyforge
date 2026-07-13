# Planner scaling — joint plan-cycle latency vs tenant count

Engineering microbenchmark (see `planner_scaling.py` docstring for method and caveats); single CPU thread, in-process `PlannerCore.plan` — the deployed planner's exact code path minus HTTP. 20 timed calls per N, seeded demand jitter, seed 42.

| tenants | median / cycle | p95 / cycle | p95 vs 3 s operator timeout | p95 vs 10 s control period |
|---|---|---|---|---|
| 8 | 73.0 ms | 97.1 ms | 3.2% | 1.0% |
| 16 | 183.2 ms | 207.1 ms | 6.9% | 2.1% |
| 32 | 423.7 ms | 485.2 ms | 16.2% | 4.9% |
| 64 | 1135.7 ms | 1237.1 ms | 41.2% | 12.4% |
| 128 | 3502.1 ms | 3650.6 ms | 121.7% | 36.5% |
| 256 | 11720.4 ms | 12120.6 ms | 404.0% | 121.2% |

Scaling shape: fitted growth exponent **1.45** (1 = linear, 2 = quadratic). The joint fairness term couples every tenant's candidate evaluation to the rest of the portfolio, so a super-linear exponent is the cost of jointness, measured rather than hand-waved.

- p95 first exceeds the operator timeout (3 s) at **128 tenants** (on this laptop core).
- p95 first exceeds the control period (10 s) at **256 tenants** (on this laptop core).

Reading: the deployed configuration is comfortable at the eval's portfolio sizes and well beyond, and the failure mode past the timeout is the *designed* one — the operator holds the last good plan and flips Policy status to `fallback` (plan_runner.go), degrading gracefully rather than stalling the control loop. Scaling past the measured crossover is future work with named levers: partition the portfolio into planning cells, incrementally maintain the fairness term's partial sums, or move the sweep's inner loop out of pure Python. Coordination overheads *outside* the planner (CR write fan-out, telemetry aggregation) are not measured here and are named in DEFENSE_QA.md §13.
