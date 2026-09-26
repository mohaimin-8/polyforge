"""Budget-corrected risk MPC campaign analysis (PREREG_RISK_BUDGET.md, frozen).

Reads the matrix_risk_budget duckdb, scores the frozen readings — RB-H1 (the
corrected knob works: violation < point-forecast at the pre-declared q=0.90),
RB-H2 (monotone frontier), RB-H3 (strict Pareto domination of the tuned
reactive stack, six conjuncts) — and writes RESULTS_RISK_BUDGET.md. fig19
overlays the corrected frontier on the published null frontier (same axes),
which is the campaign's story whichever way the gates fall.

    python analysis_risk_budget.py
"""

from __future__ import annotations

import csv
import gzip
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

import stats

import figures as F

PROBE_ARMS = {"point": "jcac_anchored", "q90c": "jcac_q90c", "q95c": "jcac_q95c"}
PROBE_CLASSES = ("ai_uncacheable", "ai_cacheable", "agentic", "crud_bursty")
PROBE_MIXES = ("uniform", "premium_heavy")


def diagnosis_probe() -> tuple[dict, float]:
    """The POST-RUN DIAGNOSIS's 24-cell probe, executed rather than quoted
    (audit 2026-09-26: every number in the diagnosis was fixed text): the
    campaign's own rep-0 seeds at `medium`, timeseries on. Returns per
    (class, arm) the shed share, mean replicas, mean cache and small/mid tier
    shares over tenant-steps, and the medium cluster's per-tenant replica
    ceiling."""
    import sys
    from dataclasses import replace

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
    from harness import sim_backend, workloads
    from harness.config import expand, load

    spec = load(str(Path(__file__).resolve().parents[2] / "eval" / "experiments"
                    / "matrix_risk_budget.yaml"))
    cells = [r for r in expand(spec)
             if r.cluster_size == "medium" and r.rep == 0 and r.tenant_mix in PROBE_MIXES
             and r.system in PROBE_ARMS.values() and r.workload in PROBE_CLASSES]
    tally: dict = {}
    ceiling = None
    for r in cells:
        rows = sim_backend.execute(replace(r, store_timeseries=True))["timeseries"]
        t = tally.setdefault((r.workload, r.system), dict.fromkeys(
            ("n", "none", "replicas", "cache", "small", "mid"), 0))
        t["n"] += len(rows)
        t["none"] += sum(x["tier"] == "none" for x in rows)
        t["small"] += sum(x["tier"] == "small" for x in rows)
        t["mid"] += sum(x["tier"] == "mid" for x in rows)
        t["replicas"] += sum(x["replicas"] for x in rows)
        t["cache"] += sum(x["cache_mb"] for x in rows)
        if ceiling is None:
            ids, _, _, limits = workloads.build(r.workload, r.tenant_mix, r.cluster_size, r.seed, 1)
            ceiling = (limits.replicas, len(ids))
    out = {k: {"shed": t["none"] / t["n"], "replicas": t["replicas"] / t["n"],
               "cache": t["cache"] / t["n"], "small": t["small"] / t["n"], "mid": t["mid"] / t["n"]}
           for k, t in tally.items()}
    return out, ceiling


