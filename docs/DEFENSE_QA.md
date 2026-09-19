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

**The replay half now generalizes too (session 27,
`PREREG_TRACE_AZURE.md` → `RESULTS_TRACE_AZURE.md`):** the *headline
ranking* was replayed on the Azure trace itself — 72 non-overlapping 3 h
windows tiling all 216 h, 360 runs, round-robin pseudo-tenantization
disclosed (within-stream pseudo-tenants are near-perfectly correlated:
the harder packing regime). **HT-AZ PASS with disclosure**: jcac beats
tuned HPA/KEDA/FIRM on paired J at p ≤ 5.3e-22 with d_z −1.64…−1.85 and
cost −42% per window, carrying the same disclosed attainment-for-cost
trade as BurstGPT (violation +0.019 vs HPA/KEDA, p=1.8e-05). The Holt
secondary replicates (`jcac_v2` − `jcac` J −0.013, p=0.0039), and the
margin is larger on code-dominant windows. Two independent real traces
now show the same ranking with the same disclosed trade; per ground
rules the samples are never pooled.

**Adjudicated (sessions 37–38, see #29):** the −42% and the BurstGPT −70% are withdrawn as like-for-like cost claims — at eviction parity Azure is −7.1% (TP-H1a/b PASS) and BurstGPT fails its rank test (TP-H1a FAIL); at budget parity both fail (BP-H1a/b). The *J* ranking and the forecasting boundary in this answer stand; the cost percentages do not.

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

**The end-to-end residual is now measured too (session 27,
`PREREG_TENANT_SCALE.md` → `RESULTS_TENANT_SCALE.md`):** the full matrix
J at 32 and 64 tenants, per-tenant world byte-identical to the 8-tenant
mixes, cluster caps scaled linearly. **TS-H1a PASS at 32 tenants** —
jcac_anchored beats tuned hpa/keda/concurrency on J, |d_z| 1.02–1.22,
p ≤ 2.2e-4. At 64 tenants hpa and concurrency PASS (d_z ≈ −1.3); keda
misses the frozen p<0.01 conjunction bar at p=0.0102 with d_z=−1.02, so
**TS-H1b is an honest FAIL, reported as direction-consistent** (the
prereg's declared underpowered case; nulls ledger). The descriptive
trend is the answer to this question: the J margin *grows* with
portfolio width (vs HPA: d_z −0.96 at 8 → −1.19 at 32 → −1.32 at 64) —
the advantage is not an 8-tenant artifact — and portfolio fairness holds
(jcac Jain 0.994 / 0.9999 at 32/64).

## 14. "Your live ordinal check disagreed with the simulator."

It did, and it is published as measured (`PHASE7_ORDINAL.md`, protocol
frozen at f0157cd before any live number existed): the J winner flips in
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
(1:10:100) while the GPU bench measures a far flatter ratio (same
ordering, compressed scale, disclosed).

**Correction, session 44 — the flattening is larger than first reported.**
The figure previously quoted here, **1:1.516:16.64**, came from a run whose
`large` row was CPU-offloaded: a 16 GB card could not hold the 7B, so that
row measured the offload, not the tier. Re-measured on `GPU T4 x2` with
**zero modules offloaded to cpu/disk**, the ratio is **1:1.475:1.518**
(`tier_bench_t4.csv`). The mid/small ratio reproduces (1.516 → 1.475); the
large/small ratio collapses from 16.64× to 1.518×, because "super-linear at
large" was the signature of layers in host memory rather than a property of
the tier. **This strengthens the answer below rather than weakening it** —
the point is that measured serving ratios are far flatter than the
1:10:100 price table, and the corrected measurement is flatter still.
**One consequence is open and disclosed:** the frozen 60-vector grid's
`r_large` values start at 5.0, so the corrected corner (1.518) lies outside
the grid that was tested, and the analysis below therefore never evaluated
the true hardware ratio. That is being closed under a new pre-registration
with exactly one changed factor, not by re-running a frozen grid. Two closures, pre-registered before either
ran (`PREREG_TIER_RATIO.md`, pushed at d34a9ce): the *accounting* reading
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
closure ran: `PREREG_HK_ADOPTION.md` (pushed at d34a9ce), the full 1,800-run
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
transcription, not a re-derivation. **Done 2026-09-19:** all 48 protocols,
the index and the commit map are registered on OSF as one frozen, public
mirror — DOI https://doi.org/10.17605/OSF.IO/DYZKV. Say exactly what it is:
a post-run, independent, non-editable witness of the protocols as pushed;
the git push timestamps remain the pre-run anchor, and
`scripts/check_preregs.py` (CI, full history) is what proves no registered
text moved after its result. **Do not say** "pre-registered on OSF before
the runs" — they were pre-registered in git before the runs and mirrored to
OSF after.

## 18. "Your per-tenant forecasting claim rests on aggregate streams."

Yes — BurstGPT and the Azure workloads are aggregate demand streams, not
per-tenant SaaS series. "The winner tracks each stream's measured
periodicity" supports per-tenant forecasting by extrapolation across one
aggregation level, and the thesis says so. That extrapolation is now
measured one level down (`PREREG_PSEUDO_TENANT.md` pushed at d34a9ce,
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
campaign ran (`PREREG_CHAOS_SIM.md` at d34a9ce, `RESULTS_CHAOS_SIM.md`,
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

---

*The four questions below were added 2026-07-17 (session 23) as the harder
follow-ups a sharp examiner reaches for once §1–19 are conceded. They are the
project's currently-sharpest open pressure points; each is answered honestly,
and where the honest answer is "not yet closed," the closure path is named
rather than the question deflected.*

## 20. "Your one live experiment disagreed with the simulator — so why trust any simulated number?"

This is the deepest question in the defense, and "we scoped it out" is not the
answer. The disagreement is real, pre-registered, and published
(`PHASE7_ORDINAL.md`, protocol frozen at f0157cd before any live number
existed): the simulator's J winner flips in both cells. What earns the
simulator its remaining credibility is the *direction and mechanism* of the
flip, not a hand-wave past it.

Both cells moved to **parity**, not to a baseline win — and both mechanisms
were identified, and both had *favoured* jcac in-sim:

1. In `ai_cacheable` the sim's jcac edge flows through the cache economy, and
   the live data plane's cache knob is inert by construction (fixed CPU per
   request kind) — a test-substrate limitation disclosed pre-run in the frozen
   figure caption, not a discovery.
2. In `crud_bursty` the sim's HPA concedes 0.0417 violation where real HPA at
   that amplitude never violated — the sim *over-penalised* reactive lateness,
   a model datum that had been working *for* jcac and is now on the record
   against it.

So the live contact with reality removed two simulator advantages that were
**ours**; it did not expose a hidden baseline advantage. The honest reading:
the simulator is a decision-quality model whose separations are directionally
right where its mechanisms are live-active and overstated where a knob is
inert — and the campaign that established this was run by us, pre-registered,
and published as a null (it is the eighth entry in the honest-nulls ledger).
The headline claims are scoped to the simulator and real-data-replay
substrates throughout (ground rule 4); no claim ever asserted live-ranking
transfer.

**Do not say:** "the live run confirms the simulator" (it does not) or "the
disagreement doesn't matter" (it does — it bounds the sim's credibility, which
is exactly why the all-knobs-live closure in §21 is named as the primary
remaining experiment).

## 21. "The joint 3-knob controller — your central novelty — has never run live with all three knobs. Isn't the contribution unproven?"

Correct on the fact, and it is the honest number-one open item; the answer is
to be precise about what *is* and *is not* established, and to name the exact
experiment that would close it.

**Established live** (Phase 7, session 19): the full jcac control loop runs
end-to-end on a real kind cluster under a mechanical actuation gate, capacity
parity held, zero degradation — but on the *replica axis only*, because the
kind data plane burns fixed CPU per request so the cache and tier levers are
inert there. "The loop runs live" is proven; "the joint optimisation's benefit
reproduces live" is not.

Three things bound the gap without closing it: (a) each knob's realism is
carried separately on a real substrate — the cache knob by the LMSYS hit-rate
protocol *and* the over-the-wire isolation test, the tier knob by the GPU tier
bench; (b) the `-joint` ablation that establishes jointness matters (+2884%
cost when the knobs are decoupled against the published layered comparator —
a margin that shrinks by 86.4% once that comparator's absorbing-tier defect is
repaired, `RESULTS_LAYERED_FIX.md` LF-H1; the smaller number is the claim) is a
sim mechanism result, not a headline absolute; (c) the closure is now pre-registered
(`research/analysis/PREREG_WAVE4_LIVE_PLANE.md`) and gated only on a
GPU-capable host: a real cache/tier data plane where all three knobs actuate,
with the falsifier stated in advance (if the joint arm does not beat the
best single-knob arm on cost-at-fixed-fairness on that substrate, the central
claim is wounded).

**Do not say:** "the simulator is enough." Name the all-knobs-live run as the
primary remaining experiment and state exactly what result would falsify the
claim.

## 22. "−70% is against HPA/KEDA/FIRM, which over-provision. Against a well-configured 2026 serving engine, does the win survive?"

First, the premise: the −70% is **withdrawn** as a like-for-like cost claim
(#29) — what survives is the feasibility result, that the per-tenant budget
is satisfiable only by a controller holding the tier knob. The altitude
answer below applies to that surviving claim exactly as it applied to the
percentage. PolyForge is a control plane; whatever it wins is an
orchestration-level result against tuned-but-reactive autoscalers, and it is
never claimed against engine-level state of the art. Engine-level efficiency (continuous batching,
disaggregated prefill/decode, KV-cache management) comes from the *actuation*
layer the three knobs sit above; the mapping is explicit (`RELATED_WORK.md`
§4: replicas → decode-pool size, cache → gateway semantic-cache budget as a
sibling of prefix-cache budgets, tier → model routing).

What PolyForge adds is **orthogonal**, not competitive: the joint,
dollar-accounted, fairness-aware decision across a *tenant portfolio*, which
none of llm-d / AIBrix / Dynamo optimises — they maximise a single
deployment's throughput, not a multi-tenant portfolio's cost-vs-SLO-vs-fairness
trade. So the honest claim is **composability**: PolyForge's decisions ride on
top of an engine-optimised stack and the two sets of gains multiply rather
than compete.

What is genuinely untested, and named as such in the limitations: the residual
orchestration-level win *on top of* an engine-level-optimised stack — because
some fraction of the (withdrawn) −70% was precisely the over-provisioning a good engine would
not do, so the number would compress against a strong engine even though the
portfolio-level decision still adds value.

**Do not say:** "−70% versus the state of the art" — or −70% at all, now
that it is withdrawn (#29). **Do say:** only a tier-holding controller can
keep a per-tenant budget, measured against tuned-but-reactive autoscalers,
full stop.

**The reactive half of this question is now measured, not argued
(session 27, `PREREG_CONCURRENCY.md` → `RESULTS_CONCURRENCY.md`):** the
matrix gained a Knative-KPA / AIBrix-shaped **concurrency/queue-depth
autoscaler** — the 2026 stack's reactive *signal* (in-flight work,
superlinear near saturation, stable-window scale-down), grid-tuned on
the paper's own J per the W34 protocol. Tuned, it is the **strongest
reactive baseline in the project** (tuning-slice J 0.543 vs HPA 0.555,
KEDA 0.573; in-matrix it beats tuned HPA on J at p=5.7e-07 and on
violation at d_z=−0.52). Against it, jcac_anchored wins the composite J
at **d_z=−0.96 (p=1.3e-44) with violation parity** (diff −0.0005,
p=0.88 — no attainment trade needed) at −37% cost (**CQ-H1/H2 PASS**).
The engine-level half of the answer (composability with llm-d-class
actuation) stands unchanged above; what this closes is "would a
modern-signal *reactive scaler* have closed the gap" — measured: no.

## 23. "Your defence is 'partition the cache per tenant.' That is almost tautologically secure — where is the research contribution?"

Three parts, and the third is the one that matters.

1. **It is a measured frontier, not an asserted property.** The contribution
   is not "partitioning is secure"; it is the *utility cost* of security
   quantified against the two mitigations operators actually reach for first.
   Partitioning removes the AUC-0.88 leak to chance (0.50) at a measured 24%
   latency cost with 76% of hits kept — while response padding never gets below
   AUC 0.73 and TTL jitter reaches chance only at ~90% hit loss (`ADVANCED.md`,
   figs 16–17). That the *cheapest-utility* option is also the *most secure*
   one is not obvious a priori; it is the citable result.
2. **It is now executed, not designed.** WA-H1 PASS over the wire against the
   real gateway (AUC 0.502, `RESULTS_WIRE_ATTACK.md`) makes "partitioning
   defends" an executed measurement rather than a design assertion.
3. **The genuinely hard question is named as open.** Can a *shared* cache — the
   one that keeps the hit-rate benefit partitioning forfeits — be defended
   without collapsing utility, via noise injection, bucketing, or differential
   privacy? PolyForge's answer today is "don't share," and the honest framing
   is that we mapped the leakage/utility frontier of the three obvious
   mitigations and identified secure-sharing as the question worth a paper.

**Do not say:** "we solved cache side channels." We characterised them, showed
the cheap-and-obvious mitigations are the weak ones, and left secure sharing
as explicit open work.

---

*The two questions below were added 2026-07-19 (session 24), when the Wave 5
structural-form program ran. They are answered with measurements that did not
exist when §1–23 were written; §24's campaign table is the direct closure of
the "only half true" asymmetry conceded in §16.*

## 24. "Your sensitivity program varied the simulator's *parameters* — prices, curves, weights — but never its *functional forms*. The forms are where a model flatters its author."

Correct until session 24, and now measured. Three pre-registered
structural-form reruns of the full 1,800-run headline matrix (protocols
pushed at `7519958` before any run; same pairing, alpha, and no-retuning
rules as the Wave 2 economy reruns):

1. **Measured latency model** (`RESULTS_LM_ADOPTION.md`): the calibration
   had measured a congestion exponent a = 0.86 *and* a p95/mean tail of
   1.59–2.41 **rising with ρ** where the sim asserts a flat 1.4 — and only
   the favorable half of that pair had ever been quoted ("errs against
   us"). Adopting both together (tail fitted from the committed
   calibration CSV, `fit_p95_factor.py`): **LM-H1 PASS 5/5** on composite
   J (margins narrow exactly as a heavier-tailed world implies, −27.5% →
   −22.9% vs hpa, and nothing flips), **LM-H2 PASS 5/5** on cost, and
   **LM-H3 PASS** — violation non-inferiority vs tuned hpa/keda under a
   frozen +0.02 margin, with jcac still violating *less* than hpa
   (paired diff −0.0057, 99% UB +0.0025 vs bound +0.0155).
2. **Mixture percentile** (`RESULTS_MIXTURE_P95.md`): the published
   `ai_p95` is a rescaled *mean* of a bimodal hit/miss mixture — a
   statistic that credits the cache with tail improvements a true
   percentile denies (a hit share below 0.95 cannot move a p95). With the
   true mixture quantile: **MX-H1 PASS 5/5, MX-H2 PASS 5/5, MX-H3
   PASS 2/2**. The tail-honest world raises every system's violations
   (jcac 0.086 → 0.114, hpa 0.073 → 0.121 — the percentile refuses the
   cache's cosmetic tail credit), narrows jcac's J margins (−27.5% →
   −22.2% vs hpa), trims its cache posture (mean 362 → 329 MB, the
   declared abandonment-of-a-devalued-knob adaptation) — and jcac still
   violates *less* than hpa paired (−0.0078, 99% UB +0.0011).
3. **Tier-scaled work units** (`RESULTS_TIER_WU.md`; its 16.64× multiplier
   is the superseded offload-dominated measurement — see §15 — and is
   reported here as the value the run actually used): the published model
   let a 16.64×-heavier model congest the pool for free (capacity was
   tier-blind while latency was tier-coupled). With the measured
   serving-time ratios as capacity multipliers: **TW-H1/H2 PASS 5/5
   each**, jcac's small-heavy posture essentially untouched while the
   reactive tier-up posture pays the capacity price it used to get free
   (gptcache violation 0.049 → 0.133) — the mechanism expectation
   declared in the prereg before the run.

The honest summary: the forms were varied, the rankings survived, and
every direction-of-error is now stated with a measurement attached rather
than a one-sided sentence.

**Do not say:** "the simulator was validated." Three named forms were
stress-tested; others (demand is still deterministic within an interval,
p95 is still the finest latency statistic) remain, and the limitations
list them.

## 25. "Is your solver actually 'exact'? And did your controller even obey its own actuation clamps?"

The first question was measured, and measuring it caught the second — the
most instructive sequence in the project.

- **Coordination gap** (`PREREG_COORD_GAP` → `COORD_GAP.md`): the
  docstring's "solved exactly by enumeration" is per-tenant; cross-tenant
  coordination is two fixed sweeps of coordinate descent with no bound.
  On 120 frozen instances at N = 2, 3 the scored gap to the exact
  joint optimum was **zero on every scored instance** — but 37 instances
  landed *outside the legal one-move lattice entirely*, and the diagnosis
  is the real finding: the second sweep re-anchored the move clamps at
  its own sweep-1 choice, so the published jcac could move **±4 replicas
  and two cache levels per interval** while every baseline was genuinely
  clamped at ±2 / one level. The v3 overload cells' arithmetic assumed
  that clamp was shared. Every committed jcac run contains the behavior.
- **Adjudication** (`PREREG_MOVE_CLAMP` → `RESULTS_MOVE_CLAMP.md`,
  pushed before the run with the outcome rule "the anchored numbers
  become the quotable ones either way"): with moves anchored at the
  interval start — same clamps as every baseline — **MC-H1 PASS 5/5,
  MC-H2 PASS 5/5, MC-H3 PASS 2/2**, and the clamp-fixed controller is
  marginally *better* than the published one (J −27.9% vs hpa against
  −27.5%; mean violation 0.0682 vs 0.0689). The unfair advantage was
  carrying nothing; interval-anchored hysteresis helps. The audit rerun
  under the fixed controller (`COORD_GAP_ANCHORED.md`) closes the loop:
  the illegal-move class vanishes and coordinate descent matches the
  exact joint optimum on **120/120** instances, zero gap — "exact" is now
  a measured property of the deployed solver on the legal lattice at
  N ≤ 3, with the N-scaling caveat stated.

**Do not say:** "the bug didn't matter so it wasn't a bug." It was a
spec violation affecting every committed jcac run, found by our own
audit, adjudicated by a pre-registered rerun whose outcome rule was
frozen before the result was known — and the fixed controller is the one
the thesis now quotes.

## 26. "You hand-designed an MPC. It is 2026 — why not just *learn* the joint policy? Isn't the optimizer unnecessary engineering?"

**Answer: we measured it, pre-registered, and the hand-designed controller
wins — at zero training cost.** Until session 29 this was the sharpest
open question we could only argue about: every baseline in the matrix is
hand-designed, and the one RL arm (`firm`, OSDI '20) learns the **replica
knob only**, so nothing tested whether a learned policy discovers the
*joint* cross-layer coordination. `PREREG_LEARNED_CONTROL.md` (pushed
before the training run, the tuning sweep, and the matrix) closed it.

The learned arm is deliberately strong, not a straw man: tabular
Q-learning over the **identical ≤60-candidate replicas×cache×tier
lattice** the MPC enumerates, rewarded on the **identical objective**,
with a shared tenant-agnostic policy (SLO class in the state) trained
**offline** on seeds disjoint from the matrix, hyperparameters selected on
a disjoint validation slice (val J spread 0.46–1.05 across the grid — a
real, non-degenerate sweep), experience replay for sample efficiency, and
deployment **frozen-greedy** so the committed Q-table alone determines
behaviour.

As measured over 300 matched cells (`RESULTS_LEARNED.md`):

- The MPC **beats** it on composite J — **−0.376, 95% CI [−0.437,
  −0.318], p=2.9e-28, d_z=−0.708** — and pays **no training episodes**,
  where the learned arm needed a full offline budget.
- **The mechanism is the interesting part.** The learner is *not* worse
  everywhere: it attains **lower violation** (0.0559 vs 0.0683,
  p=3.1e-4) — but buys that with **2.61× the spend** ($6.17 vs $2.37).
  That is the *same attainment-for-spend trade the tuned reactive
  scalers make*. A well-trained learner rediscovers "buy headroom"; it
  does not find the cost-efficient joint posture. **Finding the cheap
  configuration — not meeting the SLO — is what the joint optimizer
  contributes.**
- **LR-H2:** offline training is load-bearing. Learning the joint policy
  *within* a deployment episode is not viable (J 9.02 vs 0.78,
  d_z=−0.94): the joint action space is too large to explore online,
  while the MPC needs no episodes at all.

The gate was pre-registered as **non-inferiority** — we expected to have
to defend parity, and froze the weaker claim before the data existed.
The measured result exceeded it. The falsifier was pre-committed too: had
the learned policy won, that would have headlined the limitations and
made learned control the primary future direction.

**Do not say:** "RL can't do joint control." It can, it is credible, and
on violation alone it is slightly *better*. The defensible claim is
narrower and stronger: **an interpretable optimizer with Props 1–2
guarantees matches-and-beats a well-trained model-free learner on the
objective that pays the bills, without training data** — and the learner's
loss is economic, not a failure to control.

## 27. "You bolted a risk knob on to fix your SLO weakness, ran it twice, and still can't say you beat HPA on attainment. Isn't that the same null with extra steps?"

**Answer: no — the two campaigns measured different things, and the second
one is why we now understand the first.** `PREREG_RISK_MPC` (campaign 19)
planned against a quantile of the controller's own forecast residuals. Both
its gates failed, and failed *perfectly monotonically in the reverse
direction* (ρ = +1 / −1) — raising the quantile made the controller cheaper
and more violating. That is not noise, so we diagnosed it rather than
shrugging: inflating demand also inflated *projected tier spend*, candidates
failed the per-tenant budget filter, and the controller took its designed
shed fallback (`tier="none"` — an AI outage). **The knob was fighting the
Budget CRD, not the demand.**

`PREREG_RISK_BUDGET` (campaign 20, pushed at 7595819 before any run) tested
exactly **one changed factor**: size capacity at the risk quantile, but
project cost and check the budget at the *point* forecast — you are billed
for demand that arrives, not demand you provisioned against. As measured over
300 matched cells (`RESULTS_RISK_BUDGET.md`):

- **RB-H1 PASS.** The precise reading the null failed with the sign reversed
  now lands as designed: **−0.00232 violation, 95% CI [−0.00402, −0.00064],
  p=0.0073.** One changed factor flipped the mechanism's sign, which is the
  strongest possible confirmation that the published diagnosis was correct
  rather than a post-hoc story.
- **RB-H2 and RB-H3 FAIL, and are reported as failures.** Violation is
  non-monotone in the quantile — an *interior optimum* at q=0.90 that turns
  back up at q=0.95 — so no frontier claim is made. Strict Pareto domination
  of the reactive stack failed on all three violation conjuncts; only cost
  separates (−33…−39%). **Partial dominance is reported as partial.**
- **We then argued against our own knob.** Under the published objective
  weights the corrected arm is net *worse* on composite J (ΔJ = +0.0079,
  p=1.9e-07). So the point-forecast controller stays the quotable
  configuration, and campaign 20 is evidence **for** our default, not for the
  new feature.

The contribution is not "we finally beat HPA on SLO" — we did not, and the
standing claim is unchanged: **parity on violation at roughly a third the
spend.** The contribution is that a named limitation ("the win is conditional
on operator SLO-tolerance") is now an **explicit dial with a measured price
curve**, plus a mechanism-level bound on where the dial stops working: a
24-cell probe shows the knob buys attainment **only where a capacity lever
still has headroom with a real return** — `ai_cacheable` converts it (51→59%
of the cluster replica ceiling), while `agentic` and `ai_uncacheable`, pinned
at ~99% of that ceiling, convert it into spend instead of service.

**Do not say:** "the risk-aware controller is better" or quote the corrected
arm as the system's configuration. **Do say:** two pre-registered campaigns,
one changed factor between them, a diagnosis that predicted its own fix, and a
mechanism we measured and then declined to adopt because our own objective
says it is not worth its price.

## 28. "You set out to prove a formal SLO guarantee for your controller. You ended up proving your own theorem was empty. What is actually left?"

**Say this plainly, because the honest version is stronger than the one we
planned.** The programme was a terminal invariant set plus a
recursive-feasibility argument over the MPC's clamped actuation lattice —
the standard MPC device. We built the exact construction (finite state space,
deterministic periodic demand, greatest fixed point) and it showed the theorem
would be **true and empty**:

- the plant is **memoryless** — violation depends on the configuration held and
  the demand that arrives, never on a backlog carried forward — so no state is
  a trap and none has to be avoided to stay recoverable;
- the controller may **hold still**, so any configuration clearing the SLO
  across the orbit is trivially control-invariant;
- **replicas are cheap.** At the `flash` peak six clear the SLO and ten cost
  $0.00134 per interval against a $0.01389 budget.

So "is there a terminal set?" collapses into "is the peak servable at all?" —
a static capacity question the ±2 clamp plays no part in. We reported that
instead of shipping the vacuous version.

**What replaced it is an indistinguishability argument about cost, not
attainment.** A controller choosing the configuration for interval *k+1* has
not seen `d_{k+1}`. If its observation is `d_k`, every orbit position sharing
that observation is aliased to it, so to hold zero violation it must clear
*every* demand that can follow — which on an orbit where a trough may precede
either another trough or a burst forces peak provisioning through the troughs.
A predictive controller provisions for `d_{k+1}` alone. The gap is the **price
of reaction**, computed as a *floor* on reactive cost (reach clamp and budget
relaxed) against a *realised* predictive cycle (both enforced, trajectory
closed and rebuilt step-by-step in test). On `flash` that is **+49.0% derived
from the plant constants alone**, against **+2.7%** on the `ramp_gentle`
control cell and **−5.9%** once the observational aliasing is removed — the
mechanism check, since removing the information asymmetry must remove the gap.

**Now the part to volunteer before the examiner finds it.** Checking this
against the campaigns is *suggestive, not validated*, and an earlier version of
our own table overstated it. Comparing derived numbers from one cell against
measured numbers averaged over twelve produced an apparent sign agreement
across all three burst classes; redone per cell at matched violation, one class
flips. The current standing:

| matched cell (`uniform` mix) | derived | measured |
|---|---|---|
| medium / `spike_agentic` vs hpa | +43.4% | +53.1% |
| large / `flash_ai` vs firm | +42.5% | +79.1% |
| small / `flash_crud` vs firm | not computable | +11.5% |

Two agree in sign within ~1.2–1.9×; one is not computable under either
coupling model; the two `ramp_gentle` pairs fall outside what the theorem
describes. **The multi-tenant coupling is load-bearing, not a footnote:**
ignoring cluster caps makes `flash_crud` come out −19.5%, the opposite sign to
measurement, while charging each tenant an equal share makes the cell
infeasible — and neither is right, because the per-tenant phases are drawn
independently, so bursts do not coincide and a tenant can borrow capacity while
its neighbours sit in a trough. Separately, our derived "reactive" class is an
*idealised* policy keyed on the exact demand, whereas real HPA and KEDA merely
lag; the measured deltas therefore also contain plain reactive lag, which this
theorem does not model.

**Do not say:** "we have a formal guarantee", "the bound is validated against
the campaigns", or quote 49% and −44…−50% as the same quantity. **Do say:** the
guarantee we set out to prove does not exist on this plant and we established
that rather than assuming it; what exists instead is a computable,
brute-force-verified cost separation whose mechanism we tested by removing it;
and its correspondence to the measured record is one-operating-point structural
corroboration in a single-tenant model, with the multi-tenant extension named
as the open item.

## 29. "You withdrew your own −70% headline (see #22). What is left of the cost claim?"

The withdrawal is the answer, and it was pre-committed. After the campaigns
closed, an audit of the baseline implementations (the V-series, sessions
37–38) found three asymmetries between jcac and every comparator it had been
scored against: (1) every reactive comparator carried a 1.4581× LRU inference
charge no jcac arm paid and was pinned at a 128 MB cache it could not move;
(2) the same on the two real traces; (3) only jcac was ever subject to the
per-tenant budget filter — `controller.py` rejects over-budget candidates
before its objective is evaluated, and no baseline `plan()` has a cost term at
all. Each was answered by a new pre-registration whose outcome for the
published claim was committed *before* the re-scoring ran; no original record
was edited.

**What the re-scorings found** (`RESULTS_EVICTION_PARITY.md`,
`RESULTS_TRACE_PARITY.md`, `RESULTS_BUDGET_PARITY.md`): against `hpa_fair` /
`keda_fair` (no charge, competently pre-sized at 512 MB) the 1,800-run matrix
is cost-neutral vs `hpa_fair` (+0.8%, EP-H1a FAIL) with −14.1% vs `keda_fair`
surviving (p=3.8e-16); Azure shrinks from −42.5% to a real, pervasive −7.1%
(TP-H1a/b PASS, 69.4% of windows); BurstGPT shrinks from −70.4% to −52.2% on
the mean but **fails its own pre-registered Wilcoxon** (TP-H1a p=0.0133 vs
Holm 0.01250) — jcac is the *dearer* system in 57.3% of 6-hour windows. Against
comparators carrying jcac's own budget rule verbatim, **BP-H1a/b FAIL on both
traces** (Azure −3.3%/−1.3%, p=0.379/0.91; BurstGPT −50.2%/−49.8%,
p=0.112/0.191 — a minority-of-windows mean that does not survive the rank
gate). Per the response committed before the run, the like-for-like cost claim
is **withdrawn, not restated**.

**What survives, and why it is sharper than a percentage.** Amendment 1,
disclosed before scoring, explains why no better comparator can rescue the
percentage: at the per-tenant cap, tier spend dominates infra spend by three
orders of magnitude, and capping a replica-only arm moves its infra spend
−45.7%/−57.4% and its tier spend by **exactly 0.00%** on both traces. **No
budget-respecting comparator exists in the replica-only class.** The cost
result is therefore a *feasibility* statement: the per-tenant budget is a
promise only a controller holding the tier knob can keep, and no replica-only
reactive controller can meet it under AI load by any decision available to it.
The composite-J wins, the fairness wins, the forecasting boundary and the
security frontier are untouched by the adjudication; the SLO reading gains a
bounded caveat (#5: severity halves vs `hpa_fair` on synthetic and Azure,
reverses 58× on BurstGPT, TP-H3a/b FAIL; lifting the budget cap removes 79% of
that gap, BP-H3, as a typical-window claim whose tail still breaches the
margin in 22% of windows).

**Do not say:** "−70%", "−42%", "−52%", or "beats every tuned baseline on
cost". **Do say:** "withdrawn under three pre-registered re-scorings; what
stands is that only a tier-holding controller can keep a per-tenant budget."
The thesis text (`thesis/report/chapters/06-evaluation.tex` §6.7) and
`docs/THESIS_DRIFT.md` (generated) carry the adjudicated wording; the drift
report fails CI if a withdrawn number reappears bare.
