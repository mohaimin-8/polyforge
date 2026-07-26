# Planning cells on the shipped path — engineering verification

`PLANNER_CELLS.md` (PS-H1) measured, in the research simulator, that the
monolithic joint solve crosses the operator's 3 s timeout at 128 tenants
while per-cell latency stays flat. **The operator did not implement cells
until session 33**, so that result described a system the artifact did not
ship. This note records the same measurement taken through the shipped
path: the operator's own `planningCells` + `cellLimits`, calling the real
Python planner over HTTP.

Regenerate: see the module docstring of `cells_shipped.py`. Latencies are
machine-dependent (measured on the development host, not the published
benchmark host) — the reproduced claim is the **shape**, not the absolute
milliseconds.

Operator per-call timeout 3000 ms; control period 10000 ms.

| tenants | monolithic p95 | cells | per-cell p95 | per-cell median | full cycle (all cells) | monolithic vs timeout |
|---|---|---|---|---|---|---|
| 8 | 100.0 ms | 1 | 93.1 ms | 93.1 ms | 93.1 ms | under |
| 32 | 297.9 ms | 1 | 385.1 ms | 385.1 ms | 385.6 ms | under |
| 64 | 833.1 ms | 2 | 272.5 ms | 272.5 ms | 592.5 ms | under |
| 128 | 3179.8 ms | 4 | 309.4 ms | 264.0 ms | 1138.0 ms | **OVER** |
| 256 | 10171.2 ms | 8 | 377.9 ms | 308.3 ms | 2588.9 ms | **OVER** |

## What reproduces

- **The wall is real on the shipped path, at the same width.** Monolithic p95 first exceeds the 3000 ms operator timeout at **128 tenants** — PS-H1's simulator finding, independently reproduced through the Go operator and the deployed planner rather than in the simulator that produced it.
- **Per-cell latency is flat.** Across every partitioned width the per-cell p95 stays in a narrow band (272–378 ms) while the portfolio grows, because cell size is fixed and per-cell solve time therefore does not depend on portfolio size.
- **At 256 tenants the monolithic solve (10171 ms) exceeds the entire 10000 ms control period** — the operator could not complete one plan per cycle at all. Partitioned, the *whole* cycle (every cell, serially) finishes in 2589 ms.

## What this note does not claim

- **Not a new scientific result.** PS-H1 and PF-H1 stand as measured in
  their campaigns; this is implementation verification of a closed result,
  carries no pre-registration, and adds nothing to the scoreboard.
- **Not a fairness measurement.** Cells scope the fairness term to each
  cell; the global-Jain cost of that (worst case −0.0053 under the hash
  assignment the operator implements) is `PLANNER_CELLS_DEALIAS.md`'s
  result and is not re-measured here.
- **Not comparable in absolute ms** to `PLANNER_CELLS.md`: different host,
  and this path adds real HTTP and JSON encoding the simulator did not pay.

## Cells are opt-in

`planner.cellSize` defaults to 0 — one call for the whole portfolio, the
behaviour that predates cells — because partitioning trades a little global
fairness for the latency win. The table above is the evidence for turning
it on as a portfolio approaches ~128 tenants, not an argument for it at
every width: at 8 and 32 tenants a single cell is the whole portfolio and
the two columns measure the same call.