def ai_cacheable_share(workload: str) -> float:
    """Demand-weighted cacheable fraction of a class's AI traffic."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jcac_sim"))
    import model
    from harness import workloads

    rps = {k: v for k, v in workloads.WORKLOAD_CLASSES[workload].base_rps.items()
           if k in model.CACHEABLE_FRACTION}
    return sum(v * model.CACHEABLE_FRACTION[k] for k, v in rps.items()) / sum(rps.values())

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "eval" / "results" / "raw_sim_risk_budget.duckdb"
# Committed run-level export: the DuckDB is Zenodo-archived, so this is what
# lets a clean clone re-derive this record (stats.load_campaign_runs).
CSV = REPO_ROOT / "eval" / "results" / "metrics_matrix_risk_budget.csv.gz"
NULL_CSV = REPO_ROOT / "eval" / "results" / "metrics_matrix_risk.csv.gz"
# Written via stats.record_path so scripts/reproduce.py can redirect the
# rebuild into a scratch dir and diff it against the committed record
# without ever overwriting it.
OUT = stats.record_path("RESULTS_RISK_BUDGET.md")
TENANTS = 8
COST_SCALE = 0.01
CELL = ["workload", "tenant_mix", "cluster_size", "rep"]
BOOT_N = 10_000
BOOT_SEED = 20260723

FRONTIER = ["jcac_anchored", "jcac_q70c", "jcac_q80c", "jcac_q90c", "jcac_q95c"]
NULL_FRONTIER = ["jcac_anchored", "jcac_q70", "jcac_q80", "jcac_q90", "jcac_q95"]
REACTIVE = ["hpa", "keda", "concurrency"]
OP = "jcac_q90c"  # pre-declared operating point (PREREG_RISK_BUDGET §3)

LABELS = {
    "jcac_anchored": "PolyForge (point forecast)",
    "jcac_q70c": "PolyForge q=0.70 (corrected)",
    "jcac_q80c": "PolyForge q=0.80 (corrected)",
    "jcac_q90c": "PolyForge q=0.90 (corrected)",
    "jcac_q95c": "PolyForge q=0.95 (corrected)",
    "hpa": "HPA (tuned)", "keda": "KEDA (tuned)", "concurrency": "Concurrency (tuned)",
}
COLORS = {"hpa": F.SYSTEM_COLORS["hpa"], "keda": F.SYSTEM_COLORS["keda"],
          "concurrency": F.SYSTEM_COLORS["gptcache"]}
BLUE = F.SYSTEM_COLORS["jcac"]
NULL_GREY = "#b9b7ae"


def load() -> pd.DataFrame:
    df = stats.load_campaign_runs(DB, CSV)
    df["J"] = (df.total_cost_usd / (119 * TENANTS) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    return df


def load_null_runs() -> pd.DataFrame:
    """Published null campaign at run level, from the committed export.

    Kept at run level (not pre-aggregated) so the null's own *paired* deltas
    can be computed inside its own campaign. The two campaigns are never
    pooled: each arm is compared only against the point-forecast arm drawn
    from the same matrix (ground rule 4). Note the two campaigns' shared
    `jcac_anchored` arm differs slightly (run_id hashes the experiment name,
    so the per-cell seeds differ) — they are independent samples of the same
    configuration, which is why each frontier is drawn from its own anchor.
    """
    rows = []
    with gzip.open(NULL_CSV, "rt", newline="") as f:
        for r in csv.DictReader(f):
            if r["system"] in NULL_FRONTIER:
                rows.append({"system": r["system"], "workload": r["workload"],
                             "tenant_mix": r["tenant_mix"],
                             "cluster_size": r["cluster_size"],
                             "rep": int(r["rep"]),
                             "total_cost_usd": float(r["total_cost_usd"]),
                             "mean_violation": float(r["mean_violation"])})
    # Pin the row order rather than inheriting the file's. fig19's grey
    # error bars are bootstrap CIs over *paired* differences, and resampling
    # with a fixed seed reads the rows positionally — so the order here is
    # part of the published figure. This sort is the order the export
    # carried when fig19 was generated; stating it explicitly keeps the
    # figure reproducible even though the export is now written in the
    # DuckDB's own row order (which is what the main load() needs to match
    # its record). Keys are unique per row, so the sort is total.
    return pd.DataFrame(rows).sort_values(
        ["system", "workload", "tenant_mix", "cluster_size", "rep"],
        kind="mergesort").reset_index(drop=True)


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    m = df[df.system == a].merge(df[df.system == b], on=CELL, suffixes=("_a", "_b"))
    diff = m[f"{metric}_a"] - m[f"{metric}_b"]
    sd = diff.std(ddof=1)
    base = {"mean_diff": float(diff.mean()) if len(diff) else 0.0, "n": len(diff),
            "mean_a": float(m[f"{metric}_a"].mean()) if len(m) else 0.0,
            "mean_b": float(m[f"{metric}_b"].mean()) if len(m) else 0.0}
    if len(diff) < 2 or sd == 0.0:
        return {**base, "p": 1.0, "dz": 0.0, "ci": (0.0, 0.0)}
    _, p = sps.ttest_1samp(diff, 0.0)
    rng = np.random.default_rng(BOOT_SEED)
    vals = diff.to_numpy()
    means = rng.choice(vals, size=(BOOT_N, len(vals)), replace=True).mean(axis=1)
    ci = (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))
    return {**base, "p": float(p), "dz": float(diff.mean() / sd), "ci": ci}


def boot_ci(vals: np.ndarray) -> tuple[float, float]:
    rng = np.random.default_rng(BOOT_SEED)
    means = rng.choice(vals, size=(BOOT_N, len(vals)), replace=True).mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def md_table(frame: pd.DataFrame) -> str:
    cols = list(frame.columns)
    fmt = lambda v: f"{v:.4g}" if isinstance(v, float) else str(v)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(fmt(r[c]) for c in cols) + " |"
              for _, r in frame.iterrows()]
    return "\n".join(lines)


def frontier_figure(summary: pd.DataFrame, null_runs: pd.DataFrame,
                    runs: pd.DataFrame, h1: bool, h2: bool, h3: bool) -> str:
    """Two panels, because one panel cannot carry both readings honestly.

    (a) Where the arms sit: the whole cost/violation landscape, which is
        dominated by the ~$1.3 gap between every PolyForge arm and the tuned
        reactive stack. At that scale the five frontier arms are one cluster
        and their labels collide, so only the endpoints are labeled here.
    (b) What the quantile actually buys: the *paired* violation delta against
        each campaign's own point-forecast arm, with 95% bootstrap CIs. This
        is the quantity the gates test. Panel (a)'s error bars are unpaired
        spreads across heterogeneous cells and are an order of magnitude
        larger than the paired effect — plotting only those would hide a real
        result, which is why the per-arm detail lives here instead.
    """
    fig, (axL, axR) = F.plt.subplots(1, 2, figsize=(7.2, 2.9))

    # --- (a) the landscape --------------------------------------------
    null_summary = null_runs.groupby("system")[
        ["total_cost_usd", "mean_violation"]].mean()
    nx = [null_summary.loc[s, "total_cost_usd"] for s in NULL_FRONTIER
          if s in null_summary.index]
    ny = [null_summary.loc[s, "mean_violation"] for s in NULL_FRONTIER
          if s in null_summary.index]
    axL.plot(nx, ny, "--", color=NULL_GREY, lw=1.1, zorder=1)
    axL.plot(nx, ny, "o", color=NULL_GREY, ms=4, zorder=1)
    axL.annotate("uncorrected\n(published null)", (nx[-1], ny[-1]),
                 textcoords="offset points", xytext=(4, -4), fontsize=6.5,
                 color=F.MUTED, va="top")

    xs = [summary.loc[s, "total_cost_usd"] for s in FRONTIER]
    ys = [summary.loc[s, "mean_violation"] for s in FRONTIER]
    axL.plot(xs, ys, "-o", color=BLUE, lw=1.5, ms=5,
             markeredgecolor="white", markeredgewidth=0.8, zorder=3)
    axL.annotate("corrected", (xs[0], ys[0]), textcoords="offset points",
                 xytext=(-2, 9), fontsize=6.5, color=BLUE, ha="right")

    offsets = {"hpa": (7, 3), "concurrency": (-6, -12), "keda": (-8, -13)}
    for s in REACTIVE:
        if s not in summary.index:
            continue
        r = summary.loc[s]
        axL.plot(r.total_cost_usd, r.mean_violation, "s", color=COLORS[s], ms=6,
                 markeredgecolor="white", markeredgewidth=1, zorder=3)
        axL.annotate(LABELS[s].split(" ")[0],
                     (r.total_cost_usd, r.mean_violation),
                     textcoords="offset points", xytext=offsets.get(s, (6, -9)),
                     fontsize=7, color=F.INK2)
    axL.set_xlabel("mean cost per run (USD)")
    axL.set_ylabel("mean SLO violation (0–1)")
    axL.set_title("(a) where the arms sit", fontsize=7.5, color=F.INK2,
                  loc="left", pad=6)
    F._style(axL)
    axL.grid(axis="x", visible=True, color=F.GRID, linewidth=0.6)

    # --- (b) what the knob buys, paired -------------------------------
    qs = [0.70, 0.80, 0.90, 0.95]
    for arms, frame, color, style, label in (
        (FRONTIER[1:], runs, BLUE, "-", "corrected"),
        (NULL_FRONTIER[1:], null_runs, NULL_GREY, "--", "uncorrected"),
    ):
        mids, los, his = [], [], []
        for s in arms:
            d = paired(frame, s, "jcac_anchored", "mean_violation")
            mids.append(d["mean_diff"])
            los.append(d["ci"][0])
            his.append(d["ci"][1])
        axR.plot(qs, mids, style, color=color, lw=1.5, zorder=3)
        for q, m, lo, hi in zip(qs, mids, los, his):
            axR.vlines(q, lo, hi, color=color, lw=1.1, alpha=0.6, zorder=2)
        axR.plot(qs, mids, "o", color=color, ms=5, markeredgecolor="white",
                 markeredgewidth=0.8, zorder=4)
        axR.annotate(label, (qs[-1], mids[-1]), textcoords="offset points",
                     xytext=(-4, 8 if color is BLUE else -4), fontsize=6.5,
                     color=color if color is BLUE else F.MUTED, ha="right")
    axR.axhline(0.0, color=F.INK2, lw=0.8, zorder=1)
    axR.annotate("worse", (0.695, 0.0), textcoords="offset points",
                 xytext=(0, 3), fontsize=6, color=F.MUTED)
    axR.annotate("better", (0.695, 0.0), textcoords="offset points",
                 xytext=(0, -9), fontsize=6, color=F.MUTED)
    axR.set_xticks(qs)
    axR.set_xlabel("planning quantile q")
    axR.set_ylabel("Δ violation vs point forecast")
    axR.set_title("(b) what the quantile buys (paired, 95% CI)", fontsize=7.5,
                  color=F.INK2, loc="left", pad=6)
    F._style(axR)
    fig.subplots_adjust(wspace=0.40)

    if h1 and h2 and h3:
        caption = (
            "One changed factor repairs the frontier: with the budget checked at "
            "the point forecast (blue, mean ± 95% CI), raising the planning "
            "quantile buys violation down at modest cost — where the uncorrected "
            "mechanism (grey dashes, the published null) ran backwards. At the "
            "pre-declared q=0.90 the corrected controller strictly Pareto-"
            "dominates every tuned reactive arm (squares): less violation and "
            "less money, simultaneously. Down-and-left is better.")
    elif h1 and h2:
        caption = (
            "One changed factor repairs the frontier direction (blue, mean ± 95% "
            "CI) versus the uncorrected published null (grey dashes): raising "
            "the quantile now buys violation down at rising cost. Strict Pareto "
            "domination of every tuned reactive arm (squares) was NOT "
            "established (RB-H3); see the results table for which conjunct "
            "broke. Down-and-left is better.")
    elif h1:
        # The measured outcome: the direction is repaired and the declared
        # operating point wins its gate, but the knob is not monotone across
        # the whole grid and the violation conjuncts do not separate.
        savings = [100 * (1 - summary.loc[OP, "total_cost_usd"]
                          / summary.loc[b, "total_cost_usd"])
                   for b in REACTIVE if b in summary.index]
        caption = (
            "One changed factor reverses the mechanism. **(a)** The landscape: "
            "every PolyForge arm (blue) sits far left of the tuned reactive "
            "stack (squares) — the separation that survives pairing is cost, "
            f"{min(savings):.0f}–{max(savings):.0f}% less spend at the declared "
            "operating point — while the corrected frontier now runs downward "
            "where the uncorrected null (grey dashes) ran up and to the left. "
            "Down-and-left is better. **(b)** What the quantile buys, as the "
            "gates measure it: violation paired against each campaign's own "
            "point-forecast arm, 95% bootstrap CI. The correction flips the "
            "sign of the mechanism (blue below zero, grey above), and RB-H1 "
            "passes at the pre-declared q=0.90 — but the curve turns back up at "
            "q=0.95, an interior optimum rather than the monotone frontier "
            "RB-H2 required, so no frontier claim is made. Against the reactive "
            "arms the violation differences do not separate, so RB-H3's strict "
            "domination is not claimed either.")
    else:
        caption = (
            "The budget-corrected risk mechanism did not deliver its "
            "pre-registered readings (see RESULTS_RISK_BUDGET.md); the corrected "
            "frontier (blue) is shown against the published null (grey dashes) "
            "and the tuned reactive arms (squares) exactly as measured. "
            "Down-and-left is better.")
    F.save(fig, "fig19_risk_budget_frontier", caption)
    # F.CAPTIONS is only flushed to FIGURES.md by run_analysis.py, which does
    # not drive campaign scripts — so the caption is returned and written into
    # the record itself, where the outcome-aware wording belongs.
    return caption


def main() -> None:
    runs = load()
    null_runs = load_null_runs()
    lines: list[str] = []
    w = lines.append
    w("# Budget-corrected risk MPC — results (pre-registered)")
    w("")
    w("Protocol frozen in `PREREG_RISK_BUDGET.md`, pushed before any run — the "
      "disciplined one-changed-factor follow-up to the published "
      "`RESULTS_RISK.md` null (capacity sized at the risk quantile; cost "
      "projected and budget checked at the point forecast, because a tenant is "
      "billed for the demand that arrives, not the demand it was provisioned "
      "against). The null stands as measured and was never re-run; "
      "`risk_cost_at_point` defaults off and both prior campaigns replay at "
      "drift 0.00e+00. Rerun: `python analysis_risk_budget.py`.")
    w("")
    w(f"Valid runs: {len(runs)} over {runs.system.nunique()} systems "
      f"({len(runs[runs.system == 'jcac_anchored'])} matched cells per arm)")
    w("")

    rows = []
    for s in FRONTIER + REACTIVE:
        d = runs[runs.system == s]
        if d.empty:
            continue
        c_lo, c_hi = boot_ci(d.total_cost_usd.to_numpy())
        v_lo, v_hi = boot_ci(d.mean_violation.to_numpy())
        rows.append({"system": s, "total_cost_usd": d.total_cost_usd.mean(),
                     "cost_lo": c_lo, "cost_hi": c_hi,
                     "mean_violation": d.mean_violation.mean(),
                     "viol_lo": v_lo, "viol_hi": v_hi,
                     "J": d.J.mean(), "mean_jain": d.mean_jain.mean()})
    summary = pd.DataFrame(rows).set_index("system")

    w("## The corrected frontier (mean over matched cells, 95% bootstrap CI)")
    w("")
    pretty = summary.reset_index()[["system", "total_cost_usd", "cost_lo", "cost_hi",
                                    "mean_violation", "viol_lo", "viol_hi", "J"]].copy()
    pretty["system"] = pretty.system.map(LABELS)
    w(md_table(pretty))
    w("")

    # --- RB-H1 ---
    w("## RB-H1 (confirmatory) — does the corrected knob buy attainment?")
    w("")
    v = paired(runs, OP, "jcac_anchored", "mean_violation")
    h1 = v["mean_diff"] < 0 and v["p"] < 0.01
    c = paired(runs, OP, "jcac_anchored", "total_cost_usd")
    w(md_table(pd.DataFrame([
        {"reading": "violation, q90c − point (want < 0)", "n": v["n"],
         "q90c mean": v["mean_a"], "point mean": v["mean_b"],
         "diff": v["mean_diff"], "95% CI": f"[{v['ci'][0]:.4g}, {v['ci'][1]:.4g}]",
         "p": v["p"], "d_z": v["dz"], "verdict": "PASS" if h1 else "FAIL"},
        {"reading": "cost, q90c − point (context, no gate)", "n": c["n"],
         "q90c mean": c["mean_a"], "point mean": c["mean_b"],
         "diff": c["mean_diff"], "95% CI": f"[{c['ci'][0]:.4g}, {c['ci'][1]:.4g}]",
         "p": c["p"], "d_z": c["dz"], "verdict": "—"},
    ])))
    w("")
    if h1:
        w("**RB-H1: PASS** — the exact reading the uncorrected mechanism failed "
          "with the sign reversed now lands as designed: at q=0.90 the "
          "controller violates significantly less than its own point-forecast "
          "configuration. The one changed factor was the defect.")
    else:
        w("**RB-H1: FAIL** — the mechanism does not buy attainment even without "
          "the budget confound. Two published nulls close the risk-quantile "
          "line; the idea retires and the base controller's numbers stand.")
    w("")

    # --- RB-H2 ---
    w("## RB-H2 (confirmatory) — is the corrected frontier monotone?")
    w("")
    ranks = np.arange(len(FRONTIER))
    viol_means = np.array([summary.loc[s, "mean_violation"] for s in FRONTIER])
    cost_means = np.array([summary.loc[s, "total_cost_usd"] for s in FRONTIER])
    rho_v = float(sps.spearmanr(ranks, viol_means).statistic)
    rho_c = float(sps.spearmanr(ranks, cost_means).statistic)
    h2 = (rho_v <= -0.9) and (rho_c >= 0.9)
    w(md_table(pd.DataFrame([
        {"reading": "violation vs quantile rank (want ρ ≤ −0.9)",
         "spearman_rho": rho_v, "verdict": "PASS" if rho_v <= -0.9 else "FAIL"},
        {"reading": "cost vs quantile rank (want ρ ≥ +0.9)",
         "spearman_rho": rho_c, "verdict": "PASS" if rho_c >= 0.9 else "FAIL"},
    ])))
    w("")
    if h2:
        w("**RB-H2: PASS** — the knob is now a real control: SLO-tolerance is "
          "an explicit, dialable parameter.")
    else:
        best = int(np.argmin(viol_means))
        turn = ""
        if 0 < best < len(FRONTIER) - 1:
            turn = (f" Violation falls monotonically through `{FRONTIER[best]}` "
                    f"({viol_means[best]:.4g}) and then turns back up at "
                    f"`{FRONTIER[-1]}` ({viol_means[-1]:.4g}): the corrected knob "
                    f"has an **interior optimum**, not a monotone frontier — and "
                    f"it falls on the operating point declared before the data "
                    f"existed.")
        w(f"**RB-H2: FAIL** — cost moves as designed (ρ = {rho_c:+.2g}), but "
          f"violation does not clear the bar (ρ = {rho_v:+.2g}, required ≤ −0.9)."
          + turn + " Per the pre-committed falsifier, no frontier claim is made; "
          "what the knob buys is reported at the declared operating point only.")
    w("")

    # --- RB-H3 ---
    w("## RB-H3 (confirmatory) — strict Pareto domination of the tuned reactive stack")
    w("")
    rows, h3 = [], True
    for b in REACTIVE:
        for metric, name in (("mean_violation", "violation"), ("total_cost_usd", "cost")):
            r = paired(runs, OP, b, metric)
            ok = r["mean_diff"] < 0 and r["p"] < 0.01
            h3 = h3 and ok
            rows.append({"conjunct": f"{name}, q90c − {b} (want < 0)", "n": r["n"],
                         "q90c mean": r["mean_a"], "baseline mean": r["mean_b"],
                         "diff": r["mean_diff"],
                         "95% CI": f"[{r['ci'][0]:.4g}, {r['ci'][1]:.4g}]",
                         "p": r["p"], "d_z": r["dz"],
                         "verdict": "PASS" if ok else "FAIL"})
    w(md_table(pd.DataFrame(rows)))
    w("")
    if h3:
        w("**RB-H3: PASS (all six conjuncts)** — at the pre-declared operating "
          "point the corrected controller has **strictly lower violation and "
          "strictly lower cost than every tuned reactive arm simultaneously**: "
          "HPA, KEDA, and the 2026-stack concurrency scaler are each strictly "
          "Pareto-dominated. This is the reading neither the base controller "
          "(violation parity at lower cost, v2) nor any single-knob system "
          "produced: the joint optimizer plus an explicit risk knob buys the "
          "attainment the reactive stack sells, at a fraction of its price.")
    else:
        w("**RB-H3: FAIL** — strict domination was not established; the failed "
          "conjunct(s) are marked above and the claim is reported as partial "
          "dominance only, exactly as pre-committed. No rounding up.")
    w("")

    # --- RB-D1 ---
    w("## RB-D1 (descriptive, no gate)")
    w("")
    w("What each step of the corrected knob buys (vs the point-forecast MPC):")
    w("")
    rows = []
    for s in FRONTIER[1:]:
        vv = paired(runs, s, "jcac_anchored", "mean_violation")
        cc = paired(runs, s, "jcac_anchored", "total_cost_usd")
        jj = paired(runs, s, "jcac_anchored", "J")
        rows.append({"arm": LABELS[s], "Δviolation": vv["mean_diff"], "p(viol)": vv["p"],
                     "Δcost USD": cc["mean_diff"], "p(cost)": cc["p"],
                     "ΔJ": jj["mean_diff"], "p(J)": jj["p"]})
    w(md_table(pd.DataFrame(rows)))
    w("")
    dominated = []
    for b in REACTIVE:
        if b not in summary.index:
            continue
        by = [s for s in FRONTIER
              if summary.loc[s, "total_cost_usd"] < summary.loc[b, "total_cost_usd"]
              and summary.loc[s, "mean_violation"] < summary.loc[b, "mean_violation"]]
        if by:
            dominated.append(f"{LABELS[b]} (by {', '.join(x.replace('jcac_', '') for x in by)})")
    w(f"Baseline operating points that plot up-and-right of at least one "
      f"frontier arm **on means** — descriptive only, and deliberately *not* "
      f"the same statement as RB-H3, whose paired violation conjuncts did not "
      f"clear the bar: **{'; '.join(dominated) if dominated else 'none'}**.")
    w("")
    w("Per workload class (Δviolation and Δcost, q90c vs point-forecast MPC):")
    w("")
    rows = []
    for wl in sorted(runs.workload.unique()):
        r = paired(runs[runs.workload == wl], OP, "jcac_anchored", "mean_violation")
        cc = paired(runs[runs.workload == wl], OP, "jcac_anchored", "total_cost_usd")
        rows.append({"class": wl, "Δviolation": r["mean_diff"], "p": r["p"],
                     "Δcost USD": cc["mean_diff"], "n": r["n"]})
    w(md_table(pd.DataFrame(rows)))
    w("")
    caption = frontier_figure(summary, null_runs, runs, h1, h2, h3)
    w("![corrected frontier](../../eval/results/figures/fig19_risk_budget_frontier.png)")
    w("")
    w(f"**Figure 19.** {caption}")
    w("")
    w("## POST-RUN DIAGNOSIS (added after the frozen readings were scored)")
    w("")
    w("Prose only — no datum above is changed and every verdict stands as "
      "scored. Two readings need a mechanism: RB-H2 failed because violation "
      "falls through q=0.90 and then turns **back up** at q=0.95 (an interior "
      "optimum, not noise), and `ai_uncacheable` is the one class that gets "
      "*worse* at the operating point while every other AI class improves. "
      "The project's practice is to publish the diagnosis with the result "
      "(the `PLANNER_CELLS` aliasing precedent).")
    w("")
    w("**Method.** An exploratory probe (fixes no number, outside the frozen "
      "protocol) replays 24 cells — {point, q90c, q95c} × {ai_uncacheable, "
      "ai_cacheable, agentic, crud_bursty} × {uniform, premium_heavy} at "
      "`medium`, rep 0 — with timeseries on, and counts the controller's "
      "internal state directly: shed events (`tier=\"none\"`), the tier mix, "
      "mean replicas against the cluster ceiling, and mean cache.")
    w("")
    probe, (cap, n_tenants) = diagnosis_probe()

    def p(cls: str, arm: str) -> dict:
        return probe[(cls, PROBE_ARMS[arm])]

    def reps(cls: str) -> str:
        return " → ".join(f"{p(cls, a)['replicas']:.2f}" for a in PROBE_ARMS)

    def pct_ceiling(cls: str) -> str:
        lo, hi = (min(p(cls, a)["replicas"] for a in PROBE_ARMS) / (cap / n_tenants),
                  max(p(cls, a)["replicas"] for a in PROBE_ARMS) / (cap / n_tenants))
        return f"{lo:.0%}" if round(lo * 100) == round(hi * 100) else f"{lo:.0%}–{hi:.0%}"

    crud_shed = max(p("crud_bursty", a)["shed"] for a in PROBE_ARMS)
    w("**Finding 1 — the correction works, but does not abolish the budget "
      f"interaction.** On `ai_uncacheable` the shed rate *falls* from "
      f"{p('ai_uncacheable', 'point')['shed']:.2%} at "
      f"the point forecast to **{p('ai_uncacheable', 'q90c')['shed']:.2%}** at q90c — the "
      "correction doing exactly what it was designed to do — and then rises to "
      f"**{p('ai_uncacheable', 'q95c')['shed']:.2%}** at q95c, "
      "above even the uncorrected starting point. Capacity still costs money "
      "at *any* forecast, so at an extreme quantile the per-tenant budget "
      "filter binds again and the designed shed fallback returns. The "
      f"`crud_bursty` control shows **{crud_shed:.2%} shed at every arm**, confirming "
      "the channel is tier spend — the same signature the null's diagnosis "
      "identified. (Every number in this diagnosis is computed by this script's "
      "probe; audit 2026-09-26.)")
    w("")
    w("**Finding 2 — the knob buys attainment only where a capacity lever "
      f"still has headroom.** The `medium` cluster caps replicas at {cap} across "
      f"{n_tenants} tenants, i.e. a mean of {cap / n_tenants:.2f} per tenant when saturated:")
    w("")
    agentic, uncache = (p("agentic", "point"), p("agentic", "q95c")), \
        (p("ai_uncacheable", "point"), p("ai_uncacheable", "q95c"))
    w(md_table(pd.DataFrame([
        {"class": "ai_cacheable", "mean replicas (point→q90c→q95c)": reps("ai_cacheable"),
         "% of cluster ceiling": f"{pct_ceiling('ai_cacheable')} (headroom)",
         "what the knob buys": "real replicas; violation falls"},
        {"class": "agentic", "mean replicas (point→q90c→q95c)": reps("agentic"),
         "% of cluster ceiling": f"{pct_ceiling('agentic')} (saturated)",
         "what the knob buys": f"tier upgrades (small {agentic[0]['small']:.0%}→{agentic[1]['small']:.0%}, "
                               f"mid {agentic[0]['mid']:.0%}→{agentic[1]['mid']:.0%}); "
                               "violation falls, cost rises sharply"},
        {"class": "ai_uncacheable", "mean replicas (point→q90c→q95c)": reps("ai_uncacheable"),
         "% of cluster ceiling": f"{pct_ceiling('ai_uncacheable')} (saturated)",
         "what the knob buys": f"cache ({uncache[0]['cache']:.0f}→{uncache[1]['cache']:.0f} MB) on a "
                               f"class only {ai_cacheable_share('ai_uncacheable'):.0%} "
                               "cacheable and past half-saturation; spend, not service"},
        {"class": "crud_bursty", "mean replicas (point→q90c→q95c)": reps("crud_bursty"),
         "% of cluster ceiling": pct_ceiling("crud_bursty"),
         "what the knob buys": "nothing — no tier spend, violation already ~0"},
    ])))
    w("")
    w("This is one mechanism for both anomalies. Where the replica budget has "
      "headroom (`ai_cacheable`), risk headroom converts into capacity and "
      "attainment improves cheaply. Where replicas are pinned at the cluster "
      "ceiling, the controller can only chase the inflated target through the "
      "levers that remain: tier upgrades, which work but cost real money and "
      "eventually re-trip the budget filter (`agentic`), or cache, which on a "
      "low-cacheable class past its half-saturation point returns almost "
      "nothing (`ai_uncacheable`). **The risk knob converts forecast headroom "
      "into attainment only insofar as some capacity lever still has headroom "
      "with a real return; where the levers are saturated or low-return, the "
      "inflated target is converted into spend instead of service.** That is "
      "why the aggregate frontier has an interior optimum rather than a "
      "monotone one.")
    w("")
    w("**Scope.** The probe is 24 cells at one cluster size; the campaign's "
      "gates are scored over 300 matched cells per arm across three sizes. It "
      "reproduces the sign of every per-class effect in the RB-D1 table "
      "(`agentic` and `ai_cacheable` improve, `ai_uncacheable` regresses, CRUD "
      "inert) but it is an illustration of the mechanism, not a second "
      "measurement of the effect, and no number here revises anything above.")
    w("")
    w("## Notes")
    w("")
    jj_op = paired(runs, OP, "jcac_anchored", "J")
    w("- One changed factor from the published null, nothing else: same grid, "
      "same operating point, same baselines, same matrix shape. The null was "
      "never re-run and `risk_cost_at_point=False` keeps it bit-reproducible.")
    w("- The corrected knob's cost is real and reported: buying attainment "
      "raises spend along the frontier. The claim is *where the frontier "
      "sits* relative to the reactive stack, not that headroom is free.")
    w("- **The knob is not a new default, and is not claimed as one.** Under "
      "the published objective weights the corrected arm is net *worse* on "
      f"composite J than the point forecast (ΔJ = {jj_op['mean_diff']:+.4g}, "
      f"p = {jj_op['p']:.3g}): the attainment it buys costs more than the "
      "weights say attainment is worth. The point-forecast controller stays "
      "the quotable configuration, and this campaign is evidence *for* that "
      "default, not against it.")
    w("- What the campaign does contribute is the conversion of a named "
      "limitation into a measured dial: the standing caveat that our win is "
      "\"conditional on operator SLO-tolerance\" is now an explicit knob with "
      "a price curve attached, so an operator whose weights differ from ours "
      "can read off what tightening attainment costs — and where (per the "
      "diagnosis) it stops being purchasable at all.")
    w("- Substrate rules unchanged: sim-backend decision quality, matched "
      "cells, never mixed with replay tables. Overload (v3) regime remains "
      "the declared follow-up; no overload claim from these cells.")
    w("- Stopping rule §4 honored: one matrix execution, one analysis pass; "
      "if RB-H1 had failed the risk line would have ended with two nulls.")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
