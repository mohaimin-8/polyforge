# Pre-registration — does the simulator transfer once its plant is fitted to the live evidence? (WL-H3′)

Committed and pushed before any calibrated simulation is run. This file
freezes the plant-fit rule, the arms, the cells, the readings, the
falsifiers and the stopping rule. The fit script
(`research/calibration/live_plant_fit.py`), the fitted plant it produced
(`live_plant_b1prime.json`, `LIVE_PLANT_FIT.md`), the five experiment files
it emitted (`eval/experiments/wave4_sim_transfer_*.yaml`) and the scorer
(`analysis_wave4_sim_transfer.py`) are committed with it. Nothing here was
tuned to a simulation outcome: the fit reads only live evidence, and no
calibrated run existed when this was pushed.

## Why this experiment exists

Most of PolyForge's campaigns are simulated. `RESULTS_WAVE4_LIVE_PLANE.md`
(B1) measured the simulator's ordinal predictions reproducing live in **0 of
4 cells** on the composite objective and 1 of 4 on cost. The B1′ evidence
(`RESULTS_WAVE4_CALIBRATED.md`, 39 valid runs) names why: three of the
published plant's constants are not the live plant's.

1. A control-plane replica serves CRUD at **1.01 ms p95 with no congestion
   from 2 to 35 requests/s per replica**; the published model gives it
   100 work units/s and predicts 56–84 ms at zero load.
2. **AI requests never touch a control-plane replica** (k6 → gateway →
   vLLM); the published model charges 20–40 work units each, so it adds
   replicas to absorb AI load that the live plane never routes there.
3. The cache hits **0.95–0.97 at 64 MB** in the cacheable cells (0.30 in
   `tier_mixed`); the published curve gives 0.17 at 64 MB. And the live
   tiers are three sizes of one chat model — `small` is fastest *and*
   cheapest for every kind (262 ms vs 2156 ms for `large`) — where the
   published table makes `agent` degrade on `small` so that tier choice is
   non-trivial.

A simulator that disagrees with the live plant on these cannot be expected
to rank controllers as the live plant does. The question this registers is
whether, with those constants **fitted from the live evidence under a rule
frozen here**, the simulator's rankings reproduce the live ones — and
whether they do so better than the published plant's. A yes makes the
simulated campaigns readable as predictions of this plant; a no bounds what
they can claim.

## The fit (frozen; `live_plant_fit.py`)

Inputs: the B1′ evidence only (`wave4_calibrated_plane_evidence/runs/`,
`wave4_calibrated_plane_runs.csv`). Outputs: `live_plant_b1prime.json`.

- `replica_capacity_wu`: the highest window-mean CRUD work per replica
  seen in any valid run, divided by ρ_cal = 0.05 — the smallest capacity at
  which the flat p95 observed at that load implies at most 5% inflation. A
  lower bound: no live run reached congestion. Fitted: **1123** (published
  100), from `jcac-calibrated`/`crud_bursty` at 300.8 CRUD rps over 8.0
  replicas.
- `wu_ai_scale` = **0**: structural (the harness topology), not fitted.
- `crud_base_scale` per cell: median live CRUD p95 over the published
  zero-load prediction (`crud_base_ms × P95_FACTOR`). Fitted: 0.0120
  (`ai_cacheable`), 0.0120 (`tier_mixed`), 0.0358 (`crud_bursty`), 0.0144
  (`joint_stress`).
- Cache, per cell with AI traffic: every AI kind cacheable
  (`cacheable_uniform`), `cache_hit_max` = the `tier-only` arm's mean
  live hit rate (its cache is pinned at the chart default, so this is the
  plant's ceiling for the cell's prompt pool, not a planner's choice),
  `cache_half_mb` = 4 so the lattice's 64 MB level sits at 94% of the
  ceiling. Fitted ceilings: 0.962, 0.295, 0.955; `crud_bursty` has no AI
  traffic and keeps the published cache.
- `tier_latency_ms`, flat across kinds: `small` and `large` from the knob
  preflight's sequential probes averaged over the valid runs (262 / 2156
  ms); `mid` is **not measured live** and is the geometric mean (751 ms),
  declared as such.

Everything else — the objective, the lattice, the controllers, the tuned
parameters, the traces, the seeds, `SLO_BASE_MS`, the prices — is the
published simulator. Overrides enter through `model_form` / `economy`,
which reset to the published constants before every run and tag every
run id, so no calibrated run can collide with a published one and every
committed campaign still replays bit-identically (reproduction gate run
before this push).

## Arms, cells, design (frozen)

Five arms, the B1′ set: `jcac-calibrated`, `jcac`, `replica-only`,
`cache-only`, `tier-only`. Four cells, the B1′ set. `uniform`, `small`, 30
steps, **3 reps** with the harness's deterministic seeds (as
`wave4_sim_ref.yaml`). Two substrates:

- **Fitted plant**: `wave4_sim_transfer_{ai_cacheable,tier_mixed,crud_bursty,joint_stress}.yaml`
  (one file per cell because the cache ceiling is per cell), 15 runs each.
- **Published plant, five arms** (the control): `wave4_sim_transfer_control.yaml`,
  60 runs. B1's `wave4_sim_ref` had four arms; the control puts the fifth
  under the published constants so the two substrates are compared on the
  same arms.

Live reference: B1′'s valid runs (`wave4_calibrated_plane_runs.csv`), mean
over its two reps per arm and cell.

## Readings (frozen)

Per cell, an arm's **cost** is its mean `total_cost_usd` over reps; the
**cost winner** is the arm with the lowest mean. Ordinal only: no absolute
crosses the substrate boundary, and the live `total_cost_usd` carries the
WL-H2 preflight's spend identically in every arm (disclosed in B1′), which
cannot change a ranking.

- **WL-H3′ (primary).** The fitted simulator's cost winner equals the live
  cost winner in **at least 3 of 4 cells**, AND in more cells than the
  published-plant control does. **Falsifier:** fewer than 3 agreements, or
  no improvement over the control, is a FAIL, reported as such.
- **WL-H3′b (secondary).** Spearman rank correlation between the fitted
  simulator's and the live cost ranking of the five arms, per cell,
  reported beside the control's; the reading is the count of cells with
  ρ ≥ 0.7. No pass/fail.
- **WL-H3′c (secondary, the correction's transfer).** In every cell the
  fitted simulator ranks `jcac-calibrated` below `jcac` on cost, as live
  does (WL-H5). Pass/fail per cell.
- **Descriptive.** The same three readings on the composite objective J
  (as B1's WL-H3 defined it), and the replica trajectory the fitted
  simulator gives each arm beside the live one (`CHURN_WAVE4_CALIBRATED.md`).

## What voids a run, and the stopping rule

A sim run is void if the harness marks it not-valid. The five experiments
run once; nothing is added, no constant, rule, arm, cell or reading changes
after this push. If the fit script is found to have a defect, the defect is
disclosed in an amendment appended after this text, the experiments are
re-emitted and re-run from zero, and both outcomes are reported.

## Provenance

Raw DuckDBs `eval/results/wave4_sim_transfer_*.duckdb` (gitignored,
Zenodo); committed exports `wave4_sim_transfer_runs.csv`. Record:
`RESULTS_WAVE4_SIM_TRANSFER.md`, generated by the scorer committed with
this file and registered in `scripts/reproduce.py` once the runs exist.
