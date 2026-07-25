"""Concurrency-baseline campaign analysis (PREREG_CONCURRENCY.md, frozen).

Reads the matrix_concurrency duckdb, scores the frozen readings — CQ-H1
(J, confirmatory), CQ-H2 (cost, confirmatory), CQ-D1 (vs hpa,
descriptive) — and writes RESULTS_CONCURRENCY.md. Pairing is the standard
matched-cells design: (workload, tenant_mix, cluster_size, rep).

    python analysis_concurrency.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from scipy import stats as sps

import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "eval" / "results" / "raw_sim_concurrency.duckdb"
# Committed run-level export: the DuckDB is Zenodo-archived, so this is what
# lets a clean clone re-derive this record (stats.load_campaign_runs).
CSV = REPO_ROOT / "eval" / "results" / "metrics_matrix_concurrency.csv.gz"
# Written via stats.record_path so scripts/reproduce.py can redirect the
# rebuild into a scratch dir and diff it against the committed record
# without ever overwriting it.
OUT = stats.record_path("RESULTS_CONCURRENCY.md")
TENANTS = 8
COST_SCALE = 0.01
CELL = ["workload", "tenant_mix", "cluster_size", "rep"]

LABELS = {
    "jcac_anchored": "PolyForge (anchored)",
    "concurrency": "Concurrency (KPA/AIBrix shape)",
    "hpa": "HPA",
}


def load() -> pd.DataFrame:
    df = stats.load_campaign_runs(DB, CSV)
    # Identical to stats.composite_objective: cost normalized per scored
    # tenant-step (119 x 8 for the 120-step matrix) by COST_SCALE_USD.
    df["J"] = (df.total_cost_usd / (119 * TENANTS) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    return df


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    m = df[df.system == a].merge(df[df.system == b], on=CELL, suffixes=("_a", "_b"))
    diff = m[f"{metric}_a"] - m[f"{metric}_b"]
    sd = diff.std(ddof=1)
    if len(diff) < 2 or sd == 0.0:
        return {"mean_diff": float(diff.mean()) if len(diff) else 0.0,
                "p": 1.0, "dz": 0.0, "n": len(diff),
                "mean_a": float(m[f"{metric}_a"].mean()) if len(m) else 0.0,
                "mean_b": float(m[f"{metric}_b"].mean()) if len(m) else 0.0}
    _, p = sps.ttest_1samp(diff, 0.0)
    return {"mean_diff": float(diff.mean()), "p": float(p),
            "dz": float(diff.mean() / sd), "n": len(diff),
            "mean_a": float(m[f"{metric}_a"].mean()),
            "mean_b": float(m[f"{metric}_b"].mean())}


def md_table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    fmt = lambda v: f"{v:.4g}" if isinstance(v, float) else str(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |"
              for _, r in frame.iterrows()]
    return "\n".join(lines)


def main() -> None:
    runs = load()
    lines: list[str] = []
    w = lines.append
    w("# Concurrency/queue-depth baseline — results (pre-registered)")
    w("")
    w("Protocol frozen in `PREREG_CONCURRENCY.md` (pushed before the tuning "
      "sweep or any matrix run). The 2026 serving-stack reactive arm "
      "(Knative-KPA / AIBrix shape: in-flight-work signal, stable-window "
      "scale-down), grid-tuned on the paper's own J per the W34 protocol "
      "(`grids/concurrency.csv`; winner c_t=0.5, stable window 6 intervals — "
      "tuning-slice J 0.543, *stronger* than tuned HPA 0.555 and KEDA 0.573, "
      "so the arm is not a straw man). 900-run matrix, matched-cells pairing. "
      "Rerun: `python analysis_concurrency.py`.")
    w("")
    w("## Per-system summary (mean over the 300 matrix cells)")
    w("")
    summary = runs.groupby("system")[["J", "total_cost_usd", "mean_violation",
                                      "mean_jain"]].mean()
    order = [s for s in ("jcac_anchored", "concurrency", "hpa") if s in summary.index]
    pretty = summary.loc[order].reset_index()
    pretty["system"] = pretty.system.map(LABELS)
    w(md_table(pretty))
    w("")
    w("## CQ-H1 / CQ-H2 (confirmatory) — jcac_anchored vs tuned concurrency")
    w("")
    j = paired(runs, "jcac_anchored", "concurrency", "J")
    cost = paired(runs, "jcac_anchored", "concurrency", "total_cost_usd")
    slo = paired(runs, "jcac_anchored", "concurrency", "mean_violation")
    h1 = j["mean_diff"] < 0 and j["p"] < 0.01
    h2 = cost["mean_diff"] < 0 and cost["p"] < 0.01
    disclose = (h1 and slo["mean_diff"] > 0 and slo["p"] < 0.01
                and abs(slo["dz"]) >= 0.5)
    w(md_table(pd.DataFrame([
        {"reading": "CQ-H1: composite J", "n": j["n"],
         "diff (jcac−conc)": j["mean_diff"], "p": j["p"], "d_z": j["dz"],
         "verdict": "PASS" if h1 else "FAIL"},
        {"reading": "CQ-H2: cost (USD)", "n": cost["n"],
         "diff (jcac−conc)": cost["mean_diff"], "p": cost["p"], "d_z": cost["dz"],
         "verdict": "PASS" if h2 else "FAIL"},
        {"reading": "violation (disclosure rule)", "n": slo["n"],
         "diff (jcac−conc)": slo["mean_diff"], "p": slo["p"], "d_z": slo["dz"],
         "verdict": "DISCLOSED" if disclose else "no large-effect regression"},
    ])))
    w("")
    if h1 and disclose:
        w("**CQ-H1: PASS with disclosure** — the J win vs the tuned modern "
          "reactive arm comes with a large-effect violation regression "
          "(p<0.01, |d_z|≥0.5): the standing attainment-for-cost trade, "
          "stated prominently per §4.")
    elif h1:
        w("**CQ-H1: PASS** — the joint controller beats the tuned 2026-stack "
          "reactive arm on the composite objective.")
    else:
        w("**CQ-H1: FAIL** — per the pre-committed falsifier, this headlines "
          "the limitations: the tuned 2026-stack reactive arm closes the "
          "joint-control gap. Published as measured.")
    w(f"**CQ-H2: {'PASS' if h2 else 'FAIL'}.**")
    w("")
    w("## CQ-D1 (descriptive, no gate) — the modern arm vs tuned HPA")
    w("")
    rows = []
    for metric, name in (("J", "J"), ("total_cost_usd", "cost"),
                         ("mean_violation", "violation")):
        r = paired(runs, "concurrency", "hpa", metric)
        rows.append({"metric": name, "concurrency mean": r["mean_a"],
                     "hpa mean": r["mean_b"], "diff (conc−hpa)": r["mean_diff"],
                     "p": r["p"], "d_z": r["dz"]})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("Per workload class (J diff, concurrency − hpa):")
    w("")
    rows = []
    for wl in sorted(runs.workload.unique()):
        r = paired(runs[runs.workload == wl], "concurrency", "hpa", "J")
        rows.append({"class": wl, "J diff": r["mean_diff"], "p": r["p"],
                     "d_z": r["dz"], "n": r["n"]})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("## Notes")
    w("")
    w("- Same substrate rules as every matrix campaign: sim-backend decision "
      "quality, blocked-factorial matched cells, never mixed with replay "
      "tables (ground rules 3-4, 6).")
    w("- Stopping rule §5 honored: one tuning sweep, one matrix execution, "
      "one analysis pass; SLO confirmatory attempts remain closed (v3 rule).")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
