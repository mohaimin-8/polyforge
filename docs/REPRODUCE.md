# Reproducing PolyForge's results

One command re-derives every generated measurement record and figure from
the committed data, without touching the frozen originals:

```
pip install -r eval/requirements-reproduce.txt
python scripts/reproduce.py
```

**Use the pinned file for reproduction.** `eval/requirements.txt` is
deliberately loose so the system keeps working as its dependencies move;
byte-identity is a claim about exact output and needs exact versions. The
records were written under pandas 2.2.3 / numpy 2.2.3 / scipy 1.18.0, and
installing the loose file today resolves to pandas 3.0.x, which is how this
gate came to be green on the author's machine and red in CI from 2026-08-31
until session 43.

Output goes to `reproduce_out/` (gitignored) and each rebuilt record is
diffed against its committed version. Verified 2026-07-26 (session 32) on a
tree with **no DuckDB files at all**: **16/16 records byte-identical, 19/19
figures rebuilt.** CI runs the clean-checkout tier on every push
(`reproduce` job in `.github/workflows/ci.yml`).

That is the whole generated corpus — the v1/v2/v3 records plus all eleven
campaign records (concurrency, learned control, both risk campaigns,
tenant scale, sim chaos, and the five economy/structural-form reruns) — so
a reviewer with nothing but a clone can re-derive every published number.

Figures are held to their data three ways, each printed on its own line of
the report: **figure content** — the platform-independent dump of what each
figure plots (`eval/results/figures/content/*.json`: every line, bar, scatter
offset, scale, limit, tick label and legend entry), compared byte for byte;
**figure captions** — each rebuilt figure's caption against the committed
`FIGURES.md` inventory the site publishes, and every committed figure's
presence in it (captions are outcome-aware text computed from the data, and
`figures.save()` upserts each one as it draws); **figure files** — the PDF and
PNG bytes, exact on the renderer that drew them and reported as
not-comparable on any other. The first two decide the verdict everywhere;
the third only where the renderer matches.

## Tiers — what reproduces from what

| tier | inputs | what re-derives |
|---|---|---|
| **git** (clean clone) | committed `eval/results/metrics_*.csv.gz` run-level exports, `agg_*.csv.gz` timeseries aggregates, security JSON/CSVs, trace-replay CSVs, and `eval/harness/trace_cache/window0.json.gz` | **everything: all 38 gated records and all 19 figures** |
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

## Why the exports are read with `float_precision="round_trip"`

`pandas.read_csv` defaults to a fast float parser that is **not** exact in the
last bit. The committed exports carry each value's exact shortest repr — the
text `414.35701789047914` is byte-for-byte what DuckDB held — but the default
parser returns `414.3570178904792` for it. One ulp, and enough: a rank test
downstream turned it into a p-value of 1.26e-11 where the record says 1.17e-11,
so `RESULTS_LAYERED_FIX.md` failed to reproduce on any machine without the
DuckDB — which is every reviewer's machine.

Every two-path loader (DuckDB when present, committed export otherwise) now
passes `float_precision="round_trip"`, which makes the export path
**bit-identical** to the database path: measured max |db − csv| across the
campaign's columns goes from 1.5e-11 to exactly 0.

Loaders whose only input is a CSV are deliberately left alone. Their records
were generated through the default parser, so changing it there would alter
published numbers rather than reproduce them.

## The one input that is not a CSV

`RESULTS_TRACE_LIVE.md` replays window 0 of the BurstGPT trace through the
simulator to compare against the live sitting. The raw trace is 56 MB of
downloaded public dataset and is **not** vendored, so the window's projection
is committed instead — `eval/harness/trace_cache/window0.json.gz`, 5 KB, built
by `python -m harness.trace_demand --build-cache 0`.

`trace_demand.trace_window()` uses the raw trace when it is present and the
cache when it is not, so a clean clone rebuilds the record byte-identically.
Where the raw trace *is* present, `eval/tests/test_trace_cache.py` re-derives
the projection and compares it byte for byte, so a stale cache fails rather
than replaying quietly — the same rule `separation_mt_v3_walk.json` follows.

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
