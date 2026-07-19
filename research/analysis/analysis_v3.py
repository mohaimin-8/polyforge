"""v3 analysis: RESULTS_V3.md from the pre-registered overload matrix.

Executes the confirmatory analysis frozen in PREREG_V3.md (committed in the
same commit as this file, before any v3 run): H1' (SLO segment vs HPA/KEDA
at equal-or-lower cost, pooled over the overload matrix), H2' (mechanism
attribution vs v1 jcac — same knobs, different forecaster), and the
declared exploratory tables E1-E4. RESULTS.md and RESULTS_V2.md are never
touched; v3 numbers are reported alongside, never instead.
"""

from __future__ import annotations



import pandas as pd

import stats
from analysis_v2 import paired
from run_analysis import METRIC_LABELS, md_table

V3_DB = stats.REPO_ROOT / "eval" / "results" / "raw_sim_v3.duckdb"
OUT = stats.record_path("RESULTS_V3.md")

TREATMENT = "jcac_seasonal"
MECHANISM_CONTROL = "jcac"
H1_BASELINES = ["hpa", "keda"]
V3_BASELINES = ["hpa", "keda", "firm"]
V3_ORDER = ["jcac_seasonal", "jcac", "hpa", "keda", "firm"]
CLASSES = ["flash_crud", "flash_ai", "spike_agentic", "ramp_gentle"]

LABELS = {
    "jcac_seasonal": "PolyForge (seasonal)",
    "jcac": "PolyForge (v1 trend)",
    "hpa": "HPA",
    "keda": "KEDA",
    "firm": "FIRM",
}


def h1_section(df: pd.DataFrame, w) -> bool:
    """PREREG_V3 §4 H1': violation significantly below HPA and KEDA
    (paired p<0.01) at cost point-estimate <= 0. Same bar v2's H1 failed."""
    w("## H1′ (confirmatory, PREREG_V3 §4) — the SLO segment in the overload regime")
    w("")
    w("`jcac_seasonal` must show SLO violation significantly below **HPA** and "
      "**KEDA** (paired by cell, p < 0.01) with the paired cost point estimate "
      "≤ 0 — the identical bar the v2 H1 failed on the non-overload matrix.")
    w("")
    rows, ok_all = [], True
    for baseline in H1_BASELINES:
        slo = paired(df, TREATMENT, baseline, "mean_violation")
        cost = paired(df, TREATMENT, baseline, "total_cost_usd")
        slo_ok = slo["mean_diff"] < 0 and slo["p"] < 0.01
        cost_ok = cost["mean_diff"] <= 0
        ok = slo_ok and cost_ok
        ok_all &= ok
        # Ground rule 2: "up to" only ever next to its mean.
        base_mean = float(df[df.system == baseline].mean_violation.mean())
        rel_mean = -slo["mean_diff"] / base_mean if base_mean else 0.0
        cell = df[df.system == baseline].merge(
            df[df.system == TREATMENT], on=stats.CELL_KEYS, suffixes=("_b", "_t"))
        cell_rel = ((cell.mean_violation_b - cell.mean_violation_t)
                    / cell.mean_violation_b.replace(0.0, float("nan"))).dropna()
        up_to = float(cell_rel.max()) if len(cell_rel) else 0.0
        rows.append({
            "baseline": LABELS[baseline], "pairs": slo["pairs"],
            "violation diff (seas−base)": slo["mean_diff"], "p": slo["p"],
            "d_z": slo["dz"],
            "violation cut mean / up to": f"{100*rel_mean:.1f}% / {100*up_to:.1f}%",
            "cost diff (USD)": cost["mean_diff"],
            "verdict": "PASS" if ok else
                       f"FAIL ({'SLO' if not slo_ok else 'cost'})",
        })
    w(md_table(pd.DataFrame(rows)))
    w("")
    w(f"**H1′: {'PASS' if ok_all else 'FAIL'}** — reported as measured; the "
      "stopping rule (PREREG_V3 §6) forbids re-running or widening either way.")
    w("")
    return ok_all


