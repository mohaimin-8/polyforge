# Pre-registration: tier-price ratio sensitivity rerun (Wave 2, closes DEFENSE_QA #15)

Registered 2026-07-16 (session 20). This file is committed and pushed
**before** any run of the experiment it registers; the push event is the
timestamp anchor (as for all prior preregs), and this pre-registration will
additionally be mirrored to OSF prospectively per DEFENSE_QA #17.

## Question

Every committed matrix prices model tiers at API-market ratios
(`TIER_COST_USD_PER_REQ` small:mid:large = 1:10:100). The Phase 6 GPU bench
measured GPU-seconds per request on one fixed card at **1 : 1.516 : 16.64**
(TIER_BENCH.md: mean ms 1241.9 / 1882.8 / 20665.1; the large row is a
disclosed CPU-offload upper bound). Does the headline cost result survive
when the whole economy — the world's bill *and* every controller's beliefs —
moves to the measured self-hosting corner?

Two readings exist. The *accounting* reading (decisions frozen, spend
re-priced by exact algebra) is BREAKEVEN_TIER.md — exploratory, completed
first, and it predicts survival (aggregate deltas below). This experiment is
the *decision* reading: controllers re-plan under the new prices. It is the
confirmatory closure named in DEFENSE_QA #15 and the thesis limitations.

## Design (frozen)

- Experiment: `eval/experiments/matrix_gpu_econ.yaml` — an exact mirror of
  the v1 headline `full.yaml` (6 systems × 5 workload classes × 4 tenant
  mixes × 3 cluster sizes × 5 reps = 1,800 sim runs, steps=120,
  `timeseries_reps: 1`) with one change:
  `economy: {tier_cost_small: 1.0e-4, tier_cost_mid: 1.516e-4, tier_cost_large: 1.664e-3}`.
- Price derivation, fixed here: small anchors at the published small-tier
  price (1.0e-4 USD/req); mid and large scale by the TIER_BENCH measured
  per-request GPU-seconds ratios (1882.8/1241.9 = 1.516, 20665.1/1241.9 =
  16.64, 4 significant digits). "none" stays 0.
- Mechanism: `model.set_economy` (this session, spot-checked bit-identical
  on the default path, 8/8 replays) moves the module constants that both
  `evaluate_step` (world scoring) and every controller's planning read at
  call time — the shared-model contract is preserved; nothing else changes.
- **No baseline retuning**, disclosed: hpa/keda/firm/vtc/static never choose
  tiers (their tier posture is fixed by their SystemSpec), so their decision
  problem is invariant to tier prices; gptcache's tier rule is
  demand/latency-driven, not price-driven. Their tuned replica knobs were
  optimized under the published economy — a caveat we disclose rather than
  hide; the static/overprovisioned envelope bounds what retuning could
  recover. jcac re-decides through the shared model like everything else.

## Hypotheses (frozen)

Pairing and tests identical to the headline analysis: per-cell pairing on
(workload, tenant_mix, cluster_size, rep) = 300 pairs per baseline,
one-sample t on paired differences, alpha 0.01.

- **TR-H1 (primary):** under the GPU-corner economy, jcac's
  `total_cost_usd` beats **each** of hpa, keda, firm, static, gptcache
  (mean paired diff < 0, p < 0.01, all five).
- **TR-H2 (secondary):** jcac's composite J (pre-registered form and
  weights) beats each of the five baselines (p < 0.01).
- **Stated prediction from the accounting reading** (BREAKEVEN_TIER.md, GPU
  corner, aggregate rep-0 deltas): hpa −40.2%, keda −45.9%, firm −43.1%,
  static −60.0%, gptcache −83.0%. Divergence between the rerun and these
  numbers is the measured decision-response effect and is reported either
  way.

## Outcome handling

- All outcomes published as measured in `RESULTS_TIER_RATIO.md`, including
  any TR-H1 failure; a failure bounds the cost claim's economy envelope and
  goes into the thesis limitations verbatim.
- Violation/fairness deltas are reported descriptively (the SLO story is
  already scoped by v2/v3 preregs; no new SLO hypothesis rides along here).

## Stopping rule

One execution of the 1,800-run matrix (`raw_sim_gpu_econ.duckdb`), harness
retry policy as configured (retries: 2), crash-resume allowed (a no-op
resume printing the validation summary is the official completion, Phase 7
precedent). The campaign is valid only at 1,800/1,800 valid runs. No
widening, no second attempt, no post-hoc grid of further price points in
this campaign; further economies (e.g. the combined self-host corner in
PREREG_HK_ADOPTION) require their own pre-registered files.
