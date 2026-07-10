# Pre-registration — PolyForge v2 evaluation

**Committed 2026-07-11, before any v2 matrix run.** This document freezes the v2
system configuration, experiment design, metrics, hypotheses, gate definition, and
stopping rule per `docs/V2_README.md` ground rule 1. Any deviation from this document
is reported as exploratory, not confirmatory, and requires a new dated pre-registration.
v1 artifacts (`RESULTS.md`, `eval/results/raw_sim.duckdb`, figures) are immutable; the
v1 gate FAIL paragraph in `RESULTS.md` stays verbatim.

## 1. System under test: `jcac_v2`

The v1 `jcac` controller with the two Tier-2 mechanisms that each independently helped
in the v1 ablations, now enabled together:

```yaml
controller: jcac
params:
  forecast_method: "holt"     # damped Holt smoothing (v1 ablation: jcac_holt)
  adaptive_capacity: true     # realized-vs-projected self-calibration (v1: jcac_adaptive)
```

These are the **complete** effective parameters: `eval/baselines/tuned.yaml` has no
`jcac` entry, so the harness merge (`sim_backend.py::run` — `tuned_params()` then
`spec.params`) passes exactly these two keys to `JCACController`
(`research/jcac_sim/controller.py`). Both are independent constructor arguments; no
code change to the controller is made or needed. No parameter of `jcac_v2` may be
changed after this commit without a new pre-registration.

Registry entry: `eval/harness/systems.py::SYSTEMS["jcac_v2"]` (added in the same
commit as this document; no change to any existing SystemSpec).

## 2. Experiment: v2 headline matrix

`eval/experiments/matrix_v2.yaml`, identical to `eval/experiments/full.yaml` except:

- `systems: [jcac, hpa, keda, firm, static, gptcache, jcac_v2]` — the v1 seven-way
  blocked design becomes 7 systems × 5 workloads × 4 tenant mixes × 3 cluster sizes
  × 5 reps = **2,100 runs** over the same 300 (workload, mix, cluster, rep) cells.
- `output: eval/results/raw_sim_v2.duckdb` — a new database; the v1 raw DB is never
  reopened for writing.
- All else unchanged: `backend: sim`, `steps: 120`, `reps: 5`, `retries: 2`,
  `timeseries_reps: 1`. Seeds use the runner's existing sha256 derivation; nothing
  about seeding is chosen post hoc.

## 3. Metrics (unchanged from v1)

Per run: cost (USD/run), SLO violation (p95-based mean violation), violation
incidence, Jain fairness, cache hit rate; composite
J = 1·cost_norm + 2·mean_violation + 0.5·(1−Jain) — the same objective every
baseline was tuned on (`eval/baselines/TUNING.md`). All tests are paired by matrix
cell (one-sample t on per-cell differences, Cohen's d_z), with pooled unpaired
columns reported alongside, exactly as in v1.

## 4. Confirmatory hypotheses (Phase 1 acceptance)

- **H1 (SLO segment)**: `jcac_v2` mean SLO violation is below **HPA** and below
  **KEDA**, paired p < 0.01 each (d_z reported), at equal-or-lower cost.
  "Equal-or-lower cost" means: the paired point estimate of
  cost(`jcac_v2`) − cost(baseline) is ≤ 0; if it is > 0, H1 fails — no
  significance escape hatch on the cost side.
- **H2 (no self-harm)**: `jcac_v2` vs v1 `jcac`, paired per cell across all five
  metrics. Any significant regression (p < 0.01, |d_z| ≥ 0.5) on any metric is
  reported prominently in `RESULTS_V2.md`; if SLO violation or cost regresses at
  that threshold, `jcac_v2` is not promoted to headline and v1 numbers stand.

Analysis is emitted by an extended `run_analysis.py` into
`research/analysis/RESULTS_V2.md`; `RESULTS.md` is not touched.

## 5. Gate v2 (evaluated in Phase 2, defined now)

Same thresholds as the v1 gate — wins on **≥ 3 of 5 metrics at p < 0.01 with
|d_z| ≥ 0.5, paired by cell, against every baseline** — with the baseline set made
iso-cost so no baseline wins a metric purely by outspending:

- `hpa`, `keda`, `firm`: unchanged, tuned per `TUNING.md` (their spend is already
  comparable; re-tuning them for v2 is prohibited).
- `static_isocost`: static provisioning whose expected cost per matrix cell equals
  `jcac_v2`'s realized mean cost in that cell (mean over the cell's reps from the
  Phase 1 matrix), replica count rounded **up** — the baseline gets at least
  PolyForge's budget; rounding is strict against PolyForge.
- `cache_isocost`: fixed cache sized so its cache spend per cell is at least
  `jcac_v2`'s realized mean cache spend in that cell, same ceiling rule, HPA-tuned
  replicas (`target_rho: 0.3`), LRU eviction economics like every caching baseline.

Gate v2 is reported **alongside** the v1 gate, never instead of it. The PolyForge
system evaluated by gate v2 is `jcac_v2`.

## 6. Stopping rule

The 2,100-run matrix is run **once**, with the existing retry/exclusion machinery
(`retries: 2`). If runs remain invalid after retries, exclusions are documented in
`RESULTS_V2.md` and analysis proceeds; the matrix is not re-run to chase
significance. `run_analysis.py` is run once over the completed database and its
output is reported whatever it says. A failed H1 is published as a negative result;
the only permitted follow-up is a new, dated pre-registration for a differently
parameterized system, labeled exploratory.

## 7. Reporting rules (restated from V2_README ground rules)

- Every "up to" number appears next to its full-matrix mean.
- No table mixes substrates: our numbers vs published numbers are presented as
  "their comparison shape, reproduced on our substrate," never as head-to-head cells.
