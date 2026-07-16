# Pre-registration: empirical cache-curve adoption rerun (Wave 2, closes DEFENSE_QA #16)

Registered 2026-07-16 (session 20). Committed and pushed **before** any run;
push event is the timestamp anchor; to be mirrored to OSF prospectively per
DEFENSE_QA #17.

## Question

The matrix's assumed cache curve h(c) = 0.85·c/(c+256MB) erred **for** the
in-matrix cache benefit: the real-LMSYS measurement (SEMANTIC_CACHE.md,
Phase 3b) fit h(K) = 0.285·K/(K+6,569 entries), ≈ 19 MB half-size at the
~3 KB/entry accounting documented there. The matrices deliberately kept the
frozen optimistic curve for bit-reproducibility, all cache-using systems
share it, and DEFENSE_QA #16 concedes the differential effect on *rankings*
is unquantified. This experiment quantifies it: the full headline matrix
rerun under the measured curve — pessimistic for every cache-carrying
system including ours.

## Design (frozen)

- Experiment: `eval/experiments/matrix_hk.yaml` — exact mirror of the v1
  headline `full.yaml` (6 systems × 5 × 4 × 3 × 5 = 1,800 sim runs) with
  `economy: {cache_hit_max: 0.285, cache_half_mb: 19.0}`.
- Constant derivation, fixed here: hmax = 0.285 and K_half = 6,569 entries
  are the committed Phase 3b fit; 19.0 MB is that file's committed MB
  equivalent. The curve applies to **all** systems identically (parity is a
  property of the world, not of a system).
- Same override mechanism and no-retuning disclosure as PREREG_TIER_RATIO.
  One behavioral consequence is *declared in advance*: rules gated on
  absolute hit-rate thresholds (e.g. a grow-cache-while-hit<0.7 posture)
  can never satisfy their gate under hmax = 0.285 and will saturate their
  cache axis; that is a fair consequence of the measured world, not a bug.

## Hypotheses (frozen)

Pairing/tests as the headline (300 pairs per baseline, paired t, alpha 0.01).

- **HK-H1 (primary):** under the empirical curve, jcac beats **each** of
  hpa, keda, firm, static, gptcache on composite J (p < 0.01).
- **HK-H2 (secondary):** jcac remains the cost winner against each baseline
  (paired total_cost_usd, p < 0.01).
- **Declared mechanism expectations** (directional, not hypotheses):
  realized cache hit rates drop for every cache-using system; the
  cache-centric baseline (gptcache) is devalued most; jcac may reallocate
  spend from cache toward replicas/tier — the reallocation itself is
  evidence the joint controller adapts to the world it is given.

## Exploratory arm (declared now, run only after both primary matrices)

`matrix_selfhost` = GPU-corner prices **and** empirical curve together
(the "self-hosted realistic" world), same 1,800-cell mirror. Exploratory:
its reading informs future-work text, not the headline. If it runs, it runs
once under this paragraph's declaration; its YAML must copy both economy
blocks verbatim.

## Outcome handling and stopping rule

As PREREG_TIER_RATIO: results to `RESULTS_HK_ADOPTION.md` as measured
(including any H1 failure — under a 3.4× weaker cache economy a narrowed or
lost cache-driven margin is a *finding*, stated verbatim); one execution of
`raw_sim_hk.duckdb`, valid only at 1,800/1,800; crash-resume allowed; no
widening.
