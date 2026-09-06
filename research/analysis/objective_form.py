"""Composite-objective functional-form robustness — DECLARED EXPLORATORY (Wave 1).

Declared 2026-07-16 (session 20), forms frozen in this file before execution.

Motivation: SENSITIVITY_J.md answered "does the J win survive perturbing the
*weights*?" (425 grid cells; direction never flips). A committee can equally
ask whether it survives changing the *functional form* of the objective —
weighted sums are one aggregation choice among several defensible ones.
This script recomputes the paired treatment-vs-baseline comparison of each
closed campaign under a frozen set of alternative objective forms, weights
pinned at the pre-registered (w_v, w_f) = (2, 0.5) throughout. It runs no
simulations, re-tunes nothing, and touches no campaign file.

Frozen forms — do not extend after execution (v = mean_violation,
vss = violation_step_share, F = 1 - mean_jain, c = cost_norm recovered by
exact algebra from each campaign's own J, as in sensitivity_j.py):
  F0 published anchor:  J = c + 2 v + 0.5 F
  F1 step-share:        J = c + 2 vss + 0.5 F     (violation *frequency*,
                        not depth — the metric an SLA credit regime sees)
  F2 log-cost:          J = ln(1 + c) + 2 v + 0.5 F   (diminishing marginal
                        pain of spend; compresses cost-driven wins)
  F3 multiplicative:    J = ln[(1 + c) (1 + v)^2 (1 + F)^0.5]
                        (Cobb-Douglas-style: terms multiply, a system must
                        be adequate on every axis; log kept for paired
                        differences — monotone, so ordering is identical)
  F4 lexicographic with tolerance (epsilon = 0.01 on mean_violation):
                        within a pair, if |v_t - v_b| <= epsilon the cheaper
                        system wins the pair, otherwise the lower-violation
                        system wins. Scored as win share with a two-sided
                        binomial sign test (no averaging is meaningful for
                        a lexicographic order).

Statistics: F0-F3 use the same paired t on per-cell differences as
sensitivity_j.py (win = mean diff < 0 at p < 0.01). F4 uses win share > 0.5
at binomial p < 0.01. Success criterion (descriptive, not a hypothesis):
in how many form-baseline combinations does the campaign's published
direction survive? Any reversals are listed verbatim.

Rules of this analysis, stated before the first run:
- EXPLORATORY. No confirmatory claim may be derived from it. The
  pre-registered J (F0 at weights (2, 0.5)) remains the only citable
  objective.
- One execution, output to OBJECTIVE_FORM.md. If this file and the output
  disagree, the output is stale and must be regenerated, never edited.

Campaigns covered (identical to sensitivity_j.py):
  v1 headline   raw_sim.duckdb            jcac          vs hpa/keda/firm/static/gptcache
  v2 matrix     raw_sim_v2.duckdb         jcac_v2       vs hpa/keda/firm/static/gptcache
  v3 overload   raw_sim_v3.duckdb         jcac_seasonal vs hpa/keda/firm
  VTC fairness  vtc_fairness.duckdb       jcac          vs vtc_replica
  replay n=96   trace_replay2_runs.csv    jcac          vs hpa/keda/firm
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

import stats

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "eval" / "results"
# Written through stats.record_path so POLYFORGE_ANALYSIS_OUT can redirect it.
# It resolved to this directory unconditionally until session 43, which meant
# the committed record was the only place this script could write: running it
# overwrote the published record, and the reproduction gate could not
# regenerate it into a scratch directory to compare.
OUT = stats.record_path("OBJECTIVE_FORM.md")

ALPHA = 0.01
EPSILON = 0.01

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
    """Per-run frame with exact cost_norm, as in sensitivity_j.py."""
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


FORMS = {
    "F0 published": lambda d: d.cost_norm + 2.0 * d.mean_violation
    + 0.5 * (1.0 - d.mean_jain),
    "F1 step-share": lambda d: d.cost_norm + 2.0 * d.violation_step_share
    + 0.5 * (1.0 - d.mean_jain),
    "F2 log-cost": lambda d: np.log1p(d.cost_norm) + 2.0 * d.mean_violation
    + 0.5 * (1.0 - d.mean_jain),
    "F3 multiplicative": lambda d: np.log1p(d.cost_norm)
    + 2.0 * np.log1p(d.mean_violation)
    + 0.5 * np.log1p(1.0 - d.mean_jain),
}


def paired_form(df, treatment, baseline, keys, form_fn):
    frame = df.assign(Jf=form_fn(df))
    merged = frame[frame.system == treatment].merge(
        frame[frame.system == baseline], on=keys, suffixes=("_t", "_b")
    )
    diff = merged.Jf_t - merged.Jf_b
    sd = diff.std(ddof=1)
    if len(merged) < 2 or sd == 0.0:
        p, dz = 1.0, 0.0
    else:
        _, p = sps.ttest_1samp(diff, 0.0)
        dz = float(diff.mean() / sd)
    return {
        "pairs": len(merged), "mean_diff": float(diff.mean()),
        "p": float(p), "dz": dz,
        "win": bool(diff.mean() < 0 and p < ALPHA),
    }


def lexicographic(df, treatment, baseline, keys):
    merged = df[df.system == treatment].merge(
        df[df.system == baseline], on=keys, suffixes=("_t", "_b")
    )
    close = (merged.mean_violation_t - merged.mean_violation_b).abs() <= EPSILON
    win_cost = merged.total_cost_usd_t < merged.total_cost_usd_b
    win_viol = merged.mean_violation_t < merged.mean_violation_b
    wins = int((close & win_cost).sum() + (~close & win_viol).sum())
    n = len(merged)
    p = float(sps.binomtest(wins, n, 0.5).pvalue) if n else 1.0
    return {
        "pairs": n, "wins": wins, "share": wins / n if n else 0.0, "p": p,
        "win": bool(n and wins / n > 0.5 and p < ALPHA),
    }


def main() -> None:
    lines: list[str] = []
    w = lines.append
    w("# Objective functional-form robustness — EXPLORATORY (declared before run, Wave 1)")
    w("")
    w("Generated by `objective_form.py`; forms, epsilon and rules are frozen in that")
    w("file's docstring. Weights pinned at the pre-registered (2, 0.5) throughout —")
    w("this is the *form* axis, orthogonal to SENSITIVITY_J.md's *weight* axis.")
    w("**No confirmatory claim derives from this analysis**; the pre-registered J")
    w("remains the only citable objective.")
    w("")

    grand_total = grand_wins = 0
    reversals: list[str] = []
    for spec in CAMPAIGNS:
        df = load_campaign(spec["source"])
        w(f"## {spec['name']} — `{spec['source']}`, "
          f"`{spec['treatment']}` vs {', '.join('`%s`' % b for b in spec['baselines'])}")
        w("")
        w("| baseline | " + " | ".join(list(FORMS) + [f"F4 lexicographic (eps={EPSILON})"]) + " |")
        w("|---" * (len(FORMS) + 2) + "|")
        for baseline in spec["baselines"]:
            cells = []
            for fname, ffn in FORMS.items():
                r = paired_form(df, spec["treatment"], baseline, spec["keys"], ffn)
                mark = "WIN" if r["win"] else "no"
                cells.append(f"{mark} dz={r['dz']:+.2f} p={r['p']:.2g}")
                grand_total += 1
                grand_wins += int(r["win"])
                if not r["win"]:
                    reversals.append(
                        f"{spec['name']} / {baseline} / {fname}: "
                        f"dz={r['dz']:+.2f}, p={r['p']:.2g}, "
                        f"mean diff {r['mean_diff']:+.4f}"
                    )
            lex = lexicographic(df, spec["treatment"], baseline, spec["keys"])
            mark = "WIN" if lex["win"] else "no"
            cells.append(f"{mark} {lex['wins']}/{lex['pairs']} p={lex['p']:.2g}")
            grand_total += 1
            grand_wins += int(lex["win"])
            if not lex["win"]:
                reversals.append(
                    f"{spec['name']} / {baseline} / F4 lexicographic: "
                    f"win share {lex['share']:.2f} ({lex['wins']}/{lex['pairs']}), "
                    f"p={lex['p']:.2g}"
                )
            w(f"| {baseline} | " + " | ".join(cells) + " |")
        w("")

    w("## Non-wins, verbatim")
    w("")
    if reversals:
        for r in reversals:
            w(f"- {r}")
    else:
        w("- none")
    w("")
    w("## Reading")
    w("")
    w(f"Across all campaigns, baselines and forms: **{grand_wins}/{grand_total}**")
    w(f"combinations preserve the published direction at p < {ALPHA}. A non-win under")
    w("F2/F3 (which deliberately compress cost-driven separation) or F4 (which asks")
    w("for strict violation-then-cost dominance) bounds the claim's shape — it says")
    w("*which kind* of preference order the win depends on — rather than refuting")
    w("the pre-registered result. Any non-win at F0 would indicate a data or")
    w("methods drift and must be investigated, not narrated away.")
    w("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({grand_wins}/{grand_total} preserved)")


if __name__ == "__main__":
    main()
