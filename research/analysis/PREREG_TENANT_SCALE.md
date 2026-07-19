# Pre-registration — end-to-end tenant-scale slice (32 and 64 tenants)

Registered 2026-07-19 (session 27). Committed and pushed **before** any run;
the push is the timestamp anchor; OSF mirror prospective. DEFENSE_QA #13
("eight tenants is not multi-tenancy at scale") was answered in two parts:
the planner-latency lever (`PLANNER_SCALING.md`, `PLANNER_CELLS*.md`, to
1024 tenants) and the honest concession that the **end-to-end** matrix J is
measured at 8 tenants only. This campaign closes the end-to-end residual at
32 and 64 tenants: same per-tenant demand classes, same budgets, same
per-tenant capacity regime — only the portfolio width grows.

## §1 World (frozen; additive definitions committed with this push)

- **Mixes** (`eval/harness/workloads.py`): `uniform32` (32× the standard
  slot), `whale32` (the whale composition ×4: 4 whales + 12 standard + 16
  best-effort minnows, ratio preserved), `uniform64` (64× standard).
  Per-tenant demand/budget/SLO are byte-identical to the 8-tenant slots.
- **Cluster sizes:** `medium4x` (cache 16384 MB, 192 replicas) and
  `medium8x` (32768 MB, 384 replicas) — medium's caps scaled linearly with
  the 4×/8× population; the per-tenant `replica_max` stays 10, so the
  per-tenant regime is the matrix's and only the packing width changes.
- **Cells:** 32-tenant experiment = {uniform32, whale32} × medium4x;
  64-tenant experiment = {uniform64} × medium8x. Workload classes
  `crud_bursty` and `ai_cacheable` (the Phase-7 pair: one CRUD-heavy
  bursty, one AI-heavy cacheable). 120 steps, 5 reps.
- **Systems:** `jcac_anchored` (the quotable controller), `hpa`, `keda`,
  `concurrency` (the three tuned reactive arms, including the 2026-stack
  arm if its tuning committed under PREREG_CONCURRENCY; all read
  `tuned.yaml`). 4 systems.
- **Runs:** 32T: 4×2×2×5 = 80; 64T: 4×2×1×5 = 40; **120 total**
  (`matrix_scale32.yaml`, `matrix_scale64.yaml`).
- **Planning stays monolithic** in the sim controller; per-run wall time is
  recorded by the harness. The deployment-scale answer (hash-partitioned
  planning cells) is already measured in `PLANNER_CELLS_DEALIAS.md`; this
  campaign measures decision quality at widths where monolithic planning
  is still deadline-feasible.

## §2 Hypotheses

- **TS-H1a (confirmatory, 32 tenants):** `jcac_anchored` beats **each** of
  hpa/keda/concurrency on composite J (matched-cells paired t over the 20
  32T cells per pair, p < 0.01, mean diff < 0). The standing
  violation-disclosure rule applies (regression at p < 0.01, |d_z| ≥ 0.5
  → PASS-with-disclosure).
- **TS-H1b (confirmatory, 64 tenants, large-effects power only):** the
  same reading over the 10 64T cells per pair. n = 10 gives ~85% power at
  |d_z| = 1 (the matrix's typical magnitude) and is underpowered below
  |d_z| ≈ 0.8 — a FAIL with a consistent direction is reported as
  direction-consistent, and no widening is permitted.
- **TS-D1 (descriptive, no gate):** the J margin (jcac_anchored − hpa, and
  − concurrency) across populations 8 → 32 → 64, the 8T reference being
  the same systems' cells in `matrix_concurrency` (different experiment
  seeds — cross-experiment, so trend description only, never a test).
- **TS-D2 (descriptive, no gate):** mean Jain per system at 32/64 —
  whether portfolio fairness holds as width grows.

**Falsifier, pre-committed:** if the joint controller's J advantage does
not survive the wider portfolio (TS-H1a fails), DEFENSE_QA #13's answer
becomes "the advantage is an 8-tenant artifact" and that headlines the
limitations. Published as measured.

## §3 Stopping rule

One execution of each of the two experiment files (crash retries only,
retries: 2), standard validation, one analysis pass
(`analysis_tenant_scale.py` → `RESULTS_TENANT_SCALE.md`). No new mixes,
sizes, classes, systems, or reps after this push; 128+ tenants stays with
the planner-cells program, out of scope here.
