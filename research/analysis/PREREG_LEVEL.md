# PREREG_LEVEL — the level-playing-field campaign

**Status:** registered 2026-11-09, before any run of the five experiments
below. Committed and pushed to the public branch `level-campaign` (paper drafts
excluded) before the first run; the push time and the GitHub Actions run it
creates are the timestamp anchor. The scorer `research/analysis/analysis_level.py`
is frozen with this file. Nothing below may change after the first run; an
amendment needs a dated note *after* this text, before the run it affects.

## 1. Why

The final audit of 2026-11-09 (`Thesis Project/PolyForge_Final_Audit_2026-11-09.md`,
scripts in `PolyForge_Audit_2026-11-09_scripts/`) found leaks on both sides of
every registered comparison between the joint controller and the per-layer
baselines:

| leak | who it flattered | evidence |
|---|---|---|
| Tier "none" costs $0 and J prices a refused request only through a capped, traffic-weighted violation; billing refused requests at the small-tier price erased every dollar advantage of `jcac_converged` | the joint controller | audit `a5` |
| Only the joint controller could refuse AI calls or keep a budget; BM's filter trims replicas only and left the comparators over budget (3.0% of tenant-steps against 0.9%) | the joint controller | `RESULTS_BUDGET_MATCHED.md` |
| The cheapest-tier stack ran about 48 replicas against the small cluster's cap of 24 on every step | the comparator | audit `verify_caps.py`, `a4`, `p8` |
| The published agent latency row (small 6,000 > large 3,500 > mid 2,500 ms) is unmeasured; with a monotone row the directional coordination gain vanished | the joint controller (RQ2 direction) | audit `p3`, `a8` |
| The never-shed ablation's fallback cut replicas and cache while serving over budget | the comparator | audit `p7` |
| The published switching penalty ratchets replicas up (11.3 increases vs 0.9 decreases per run) | the comparator | audit `p5`; the B1′ diagnosis |

This campaign removes them together and tests the joint controller once more.

## 2. Design

**Objective (registered, primary).** J_L = J + an outage price: every refused
AI request (a cache miss on a tier-"none" tenant-step) is charged the small
tier's price, $1e-4,

    J_L = (cost + shed x 1e-4) / (steps x 8) / 0.01 + 2 v + 0.5 (1 - Jain),

each run's own scored steps; `shed` is recovered exactly from the stored
metrics (`analysis_level.shed_requests`, SHED_POOLED's identity). Refusing
service therefore never saves money relative to the cheapest tier. Reported
beside it, descriptively: the published J, and J_L with the mid-tier price.

**Arms (every arm obeys the cluster caps).**

| arm | role |
|---|---|
| `jcac_level` | **treatment**: the anchored convergent joint controller, which (a) pays the small-tier price for refused requests in its own objective (`shed_price_tier="small"`; objective only — the budget filter tests real spend), (b) serves on the cheapest affordable tier before it sheds (`degrade_gracefully`), (c) uses the documented half-replica switching penalty (`SWITCH_PENALTY_HALF_REPLICA`, the B1′ correction), (d) forecasts with damped Holt |
| `jcac_level_decoupled` | RQ2 ablation: the treatment with each knob searched on its own |
| `jcac_converged` | the published controller (reference) |
| `layered_small_level_gated` | the cheapest-tier stack inside the caps whose gateway refuses AI model calls in any interval its tenant's budget cannot pay for (`baselines.BudgetGated`), replica layer re-tuned |
| `layered_small_level_capped` | the cheapest-tier stack inside the caps, re-tuned |
| `hpa_level_capped` | capped HPA (512 MB cache), re-tuned |
| `keda_level_capped` | capped KEDA (512 MB cache), re-tuned |
| `static_share` | equal cluster share, cheapest tier, never adapts |

**Treatment selection (development data, disclosed).** Four variants, each a
fix of a defect in §1, were declared in `eval/baselines/select_level_treatment.py`
and scored under J_L on `tune.py`'s slice and seeds (the comparators' tuning
data, disjoint from every experiment's seeds). The pre-declared rule (lowest
mean over the three sizes) chose V3:

| variant | small | medium | large | mean |
|---|---:|---:|---:|---:|
| V0 published | 0.60127 | 0.48459 | 0.44344 | 0.50977 |
| V1 priced outage + graceful degradation | 0.58991 | 0.47488 | 0.42813 | 0.49764 |
| V2 = V1 + half-replica penalty | 0.59280 | 0.51070 | 0.47537 | 0.52629 |
| **V3 = V2 + Holt** | 0.57305 | 0.47212 | 0.43114 | **0.49210** |

**Comparator tuning (frozen, committed with this file).** `tune_arms.py --level`
tuned each comparator per cluster size AS RUN under J_L on the same slice
(merged into `eval/baselines/tuned_arms.yaml`; grids in `grids_arms/`; no
optimum on a grid edge). Best slice J_L, seen before registration:

| arm | small | medium | large | value (small / medium / large) |
|---|---:|---:|---:|---|
| `layered_small_level_capped` | 0.53388 | 0.43477 | 0.43507 | target_rho 0.1 / 0.05 / 0.1 |
| `layered_small_level_gated` | 0.53483 | 0.43820 | 0.44043 | target_rho 0.1 / 0.05 / 0.1 |
| `hpa_level_capped` | 0.53269 | 0.43261 | 0.45389 | target_rho 0.1 / 0.05 / 0.075 |
| `keda_level_capped` | 0.53108 | 0.45088 | 0.47386 | rps_per_replica 0.5 / 0.25 / 0.15 |

