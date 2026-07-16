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

As measured: at the τ=0.85 operating point, precision is 0.313 overall,
0.443 same-model, 0.353 first-turn, 165.5 incorrect hits per 1k queries —
and the built-in calibration (τ≥0.95 near-duplicate prompts agree only
33.8% at ρ=0.70) shows the absolute level is dominated by LLM response
stochasticity, not caching errors alone. The claim is therefore the
*shape*: precision rises monotonically as τ tightens while hit rate
falls, and same-model precision is ≈2× cross-model. A production
deployment caching a single model's responses sits in the most favorable
stratum. This is a harder-nosed quality statement than any surveyed
cache paper publishes, and it cost the headline nothing that was
truthfully there.

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
descoped as non-LLM demand (RELATED_WORK.md gap table). The second LLM
demand trace is now measured: the Azure LLM inference 2024 release
(44.1M requests, two production workloads) through the identical frozen
decomposition (`FORECAST_AZURE.md`). The mechanism boundary reproduced —
seasonal forecasting wins only on the stream with a genuine daily cycle
(code, −15.6% RMSE at autocorr 0.57), loses on the weakly-periodic conv
stream, and pooling streams destroys forecastability — which is direct
external support for per-tenant forecasting and for scoping seasonal
claims to measured periodicity, exactly as v3 H2' bounded them.

## 13. "Eight tenants is not multi-tenancy at scale."

Eight tenants per cluster is the factor under study (composition, not
population — `eval/harness/workloads.py`). The scaling bound is
*measured*, not scoped away: `research/analysis/PLANNER_SCALING.md`
drives the deployed planner code path from 8 to 256 tenants on one
laptop core — fitted growth exponent 1.45 (the joint fairness term
couples tenants, so super-linearity is the measured cost of jointness),
p95 cycle time first exceeding the operator's 3 s request timeout at
128 tenants and the 10 s control period at 256. Two honest notes travel
with it: past the timeout, the failure mode is the designed one (hold
last good plan, `fallback` status — plan_runner.go), and coordination
costs outside the planner (CR write fan-out, telemetry aggregation) are
not covered by the microbenchmark.

The named scaling lever is now executed, not just named (Wave 4,
`PLANNER_CELLS.md` + `PLANNER_CELLS_DEALIAS.md`, both pre-registered):
partition the portfolio into fixed K=32 planning cells, each planned by
the deployed `PlannerCore.plan` on an independent replica. **PS-H1 PASS:
per-cell p95 stays flat at ~195 ms through 1024 tenants** (fitted
exponent 0.27) where the monolithic joint plan takes **71 s** — 1024
tenants become deadline-feasible. The fairness cost of independent-cell
planning depends on the assignment rule, and measuring both rules is the
useful result: naive round-robin-on-index *aliases* with periodic tenant
structure (a whale every 8th tenant collapses into a few cells when the
cell count is a multiple of 8) and global Jain fell 0.99 → 0.90
(PS-H2 FAIL, published with the diagnosis); a **hash-based assignment
that decorrelates cell membership from index recovers it — worst ΔJain
−0.0053 through 1024 (PF-H1 PASS).** The engineering lesson (hash cells,
not index round-robin) is itself measured. Contiguous-by-budget
assignment remains the adversarial worst case, named as future work.

## 14. "Your live ordinal check disagreed with the simulator."

It did, and it is published as measured (`PHASE7_ORDINAL.md`, protocol
frozen at 650ce29 before any live number existed): the J winner flips in
both cells. The content of the disagreement matters: live, the two arms
land at *parity* on every metric — J separations of 0.002 with overlapping
rep ranges, violations zero for both arms in both cells — while the sim
separates them decisively. Two mechanisms, one disclosed pre-run, one newly
measured: (1) in `ai_cacheable` the sim's jcac win flows through the cache
economy, and the live cache knob is inert by construction — the frozen
caption's disclosure made concrete; (2) in `crud_bursty` the sim's HPA
concedes 0.0417 violation where real HPA at this amplitude never violates —
the sim overestimates reactive lateness in this cell, a model datum that
had *favored* jcac in-sim and is now on the record. What the live campaign
does establish: the full jcac loop runs live under a mechanical actuation
gate, capacity parity held, and no thesis claim rested on live ranking
reproduction — the claims stand on the sim and real-data-replay substrates,
scoped as such throughout. The named closure is a live substrate with real
cache and tier levers.

## 15. "You never tested cost sensitivity to the tier-price ratios."

