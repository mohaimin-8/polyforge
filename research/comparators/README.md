# research/comparators — programme 2, work package WP9

Comparators and the tuning protocol (plan: `Thesis Project/PolyForge_Q1_Research_Plan_v2_detailed_2026-11-12.md`,
outside the repository). Exploratory until a registration says otherwise: nothing here has been tuned yet.

| file | what it is |
|---|---|
| `arms.py` | the arm set: each arm's tunable parameters (name, type, range or choices, default), seeded sampling of configurations, in-process harness registration (`arm_system`, `execute`) |
| `tunable_planner.py` | `TunablePlanner`: `JCACController` plus a settable horizon and objective weights (identical to it at the defaults; tested) |
| `k8s_hpa.py` | Kubernetes' HorizontalPodAutoscaler with its documented default behaviour, per tenant pool |
| `layer_control.py` | the per-layer comparators: the stack of rules, the independent per-layer planners, the budget gate |
| `protocol.py` | the protocol as pure functions: objective J, workload class rule, selection, evaluation plan |
| `tune.py` | runs the protocol (multiprocessing pool); writes `tuned.json`, `report.md`, `runs.csv.gz`, `references.csv.gz` |
| `report.py` | the markdown report |
| `test_comparators.py` | spaces, every arm on both plant modes, selection and class rule, HPA defaults, budget gate, `tune.py` end to end |
| `smoke/` | outputs of the pipeline check (not results) |

## Arms

