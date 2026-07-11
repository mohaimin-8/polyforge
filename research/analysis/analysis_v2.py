"""v2 analysis: RESULTS_V2.md from the pre-registered v2 headline matrix.

Everything here executes the confirmatory analysis frozen in PREREG_V2.md
(committed before any v2 run): H1 (SLO segment vs HPA/KEDA at equal-or-
lower cost), H2 (no self-harm vs v1 jcac), the composite objective, the
full per-metric table, and the v1-style gate restated for jcac_v2.
Gate v2 (iso-cost baselines) is emitted only once the Phase 2 delta cells
exist in eval/results/isocost.duckdb.

RESULTS.md and the v1 database are never touched.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from scipy import stats as sps

import figures
import stats
from run_analysis import METRIC_LABELS, md_table

V2_DB = stats.REPO_ROOT / "eval" / "results" / "raw_sim_v2.duckdb"
ISOCOST_DB = stats.REPO_ROOT / "eval" / "results" / "isocost.duckdb"
OUT = Path(__file__).resolve().parent / "RESULTS_V2.md"

TREATMENT = "jcac_v2"
V1 = "jcac"
H1_BASELINES = ["hpa", "keda"]
ISOCOST_BASELINES = ["hpa", "keda", "firm", "static_isocost", "cache_isocost"]

LABELS = {
    **figures.SYSTEM_LABELS,
    "jcac_v2": "PolyForge v2",
    "static_isocost": "Static (iso-cost)",
    "cache_isocost": "Cache-max (iso-cost)",
}
V2_ORDER = ["jcac_v2", "jcac", "hpa", "keda", "firm", "static", "gptcache"]


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    """Paired-by-cell test of system a vs system b on one metric.
    Returns mean_diff (a−b), p, d_z, pairs, and per-cell diffs."""
    merged = df[df.system == a].merge(
        df[df.system == b], on=stats.CELL_KEYS, suffixes=("_a", "_b")
    )
    diff = merged[f"{metric}_a"] - merged[f"{metric}_b"]
    sd = diff.std(ddof=1)
    if len(diff) < 2 or sd == 0.0:
        return {"mean_diff": float(diff.mean()) if len(diff) else 0.0,
                "p": 1.0, "dz": 0.0, "pairs": len(diff), "diff": diff}
    _, p = sps.ttest_1samp(diff, 0.0)
    return {"mean_diff": float(diff.mean()), "p": float(p),
            "dz": float(diff.mean() / sd), "pairs": len(diff), "diff": diff}


def h1_section(df: pd.DataFrame, w) -> bool:
    """PREREG §4 H1: SLO violation significantly below HPA and KEDA
    (paired p<0.01) at cost point-estimate ≤ 0. No escape hatch on cost."""
    w("## H1 (confirmatory, PREREG_V2 §4) — the SLO segment")
    w("")
    w("`jcac_v2` must show SLO violation significantly below **HPA** and "
      "**KEDA** (paired by cell, p < 0.01) with the paired cost point "
      "estimate ≤ 0 — a cost tie does not rescue a violation loss and a "
      "violation win does not excuse spending more.")
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
            "violation diff (v2−base)": slo["mean_diff"], "p": slo["p"],
            "d_z": slo["dz"],
            "violation cut mean / up to": f"{100*rel_mean:.1f}% / {100*up_to:.1f}%",
            "cost diff (USD)": cost["mean_diff"],
            "verdict": "PASS" if ok else
                       f"FAIL ({'SLO' if not slo_ok else 'cost'})",
        })
    w(md_table(pd.DataFrame(rows)))
    w("")
    w(f"**H1: {'PASS' if ok_all else 'FAIL'}** — reported as measured; the "
      "stopping rule (PREREG_V2 §6) forbids re-running or re-tuning either way.")
    w("")
    return ok_all


def h2_section(df: pd.DataFrame, w) -> bool:
    """PREREG §4 H2: any large-effect regression vs v1 jcac is reported;
    SLO-violation or cost regression at p<0.01, |d_z|≥0.5 blocks promotion."""
    w("## H2 (confirmatory, PREREG_V2 §4) — no self-harm vs v1 `jcac`")
    w("")
    w("Paired per cell within this matrix (both systems reran under "
      "matrix_v2 seeds; the immutable v1 database is not reused).")
    w("")
    rows, blockers, regressions = [], [], []
    for m in stats.METRICS:
        r = paired(df, TREATMENT, V1, m)
        better = r["mean_diff"] * stats.BETTER[m] > 0
        regressed = (not better and r["mean_diff"] != 0.0
                     and r["p"] < 0.01 and abs(r["dz"]) >= 0.5)
        if regressed:
            regressions.append(m)
            if m in ("mean_violation", "total_cost_usd"):
                blockers.append(m)
        rows.append({
            "metric": METRIC_LABELS[m],
            "v2 mean": float(df[df.system == TREATMENT][m].mean()),
            "v1 mean": float(df[df.system == V1][m].mean()),
            "diff (v2−v1)": r["mean_diff"], "p": r["p"], "d_z": r["dz"],
            "direction": "better" if better else ("tie" if r["mean_diff"] == 0.0 else "worse"),
            "large-effect regression": "YES" if regressed else "no",
        })
    w(md_table(pd.DataFrame(rows)))
    w("")
    if blockers:
        w(f"**H2: FAIL** — large-effect regression on "
          f"{', '.join(METRIC_LABELS[m] for m in blockers)}: `jcac_v2` is "
          "**not promoted**; v1 numbers stand (PREREG_V2 §4).")
    elif regressions:
        w(f"**H2: PASS with disclosure** — large-effect regression on "
          f"{', '.join(METRIC_LABELS[m] for m in regressions)} (not an SLO/cost "
          "blocker, disclosed prominently per PREREG_V2).")
    else:
        w("**H2: PASS** — no large-effect regression on any metric.")
    w("")
    return not blockers


def gate_section(df: pd.DataFrame, w, treatment: str, baselines: list[str],
                 title: str, note: str) -> None:
    head = stats.headline_comparison(df, treatment=treatment, baselines=baselines)
    pretty = head.assign(
        metric=head.metric.map(METRIC_LABELS),
        baseline=head.baseline.map(lambda s: LABELS.get(s, s)),
        verdict=head.apply(
            lambda r: ("**better, p<0.01, |dz|≥0.5**" if r.significant_large
                       else ("better" if r.jcac_better else "worse")), axis=1),
    ).rename(columns={"jcac_mean": "v2_mean"})[
        ["baseline", "metric", "v2_mean", "baseline_mean", "p", "cohens_dz",
         "pooled_p", "pooled_d", "verdict"]]
    w(f"## {title}")
    w("")
    w(note)
    w("")
    w(md_table(pretty))
    w("")
    gate_ok = True
    for baseline in baselines:
        sub = head[head.baseline == baseline]
        wins = int(sub.significant_large.sum())
        ok = wins >= 3
        gate_ok &= ok
        w(f"- vs **{LABELS.get(baseline, baseline)}**: significant large-effect "
          f"wins on {wins}/5 metrics — {'PASS' if ok else 'FAIL'}")
    w("")
    w(f"**Gate ({'PASS' if gate_ok else 'FAIL'})**: ≥ 3 of 5 metrics at "
      "p < 0.01 with |d_z| ≥ 0.5 (paired-by-cell) against every baseline "
      "in this set — reported as measured either way.")
    w("")


def main() -> None:
    df = stats.load_runs(V2_DB)
    lines: list[str] = []
    w = lines.append

    w("# v2 statistical analysis — results (pre-registered)")
    w("")
    w(f"Source: `eval/results/{V2_DB.name}` ({len(df)} valid runs), sim "
      "backend, experiment `matrix_v2` (2,100 planned runs). Protocol frozen "
      "in `PREREG_V2.md` **before** the first run; analysis executed once "
      "(stopping rule §6). v1 results (`RESULTS.md`) are immutable and "
      "reported alongside, never replaced. Regenerate: `python run_analysis.py`.")
    w("")

    w("## Per-system summary (mean [95% CI] over all matrix cells)")
    w("")
    summary = stats.system_summary(df)
    rows = []
    for system in V2_ORDER:
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

    w("## Composite objective (paired by matrix cell)")
    w("")
    w("J = cost_norm + 2·violation + 0.5·(1−Jain) — the objective every "
      "baseline was tuned on (TUNING.md). Lower is better.")
    w("")
    obj = stats.objective_comparison(df, treatment=TREATMENT)
    w(md_table(obj.assign(baseline=obj.baseline.map(lambda s: LABELS.get(s, s)))
               .rename(columns={"J_jcac": "J_v2"})))
    w("")

    gate_section(
        df, w, TREATMENT, stats.BASELINES,
        "Per-metric: PolyForge v2 vs each v1 baseline (paired by matrix cell)",
        "The v1 gate definition applied to `jcac_v2`, against the original "
        "(non-iso-cost) baseline set. The v1 structural impossibility "
        "documented in RESULTS.md applies here identically: `static` and "
        "`gptcache` buy their metric wins with 12–30× spend. Gate v2 below "
        "removes exactly that confound; this section exists so v1 and v2 "
        "are always shown side by side.")

    df_iso = None
    if ISOCOST_DB.exists():
        try:
            df_iso = stats.load_runs(ISOCOST_DB)
        except Exception:
            df_iso = None  # still being written; report pending
    iso_complete = (
        df_iso is not None
        and (df_iso.system == "static_isocost").sum() >= 300
        and (df_iso.system == "cache_isocost").sum() >= 300
    )
    if iso_complete:
        combined = pd.concat([df, df_iso[df_iso.system.isin(
            {"static_isocost", "cache_isocost"})]], ignore_index=True)
        gate_section(
            combined, w, TREATMENT, ISOCOST_BASELINES,
            "Gate v2 — all baselines iso-cost (PREREG_V2 §5)",
            "`static_isocost` and `cache_isocost` are budget-matched to "
            "`jcac_v2`'s realized per-cell spend (ceiling rounding — the "
            "baseline gets at least PolyForge's budget; strict against "
            "PolyForge; in 36/60 cells the pinned posture outspends the "
            "whole budget even at 1 replica, see isocost_budgets.yaml). "
            "HPA/KEDA/FIRM are unchanged and tuned (TUNING.md). Reported "
            "alongside the v1 gate, never instead.")
    else:
        w("## Gate v2 — all baselines iso-cost (PREREG_V2 §5)")
        w("")
        w("Pending: Phase 2 delta cells (`eval/results/isocost.duckdb`) are "
          "not complete yet. Definition frozen in PREREG_V2 §5.")
        w("")

    w("## Verdict summary")
    w("")
    w(f"- H1 (SLO segment vs HPA/KEDA at ≤ cost): **{'PASS' if h1 else 'FAIL'}**")
    w(f"- H2 (no self-harm vs v1): **{'PASS' if h2 else 'FAIL'}**")
    w(f"- Headline system for v2 claims: **{'jcac_v2' if (h1 and h2) else 'jcac (v1 stands)'}**")
    w("")
    w("## Notes")
    w("")
    w("- Same blocked factorial design, metrics, pairing, and thresholds as "
      "v1 (RESULTS.md); the only new system is the pre-registered `jcac_v2`.")
    w("- Latency metrics are p95, not p99 (documented deviation, eval/README.md).")
    w("- Sim-backend decision quality, not live-cluster absolutes; real-trace "
      "replays land in Phase 3 and are reported on their own substrate, "
      "never mixed into these tables (ground rule 4).")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
