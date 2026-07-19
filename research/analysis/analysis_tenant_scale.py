"""Tenant-scale campaign analysis (PREREG_TENANT_SCALE.md, frozen).

Scores TS-H1a (32T, confirmatory), TS-H1b (64T, confirmatory at
large-effects power), TS-D1 (margin trend 8 -> 32 -> 64, descriptive,
8T reference = matrix_concurrency), TS-D2 (Jain at width, descriptive),
and writes RESULTS_TENANT_SCALE.md.

    python analysis_tenant_scale.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
from scipy import stats as sps

REPO_ROOT = Path(__file__).resolve().parents[2]
DB32 = REPO_ROOT / "eval" / "results" / "raw_sim_scale32.duckdb"
DB64 = REPO_ROOT / "eval" / "results" / "raw_sim_scale64.duckdb"
DB8 = REPO_ROOT / "eval" / "results" / "raw_sim_concurrency.duckdb"
OUT = Path(__file__).resolve().parent / "RESULTS_TENANT_SCALE.md"
COST_SCALE = 0.01
SCORED_STEPS = 119  # 120-step runs score 119 intervals (stats.py convention)
CELL = ["workload", "tenant_mix", "cluster_size", "rep"]
BASELINES = ["hpa", "keda", "concurrency"]
TENANTS_BY_MIX = {"uniform32": 32, "whale32": 32, "uniform64": 64,
                  "uniform": 8, "premium_heavy": 8, "besteffort_heavy": 8,
                  "whale": 8}

LABELS = {"jcac_anchored": "PolyForge (anchored)", "hpa": "HPA",
          "keda": "KEDA", "concurrency": "Concurrency (KPA shape)"}


def load(db: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    df = con.execute(
        "select r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep, "
        "m.total_cost_usd, m.mean_violation, m.mean_jain, m.steps "
        "from runs r join metrics m on r.run_id = m.run_id "
        "where r.status = 'valid'"
    ).fetchdf()
    con.close()
    tenants = df.tenant_mix.map(TENANTS_BY_MIX)
    df["J"] = (df.total_cost_usd / (SCORED_STEPS * tenants) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    return df


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    m = df[df.system == a].merge(df[df.system == b], on=CELL, suffixes=("_a", "_b"))
    diff = m[f"{metric}_a"] - m[f"{metric}_b"]
    sd = diff.std(ddof=1)
    if len(diff) < 2 or sd == 0.0:
        return {"mean_diff": float(diff.mean()) if len(diff) else 0.0,
                "p": 1.0, "dz": 0.0, "n": len(diff)}
    _, p = sps.ttest_1samp(diff, 0.0)
    return {"mean_diff": float(diff.mean()), "p": float(p),
            "dz": float(diff.mean() / sd), "n": len(diff)}


def md_table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    fmt = lambda v: f"{v:.4g}" if isinstance(v, float) else str(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |"
              for _, r in frame.iterrows()]
    return "\n".join(lines)


def gate_section(w, df: pd.DataFrame, name: str) -> None:
    rows, all_ok, disclosures = [], True, []
    for baseline in BASELINES:
        j = paired(df, "jcac_anchored", baseline, "J")
        slo = paired(df, "jcac_anchored", baseline, "mean_violation")
        ok = j["mean_diff"] < 0 and j["p"] < 0.01
        all_ok &= ok
        if ok and slo["mean_diff"] > 0 and slo["p"] < 0.01 and abs(slo["dz"]) >= 0.5:
            disclosures.append(baseline)
        rows.append({
            "baseline": LABELS[baseline], "n": j["n"],
            "J diff (jcac−base)": j["mean_diff"], "p": j["p"], "d_z": j["dz"],
            "violation diff": slo["mean_diff"], "violation p": slo["p"],
            "verdict": "PASS" if ok else "FAIL",
        })
    w(md_table(pd.DataFrame(rows)))
    w("")
    tail = (" with disclosure — violation regression vs "
            + ", ".join(LABELS[b] for b in disclosures)
            + " (p<0.01, |d_z|≥0.5), the standing attainment-for-cost trade"
            if disclosures else "")
    w(f"**{name}: {'PASS' if all_ok else 'FAIL'}{tail}.**")
    w("")


def main() -> None:
    df32, df64 = load(DB32), load(DB64)
    lines: list[str] = []
    w = lines.append
    w("# End-to-end tenant scale — results (pre-registered, 32 and 64 tenants)")
    w("")
    w("Protocol frozen in `PREREG_TENANT_SCALE.md` (pushed before any run). "
      "Per-tenant world identical to the 8-tenant matrix; cluster caps "
      "scaled linearly; monolithic planning, wall time recorded. Rerun: "
      "`python analysis_tenant_scale.py`.")
    w("")
    for scale, df in (("32 tenants", df32), ("64 tenants", df64)):
        w(f"## Per-system summary — {scale}")
        w("")
        summary = df.groupby("system")[["J", "total_cost_usd", "mean_violation",
                                        "mean_jain"]].mean()
        order = [s for s in ("jcac_anchored", *BASELINES) if s in summary.index]
        pretty = summary.loc[order].reset_index()
        pretty["system"] = pretty.system.map(LABELS)
        w(md_table(pretty))
        w("")
    w("## TS-H1a (confirmatory) — 32 tenants")
    w("")
    gate_section(w, df32, "TS-H1a")
    w("## TS-H1b (confirmatory, large-effects power) — 64 tenants")
    w("")
    gate_section(w, df64, "TS-H1b")

    w("## TS-D1 (descriptive) — J margin vs portfolio width")
    w("")
    if DB8.exists():
        df8 = load(DB8)
        rows = []
        for baseline in ("hpa", "concurrency"):
            for scale, frame in (("8 (matrix_concurrency ref)", df8),
                                 ("32", df32), ("64", df64)):
                r = paired(frame, "jcac_anchored", baseline, "J")
                if r["n"]:
                    rows.append({"baseline": LABELS[baseline], "tenants": scale,
                                 "J diff": r["mean_diff"], "d_z": r["dz"],
                                 "n": r["n"]})
        w(md_table(pd.DataFrame(rows)))
        w("")
        w("Cross-experiment reference (different seeds): trend description "
          "only, never a test (prereg §2).")
    else:
        w("(8-tenant reference DB absent; trend omitted.)")
    w("")
    w("## TS-D2 (descriptive) — fairness at width")
    w("")
    rows = []
    for scale, df in (("32", df32), ("64", df64)):
        for system in ("jcac_anchored", *BASELINES):
            sub = df[df.system == system]
            if len(sub):
                rows.append({"tenants": scale, "system": LABELS[system],
                             "mean Jain": float(sub.mean_jain.mean()),
                             "min Jain": float(sub.mean_jain.min())})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("## Notes")
    w("")
    w("- Stopping rule honored: one execution per experiment file, one "
      "analysis pass; 128+ tenants stays with the planner-cells program.")
    w("- Sim-backend decision quality; matrix substrate rules apply.")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
