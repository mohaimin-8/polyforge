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

**This campaign has NOT been re-run, and that is deliberate.**
`PREREG_TIER_RATIO_V2.md` covers the *price* readings only and says so
explicitly; re-running the work-unit reading at the corrected multipliers
would need its own pre-registered file. Until such a file exists, the
capacity-multiplier result here should be read as answering "what happens when
a large model is ~16.6x heavier", which the corrected measurement says this
hardware does not exhibit. The exposure is disclosed rather than closed.

For the price reading, the corrected corner was run and the headline result
survives: TR2-H1 PASS 5/5, TR2-H2 PASS 5/5 (`RESULTS_TIER_RATIO_V2.md`).