On this development slice the treatment is worse than every comparator on the
small and medium clusters and level with or better than them on the large one.
The comparators were tuned on it, so that comparison favours them; the
campaign decides on fresh seeds.

**Substrates (five experiments, shared seeds within each; the run identity
includes the experiment name, so every seed is fresh).**

| key | experiment | substrate | runs |
|---|---|---|---:|
| LV | `matrix_level` | main matrix (5 workloads x 4 mixes x 3 sizes), published plant, instant actuation — **primary** | 2,400 |
| MO | `matrix_level_mono` | main matrix, robustness plant: agent row 937.5 / 2,500 / 6,250 ms (agent = 3.125 x chat per tier, keeping the published mid value) | 2,400 |
| LG | `matrix_level_lag` | main matrix, replicas one interval late and grown caches half-warm (as ACTUATION) | 2,400 |
| HO | `matrix_level_heldout` | the two HELDOUT Markov-modulated workloads | 960 |
| TR | `matrix_level_trace` | TA's 96 real BurstGPT windows (`jcac_converged` omitted: 183 s per window, TA scored it) | 672 |

120 steps per run (2,159 on TR), 5 replications (1 on TR). Comparators are not
re-tuned for MO, LG, HO or TR.

## 3. Tests (one Holm family of 11, alpha 0.05)

Unit: the design point (replications averaged) or the window; listwise complete
over every arm of the experiment. PASS = one-sided Wilcoxon (treatment better)
at its Holm threshold AND the 95% bootstrap CI of the mean ΔJ_L entirely below
0 (10,000 resamples, seed 20260926) — the FAIR_J rule, imported.

| id | experiment | treatment vs | prediction |
|---|---|---|---|
| LV-B1 | LV | `layered_small_level_gated` | **FAIL** (the slice puts the stack ahead on small and medium) |
| LV-B2 | LV | `hpa_level_capped` | FAIL |
| LV-B3 | LV | `keda_level_capped` | FAIL |
| LV-B4 | LV | `layered_small_level_capped` | FAIL |
| LV-B5 | LV | `static_share` | PASS |
| LV-P1 | LV | `jcac_converged` | PASS (the fixes help the controller under J_L) |
| LV-R1 | MO | `layered_small_level_gated` | FAIL |
| LV-L1 | LG | `layered_small_level_gated` | FAIL |
| LV-L2 | LG | `hpa_level_capped` | PASS (the ACTUATION pattern: a horizon helps under lag) |
| LV-O1 | HO | `layered_small_level_gated` | FAIL |
| LV-T1 | TR | `layered_small_level_gated` | FAIL |

**Coordination (registered readings, no p-value).** LV-C[key] for each
experiment: treatment − `jcac_level_decoupled` on J_L, EQUIVALENT when the 95%
bootstrap CI lies inside **±0.01** (tighter than JOINTNESS's ±0.025, which is
reported beside it for continuity). Prediction: EQUIVALENT in all five.

**Descriptive (no verdict).** Per-arm means of J_L, J, J_L at the mid price,
cost, violation, excess, Jain, shed share (steps and requests), budget overrun
and cost per served request; per-metric deltas and the excess non-inferiority
reading (+0.05) for every tested comparison; per cluster size, per workload
and AI-only cuts on LV and LG.

## 4. What the outcomes mean (fixed now)

- **LV-B1 PASS**: on the periodic matrix, at a level field, the joint
  controller beats the strongest per-layer stack; the earlier RQ1 null is
  overturned there (scope: this plant). **FAIL**: the RQ1 null stands at a
  level field.
- **LV-L2 PASS and LV-L1 PASS**: the planning horizon pays under actuation lag
  against both reactive control and the strongest stack.
- **LV-O1 / LV-T1**: whether any positive result generalises beyond the
  periodic matrix; a FAIL bounds every LV PASS to it.
- **LV-R1**: whether the primary result depends on the unmeasured agent row.
- **LV-P1**: whether fixing the audited defects improves the controller under
  an outage-priced objective.
- **LV-C**: whether joint search adds anything beyond per-knob search once the
  outage is priced; EQUIVALENT keeps the paper's mechanism result.

## 5. Rules

- No arm, grid, objective, scorer or substrate changes after the first run.
- The harness retries a failed run up to twice; a run still failing is
  reported, and its design point drops out listwise for every arm.
- Every verdict is reported as scored, PASS or FAIL, in the direction it lands.
- No campaign is re-run, widened or re-scored under another rule. A defect
  found after the run is answered by a new registration beside this record.

## Post-run note (2026-11-10, after the runs, before the record was committed)

Appended after the registered text, which is unchanged.

- **Timing.** Protocol and scorer committed as 039a153 (2026-11-09 16:50:00 UTC)
  and pushed to the public branch `level-campaign` at 16:50:45 UTC (GitHub
  Actions run 42658844968 created 16:50:48 UTC). The runner started at
  16:51:00 UTC; the first run completed at 16:51:06 UTC. All five experiments
  completed (8,832 of 8,832 runs valid; every harness audit `ok`).
- **Two loader/exporter fixes, no number changed.** (1) `eval/scripts/export_metrics_csv.py`
  did not export the three request-accounting columns the frozen objective
  J_L needs; it now exports them under its existing rule (only a column that
  holds data), so every older export stays byte-identical. (2) The frozen
  scorer's CSV loader selected an all-empty reporting column
  (`coordination_disagreement_share`, not tracked in LEVEL) that the exporter
  omits; it now reindexes, giving NaN exactly as the DuckDB path does. The
  record built from the DuckDBs and the record rebuilt from the committed
  CSV exports alone are byte-identical (sha256 4cdb9c23...0253). No test,
  threshold, objective, arm or computed number changed.
