# Reproducing PolyForge's results

One command re-derives every generated measurement record and figure from
the committed data, without touching the frozen originals:

```
pip install -r eval/requirements.txt
python scripts/reproduce.py
```

Output goes to `reproduce_out/` (gitignored) and each rebuilt record is
diffed against its committed version. Verified 2026-07-26 (session 32) on a
tree with **no DuckDB files at all**: **16/16 records byte-identical, 19/19
figures rebuilt.** CI runs the clean-checkout tier on every push
(`reproduce` job in `.github/workflows/ci.yml`).

That is the whole generated corpus — the v1/v2/v3 records plus all eleven
campaign records (concurrency, learned control, both risk campaigns,
tenant scale, sim chaos, and the five economy/structural-form reruns) — so
a reviewer with nothing but a clone can re-derive every published number.

## Tiers — what reproduces from what

| tier | inputs | what re-derives |
|---|---|---|
| **git** (clean clone) | committed `eval/results/metrics_*.csv.gz` run-level exports, `agg_*.csv.gz` timeseries aggregates, security JSON/CSVs, trace-replay CSVs | **everything: all 16 records and all 19 figures** |
| **archive** (Zenodo DuckDBs restored into `eval/results/`) | + `raw_sim*.duckdb`, `ablations.duckdb`, `forecasters.duckdb`, `realism.duckdb`, `fairness_v2.duckdb`, … | the same 16/19, plus the full timeseries for any *new* analysis |
| **rerun** (hours of compute) | `eval/experiments/*.yaml` via `python -m harness.runner` | the raw databases themselves, seed-deterministic per run_id |
| **live** (user-gated hosts) | `docs/WAVE3_LIVE_RUNBOOK.md`, `PREREG_WAVE4_LIVE_PLANE.md` | the live campaign CSVs |

The archive tier no longer re-derives *more* than the git tier; it exists
for work that needs the raw per-step data rather than the published
statistics.

Every campaign now has a committed run-level export — including the v2/v3
matrices, the VTC and fairness slices, iso-cost, and the five Wave-5
robustness reruns (`metrics_matrix_v2/_v3_overload/_clamp/_lm/_mixp95/
_structreal/_tierwu`, `metrics_vtc_fairness`, `metrics_fairness_v2`,
`metrics_isocost`) — so reviewers can re-check any headline number's
run-level data by diffing a gzipped CSV.

## Mechanics

- `research/analysis/stats.py` loads run-level metrics from the DuckDB
  when present and falls back to the campaign's committed csv.gz export
  otherwise — `load_runs`/`CSV_EXPORTS` for the shared projection,
  `load_campaign_runs` for the per-campaign matrices.
- Raw timeseries (millions of rows) stay in the Zenodo archives. Where an
  analysis needs them, git carries a committed **aggregate of exactly the
  query that analysis runs** (`eval/scripts/export_timeseries_agg.py`
  writes them; each `agg_*.csv.gz` names its consumer). That covers the
  chaos violation traces, the per-(system, tier) posture counts, the
  fairness worst-tenant rollup, and the two runs fig09 plots. The honest
  scope: these reproduce those records exactly, but computing a *different*
  statistic over the same timeseries needs the archive — and asking for a
  slice outside the committed one raises rather than silently returning
  nothing.
- Row order is part of the contract. Several campaigns report bootstrap CIs,
  and resampling under a fixed seed reads rows positionally, so the exports
  are written in the DuckDB's own row order and neither load path re-sorts.
  `load_runs` is the exception: its SQL has an `ORDER BY`, so its CSV branch
  re-applies exactly that sort.
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
