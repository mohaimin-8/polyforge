# Pre-registration — replay on real Azure LLM 2024 demand (second real trace, external validity)

Registered 2026-07-19 (session 27). Committed and pushed **before** any replay
run; the push event is the timestamp anchor; OSF mirror prospective. This
answers DEFENSE_QA #12's residual head-on: the replay evidence for the
composite-J ranking rests on one demand trace (BurstGPT). The Azure LLM
inference trace (2024 release) is the second real LLM-serving demand trace
already committed to the tree (`etl_azure_llm.py`; used so far only for the
forecast ablation, `FORECAST_AZURE.md`). This pre-registration extends it to
the **replay substrate**: same engine, same tuned systems, same objective,
new real demand shape.

## §1 Source and its measured properties (fixed by the committed ETL)

`research/traces/out/azure_llm_2024.csv.gz` — 44,107,694 production requests
from two real Azure LLM services (conv: 27,303,999 @ 35.11 rps mean; code:
16,803,695 @ 21.61 rps mean), spanning exactly 216 h with **one contiguous
segment** (no ≥24 h gaps; probe rerunnable from the committed ETL output).
CC-BY, DynamoLLM/HPCA'25 dataset. These are data properties, not results.

## §2 Protocol (frozen)

Demand construction mirrors PREREG_TRACE §2 with these fixed deltas:

- **Pseudo-tenantization, disclosed:** the release carries two real streams,
  not eight tenants. Each stream is split into 4 pseudo-tenants by
  deterministic round-robin over the stream's rows in timestamp order
  (`conv` → t00–t03, `code` → t04–t07). At the 600 s rate resolution each
  pseudo-tenant is an exact ¼-thinning carrying the stream's real shape.
  Consequence, stated up front: within-stream pseudo-tenants are nearly
  perfectly demand-correlated — peaks land together, which is the *harder*
  packing regime for a joint controller (no anti-correlated slack to
  exploit) — and cross-stream diversity (conv vs code cycles,
  `FORECAST_AZURE.md`) is real. Precedent: `etl_burstgpt.py`'s documented
  no-session tenant approximation and `PSEUDO_TENANT.md`.
- **Windows: 72 × 3 h, back-to-back non-overlapping tiling** of the single
  segment (window *i* starts at `seg_start + i·3 h`, i = 0..71; 216 h is
  consumed exactly). 3 h × 10 s intervals = 1080 control steps per window.
  The window length differs from BurstGPT's 6 h because the trace is 9 days,
  not 335: 6 h windows would allow only 28 independent windows (~31% power).
  **Power at the BurstGPT-observed d_z = 0.43:** 0.43·√72 ≈ 3.65 against
  t₀.₀₀₅(71) ≈ 2.65 → ≈ 84%.
- **Rates:** real per-pseudo-tenant 600 s request counts, scaled by k from
  the same frozen formula (mean per-tenant work = 100 wu/s at chat = 20 wu),
  held piecewise-constant over 10 s intervals; Poisson arrival jitter drawn
  **once per window** and shared by every system (exact pairing).
- **Jitter seeds: 3000 + window index** (disjoint from both BurstGPT
  samples' 1000/2000 bases).
- **Systems: `jcac`, `jcac_v2`, `hpa`, `keda`, `firm`** (5 × 72 = 360 runs),
  identical tuned parameters (`tuned.yaml`), identical engine semantics,
  MEDIUM cluster limits, default tenant configs — everything imported from
  the committed `trace_matrix.py` machinery. `jcac_seasonal` is excluded by
  structure, not preference: a 3 h window cannot contain a daily cycle, so
  the within-run seasonal detector has nothing to lock onto (its Azure
  evidence lives in `FORECAST_AZURE.md`, where history spans the trace).
- **Pipeline smoke, sanctioned:** one discarded verification run of window 0
  with {jcac, hpa} to prove the adapter executes; its numbers are never
  read into any table. Crash retries only thereafter.

## §3 Hypothesis (confirmatory)

**HT-AZ:** paired by window, `jcac` shows composite J (cost/0.01/scored +
2·violation + 0.5·(1−Jain)) significantly below **each** of HPA, KEDA, FIRM
(paired t, p < 0.01). Cost and violation diffs reported alongside; a J win
accompanied by a violation regression at p < 0.01 and |d_z| ≥ 0.5 vs the
same baseline is **PASS-with-disclosure**, stated prominently (the same
trade rule as HT/HT2).

**Declared secondary (estimates, not gates):** per-stream stratification
(J/cost diffs over conv-dominant vs code-dominant contributions read as
window-level diffs, both streams present in every window); ET-AZ1 —
`jcac_v2` − `jcac` on J (Holt-vs-trend on a trace where neither stream
matched BurstGPT's forecast winner).

## §4 Stopping rule

One run of the 360-cell set (crash retries only), one analysis pass, either
verdict published in `RESULTS_TRACE_AZURE.md`. No constant in §2 changes
after the run starts. The 72-window tiling exhausts the trace: **there is no
second Azure sample.** If HT-AZ fails with a direction consistent with the
BurstGPT samples, the permanent reading is "direction consistent,
significance not reached on the second trace" — reported next to both
BurstGPT samples, never pooled with them (ground rules 3–4).
