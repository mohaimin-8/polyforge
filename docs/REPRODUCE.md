# Reproducing PolyForge's results

One command re-derives every generated measurement record and figure from
the committed data, without touching the frozen originals:

```
pip install -r eval/requirements.txt
python scripts/reproduce.py
```

Output goes to `reproduce_out/` (gitignored) and each rebuilt record is
diffed against its committed version. Verified 2026-07-19 (session 28):
**all five records byte-identical, 17/17 figures rebuilt.** CI runs the
clean-checkout tier on every push (`reproduce` job in
`.github/workflows/ci.yml`).

## Tiers — what reproduces from what

| tier | inputs | what re-derives |
|---|---|---|
| **git** (clean clone) | committed `eval/results/metrics_*.csv.gz` run-level exports + security JSON/CSVs + trace-replay CSVs | `RESULTS.md`, `ADVANCED.md`, figures 1–8 and 10–17 |
| **archive** (Zenodo DuckDBs restored into `eval/results/`) | + `raw_sim*.duckdb`, `ablations.duckdb`, `forecasters.duckdb`, `realism.duckdb`, `fairness_v2.duckdb`, … | + `RESULTS_V2.md`, `RESULTS_V3.md`, `FAIRNESS_V2.md`, fig09 (timeseries) |
| **rerun** (hours of compute) | `eval/experiments/*.yaml` via `python -m harness.runner` | the raw databases themselves, seed-deterministic per run_id |
| **live** (user-gated hosts) | `docs/WAVE3_LIVE_RUNBOOK.md`, `PREREG_WAVE4_LIVE_PLANE.md` | the live campaign CSVs |

Every campaign now has a committed run-level export — including the v2/v3
matrices, the VTC and fairness slices, iso-cost, and the five Wave-5
robustness reruns (`metrics_matrix_v2/_v3_overload/_clamp/_lm/_mixp95/
_structreal/_tierwu`, `metrics_vtc_fairness`, `metrics_fairness_v2`,
`metrics_isocost`) — so reviewers can re-check any headline number's
run-level data by diffing a gzipped CSV.

## Mechanics

- `research/analysis/stats.py` loads run-level metrics from the DuckDB
  when present and falls back to the campaign's committed csv.gz export
  otherwise (`CSV_EXPORTS`); timeseries exist only in the archived
  DuckDBs, so timeseries consumers raise a clear error in the git tier.
- The exports round-trip DuckDB float32 columns (latency percentiles) at
  float32 precision (~1e-11 absolute) — seven orders of magnitude below
  the 4-significant-figure reporting precision.
- `POLYFORGE_ANALYSIS_OUT` / `POLYFORGE_FIG_DIR` redirect record and
  figure output; `scripts/reproduce.py` sets them so the committed
  (pre-registration-frozen) records are never overwritten, then reports
  MATCH / drift per record.
- Per-campaign analysis scripts outside the `run_analysis.py` chain
  (`live_chaos_p99.py`, `trace_matrix*.py`, `analysis_concurrency.py`,
  `analysis_tenant_scale.py`, …) read their committed campaign CSVs
  directly; their records name their exact inputs in their headers.

## Related

- `eval/README.md` — harness design, seed determinism, replaying a run_id
- `docs/RELEASE_CHECKLIST.md` — Zenodo deposit / OSF registration steps
- `research/paper/main.tex` — manuscript scaffold consuming the
  regenerable figures
