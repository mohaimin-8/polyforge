# W35a smoke-run bug log

Every failure mode found while hardening the harness before the full
matrix, with its fix. The point of this file: bugs found at smoke cost
minutes; the same bugs found at run 700 of 1,800 cost a restart at run 1.

## #1 — Irreproducible tuning seeds via builtin `hash()`

- **Symptom**: `baselines/tune.py` derived per-cell seeds with
  `hash((workload, mix, rep))`. Python randomizes string hashing per
  process (PYTHONHASHSEED), so every invocation of the tuning sweep would
  have scored the grid on *different* workloads — best-of-grid params
  would not have been reproducible, silently.
- **Found**: code review before the first sweep ran.
- **Fix**: sha256-derived seeds (`tune.py::score_combo`). Rule adopted
  everywhere: run identity and seeds only ever come from `hashlib`, never
  `hash()`.

## #2 — Unconstrained tuning degenerates utilization baselines into `static`

- **Symptom**: the tuning objective (J = cost + 2·violation + 0.5·(1−Jain))
  improves monotonically as utilization targets fall — an exploratory grid
  extended to target_rho = 0.2 / 1 rps-per-replica kept choosing the edge.
  "Tuned HPA" was converging to provision-everything-always, i.e. a worse
  duplicate of the `static` baseline, not HPA.
- **Found**: first two tuning sweeps both returned edge values.
- **Fix**: grids bounded to the vendor-sane operational envelope
  (target_rho ≥ 0.3; ≥ 2 rps/replica), monotonicity documented in
  `baselines/TUNING.md` so reviewers see why the bound exists and that the
  degenerate end of the spectrum is represented by `static` itself.

## #3 — Reproducibility gate false-alarms under multiple comparisons

- **Symptom**: `scripts/ks_check.py` (two independent 12-rep groups of the
  same scenario, KS per metric at p > 0.1) failed on `cache_hit_rate`
  (KS = 0.583, p = 0.031) — apparently a non-deterministic harness.
- **Found**: first ks_check run after the 50-run smoke.
- **Diagnosis**: empirical bias test across six independent 12-rep groups
  showed between-group scatter of means (3.2e-5) *below* the theoretical
  standard error (4.1e-5) — the harness is unbiased; four simultaneous
  tests at α = 0.1 on a near-degenerate distribution (CV ≈ 0.07%) have a
  ~34% familywise false-alarm rate. The gate was broken, not the harness.
- **Fix**: Holm–Bonferroni at familywise α = 0.1 in ks_check.py. The
  corrected gate passes; the same correction discipline applies to the
  W36 per-metric significance claims.

## Smoke outcome

- 50/50 runs valid, 0 flakes, 0 retries, 34 s wall with 4 workers
  (`results/smoke.duckdb`).
- Resume verified: re-invoking the runner reports 50 already-valid,
  0 pending.
- Spot-check: 10 random runs replayed bit-identically (drift 0.0 against
  a ±3% tolerance).
- Reproducibility: Holm-corrected KS across independent rep groups green
  on all four gated metrics.

## Cluster-backend failure modes (pre-registered, not yet observable)

The roadmap's cloud-specific failure modes (clock skew corrupting Jain
windows, ClickHouse timeouts under burst, kind inode exhaustion after ~30
runs, k6 merge races) cannot occur on the sim backend and remain open
until the first cloud smoke on the W35a box. The harness-side defenses
already in place: single-writer DB, per-run idempotent IDs, retry-then-
mark-failed, fixed-name idempotent kind teardown.
