"""W36 entry point: statistics + figures + RESULTS.md in one command.

    cd research/analysis && python run_analysis.py

Deliberate deviation from the week plan: the roadmap names an
analysis.ipynb; this is a script package instead (stats.py, figures.py,
this driver) because scripts diff, review, and re-run deterministically in
CI where notebooks don't. Everything the notebook would show is in
RESULTS.md and eval/results/figures/.
"""

from __future__ import annotations

import pandas as pd

import figures
import stats

OUT = stats.record_path("RESULTS.md")

METRIC_LABELS = {
    "total_cost_usd": "cost (USD/run)",
    "mean_violation": "SLO violation",
    "violation_step_share": "violation incidence",
    "mean_jain": "Jain fairness",
    "cache_hit_rate": "cache hit rate",
}


def md_table(df: pd.DataFrame, floatfmt: str = "{:.4g}") -> str:
    df = df.copy()
    for col in df.columns:
        if df[col].dtype.kind == "f":
            df[col] = df[col].map(lambda v: floatfmt.format(v))
    header = "| " + " | ".join(df.columns) + " |"
    sep = "|" + "|".join(["---"] * len(df.columns)) + "|"
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([header, sep] + rows)


def undominated(summary: pd.DataFrame, systems: list[str]) -> list[str]:
    """Systems no other system beats on BOTH mean cost and mean violation
    (at least as good on both, strictly better on one)."""
    pts = {s: (summary.loc[s, "total_cost_usd"], summary.loc[s, "mean_violation"]) for s in systems}
    return [s for s, (c, v) in pts.items()
            if not any(oc <= c and ov <= v and (oc < c or ov < v)
                       for o, (oc, ov) in pts.items() if o != s)]


def gate_fail_reading(summary: pd.DataFrame, obj: pd.DataFrame, head: pd.DataFrame) -> str:
    """Why the per-metric gate fails, with every number computed (audit
    2026-09-26: this paragraph was fixed text the gate could not check --
    "~12x", "~30x", "all p < 1e-24", "the only Pareto-undominated system")."""
    cost = summary["total_cost_usd"]
    static_x = cost["static"] / cost["jcac"]
    cache_x = cost["gptcache"] / cost["jcac"]
    cost_rows = head[head.metric == "total_cost_usd"]
    cost_all = bool(cost_rows.jcac_better.all())
    dz = cost_rows.cohens_dz.abs()
    front = undominated(summary, [s for s in figures.SYSTEM_ORDER if s in summary.index])
    front_txt = ("the only system on the (cost, violation) Pareto front" if front == ["jcac"]
                 else "on the (cost, violation) Pareto front together with "
                 + ", ".join(f"`{s}`" for s in front if s != "jcac") if "jcac" in front
                 else "NOT on the (cost, violation) Pareto front (front: "
                 + ", ".join(f"`{s}`" for s in front) + ")")
    return ("Why the raw per-metric gate cannot pass against this baseline set: `static` is "
            f"over-provisioned to peak, so it wins every SLO-shaped metric by construction while "
            f"paying {static_x:.1f}× PolyForge's mean cost; `gptcache` maxes the cache, so it wins "
            f"hit rate while paying {cache_x:.1f}×. A system cannot out-violate a baseline that never "
            f"violates, only match it at lower cost -- the composite-objective sweep above (largest "
            f"p = {obj.p.max():.2g}, smallest |d_z| = {obj.cohens_dz.abs().min():.2f}). On cost "
            f"alone PolyForge is lower than {'every' if cost_all else 'NOT every'} baseline "
            f"(|d_z| {dz.min():.2f}–{dz.max():.2f}); on mean cost and mean violation it is "
            f"{front_txt}. These comparators carry the LRU charge and the 128 MB cache pin later "
            "adjudicated as unfair (RESULTS_EVICTION_PARITY.md). The gate row stays FAIL.")


