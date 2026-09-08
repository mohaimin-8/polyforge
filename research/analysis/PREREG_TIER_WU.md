# Pre-registration: tier-scaled work-unit rerun (Wave 5, closes the "a large model congests the pool for free" incoherence)

Registered 2026-07-18 (session 24). Committed and pushed **before** any
campaign run; the push event is the timestamp anchor, mirrored to
`OSF_REGISTRATION.md` prospectively per DEFENSE_QA #17.

## Question

The published model charges every AI request the same work units
regardless of tier, while its own tier bench measured per-request serving
time rising 1 : 1.516 : 16.64 across small/mid/large (`TIER_BENCH.md`).
Combined with miss latency being inflated by pool congestion, the model's
two economies are decoupled in cost but incoherently coupled in latency:
under the self-hosted reading a heavier model should consume
proportionally more serving capacity, and under the API reading it should
not see local congestion at all. The published matrices take the
self-hosted latency coupling but not its capacity consequence — a
free-capacity subsidy to every tier-upgrade posture. This experiment
makes the self-hosted reading capacity-coherent: an AI request on
mid/large consumes 1.516× / 16.64× the work units it consumes on small.
(The price half of the self-hosted reading was already closed by
PREREG_TIER_RATIO; this closes the capacity half.)

## Design (frozen)

- Experiment: `eval/experiments/matrix_tierwu.yaml` — exact mirror of the
  v1 headline `full.yaml` (1,800 sim runs) with
  `model_form: {wu_tier_mid: 1.516, wu_tier_large: 16.64}`.
- Constant derivation, fixed here: the factors are the committed
  TIER_BENCH.md mean-latency ratios (1882.8/1241.9, 20665.1/1241.9) on
  identical hardware and request shape, independently replicated within
  3% (`tier_bench_replication.csv`). Disclosed caveat carried verbatim:
  the `large` row is an offload-dominated **upper bound**, so 16.64× is
  the most pessimistic capacity reading for large-tier use; the
  qualitative claim ("heavier models consume materially more capacity")
  does not depend on the exact value. `none` keeps factor 1.0 (no model
  runs; the residual work is gateway overhead). CRUD kinds are
  unaffected. The factor applies to **all** systems identically — world
  and every controller's beliefs move together (all seven `work_units`
  call sites, including every baseline's ρ estimate, pass the acting
  tier).
- Mechanism, no-retuning disclosure, and bit-identity evidence as
  PREREG_LM_ADOPTION (same session, zero-drift spot-checks of committed
  matrices). One declared consequence: reactive tier-up rules
  (layered/gptcache) now buy latency relief that costs capacity, so a
  tier-up under load can *worsen* congestion — that is the measured
  world's trade-off, and observing controllers walk into it is part of
  what the campaign measures. Budget-infeasible corners where the lattice
  collapses to the shed-cost fallback are likewise fair outcomes.
- Only a mechanics smoke (non-campaign cell IDs, validity/timing only)
  preceded this registration; no campaign cell has been executed.

## Hypotheses (frozen)

Pairing/tests as the headline (300 per-cell pairs per baseline,
one-sample t, alpha 0.01), mechanized in `analysis_econ.py tierwu`.

- **TW-H1 (primary):** under tier-scaled work units, jcac beats **each**
  of hpa, keda, firm, static, gptcache on composite J (p < 0.01).
- **TW-H2 (secondary):** jcac remains the cost winner against each
  baseline (paired total_cost_usd, p < 0.01).
- **Declared mechanism expectations** (directional, not hypotheses):
  gptcache (reactive tier-up posture, 16% large steps in v1) and static
  (pinned mid, 1.516× on all AI work) are pressured most; jcac's
  small-heavy posture (91% small steps in v1) is least exposed, so the
  reruns should *widen* jcac's margins — stated in advance so a widened
  win cannot be read as a surprise favorable to us.

## Outcome handling and stopping rule

As PREREG_HK_ADOPTION: results to `RESULTS_TIER_WU.md` as measured
(including any failure, stated verbatim); one execution of
`raw_sim_tierwu.duckdb`, valid only at 1,800/1,800; crash-resume allowed;
no widening. The exploratory all-forms arm is declared in
PREREG_MIXTURE_P95.md and governed there.


---

## Superseding note (POST-RUN, session 44 — not a pre-run amendment)

This file and `RESULTS_TIER_WU.md` stand as registered and executed. The
work-unit multipliers declared here — mid 1.516x, large 16.64x, from
`TIER_BENCH.md` — share an input with `PREREG_TIER_RATIO.md`, and that input
was mis-measured: the `large` figure came from a 7B row that was CPU-offloaded
on a 16 GB card, so it measured the offload rather than the tier. Re-measured
on `GPU T4 x2` with **zero modules offloaded**, the serving-time ratios are
**1 : 1.475 : 1.518** (`research/calibration/tier_bench_t4.csv`).

**This campaign has NOT been re-run, and after further measurement that is a
positive decision rather than a deferral: a naive re-run at 1.518x would make
this model LESS accurate, not more.**

Work units are a *capacity* quantity. This file derives them from measured
serving time, which is a good proxy only when serving time is dominated by the
work the model actually does. Session 44 measured that it is not. Decomposing
the single-card TPOT (`tier_bench_1gpu.csv`):

| tier | fp16 weights | weights/bandwidth | measured TPOT | implied fixed overhead |
|---|---|---|---|---|
| small | 0.99 GB | 3.09 ms | 32.22 ms | **29.13 ms** |
| mid | 6.17 GB | 19.28 ms | 48.00 ms | **28.72 ms** |

The predicted TPOT gap from bandwidth alone is 16.19 ms against a measured
15.78 ms — **97.5% agreement** — and the residual is a **constant ~29 ms per
token that does not scale with model size**. That is the Python decode loop,
independently identified as the two-GPU concurrency ceiling in the same
session. A fixed per-token cost added to every tier alike **compresses all
ratios toward 1**.

So the three candidate multipliers for `large` are not equally good:

| multiplier | source | vs intrinsic 15.43x |
|---|---|---|
| **16.640x** | committed here (P100, CPU-offloaded) | **off by 1.21** |
| 1.518x | session 44 (T4 x2, transformers) | **off by 13.91** |
| 15.43x | weights read per token, the capacity-bound reading | — |

**The offload artifact happened to land within 8% of the physically correct
capacity ratio**, because offload slows a model for a reason that scales with
its weight size, whereas the transformers overhead flattens every tier alike.
The committed 16.64x for `large` is therefore close to right *for this
particular measurand*; the committed 1.516x for `mid` is the weaker figure
(intrinsic 6.26x), and it is weak in the conservative direction — it
under-charges mid-tier capacity, so it cannot manufacture the effect this
campaign found.

**What a correct re-run needs is a serving engine whose fixed overhead does
not dominate**, which is exactly what the B1 substrate now provides
(`PREREG_WAVE4_LIVE_PLANE.md` session-44 amendment: vLLM on a rented host).
Re-measuring the tier bench there yields ratios that approach the capacity
reading, and *those* are the multipliers a work-unit v2 should freeze. Until
that measurement exists, this campaign stands with the caveat it already
declared verbatim — that 16.64x is an upper bound — now sharpened: it is an
upper bound that is nearly correct for capacity, and the number that looked
like its correction is not.

For the price reading, the corrected corner was run and the headline result
survives: TR2-H1 PASS 5/5, TR2-H2 PASS 5/5 (`RESULTS_TIER_RATIO_V2.md`).
