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

## Advanced figures (Tier 2 + security, v2)

- **fig13_forecast_ablation** — The forecast ablation reveals headroom in the system's own default: the W30 linear-trend forecaster overreacts to bucket noise, and damped Holt smoothing beats it by ~16% on SLO violation at equal cost. Online-seasonal detection helps only where a period exists. Holt is the recommended default; the headline evaluation used trend, so these gains stack on top.
- **fig14_realism_pareto** — Under reconfiguration realism (replica startup lag + cache warm-up, billed immediately): reactive scalers pay for capacity that misses the burst it was bought for, so their violations rise; PolyForge's hysteresis and the self-calibrating variant hold the frontier.
- **fig15_adaptive_under_realism** — Self-calibration under realism: learning effective capacity from realized-vs-projected feedback lowers violations most on the bursty classes, where startup lag hurts a model that trusts nominal capacity.
- **fig16_cache_side_channel** — Left: a shared semantic cache leaks tenant prompt membership from response time alone (AUC rises with cross-tenant warming); PolyForge's per-tenant cache pins the attacker at chance. Right: isolation costs hit rate, but demand-proportional sizing recovers part of the naive equal-split penalty.
- **fig17_defense_frontier** — Leakage (worst-case membership-inference AUC) versus the share of the cache's latency benefit each defense gives up, same attack for all. Response-time padding and TTL jitter trace cost/leakage curves that only approach chance at extreme cost (padding never below AUC 0.73; TTL needs to discard ~90% of hits); per-tenant partitioning reaches chance-level AUC at a fraction of that cost while retaining most hits — it dominates the frontier rather than merely joining it.
