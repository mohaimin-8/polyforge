# PolyForge — consolidated results (master record)

**This is the single reading entry point for every measured result in the project.**
The per-campaign files it consolidates (`RESULTS.md`, `RESULTS_V2.md`, `RESULTS_V3.md`,
`RESULTS_TRACE.md`, `RESULTS_TRACE2.md`, `VTC_FAIRNESS.md`, `FAIRNESS_V2.md`,
`FORECAST_TRACE.md`, `FORECAST_TRACE_REAL.md`, `ADVANCED.md`, `SEMANTIC_CACHE.md`,
`CACHE_PRECISION.md`, `EFFECT_SIZES.md`, `PLANNER_SCALING.md`, `FORECAST_AZURE.md`,
`PHASE7_ORDINAL.md`, and the Wave 1–2 robustness records `BREAKEVEN_TIER.md`,
`OBJECTIVE_FORM.md`, `RESULTS_TIER_RATIO.md`, `RESULTS_HK_ADOPTION.md`,
`RESULTS_CHAOS_SIM.md`, `PSEUDO_TENANT.md`, the Wave 4 scaling records
`PLANNER_CELLS.md`, `PLANNER_CELLS_DEALIAS.md`, and the Wave 5 structural-form and
solver-audit records `RESULTS_LM_ADOPTION.md`, `RESULTS_MIXTURE_P95.md`,
`RESULTS_TIER_WU.md`, `COORD_GAP.md`, `COORD_GAP_ANCHORED.md`, `RESULTS_MOVE_CLAMP.md`) are
**machine-generated measurement records**: each is written by its analysis script from
the raw run databases and is immutable once its campaign closes (pre-registration ground
rules). They stay exactly as they are — this file summarizes and reconciles them but
never replaces them. When a number here and a number there disagree, the campaign file
wins and this file has a bug.

Protocol files (`PREREG_*.md`), theory (`THEORY_V2.md`), and positioning
(`docs/RELATED_WORK.md`) are out of scope here by design: they are frozen contracts and
context, not results.

---

## Scoreboard — the five contested segments plus security

