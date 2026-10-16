# Pre-registration: joint control when the tier knob trades answer quality (QL; 2026-10-16)

Registered 2026-10-16. Committed and pushed **before** any run of
`eval/experiments/matrix_quality.yaml`; the push event is the timestamp
anchor, checked by `scripts/check_prereg_timing.py`. Scorer:
`analysis_quality.py`, committed with this file, tested on synthetic matrices
(`test_quality.py`), not edited after the run. It applies the PREREG_FAIR_J
rule with the frozen scorer's own functions.

## Why this exists

J prices cost, lateness and fairness, never what an answer is worth. A final
review (2026-10-16) named this the first construct objection a reviewer will
raise: with no quality term the tier knob collapses to "the cheapest tier
that meets latency", a static cheapest-tier configuration comes within a few
percent of the joint controller, and "per-layer optima do not compose" is
tested on a problem in which the tier layer has nothing to trade. This
campaign asks the composition question on the problem with the trade in it.

## The quality model (frozen; `research/jcac_sim/model.py`)

- **Per-tier quality** `TIER_QUALITY`: for the instruction-tuned models the
  live tiers run (small = Qwen2.5-0.5B, mid = 3B, large = 7B), the mean of
  the 11 benchmarks the Qwen2.5 Technical Report (arXiv 2412.15115, Tables
  8–10) reports for all three — MMLU-Pro, MMLU-redux, LiveBench 0831, GPQA,
  MATH, GSM8K, HumanEval, MBPP, MultiPL-E, LiveCodeBench, IFEval — divided by
  100: 0.2836 / 0.5484 / 0.6413. Shedding (`none`) is 0.
- **Shortfall** of a tier: 1 − quality / quality(large) → 0.5577 / 0.1449 / 0
  / 1 (none).
- **Cache hits** return a stored answer to a similar prompt. They keep
  `CACHE_HIT_RELATIVE_PRECISION` = 0.313 / 0.338 = 0.926 of the serving
  tier's quality: `CACHE_PRECISION.md`'s precision at the operating threshold
  over its stated stochasticity ceiling, the relative reading that record
  says to cite. A hit while AI is shed is taken as written by `small`.
- **Per-step quality loss**: the traffic-weighted shortfall of the step's AI
  answers, weighted by the AI share of the tenant's traffic, exactly as
  `violation` is. Always recorded (`mean_quality_loss`); priced only here.

## The objective and the economy (frozen)

- **J_q = J + 2.0 × mean quality loss.** The weight equals J's lateness
  weight: a fully lost answer counts as a fully late one.
- **Economy:** the measured GPU price ladder of PREREG_TIER_RATIO_V2
  (`matrix_gpu_econ_v2`): $1.0e-4 / 1.475e-4 / 1.518e-4 per request on
  small / mid / large — the self-hosted deployment PolyForge targets. Under
  the published API-like ladder (1 : 10 : 100) a large-tier step costs about
  85 times a small one and quality cannot move any decision; that regime is
  `RESULTS_FAIR_J` / `RESULTS_JOINTNESS`'s.

## What was already seen (disclosed)

This protocol is not blind. Before it was written, on `tune.py`'s tuning slice
(3 workloads x 2 mixes x 2 reps, seeds disjoint from every campaign), with
J_q and the GPU economy:

| arm (slice) | small | medium | large |
|---|---:|---:|---:|
| treatment as registered (`jcac_quality`, no budget filter) | 0.676 | 0.531 | 0.480 |
| the same, decoupled search | 0.654 | 0.531 | 0.483 |
| the treatment WITH its budget filter | 0.715 | 0.585 | 0.538 |
| the quality-blind published treatment | 0.900 | 0.783 | 0.769 |
| layered stack + quality router, inside the caps | 0.615 | 0.504 | 0.502 |
| layered stack + quality router, NOT capped | 0.488 | 0.495 | 0.502 |
| capped HPA on its tuned tier (large) | 0.587 | 0.509 | 0.534 |
| equal share on its tuned tier (large) | 0.665 | 0.527 | 0.557 |

Two design decisions follow from it and are disclosed as such: the treatment
and its search variants run WITHOUT the per-tenant budget filter, because no
comparator carries one and the filter alone cost the treatment 0.04–0.06 J_q
on the slice; and the layered comparator runs inside the cluster caps, as the
treatment does (the uncapped version is not registered). Also seen: the
records of JOINTNESS's design (none of its results — it was running).

