# Planning-cell partitioning to 1024 tenants — as measured (PREREG_PLANNER_CELLS.md)

Wave 4 executes the scaling lever named in `PLANNER_SCALING.md`: partition
the portfolio into fixed **K = 32**-tenant planning cells (round-robin),
each planned by the deployed `PlannerCore.plan` against its capacity-parity
share of the cluster. Single CPU thread, this machine; the shape and the
cross-arm gap are the claim, not the absolute latencies. Demand is
heterogeneous (every 8th tenant a 4x whale) so global Jain is a real
quantity < 1; seed 42.

| tenants | cells | monolithic median | monolithic p95 | partitioned per-cell median | partitioned per-cell p95 | partitioned serial median |
|---|---|---|---|---|---|---|
| 8 | 1 | 34.8 ms | 39.6 ms | 36.1 ms | 63.6 ms | 36.1 ms |
| 32 | 1 | 191.5 ms | 235.0 ms | 196.1 ms | 224.9 ms | 196.1 ms |
| 64 | 2 | 544.3 ms | 597.8 ms | 204.5 ms | 255.5 ms | 400.6 ms |
| 128 | 4 | 2018.2 ms | 3229.1 ms | 276.9 ms | 308.1 ms | 989.5 ms |
| 256 | 8 | 6302.3 ms | 7254.5 ms | 206.2 ms | 250.9 ms | 1553.4 ms |
| 512 | 16 | 19042.2 ms | 19312.6 ms | 201.1 ms | 203.1 ms | 2979.7 ms |
| 1024 | 32 | 71151.5 ms | 71151.5 ms | 195.3 ms | 195.3 ms | 5816.4 ms |

| tenants | Jain monolithic | Jain partitioned | ΔJain | mean viol mono | mean viol part | mean cost mono | mean cost part |
|---|---|---|---|---|---|---|---|
| 8 | 1.0000 | 1.0000 | +0.0000 | 0.0000 | 0.0000 | 0.0074 | 0.0072 |
| 32 | 0.9894 | 1.0000 | +0.0106 | 0.0182 | 0.0000 | 0.0069 | 0.0075 |
| 64 | 0.9894 | 0.9894 | +0.0000 | 0.0182 | 0.0182 | 0.0066 | 0.0068 |
| 128 | 0.9893 | 0.9375 | -0.0518 | 0.0184 | 0.0633 | 0.0069 | 0.0064 |
| 256 | 0.9894 | 0.9004 | -0.0890 | 0.0183 | 0.1005 | 0.0069 | 0.0056 |
| 512 | 0.9895 | 0.9111 | -0.0784 | 0.0176 | 0.0904 | 0.0069 | 0.0058 |
| 1024 | 0.9858 | 0.9187 | -0.0671 | 0.0246 | 0.0837 | 0.0067 | 0.0059 |

## Hypothesis outcomes

- **PS-H1 (feasibility): PASS.** Monolithic p95 first exceeds the 3 s operator timeout at **128** tenants; partitioned per-cell p95 stays under it at every N through 1024.
- **PS-H2 (fairness cost): FAIL.** Worst-case global-Jain change from partitioning across all N is **-0.0890** (tolerance ΔJain ≥ −0.05). The fairness cost exceeds the bound — the measured tradeoff, published as such.
- **PS-H3 (descriptive):** fitted latency exponent in N — monolithic **1.59** (reproduces the ~1.45 joint-planner cost), partitioned per-cell **0.27** (flat: cell size is fixed, so per-cell latency is independent of portfolio size).

Reading: partitioning converts the planner's super-linear cycle cost into a
per-cell constant, so 1024 tenants plan within the operator deadline on
independent planner replicas, at a global-fairness cost bounded and measured
above. Round-robin cell assignment is what makes the fairness cost small —
it spreads whales evenly, so every cell optimizes a representative mix;
contiguous-by-budget partitioning is the adversarial opposite and is named
as future work. Cross-planner coordination overheads (CR write fan-out,
telemetry aggregation) are still not measured here (DEFENSE_QA #13).

---

**Erratum (post-run, prose only — the tables and PS-H1/H2/H3 verdicts above
are the immutable measured record and are unchanged).** The generic closing
"Reading" paragraph was emitted unconditionally by the script and its claim
that "round-robin ... makes the fairness cost small" is **falsified by the
PS-H2 FAIL directly above it.** Diagnosis (verified, not tuned): round-robin
on tenant index (cell = i mod C) aliases with the whale period (a whale every
8th tenant), so when the cell count C is a multiple of 8 — exactly N ∈ {256,
512, 1024}, where C = 8, 16, 32 — all whales collapse into a few cells (N=256:
all 32 whales in 1 of 8 cells) instead of spreading. The measured −0.089 Jain
drop is therefore an artifact of the assignment rule aliasing with periodic
tenant structure, not an inherent cost of partitioning. **PS-H1 (latency) is
unaffected and stands.** The fairness question is answered cleanly under a
de-aliased (hash-based) assignment in `PLANNER_CELLS_DEALIAS.md`
(`PREREG_PLANNER_CELLS_DEALIAS.md`, pushed before that run); both results are
reported together, neither replaces the other.
