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
