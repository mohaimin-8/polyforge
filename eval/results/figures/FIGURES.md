# W36 figure inventory

Each caption answers: what does this figure prove?

- **fig01_cost_by_system** — PolyForge spends the least of any controller (mean ± 95% CI over all 300 matrix cells per system, log scale) — joint control converts visibility into savings rather than over-provisioning.
- **fig02_violation_by_system** — SLO violations by system (mean ± 95% CI): PolyForge holds violations near the over-provisioned floor at a fraction of its cost.
- **fig03_pareto_cost_violation** — The cost–violation plane (means ± 95% CI): PolyForge sits on the bottom-left frontier — every baseline is dominated on at least one axis without winning the other.
- **fig04_jain_by_tenant_mix** — Fairness by tenant composition (mean ± 95% CI): the whale mix is where fairness is hard, and where the fairness-aware objective earns its term.
- **fig05_cache_hit_by_workload** — Realized semantic-cache hit share on AI traffic: adaptive cache sizing doubles the fixed-cache baselines on cacheable workloads and correctly declines to spend memory on uncacheable ones.
- **fig06_cost_by_cluster_size** — Cost scaling with cluster headroom: static and cache-max policies pay for whatever exists; feedback controllers' spend is demand-shaped, and PolyForge stays lowest at every size.
- **fig07_violation_by_workload** — Where baselines break, by taxonomy class (mean ± 95% CI): replica-only controllers cannot fix agentic latency (it needs the tier knob), which is the taxonomy's control-implication column made measurable.
- **fig08_ablation_deltas** — Removing any one contribution measurably hurts (red = worse than full PolyForge; * = p < 0.01): each of the four components carries independent, statistically significant weight.
- **fig09_adaptation_trace** — One agentic run, blow by blow: within the first minute PolyForge fills its replica budget *and* quadruples the semantic cache — the forecast says bursts will keep coming — while HPA chases every burst with the only knob it has and never touches the cache. Joint control in a single trace.
- **fig10_latency_p95** — Tail latency by traffic family (mean ± 95% CI, log): PolyForge holds both families' tails near the SLO-clean systems while spending an order of magnitude less than they do.
- **fig11_violation_step_share** — How often anyone is violating at all: the incidence view of SLO compliance, complementing fig. 2's severity view.
- **fig12_composite_objective** — The paper's composite objective (J = cost + 2·violation + 0.5·(1−Jain), normalized per tenant-step): the single-number ranking every other figure decomposes.
