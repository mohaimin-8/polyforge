"""Export run-level metrics from a result DB to a gzipped CSV that lives
in git. The raw DuckDB files (with timeseries) are Zenodo-archived, not
committed; this export is what a reviewer diffs and what re-analysis can
start from without DuckDB.

Usage (from eval/):
    python scripts/export_metrics_csv.py experiments/full.yaml
    python scripts/export_metrics_csv.py experiments/ablations.yaml
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

import duckdb

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness.config import load  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("experiment")
    args = ap.parse_args()

    spec = load(args.experiment)
    db_path = (EVAL_DIR.parent / spec.output).resolve()
    out = EVAL_DIR / "results" / f"metrics_{spec.name}.csv.gz"

    con = duckdb.connect(str(db_path), read_only=True)
    # Deliberately no ORDER BY: the export must carry the DuckDB's own row
    # order, because that is the order the analysis scripts read when they
    # generated the committed records. Bootstrap CIs resample positionally
    # under a fixed seed, so re-ordering the rows silently shifts published
    # CI bounds — the export would then re-derive a record that differs from
    # the frozen one in its last digits. Any consumer that needs a specific
    # order must sort explicitly and say why (see
    # research/analysis/analysis_risk_budget.py::load_null_runs).
    rows = con.execute(
        """
        SELECT r.run_id, r.system, r.workload, r.tenant_mix, r.cluster_size,
               r.rep, r.seed, r.steps, m.total_cost_usd, m.mean_violation,
               m.violation_step_share, m.mean_jain, m.cache_hit_rate,
               m.crud_p95_ms, m.ai_p95_ms
        FROM metrics m JOIN runs r USING (run_id)
        WHERE r.status = 'valid'
        """
    ).fetchall()
    columns = [d[0] for d in con.description]
    con.close()

    with gzip.open(out, "wt", newline="", encoding="utf-8") as f:
        f.write(",".join(columns) + "\n")
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")
    print(f"wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
