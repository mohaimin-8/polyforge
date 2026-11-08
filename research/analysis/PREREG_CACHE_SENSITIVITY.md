# Pre-registration: does the coordination result survive other cache-hit curves? (CS; 2026-11-09)

Registered 2026-11-09. Committed **before** any run of
`eval/experiments/matrix_cache_{measured,published,high}.yaml` (commit time is
the anchor; the commit stays local until the authors push the paper's
branch). Scorer: `analysis_cache_sensitivity.py`, committed with this file,
checked on a synthetic frame, not edited after the run. Tests use the frozen
`analysis_fair_j.py` rule.

## Why this exists

The simulator's semantic cache hits `h(c) = 0.85 c / (c + 256)` of a
workload's cacheable share for a cache of `c` MB. That curve is a modelling
assumption. `SEMANTIC_CACHE.md` measured the same saturating form on 200,000
real LMSYS-Chat-1M turns: `hmax = 0.285`, `K_half = 6,569` entries, about
19 MB, so the published curve is optimistic, and that record says adopting the
measured constants "would be a new pre-registered experiment". An external
audit of the paper (2026-11-09) asked whether the coordination result holds
under other cache curves. This campaign tests it.

## What was already seen (disclosed)

All records to date, including RV, TA and `COST_NORMALISATION.md`; in
particular JN-C1 (joint search beats decoupled search by 0.0066 J, inside the
±0.025 margin), JN-B1 and JN-B4 (ties with the cheapest-tier stack and capped
HPA) and WIDTH's mechanism. No run of this campaign has been made.

## Design

Three cache curves, one campaign each, set through the harness's economy
override (planner and plant share the curve):

| curve | `cache_hit_max` | `cache_half_mb` | basis |
|---|---:|---:|---|
| `measured` | 0.285 | 19 | `SEMANTIC_CACHE.md` fit on real LMSYS-Chat-1M |
| `published` | 0.85 | 256 | the paper's curve |
| `high` | 0.95 | 128 | an upper bound (84% at 1,024 MB) |

Each: the 60-point main matrix (5 workloads × 4 eight-tenant mixes × 3
cluster sizes), 120 steps, 5 reps, shared seeds, published plant otherwise.
Arms: `jcac_converged`, `jcac_decoupled`, `hpa_fair_capped`,
`layered_small_tuned`. The comparators keep their tuning from the published
curve (not re-tuned). 3 × 1,200 = 3,600 runs.

**Unit and score.** The design point (reps averaged), listwise complete.
J per run = cost / (scored steps × 8) / $0.01 + 2·v̄ + 0.5·(1 − Jain), with the
run's own scored steps (see `COST_NORMALISATION.md`).

## Tests

**Coordination (equivalence), one per curve:** CS-EQ[curve]:
`jcac_converged` − `jcac_decoupled` is EQUIVALENT if its 95% bootstrap CI
(10,000 resamples) lies inside ±0.025 J. Prediction: EQUIVALENT on all three.

**Practical comparisons (superiority), one Holm family of 6:**

| id | a | b | prediction |
|---|---|---|---|
| CS-H1[measured / published / high] | `jcac_converged` | `hpa_fair_capped` | FAIL ×3 |
| CS-H2[measured / published / high] | `jcac_converged` | `layered_small_tuned` | FAIL ×3 |

PASS = one-sided Wilcoxon (ΔJ < 0) at its Holm threshold AND the 95%
bootstrap CI entirely below 0.

**Descriptive (no test):** per-arm means of J, cost, violation, excess, Jain,
cache hit rate and AI shed share per curve; the coordination gain
`J(decoupled) − J(joint)` per curve with its CI; the share of best responses
where decoupled search would differ (`coordination_disagreement_share`).

## Stopping rule

Each campaign runs once to completion (`retries: 2`); design points missing a
rep for any arm are dropped listwise. No rerun after scoring.
