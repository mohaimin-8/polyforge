# Planner plan-cycle latency: two-sweep vs convergent solver

Engineering microbenchmark, descriptive (see `planner_timing_solvers.py`). In-process `PlannerCore.plan`, single CPU thread, 20 timed calls per configuration and tenant count after one warm-up, the payloads of `planner_scaling.py` (seed 42, re-seeded per configuration so both see identical inputs). Machine: Intel64 Family 6 Model 140 Stepping 1, GenuineIntel, Python 3.13.5. Absolute times are machine-dependent.

| tenants | solver | median / cycle | p95 / cycle |
|---|---|---:|---:|
| 8 | two-sweep (published default) | 22.6 ms | 27.5 ms |
| 8 | convergent (anchored moves, sweeps to a fixed point) | 30.9 ms | 43.6 ms |
| 16 | two-sweep (published default) | 74.7 ms | 99.2 ms |
| 16 | convergent (anchored moves, sweeps to a fixed point) | 65.9 ms | 93.6 ms |
| 32 | two-sweep (published default) | 141.5 ms | 227.1 ms |
| 32 | convergent (anchored moves, sweeps to a fixed point) | 171.5 ms | 254.3 ms |

