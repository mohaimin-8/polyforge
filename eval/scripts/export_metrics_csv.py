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
import io
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
    # The V-series severity pair is appended only when the campaign's DB
    # actually has it. Every campaign committed before PREREG_EVICTION_PARITY
    # predates those columns, so this keeps their exports byte-identical while
    # letting newer campaigns carry the metrics their records need (R4).
    have = {r[0] for r in con.execute("DESCRIBE metrics").fetchall()}
    extra = [c for c in ("mean_excess", "tier_none_step_share") if c in have]
    extra_sql = "".join(f", m.{c}" for c in extra)

    rows = con.execute(
        f"""
        SELECT r.run_id, r.system, r.workload, r.tenant_mix, r.cluster_size,
               r.rep, r.seed, r.steps, m.total_cost_usd, m.mean_violation,
               m.violation_step_share, m.mean_jain, m.cache_hit_rate,
               m.crud_p95_ms, m.ai_p95_ms{extra_sql}
        FROM metrics m JOIN runs r USING (run_id)
        WHERE r.status = 'valid'
        """
    ).fetchall()
    columns = [d[0] for d in con.description]
    con.close()

    # mtime=0, for the reason 8f2eb30 fixed in the aggregate exporter and
    # missed here: gzip stamps the current time into its header, so
    # re-running this script produced a new byte stream from identical
    # data. "Re-export and diff" is the only check a committed export can
    # carry, and a moving header defeats it. The exports already in git are
    # not re-stamped: their DuckDBs are Zenodo-archived rather than in the
    # tree, so they cannot be regenerated here.
    raw = gzip.GzipFile(out, "wb", mtime=0)
    with io.TextIOWrapper(raw, newline="", encoding="utf-8") as f:
        f.write(",".join(columns) + "\n")
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")
    print(f"wrote {out} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