| arm | what it is | existing code |
|---|---|---|
| `joint` | joint planner: one tenant's three knobs searched together, tenants coordinated by convergent sweeps on one shared objective | `controller.JCACController`, `knob_search="joint"` (registered arm `jcac_converged`), via `TunablePlanner` |
| `per_knob` | per-knob planner: each knob best-responded alone on the shared objective, winners combined (arbiter repairs infeasible combinations) | `knob_search="decoupled"` (`jcac_decoupled`) |
| `one_knob` | iterated one-knob planner: one knob at a time to a fixed point, never a two-knob move | `knob_search="knobwise"` (`jcac_knobwise`) |
| `layers` | independent per-layer planners: pool on utilisation or queue against a target, priced against replica cost; cache on expected hits (valued at a fixed price per hit) against memory cost; router on per-request price, AI p95 overshoot and, when priced, quality loss. Each layer has its own objective, sees the interval-start state of the others, and averages over the same forecasters the planners use | new: `layer_control.IndependentLayersController` (the router's objective has the form of `baselines.quality_aware_tier`, with its own weights) |
| `rules` | per-layer stack of rules: the default-behaviour HPA on each tenant pool, a cache-sizing rule (grow while a level buys at least θ hits/s per MB; shrink when it no longer does), a router rule (preferred tier while its projected AI p95 meets target, else `baselines.cheapest_meeting_tier`), and the budget gate | new: `layer_control.RulesStackController` + `k8s_hpa.K8sReplicaLayer` |
| `static_best` | reference line: the hindsight-best fixed state per tenant inside an equal share of the caps (WP1) | `research/theory/clairvoyant.py` `static_best` |
| `clairvoyant` | reference lines: LB0 (any controller), LB1/LB2 (controllers inside the move limits), UB (an achievable plan) (WP1) | `research/theory/clairvoyant.py` `bounds` |

All planner arms are anchored and convergent (`anchor_moves`, `converge_sweeps`), as in every programme-2 pilot. The
per-layer arms run inside the cluster caps (`baselines.CapEnforced`) and, when the budget is enforced, behind
`layer_control.BudgetGate`, applied last: `BudgetGate(CapEnforced(...))`. The gate only lowers usage, so the final
state keeps both the caps and the budget. The gate refuses billed model calls only when that saves money; otherwise
it trims replicas. Programme 1's `baselines.BudgetGated` sheds every AI request in a self-hosted plant, where refusing
saves nothing. All arms start at the same state and use the same cache, so none is charged an eviction factor.

Budget modes (`--budget`): `enforced` (default) keeps every arm inside each tenant's per-step budget: planners filter
candidates (`enforce_budget`), per-layer arms are gated. `plant` leaves only plant 2's own admission gate, for all
arms. This is the "budget symmetry" factor of WP5's specification curve.

### Kubernetes default behaviour

From <https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/> (retrieved 2026-11-12) and
the controller source (`pkg/controller/podautoscaler/horizontal.go`, read the same day; details in `k8s_hpa.py`):

- desired = ceil(current × metric / target), held inside a tolerance of 0.1;
- one sync every 15 s;
- scale-up: no stabilisation, at most max(100 %, 4 pods) per 15 s;
- scale-down: the highest recommendation of the last 300 s, at most 100 % per 15 s.

The simulator decides every 10 s, so the HPA acts on the intervals that contain a sync tick: ticks at 0, 15, 30 and
45 s fall in the intervals that start at 0, 10, 30 and 40 s.

`baselines.HPAController(policy="k8s_default")` was not reused. It has no tolerance band, acts every 10 s, treats one
10 s interval as a 15 s policy period, and keeps 30 intervals of recommendations. It is left unchanged.

An HPA created with no `behavior` field runs the controller's legacy path, available as `hpa_behavior="unset"`:
- the window maximum applies in both directions;
- scale-up is capped at max(2 × current, 4) replicas.

The arm uses the documented default block.

## Parameter spaces

Planner arms (`joint`, `per_knob`, `one_knob`). The cost weight α is fixed at 1 because it sets the objective's scale.

| parameter | range | default |
|---|---|---|
| `switch_penalty` | log-uniform [0.001, 0.2] | 0.05 (published) |
| `horizon_steps` | integer 1..6 (10 s each) | 6 |
| `forecast_method` | {persistence, trend, holt, seasonal} | trend |
| `weight_beta` | log-uniform [0.25, 32] | 2.0 |
| `weight_gamma` | uniform [0, 2] | 0.5 |

`layers`:

| parameter | range | default |
|---|---|---|
| `forecast_method` | {persistence, trend, holt, seasonal} | trend |
| `horizon_steps` | integer 1..6 | 6 |
| `pool_signal` | {utilisation, queue} | utilisation |
| `pool_target_utilisation` | log-uniform [0.05, 1] | 0.6 |
| `pool_target_queue` | log-uniform [0.25, 8] (work in flight per replica) | 1.5 |
| `pool_weight` | log-uniform [0.01, 100] | 1.0 |
| `hit_value_usd` | log-uniform [1e-8, 1e-2] | 1e-4 |
| `router_latency_weight` | log-uniform [0.1, 100] | 2.0 |

`rules`:

| parameter | range | default |
|---|---|---|
| `hpa_metric` | {utilisation, queue} | utilisation |
| `hpa_target_utilisation` | log-uniform [0.05, 1] | 0.6 |
| `hpa_target_queue` | log-uniform [0.25, 8] | 1.5 |
| `cache_hits_per_mb_s` | log-uniform [1e-5, 0.1] | 1e-3 |
| `router_tier` | {small, mid, large} | small |

With `--quality-weight q > 0`, `joint`, `per_knob`, `one_knob` and `layers` also tune `quality_weight`, uniform on
[0, 2q] with default q. In `rules` the router's preferred tier plays that role.

## Tuning protocol (`tune.py`)

1. Every arm gets the same budget: `--configs` (64) configurations. Configuration 0 is the declared defaults; the rest
   are random draws from a stream seeded by (`--seed`, arm name). A draw therefore does not depend on which other
   arms are tuned, and raising `--configs` only appends. `--no-default` makes all 64 random.
2. Every configuration runs on every tuning workload, with shared seeds: all arms and configurations of a workload
   see the same demand.
3. Selection is by mean J on the tuning half. A configuration that fails any tuning run is disqualified in every
   scope, and ties go to the lower index. Granularity is a factor: one configuration overall, and one per workload
   class.
4. Reporting is on the evaluation half: each arm's overall configuration on every evaluation workload, and each
   class's configuration on the workloads of that class. Identical jobs run once. The report gives the J terms, the
   paired difference to `joint`, what per-class tuning buys, and, with `--references`, the WP1 reference lines and
   normalised regret.

**Objective:**

J = cost / (scored tenant-steps) / 0.01 + 2 × mean violation + 0.5 × (1 − mean Jain) [+ q × mean quality loss]

Population workloads score all `steps` steps, because they build steps + 1 buckets. `research/sensitivity/morris_pilot.py`
divides by 119 × 8, which overstates its cost term by 120/119 on these workloads.

**Workload classes**, fixed before any result: the stream family carrying the larger share of the workload's demand
(the sum of its tenants' `rate_rps`).
- `conversation` = azure2024/conv and the two BurstGPT conv streams;
- `api_or_code` = the two BurstGPT API streams and azure2024/code;
- ties go to the alphabetically first family.

Counts: tuning half 64 / 56, evaluation half 56 / 64 (api_or_code / conversation). A tenant count would have tied 4–4
in 29 of 120 tuning workloads; demand share ties in none.

## Run

```bash
cd research/comparators
python -m pytest -q                       # 45 tests, about 20 s
python tune.py --plant B --dry-run        # runs and configurations of the full protocol
python tune.py --plant B --arms joint rules --configs 2 --tuning-workloads 2 \
    --evaluation-workloads 2 --steps 8 --references --workers 2 --out smoke   # pipeline check, about 30 s
python tune.py --plant B --mix own --workers 6    # the full protocol (not run)
```

Full protocol, one plant and one mix, default settings: 5 arms × 64 configurations × 120 tuning workloads = 38,400
tuning runs, plus at most 1,200 evaluation runs (plus 120 reference worlds with `--references`). The joint planner
dominates the cost. Measured on this laptop (the other arms' costs were measured in the smoke runs):

| arm | CPU per step |
|---|---|
| joint planner, horizon 6 | about 0.2 s (mode B) to 0.4 s (mode A) |
| per-knob and one-knob planners | several times less (1-D candidate sets) |
| per-layer arms | about 0.01 s |

That puts the default protocol at very roughly 50–100 CPU-hours per plant and mix. Time a pilot before launching
the campaign.

`--tuning-workloads` and `--evaluation-workloads` take the first N of a half in name order, stratified by class. An
output directory is named after the main settings plus a digest of every setting, so runs never overwrite each other.
Exit status is 1 if any run failed (the report lists it). A pool that finishes nothing for `--stall-timeout` seconds
raises instead of waiting forever.

## Not done, and stated limits

- **No tuning campaign has been run.** `smoke/` holds pipeline checks only (2 configurations, 2 + 2 workloads,
  8 steps).
- **The reference lines do not price answer quality.** They are skipped when q > 0.
- **LB1, LB2 and UB assume the planners' move limits.** The HPA (up to +4 or ×2 per sync), the cap trim and the
  budget gate can exceed them, and so can the planners' infeasible-lattice fallback. Only LB0 bounds every arm; the
  report shows each arm's clamp-violation share.
- **HPA approximations.** Syncs are mapped onto 10 s intervals. Metrics are read without delay and every pod is ready
  (readiness delay 30 s and CPU-initialisation period 5 min never bind). Utilisation is offered work over capacity, so
  it may exceed 1.
- **The cache-sizing rule, router rule and per-layer objectives are designs of this package.** Kubernetes defines
  nothing for them. Their ranges are stated choices.
- **Granularity has two classes only.** The rule comes from the population's stream families; no other class rule
  was tried.
- **The tier knob has a trade-off only when quality is priced** (plant 2 README). With the default q = 0, the router
  parameters of both per-layer arms matter mainly through latency.