def h2_section(df: pd.DataFrame, w) -> bool:
    """PREREG_V3 §4 H2': the forecaster is the mechanism — violation below
    v1 jcac (p<0.01) with no large-effect cost regression."""
    w("## H2′ (confirmatory, PREREG_V3 §4) — mechanism attribution vs v1 `jcac`")
    w("")
    w("Both arms share every knob, weight, and guardrail; they differ only in "
      "the forecaster. A PASS attributes the H1′ result to forecasting rather "
      "than to the joint knobs the baselines lack.")
    w("")
    slo = paired(df, TREATMENT, MECHANISM_CONTROL, "mean_violation")
    cost = paired(df, TREATMENT, MECHANISM_CONTROL, "total_cost_usd")
    slo_ok = slo["mean_diff"] < 0 and slo["p"] < 0.01
    cost_regressed = (cost["mean_diff"] > 0 and cost["p"] < 0.01
                      and abs(cost["dz"]) >= 0.5)
    ok = slo_ok and not cost_regressed
    rows = [{
        "metric": METRIC_LABELS[m],
        "seasonal mean": float(df[df.system == TREATMENT][m].mean()),
        "trend mean": float(df[df.system == MECHANISM_CONTROL][m].mean()),
        "diff (seas−trend)": (r := paired(df, TREATMENT, MECHANISM_CONTROL, m))["mean_diff"],
        "p": r["p"], "d_z": r["dz"],
    } for m in stats.METRICS]
    w(md_table(pd.DataFrame(rows)))
    w("")
    w(f"- violation below trend at p<0.01: **{'yes' if slo_ok else 'NO'}** "
      f"(p={slo['p']:.3g}, d_z={slo['dz']:.3f})")
    w(f"- large-effect cost regression: **{'YES' if cost_regressed else 'no'}** "
      f"(diff={cost['mean_diff']:+.3f} USD, p={cost['p']:.3g}, d_z={cost['dz']:.3f})")
    w("")
    w(f"**H2′: {'PASS' if ok else 'FAIL'}** — reported as measured.")
    w("")
    return ok


def e1_per_class(df: pd.DataFrame, w) -> None:
    w("## E1 (exploratory, declared) — per-class breakdown")
    w("")
    w("Prereg prediction: wins concentrated in `flash_*`/`spike_*`, tie on the "
      "`ramp_gentle` control cell — the specificity check that the effect "
      "lives exactly where actuation binds.")
    w("")
    rows = []
    for cls in CLASSES:
        sub = df[df.workload == cls]
        for baseline in H1_BASELINES + [MECHANISM_CONTROL]:
            slo = paired(sub, TREATMENT, baseline, "mean_violation")
            cost = paired(sub, TREATMENT, baseline, "total_cost_usd")
            rows.append({
                "class": cls, "vs": LABELS[baseline], "pairs": slo["pairs"],
                "violation diff": slo["mean_diff"], "p": slo["p"], "d_z": slo["dz"],
                "cost diff (USD)": cost["mean_diff"],
            })
    w(md_table(pd.DataFrame(rows)))
    w("")


def e2_keda_cost(df: pd.DataFrame, w) -> None:
    w("## E2 (exploratory, declared) — KEDA's posture on flash cells")
    w("")
    rows = []
    for cls in CLASSES:
        sub = df[df.workload == cls]
        keda = float(sub[sub.system == "keda"].total_cost_usd.mean())
        seas = float(sub[sub.system == TREATMENT].total_cost_usd.mean())
        rows.append({"class": cls, "KEDA cost (USD/run)": keda,
                     "seasonal cost (USD/run)": seas,
                     "ratio": keda / seas if seas else float("nan")})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("Prereg prediction: tuned KEDA (2 rps/replica) pins near replica_max on "
      "high-rps classes — SLO bought by permanent overspend, the v2 iso-cost "
      "finding recurring in the overload regime.")
    w("")


def e4_composite(df: pd.DataFrame, w) -> None:
    w("## E4 (exploratory, declared) — composite objective J (paired by cell)")
    w("")
    frame = df.assign(J=stats.composite_objective(df))
    t_vals = frame[frame.system == TREATMENT]
    rows = []
    for baseline in V3_BASELINES + [MECHANISM_CONTROL]:
        merged = t_vals.merge(frame[frame.system == baseline],
                              on=stats.CELL_KEYS, suffixes=("_t", "_b"))
        diff = merged.J_t - merged.J_b
        from scipy import stats as sps
        _, p = sps.ttest_1samp(diff, 0.0)
        rows.append({
            "baseline": LABELS[baseline], "pairs": len(merged),
            "J_seasonal": float(merged.J_t.mean()),
            "J_baseline": float(merged.J_b.mean()),
            "p": float(p), "cohens_dz": float(diff.mean() / diff.std(ddof=1)),
        })
    w(md_table(pd.DataFrame(rows)))
    w("")


