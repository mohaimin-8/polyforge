# W34 baseline tuning — grid search evidence

Every tunable baseline was swept over a parameter grid before the final
runs; the committed grids (`grids/*.csv`) are the proof that no baseline
runs untuned defaults. `tuned.yaml` holds the winners; the harness loads
it automatically.

## Protocol

- **Tuning slice**: 3 workload classes (`crud_bursty`, `ai_cacheable`,
  `agentic`) × 2 tenant mixes (`uniform`, `whale`) × 2 jittered reps on
  the `medium` cluster — 12 runs per grid point, disjoint seeds from every
  evaluation experiment (nothing is tuned on evaluation cells).
- **Objective**: the same composite the paper evaluates and PolyForge
  itself optimizes, J = 1·cost_norm + 2·mean_violation + 0.5·(1−Jain).
  Tuning baselines on PolyForge's own objective gives them their best
  possible showing on the terms of the comparison.
- **Reproduce**: `cd eval && python baselines/tune.py` (deterministic;
  seeds are sha256-derived, not `hash()`).

## Results

| baseline | grid | best | J at best | J at vendor default |
|---|---|---|---|---|
| hpa | target_rho ∈ {0.3…0.8} | **0.3** | 0.555 | 0.628 (0.8) |
| keda | rps_per_replica ∈ {2…16} | **2.0** | 0.573 | — (no universal default) |
| gptcache | target_rho ∈ {0.3…0.8} | **0.3** | 7.362 | 7.58 (0.4-edge of first sweep) |
| firm | lr × ε × w_slo (27 combos) | **lr=0.1, ε=0.1, w_slo=4** | 0.619 | 0.632 (lr=.3, ε=.1, w_slo=2) |
| vtc_replica | target_rho ∈ {0.3…0.8} (session 15, `--only` merge; frozen rows untouched) | **0.3** | 0.555 | 0.628 (0.8) |
| static | — (no knob: over-provisioned to `replica_max` by definition) | — | — | — |

## Why the utilization grids are bounded below at 0.3

J improves **monotonically** as utilization targets fall (see
`grids/hpa.csv`: J rises steadily from 0.555 at ρ=0.3 to 0.628 at ρ=0.8):
replicas are cheap relative to the violation weight, so an unconstrained
sweep drives every utilization controller toward "provision everything,
always" — at which point it stops being HPA and becomes the `static`
baseline, which the matrix already contains. We therefore tune within the
vendor-sane operational envelope (K8s HPA's shipped default target is
0.8 CPU; 0.3 is already a generous production floor) and give the
degenerate end of the spectrum its own honest representative (`static`).
An exploratory sweep down to ρ=0.2 / 1 rps confirmed the monotonicity and
is why the bound exists.

Two consequences worth stating for reviewers:

1. The tuned baselines sit at the *SLO-generous* end of their envelope —
   they concede cost, not violations. PolyForge's claim is dominating that
   trade-off, so this is the strongest reasonable configuration to face.
2. `gptcache`'s J is dominated by inference spend (its posture caches
   everything and pays LRU miss economics — see
   `harness/systems.py::lru_miss_cost_factor`); no target_rho rescues it,
   which is itself the W28 result restated at system level.

## FIRM notes

The FIRM-replica agent (re-implementation of the OSDI '20 RL controller,
replica knob only — `research/jcac_sim/baselines.py`) is seeded per run;
the grid selects its learning hyper-parameters. w_slo=4 winning over
w_slo=1 matches FIRM's design intent: SLO recovery outranks utilization
efficiency. Qualitative validation against the paper (SLO-violation
mitigation vs static allocation under load spikes) is asserted in
`research/jcac_sim/test_jcac.py::test_firm_learns_to_scale_out_of_violation`.
