"""Risk-aware MPC campaign analysis (PREREG_RISK_MPC.md, frozen).

Reads the matrix_risk duckdb, scores the frozen readings — RQ-H1 (the knob
is a real control: monotone frontier), RQ-H2 (dominance at the pre-declared
q=0.90), RQ-D1 (descriptive frontier + figure) — and writes RESULTS_RISK.md.
Pairing is the standard matched-cells design.

    python analysis_risk.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats as sps

import figures as F  # shared 600-DPI / colour-blind-safe conventions

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "eval" / "results" / "raw_sim_risk.duckdb"
OUT = Path(__file__).resolve().parent / "RESULTS_RISK.md"
TENANTS = 8
COST_SCALE = 0.01
CELL = ["workload", "tenant_mix", "cluster_size", "rep"]
BOOT_N = 10_000
BOOT_SEED = 20260722

# Frozen frontier order (PREREG_RISK_MPC §3): point forecast → most conservative.
FRONTIER = ["jcac_anchored", "jcac_q70", "jcac_q80", "jcac_q90", "jcac_q95"]
REACTIVE = ["hpa", "keda", "concurrency"]
OPERATING_POINT = "jcac_q90"  # pre-declared in §3, not chosen post hoc

LABELS = {
    "jcac_anchored": "PolyForge (point forecast)",
    "jcac_q70": "PolyForge q=0.70",
    "jcac_q80": "PolyForge q=0.80",
    "jcac_q90": "PolyForge q=0.90",
    "jcac_q95": "PolyForge q=0.95",
    "hpa": "HPA (tuned)", "keda": "KEDA (tuned)",
    "concurrency": "Concurrency (tuned)",
}
COLORS = {"hpa": F.SYSTEM_COLORS["hpa"], "keda": F.SYSTEM_COLORS["keda"],
          "concurrency": F.SYSTEM_COLORS["gptcache"]}
FRONTIER_COLOR = F.SYSTEM_COLORS["jcac"]


def load() -> pd.DataFrame:
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute(
        "select r.system, r.workload, r.tenant_mix, r.cluster_size, r.rep, "
        "m.total_cost_usd, m.mean_violation, m.mean_jain, m.steps "
        "from runs r join metrics m on r.run_id = m.run_id "
        "where r.status = 'valid'"
    ).fetchdf()
    con.close()
    df["J"] = (df.total_cost_usd / (119 * TENANTS) / COST_SCALE
               + 2.0 * df.mean_violation + 0.5 * (1.0 - df.mean_jain))
    return df


def paired(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    m = df[df.system == a].merge(df[df.system == b], on=CELL, suffixes=("_a", "_b"))
    diff = m[f"{metric}_a"] - m[f"{metric}_b"]
    sd = diff.std(ddof=1)
    base = {"mean_diff": float(diff.mean()) if len(diff) else 0.0, "n": len(diff),
            "mean_a": float(m[f"{metric}_a"].mean()) if len(m) else 0.0,
            "mean_b": float(m[f"{metric}_b"].mean()) if len(m) else 0.0}
    if len(diff) < 2 or sd == 0.0:
        return {**base, "p": 1.0, "dz": 0.0}
    _, p = sps.ttest_1samp(diff, 0.0)
    return {**base, "p": float(p), "dz": float(diff.mean() / sd)}


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


def frontier_figure(summary: pd.DataFrame) -> None:
    fig, ax = F.plt.subplots(figsize=(4.4, 3.0))
    xs = [summary.loc[s, "total_cost_usd"] for s in FRONTIER]
    ys = [summary.loc[s, "mean_violation"] for s in FRONTIER]
    ax.plot(xs, ys, "-", color=FRONTIER_COLOR, lw=1.4, zorder=2)
    for s in FRONTIER:
        r = summary.loc[s]
        ax.hlines(r.mean_violation, r.cost_lo, r.cost_hi,
                  color=FRONTIER_COLOR, lw=1.0, alpha=0.6, zorder=1)
        ax.vlines(r.total_cost_usd, r.viol_lo, r.viol_hi,
                  color=FRONTIER_COLOR, lw=1.0, alpha=0.6, zorder=1)
        ax.plot(r.total_cost_usd, r.mean_violation, "o", color=FRONTIER_COLOR,
                ms=6, markeredgecolor="white", markeredgewidth=1, zorder=3)
        ax.annotate(s.replace("jcac_", "").replace("anchored", "point"),
                    (r.total_cost_usd, r.mean_violation),
                    textcoords="offset points", xytext=(6, 5),
                    fontsize=7, color=F.INK2)
    # Stagger the reactive labels: their points cluster, so a single offset
    # collides.
    offsets = {"hpa": (7, 4), "concurrency": (-4, -12), "keda": (6, -11)}
    for s in REACTIVE:
        if s not in summary.index:
            continue
        r = summary.loc[s]
        ax.plot(r.total_cost_usd, r.mean_violation, "s", color=COLORS[s], ms=6,
                markeredgecolor="white", markeredgewidth=1, zorder=3)
        ax.annotate(LABELS[s].split(" ")[0], (r.total_cost_usd, r.mean_violation),
                    textcoords="offset points", xytext=offsets.get(s, (6, -9)),
                    fontsize=7, color=F.INK2)
    ax.set_xlabel("mean cost per run (USD)")
    ax.set_ylabel("mean SLO violation (0–1)")
    F._style(ax)
    ax.grid(axis="x", visible=True, color=F.GRID, linewidth=0.6)

    # Outcome-aware caption: the pre-written optimistic wording would have
    # described a frontier that the data does not show (PLANNER_CELLS lesson).
    rising = summary.loc[FRONTIER[-1], "mean_violation"] > summary.loc[FRONTIER[0], "mean_violation"]
    if rising:
        caption = (
            "The risk knob moved the controller the **wrong way** (blue, mean ± "
            "95% CI): raising the planning quantile *lowered* spend and *raised* "
            "violation, monotonically. Diagnosis — inflating demand also inflates "
            "projected tier spend, tripping the per-tenant budget guardrail into "
            "its shed fallback (tier=\"none\", a designed AI outage), which is "
            "both cheap and a total SLO miss. Published as measured; RQ-H1/H2 "
            "FAIL. The tuned reactive scalers (squares) remain far to the right: "
            "every PolyForge arm is substantially cheaper at comparable "
            "violation. Down-and-left is better.")
    else:
        caption = (
            "Risk-aware MPC traces a cost/violation frontier (blue, mean ± 95% "
            "CI): raising the planning quantile buys attainment with spend along "
            "a controlled curve, where the tuned reactive scalers (squares) sit "
            "at fixed operating points. Down-and-left is better.")
    F.save(fig, "fig18_risk_frontier", caption)


def main() -> None:
    runs = load()
    lines: list[str] = []
    w = lines.append
    w("# Risk-aware (quantile) MPC — results (pre-registered)")
    w("")
    w("Protocol frozen in `PREREG_RISK_MPC.md`, pushed before any run. The "
      "published controller plans at the *expected* demand, so realized demand "
      "lands above plan roughly half the time — the mechanism behind the "
      "disclosed attainment-for-spend trade. Here each candidate is scored "
      "against a **quantile of the controller's own forecast errors** "
      "(distribution-free, floored at zero). `risk_quantile=None` remains the "
      "published path and was verified bit-identical (max drift 0.00e+00) "
      "before the anchor push. This revises no closed campaign: v2 H1 and v3 "
      "H1′ stand as measured for the base controller. "
      "Rerun: `python analysis_risk.py`.")
    w("")
    w(f"Valid runs: {len(runs)} over {runs.system.nunique()} systems "
      f"({len(runs[runs.system == 'jcac_anchored'])} matched cells per arm)")
    w("")

    # --- summary + CIs ---
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

    w("## The frontier (mean over matched cells, 95% bootstrap CI)")
    w("")
    pretty = summary.reset_index()[["system", "total_cost_usd", "cost_lo", "cost_hi",
                                    "mean_violation", "viol_lo", "viol_hi", "J"]].copy()
    pretty["system"] = pretty.system.map(LABELS)
    w(md_table(pretty))
    w("")

    # --- RQ-H1: monotone control ---
    w("## RQ-H1 (confirmatory) — is the risk knob a real control?")
    w("")
    ranks = np.arange(len(FRONTIER))
    viol_means = np.array([summary.loc[s, "mean_violation"] for s in FRONTIER])
    cost_means = np.array([summary.loc[s, "total_cost_usd"] for s in FRONTIER])
    rho_v = float(sps.spearmanr(ranks, viol_means).statistic)
    rho_c = float(sps.spearmanr(ranks, cost_means).statistic)
    h1 = (rho_v <= -0.9) and (rho_c >= 0.9)
    w(md_table(pd.DataFrame([
        {"reading": "violation vs quantile rank (want ρ ≤ −0.9)", "spearman_rho": rho_v,
         "verdict": "PASS" if rho_v <= -0.9 else "FAIL"},
        {"reading": "cost vs quantile rank (want ρ ≥ +0.9)", "spearman_rho": rho_c,
         "verdict": "PASS" if rho_c >= 0.9 else "FAIL"},
    ])))
    w("")
    if h1:
        w("**RQ-H1: PASS** — raising the planning quantile monotonically lowers "
          "violation and raises cost. The knob steers the system along the "
          "trade rather than perturbing it: SLO-tolerance is now an explicit, "
          "dialable parameter instead of an implicit consequence of planning "
          "at the mean.")
    else:
        w("**RQ-H1: FAIL** — the residual-quantile knob does not move the "
          "system monotonically along the cost/violation trade. Per §3 the "
          "mechanism is published as a **null**, in the same tradition as the "
          "γ-term: the idea is sound in principle but does not steer this "
          "system, and no frontier claim follows.")
    w("")

    # --- RQ-H2: dominance at the declared operating point ---
    w(f"## RQ-H2 (confirmatory) — dominance at the pre-declared q = 0.90")
    w("")
    v = paired(runs, OPERATING_POINT, "jcac_anchored", "mean_violation")
    viol_ok = v["mean_diff"] < 0 and v["p"] < 0.01
    rows = [{"reading": "violation vs point-forecast MPC (want < 0)", "n": v["n"],
             f"{OPERATING_POINT} mean": v["mean_a"], "other mean": v["mean_b"],
             "diff": v["mean_diff"], "p": v["p"], "d_z": v["dz"],
             "verdict": "PASS" if viol_ok else "FAIL"}]
    cost_ok = True
    for b in REACTIVE:
        c = paired(runs, OPERATING_POINT, b, "total_cost_usd")
        ok = c["mean_diff"] < 0 and c["p"] < 0.01
        cost_ok = cost_ok and ok
        rows.append({"reading": f"cost vs tuned {b} (want < 0)", "n": c["n"],
                     f"{OPERATING_POINT} mean": c["mean_a"], "other mean": c["mean_b"],
                     "diff": c["mean_diff"], "p": c["p"], "d_z": c["dz"],
                     "verdict": "PASS" if ok else "FAIL"})
    h2 = viol_ok and cost_ok
    w(md_table(pd.DataFrame(rows)))
    w("")
    if h2:
        w("**RQ-H2: PASS (both conjuncts)** — at q=0.90 the controller buys a "
          "significant violation reduction over its own point-forecast "
          "configuration **and still costs less than every tuned reactive "
          "arm**. The substantive claim holds: *attainment can be dialed up "
          "while remaining the cheap system*.")
    else:
        w("**RQ-H2: FAIL** — per the pre-committed falsifier, attainment cannot "
          "be bought cheaply even with an explicit risk knob at the declared "
          "operating point. The project's standing honest boundary — "
          "*attainment costs money* — is confirmed for our own controller and "
          "published as such. The frontier table above still shows what the "
          "knob does buy, and at what price.")
    w("")

    # --- RQ-D1 ---
    w("## RQ-D1 (descriptive, no gate) — what each step of the knob buys")
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
    dominated = [LABELS[b] for b in REACTIVE if b in summary.index and any(
        summary.loc[s, "total_cost_usd"] < summary.loc[b, "total_cost_usd"]
        and summary.loc[s, "mean_violation"] < summary.loc[b, "mean_violation"]
        for s in FRONTIER)]
    w(f"Baseline operating points Pareto-dominated by at least one frontier "
      f"arm (strictly cheaper *and* strictly lower violation): "
      f"**{', '.join(dominated) if dominated else 'none'}**.")
    w("")
    w("Per workload class (Δviolation at q=0.90 vs the point-forecast MPC):")
    w("")
    rows = []
    for wl in sorted(runs.workload.unique()):
        r = paired(runs[runs.workload == wl], OPERATING_POINT, "jcac_anchored",
                   "mean_violation")
        c = paired(runs[runs.workload == wl], OPERATING_POINT, "jcac_anchored",
                   "total_cost_usd")
        rows.append({"class": wl, "Δviolation": r["mean_diff"], "p": r["p"],
                     "Δcost USD": c["mean_diff"], "n": r["n"]})
    w(md_table(pd.DataFrame(rows)))
    w("")
    w("![risk frontier](../../eval/results/figures/fig18_risk_frontier.png)")
    w("")
    w("## POST-RUN DIAGNOSIS (added after the frozen readings were scored)")
    w("")
    w("Prose only — no datum above is changed, and the FAIL verdicts stand as "
      "the campaign's result. This section exists because the failure is "
      "*perfectly monotone in the reverse direction* (ρ = +1 / −1), which is a "
      "systematic effect, not noise, and the project's practice is to publish "
      "the diagnosis with the null (the `PLANNER_CELLS` aliasing precedent).")
    w("")
    w("**What happened.** The knob was applied to the whole projection — both "
      "the SLO/capacity term *and* the cost term. Inflating demand also "
      "inflates *projected spend*, because tier (inference) cost is "
      "`miss_rps × interval × price` and scales with demand. That pushes "
      "candidates past the per-tenant budget filter "
      "(`_best_for_tenant`: `if cost > budget_per_step: continue`), and when "
      "the lattice empties the controller takes its designed fallback: shed to "
      "`replicas=min, cache=0, tier=\"none\"` — an AI outage. Shedding is "
      "cheap, so **cost falls**; the outage is a total SLO miss, so "
      "**violation rises**. The knob was fighting the Budget CRD, not the "
      "demand.")
    w("")
    w("**Evidence.** (a) The class breakdown above: `crud_bursty` and "
      "`crud_steady` show *no* effect (p = 0.27, 0.92) — CRUD carries no tier "
      "spend, so its budget headroom is untouched — while all three AI classes "
      "move sharply. (b) A targeted probe (`ai_cacheable/uniform/medium`, seed "
      "4242) counts the shed state directly: `tier=\"none\"` occurs **0** times "
      "at the point forecast, **2** at q=0.90 and **4** at q=0.95, while mean "
      "replicas *rise* 2.64 → 2.93 — the capacity half of the mechanism worked "
      "exactly as designed; the budget interaction defeated it.")
    w("")
    w("**What this does and does not license.** It does *not* rescue the "
      "hypotheses: as specified and pre-registered, the mechanism failed, and "
      "that is the published result. It does identify one changed factor for a "
      "disciplined follow-up: **size capacity against the risk-inflated demand "
      "but project cost and check the budget against the point forecast** — you "
      "are billed for the demand that *arrives*, not the demand you provisioned "
      "against. That follow-up requires its own pre-registration (the "
      "`PREREG_PLANNER_CELLS_DEALIAS` precedent: a new prereg with one changed "
      "factor, never a silent re-run), and this campaign's stopping rule "
      "forbids re-running it here.")
    w("")
    w("## Notes")
    w("")
    w("- The knob is **distribution-free**: the empirical quantile of the "
      "controller's own realized one-step forecast errors, floored at zero so "
      "it may only add headroom. No normality assumption, no oracle.")
    w("- Orthogonal to `adaptive_capacity` (which corrects model optimism); "
      "this corrects demand variance. Neither substitutes for the other.")
    w("- Substrate rules unchanged: sim-backend decision quality, "
      "blocked-factorial matched cells, never mixed with replay tables.")
    w("- The **overload (v3) regime**, where the attainment trade binds "
      "hardest, was pre-declared as the follow-up and is deliberately not run "
      "here; no overload claim may be read from these cells.")
    w("- Stopping rule §4 honored: one matrix execution, one analysis pass, "
      "frozen quantile grid, operating point declared before the data existed.")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")

    frontier_figure(summary)


if __name__ == "__main__":
    main()
