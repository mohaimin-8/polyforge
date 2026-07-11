# Pre-registration — headline replay on real BurstGPT demand (cost restatement)

Dated 2026-07-12 (session 15). Committed **before** any replay run, per V2_README
ground rule 1. Completes the deferred half of v2 Phase 3a: the v1 headline
comparison, re-run with the *real* BurstGPT v2.0 demand shape driving the simulator
instead of the synthetic taxonomy classes. Own substrate, own results document
(`RESULTS_TRACE.md`); never mixed into any matrix table (ground rule 4).

## §1 What is being restated, and what is not

The v1 headline (RESULTS.md) says PolyForge beats every tuned baseline on the
composite objective J and on cost, at violation parity, on synthetic demand. The
one discountable ingredient is the demand generator. This experiment replaces it
with the real trace: **10,632,194 real Azure OpenAI/ChatGPT requests**
(`research/traces/out/burstgpt_real.csv.gz`, assembled reproducibly by
`fetch_burstgpt.py`). Not restated: live-cluster absolutes (Phase 7), cache
hit-rate headlines (Phase 3b), or any v2/v3 hypothesis.

## §2 Protocol (frozen)

**Demand construction.** The trace's real arrival process is far below the
calibrated model's operating point (mean ≈ 0.37 rps cluster-wide; everything fits
in one replica and all controllers tie trivially). The restatement therefore
preserves the real demand *shape* and pins the *magnitude* to the matrix's scale:

1. Bucket the normalized trace per tenant at **600 s** (the finest resolution the
   real arrival density supports without shot noise dominating).
2. Scale every rate by the constant **k** such that the mean per-tenant work over
   covered buckets equals **100 wu/s = one replica's capacity** — the same order
   as every v1 workload class (crud_bursty 56, ai_cacheable ~125 raw wu/s). k is
   computed by the script from this formula and printed; it is not a knob.
3. Expand each 600 s bucket into 60 control intervals (10 s each,
   `CONTROL_INTERVAL_S`) of piecewise-constant demand, then Poisson-resample per
   interval with the committed `simulate.jitter_buckets` — exactly the synthetic
   matrix's realism model (underlying rate curve + arrival noise), **jittered once
   per window and shared by every system** so pairing is exact.

**Windows.** 16 windows of 6 h (2,160 control steps each): 8 per contiguous
collection segment (segments as in `FORECAST_TRACE_REAL.md`; the 104-day gap is
missing data), window *i* of a segment starting at
`seg_start + i · (seg_len − 6 h)/7`, i = 0..7. Deterministic; no selection after
seeing outcomes. Windows are the replication unit (n = 16 pairs per comparison).

**Cluster & tenants.** `medium` limits (4096 MB cache, 48 replicas), the trace's
8 tenants, `simulate.default_configs` (SLO classes cycle premium/standard/
best-effort, 5 USD/h budgets, replica_max 10) — the committed defaults, not a new
mix. Engine semantics identical to the v1/v2/v3 headline: no transition costs, no
interference.

**Systems (frozen, tuned.yaml unchanged):** `jcac`, `jcac_v2`, `jcac_seasonal`,
`hpa`, `keda`, `firm` — 6 × 16 = 96 runs, seeds = 1000 + window index (FIRM takes
the same). `static`/`gptcache` omitted: posture fully determined, story already
told by the v2 iso-cost gate.

## §3 Hypothesis (confirmatory)

**HT (the restatement):** paired by window, `jcac` shows composite
J = cost_norm + 2·violation + 0.5·(1−Jain) — the objective every baseline was
tuned on — significantly below **each** of HPA, KEDA, FIRM (paired t, p < 0.01).
Cost and violation diffs are reported alongside; a J win bought by a violation
regression at p < 0.01 and |d_z| ≥ 0.5 vs the same baseline is reported as
**PASS-with-disclosure**, prominently.

**Declared exploratory:** ET1 — `jcac_v2` (Holt) vs `jcac` (trend) on real shape
(FORECAST_TRACE_REAL predicts Holt helps); ET2 — `jcac_seasonal` vs `jcac` (weak
real periodicity predicts ≈ tie); ET3 — per-segment breakdown.

## §4 Stopping rule

One run of the 96-cell set (crash retries only), one analysis pass, both verdicts
published as measured in `RESULTS_TRACE.md`. Constants in §2 (bucket size, k
formula, window count/placement, systems, seeds) may not change after the first
run starts; a code defect fix is documented, never re-tuned. The run-level
summary CSV is committed (`eval/results/trace_replay_runs.csv`) so every number
is recomputable without the 429 MB download.
