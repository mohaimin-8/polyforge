# PolyForge — consolidated results (master record)

**This is the single reading entry point for every measured result in the project.**
The per-campaign files it consolidates (`RESULTS.md`, `RESULTS_V2.md`, `RESULTS_V3.md`,
`RESULTS_TRACE.md`, `RESULTS_TRACE2.md`, `VTC_FAIRNESS.md`, `FAIRNESS_V2.md`,
`FORECAST_TRACE.md`, `FORECAST_TRACE_REAL.md`, `ADVANCED.md`, `SEMANTIC_CACHE.md`) are
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
| **Forecasting** | **WON — on real data** | On the real 10.63M-request BurstGPT trace, damped Holt (jcac_v2's forecaster) cuts one-step RMSE −26.9% vs the trend default (segment 1); the synthetic stand-in's −44.8% seasonal prediction **does not transfer** and is published as such; seasonal's mechanism is confirmed only where real periodicity exists (v3 H2′) | `FORECAST_TRACE_REAL.md`, `FORECAST_TRACE.md`, `RESULTS_V3.md` |
| **Cache** | **CLOSED — real-data headline measured** | On real LMSYS-Chat-1M (200k-turn reservoir sample, MiniLM, exact NN): adaptive semantic hit rate **29.6% @ cosine 0.85, 48.8% @ 0.70** (InstCache's 51.34% anchor is their different protocol on the same dataset — placed beside, never head-to-head); adaptive beats a non-strawman fixed cache at 10% of inserted by **+93%** (29.6 vs 15.3); **$0.296 saved/1k queries** at mid-tier pricing; empirical h(K): hmax 0.285, K_half ≈ 6.6k entries (~19 MB) — the sim's assumed curve was optimistic, documented, constants unchanged | `SEMANTIC_CACHE.md` |
| **Security** (uncontested novelty) | **WON — frontier dominance** | Shared semantic cache leaks prompt membership at AUC 0.88 from timing alone; per-tenant partitioning returns the attacker to chance (0.50) at 24% latency cost with 76% hits kept — strictly dominating padding (never < 0.73) and TTL jitter (chance only at ~90% hit loss) | `ADVANCED.md` (figs 16–17) |

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

### 9. VTC-replica fairness comparison — 200 runs

Tuned least-weighted-service-first pool division (VTC's scheduler reduced to the replica
knob, OSDI '24), interference injection on. **HV1 PASS**: loses composite J to jcac
(d_z=−1.12, p=3e-19). **HV2 PASS (symmetric)**: the dedicated fair divider does *not*
buy a large fairness win — jcac is *more* fair on Jain (0.9705 vs 0.9599, p=1.5e-6) at
−35% cost, −36% worst-tenant p95. → `VTC_FAIRNESS.md`

---

## Which number to cite (disambiguation)

| claim | use this number | not this one | why |
|---|---|---|---|
| Cost vs baselines, real demand | **−70%** (n=96, p≤4.3e-06, `RESULTS_TRACE2.md`) | −76% (n=16, `RESULTS_TRACE.md`) | −76% is the underpowered first sample's point estimate (HT FAIL); −70% is the powered confirmatory result. The first sample stands as measured but is not the citable headline |
| Cost vs baselines, synthetic matrix | −36…−48% vs HPA/KEDA/FIRM (`RESULTS.md`) | — | Different substrate; never mix with replay numbers (ground rule 4) |
| Forecasting gain | **Holt −26.9% RMSE** on real trace segment 1 | seasonal −44.8% (`FORECAST_TRACE.md`) | −44.8% is the synthetic stand-in; it did not transfer to real data and is published as a non-transfer |
| SLO | parity at −43…−48% cost (v2); mechanism p=6e-4 (v3 H2′) | any "beats HPA/KEDA on violation" claim | H1 and H1′ both FAILED; only the iso-attainment framing and the mechanism are claimable |
| Fairness | Jain 0.9705 vs VTC 0.9599 (p=1.5e-6) + FIRM d_z=1.0 | γ-ablation | the γ-term itself is a published null |
| Security | AUC 0.88 → 0.50 at 24% latency / 76% hits kept | — | frontier table in `ADVANCED.md` |
| Cache hit-rate headline | **29.6% @ 0.85 (48.8% @ 0.70), real LMSYS, +93% vs 10%-fixed** | synthetic 1.000 saturation; the +594% vs 200-entry fixed | synthetic saturates under a real encoder (caveat); the 200-entry fixed point is a strawman at 100k inserted — cite the 10%-fixed comparison |
| Effect sizes, real-demand replay | **d_z with 95% bootstrap CI** (`EFFECT_SIZES.md`, n=96 rows) | bare p-values below ~1e-6 | at hundreds of paired seeded runs, tiny p measures simulator determinism; the interval is the citable unit |
| Planner scalability | growth exponent 1.45; p95 crosses the 3 s timeout at 128 tenants (`PLANNER_SCALING.md`) | "linear in tenants" (design intuition) | measured super-linearity is the price of the joint fairness term; past the timeout the designed fallback holds the last good plan |

## Honest-nulls ledger

Published negatives, all pre-registered or declared before measurement — these are part
of the contribution, not failures to hide: **v2 H1** (SLO vs HPA/KEDA), **v3 H1′**
(overload SLO vs HPA/KEDA), **first-sample HT** (underpowered), **γ-term** (no
significant fairness contribution under injection), **seasonal non-transfer** (synthetic
prediction failed on real data), **v1/v2 raw per-metric gate** (structurally impossible
against by-construction winners; framing documented in `RESULTS.md`).

## Cross-campaign ground rules

1. Every campaign's protocol was committed and pushed **before** its first run
   (7 pre-registrations: PREREG_V2, PREREG_V3, PREREG_TRACE, PREREG_TRACE2, PREREG_VTC,
   plus the v1 gate and Phase 4 acceptance frozen in the roadmap/V2_README).
2. Closed campaigns are immutable: stopping rules forbid re-running, widening, or
   post-hoc tuning; per-campaign files are never edited after their run.
3. Substrates never mix: matrix tables, replay tables, and forecast-ablation tables are
   reported on their own substrate only. Replay dollars are model-scale; the claim is
   the ranking.
4. Samples are never pooled (n=16 and n=96 replays are separate experiments).
5. Latency metrics are p95, not p99 (documented deviation, `eval/README.md`).
6. All of this is sim-backend decision quality under a stated system model
   (`research/jcac_sim/model.py`), not live-cluster absolutes.

Regeneration: each campaign file names its own script (`run_analysis.py`,
`trace_matrix.py --analyze`, `trace_matrix2.py --analyze`, `analysis_vtc.py`,
`fairness_v2.py`, `forecast_trace_real.py`, `semantic_cache_eval.py`, `advanced.py`).
This master file is hand-maintained; update it when — and only when — a campaign file
changes.
