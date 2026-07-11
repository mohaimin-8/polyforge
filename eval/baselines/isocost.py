"""Derive iso-cost baseline budgets from the completed matrix_v2 database
(PREREG_V2 §5). Run once, after matrix_v2 finishes:

    cd eval && python baselines/isocost.py

Writes `eval/baselines/isocost_budgets.yaml`, consumed per run by
harness/isocost.py. Nothing here is tunable: every number is either a
committed model constant or jcac_v2's realized spend.

Constructions (both ceiling-rounded — the baseline receives *at least*
PolyForge's budget; rounding is strict against PolyForge):

- `static_isocost`: for the pinned static posture (cache 1024 MB, mid tier)
  run cost is exactly affine in replicas, cost(n) = A + B·n_tenants·n with
  B = REPLICA_COST_USD_HR · (CONTROL_INTERVAL_S/3600) · steps  (=$0.016,
  verified affine to the cent against the simulator). A is estimated per
  cell from the matrix's own `static` runs (same posture, n = replica_max,
  mean over reps, LRU tier economics included):
      A_hat = mean_static_cost − B·n_tenants·replica_max
      n = ceil((budget − A_hat) / (B·n_tenants)),  clamped to [1, replica_max]
  Cells where even n=1 overshoots the budget (the posture's tier spend
  alone exceeds PolyForge's whole budget) are clamped and flagged
  `budget_infeasible: true` — the baseline runs with MORE money than
  PolyForge everywhere.

- `cache_isocost`: the GPTCache posture with the cache pinned at the
  smallest committed cache level ≥ jcac_v2's realized mean per-tenant
  cache size (rep-0 timeseries, the matrix's stored-timeseries rep), so
  its cache spend is at least PolyForge's.
"""

from __future__ import annotations

import math
import sys
from datetime import date
from pathlib import Path

import duckdb
import yaml

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(EVAL_DIR.parent / "research" / "jcac_sim"))

from model import CACHE_LEVELS_MB, CONTROL_INTERVAL_S, REPLICA_COST_USD_HR  # noqa: E402
from harness.workloads import CLUSTER_SIZES, TENANT_MIXES  # noqa: E402

DB = EVAL_DIR / "results" / "raw_sim_v2.duckdb"
OUT = Path(__file__).resolve().parent / "isocost_budgets.yaml"
STEPS = 120


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    budgets = con.execute(
        """
        SELECT r.workload, r.tenant_mix, r.cluster_size,
               avg(CASE WHEN r.system = 'jcac_v2' THEN m.total_cost_usd END) AS budget,
               avg(CASE WHEN r.system = 'static' THEN m.total_cost_usd END) AS static_cost
        FROM metrics m JOIN runs r USING (run_id)
        WHERE r.status = 'valid' AND r.system IN ('jcac_v2', 'static')
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
        """
    ).fetchall()
    cache_rows = dict(con.execute(
        """
        SELECT r.workload || '|' || r.tenant_mix || '|' || r.cluster_size,
               avg(t.cache_mb)
        FROM timeseries t JOIN runs r USING (run_id)
        WHERE r.status = 'valid' AND r.system = 'jcac_v2'
        GROUP BY 1
        """
    ).fetchall())
    con.close()

    b = REPLICA_COST_USD_HR * (CONTROL_INTERVAL_S / 3600.0) * STEPS  # per replica per run
    cells = {}
    infeasible = 0
    for workload, mix, cluster, budget, static_cost in budgets:
        if budget is None or static_cost is None:
            raise SystemExit(f"cell {workload}|{mix}|{cluster}: missing jcac_v2 or static runs")
        n_tenants = len(TENANT_MIXES[mix])
        replica_max = CLUSTER_SIZES[cluster].replica_max
        a_hat = static_cost - b * n_tenants * replica_max
        n_raw = math.ceil((budget - a_hat) / (b * n_tenants))
        n = max(1, min(replica_max, n_raw))
        key = f"{workload}|{mix}|{cluster}"
        mean_cache = float(cache_rows.get(key, 0.0))
        level = next((lv for lv in CACHE_LEVELS_MB if lv >= mean_cache), CACHE_LEVELS_MB[-1])
        cells[key] = {
            "budget_usd": round(float(budget), 6),
            "static_A_usd": round(float(a_hat), 6),
            "static_replicas_per_tenant": int(n),
            "budget_infeasible": bool(n_raw < 1),
            "jcac_v2_mean_cache_mb": round(mean_cache, 2),
            "cache_level_mb": int(level),
        }
        infeasible += n_raw < 1

    OUT.write_text(yaml.safe_dump({
        "generated": str(date.today()),
        "source": "eval/results/raw_sim_v2.duckdb (matrix_v2)",
        "protocol": "PREREG_V2.md section 5 - ceiling rounding, strict against PolyForge",
        "B_per_replica_run_usd": b,
        "cells": cells,
    }, sort_keys=False), encoding="utf-8")
    print(f"wrote {OUT}: {len(cells)} cells, {infeasible} budget-infeasible "
          f"(baseline outspends PolyForge even at n=1)")


if __name__ == "__main__":
    main()
