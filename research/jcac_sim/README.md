# JCAC offline simulator (W30)

Design-time home of the **Joint Cross-layer Adaptive Controller**: the MPC
formulation, an offline simulator that replays W25b traces, the single-layer
baselines it must beat, and the α/β/γ weight sweep that produces the paper's
Pareto figure. The live planner (W31, `services/planner`) imports
`model.py`/`controller.py` from here — simulator and production solve the
same problem with the same code.

## Formulation

Per tenant *i*, state **x**ᵢ = (replicas, cache_mb, tier ∈ {none, small,
mid, large}). Every 10 s the controller solves, over a 60 s horizon H:

```
min_u  Σ_{k=1..H} [ α·cost_norm(k) + β·log1p(slo_excess(k)) ] + γ·(1 − Jain)

s.t.   replica_min ≤ r_i ≤ replica_max            (Policy bounds)
       cache moves ≤ 1 level per interval          (thrash prevention)
       Σ_i cache_i ≤ cluster cache,  Σ_i r_i ≤ cluster CPU
       projected step cost_i ≤ Budget_i
```

applies the first action, and re-plans (receding horizon). Demand forecasts
are linear trends over the last 3 intervals; move blocking (candidate held
across the horizon) keeps the search space linear in candidates.

**Solver** (ADR 0014 deviation): the problem is mixed-integer — integer
replicas, categorical tier — which CVXPY's bundled convex solvers cannot
express. The move-blocked lattice is ≤ 60 candidates per tenant, so it is
solved *exactly* by enumeration, with two rounds of coordinate descent
coupling tenants through the shared objective and cluster constraints.

**Objective shape**: the reported SLO violation is bounded to [0,1], but the
optimizer minimizes `log1p(excess)` — unbounded so deep overload keeps a
gradient, compressive so one hopeless tenant-step cannot dominate the plan.
A switch penalty per changed knob provides hysteresis. Congestion is graded
in overload (quadratic above ρ=0.95, capped) for the same reason.

## Files

| file | contents |
|---|---|
| `model.py` | system model shared by all controllers: work units, congestion, cache hit curve, tier latency/cost, SLO targets, Jain index |
| `controller.py` | JCAC: forecast, lattice enumeration, coordinate descent, budget/cluster constraints |
| `baselines.py` | `static`, `hpa` (utilization), `keda` (event rate), `layered` (all three knobs, each locally tuned, no joint view) |
| `simulate.py` | trace replay engine; controller sees bucket *t*, is scored on bucket *t+1* |
| `sweep.py` | α/β/γ grid → `../results/jcac/{sweep.csv, baselines.json, pareto.png}` |
| `test_jcac.py` | `python -m unittest` — model invariants, guardrails, hysteresis, surge response |

## Reproduce

```bash
cd research/jcac_sim
python -m unittest                                  # invariants
python simulate.py --trace ../traces/out/azure_synth.csv.gz \
    --controller jcac --max-steps 120               # one run
python sweep.py --trace ../traces/out/azure_synth.csv.gz \
    --max-steps 120 --out ../results/jcac           # W30 exploratory sweep
```

The W30 sweep (`pareto.png`) is exploratory and no longer cited by the paper: one
trace, one seed, no arrival jitter, 20 minutes, and the published unanchored
controller. On it, no JCAC weight setting dominates `hpa` ($0.348, violation 0.102)
or `keda` ($0.348, 0.102); the nearest settings are cheaper-but-worse or
better-but-dearer, so it does not
support a "Pareto-dominates the baselines" reading (audit 2026-09-26). The
paper's weight-sweep evidence is `research/analysis/analysis_dominance.py`.

Results on `azure_synth` (seed 42, first 20 min, defaults α=1 β=2 γ=0.5):
JCAC roughly halves mean SLO violation vs HPA/KEDA with the best Jain
fairness of any controller, while `layered` — every knob locally tuned but
no joint view — spends ~7× more than JCAC for *worse* SLO. HPA and KEDA
converge to identical configurations on this trace because per-tenant RPS
is low and their violations come from the model tier, a knob they cannot
see. Exact numbers regenerate deterministically from the commands above.

The model's absolute numbers are simulator artifacts; only controller
*rankings* under identical assumptions carry to the paper. Live-cluster
numbers come from the W33 harness.
