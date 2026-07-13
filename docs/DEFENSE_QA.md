# Defense Q&A — the hard questions, answered before they are asked

Written 2026-07-13 (session 17). Each entry is a question a competent
examiner or artifact reviewer should ask, the honest answer, and where the
evidence lives. Nothing here softens a result; the project's position is
that every one of these has a better answer *stated in advance* than
improvised at the podium.

## 1. "Your headline numbers come from your own simulator."

True, and framed that way everywhere ("decision quality under the stated
system model"). Three things bound the gap:

- **Calibration direction.** The congestion form was measured against a
  real inference server (`research/calibration/CALIBRATION.md`): the sim
  over-penalizes high utilization (fitted a=0.86 vs assumed a=1.0), i.e.
  the model errs *against* PolyForge's lean postures. The GPU tier table
  (`TIER_BENCH.md`) confirms tier ordering on real hardware.
- **Real demand and real prompts.** The cost/forecasting claims are
  restated on replayed BurstGPT demand (n=96 confirmatory,
  `RESULTS_TRACE2.md`); the cache claims are measured on real
  LMSYS-Chat-1M prompts, not synthetic traffic.
- **Live ordinal check.** Ground rule 4: absolutes are never compared
  across substrates; the kind-cluster experiment asks only whether the
  sim's *ranking* reproduces live. The hpa arm is verified; the jcac arm
  is wired and gated behind an executable actuation check
  (`docs/PHASE7_JCAC_PLAN.md`).

What is *not* claimed: absolute dollar or latency numbers transferring to
any production fleet.

## 2. "The live figure only exercises replica control."

Correct, and disclosed proactively: the live replay data plane burns fixed
CPU per request kind, so the cache-size and model-tier knobs are inert
live — the live ordinal comparison validates the *replica-control
projection* of the joint controller, not all three knobs. The figure
caption must say exactly that (pre-written in `PHASE7_JCAC_PLAN.md`). The
cache knob's realism is carried separately by the LMSYS protocol
(`SEMANTIC_CACHE.md`, `CACHE_PRECISION.md`); the tier knob's by the GPU
tier bench.

## 3. "You designed the v3 overload cells so your system would win."

The cells were sized by arithmetic on the *committed model constants* —
burst climb exceeding the shared ±2-replicas-per-interval actuation clamp
— and that arithmetic is pinned by unit tests, not tuned by trial runs
(`eval/tests/test_harness.py::TestV3OverloadCells`, PREREG_V3 §3). Three
protections: (a) the prereg named the sole treatment and hypotheses before
any run; (b) `ramp_gentle` is a same-envelope control where the alleged
advantage should and does *not* appear; (c) the confirmatory H1′ still
FAILED — the cells did not manufacture a win; they isolated a mechanism
(H2′ PASS: seasonal < trend on violation, no cost premium). A designed
regime with an honest null inside it is evidence of measurement, not of
rigging.

## 4. "p = 10⁻³³? Really?"

At hundreds of paired, seeded simulator runs, tiny p-values measure the
simulator's determinism, not the effect. The citation unit is the paired
effect size with a confidence interval:
`research/analysis/EFFECT_SIZES.md` restates the real-trace campaigns as
d_z with 95% bootstrap CIs (n=96 confirmatory: J d_z −0.43…−0.47, all CIs
excluding 0; the +0.07 violation trade shown with its own CI). P-values
appear once, as the prereg gate outcome, and are never headlined below
~1e-6.

## 5. "Your SLO story moved the goalposts."

The ledger says otherwise, and the ledger is the answer: six
pre-registrations, all committed and pushed before their runs, two
confirmatory SLO failures published as failures (v2 H1, v3 H1′), one
mechanism confirmation (H2′), and a stopping rule that forbids a third
attempt inside this thesis (`RESULTS_MASTER.md` consolidates it). The
final SLO claim — violation parity with tuned reactive scalers at
−43…−48% cost — is what survived, not what was hoped for. Goalpost-moving
is invisible when it happens in private; this project's whole point is
that it happened in public, in the git history.

## 6. "Jain's index over SLO satisfaction is not fairness as OSDI defines it."

Correct: VTC and successors (Equinox, D²LPM, Justitia) define *service*
fairness at token granularity inside a continuous-batching engine.
PolyForge's fairness is at a different altitude — capacity allocation
across tenants at the control-plane level — and the claims are scoped to
that altitude. The `vtc_replica` baseline is a replica-level transplant of
VTC's least-service-first idea (stated in its docstring), not a
reimplementation of the OSDI scheduler; beating it on Jain (0.9705 vs
0.9599, d_z −1.12) says PolyForge allocates capacity more evenly, not that
it schedules tokens more fairly. Token-level fair schedulers are
complementary — they would run *inside* each replica pool PolyForge sizes.

## 7. "A cache hit that serves the wrong answer isn't a saving."

Agreed — that is why hit *precision* is measured, on the same real
dataset, with the protocol frozen before the first number was seen
(`cache_hit_precision.py` → `CACHE_PRECISION.md`): P(served response
agrees with the actually-received response | hit), with same-model and
first-turn strata to bound the two declared confounders, and
incorrect-hits-per-1k reported next to savings-per-1k. No single
"quality-adjusted dollar" is invented, because pricing a wrong answer is
application-specific. Staleness/TTL remains out of scope and is listed as
such.

## 8. "Your cost model mixes two economies."

It does, deliberately, and they are separable in the model: replica-hours
are priced as infrastructure ($0.048/replica-hr, and the live arm meters
real replica-seconds), while model-tier calls are priced at market API
ratios (1:10:100). A self-hosting operator should read the tier ratios as
relative GPU-time costs (the tier bench measured 1:1.5:16.6 latency ratios
on real hardware — same ordering, compressed scale, disclosed in
`TIER_BENCH.md`). Spot pricing, MIG partitioning, and fractional GPUs are
cost levers below the model's altitude and are named as out of scope.
Reconfiguration friction is not ignored: the realism ablation bills
replica startup lag and cold caches (`ADVANCED.md`, fig 15-16) — though
multi-minute large-model pulls exceed what it models, and the limitation
says so.

## 9. "The 2026 serving stack scales on KV pressure and queue depth, not replicas×cache×tier."

PolyForge is a control plane, not an inference engine. The three knobs are
the *portfolio* level of the stack; llm-d/AIBrix-class systems are the
*actuation* level, and the mapping is direct (RELATED_WORK.md §4:
replicas → decode-pool size / vLLM pod count; cache MB → gateway
semantic-cache budget, sibling of prefix-cache budgets; tier → model
routing). The demand signal is deliberately behind an interface
(`planner.DemandSource`) so a token-level source (queue depth, KV
pressure, TTFT) can replace the request-rate source without touching the
planner. What PolyForge adds that the 2026 stack lacks is the *joint*,
dollar-accounted, fairness-aware decision across tenants — none of
llm-d/AIBrix/Dynamo optimizes cost-vs-SLO-vs-fairness jointly across a
tenant portfolio.

## 10. "Were the baselines actually tuned, or strawmen?"

Grid-searched on the paper's own composite objective, per baseline, with
the sweeps committed (`eval/baselines/TUNING.md`, grids in
`eval/baselines/grids/*.csv`). The grids are bounded at a vendor-sane
envelope because J improves monotonically toward over-provisioning — an
unbounded grid would tune every baseline into a cost blowout and *flatter*
PolyForge. Iso-cost variants exist precisely to remove the "it just spends
less" objection, and the gate they impose is reported FAIL-honest where it
fails (`RESULTS_V2.md` §iso-cost).

## 11. "Why p95 and not p99?"

A deliberate, documented deviation (`eval/README.md`): the sim's latency
estimator is a queueing approximation whose tail beyond p95 is not
credible, and pretending otherwise would be false precision. The live
path measures real wall-time latency and can report any percentile; p99
live restatement is listed as future work, not silently substituted.

## 12. "One demand trace, one prompt dataset — does anything generalize?"

The claims are scoped to the traces named: BurstGPT (10.6M requests,
335 days) for demand, LMSYS-Chat-1M for prompts. One honest
generalization *failure* is already published — the synthetic stand-in's
seasonal-forecasting win did not transfer to the real trace
(`FORECAST_TRACE_REAL.md`); Holt did, and the recommendation followed the
data. FaaS/VM traces (Azure Functions, Alibaba) were considered and
descoped as non-LLM demand (RELATED_WORK.md gap table). A second LLM
demand trace is the single highest-value external-validity addition if
one becomes available.

## 13. "Eight tenants is not multi-tenancy at scale."

Eight tenants per cluster is the factor under study (composition, not
population — `eval/harness/workloads.py`). The scaling bound is now
*measured*, not scoped away: `research/analysis/PLANNER_SCALING.md`
drives the deployed planner code path from 8 to 256 tenants on one
laptop core — fitted growth exponent 1.45 (the joint fairness term
couples tenants, so super-linearity is the measured cost of jointness),
p95 cycle time first exceeding the operator's 3 s request timeout at
128 tenants and the 10 s control period at 256. Two honest notes travel
with it: past the timeout, the failure mode is the designed one (hold
last good plan, `fallback` status — plan_runner.go), and coordination
costs outside the planner (CR write fan-out, telemetry aggregation) are
not covered by the microbenchmark. Scaling levers past the crossover are
named there (planning cells, incremental fairness partial sums, faster
inner loop).
