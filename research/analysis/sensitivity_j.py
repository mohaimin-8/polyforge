"""Composite-objective weight-sensitivity analysis — DECLARED EXPLORATORY.

Declared 2026-07-12 (session 16), grid frozen in this file before execution.

Motivation: every campaign's headline uses J = cost_norm + 2·violation +
0.5·(1−Jain), the objective the baselines were tuned on (TUNING.md). A
committee can fairly ask whether "PolyForge wins J against every baseline"
survives if the weights are not exactly (1, 2, 0.5). This script recomputes
the paired J comparison of each CLOSED campaign under a frozen grid of
weight perturbations. It runs no simulations, re-tunes nothing, and touches
no campaign file — it re-reads the immutable run databases only, so it
breaks neither the stopping rules nor ground rule 2 (RESULTS_MASTER.md).

Rules of this analysis, stated before the first run:
- EXPLORATORY. No confirmatory claim may be derived from it. The
  pre-registered weights (2, 0.5) remain the only citable J numbers.
- The grid below is frozen: w_v ∈ {0, 0.5, 1, 2, 4} × w_f ∈
  {0, 0.25, 0.5, 1, 2}, cost weight pinned at 1 (J is reported up to an
  overall positive scale, so fixing the cost coefficient spans every
  weighting direction). (2, 0.5) is the pre-registered cell; w=0 cells ask
  whether the win survives valuing a term at nothing.
- Success criterion (descriptive, not a hypothesis): in how many of the 25
  cells does the campaign's treatment beat the campaign's baseline with
  mean paired ΔJ < 0 at p < 0.01? Any flip cells are listed verbatim.
- One execution, output to SENSITIVITY_J.md. If this file and the output
  disagree, the output is stale and must be regenerated, never edited.

Campaigns covered (treatment vs baselines, pairing):
  v1 headline   raw_sim.duckdb            jcac          vs hpa/keda/firm/static/gptcache, by cell
  v2 matrix     raw_sim_v2.duckdb         jcac_v2       vs hpa/keda/firm/static/gptcache, by cell
  v3 overload   raw_sim_v3.duckdb         jcac_seasonal vs hpa/keda/firm, by cell
  VTC fairness  vtc_fairness.duckdb       jcac          vs vtc_replica, by cell
  replay n=96   trace_replay2_runs.csv    jcac          vs hpa/keda/firm, by window

Cost normalization is never re-derived by hand: DuckDB campaigns use the
identity cost_norm = J_prereg − 2·violation − 0.5·(1−Jain) with J_prereg
from stats.composite_objective (the audited implementation), and the replay
recovers cost_norm from its own stored J column the same way. The recovered
term is exact algebra, not a re-estimate.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from scipy import stats as sps

import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "eval" / "results"
# Written through stats.record_path so POLYFORGE_ANALYSIS_OUT can redirect it;
# it resolved to this directory unconditionally, so running the script
# overwrote the committed record and the gate could not compare it.
OUT = stats.record_path("SENSITIVITY_J.md")

# Frozen grid — do not extend after execution.
W_VIOLATION = [0.0, 0.5, 1.0, 2.0, 4.0]
W_FAIRNESS = [0.0, 0.25, 0.5, 1.0, 2.0]
PREREG = (2.0, 0.5)
ALPHA = 0.01

CAMPAIGNS = [
    {
        "name": "v1 headline matrix",
        "source": "raw_sim.duckdb",
        "treatment": "jcac",
        "baselines": ["hpa", "keda", "firm", "static", "gptcache"],
        "keys": stats.CELL_KEYS,
    },
    {
        "name": "v2 matrix",
        "source": "raw_sim_v2.duckdb",
        "treatment": "jcac_v2",
        "baselines": ["hpa", "keda", "firm", "static", "gptcache"],
        "keys": stats.CELL_KEYS,
    },
    {
        "name": "v3 overload matrix",
        "source": "raw_sim_v3.duckdb",
        "treatment": "jcac_seasonal",
        "baselines": ["hpa", "keda", "firm"],
        "keys": stats.CELL_KEYS,
    },
    {
        "name": "VTC fairness slice",
        "source": "vtc_fairness.duckdb",
        "treatment": "jcac",
        "baselines": ["vtc_replica"],
        "keys": stats.CELL_KEYS,
    },
    {
        "name": "real-demand replay (n=96)",
        "source": "trace_replay2_runs.csv",
        "treatment": "jcac",
        "baselines": ["hpa", "keda", "firm"],
        "keys": ["window"],
    },
]


def load_campaign(source: str) -> pd.DataFrame:
    """Per-run frame with an exact cost_norm column recovered from the
    campaign's own pre-registered J implementation."""
    if source.endswith(".csv"):
        df = pd.read_csv(RESULTS_DIR / source)
        j_prereg = df["J"]
    else:
        df = stats.load_runs(RESULTS_DIR / source)
        j_prereg = stats.composite_objective(df)
    df = df.assign(
        cost_norm=j_prereg - 2.0 * df.mean_violation - 0.5 * (1.0 - df.mean_jain)
    )
    return df


