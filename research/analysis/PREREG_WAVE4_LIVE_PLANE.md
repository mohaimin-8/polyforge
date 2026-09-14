# Pre-registration: three-knob live data plane (Wave 4, closes DEFENSE_QA #21 — the joint controller's central novelty, never yet exercised live in full)

Registered 2026-07-17 (session 23). Committed and pushed **before** the run;
the push event is the timestamp anchor; to be mirrored to OSF prospectively.
**Execution is deferred and user-gated** — it needs (a) a GPU-capable host
(paid cloud GPU or a GPU Codespace; this machine has no local Docker and no
GPU) and (b) harness work to make the cache and tier knobs real live levers
(named in §Substrate below). This file freezes the protocol so that when the
host is available the live run is confirmatory, not improvised. It does **not**
report any result.

## Why this experiment exists

The Phase 7 live ordinal check (session 19, `PHASE7_ORDINAL.md`) established
that the full jcac control loop runs live end-to-end under a mechanical
actuation gate, with capacity parity held and no degradation — **but on the
replica axis only.** The kind data plane burned fixed CPU per request kind, so
a cache hit saved no real wall time and a model-tier choice changed no real
cost: the cache and tier knobs were *inert by construction*, and the frozen
figure caption disclosed exactly that. The consequence, stated honestly in the
thesis limitations and in DEFENSE_QA #21: the joint 3-knob controller — the
central novelty that separates PolyForge from every single-knob baseline — has
**no live evidence with all three knobs active.** The `-joint` ablation that
establishes jointness matters (+2884% cost decoupled) is a simulator mechanism
result; the cost/J headline is simulator decision quality; each knob's realism
is carried *separately* on a real substrate (cache by LMSYS + the wire attack,
tier by the GPU bench) but never *jointly* on one live plane.

This pre-registration is the named closure: a live data plane on which all
three knobs demonstrably actuate, so the joint controller's benefit can be
measured — or falsified — against single-knob baselines on real hardware.

## Substrate (frozen)

The substrate must make each knob a *real* lever. This is the harness work the
run depends on, listed so it is built before the run, not improvised during it:

1. **Replica knob (already real, reuse Phase 7).** The session-19 kind
   harness: operator installed, actuation gate, `phase7_jcac_smoke.yaml` cell
   geometry, real pods scaled by the operator.
2. **Tier knob (new, GPU-backed).** The gateway routes to **at least two real
   model tiers** served on the GPU host — the tier bench pair is the frozen
   default: a small tier (Qwen2.5-0.5B) and a mid tier (a 3B-class model),
   with a large tier (7B) included only if the host has the VRAM headroom the
   tier bench flagged (`TIER_BENCH.md`; the 7B CPU-offload caveat must not
   recur — if the host cannot hold 7B in GPU memory, the large tier is dropped
   and the run is a two-tier run, declared as such). Tier selection changes the
   *real* per-request latency and the *real* $-cost (metered from the tier the
   request actually hit), so the tier knob bites.
3. **Cache knob (new, real hit/miss latency gap).** The gateway's production
   `SemanticCache` is enabled with a real hit/miss cost differential: a hit
   short-circuits the backend, a miss pays the real tier latency above. This is
   the same mechanism the wire attack already exercised on the real gateway
   process (`research/security/mock_llm.py` gave misses a real delay); here the
   miss cost is the *real model tier* latency, not a mock. Cache size in MB is
   the operator's knob and changes the realized hit rate against the cell's
   prompt-reuse structure.

Preflight validity gate (must pass before any comparison is scored — this is
the guard against a second inert-knob run): a one-cell probe confirming that
(i) toggling the cache-MB knob moves the measured hit rate by a material
margin, and (ii) forcing a tier change moves measured per-request latency and
$-cost. If either knob is inert on the provisioned host, **WL-H1 is void and
the substrate is reported inadequate** — no joint-vs-single claim is made from
an inert-knob run. Honesty over a result.

## Arms (frozen)

All arms tuned on the paper's own composite objective J (cost + 2·violation +
0.5·(1−Jain)), grids at the vendor-sane envelope, per the established protocol
(`eval/baselines/TUNING.md`).

