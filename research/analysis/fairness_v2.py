"""Phase 4 (v2): the fairness γ-ablation under noisy-neighbor interference.

v1's honest limitation (RESULTS.md, Notes): the synthetic workloads never
produced the W32 interference signal the γ-term responds to, so the γ=0
ablation was untested, not refuted. `fairness_v2.yaml` injects that signal
(simulate.interference_scores / interfered_state) and re-runs the ablation
on the same slice as the v1 ablations (medium cluster, 5 workloads,
4 mixes, 5 reps, 100 cells per system).

Emits FAIRNESS_V2.md: paired γ=0.5-vs-γ=0 on the five headline metrics
plus worst-tenant p95 (from per-tenant timeseries — the metric the γ-term
exists to protect). Either outcome resolves the segment: a significant
γ contribution, or a reported negative result and the term is dropped
from claims (V2_README Phase 4 acceptance).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
from scipy import stats as sps

import stats
from run_analysis import METRIC_LABELS, md_table

DB = stats.REPO_ROOT / "eval" / "results" / "fairness_v2.duckdb"
OUT = stats.record_path("FAIRNESS_V2.md")

FULL, ABLATED = "jcac", "jcac_nofairness"
CELLS = ["workload", "tenant_mix", "cluster_size", "rep"]


def load_worst_tenant(db_path: Path) -> pd.DataFrame:
    """Per run: the worst tenant's p95 latency and mean violation —
    'no tenant is allowed to have a terrible day so another can have a
    great one' is the γ-term's actual job, and cluster means hide it.

    From the DuckDB when present, else from the committed aggregate that
    `eval/scripts/export_timeseries_agg.py` writes with this exact query —
    the timeseries are Zenodo-archived (192k rows), the grouped result is
    200, so the aggregate is what git carries."""
    if not db_path.exists():
        agg = db_path.parent / "agg_fairness_v2_worst_tenant.csv.gz"
        if agg.exists():
            df = pd.read_csv(agg)
            for col in ("worst_tenant_p95_ms", "worst_tenant_violation"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df
        raise FileNotFoundError(
            f"{db_path.name} missing and its committed aggregate {agg.name} "
            "is absent too; run the campaign or fetch the Zenodo archive")
    con = duckdb.connect(str(db_path), read_only=True)
    df = con.execute(
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
        """
    ).df()
    con.close()
    return df


def paired_rows(df: pd.DataFrame, metrics: list[str]) -> list[dict]:
    """γ-removal effect, paired by cell: ablated − full (positive diff on
    a lower-is-better metric = removal hurts)."""
    a = df[df.system == ABLATED]
    f = df[df.system == FULL]
    merged = a.merge(f, on=CELLS, suffixes=("_a", "_f"))
    rows = []
    for m in metrics:
        diff = merged[f"{m}_a"] - merged[f"{m}_f"]
        sd = diff.std(ddof=1)
        if len(diff) < 2 or sd == 0.0:
            p, dz = 1.0, 0.0
        else:
            _, p = sps.ttest_1samp(diff, 0.0)
            dz = float(diff.mean() / sd)
        better = stats.BETTER.get(m, -1)
        hurts = diff.mean() * better < 0
        rel = (diff.mean() / abs(merged[f"{m}_f"].mean())
               if merged[f"{m}_f"].mean() else 0.0)
        rows.append({
            "metric": METRIC_LABELS.get(m, m),
            "full (γ=0.5)": float(merged[f"{m}_f"].mean()),
            "ablated (γ=0)": float(merged[f"{m}_a"].mean()),
            "removal change": f"{100 * rel:+.1f}%",
            "p": float(p), "d_z (removal)": dz,
            "removal hurts": bool(hurts),
            "significant (p<0.01)": bool(p < 0.01),
        })
    return rows


def main() -> None:
    df = stats.load_runs(DB)
    worst = load_worst_tenant(DB)

    lines: list[str] = []
    w = lines.append
    w("# Fairness γ-ablation under interference injection (v2 Phase 4)")
    w("")
    w(f"Source: `eval/results/{DB.name}` ({len(df)} valid runs): "
      "`jcac` (γ=0.5) vs `jcac_nofairness` (γ=0), medium cluster, "
      "5 workloads × 4 tenant mixes × 5 reps, noisy-neighbor interference "
      "injected (executed-work share > 2× fair share ⇒ victims lose up to "
      "50% serving capacity; the controller sees the observable detector "
      "score). This is the signal v1 documented as absent (RESULTS.md "
      "Notes) — constants are fixed by the W32 signal shape, not tuned.")
    w("")
    w("## Headline metrics (paired by cell, removal = γ=0 minus full)")
    w("")
    w(md_table(pd.DataFrame(paired_rows(df, stats.METRICS))))
    w("")
    w("## Worst-tenant metrics (from per-tenant timeseries, all reps)")
    w("")
    w("The γ-term's job is bounding how bad the *worst* tenant's experience "
      "gets while a neighbor bursts; cluster-mean metrics can hide that "
      "entirely.")
    w("")
    w(md_table(pd.DataFrame(paired_rows(
        worst, ["worst_tenant_p95_ms", "worst_tenant_violation"]))))
    w("")

    # Exploratory (NOT pre-registered): the injection gate (executed-work
    # share > 2× fair share) only trips when one tenant dominates, which by
    # construction is the `whale` mix — the designated fairness stressor
    # (workloads.py). In the other three mixes no interference is injected,
    # so those cells contribute identical γ=0.5/γ=0 behavior and dilute any
    # real effect 4×. This subgroup isolates the cells where the signal the
    # γ-term responds to is actually present. Reported as exploratory.
    whale = df[df.tenant_mix == "whale"]
    whale_worst = worst[worst.tenant_mix == "whale"]
    w("## Exploratory subgroup — whale cells only (where injection fires)")
    w("")
    w("Not pre-registered. The 2×-fair-share gate only trips under the "
      "`whale` mix; the other three mixes inject nothing and dilute the "
      "aggregate. This isolates the 25 cells where the γ-term's signal is "
      "present.")
    w("")
    w(md_table(pd.DataFrame(paired_rows(whale, stats.METRICS))))
    w("")
    w(md_table(pd.DataFrame(paired_rows(
        whale_worst, ["worst_tenant_p95_ms", "worst_tenant_violation"]))))
    w("")

    sig = [r for r in paired_rows(df, stats.METRICS)
           + paired_rows(worst, ["worst_tenant_p95_ms", "worst_tenant_violation"])
           if r["significant (p<0.01)"] and r["removal hurts"]]
    w("## Verdict")
    w("")
    if sig:
        w(f"**γ-term contribution is measurable under interference**: removal "
          f"significantly (p<0.01) degrades {len(sig)} metric(s): "
          f"{', '.join(r['metric'] for r in sig)}. The v1 'untested, not "
          "refuted' limitation is resolved in favor of the term.")
    else:
        w("**Negative result, reported as measured**: even under injected "
          "interference the γ-term shows no significant contribution at "
          "p<0.01. Per V2_README Phase 4 acceptance, the term is dropped "
          "from the paper's claims (it remains in the system as a "
          "configurable weight); the segment is resolved either way.")
    w("")
    w("Interference constants (gate 2× fair share, max 50% capacity cut) are "
      "committed in `research/jcac_sim/simulate.py`; they were fixed before "
      "this experiment ran and not swept.")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