def main() -> None:
    df = stats.load_runs(stats.FULL_DB)
    df_abl = stats.load_runs(stats.ABLATIONS_DB)
    lines: list[str] = []
    w = lines.append

    w("# W36 statistical analysis — results")
    w("")
    w(f"Source: `eval/results/raw_sim.duckdb` ({len(df)} valid runs) and "
      f"`eval/results/ablations.duckdb` ({len(df_abl)} valid runs), sim backend. "
      "Regenerate: `python run_analysis.py`.")
    w("")

    # --- per-system summary -------------------------------------------
    w("## Per-system summary (mean [95% CI] over all matrix cells)")
    w("")
    summary = stats.system_summary(df)
    rows = []
    for system in figures.SYSTEM_ORDER:
        r = summary.loc[system]
        row = {"system": figures.SYSTEM_LABELS[system]}
        for m, label in METRIC_LABELS.items():
            row[label] = f"{r[m]:.4g} [{r[f'{m}_lo']:.4g}, {r[f'{m}_hi']:.4g}]"
        rows.append(row)
    w(md_table(pd.DataFrame(rows)))
    w("")

    # --- ANOVA ---------------------------------------------------------
    w("## Two-way ANOVA (system × workload class)")
    w("")
    w("Type II SS on run-level values. Large system effects with p ≈ 0 are the "
      "licence for the pairwise comparisons below.")
    w("")
    anova_rows = []
    for m, label in METRIC_LABELS.items():
        a = stats.two_way_anova(df, m)
        for term, pretty in (("C(system)", "system"), ("C(workload)", "workload"),
                             ("C(system):C(workload)", "interaction")):
            anova_rows.append({
                "metric": label, "effect": pretty,
                "F": a[term]["F"], "p": a[term]["p"],
                "partial η²": a[term]["partial_eta_sq"],
            })
    w(md_table(pd.DataFrame(anova_rows)))
    w("")

    # --- primary claim: the composite objective --------------------------
    w("## Primary claim — composite objective (paired by matrix cell)")
    w("")
    w("J = cost_norm + 2·violation + 0.5·(1−Jain), the objective the paper "
      "states and every baseline was tuned on (TUNING.md). Lower is better.")
    w("")
    obj = stats.objective_comparison(df)
    obj_pretty = obj.assign(baseline=obj.baseline.map(figures.SYSTEM_LABELS))
    w(md_table(obj_pretty))
    w("")
    all_win = bool((obj.p < 0.01).all() and (obj.cohens_dz.abs() >= 0.5).all()
                   and (obj.J_jcac < obj.J_baseline).all())
    w(f"**PolyForge beats every tuned baseline on the stated objective "
      f"(all p < 0.01, all |d_z| ≥ 0.5): {'YES' if all_win else 'NO'}.**")
    w("")

    # --- per-metric: PolyForge vs each baseline ---------------------------
    w("## Per-metric: PolyForge vs each baseline (paired by matrix cell)")
    w("")
    w("The matrix is a blocked factorial design — every (workload, mix, "
      "cluster, rep) cell has exactly one run per system — so the test is a "
      "one-sample t on per-cell differences with Cohen's d_z. Pooled unpaired "
      "columns shown for transparency.")
    w("")
    head = stats.headline_comparison(df)
    pretty = head.assign(
        metric=head.metric.map(METRIC_LABELS),
        baseline=head.baseline.map(figures.SYSTEM_LABELS),
        verdict=head.apply(
            lambda r: ("**better, p<0.01, |dz|≥0.5**" if r.significant_large
                       else ("better" if r.jcac_better else "worse")), axis=1),
    )[["baseline", "metric", "jcac_mean", "baseline_mean", "p", "cohens_dz",
       "pooled_p", "pooled_d", "verdict"]]
    w(md_table(pretty))
    w("")

    # Roadmap gate: beats every baseline on >= 3 of 5 metrics with p<0.01, d>=0.5.
    w("### Roadmap verification gate")
    w("")
    gate_ok = True
    for baseline in stats.BASELINES:
        sub = head[head.baseline == baseline]
        wins = int(sub.significant_large.sum())
        ok = wins >= 3
        gate_ok &= ok
        w(f"- vs **{figures.SYSTEM_LABELS[baseline]}**: significant large-effect wins "
          f"on {wins}/5 metrics — {'PASS' if ok else 'FAIL'}")
    w("")
    w(f"**Gate ({'PASS' if gate_ok else 'FAIL'})**: PolyForge beats every baseline on "
      "≥ 3 of 5 metrics at p < 0.01 with |d_z| ≥ 0.5 (paired-by-cell)."
      + ("" if gate_ok else " — reported as measured; do not tune post hoc."))
    w("")
    if not gate_ok:
        w(gate_fail_reading(summary, obj, head))
        w("")

    # --- ablations -------------------------------------------------------
    w("## Ablations (full PolyForge vs minus-one-component)")
    w("")
    abl = stats.ablation_table(df_abl)
    pretty_abl = abl.assign(
        ablation=abl.ablation.map(figures.SYSTEM_LABELS),
        metric=abl.metric.map(METRIC_LABELS),
        change=abl.relative_change.map(lambda v: f"{100 * v:+.1f}%"),
    )[["ablation", "metric", "change", "p", "cohens_dz_removal", "removal_hurts"]]
    w(md_table(pretty_abl))
    w("")
    w("### Component verdicts")
    w("")
    for ablation in stats.ABLATIONS:
        sub = abl[(abl.ablation == ablation)]
        hurt = sub[sub.removal_hurts & sub.significant]
        w(f"- **{figures.SYSTEM_LABELS[ablation]}**: removal significantly (p<0.01) "
          f"degrades {len(hurt)}/5 metrics"
          + (f" ({', '.join(METRIC_LABELS[m] for m in hurt.metric)})" if len(hurt) else "")
          + ".")
    w("")
    w("## Figures")
    w("")
    w("12 publication figures (600-DPI PNG + vector PDF, color-blind-safe "
      "fixed-slot palette): `eval/results/figures/`, captions in "
      "`eval/results/figures/FIGURES.md`.")
    w("")
    w("## Notes and limitations")
    w("")
    w("- Latency metrics are **p95** (the model's estimator), not p99 — "
      "documented deviation, see eval/README.md.")
    w("- Runs are sim-backend: decision quality under a shared, stated system "
      "model (research/jcac_sim/model.py), not live-cluster absolutes. The "
      "cluster backend reproduces the schema for the cloud runs.")
    w("- Primary effect size is paired-by-cell d_z (blocked factorial design); "
      "pooled unpaired d is reported alongside and is smaller because "
      "between-cell variance (cost spans ~10x across cluster sizes) enters "
      "its denominator.")
    w("- The fairness ablation (γ=0) shows no significant sim-backend "
      "degradation: the synthetic workloads never inject the W32 "
      "noisy-neighbor interference signal the γ-term responds to, so its "
      "contribution is untested here, not refuted — it needs the cluster "
      "backend's eBPF-shaped signals (limitation for paper §9).")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")

    figures.main()

    # Advanced work (forecast ablation, realism, security) if its result
    # databases or committed exports are present; skipped cleanly otherwise.
    if stats.runs_available(stats.FORECASTERS_DB) and stats.runs_available(stats.REALISM_DB):
        import advanced

        advanced.main()
    else:
        print("advanced.py skipped: run forecasters.yaml + realism.yaml first")

    # v2 pre-registered analysis (PREREG_V2.md) once the v2 matrix exists.
    import analysis_v2

    if stats.runs_available(analysis_v2.V2_DB):
        analysis_v2.main()
    else:
        print("analysis_v2.py skipped: run experiments/matrix_v2.yaml first")

    # v2 Phase 4 fairness γ-ablation under interference injection.
    import fairness_v2

    if stats.runs_available(fairness_v2.DB):
        fairness_v2.main()
    else:
        print("fairness_v2.py skipped: run experiments/fairness_v2.yaml first")

    # v3 pre-registered overload-matrix analysis (PREREG_V3.md).
    import analysis_v3

    if stats.runs_available(analysis_v3.V3_DB):
        analysis_v3.main()
    else:
        print("analysis_v3.py skipped: run experiments/matrix_v3_overload.yaml first")


if __name__ == "__main__":
    main()