- **jcac** — the joint controller, all three knobs live.
- **replica-only** — the strongest single-knob reactive baseline (tuned
  HPA/KEDA replica control), cache and tier held at a fixed sensible default.
- **cache-only** and **tier-only ablations** of jcac (the joint controller with
  two of its three knobs frozen), so the comparison isolates *jointness*, not
  just "jcac vs HPA." These ablations are the live analogue of the sim
  `-joint` ablation and are the sharpest test of the central claim.

## Cells (frozen)

Three cells, each chosen so a different knob is load-bearing, plus one where
all three interact:

1. `ai_cacheable` (cache-load-bearing) — high prompt reuse, so the cache knob
   dominates; the cell whose sim separation was inert live in Phase 7.
2. `tier_mixed` (tier-load-bearing) — demand mixing cheap and expensive request
   classes so tier routing changes cost materially.
3. `crud_bursty` (replica-load-bearing) — the Phase 7 burst cell, so results
   attach to a measured live baseline.
4. `joint_stress` (all three) — a burst of cacheable, tier-mixed demand where
   the three knobs must be co-scheduled; the cell the joint claim most needs.

## Hypotheses (frozen)

- **WL-H1 (primary, the central claim):** on the live all-knobs data plane,
  jcac beats the best single-knob arm (and beats each two-knob ablation) on
  **cost at iso-fairness** — lower mean $-cost at a Jain index no worse by more
  than 0.01 — in the `joint_stress` cell, with a paired bootstrap 95% CI on the
  cost delta excluding 0. **Falsifier, pre-committed:** if jcac does *not* beat
  the best single-knob arm on cost-at-iso-fairness on this substrate, the
  central claim is wounded and that result headlines the thesis limitations —
  it is not re-run, re-tuned, or widened. A live parity here would be the
  live-substrate analogue of the Phase 7 ordinal DISAGREE, published as
  measured.
- **WL-H2 (substrate validity, gating):** the preflight knob-liveness gate
  passes — cache-MB moves hit rate and tier choice moves latency/$-cost by
  material margins. WL-H2 must hold for WL-H1 to be interpretable; a WL-H2
  failure voids WL-H1 (see the preflight gate above).
