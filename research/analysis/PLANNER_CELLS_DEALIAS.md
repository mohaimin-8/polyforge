# De-aliased planning-cell assignment — as measured (PREREG_PLANNER_CELLS_DEALIAS.md)

Follow-up to the confound found in `PLANNER_CELLS.md`: the frozen run's
round-robin-on-index rule aliased with the whale period (every 8th tenant)
and concentrated whales whenever the cell count was a multiple of 8. Here
the one changed factor is the partition rule — **hash order by md5(tenant_id),
chunk into K = 32** — which cannot alias with tenant index. Latency
is unchanged (identical cell sizes) and not re-measured; PS-H1's feasibility
result stands. Same deployed planner, demand (seed 42, whale every 8th), and
N grid.

| tenants | cells | Jain monolithic | Jain hash-partitioned | ΔJain (hash) | ΔJain (round-robin, frozen) |
|---|---|---|---|---|---|
| 8 | 1 | 1.0000 | 1.0000 | +0.0000 | +0.0000 |
| 32 | 1 | 0.9894 | 0.9894 | +0.0000 | +0.0106 |
| 64 | 2 | 1.0000 | 0.9947 | -0.0053 | +0.0000 |
| 128 | 4 | 0.9916 | 0.9894 | -0.0022 | -0.0518 |
| 256 | 8 | 0.9921 | 0.9921 | -0.0000 | -0.0890 |
| 512 | 16 | 0.9904 | 0.9926 | +0.0022 | -0.0784 |
| 1024 | 32 | 0.9893 | 0.9878 | -0.0015 | -0.0671 |

## Hypothesis outcomes

- **PF-H1 (fairness under de-aliased assignment): PASS.** Worst-case global-Jain change across all N is **-0.0053** (tolerance ΔJain ≥ −0.05). Hash-based planning cells preserve global fairness within the bound — the fairness question PS-H2 could not answer, answered.
- **PF-H2 (the engineering lesson):** the ΔJain columns above contrast the two rules. Where round-robin aliased (N a multiple of 8·K in cell count) its fairness collapsed while hash assignment held — hash-based cell assignment is the correct choice, and the difference is the measured cost of the alias.

Reading: partitioning's latency win (PS-H1, `PLANNER_CELLS.md`) comes with a global-fairness cost that is an artifact of the *assignment rule*, not of partitioning itself: a hash assignment that decorrelates cell membership from tenant index recovers global fairness while keeping per-cell latency flat. Contiguous-by-budget assignment (all whales together) remains the adversarial worst case and is still named as future work.
