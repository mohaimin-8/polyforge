# PolyForge v3 — Closing the Remaining Segments

Drafted 2026-07-11 (session 14). Extends `docs/V2_README.md` (Phases 0–5 + 8 complete);
same non-negotiable ground rules: pre-register before running, tuned baselines, no
substrate mixing, v1/v2 artifacts immutable. A win bought by breaking these is worth
zero in a defense — every lever below is a *new pre-registered experiment* or a
*real-dataset completion*, never a re-tune of a failed hypothesis.

## Segment scoreboard after v2 (honest state)

| Segment | Verdict | Evidence | What's missing |
|---|---|---|---|
| **Cost** | **WON** (on substrate) | −43%/−48% vs tuned HPA/KEDA, p<1e-33, dz 0.8–1.1; wins cost vs all 5 baselines at large effect | real-BurstGPT restatement (download only) |
| **SLO** | **NOT WON** — honest null | H1 FAIL: violation parity with HPA/KEDA (at −43…−48% cost); beats FIRM p=2e-19 | the pre-registered *overload cells* lever was never run |
| **Cache** | **CLOSED (16c)** | real LMSYS: 29.6% @ 0.85 / 48.8% @ 0.70, +93% vs 10%-fixed, $0.296/1k; h(K) fit done | — (was: the real-LMSYS number; delivered) |
| **Fairness** | **WON vs FIRM**; γ null | Jain 0.969 vs 0.929, p=7e-44, dz=0.95; γ-ablation null under injection (dropped from claims) | optional: VTC-style baseline for the scoreboard's actual target |
| **Forecasting** | **WON** | seasonal −44.8% RMSE vs trend on daily periodicity (autocorr 0.85), exactly as predicted | pointer run on real trace (same download as Cost) |

Net: 2 clear wins, 1 win-with-asterisk, 2 open. The two open segments have concrete,
un-attempted, rule-compliant levers.

## Why SLO is winnable despite the H1 FAIL

The v2 stopping rule forbids re-running or re-tuning `jcac_v2` on the existing matrix —
and that is not the path anyway. The matrix's demand shapes are smooth enough that
reactive scalers keep up: when everyone can react in time, everyone ties on violation,
and the only degree of freedom left is cost (which we won). The V2_README target
scoreboard named the real lever — **"+ overload cells"** — and Phase 1 shipped without
them (`grep -ri overload eval/` → nothing). Where demand ramps faster than a reactive
scaler's stabilization window, proactive forecasting is *structurally* required, not
incrementally better. Separately, Phase 3a proved the seasonal forecaster cuts one-step
RMSE 44.8% on real daily periodicity — but the matrix cells never expose the control
loop to that periodicity, so the advantage never gets to act. Both facts point at the
same experiment: **new cells, not new tuning.**

## Phase A — SLO overload + trace-periodicity matrix — DONE (session 14, as measured)

- [x] `research/analysis/PREREG_V3.md` committed **and pushed** before any run
      (commit 6229b6a); `jcac_seasonal` named in advance as the sole treatment;
      analysis_v3.py frozen in the same commit.
- [x] `eval/experiments/matrix_v3_overload.yaml`: four classes sized by
      model-constant arithmetic (pinned by unit tests, not trial runs) —
      `flash_crud`/`flash_ai` (5-of-16-step 6× bursts), `spike_agentic`
      (2-of-12-step 8× spikes), `ramp_gentle` control (same envelope,
      actuation-feasible slope). 1,200 runs, **1,200/1,200 valid, 0 failed**.
- [x] `analysis_v3.py` → `RESULTS_V3.md` (commit 7021573), executed once per
      the stopping rule.
- [x] **Verdict as measured: H1′ FAIL, H2′ PASS.** Pooled, tuned HPA/KEDA buy
      overload attainment with 3.2–3.4× spend (seasonal −69/−71% cost at
      +0.05/+0.09 violation), and only PolyForge honors the cluster capacity
      cap at these amplitudes. The *mechanism* is confirmed (H2′: seasonal <
      trend on violation, p=6e-4, no cost regression), and the
      declared-exploratory spike class shows the full predicted phenomenon —
      seasonal beats HPA on violation (p=5e-5, d_z=0.57) at 46% lower cost.
      Composite J: seasonal wins vs all three baselines (p ≤ 1e-11). Per
      PREREG_V3 §4 the attempt is closed: no third confirmatory attempt inside
      this thesis.
- **The SLO section's final shape (post-v3):** (1) iso-attainment framing —
  parity attainment at −43…−48% cost (RESULTS_V2.md); (2) confirmed mechanism —
  period-aware forecasting reduces violations over trend at no cost premium
  (H2′, RESULTS_V3.md); (3) declared-exploratory spike-class result — where
  reaction is structurally impossible, proactive control beats HPA on violation
  *and* cost simultaneously; (4) the honest boundary — reactive scalers at
  SLO-generous tuning can outbuy proactive control on attainment at 3.2–3.4×
  spend, and PolyForge alone respects shared-cluster capacity limits. Chiron's
  "+90%" stays an "up to" on their own substrate (ground rule 4).

## Phase B — Cache headline on real LMSYS-Chat-1M — DONE (session 16c, as measured)

- [x] **Author:** token provided 2026-07-12; gate accepted; sentence-transformers
      already installed (session 14).