- **WL-H3 (descriptive, addresses the sim-credibility question DEFENSE_QA
  #20):** report whether the simulator's *ordinal* ranking of the arms
  reproduces live *now that the knobs actuate*. If it reproduces where Phase 7
  (inert knobs) did not, that is direct evidence the Phase 7 disagreement was
  the disclosed inert-knob mechanism and not a sim-validity failure. If it does
  not reproduce, that divergence is the finding and goes into the thesis
  verbatim. No pass/fail; ordinal only; absolutes never crossed between
  substrates (ground rule 3).

## Metrics and export

The same `control-plane eval-export` document as Phase 7, now meaningful on all
three axes: real replica-seconds, realized cache hit rate and cache-MB, and the
tier histogram with $-cost metered from the tier each request actually hit.
Live p95 **and** p99 are read (the p99 export landed and unit-tested in Wave 3,
`PREREG_LIVE_CHAOS_P99.md`); p99 stays a live-only number, never back-fitted
into a sim table (ground rule 5).

## Outcome handling and stopping rule

Results to `RESULTS_WAVE4_LIVE_PLANE.md` as measured, WL-H1 failure included
and headlined if it occurs. One execution per arm per cell on the provisioned
host; arms, cells, tuning grids, and the iso-fairness margin (0.01) are fixed
by this push and may not change after it. Any substrate change forced by the
provisioned host (e.g. dropping the 7B tier for VRAM, or the exact mid-tier
model) is declared as a pushed pre-run amendment in this file *before* any
comparison number is collected — the same amendment discipline the wire attack
used (`PREREG_WIRE_ATTACK.md` §Amendment). The raw duckdb stays gitignored and
travels in the Zenodo bundle; run-level CSVs are committed.

## Gating status (honest, 2026-07-17)

Not runnable on the current machine. Blocked on, in order:

1. **A GPU-capable host** with VRAM for at least a small+mid tier (paid cloud
   GPU or a GPU Codespace) — a payment/account step, user-owned.
2. **Harness work** to wire the tier-routing data plane and the real cache
   hit/miss latency gap into the live run (the §Substrate items 2–3) — buildable
   at the desk before the host is provisioned; a natural next agent task once
   the user opens the gate.

Until both are met, this protocol is the frozen anchor and the run is deferred,
exactly as `PREREG_WIRE_ATTACK.md` and `PREREG_LIVE_CHAOS_P99.md` were before
their live sittings. The push of this file is the pre-registration; no result
exists yet, and none is implied.

**Status update, 2026-07-19 (harness work landed; still no result):** gate 2
is closed. The §Substrate 2-3 harness work is committed and desk-verified
end-to-end: per-tenant tier routing + cache byte budgets on the production
gateway (`internal/ai/gateway/knobs.go`), operator knob push covered by the
Applied actuation gate (`internal/operator/controllers/gateway_knobs.go`),
cache-hit-costs-nothing metering + tier histogram in `eval-export`, the
gateway deploy leg (`Dockerfile.gateway`, chart `gateway.*` values, harness
`POLYFORGE_EVAL_LIVE_AI` mode with per-cell prompt-reuse pools), the frozen
cell classes `tier_mixed` / `joint_stress` in `eval/harness/workloads.py`,
and the executable WL-H2 gate (`eval/scripts/knob_preflight.py`). The gate
was exercised at the desk against the real gateway binary with two
mock-latency tier backends: cache knob hit-rate 1.00 @64MB vs 0.00 @0MB,
tier knob 61.6 ms vs 245.0 ms with routing verified — **WL-H2 PASS on the
desk substrate** (mock backends stand in for the GPU host's model servers;
the live run must re-run the gate on the provisioned host). Gate 1 — the
GPU-capable host — remains the only blocker, and no comparison number
exists.

## Amendment (declared pre-run, session 33 — substrate: two tiers, split host)

The frozen §Substrate assumes the model tiers are "served on the GPU host",
implicitly the same host as the cluster, with VRAM possibly sufficient for a
7B `large` tier. The substrate actually available is a **free** one, and it
differs in two ways. Per §Outcome handling ("any substrate change forced by
the provisioned host ... is declared as a pushed pre-run amendment in this
file *before* any comparison number is collected"), this amendment is
committed and pushed before any comparison number exists. Arms, cells,
hypotheses, the iso-fairness margin (0.01), and the stopping rule are
untouched.

1. **Two tiers, not three — `small` and `mid` only.** `small` =
   Qwen2.5-0.5B-Instruct, `mid` = Qwen2.5-3B-Instruct: the tier-bench pair
   this file names as its frozen default. The `large` (7B) tier is dropped
   under the rule §Substrate already states — `TIER_BENCH.md` measured 7B fp16
   (~15.4 GB) spilling to CPU on a 16 GB card in two independent sessions, and
   a CPU-offloaded tier measures the offload, not the tier. This run is
   therefore **a two-tier run, declared as such**. Note this constraint is not
   specific to the free host: any single-16 GB GPU forces it.

2. **Split host: the tiers are served on a separate machine, reached over a
   public tunnel.** The cluster half (kind, operator, planner, gateway) runs
   on one free host; the GPU half (`research/calibration/kaggle_tier_server.py`,
   both tiers behind one OpenAI-compatible endpoint) runs on a free GPU kernel
   and is reached at a `trycloudflare.com` URL supplied through
   `POLYFORGE_EVAL_TIER_BACKENDS`. No code path changes — the harness already
   takes tier backends as URLs — but every AI request now crosses the public
   internet, which the frozen substrate did not contemplate. What that does,
   stated before the numbers exist so it cannot be chosen afterwards:

   - **The tier separation is preserved.** Round-trip is added to both tiers
     alike, so the small/mid *gap* — the quantity WL-H2 tests and the tier
     knob actuates on — is unchanged; only WL-H2's relative threshold grows,
     at 0.25x the added round-trip.
   - **$-cost is unaffected.** Cost is metered from the tier a request
     actually hit, not from its latency, so WL-H1's cost term is untouched.
   - **Absolute latency is inflated**, and with it the SLO term and the Jain
     index computed over SLO satisfaction. Live absolutes from this substrate
     are **not** comparable to `TIER_BENCH.md`'s in-host tier latencies and
     are never quoted as tier latencies (ground rule 3 applies with full
     force here).
   - **The SLO term can saturate.** The premium AI target is 2500 ms
     (`model.py` `SLO_BASE_MS["ai"]` x `SLO_CLASS_FACTOR["premium"]`) and the
     `mid` tier benched at 1883 ms, so roughly 600 ms of added round-trip puts
     that tier over target, after which premium tenants violate irrespective
     of the controller's choices and the SLO dimension the arms are separated
     on goes flat. **This binds well before WL-H2 does** (which tolerates
     ~1.3 s), and it is the failure mode this amendment most needs on record.

3. **Pre-run substrate gate, in addition to WL-H2.** Before the matrix is
   run, `eval/scripts/tunnel_preflight.py` must exit 0 **and** report the
   slowest tier inside the premium AI SLO. Its JSON report
   (`eval/results/tunnel_preflight.json`: per-tier means, measured gap,
   threshold, round-trip headroom, SLO headroom) is committed with the run
   and quoted in `RESULTS_WAVE4_LIVE_PLANE.md`. If the slowest tier is over
   target, the substrate is declared inadequate on the same terms as a WL-H2
   failure and **no comparison number is collected** — WL-H1 is not scored
   from a saturated substrate.

4. **Scope.** Clauses 2 and 3 apply only if the split-host route is taken. If
   a single GPU host is provisioned instead (cloud credit, rented, or a GPU
   Codespace), only clause 1 applies and clauses 2-3 are void — the run is
   then exactly the frozen protocol at two tiers.

Neither clause weakens a hypothesis or its falsifier. WL-H1 still fails
loudly if the joint arm does not beat the best single-knob arm on cost at
iso-fairness, and a WL-H2 or clause-3 failure still voids the comparison
rather than producing a softened claim.

---

## Amendment (declared pre-run, session 44 — single host, three tiers, corrected latency metric)

Declared and pushed **before any comparison number exists**, per §Outcome
handling. Arms, cells, hypotheses, the iso-fairness margin (0.01) and the
stopping rule are untouched. This amendment **narrows** the session-33
amendment rather than adding to it.

### 1. Single GPU host — session-33 clauses 2 and 3 are void by clause 4

The substrate is one rented GPU host running **both** halves: the kind
cluster (operator, planner, gateway) and the model tiers. Session-33 clause 4
already fixes the consequence: *"If a single GPU host is provisioned instead
(cloud credit, rented, or a GPU Codespace), only clause 1 applies and clauses
2-3 are void."* So the split host, the `trycloudflare` tunnel, the inflated
absolute latencies, and the tunnel-specific pre-run gate all fall away. **No
new latitude is being taken here; a declared conditional is being satisfied.**

This matters for one reason beyond tidiness. `WAVE4_FREE_ROUTE.md`'s
clause-3 budget of "619 ms of tunnel round-trip" was computed from a P100
`mid` of 1881 ms. Session 44 re-measured `mid` on the T4 x2 the free route
now requires: **2310 ms pinned to one card (190 ms of headroom) and 3081 ms
sharded (over target outright)**, while the cell needs ~64 AI rps at base and
~110 at peak against a measured 26.5-46.5 rps, and the batching that would
close that gap pushes `mid` to 3285 ms at batch 32. **No batch size satisfies
both gates.** The free split-host route is therefore not merely inconvenient
but *measurably infeasible* for this cell, which is why a host is being
rented rather than the cell being shrunk.

### 2. Three tiers — supersedes session-33 clause 1

Session-33 clause 1 dropped the 7B `large` tier and justified it thus:
*"`TIER_BENCH.md` measured 7B fp16 (~15.4 GB) spilling to CPU on a 16 GB card
in two independent sessions... Note this constraint is not specific to the
free host: any single-16 GB GPU forces it."*

**That premise is now false, and it was falsified by measurement, not by
argument.** Session 44 ran the identical tier-bench protocol on 2 x 16 GB and
recorded `modules offloaded to cpu/disk: 0` for all three tiers, with `large`
going 20665.1 ms -> 3171.5 ms (`research/calibration/tier_bench_t4.csv`,
`TIER_BENCH.md`). The rented host has more VRAM again, so the constraint does
not bind at all.

The run is therefore a **three-tier run** — `small` = Qwen2.5-0.5B-Instruct,
`mid` = Qwen2.5-3B-Instruct, `large` = Qwen2.5-7B-Instruct — which is what
§Substrate specifies as its own default: *"a large tier (7B) included only if
the host has the VRAM headroom the tier bench flagged"*. **The condition
§Substrate itself names is now met.** This strengthens WL-H1: the tier knob
has three positions to co-schedule instead of two.

**Verification obligation, binding.** The pre-run WL-H2 gate must confirm on
the provisioned host that `large` is resident in GPU memory with no CPU
offload, by the same placement report used in session 44. If `large` offloads
on the day, it is dropped and the run reverts to two tiers **declared as
such** — a CPU-offloaded tier measures the offload, not the tier, and that
rule is unchanged from §Substrate.

### 3. Serving engine: vLLM — an implementation choice, not a deviation

This file **does not pin a serving engine**; §Substrate requires only that
each tier is a real model server so the tier knob bites. vLLM is used, behind
its OpenAI-compatible endpoint, which the gateway already consumes
(`kind: "openai"` in `POLYFORGE_TIER_BACKENDS`). Recorded because it is
material to the numbers:

* `V2_README`'s Phase 6 **originally specified vLLM serving**; transformers
  was the documented deviation, taken because the free pool's GPU
  architecture was not vLLM-guaranteed (`TIER_BENCH.md`). On a provisioned
  host that reason expires, so this **returns to the originally specified
  substrate** rather than introducing a new deviation.
* It is also load-bearing rather than cosmetic. Session 44's two-GPU
  measurement found replica scaling of only 1.15x-1.93x, and located the
  ceiling in the **host-side Python decode loop** rather than the cards
  (`mid` TPOT 48.00 ms/token of which only ~19 ms is weight bandwidth; the
  second-card penalty is a near-constant ~1 s that collapses to 0.17 s
  exactly where GPU work dominates). A faster card under transformers would
  therefore not have cleared the throughput requirement; a native decode loop
  with continuous batching is the part that does.

### 4. Live latency metric: the corrected one is primary

Live records to date quote `crud_p95` from `replay.go`, which stops its clock
**before** the telemetry write (line 76 vs the write at line 94) — CPU
service time, not service latency. The middleware histogram
`polyforge_http_request_duration_seconds` times the whole handler and is the
correct measurand; session 43 found nothing had ever scraped `/metrics`, and
`scripts/soak_observer.sh` now does.

**For this run the histogram is primary.** The replay number is carried
alongside every reported figure, and the two are **never compared across that
boundary** — the same rule §Ground rules already applies to absolutes across
substrates.

This is declared here, before any number exists, because it is **not
cosmetic**: L6 measured the gap at **+21% mean and +239% p95**, and WL-H1 is
cost *at iso-fairness*, where the Jain index is computed over SLO
satisfaction. A p95 that more than triples moves the SLO term and therefore
the fairness term that WL-H1's margin is defined against. Choosing the metric
after seeing either arm's numbers would be exactly the degree of freedom
pre-registration exists to remove.

**Consequence accepted in advance:** live violation rates under the correct
metric will look substantially worse than in every previously published live
record. That difference is a measurement correction, not a regression, and it
is not a reason to prefer the flattering number. Prior records stand as
scored on the only metric that existed when they ran — attempt 10's histogram
went with its cluster and cannot be recovered.

Neither clause weakens a hypothesis or its falsifier. WL-H1 still fails
loudly if the joint arm does not beat the best single-knob arm on cost at
iso-fairness; WL-H2 still voids WL-H1; and a `large` tier that offloads on
the day still costs the third tier rather than being quoted anyway.

---

## Amendment (declared pre-run, session 47 — host capacity, a concurrency preflight, and what a voided primary cell means)

Declared and pushed **before any comparison number exists**, per §Outcome
handling. Arms, cells, hypotheses, the iso-fairness margin (0.01) and the
stopping rule are untouched. This amendment adds **no latitude**; it declares
in advance how two already-measured facts about the substrate are handled,
so that neither is decided after a number has been seen.

### 1. What was measured, and why this is declared now

Sessions 45–46 rehearsed the full 16-cell matrix on this laptop (8 cores)
against a mock tier server, free, 2.8 h: **12 valid, 4 failed — the four
failures are every `joint_stress` run on all four arms**, and `joint_stress`
is the cell WL-H1 is defined in. The failure is the harness's own validity
guard (`check_sampler_coverage`: the replica sampler must cover ≥ 90% of the
load window, else infra cost is under-counted and the run is invalid). Four
hypotheses for the cause were raised and each refuted by its own test:

| hypothesis | test | result |
|---|---|---|
| tier-backend throughput | mock 12x faster (150 ms tier latency) | **byte-identical failure** |
| `kubectl top` polling cost | timed | ~130 ms, unchanged |
| sampler speed | failed-attempt count | 0 |
| gateway replicas | `replicaCount` 1 → 4 | **worse**: 12.22% → 18.65% refused |

The mechanism, from k6, reproduced twice: `vus_max 9600`, `http_req_failed`
12.2–12.4%, `http_req_duration p(95)` 186–190 ms. The tier backend is
comfortable; the **cluster refuses ~12% of requests under 9,600 concurrent
VUs**, which trips the harness's `rate<0.01` threshold, k6 aborts at ~70 s of
the 300 s window with `abortOnFail`, the sampler dies with it, and the
coverage guard fires. The binding variable is **host concurrency capacity**,
the same shape that voided `PREREG_MULTINODE.md` sitting 2. Evidence:
`eval/results/wave4_jointstress_probe_evidence/` (labelled NOT EVIDENCE — it
is a mock probe, kept because it is the raw proof of the mechanism).

**Consequence for the substrate.** The session-44 runbook's host floor of
"≥ 8 vCPU" was wrong: 8 is measured insufficient. The single-host run
requires **≥ 16 vCPU, preferably 32**, alongside the ≥ 40 GB VRAM clause 2
already needs. Whether 16–32 cores serve 9,600 VUs is **untested**; clause 2
below exists so that it is tested before the run rather than by the run.

### 2. A concurrency preflight, gating, on the provisioned host — before any model is loaded

Analogous to the WL-H2 knob-liveness gate and declared for the same reason: a
substrate that cannot serve the cell must fail *before* the scored matrix,
not three hours into it.

* **What runs:** `eval/experiments/wave4_jointstress_probe.yaml` — the
  `joint_stress` cell against the **mock** tier server
  (`kaggle_tier_server.py --mock --no-tunnel`), on the provisioned host, with
  its evidence and database redirected to the probe directories. The mock
  reproduced the laptop failure byte-identically, so it isolates the host
  capacity hypothesis from the tiers, and **it produces no comparison
  number** — the mock's latencies are fixed sleeps that never enter a record.
* **Pass:** the run is `valid` by the harness's existing guards — k6
  `http_req_failed` under its frozen `rate<0.01` threshold for the full
  window and sampler coverage ≥ 90%. Nothing about the guards changes.
* **Fail:** **the scored matrix is not started.** The k6 summary is committed
  under a NOT EVIDENCE label beside the session-45 one, and the choice
  between a larger host and a smaller cell under a **new** pre-registration
  is made with this run unspent. This file does not pre-authorise a smaller
  cell; the cell is frozen and a different cell is a different prereg.

Order on the clock, binding: bootstrap → **this preflight** → tier servers
up → WL-H2 gate (`--tiers small,mid,large`, clause 2 of the session-44
amendment) → the 16-cell matrix, once.

### 3. What a voided `joint_stress` means for WL-H1 — declared before it can happen

WL-H1 is defined *in the `joint_stress` cell* (§Hypotheses). Therefore:

* **If `joint_stress` is voided on every arm by the harness's validity
  guard**, WL-H1 is **NOT EVALUATED** — neither PASS nor FAIL. It is reported
  as unevaluable on the provisioned host, with the k6 summary and the
  coverage figure for each arm, and that sentence headlines
  `RESULTS_WAVE4_LIVE_PLANE.md`'s WL-H1 entry. **It is not re-scored on the
  other three cells** — the hypothesis names one cell, and reading it off a
  different cell after seeing the numbers would be the degree of freedom this
  document exists to remove. The three cells that ran are reported as
  measured, descriptively, and WL-H3's ordinal comparison runs on whatever
  cells produced valid runs on all four arms.
* **If `joint_stress` is voided on some arms and not others**, WL-H1 is
  likewise NOT EVALUATED: a cost comparison between arms that ran and arms
  that did not is not a comparison. Which arms voided is reported.
* **If `joint_stress` is valid on all four arms**, WL-H1 is scored exactly as
  frozen. The preflight in clause 2 having passed is not evidence for WL-H1
  and is not cited as such.
* **WL-H2 is unaffected** by any of this; it is gated by its own preflight.

A voided primary cell with its mechanism measured is a limitation the paper
states. A primary cell quietly replaced by a friendlier one is not, and it
does not happen under this file.

Neither clause weakens a hypothesis or its falsifier. WL-H1 still fails
loudly if the joint arm does not beat the best single-knob arm on cost at
iso-fairness in `joint_stress`; a substrate that cannot run `joint_stress`
leaves WL-H1 unevaluated rather than moving it; and no cell, arm, margin or
metric changes.

## Pre-run note (session 48, 2026-09-15 — the host-floor attribution was wrong; the substrate, not the host, refused the cell)

Declared before any scored run, superseding the **factual claim** in the
session-47 amendment's clause 1 (host floor ≥ 16 vCPU) and leaving every
hypothesis, arm, cell, margin, metric and gate exactly as registered.

**What was measured.** The concurrency preflight of clause 2 was executed
five times on a fresh 8-vCPU EC2 host (`c6i.2xlarge`, AWS DLAMI Ubuntu 22.04)
with k6 emitting every request's HTTP status and, from the third run, the
gateway pod's log followed. Evidence: `eval/results/wave4_jointstress_probe_evidence/2026-09-15_ec2_ladder/`
(NOT EVIDENCE for any hypothesis; the raw proof of a mechanism), commit
`4e499ef`.

* The 12.2–12.4% `http_req_failed` that sessions 45–47 read as the cluster
  refusing 9,600 concurrent VUs was **734 of 740 failures answering HTTP 429**
  from the AI gateway's own per-tenant token bucket, hard-coded at 600
  requests/minute with burst 60 and not configurable. `joint_stress` bursts a
  tenant to ~20 AI rps = 1,200 RPM. Active VUs peaked at 96; `vus_max` 9,600
  is the pre-allocated pool. The CRUD path had no failures in any run.
* With that limiter lifted — exactly as the eval install already lifts the
  control plane's, and for the reason that install documents — the residue
  was three substrate defects: unpinned tenants routed to an external default
  provider absent from the eval install (HTTP 502); the AI load path entering
  through `kubectl port-forward`, which dropped streams (EOF, no gateway-side
  error); and a Python-3.10-only crash in the harness after the load window.
* With those fixed the frozen cell is **valid on 8 vCPU**: 37,269 requests,
  1 failure, 0 dropped iterations, WL-H2 PASS, full window.

**Consequence.** The substrate for this campaign is one host with ≥ 40 GB
VRAM on sm_80+ and **≥ 8 vCPU** (measured sufficient; the amendment's clause
2 preflight still runs on the provisioned host and still gates). The four
substrate changes are configuration and harness, disclosed here: gateway
admission limiter lifted for the eval install; unpinned tenants served by
the configured default tier; AI load path on a NodePort; sampler thread
fixed. None touches the controller, the arms, the cells or the metrics.
Clause 3 (a voided `joint_stress` leaves WL-H1 NOT EVALUATED) stands
unchanged; it is simply no longer expected to fire.
