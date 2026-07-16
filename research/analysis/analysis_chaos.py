"""Analysis for the Wave 2 chaos campaign (PREREG_CHAOS_SIM.md). Tests,
pairing (workload x rep = 60 pairs), alpha 0.01, and the recovery metric are
frozen in the prereg; this script mechanizes them.

  python analysis_chaos.py    -> RESULTS_CHAOS_SIM.md
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
from scipy import stats as sps

import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "eval" / "results" / "chaos_sim.duckdb"
OUT = Path(__file__).resolve().parent / "RESULTS_CHAOS_SIM.md"
ALPHA = 0.01
KEYS = ["workload", "rep"]  # single mix and size by design

# Chaos arm -> (its control arm, first scored step after the failure window).
# Outage planning window [40, 40+n) affects scored steps [41, 41+n);
# kill (60, f, 3) affects scored steps [60, 63).
ARMS = {
    "jcac_outage_1m": ("jcac", 47),
    "jcac_outage_3m": ("jcac", 59),
    "hpa_outage_1m": ("hpa", 47),
    "jcac_kill50": ("jcac", 63),
    "hpa_kill50": ("hpa", 63),
    "keda_kill50": ("keda", 63),
}
RECOVERY_TOL = 0.02
ROLL = 3


def paired(df: pd.DataFrame, a: str, b: str, metric: str = "J") -> dict:
    """Paired a-minus-b on (workload, rep) cells."""
    m = df[df.system == a].merge(df[df.system == b], on=KEYS, suffixes=("_a", "_b"))
    diff = m[f"{metric}_a"] - m[f"{metric}_b"]
    sd = diff.std(ddof=1)
    tstat, p = sps.ttest_1samp(diff, 0.0)
    n = len(diff)
    ci = sps.t.interval(0.95, n - 1, loc=diff.mean(), scale=diff.sem())
    return {
        "pairs": n, "mean": float(diff.mean()), "dz": float(diff.mean() / sd) if sd else 0.0,
        "p": float(p), "ci": (float(ci[0]), float(ci[1])),
        "win": bool(diff.mean() < 0 and p < ALPHA),
    }


def violation_traces() -> pd.DataFrame:
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute(
        """
        SELECT r.system, r.workload, t.step, AVG(t.violation) AS violation
        FROM timeseries t JOIN runs r USING (run_id)
        WHERE r.status = 'valid'
        GROUP BY r.system, r.workload, t.step
        ORDER BY r.system, r.workload, t.step
        """
    ).df()
    con.close()
    return df


def recovery_steps(traces: pd.DataFrame, arm: str, control: str,
                   workload: str, start: int) -> str:
    a = traces[(traces.system == arm) & (traces.workload == workload)]
    c = traces[(traces.system == control) & (traces.workload == workload)]
    merged = a.merge(c, on="step", suffixes=("_a", "_c")).sort_values("step")
    merged["roll_a"] = merged.violation_a.rolling(ROLL).mean()
    merged["roll_c"] = merged.violation_c.rolling(ROLL).mean()
    after = merged[merged.step >= start + ROLL - 1]
    hit = after[(after.roll_a - after.roll_c).abs() <= RECOVERY_TOL]
    if hit.empty:
        return "not within run"
    return str(int(hit.step.iloc[0]) - start)


def main() -> None:
    df = stats.load_runs(DB)
    df = df.assign(J=stats.composite_objective(df))
    traces = violation_traces()

    lines: list[str] = []
    w = lines.append
    w("# Simulated chaos campaign — as measured (PREREG_CHAOS_SIM.md)")
    w("")
    w(f"Database `chaos_sim.duckdb`, {len(df)} valid runs; pairing "
      f"(workload, rep) = 60 pairs; paired t, alpha {ALPHA}. Windows: planner")
    w("outage planning steps [40,46)/[40,58) (scored [41,47)/[41,59)); replica")
    w("kill scored steps [60,63).")
    w("")

    w("## Pre-registered hypotheses")
    w("")
    h1 = paired(df, "jcac_outage_1m", "hpa")
    w(f"- **CH-H1 (primary)** J(jcac_outage_1m) < J(hpa): mean {h1['mean']:+.4f}, "
      f"dz {h1['dz']:+.2f}, p {h1['p']:.2g} -> "
      f"**{'PASS' if h1['win'] else 'FAIL'}** — PolyForge with a dead planner "
      f"for 1 min {'still beats' if h1['win'] else 'does not beat'} a healthy HPA.")
    h2 = paired(df, "jcac_kill50", "hpa_kill50")
    w(f"- **CH-H2 (primary)** J(jcac_kill50) < J(hpa_kill50): mean {h2['mean']:+.4f}, "
      f"dz {h2['dz']:+.2f}, p {h2['p']:.2g} -> **{'PASS' if h2['win'] else 'FAIL'}**.")
    h3 = paired(df, "jcac_outage_3m", "jcac")
    w(f"- **CH-H3 (secondary, dose)** J(jcac_outage_3m) - J(jcac): mean "
      f"{h3['mean']:+.4f} (95% CI [{h3['ci'][0]:+.4f}, {h3['ci'][1]:+.4f}]), "
      f"dz {h3['dz']:+.2f}, p {h3['p']:.2g}. Directional expectation was "
      f"degradation > 0: {'consistent' if h3['mean'] > 0 else 'NOT consistent'}.")
    h3b = paired(df, "jcac_outage_3m", "hpa")
    w(f"  - jcac_outage_3m vs healthy hpa, no hypothesis, as measured: mean "
      f"{h3b['mean']:+.4f}, dz {h3b['dz']:+.2f}, p {h3b['p']:.2g} "
      f"({'jcac_outage_3m ahead' if h3b['mean'] < 0 else 'hpa ahead'}).")
    d_jcac = paired(df, "jcac_outage_1m", "jcac")
    d_hpa = paired(df, "hpa_outage_1m", "hpa")
    w(f"- **CH-H4 (secondary, centralization tax, descriptive)** 1-min freeze "
      f"costs jcac dJ = {d_jcac['mean']:+.4f} (95% CI [{d_jcac['ci'][0]:+.4f}, "
      f"{d_jcac['ci'][1]:+.4f}]) vs hpa dJ = {d_hpa['mean']:+.4f} "
      f"(95% CI [{d_hpa['ci'][0]:+.4f}, {d_hpa['ci'][1]:+.4f}]).")
    w("")

    w("## Per-arm dose table (J vs own control, paired; descriptive)")
    w("")
    w("| arm | control | mean dJ | dz | p | 95% CI |")
    w("|---|---|---|---|---|---|")
    for arm, (control, _) in ARMS.items():
        r = paired(df, arm, control)
        w(f"| {arm} | {control} | {r['mean']:+.4f} | {r['dz']:+.2f} | {r['p']:.2g} "
          f"| [{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}] |")
    w("")

    w("## Recovery time (frozen metric: steps after window end until 3-step")
    w("rolling mean violation is within 0.02 of own control; descriptive)")
    w("")
    workloads = sorted(df.workload.unique())
    w("| arm | " + " | ".join(workloads) + " |")
    w("|---" * (len(workloads) + 1) + "|")
    for arm, (control, start) in ARMS.items():
        cells = [recovery_steps(traces, arm, control, wl, start) for wl in workloads]
        w(f"| {arm} | " + " | ".join(cells) + " |")
    w("")

    w("## Context: mean metrics per system (descriptive)")
    w("")
    agg = df.groupby("system")[["J", "total_cost_usd", "mean_violation", "mean_jain"]].mean()
    w("| system | J | cost | violation | Jain |")
    w("|---|---|---|---|---|")
    for system in agg.sort_values("J").index:
        a = agg.loc[system]
        w(f"| {system} | {a.J:.4f} | {a.total_cost_usd:.3f} | "
          f"{a.mean_violation:.4f} | {a.mean_jain:.4f} |")
    w("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}: H1 {'PASS' if h1['win'] else 'FAIL'}, "
          f"H2 {'PASS' if h2['win'] else 'FAIL'}")


if __name__ == "__main__":
    main()