Now measured, and it holds. The matrix prices tiers at API ratios
(1:10:100) while the GPU bench measured 1:1.516:16.64 (same ordering,
compressed scale, disclosed). Two closures, pre-registered before either
ran (`PREREG_TIER_RATIO.md`, pushed at 7deb6a3): the *accounting* reading
(`BREAKEVEN_TIER.md`, decisions frozen, exact re-pricing over 60 price
vectors) finds no aggregate cost win reversed anywhere in the grid; the
*decision* reading (`RESULTS_TIER_RATIO.md`, the full 1,800-run headline
matrix rerun at the measured GPU corner so both the world's bill and every
controller's beliefs move together) confirms the cost win against all five
baselines survives (TR-H1 PASS 5/5, p ≤ 6.8e-47) and so does composite J
(TR-H2 PASS 5/5). The interesting datum: given cheap mid-tier inference the
joint controller *re-plans* into it (mid-tier steps 1.7% → 13.7%) and still
wins cost — adaptation the fixed-decision reading cannot show. `SENSITIVITY_J.md`
(weights) and now this (prices) are orthogonal robustness axes; both survive.

## 16. "Your 'calibration errs against us' line is only half true."

The congestion fit (a=0.86) errs against PolyForge's lean postures; the
assumed cache curve erred *for* the in-matrix cache benefit (measured h(K):
hmax 0.285 vs the assumed 0.85 — `SEMANTIC_CACHE.md`). Both directions are
documented, and the second is now quantified rather than conceded. The named
closure ran: `PREREG_HK_ADOPTION.md` (pushed at 7deb6a3), the full 1,800-run
headline matrix rerun under the *measured* curve — pessimistic for every
cache-using system, ours included (`RESULTS_HK_ADOPTION.md`). Effect as
expected and as measured: realized cache hit rate halves (jcac 0.106 → 0.051),
the cache-centric baseline is devalued most (gptcache mean cost 13.5 → 90.5),
and the joint controller *stops paying for cache it can no longer use*
(mean cache 362 → 136 MB). The ranking survives: HK-H1 (composite J) PASS
5/5 and HK-H2 (cost) PASS 5/5, because PolyForge's advantage was never
primarily the cache curve — the J margin against HPA narrows (−27.5% →
−19.4% aggregate) but does not close. The differential-effect-on-rankings
gap this question named is measured shut.

## 17. "Pre-registration without a registry is self-refereed."

The anchor is GitHub push-event forensics: every protocol was committed and
pushed before its first run, and amendments were declared pre-run in the
same history. That is weaker than an external registry and stronger than
nothing; the strongest evidence that the mechanism was not gamed is what it
produced — failed confirmatory hypotheses and further negatives, published
in full. The named upgrade is now in motion: `OSF_REGISTRATION.md` is the
registry-ready mirror index, carrying every Wave 2/3 protocol with its git
push anchor (commit + ISO timestamp) so an OSF registration is a
transcription, not a re-derivation. Submitting to OSF needs a browser and an
account and is the one human step, flagged there exactly as the Zenodo/GHCR
items are in `docs/RELEASE_CHECKLIST.md`; until each DOI lands the git anchor
stands and is independently checkable.

## 18. "Your per-tenant forecasting claim rests on aggregate streams."

Yes — BurstGPT and the Azure workloads are aggregate demand streams, not
per-tenant SaaS series. "The winner tracks each stream's measured
periodicity" supports per-tenant forecasting by extrapolation across one
aggregation level, and the thesis says so. That extrapolation is now
measured one level down (`PREREG_PSEUDO_TENANT.md` pushed at 7deb6a3,
`PSEUDO_TENANT.md`): the same traces split by Model × Log Type into 19
qualifying sub-streams, driven through the identical frozen forecast
protocol. The boundary reproduces with **zero counterexamples** — no weakly
periodic sub-stream (detrended autocorrelation < 0.5) shows a material
seasonal win (PT-H1 PASS), every one of the six periodic sub-streams does
(PT-H2), and **three different forecasters win across sub-streams**
(persistence / Holt / seasonal), which is precisely what a per-tenant
pluggable forecasting layer exists to exploit (PT-H3). The claim is no
longer an extrapolation across one aggregation level; it is measured across
two. True per-tenant SaaS series remain unavailable and that limit stands.

## 19. "Where is the chaos testing? And was the security attack real?"

Both boundaries are now addressed — one measured in sim, both live halves
pre-registered with the desk work landed. Failure injection: the sim chaos
campaign ran (`PREREG_CHAOS_SIM.md` at 7deb6a3, `RESULTS_CHAOS_SIM.md`,
540/540) — planner-outage and replica-kill injected into the scoring engine,
controllers blind. A dead planner for one minute still beats a *healthy* HPA
(CH-H1 PASS, dz −1.69); under an identical 50% replica kill jcac keeps its J
win (CH-H2 PASS, dz −1.79); recovery lands within 2–5 steps. The result that
turns the objection around: a one-minute control freeze costs jcac less
(ΔJ +0.0063) than it costs a frozen HPA (ΔJ +0.0109, p 3.8e-7) — a
proactively-planned held configuration ages better than a reactively-lagged
one, so the central-planner "single point of failure" is, at this fault
scale, the *more* graceful failure. The **live** chaos half (planner crash
mid-burst and apiserver throttling on the kind cluster) is pre-registered
(`PREREG_LIVE_CHAOS_P99.md`, deferred to a Codespace session), and it also
closes the p99 question (#11): the export now emits live p99 alongside p95
(desk-landed, unit-tested), kept out of the sim tables because the sim is a
p95 estimator by construction. Security: the attack is no longer only a
sim-timing reading — it was **executed against the real `cmd/ai-gateway`
process** over HTTP (`PREREG_WIRE_ATTACK.md` + its pre-run amendment,
`RESULTS_WIRE_ATTACK.md`). The load-bearing defense hypothesis holds on the
wire: under per-tenant isolation the attacker's membership AUC is **0.502
(95% CI [0.384, 0.612]), zero cross-tenant hits — chance (WA-H1 PASS)** on
the real cache/HTTP path, not a model. The deliberately-insecure shared
posture (the pre-registered, unit-tested `POLYFORGE_CACHE_SHARED` flag) leaks
perfectly on loopback (AUC 1.000, exactly the 50 victim-warmed secrets), and
the measured hit/miss gap is 15.6 ms vs 98.7 ms. Two honest scope notes: the
substrate is the gateway *process* over loopback (real HTTP, real cache, real
timing, but no WAN RTT jitter — which would only *weaken* the shared-posture
number, never the defense), and the deployed offline embedder is lexical, so
the threat measured is exact-prompt membership (the conservative, embedder-
agnostic form). What stays deferred to a Codespace campaign: the live chaos
run itself (fault injection wired into the kind harness) and a live p99
*number* (the export is landed and unit-tested; persisting it through the
harness is the remaining plumbing). Membership inference remains the only
threat class studied.