- [x] Protocol run with one *declared pre-run amendment* (commit 31c73c7, pushed
      **before** the gated download): the committed protocol is infeasible at full
      scale (2M+ turns → ~40 TB dense similarity matrix), so — seeded reservoir
      sample n=200,000 of 2,015,645 user turns; block-wise exact NN (verified
      bit-identical on synthetic); supplementary 10%-fixed baseline so the fixed
      point is not a strawman. Semantics unchanged; the amendment chain is the
      pre-registration's honest continuation.
- [x] Reported (`SEMANTIC_CACHE.md`): adaptive **29.6% @ cosine 0.85 / 48.8% @
      0.70** on the dataset InstCache reports 51.34% on (their mechanism differs —
      beside, not head-to-head); **+93% vs 10%-fixed** (15.3%); **$0.296/1k queries**
      at mid-tier pricing.
- [x] Empirical `h(K)` measured and fitted (hmax 0.285, K_half 6,569 entries ≈ 19 MB):
      Phase 3b complete. *Deliberate deviation from this plan's wording:* the fit is
      documented next to the sim's assumed curve but NOT fed into `model.py` —
      mutating committed constants breaks bit-reproducibility; adopting them is a new
      pre-registered experiment (session 16b calibration precedent).
- [x] Acceptance met: a real-dataset semantic hit-rate number sits next to the
      InstCache/SCALM anchors with $-savings attached.

## Phase C — Cost & forecasting real-trace restatement — forecasting half DONE (session 14)

- [x] Real BurstGPT v2.0 downloaded (429 MB, 3 CSVs — not multi-GB) and normalized:
      10,632,194 requests / 335 days through the committed `normalize()`
      (`research/traces/fetch_burstgpt.py`, deterministic, raw data gitignored).
- [x] Forecast ablation on the real trace (`forecast_trace_real.py`, same protocol
      functions): **the stand-in's seasonal prediction does not transfer** — real
      periodicity is weak (best autocorr ≤0.49 @ lag 8 h), seasonal never beats
      trend; **Holt −26.9% RMSE (segment 1), persistence −16.6% (segment 2)**.
      Honest upgrade: the real data confirms the W36+ Holt recommendation and
      `jcac_v2`'s forecaster; seasonal's value is bounded to strongly periodic
      regimes (RESULTS_V3.md H2′ proves the mechanism there). Reported per
      contiguous segment (104-day collection gap = missing data, not zero demand),
      gap-inclusive numbers alongside.
- [x] Headline-matrix-on-BurstGPT replay — DONE as measured (session 15,
      PREREG_TRACE.md committed+pushed 717f8bd before the run; RESULTS_TRACE.md,
      commit 01cb5ed). **HT FAIL on the p<0.01 bar at n=16 windows — but every
      point estimate matches the v1 headline**: J 0.33 vs 0.57–0.61, cost −76%
      vs HPA/KEDA/FIRM at violation +0.07; the effect concentrates in the
      high-traffic collection period (J diff ≈ −0.5) and vanishes in the quiet
      one. ET1 confirms Holt > trend on real shape (d_z=−0.62); ET2 confirms
      seasonal ≈ trend under weak periodicity. A higher-powered replay (more
      windows) is permitted only as a new pre-registration in a new file.
- [x] Powered second replay — DONE as measured (session 15, `PREREG_TRACE2.md`
      committed+pushed b09a492 before the run; `RESULTS_TRACE2.md`, commit
      cb96dfd). **HT2 PASS with disclosure** at n=96 independent seeds: J beats
      HPA/KEDA/FIRM at p ≤ 5.5e-05, **cost −70%** (p ≤ 4.3e-06), violation
      trade +0.07 disclosed as pre-registered. The real-demand headline is now
      confirmatory; samples never pooled. **−70% is the citable number**, not
      the n=16 sample's −76% (see RESULTS_MASTER.md disambiguation).
- [x] Acceptance (forecasting): the segment's numbers are pointable at a real
      public trace, including the parts that contradicted the stand-in.

## Phase D — Fairness strengthening — DONE (session 15, as measured)

The segment is already resolved (Jain win vs FIRM at dz=0.95; γ honestly nulled).
The scoreboard's named target is VTC's *empirical* side:
- [x] `vtc_replica` implemented and tuned per TUNING.md (commit acb792b);
      comparison pre-registered in `PREREG_VTC.md` before the run.
- [x] Outcome as measured (`VTC_FAIRNESS.md`, commit bfb70fc): **HV1 PASS,
      HV2 PASS** — jcac beats the dedicated fair divider on composite J
      (d_z=−1.12, p=3e-19) *and on Jain itself* (0.9705 vs 0.9599, p=1.5e-6)
      at −35% cost, −36% worst-tenant p95.
- [x] γ-term not resurrected; the null stands.

## Sequencing (one bounded slice per session)

| Session | Slice | Blocked on |
|---|---|---|
| 14 (this) | scoreboard audit + this plan committed | — |
| 15 | PREREG_V3.md + matrix_v3_overload.yaml (no runs) | — |
| 16 | run v3 matrix + RESULTS_V3.md | — |
| 17 | Phase B cache run | **author: HF token + deps** |
| 18 | Phase C downloads + trace reruns | network (multi-GB) |
| 19 | Phase D and/or Phase 9 hardening + README v2/v3 numbers | — |

~~Also outstanding from v2: commits `f8aa8e6`+`1388a59` are unpushed.~~ Resolved:
all work through `cb96dfd` is pushed, CI green (session 15).

## What this plan will not do

No post-hoc tuning of `jcac_v2` (stopping rule), no baseline crippling, no substrate
mixing, no deletion of the H1 FAIL or γ-null paragraphs. The wins are bought with new
regimes and real data, which is the only currency a committee accepts.
