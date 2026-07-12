"""VTC-replica fairness comparison (PREREG_VTC.md), analysis frozen with it.

jcac vs the tuned VTC-style pool divider on the Phase 4 fairness slice
under interference injection. Emits VTC_FAIRNESS.md: HV1 (joint control
wins the joint objective), HV2 (dedicated fair division does not deliver a
large fairness win — stated symmetrically, failure publishable), plus the
declared exploratory tables. Reuses the committed fairness_v2 machinery
for worst-tenant metrics.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from scipy import stats as sps

import stats
from fairness_v2 import load_worst_tenant
from run_analysis import METRIC_LABELS, md_table

DB = stats.REPO_ROOT / "eval" / "results" / "vtc_fairness.duckdb"
OUT = Path(__file__).resolve().parent / "VTC_FAIRNESS.md"

JCAC, VTC = "jcac", "vtc_replica"
CELLS = ["workload", "tenant_mix", "cluster_size", "rep"]


def paired(df: pd.DataFrame, metric: str) -> dict:
    """jcac − vtc_replica, paired by cell."""
    m = df[df.system == JCAC].merge(
        df[df.system == VTC], on=CELLS, suffixes=("_j", "_v"))
    diff = m[f"{metric}_j"] - m[f"{metric}_v"]
    sd = diff.std(ddof=1)
    if len(diff) < 2 or sd == 0.0:
        return {"jcac": float(m[f"{metric}_j"].mean()) if len(m) else 0.0,
                "vtc": float(m[f"{metric}_v"].mean()) if len(m) else 0.0,
                "mean_diff": float(diff.mean()) if len(diff) else 0.0,
                "p": 1.0, "dz": 0.0, "n": len(diff)}
    _, p = sps.ttest_1samp(diff, 0.0)
    return {"jcac": float(m[f"{metric}_j"].mean()),
            "vtc": float(m[f"{metric}_v"].mean()),
            "mean_diff": float(diff.mean()), "p": float(p),
            "dz": float(diff.mean() / sd), "n": len(diff)}


def metric_table(df: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    rows = []
    for metric in metrics:
        r = paired(df, metric)
        rows.append({
            "metric": METRIC_LABELS.get(metric, metric),
            "jcac": r["jcac"], "vtc_replica": r["vtc"],
            "diff (jcac−vtc)": r["mean_diff"], "p": r["p"], "d_z": r["dz"],
        })
    return pd.DataFrame(rows)


def main() -> None:
    df = stats.load_runs(DB)
    frame = df.assign(J=stats.composite_objective(df))
    worst = load_worst_tenant(DB)

    lines: list[str] = []
    w = lines.append
    w("# VTC-replica fairness comparison — results (pre-registered)")
    w("")
    w(f"Source: `eval/results/{DB.name}` ({len(df)} valid runs): `jcac` vs the "
      "tuned `vtc_replica` (least-weighted-service-first pool division, "
      "target_rho=0.3 per grids/vtc_replica.csv), Phase 4 fairness slice, "
      "interference injection on. Protocol frozen in `PREREG_VTC.md` before "
      "the run; analysis executed once. Regenerate: `python analysis_vtc.py`.")
    w("")

    w("## HV1 (confirmatory) — the joint objective")
    w("")
    j = paired(frame, "J")
    hv1 = j["mean_diff"] < 0 and j["p"] < 0.01
    w(f"J (jcac) = {j['jcac']:.4g} vs J (vtc_replica) = {j['vtc']:.4g}; paired "
      f"diff {j['mean_diff']:+.4g}, p = {j['p']:.3g}, d_z = {j['dz']:.3f} "
      f"(n = {j['n']}).")
    w("")
    w(f"**HV1: {'PASS' if hv1 else 'FAIL'}** — reported as measured.")
    w("")

    w("## HV2 (confirmatory, symmetric) — does dedicated fair division buy a large fairness win?")
    w("")
    jain = paired(frame, "mean_jain")
    wv = paired(worst, "worst_tenant_violation")
    # VTC wins a fairness metric when jcac − vtc is worse for jcac at
    # p<0.01 and |dz|>=0.5: Jain lower (diff<0), worst-tenant violation
    # higher (diff>0).
    vtc_wins_jain = jain["mean_diff"] < 0 and jain["p"] < 0.01 and abs(jain["dz"]) >= 0.5
    vtc_wins_worst = wv["mean_diff"] > 0 and wv["p"] < 0.01 and abs(wv["dz"]) >= 0.5
    hv2 = not (vtc_wins_jain or vtc_wins_worst)
    w(md_table(pd.DataFrame([
        {"metric": "Jain fairness", "jcac": jain["jcac"], "vtc_replica": jain["vtc"],
         "diff (jcac−vtc)": jain["mean_diff"], "p": jain["p"], "d_z": jain["dz"],
         "VTC large-effect win": "YES" if vtc_wins_jain else "no"},
        {"metric": "worst-tenant violation", "jcac": wv["jcac"], "vtc_replica": wv["vtc"],
         "diff (jcac−vtc)": wv["mean_diff"], "p": wv["p"], "d_z": wv["dz"],
         "VTC large-effect win": "YES" if vtc_wins_worst else "no"},
    ])))
    w("")
    if hv2:
        w("**HV2: PASS** — dedicated token-fair division does not deliver a "
          "large fairness advantage over joint control that carries fairness "
          "as one objective term.")
    else:
        w("**HV2: FAIL — published as the honest boundary**: the dedicated "
          "fair scheduler buys more raw fairness than joint control; PolyForge "
          "holds the joint objective (HV1). The thesis fairness claim is "
          "bounded accordingly.")
    w("")

    w("## Full per-metric table (paired by cell)")
    w("")
    w(md_table(metric_table(frame, stats.METRICS + ["J"])))
    w("")
    w("## Worst-tenant metrics (per-tenant timeseries, all reps)")
    w("")
    w(md_table(metric_table(worst, ["worst_tenant_p95_ms", "worst_tenant_violation"])))
    w("")

    w("## Exploratory (declared) — whale cells only (where injection fires)")
    w("")
    whale = frame[frame.tenant_mix == "whale"]
    whale_worst = worst[worst.tenant_mix == "whale"]
    w(md_table(metric_table(whale, stats.METRICS + ["J"])))
    w("")
    w(md_table(metric_table(whale_worst, ["worst_tenant_p95_ms", "worst_tenant_violation"])))
    w("")
    w("## Notes")
    w("")
    w("- `vtc_replica` is a reduction of VTC's scheduler to the replica knob "
      "(PREREG_VTC §1), the same shape as FIRM-replica; VTC's own 2× service "
      "bound applies to its token-level scheduler and is not claimed or "
      "tested here.")
    w("- Tuned per TUNING.md on the standard slice; every previously "
      "committed tuned value untouched (merge, not resweep).")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: HV1={'PASS' if hv1 else 'FAIL'}, HV2={'PASS' if hv2 else 'FAIL'}")


if __name__ == "__main__":
    main()
