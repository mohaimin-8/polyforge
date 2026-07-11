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
| **Cache** | **PARTIAL** | +83% vs fixed sim (0.106 vs 0.058); large-effect hit-rate wins vs scalers; loses hit rate to cache-max iso-cost | the InstCache/SCALM-comparable number: real LMSYS-Chat-1M + real embedder |
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

## Phase A — SLO overload + trace-periodicity matrix (2 sessions, local)

- [ ] `research/analysis/PREREG_V3.md` **committed before any run**: systems
      (`jcac`, `jcac_seasonal`, HPA, KEDA, FIRM — all existing specs, untouched),
      the new cell axes, H1′ and the stopping rule.
  - **H1′ (confirmatory):** on overload/periodic cells, PolyForge (jcac or
    jcac_seasonal, *named in advance* — pick one, no post-hoc selection) shows SLO
    violation significantly below HPA and KEDA (paired, p<0.01) with paired cost
    point estimate ≤ 0. Same bar H1 failed at; new regime, not new tuning.
- [ ] `eval/experiments/matrix_v3_overload.yaml`:
  - **Overload axis:** step/ramp cells whose ramp time is shorter than the tuned
    HPA/KEDA stabilization+reaction window (derive the threshold from their tuned
    params in `eval/baselines/TUNING.md` — committed in the prereg, not swept).
  - **Periodicity axis:** demand driven by the committed BurstGPT stand-in's diurnal
    shape (time-compressed so ≥3 periods fit a run), giving `jcac_seasonal`'s
    detector a real period at control timescale.
  - Include *non*-overload control cells so the claim is "wins where reaction time
    binds, ties elsewhere" — the reviewer-proof shape.
- [ ] Run (~600–900 runs, CPU, same harness), `analysis_v3.py` → `RESULTS_V3.md`,
      reported alongside v1/v2, never instead.
- [ ] Acceptance: H1′ verdict as measured. If PASS: SLO segment claim becomes
      "under demand that outruns reactive scaling, PolyForge cuts violations at
      lower cost; elsewhere it matches attainment at ~half the cost." If FAIL:
      published as measured; the standing claim below still holds.
- **Standing claim already earned (no run needed):** parity attainment at −43…−48%
  cost *is* the iso-attainment win — Chiron's "+90%" is an "up to" on their own
  substrate (ground rule 4 forbids the head-to-head cell anyway). Write this frame
  into the thesis SLO section regardless of H1′.

## Phase B — Cache headline on real LMSYS-Chat-1M (1 session + user unblock)

- [ ] **USER:** Hugging Face account, accept the LMSYS-Chat-1M gate, provide token;
      `pip install sentence-transformers` (CPU is fine for MiniLM).
- [ ] Run the *committed, unchanged* protocol:
      `semantic_cache_eval.py --conversations lmsys-chat-1m/*.parquet` with
      `all-MiniLM-L6-v2`. The protocol was frozen before the data was seen — that is
      the pre-registration.
- [ ] Report: hit-rate curve (fixed vs adaptive) on the same dataset InstCache
      reports 51.34% on, **plus dollar-weighted savings** — the metric no cache-only
      paper can produce, and the segment's unique win condition.
- [ ] Feed the empirical `h(c)` fit into `model.py` (Phase 3b completion); rerun the
      affected figure only.
- [ ] Acceptance: a real-dataset semantic hit-rate number exists next to the
      InstCache/SCALM anchors (their shape, our substrate), with $-savings attached.

## Phase C — Cost & forecasting real-trace restatement (1 session, download only)

- [ ] Download real BurstGPT CSV (multi-GB, network); run the committed
      `etl_burstgpt.py` normalize (identical code path already tested).
- [ ] `forecast_trace.py --trace <real parquet>` — upgrades the −44.8% forecasting
      result from "committed stand-in" to "10.31M real Azure requests."
- [ ] Headline-matrix-on-BurstGPT replay (V2_README 3a deferred item) — the
      "on 10M real requests" cost restatement.
- [ ] Acceptance: both segments' numbers pointable at a real public trace; no
      synthetic-only asterisk left on a won segment.

## Phase D — Fairness strengthening (optional, 1 session)

The segment is already resolved (Jain win vs FIRM at dz=0.95; γ honestly nulled).
The scoreboard's named target is VTC's *empirical* side:
- [ ] Implement a VTC-style token-weighted fair scheduler as a baseline SystemSpec
      (service ordered by accumulated weighted tokens — their published mechanism,
      tuned per TUNING.md rules); pre-register the comparison (Jain + worst-tenant
      p95 under the Phase 4 interference injection).
- [ ] Either outcome is reportable: if VTC-style wins Jain but pays cost/SLO, the
      composite J and the "joint control absorbs interference" finding both stand.
- [ ] Do **not** resurrect the γ-term; the null is published.

## Sequencing (one bounded slice per session)

| Session | Slice | Blocked on |
|---|---|---|
| 14 (this) | scoreboard audit + this plan committed | — |
| 15 | PREREG_V3.md + matrix_v3_overload.yaml (no runs) | — |
| 16 | run v3 matrix + RESULTS_V3.md | — |
| 17 | Phase B cache run | **user: HF token + deps** |
| 18 | Phase C downloads + trace reruns | network (multi-GB) |
| 19 | Phase D and/or Phase 9 hardening + README v2/v3 numbers | — |

Also outstanding from v2: commits `f8aa8e6`+`1388a59` are **unpushed** — push before
anything else touches the tree.

## What this plan will not do

No post-hoc tuning of `jcac_v2` (stopping rule), no baseline crippling, no substrate
mixing, no deletion of the H1 FAIL or γ-null paragraphs. The wins are bought with new
regimes and real data, which is the only currency a committee accepts.