No run of any arm on this campaign's seeds exists.

## Design (frozen)

`eval/experiments/matrix_quality.yaml`: simulator, published model forms, the
GPU economy above, 120 steps, 5 reps, `shared_seeds: true`, a new experiment
name (fresh seeds), the headline cells (60 design points). 8 arms, 2,400 runs.

| arm | role |
|---|---|
| `jcac_quality` | **treatment**: `jcac_converged` planning on J_q, no budget filter |
| `jcac_quality_decoupled` | QL-C1: the same, knobs searched independently and combined |
| `jcac_quality_knobwise` | QL-C2: the same, one knob at a time to a fixed point |
| `layered_quality_capped` | QL-B1: layered stack whose tier layer minimises one step's cost/0.01 + 2·violation + 2·quality loss (J_q's own weights, layer-local), inside the caps |
| `hpa_quality_capped` | QL-B2: capped `hpa_fair` on a fixed tier |
| `static_share_quality` | QL-B3: equal cluster share on a fixed tier |
| `jcac_quality_budget` | descriptive: the treatment WITH its budget filter |
| `jcac_converged` | descriptive: the quality-blind published treatment |

**Tuning (frozen; committed with this file).** `tune_arms.py --quality` tuned
the comparators AS RUN, per cluster size, on the tuning slice under the GPU
economy and J_q, merging into `tuned_arms.yaml`; no target_rho optimum is on
its grid edge:

| arm | small | medium | large |
|---|---|---|---|
| `layered_quality_capped` | ρ = 0.4 | ρ = 0.05 | ρ = 0.1 |
| `hpa_quality_capped` | ρ = 0.4, large | ρ = 0.05, large | ρ = 0.1, large |
| `static_share_quality` | large | large | large |

## Tests (frozen)

- The PREREG_FAIR_J rule on J_q: design point as the unit, listwise
  completeness; a win needs the one-sided Wilcoxon p at or below its Holm
  threshold AND the 95% bootstrap CI of the mean ΔJ_q (10,000 design-point
  resamples, seed 20260926) entirely below 0.
- **Confirmatory, QL-C1, QL-C2, QL-B1..B3:** the treatment against each. One
  Holm family of 5, α = 0.05.
- **Coordination equivalence (QL-C1-EQ, QL-C2-EQ):** EQUIVALENT when the 95%
  bootstrap CI of the mean ΔJ_q lies strictly inside ±0.025.
- **SLO guard:** `mean_excess` non-inferiority (one-sided 95% bound < 0.05).
- **Direction (registered reading):** a CI entirely above 0 is "comparator
  better"; a FAIL is never called a loss unless that holds.
- **Descriptive:** per-arm means and guards; per-metric differences (cost,
  violation, excess, quality loss, Jain); the budgeted and the quality-blind
  treatment against the treatment; per cluster size; per workload; the AI
  workloads alone.

## Predictions

From the slice above.
- **QL-C1, QL-C2: FAIL, and EQUIVALENT** — coordination still buys nothing
  measurable.
- **QL-B1 (capped quality-layered stack): FAIL, "comparator better"** on
  small and medium, "treatment better" on large.
- **QL-B2 (capped HPA): FAIL**, same size split.
- **QL-B3 (static share): FAIL**, low confidence; "treatment better" on
  large only.

## What each outcome means (committed before the run)

- **QL-C1/C2 FAIL and EQUIVALENT:** even when the tier knob trades quality,
  coordinating one tenant's knobs buys nothing measurable on this plant;
  combined with `RESULTS_JOINTNESS`, "per-layer optima do not compose" is not
  supported on either objective, and no jointness claim is made.
- **QL-C1 or QL-C2 PASS:** with quality priced, joint search beats that
  search by the quoted margin — the first registered evidence that the knobs
  must be searched together, scoped to this objective and economy.
- **QL-B1..B3 PASS:** the joint controller beats that comparator on J_q;
  **FAIL with "comparator better":** the comparator is better, and that is
  stated, with the size split.
- The per-size split decides where any claim may be scoped.

## Stopping rule

As `PREREG_FAIR_J`: one sitting; no re-run, widening, re-tuning or change of
arms after the first run; two transient-error retries; a failing run is
recorded and its design point dropped for every arm; a scorer defect found
after the run is fixed in a new scorer and a new record. `tuned_arms.yaml` is
not regenerated after the first run.