def weighted_j(df: pd.DataFrame, w_v: float, w_f: float) -> pd.Series:
    return df.cost_norm + w_v * df.mean_violation + w_f * (1.0 - df.mean_jain)


def paired_cell(df, treatment, baseline, keys, w_v, w_f):
    frame = df.assign(Jw=weighted_j(df, w_v, w_f))
    merged = frame[frame.system == treatment].merge(
        frame[frame.system == baseline], on=keys, suffixes=("_t", "_b")
    )
    diff = merged.Jw_t - merged.Jw_b
    sd = diff.std(ddof=1)
    if len(merged) < 2 or sd == 0.0:
        p, dz = 1.0, 0.0
    else:
        _, p = sps.ttest_1samp(diff, 0.0)
        dz = float(diff.mean() / sd)
    return {
        "w_v": w_v, "w_f": w_f, "pairs": len(merged),
        "mean_diff": float(diff.mean()), "p": float(p), "dz": dz,
        "win": bool(diff.mean() < 0 and p < ALPHA),
    }


def main() -> None:
    lines: list[str] = []
    w = lines.append
    w("# Composite-J weight sensitivity — EXPLORATORY (declared before run)")
    w("")
    w("Generated by `sensitivity_j.py`; grid and rules are frozen in that file's")
    w("docstring. **No confirmatory claim derives from this analysis** — the")
    w("pre-registered weights (w_v=2, w_f=0.5) remain the only citable J numbers.")
    w("This file answers one robustness question: does \"treatment beats baseline")
    w(f"on J, paired, p<{ALPHA}\" survive perturbing the objective's weights across")
    w(f"w_v ∈ {W_VIOLATION} × w_f ∈ {W_FAIRNESS} (25 cells, cost weight ≡ 1)?")
    w("")

    grand_total = grand_wins = 0
    for spec in CAMPAIGNS:
        df = load_campaign(spec["source"])
        w(f"## {spec['name']} — `{spec['source']}`, "
          f"`{spec['treatment']}` vs {', '.join('`%s`' % b for b in spec['baselines'])}")
        w("")
        w("| baseline | pairs | dz @ prereg (2, 0.5) | dz range over grid | win cells (of 25) | flip cells |")
        w("|---|---|---|---|---|---|")
        for baseline in spec["baselines"]:
            cells = [
                paired_cell(df, spec["treatment"], baseline, spec["keys"], w_v, w_f)
                for w_v in W_VIOLATION for w_f in W_FAIRNESS
            ]
            table = pd.DataFrame(cells)
            prereg_row = table[(table.w_v == PREREG[0]) & (table.w_f == PREREG[1])].iloc[0]
            wins = int(table.win.sum())
            flips = table[~table.win]
            flip_desc = "—" if flips.empty else "; ".join(
                f"(w_v={r.w_v:g}, w_f={r.w_f:g}: dz={r.dz:+.2f}, p={r.p:.2g})"
                for r in flips.itertuples()
            )
            w(f"| {baseline} | {int(prereg_row.pairs)} | {prereg_row.dz:+.2f} "
              f"(p={prereg_row.p:.2g}) | {table.dz.min():+.2f} … {table.dz.max():+.2f} "
              f"| **{wins}/25** | {flip_desc} |")
            grand_total += len(cells)
            grand_wins += wins
        w("")

    w("## Reading")
    w("")
    w(f"Across all campaigns and baselines: **{grand_wins}/{grand_total}** grid cells")
    w(f"preserve the win (paired mean ΔJ < 0 at p < {ALPHA}). Flip cells, if any, are")
    w("listed verbatim above — including them is the point of the exercise. A flip")
    w("only at extreme weights (e.g. w_v=4 with w_f=0) bounds the claim rather than")
    w("refuting it; a flip near the pre-registered cell would be reportable as a")
    w("genuine fragility. Either way this section is exploratory context for the")
    w("threats-to-validity chapter, not a new result.")
    w("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({grand_wins}/{grand_total} win cells)")


if __name__ == "__main__":
    main()