| segment | verdict | the citable claim | evidence |
|---|---|---|---|
| **Cost** | **WON — confirmatory, synthetic + real** | Beats every tuned baseline on cost in the 1,800-run matrix (d_z 0.66–1.12); on real BurstGPT demand: **−70% cost vs tuned HPA/KEDA/FIRM, p ≤ 4.3e-06, n=96** | `RESULTS.md`, `RESULTS_TRACE2.md` |
| **SLO** | **Closed honestly — iso-attainment framing** | Violation parity with tuned HPA/KEDA at −43…−48% cost (v2, H1 FAIL); beats FIRM (p=2e-19). Overload regime: reactive scalers buy attainment at 3.2–3.4× spend (v3, H1′ FAIL); forecast mechanism confirmed (H2′ PASS, p=6e-4); spike-class exploratory win (p=5e-5, d_z=0.57, −46% cost). Stopping rule: no further confirmatory attempts | `RESULTS_V2.md`, `RESULTS_V3.md` |
| **Fairness** | **WON — two dedicated baselines beaten** | Beats FIRM on Jain (d_z=1.0, p=4.5e-47); beats the tuned VTC-replica **on Jain itself** (0.9705 vs 0.9599, p=1.5e-6) at −35% cost and −36% worst-tenant p95 (HV1+HV2 PASS). Own γ-term honestly nulled under injected interference | `VTC_FAIRNESS.md`, `FAIRNESS_V2.md` |
| **Forecasting** | **WON — on real data** | On the real 10.63M-request BurstGPT trace, damped Holt (jcac_v2's forecaster) cuts one-step RMSE −26.9% vs the trend default (segment 1); the synthetic stand-in's −44.8% seasonal prediction **does not transfer** and is published as such; seasonal's mechanism is confirmed only where real periodicity exists (v3 H2′). **Second real trace (Azure LLM 2024, 44.1M requests): the mechanism boundary reproduces** — seasonal wins −15.6% RMSE exactly on the stream with a genuine daily cycle (code, autocorr 0.57 @ lag 24), loses +48.5% on the weakly-periodic conv stream (persistence wins there), and pooling the streams destroys forecastability (seasonal +104%) — per-stream forecasting, which is what PolyForge does per tenant, is the supported design. Across three real streams no single forecaster dominates; the winner tracks measured periodicity | `FORECAST_TRACE_REAL.md`, `FORECAST_TRACE.md`, `RESULTS_V3.md`, `FORECAST_AZURE.md` |
| **Cache** | **CLOSED — real-data headline measured** | On real LMSYS-Chat-1M (200k-turn reservoir sample, MiniLM, exact NN): adaptive semantic hit rate **29.6% @ cosine 0.85, 48.8% @ 0.70** (InstCache's 51.34% anchor is their different protocol on the same dataset — placed beside, never head-to-head); adaptive beats a non-strawman fixed cache at 10% of inserted by **+93%** (29.6 vs 15.3); **$0.296 saved/1k queries** at mid-tier pricing; empirical h(K): hmax 0.285, K_half ≈ 6.6k entries (~19 MB) — the sim's assumed curve was optimistic, documented, constants unchanged. **Hit quality measured** (pre-registered proxy, `CACHE_PRECISION.md`): at τ=0.85, response-agreement precision 0.313 overall / 0.443 same-model / 0.353 first-turn (ρ=0.70), 165.5 incorrect hits per 1k queries; the τ=0.95 near-duplicate row (0.338) is the proxy's stochasticity ceiling, so cite the *relative* readings — precision rises with τ (0.249→0.338) while hit rate falls, same-model ≈ 2× cross-model — not the absolute level | `SEMANTIC_CACHE.md`, `CACHE_PRECISION.md` |
| **Security** (uncontested novelty) | **WON — frontier dominance, now confirmed over the wire** | Shared semantic cache leaks prompt membership at AUC 0.88 from timing alone (sim); per-tenant partitioning returns the attacker to chance (0.50) at 24% latency cost with 76% hits kept — strictly dominating padding (never < 0.73) and TTL jitter (chance only at ~90% hit loss). **Executed against the real gateway process (`RESULTS_WIRE_ATTACK.md`): per-tenant AUC 0.502 (CI [0.384, 0.612]), 0 hits — WA-H1 PASS on the real HTTP/cache path; shared posture leaks perfectly on loopback (AUC 1.000, all 50 secrets)** | `ADVANCED.md` (figs 16–17), `RESULTS_WIRE_ATTACK.md` |

Composite objective J (cost + 2·violation + 0.5·(1−Jain), the objective every baseline
was tuned on): PolyForge wins against **every** baseline in **every** campaign that
tested it — v1 (p ≤ 4.6e-25), v2 (p ≤ 6.7e-25), v3 overload (p ≤ 1.3e-11), VTC slice
(p = 3.3e-19), real-demand replay (p ≤ 5.5e-05) — and is the only Pareto-undominated
system in the matrix.

---

## Campaign ledger (chronological)

### 1. v1 headline matrix — 1,800 runs + 500 ablations (W35–W36)

Blocked factorial (5 workload classes × 4 tenant mixes × 3 cluster sizes × reps × 6
systems), all baselines grid-search-tuned on the paper's own objective. PolyForge beats
every tuned baseline on composite J (p ≤ 4.6e-25, |d_z| 0.66–1.44) and on cost against
all five; the roadmap's raw per-metric gate **FAILS as measured** because
over-provisioned Static and cache-maxed GPTCache win SLO/hit-rate metrics by
construction at 12–30× spend. Ablations: −joint control +2884% cost, −cost-aware
eviction +35.7% cost, −classifier +2.1% cost / −45% relative hit rate. → `RESULTS.md`

### 2. Advanced tier — forecasters, realism, security (session 11)

Forecast ablation: the W30 trend default overreacts to noise; damped Holt −16.3%
violation at equal cost (p=0.0027) → Holt becomes the documented recommended default
(trend stays code default so the committed 1,800-run headline remains reproducible).
Reconfiguration realism (startup lag + half-warm caches): self-calibration earns
d_z=+0.46 (p=0.029). Security: the cache timing side channel and its defense (scoreboard
row above). → `ADVANCED.md`

### 3. v2 pre-registered matrix — 2,100 runs (Phases 0–5+8)

`jcac_v2` = Holt + adaptive capacity, protocol frozen in `PREREG_V2.md` before any run.
**H1 FAIL** (SLO not significantly below HPA/KEDA — parity at −43…−48% cost; beats FIRM
p=2e-19). **H2 PASS** (no large-effect self-regression; cost −11% vs v1, p=4e-36).
Iso-cost gate v2: budget-matching flips Static from win-by-spending to 3/5 loss; 36/60
cells are budget-infeasible for cache-max (outspends 36×). v1 `jcac` stays the headline
system per pre-registration. → `RESULTS_V2.md`

### 4. Fairness γ-ablation under injected interference — 200 runs (Phase 4)

Noisy-neighbor injection (>2× fair-share tenant steals ≤50% co-tenant capacity, detector
score visible to the controller). γ-term shows **no significant contribution** even in
the whale-only subgroup where the injection actually fires — negative result published,
term dropped from claims (stays in the system as a configurable weight).
→ `FAIRNESS_V2.md`

### 5. Semantic-cache protocol — pipeline verification, then the real-LMSYS headline (Phase 3b + B)

Real MiniLM encoder end-to-end; synthetic prompt set saturates under a semantic encoder
(its duplicate structure is lexical by design — documented caveat). **Real headline
(session 16c):** feasibility amendments committed *before* the gated download (31c73c7 —
seeded reservoir sample n=200,000 of 2,015,645 user turns, block-wise exact NN verified
bit-identical on synthetic, supplementary 10%-fixed baseline). As measured: adaptive
29.6% @ 0.85 / 48.8% @ 0.70; adaptive +93% vs the 10%-fixed baseline; $0.296/1k queries;
empirical h(K) fit hmax 0.285, K_half 6,569 entries. Adopting empirical h(c) constants
in `model.py` requires a new pre-registration (session 16b precedent).
→ `SEMANTIC_CACHE.md`

### 6. Forecast ablations — synthetic stand-in vs real trace (Phase 3a + C)

Stand-in (textbook diurnal, autocorr 0.85 @ 24 h): seasonal −44.8% RMSE. Real BurstGPT
v2.0 (10,632,194 requests, 335 days, analyzed per contiguous segment around a 104-day
collection gap): periodicity is weak (best autocorr ≤ 0.49 @ 8 h), **seasonal never
beats trend — the stand-in's prediction does not transfer**; damped Holt −26.9% RMSE
(segment 1), persistence −16.6% (segment 2), trend is the worst choice on real data.
Where stand-in and real trace disagree, the real trace is the reading that counts.
→ `FORECAST_TRACE.md`, `FORECAST_TRACE_REAL.md`

### 7. v3 overload matrix — 1,200 runs (Phase A)

Overload cells sized by model-constant arithmetic so the shared ±2/interval actuation
clamp actually binds (it never binds in the v1/v2 regime — that structural insight is
PREREG_V3 §1). **H1′ FAIL**: tuned HPA/KEDA buy overload attainment at 3.2–3.4× spend
(seasonal −69/−71% cost, +0.05/+0.09 violation); only JCAC honors cluster replica caps
(disclosed harness asymmetry, strict against us). **H2′ PASS**: seasonal < trend on
violation (p=6e-4) at no cost regression — the forecast mechanism is real. Declared
exploratory: on `spike_agentic` seasonal beats HPA on violation (p=5e-5, d_z=0.57) at
−46% cost. Composite J beats all three baselines (p ≤ 1.3e-11). Stopping rule closes
confirmatory SLO attempts for the thesis. → `RESULTS_V3.md`

### 8. Real-demand replay — first sample n=16, then powered n=96 (Phase C)

Real BurstGPT demand shape, 6 h windows, demand scale k=75.1 (frozen pre-run), arrival
jitter drawn once per window and shared across systems (exact pairing). **First sample
(n=16): HT FAIL** — underpowered (p≈0.07–0.11), but every point estimate reproduces the
v1 ranking (J −0.25..−0.28, cost −76%). **Powered second sample (n=96, independent
seeds, ~94% power): HT2 PASS with disclosure** — J beats HPA/KEDA/FIRM at p ≤ 5.5e-05
(d_z −0.43..−0.47), **cost −70% (−$77..−$80 per window, p ≤ 4.3e-06)**, and the
pre-registered disclosure is honored: a violation trade of +0.07 (p < 1e-6) rides along
— the same attainment-for-multiples-of-spend trade quantified everywhere else. Both
collection segments show the identical effect; the first sample's segment-2 parity was
small-n noise. Samples are never pooled. → `RESULTS_TRACE.md`, `RESULTS_TRACE2.md`

### 9a. Phase 7 live ordinal check — smoke + 12 runs on a real kind cluster (session 19)

The jcac arm's first live execution passed its smoke on the first attempt (1/1 valid,
actuation gate passed before load — the session-17 desk fixes held with zero bug tail).
Full mirrored slice: {jcac, hpa} × {crud_bursty, ai_cacheable} × 3 reps, **12/12 valid**
(one hpa attempt failed on a pod-level memcg OOM at 22:29:43 and was cleanly retried per
the retry-then-mark design; the validated data is the clean attempt's). Analyzed by the
pre-frozen protocol (`phase7_ordinal.py`, commit 650ce29, committed before any live
number existed). **As measured: the primary reading DISAGREES — the J winner flips in
both cells.** Descriptively, live the two arms land at *parity* on every metric (J
separations 0.002 with overlapping rep ranges; violations zero for both arms in both
cells) while the sim separates them decisively. Mechanisms: `ai_cacheable`'s sim
separation flows through the cache economy and the live cache knob is inert by
construction (the frozen caption's disclosure made concrete); in `crud_bursty` the sim's
HPA concedes 0.0417 violation where real HPA at this amplitude never violates — the sim
overestimates reactive lateness in that cell, a direction that had favored jcac in-sim.
What the campaign establishes: the full jcac loop runs live under a mechanical actuation
gate, capacity parity held, no live degradation. No thesis claim rested on live ranking
reproduction; the claims stand on the sim and replay substrates, scoped as such.
→ `PHASE7_ORDINAL.md`, run table `eval/results/phase7_live_runs.csv`

### 9. VTC-replica fairness comparison — 200 runs

Tuned least-weighted-service-first pool division (VTC's scheduler reduced to the replica
knob, OSDI '24), interference injection on. **HV1 PASS**: loses composite J to jcac
(d_z=−1.12, p=3e-19). **HV2 PASS (symmetric)**: the dedicated fair divider does *not*
buy a large fairness win — jcac is *more* fair on Jain (0.9705 vs 0.9599, p=1.5e-6) at
−35% cost, −36% worst-tenant p95. → `VTC_FAIRNESS.md`

### 10. Wave 1–2 leak-fill campaigns — robustness of the closed results (session 20)

Six DEFENSE_QA audit gaps (§14–19) turned into measurements. Wave 1 re-reads the
immutable databases only (no new runs); Wave 2 is four pre-registered campaigns, each
protocol pushed before its run (7deb6a3), the two economy reruns exact 1,800-cell
mirrors of the v1 headline matrix under a changed world, spot-checked bit-identical
(drift 0.00e+00) on the default path.

- **Tier-price sensitivity (§15).** Break-even accounting (`BREAKEVEN_TIER.md`,
  exploratory): re-pricing every rep-0 row across 60 frozen price vectors reverses no
  aggregate cost win anywhere. Decision rerun (`RESULTS_TIER_RATIO.md`, 1,800 runs at the
  measured GPU corner 1:1.516:16.64): **TR-H1 cost PASS 5/5** (p ≤ 6.8e-47), **TR-H2 J
  PASS 5/5**; given cheap mid-tier inference the joint controller re-plans into it
  (mid-tier steps 1.7% → 13.7%) and still wins — adaptation the frozen-decision reading
  cannot show.
- **Empirical cache curve (§16).** `RESULTS_HK_ADOPTION.md`, 1,800 runs under the
  measured LMSYS curve (hmax 0.285 vs assumed 0.85, half 19 MB), pessimistic for every
  cache-using system: hit rate halves (jcac 0.106 → 0.051), gptcache is devalued most
  (mean cost 13.5 → 90.5), jcac stops paying for cache (362 → 136 MB) — and **HK-H1 J
  PASS 5/5**, **HK-H2 cost PASS 5/5**. The J margin vs HPA narrows (−27.5% → −19.4%
  aggregate) but never closes: the advantage was never primarily cache-driven.
- **Objective-form robustness (§ threats-to-validity, exploratory).** `OBJECTIVE_FORM.md`:
  69/85 form×baseline combinations preserve the published direction at p<0.01 across five
  campaigns; all 16 non-wins are the already-disclosed +0.07 violation trade surfacing
  under violation-first orderings (F4 lexicographic) or log-cost compression near-misses
  (p 0.011–0.014) — the win's *shape*, not a refutation. Orthogonal to the weight sweep
  (`SENSITIVITY_J.md`, 416/425).
- **Sim chaos (§19, sim half).** `RESULTS_CHAOS_SIM.md`, 540 runs, controllers blind to
  the injection. **CH-H1 PASS**: jcac with a dead planner for 1 min beats a *healthy* HPA
  (dz −1.69, p 4.2e-19). **CH-H2 PASS**: under an identical 50% replica kill jcac keeps
  its J win (dz −1.79). Centralization-tax finding: a 1-min freeze costs jcac ΔJ +0.0063
  vs HPA's +0.0109 (p 3.8e-7) — a proactively-planned held config ages better than a
  frozen reactive one. Recovery within 2–5 steps.
- **Pseudo-per-tenant forecasting (§18).** `PSEUDO_TENANT.md`, 19 qualifying sub-streams
  (Model × Log Type of the same traces) under the frozen forecast protocol. **PT-H1 PASS,
  zero counterexamples**: no weakly-periodic (autocorr<0.5) stream shows a material
  seasonal win; all six periodic streams do (PT-H2); **three distinct forecasters win**
  across streams (PT-H3) — the per-tenant pluggability claim, measured one aggregation
  level down.

Deferred to a live Codespace session, pre-registered and desk-prepared now (c38bce6):
the over-the-wire membership attack (`PREREG_WIRE_ATTACK.md`) and live chaos + p99
(`PREREG_LIVE_CHAOS_P99.md`, p99 export landed and unit-tested at the desk). OSF
prospective mirror index: `OSF_REGISTRATION.md` (§17), submission is the one user step.

### 11. Wave 4 — planning-cell partitioning to 1024 tenants (session 21, local)

Executes the scaling lever `PLANNER_SCALING.md` named as future work (DEFENSE_QA #13),
on the deployed `PlannerCore.plan` code path, single laptop core; two pre-registered
campaigns.

- **Latency (`PLANNER_CELLS.md`, PREREG_PLANNER_CELLS.md at 264356a).** Partition the
  portfolio into fixed K=32 planning cells (each planned on an independent replica).
  **PS-H1 PASS:** partitioned per-cell p95 stays flat at ~195 ms through 1024 tenants
  (fitted exponent 0.27) while the monolithic joint plan grows to **71 s at 1024**
  (exponent 1.59, reproducing the ~1.45 joint-planner cost) and crosses the 3 s operator
  timeout at 128. Partitioning makes 1024 tenants deadline-feasible.
- **Fairness (`PLANNER_CELLS.md` + `PLANNER_CELLS_DEALIAS.md`).** **PS-H2 FAIL** under the
  frozen round-robin-on-index rule (worst ΔJain −0.089): diagnosed — round-robin aliases
  with the whale period (a whale every 8th tenant), so all whales collapse into a few
  cells whenever the cell count is a multiple of 8 (N ∈ {256,512,1024}). Published as
  measured with the diagnosis (erratum in the file), then the disciplined follow-up
  (`PREREG_PLANNER_CELLS_DEALIAS.md` at 0a61c6a, one changed factor): a hash-based
  assignment that decorrelates cell membership from index. **PF-H1 PASS: worst ΔJain
  −0.0053 through 1024** — the fairness cost was the aliasing, not partitioning. The
  engineering lesson (hash cells, not index round-robin) is itself measured.

### 12. Wave 3 — over-the-wire cache side-channel, executed (session 22)

The security half of Wave 3 ran for real. `research/security/wire_attack.py` drove the
frozen protocol (PREREG_WIRE_ATTACK + pre-run amendment) against a live `cmd/ai-gateway`
process — the production `SemanticCache` over real HTTP, deployed local n-gram embedder,
mock LLM backend for miss latency, both cache postures via the pre-registered
`POLYFORGE_CACHE_SHARED` flag (unit-tested: `TestSharedCachePostureLeaksCrossTenant`).
**WA-H1 PASS on the wire:** per-tenant isolation holds the attacker's membership AUC at
**0.502 (95% CI [0.384, 0.612]), zero cross-tenant hits** — chance, on the real cache/HTTP
path rather than the sim's timing model. Shared posture leaks perfectly on loopback (AUC
1.000, exactly the 50 victim-warmed secrets); measured hit/miss gap 15.6/98.7 ms. Scope,
disclosed: gateway *process* over loopback (no WAN jitter, which would only weaken the
shared number); exact-prompt membership threat (embedder-agnostic, since the offline
embedder is lexical). → `RESULTS_WIRE_ATTACK.md`

Still deferred (need the kind cluster + harness plumbing, not local Docker): the live
chaos *campaign* (fault injection wired into the harness) and a live p99 *number* (the
export is landed + unit-tested; persisting it through the harness remains). Runbook:
`docs/WAVE3_LIVE_RUNBOOK.md`.

### 13. Wave 5 — structural-form program + solver audit (session 24)

The sensitivity axis Waves 1–2 never varied: the simulator's *functional forms*. Four
pre-registered campaigns (protocols pushed at `7519958`/`fc7e72c`/`cfab691` before their
runs), each a full 1,800-run headline-matrix mirror, all validation-green and
spot-checked bit-identical:

- **Measured latency model** (a = 0.86 + ρ-dependent p95/mean tail fitted from the
  committed calibration — the correction that errs *against* lean postures, closing the
  one-sided half of DEFENSE_QA #16): **LM-H1/H2 PASS 5/5 each, LM-H3 (violation
  non-inferiority vs tuned hpa/keda, frozen +0.02 margin) PASS** — jcac still violates
  less than hpa paired (−0.0057). → `RESULTS_LM_ADOPTION.md`
- **Mixture-percentile p95** (ai_p95 as the true hit/miss mixture quantile instead of
  mean×1.4 — withdraws the cache's cosmetic tail credit): **MX-H1/H2 PASS 5/5,
  MX-H3 PASS**; violations rise for everyone (the honest tail), jcac trims cache
  362 → 329 MB (declared devalued-knob adaptation). → `RESULTS_MIXTURE_P95.md`
- **Tier-scaled work units** (mid 1.516×, large 16.64× — the measured serving-time
  ratios; a heavier model can no longer congest the pool for free): **TW-H1/H2 PASS
  5/5**; jcac's small-heavy posture untouched, gptcache's tier-up posture pays
  (violation 0.049 → 0.133), exactly the pre-declared mechanism. → `RESULTS_TIER_WU.md`
- **Solver audit + clamp adjudication:** the coordination gap is **zero on every scored
  instance** (83/83, then 120/120 anchored) — and the audit *caught the published
  controller exceeding its per-interval move clamps* via the second CD sweep (±4
  replicas / two cache levels vs every baseline's ±2 / one; present in every committed
  jcac run; the v3 cells' arithmetic assumed the clamp was shared). The pre-registered
  clamp-fixed rerun: **MC-H1/H2 PASS 5/5 each, MC-H3 PASS 2/2**, and the fixed
  controller is marginally *better* (J −27.9% vs hpa against v1's −27.5%) — the
  advantage carried nothing, and `anchor_moves=True` is the quotable configuration.
  → `COORD_GAP.md`, `COORD_GAP_ANCHORED.md`, `RESULTS_MOVE_CLAMP.md`

Engineering landed alongside (live path, no committed number changed): churn-safe +
thread-safe planner state, deployment-selectable forecasters (`--forecast`), and the
multi-resolution `seasonal_mr` forecaster that makes day-scale periodicity visible to
the deployed planner (`FORECAST_MR.md` — engineering validation, explicitly not a
thesis claim). The exploratory all-forms arm (`matrix_structreal`, declared in
PREREG_MIXTURE_P95) informs future-work text only.

---

## Which number to cite (disambiguation)

| claim | use this number | not this one | why |
|---|---|---|---|
| Cost vs baselines, real demand | **−70%** (n=96, p≤4.3e-06, `RESULTS_TRACE2.md`) | −76% (n=16, `RESULTS_TRACE.md`) | −76% is the underpowered first sample's point estimate (HT FAIL); −70% is the powered confirmatory result. The first sample stands as measured but is not the citable headline |
| Cost vs baselines, synthetic matrix | −36…−48% vs HPA/KEDA/FIRM (`RESULTS.md`) | — | Different substrate; never mix with replay numbers (ground rule 4) |
| Forecasting gain | **Holt −26.9% RMSE** on real trace segment 1 | seasonal −44.8% (`FORECAST_TRACE.md`) | −44.8% is the synthetic stand-in; it did not transfer to real data and is published as a non-transfer |
| Forecasting, external validity | **per-stream winner tracks periodicity** (Azure-code: seasonal −15.6% at autocorr 0.57; Azure-conv: persistence; BurstGPT: Holt — `FORECAST_AZURE.md`) | "Holt is the best forecaster" | Holt's win is scoped to BurstGPT; the cross-trace claim is that forecaster choice should follow the stream's measured structure — the pluggable-forecaster design, not any fixed method |
| SLO | parity at −43…−48% cost (v2); mechanism p=6e-4 (v3 H2′) | any "beats HPA/KEDA on violation" claim | H1 and H1′ both FAILED; only the iso-attainment framing and the mechanism are claimable |
| Fairness | Jain 0.9705 vs VTC 0.9599 (p=1.5e-6) + FIRM d_z=1.0 | γ-ablation | the γ-term itself is a published null |
| Security | AUC 0.88 → 0.50 at 24% latency / 76% hits kept | — | frontier table in `ADVANCED.md` |
| Cache hit-rate headline | **29.6% @ 0.85 (48.8% @ 0.70), real LMSYS, +93% vs 10%-fixed** | synthetic 1.000 saturation; the +594% vs 200-entry fixed | synthetic saturates under a real encoder (caveat); the 200-entry fixed point is a strawman at 100k inserted — cite the 10%-fixed comparison |
| Cache hit quality | **precision-vs-τ shape + strata** (0.443 same-model vs 0.223 cross-model; first-turn 0.353; `CACHE_PRECISION.md`) | "69% of hits are wrong" | the ρ=0.70 proxy is deflated by LLM response stochasticity — near-duplicate prompts (τ≥0.95) only agree 33.8% — so the absolute precision under-states correctness; relative readings are the claim |
| Effect sizes, real-demand replay | **d_z with 95% bootstrap CI** (`EFFECT_SIZES.md`, n=96 rows) | bare p-values below ~1e-6 | at hundreds of paired seeded runs, tiny p measures simulator determinism; the interval is the citable unit |
| Planner scalability | growth exponent 1.45; p95 crosses the 3 s timeout at 128 tenants (`PLANNER_SCALING.md`) | "linear in tenants" (design intuition) | measured super-linearity is the price of the joint fairness term; past the timeout the designed fallback holds the last good plan |
| Live validation | **both arms live-verified end-to-end; ordinal check recorded DISAGREE — live parity at the replica-only projection** (`PHASE7_ORDINAL.md`) | any "sim ranking confirmed live" claim | the frozen protocol's primary reading flips in both cells; live separations (0.002 in J) are within rep spread, and the cache lever behind the sim's `ai_cacheable` separation is inert live by construction |
| Cost/J win robustness to prices & cache curve | **survives both: TR-H1/H2 PASS 5/5 at the measured GPU price corner (`RESULTS_TIER_RATIO.md`); HK-H1/H2 PASS 5/5 under the measured cache curve (`RESULTS_HK_ADOPTION.md`)** | the published-economy numbers as if they were the only economy | the headline still runs on the published economy for bit-reproducibility; these two pre-registered reruns are the sensitivity evidence, cite them *as* robustness, not as replacements |
| Fallback under failure | **jcac with a 1-min-dead planner beats a healthy HPA (CH-H1, dz −1.69); a freeze costs jcac *less* than HPA (`RESULTS_CHAOS_SIM.md`)** | any live-chaos claim | the chaos campaign is sim-substrate (controllers blind, engine-injected); the live chaos demonstration is pre-registered and deferred |
| Robustness to the model's *forms* | **LM/MX/TW all PASS with narrowed margins; SLO non-inferiority held in all three (`RESULTS_LM_ADOPTION.md`, `RESULTS_MIXTURE_P95.md`, `RESULTS_TIER_WU.md`)** | the published-form numbers as if forms were validated wholesale | same rule as the economy reruns: cite these *as* structural robustness; intra-interval determinism and p95-as-finest-statistic remain unvaried |
| Controller spec & solver quality | **anchored controller: clamps honored, CD = exact joint optimum 120/120 at N≤3, MC 5/5-5/5-2/2 (`RESULTS_MOVE_CLAMP.md`, `COORD_GAP_ANCHORED.md`)** | the published controller's numbers as the quotable config | the published jcac exceeded its own per-interval clamps (audit-caught, disclosed); its matrices stand as the bit-reproducible record, but the anchored numbers are what the thesis quotes |
| Per-tenant forecasting | **boundary reproduces one level down: 0 counterexamples, 3 distinct winners across 19 sub-streams (`PSEUDO_TENANT.md`)** | "validated on per-tenant SaaS series" | the decomposition is Model×Log-Type of aggregate traces; true per-tenant series remain unavailable (stated) |
| Planner scale to 1024 | **planning cells: per-cell p95 ~195 ms flat vs 71 s monolithic (PS-H1); fairness preserved under hash assignment, ΔJain −0.0053 (PF-H1)** (`PLANNER_CELLS*.md`) | round-robin's ΔJain −0.089 as the fairness cost | that drop is a whale-period/cell-count aliasing artifact (PS-H2 FAIL, diagnosed); hash-based cell assignment is the measured fix |

## Honest-nulls ledger

Published negatives, all pre-registered or declared before measurement — these are part
of the contribution, not failures to hide: **v2 H1** (SLO vs HPA/KEDA), **v3 H1′**
(overload SLO vs HPA/KEDA), **first-sample HT** (underpowered), **γ-term** (no
significant fairness contribution under injection), **seasonal non-transfer** (synthetic
prediction failed on real data), **v1/v2 raw per-metric gate** (structurally impossible
against by-construction winners; framing documented in `RESULTS.md`), **cache hit quality** (a majority of τ=0.85 hits fail the ρ=0.70 response-agreement proxy — published with its stochasticity-ceiling calibration rather than hidden behind the hit-rate headline), **live ordinal check** (J-winner DISAGREE in both cells — live parity between the arms at the replica-only projection; frozen protocol, published as measured in `PHASE7_ORDINAL.md`).

## Cross-campaign ground rules

1. Every campaign's protocol was committed and pushed **before** its first run
   (13 pre-registrations: PREREG_V2, PREREG_V3, PREREG_TRACE, PREREG_TRACE2, PREREG_VTC,
   PREREG_TIER_RATIO, PREREG_HK_ADOPTION, PREREG_CHAOS_SIM, PREREG_PSEUDO_TENANT,
   PREREG_PLANNER_CELLS, PREREG_PLANNER_CELLS_DEALIAS — plus the two deferred live
   protocols PREREG_WIRE_ATTACK and PREREG_LIVE_CHAOS_P99 pushed before any live number
   exists — plus the v1 gate and Phase 4 acceptance frozen in the roadmap/V2_README). The
   PREREG_PLANNER_CELLS_DEALIAS follow-up is itself the disciplined response to a confound
   found in the frozen PLANNER_CELLS run — a new pre-registration, not a silent re-run.
   From Wave 2 on, each is additionally mirrored to OSF prospectively
   (`OSF_REGISTRATION.md`; the submit step is a flagged user action).
2. Closed campaigns are immutable: stopping rules forbid re-running, widening, or
   post-hoc tuning; per-campaign files are never edited after their run.
3. Substrates never mix: matrix tables, replay tables, and forecast-ablation tables are
   reported on their own substrate only. Replay dollars are model-scale; the claim is
   the ranking.
4. Samples are never pooled (n=16 and n=96 replays are separate experiments).
5. Sim latency metrics are p95, not p99 — the sim is a p95 estimator by construction
   (`model.P95_FACTOR`, documented deviation in `eval/README.md`). p99 is a **live-only**
   number: `control-plane eval-export` emits live p95 and p99 side by side, and the live
   p99 reading (`PREREG_LIVE_CHAOS_P99.md`, deferred) is never back-fitted into a sim
   table.
6. All of this is sim-backend decision quality under a stated system model
   (`research/jcac_sim/model.py`), not live-cluster absolutes.

Regeneration: each campaign file names its own script (`run_analysis.py`,
`trace_matrix.py --analyze`, `trace_matrix2.py --analyze`, `analysis_vtc.py`,
`fairness_v2.py`, `forecast_trace_real.py`, `semantic_cache_eval.py`, `advanced.py`).
This master file is hand-maintained; update it when — and only when — a campaign file
changes.
