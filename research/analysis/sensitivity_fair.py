"""The post-audit J comparisons under other lateness weights and without the
cap -- DECLARED EXPLORATORY.

Written 2026-10-14 for the final check-up. RESULTS_FAIR_J and RESULTS_RETUNED
judge the treatment by J = cost_norm + 2·violation + 0.5·(1−Jain), where each
family's SLO overshoot is capped at 1.0 before it enters `violation`. Two
questions those records cannot answer:

- Does the reading depend on the weight 2? `SENSITIVITY_J.md` asked this of
  the pre-audit campaigns against the old baselines; it has never been asked
  of the fair and re-tuned comparators the current claims rest on.
- Does it depend on the cap? A shed AI service (tier `none`, a 30 s outage)
  overshoots its target many times over and scores 1.0, the same as a miss
  of 2x. The engine also records the uncapped overshoot (`mean_excess`, the
  SLO guard's metric); this scores J with that in place of `violation`.

Rules of this analysis:
- EXPLORATORY. Nothing here is pre-registered and no registered verdict
  changes. The grid -- doubling steps around the registered weight, capped
  and uncapped -- was fixed in the audit plan, but values were inspected
  interactively while this script was written.
- The registered cell (capped, w=2) must reproduce the registered ΔJ and CI
  exactly; test_sensitivity_fair.py pins that against both frozen scorers.
- Same unit, data, listwise rule and bootstrap as the frozen scorers, which
  this imports rather than restates: design points (reps averaged), 10,000
  resamples, seed 20260926. Directions use PREREG_RETUNED's registered
  reading of a CI.
- The crossover weight is a point estimate from design-point means, and
  exists only where the cost side and the lateness side disagree in sign.

    python sensitivity_fair.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analysis_fair_j as fair_j  # noqa: E402
import analysis_retuned as retuned  # noqa: E402
from stats import record_path  # noqa: E402

RECORD = "SENSITIVITY_FAIR.md"
TREATMENT = fair_j.TREATMENT
WEIGHTS = (1.0, 2.0, 4.0, 8.0)          # frozen; 2 is the registered weight
REGISTERED_W = 2.0
MEASURES = {"mean_violation": "capped (registered)", "mean_excess": "uncapped"}
FAIRNESS_W = 0.5                        # registered, held fixed
SHORT = {"treatment better": "**T**", "no difference shown": "=", "comparator better": "**C**"}
TERMS = ("cost", "lateness", "fairness")


def campaigns() -> dict[str, tuple[pd.DataFrame, dict[str, str]]]:
    """Each campaign's design-point-complete frame, exactly as its frozen
    scorer builds it, with its confirmatory family."""
    fair_arms = [fair_j.TREATMENT, fair_j.ALSO, *fair_j.PRIMARY.values(), *fair_j.SECONDARY.values()]
    return {
        "FAIR_J": (fair_j.complete_design_points(fair_j.load(), fair_arms), fair_j.PRIMARY),
        "RETUNED": (fair_j.complete_design_points(retuned.load(), retuned.ARMS), retuned.PRIMARY),
    }


def weighted_j(df: pd.DataFrame, w: float, measure: str) -> pd.Series:
    """J with lateness weight `w` on `measure`. At (2, mean_violation) this is
    stats.composite_objective term for term, in the same order, so the
    registered cell is bit-identical rather than merely close."""
    return (df.total_cost_usd / (119 * 8) / 0.01
            + w * df[measure] + FAIRNESS_W * (1.0 - df.mean_jain))


def cell(df: pd.DataFrame, comp: str, w: float, measure: str) -> dict:
    d = fair_j.design_diffs(df.assign(J=weighted_j(df, w, measure)), TREATMENT, comp, "J")
    means = fair_j.boot_means(d)
    r = {"n": len(d), "mean": float(d.mean()),
         "ci": (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))}
    return {**r, "direction": retuned.direction(r)}


def cost_side(df: pd.DataFrame, comp: str, measure: str) -> float:
    """Mean ΔJ with the lateness term removed: the cost-and-fairness side."""
    return float(fair_j.design_diffs(df.assign(J=weighted_j(df, 0.0, measure)), TREATMENT, comp, "J").mean())


def crossover(df: pd.DataFrame, comp: str, measure: str) -> float | None:
    """The lateness weight at which mean ΔJ changes sign, or None when the
    cost-and-fairness side and the lateness side favour the same arm (then
    no positive weight reverses the reading)."""
    rest = cost_side(df, comp, measure)
    late = fair_j.design_diffs(df, TREATMENT, comp, measure).mean()
    if late == 0.0 or rest * late >= 0.0:
        return None
    return float(-rest / late)


def term_shares(s: pd.DataFrame) -> dict[str, float]:
    """Mean contribution of each registered J term for one arm."""
    return {"cost": float((s.total_cost_usd / (119 * 8) / 0.01).mean()),
            "lateness": float((REGISTERED_W * s.mean_violation).mean()),
            "fairness": float((FAIRNESS_W * (1.0 - s.mean_jain)).mean())}


def identical_runs(df: pd.DataFrame, a: str, b: str) -> tuple[int, int]:
    """Runs where two arms produced the same metrics, paired by design and rep."""
    keys = [*fair_j.DESIGN, "rep"]
    cols = ["total_cost_usd", "mean_violation", "mean_jain", "mean_excess", "tier_none_step_share"]
    m = df[df.system == a].merge(df[df.system == b], on=keys, suffixes=("_a", "_b"),
                                  validate="one_to_one")
    same = np.ones(len(m), dtype=bool)
    for c in cols:
        same &= (m[f"{c}_a"] == m[f"{c}_b"]).to_numpy()
    return int(same.sum()), len(m)


def _header(n_dp: dict[str, int]) -> list[str]:
    return [
        "# J comparisons under other lateness weights and without the cap -- EXPLORATORY", "",
        "Generated by `sensitivity_fair.py`. **Exploratory: nothing here is pre-registered and no "
        "registered verdict changes.** The registered J weighs lateness 2 and caps each family's SLO "
        "overshoot at 1.0, so a shed AI service (a 30 s outage) scores the same as a 2x miss. This re-scores "
        "the confirmatory comparisons of `RESULTS_FAIR_J.md` and `RESULTS_RETUNED.md` with the lateness "
        f"weight at {', '.join(f'{w:g}' for w in WEIGHTS)}, capped as registered and uncapped "
        "(`mean_excess`, the SLO guard's metric). Cost weight 1 and fairness weight 0.5 are held at their "
        "registered values. Treatment `jcac_converged`; unit, listwise rule and bootstrap are the frozen "
        f"scorers' (design points: FAIR_J {n_dp['FAIR_J']}, RETUNED {n_dp['RETUNED']}; "
        f"{fair_j.BOOT_N:,} resamples, seed {fair_j.BOOT_SEED}). Negative ΔJ = the treatment is better. "
        "The capped w=2 cells reproduce the registered rows exactly.", ""]


def build(frames: dict[str, tuple[pd.DataFrame, dict[str, str]]]) -> str:
    L = _header({name: df[fair_j.DESIGN].drop_duplicates().shape[0] for name, (df, _) in frames.items()})
    grid = {(name, comp, measure, w): cell(df, comp, w, measure)
            for name, (df, family) in frames.items() for comp in family.values()
            for measure in MEASURES for w in WEIGHTS}

    L += ["## Summary: which arm the 95% CI favours", "",
          "**T** = treatment better (CI below 0), **=** = no difference shown, **C** = comparator better "
          "(CI above 0). The registered cell is capped, w=2.", "",
          "| campaign | hypothesis | vs | " + " | ".join(f"capped w={w:g}" for w in WEIGHTS) + " | "
          + " | ".join(f"uncapped w={w:g}" for w in WEIGHTS) + " |",
          "|---|---|---|" + ":---:|" * (2 * len(WEIGHTS))]
    for name, (df, family) in frames.items():
        for hid, comp in family.items():
            marks = [SHORT[grid[(name, comp, m, w)]["direction"]] for m in MEASURES for w in WEIGHTS]
            L.append(f"| {name} | {hid} | `{comp}` | " + " | ".join(marks) + " |")

    for measure, label in MEASURES.items():
        L += ["", f"## ΔJ with lateness {label}", "",
              "| campaign | vs | lateness weight | ΔJ | 95% CI | direction |", "|---|---|---:|---:|---|---|"]
        for name, (df, family) in frames.items():
            for comp in family.values():
                for w in WEIGHTS:
                    r = grid[(name, comp, measure, w)]
                    mark = " (registered)" if measure == "mean_violation" and w == REGISTERED_W else ""
                    L.append(f"| {name} | `{comp}` | {w:g}{mark} | {r['mean']:+.4f} | "
                             f"[{r['ci'][0]:+.4f}, {r['ci'][1]:+.4f}] | {r['direction']} |")

    L += ["", "## Crossover: the lateness weight at which mean ΔJ changes sign", "",
          "A point estimate from design-point means. Where it exists, one arm wins on cost and fairness "
          "and the other on lateness; *better below* names the arm the means favour at lighter lateness "
          "weights. `none` = both sides favour the same arm, so no positive weight reverses the reading of "
          "the means.", "",
          "| campaign | vs | capped | better below | uncapped | better below |", "|---|---|---:|---|---:|---|"]
    for name, (df, family) in frames.items():
        for comp in family.values():
            cells = []
            for m in MEASURES:
                x = crossover(df, comp, m)
                below = "treatment" if cost_side(df, comp, m) < 0 else "comparator"
                cells += ["none", "-"] if x is None else [f"{x:.2f}", below]
            L.append(f"| {name} | `{comp}` | " + " | ".join(cells) + " |")

    L += ["", "## What each registered J term contributes (per-arm means)", "",
          "| campaign | arm | J | cost term | lateness term (2·violation) | fairness term | cost share of J |",
          "|---|---|---:|---:|---:|---:|---:|"]
    for name, (df, family) in frames.items():
        for arm in [TREATMENT, *family.values()]:
            t = term_shares(df[df.system == arm])
            j = sum(t[k] for k in TERMS)
            L.append(f"| {name} | `{arm}` | {j:.4f} | {t['cost']:.4f} | {t['lateness']:.4f} | "
                     f"{t['fairness']:.4f} | {t['cost'] / j:.0%} |")

    L += ["", "## Where the treatment sheds AI, and what the cap hides there", "",
          "AI shed = share of tenant-steps on tier `none`. Uncapped / capped = mean_excess over "
          "mean_violation for the treatment: how many times larger its lateness is before the cap. The last "
          f"column is the highest shed rate of any other arm in the campaign's frame (`{fair_j.ALSO}` "
          "excluded: it is the treatment's twin, see below).", "",
          "| campaign | workload | treatment AI shed | treatment uncapped / capped | highest shed, other arms |",
          "|---|---|---:|---:|---:|"]
    for name, (df, family) in frames.items():
        t = df[df.system == TREATMENT].groupby("workload")
        others = df[~df.system.isin([TREATMENT, fair_j.ALSO])]
        c = others.groupby("workload").tier_none_step_share.max()
        shed, exc, vio = t.tier_none_step_share.mean(), t.mean_excess.mean(), t.mean_violation.mean()
        for wl in shed.index:
            ratio = f"{exc[wl] / vio[wl]:.1f}x" if vio[wl] > 0 else "-"
            L.append(f"| {name} | `{wl}` | {shed[wl]:.1%} | {ratio} | {c[wl]:.1%} |")

    df_f = frames["FAIR_J"][0]
    same, total = identical_runs(df_f, TREATMENT, fair_j.ALSO)
    L += ["", f"## `{TREATMENT}` and `{fair_j.ALSO}` in FAIR_J", "",
          f"The two controllers produced identical metrics in **{same} of {total}** paired runs (same design "
          "point, same rep, every scored metric equal), so FAIR_J's result for the published quotable "
          "controller is the converged solver's result, not a second measurement.", ""]
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path(RECORD)
    out.write_text(build(campaigns()), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