def main() -> None:
    df = stats.load_runs(V3_DB)
    lines: list[str] = []
    w = lines.append

    w("# v3 statistical analysis — overload matrix (pre-registered)")
    w("")
    w(f"Source: `eval/results/{V3_DB.name}` ({len(df)} valid runs), sim backend, "
      "experiment `matrix_v3_overload` (1,200 planned runs). Protocol, cell "
      "definitions, and hypotheses frozen in `PREREG_V3.md` **before** the first "
      "run; analysis executed once (stopping rule §6). RESULTS.md (v1) and "
      "RESULTS_V2.md are immutable and reported alongside, never replaced. "
      "Regenerate: `python run_analysis.py`.")
    w("")

    w("## Per-system summary (mean [95% CI] over all overload-matrix cells)")
    w("")
    summary = stats.system_summary(df)
    rows = []
    for system in V3_ORDER:
        if system not in summary.index:
            continue
        r = summary.loc[system]
        row = {"system": LABELS[system]}
        for m, label in METRIC_LABELS.items():
            row[label] = f"{r[m]:.4g} [{r[f'{m}_lo']:.4g}, {r[f'{m}_hi']:.4g}]"
        rows.append(row)
    w(md_table(pd.DataFrame(rows)))
    w("")

    h1 = h1_section(df, w)
    h2 = h2_section(df, w)

    w("## Per-metric: seasonal vs each v3 baseline (paired by matrix cell)")
    w("")
    head = stats.headline_comparison(df, treatment=TREATMENT, baselines=V3_BASELINES)
    pretty = head.assign(
        metric=head.metric.map(METRIC_LABELS),
        baseline=head.baseline.map(lambda s: LABELS.get(s, s)),
        verdict=head.apply(
            lambda r: ("**better, p<0.01, |dz|≥0.5**" if r.significant_large
                       else ("better" if r.jcac_better else "worse")), axis=1),
    ).rename(columns={"jcac_mean": "seasonal_mean"})[
        ["baseline", "metric", "seasonal_mean", "baseline_mean", "p", "cohens_dz",
         "pooled_p", "pooled_d", "verdict"]]
    w(md_table(pretty))
    w("")

    e1_per_class(df, w)
    e2_keda_cost(df, w)
    e4_composite(df, w)

    w("## Verdict summary")
    w("")
    w(f"- H1′ (SLO segment vs HPA/KEDA at ≤ cost, overload regime): "
      f"**{'PASS' if h1 else 'FAIL'}**")
    w(f"- H2′ (mechanism: forecaster, not knobs): **{'PASS' if h2 else 'FAIL'}**")
    if h1 and h2:
        w("- Claim earned: *under demand that outruns reactive scaling, "
          "proactive period-aware control cuts SLO violations at lower cost; "
          "elsewhere it matches attainment at ~half the cost (RESULTS_V2.md).*")
    else:
        w("- The v2 iso-attainment framing (parity at −43…−48% cost) remains "
          "the SLO segment's claim; this attempt is closed (PREREG_V3 §4).")
    w("")
    w("## Notes")
    w("")
    w("- Systems, tuning, engine semantics identical to v1/v2 headline "
      "(no transition costs, no interference); only the demand regime is new.")
    w("- `jcac_seasonal` runs its trend fallback until the period detector "
      "locks (≈2 cycles), so run-mean numbers *underestimate* steady-state "
      "benefit (PREREG_V3 §3, dilution (c)).")
    w("- On `spike_agentic`, part of HPA/KEDA's violation is their fixed "
      "small tier on agent traffic (disclosed in PREREG_V3 §3); the "
      "forecast-only attribution is H2′, where both arms have all knobs.")
    w("- Latency metrics are p95, not p99 (documented deviation, eval/README.md).")
    w("- Sim-backend decision quality, not live-cluster absolutes (ground rule 4).")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
