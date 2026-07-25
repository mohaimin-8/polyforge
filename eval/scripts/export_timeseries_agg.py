"""Export the two timeseries-derived views that analysis scripts need, so a
clean clone can re-derive their records without the Zenodo archives.

`export_metrics_csv.py` exports run-level metrics; that covers every campaign
whose analysis reads the `metrics` table. Two records do not fit that shape:

  * RESULTS_CHAOS_SIM.md  — needs mean violation per (system, workload, step),
    which lives in `timeseries`.
  * RESULTS_TIER_RATIO / HK_ADOPTION / LM_ADOPTION / MIXTURE_P95 / TIER_WU —
    need the per-(system, tier) posture counts, also from `timeseries`.
  * FAIRNESS_V2.md — needs each run's *worst* tenant, which only exists once
    the timeseries are grouped per tenant (200 rows out of 192k).

Committing the raw timeseries is not the answer (518k rows for chaos_sim
alone, and they are what the Zenodo deposit is for). Both consumers use a
*grouped aggregate*, and the aggregate is small — 4,320 rows for chaos, a few
dozen for the tier posture. So the aggregate is what git carries.

The honest scope of that choice: these two exports let a reviewer reproduce
the two records exactly, but not re-derive a *different* statistic over the
same timeseries. Anyone wanting that takes the DuckDB from Zenodo. Every
committed export states which query produced it, so the aggregate is
auditable rather than opaque.

Usage (from the repo root):
    python eval/scripts/export_timeseries_agg.py
"""

from __future__ import annotations

import gzip
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS = REPO_ROOT / "eval" / "results"

# (source DuckDB, output csv.gz, query) — each query is character-for-character
# the one its consumer runs, so the export cannot silently drift from it.
VIEWS = [
    (
        "chaos_sim.duckdb",
        "agg_chaos_violation_traces.csv.gz",
        # research/analysis/analysis_chaos.py::violation_traces
        """
        SELECT r.system, r.workload, t.step, AVG(t.violation) AS violation
        FROM timeseries t JOIN runs r USING (run_id)
        WHERE r.status = 'valid'
        GROUP BY r.system, r.workload, t.step
        ORDER BY r.system, r.workload, t.step
        """,
    ),
    (
        "raw_sim.duckdb",
        "agg_fig09_timeseries_slices.csv.gz",
        # research/analysis/figures.py::fig_adaptation, via
        # stats.load_timeseries — the two runs fig09 plots, and only those.
        # Same columns as load_timeseries returns, plus cluster_size and rep
        # so the fallback can re-apply the caller's filters.
        """
        SELECT r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep,
               t.step, t.tenant, t.replicas, t.cache_mb, t.tier,
               t.cost_usd, t.violation
        FROM timeseries t JOIN runs r USING (run_id)
        WHERE r.status = 'valid' AND r.workload = 'agentic'
          AND r.tenant_mix = 'uniform' AND r.cluster_size = 'medium'
          AND r.rep = 0 AND r.system IN ('jcac', 'hpa')
        ORDER BY r.system, t.step, t.tenant
        """,
    ),
    (
        "fairness_v2.duckdb",
        "agg_fairness_v2_worst_tenant.csv.gz",
        # research/analysis/fairness_v2.py::load_worst_tenant
        """
        WITH per_tenant AS (
            SELECT r.run_id, r.system, r.workload, r.tenant_mix,
                   r.cluster_size, r.rep, t.tenant,
                   quantile_cont(t.ai_p95_ms, 0.95) AS tenant_p95,
                   avg(t.violation) AS tenant_violation
            FROM timeseries t JOIN runs r USING (run_id)
            WHERE r.status = 'valid'
            GROUP BY ALL
        )
        SELECT run_id, system, workload, tenant_mix, cluster_size, rep,
               max(tenant_p95) AS worst_tenant_p95_ms,
               max(tenant_violation) AS worst_tenant_violation
        FROM per_tenant GROUP BY ALL
        """,
    ),
] + [
    (
        db,
        f"agg_tier_posture_{db.replace('raw_sim_', '').replace('.duckdb', '')}.csv.gz",
        # research/analysis/analysis_econ.py::tier_posture
        """
        SELECT r.system, t.tier, COUNT(*) AS n,
               AVG(t.cache_mb) AS mean_cache_mb
        FROM timeseries t JOIN runs r USING (run_id)
        WHERE r.status = 'valid'
        GROUP BY r.system, t.tier
        """,
    )
    for db in ("raw_sim_gpu_econ.duckdb", "raw_sim_hk.duckdb", "raw_sim_lm.duckdb",
               "raw_sim_mixp95.duckdb", "raw_sim_tierwu.duckdb")
]


def main() -> None:
    for db_name, out_name, query in VIEWS:
        db = RESULTS / db_name
        if not db.exists():
            print(f"skip {out_name}: {db_name} not present")
            continue
        con = duckdb.connect(str(db), read_only=True)
        rows = con.execute(query).fetchall()
        columns = [d[0] for d in con.description]
        con.close()
        with gzip.open(RESULTS / out_name, "wt", newline="", encoding="utf-8") as f:
            f.write(",".join(columns) + "\n")
            for row in rows:
                f.write(",".join("" if v is None else str(v) for v in row) + "\n")
        print(f"wrote {out_name} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
