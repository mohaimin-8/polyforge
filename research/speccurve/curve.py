"""Specification curve with sign-flip inference (programme 2, WP5 step 2).

A specification is one combination of the evaluation's defensible choices
(budget symmetry, tuning granularity, price of an unserved request, weight
on attainment, hysteresis, summary statistic, primary metric, ...). For each
specification the effect is the paired difference between two arms on the
same workloads (positive = the first arm is better, i.e. has the lower
score). The curve sorts the specifications by effect and marks the choices
beneath (Simonsohn, Simmons and Nelson, "Specification curve analysis",
Nature Human Behaviour 4, 2020).

Inference for the whole curve (plan, WP5): flip the sign of each workload's
paired difference at random -- the same flip for that workload in every
specification, so the dependence between specifications is kept -- and
recompute the curve 2,000 times. Under the null of no effect in any
specification every flip pattern is equally likely, so the observed median
effect and the observed share of significant specifications are compared
with their sign-flip distributions (one-sided p-values, both directions
reported).

Input: a long table with one row per (specification choices..., workload,
arm) and a score column (lower is better).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

Z = 1.959963984540054


def enumerate_specs(choices: dict[str, list]) -> pd.DataFrame:
    """The full factorial of the choices, one row per specification."""
    names = list(choices)
    rows = [dict(zip(names, combo)) for combo in itertools.product(*(choices[n] for n in names))]
    out = pd.DataFrame(rows)
    out.insert(0, "spec", range(len(out)))
    return out


def paired_matrix(runs: pd.DataFrame, spec_cols: list[str], arm_a: str, arm_b: str,
                  score: str = "score", workload: str = "workload", arm: str = "arm") -> tuple[pd.DataFrame, np.ndarray]:
    """(specs, D): D[s, w] = score(arm_b) - score(arm_a) for specification s
    and workload w, so a positive entry means arm_a is better. Every
    specification must hold both arms on the same workloads."""
    wide = runs.pivot_table(index=spec_cols + [workload], columns=arm, values=score, aggfunc="first")
    missing = {arm_a, arm_b} - set(wide.columns)
    if missing:
        raise ValueError(f"arms not in the table: {sorted(missing)}")
    diff = (wide[arm_b] - wide[arm_a]).unstack(workload)
    if diff.isna().any().any():
        raise ValueError("some specification lacks a workload for one of the arms")
    specs = diff.index.to_frame(index=False)
    return specs, diff.to_numpy(float)


@dataclass(frozen=True)
class Curve:
    specs: pd.DataFrame       # one row per specification, sorted by effect
    effect: np.ndarray        # mean paired difference per specification
    lo: np.ndarray            # 95% interval (t-free normal approximation over workloads)
    hi: np.ndarray

    @property
    def median_effect(self) -> float:
        return float(np.median(self.effect))

    def share_significant(self, direction: int) -> float:
        return float(np.mean(self.lo > 0)) if direction > 0 else float(np.mean(self.hi < 0))


def curve_from_matrix(specs: pd.DataFrame, d: np.ndarray) -> Curve:
    n = d.shape[1]
    if n < 2:
        raise ValueError("need at least two workloads")
    mean = d.mean(axis=1)
    se = d.std(axis=1, ddof=1) / np.sqrt(n)
    order = np.argsort(mean, kind="stable")
    return Curve(specs.iloc[order].reset_index(drop=True), mean[order],
                 (mean - Z * se)[order], (mean + Z * se)[order])


def _stats(d: np.ndarray) -> tuple[float, float, float]:
    """(median effect, share significantly positive, share significantly negative)."""
    n = d.shape[1]
    mean = d.mean(axis=1)
    se = d.std(axis=1, ddof=1) / np.sqrt(n)
    return float(np.median(mean)), float(np.mean(mean - Z * se > 0)), float(np.mean(mean + Z * se < 0))


def sign_flip_test(d: np.ndarray, draws: int = 2000, seed: int = 20261112) -> dict:
    """Sign-flip null for the whole curve: each workload's column is
    multiplied by +-1, the same sign in every specification."""
    rng = np.random.default_rng(seed)
    obs = _stats(d)
    null = np.empty((draws, 3))
    for i in range(draws):
        flips = rng.choice((-1.0, 1.0), size=d.shape[1])
        null[i] = _stats(d * flips)
    # one-sided p-values with the observed draw counted (Phipson and Smyth 2010)
    p = lambda col, val: float((1 + np.sum(null[:, col] >= val - 1e-15)) / (draws + 1))  # noqa: E731
    q = lambda col, val: float((1 + np.sum(null[:, col] <= val + 1e-15)) / (draws + 1))  # noqa: E731
    return {
        "median_effect": obs[0], "share_significant_positive": obs[1], "share_significant_negative": obs[2],
        "p_median_positive": p(0, obs[0]), "p_median_negative": q(0, obs[0]),
        "p_share_positive": p(1, obs[1]), "p_share_negative": p(2, obs[2]),
        "draws": draws,
    }


def report(curve: Curve, test: dict, arm_a: str, arm_b: str, choice_cols: list[str]) -> str:
    """Markdown: the sorted curve with its choices, and the whole-curve test."""
    L = [f"Effect = score({arm_b}) - score({arm_a}) per workload, averaged over workloads; positive means "
         f"{arm_a} is better. {len(curve.effect)} specifications, sorted by effect.", "",
         "| rank | effect [95% CI] | " + " | ".join(choice_cols) + " |",
         "|---:|---|" + "---|" * len(choice_cols)]
    for i, row in curve.specs.iterrows():
        L.append(f"| {i + 1} | {curve.effect[i]:+.4f} [{curve.lo[i]:+.4f}, {curve.hi[i]:+.4f}] | "
                 + " | ".join(str(row[c]) for c in choice_cols) + " |")
    L += ["", f"Whole-curve test ({test['draws']} sign flips of each workload's paired difference): median "
          f"effect {test['median_effect']:+.4f} (p = {test['p_median_positive']:.4f} that {arm_a} is better, "
          f"{test['p_median_negative']:.4f} that it is worse); significant specifications "
          f"{test['share_significant_positive']:.0%} for {arm_a} (p = {test['p_share_positive']:.4f}), "
          f"{test['share_significant_negative']:.0%} against (p = {test['p_share_negative']:.4f}).", ""]
    return "\n".join(L)


def plot(curve: Curve, choice_cols: list[str], path, title: str = "") -> None:
    """The curve above, the choices marked beneath (one row per choice level)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    levels = [(c, v) for c in choice_cols for v in sorted(curve.specs[c].astype(str).unique())]
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(10, 3 + 0.25 * len(levels)), sharex=True,
                                      gridspec_kw={"height_ratios": [3, max(1, len(levels) // 3)]})
    x = np.arange(len(curve.effect))
    sig = (curve.lo > 0) | (curve.hi < 0)
    top.vlines(x, curve.lo, curve.hi, color="0.75", linewidth=1)
    top.scatter(x[~sig], curve.effect[~sig], s=10, color="0.45", label="interval holds 0")
    top.scatter(x[sig], curve.effect[sig], s=12, color="black", label="interval excludes 0")
    top.axhline(0, color="black", linewidth=0.8)
    top.set_ylabel("effect")
    top.legend(frameon=False, fontsize=8)
    if title:
        top.set_title(title, fontsize=10)
    for j, (c, v) in enumerate(levels):
        on = curve.specs[c].astype(str).to_numpy() == v
        bottom.scatter(x[on], np.full(on.sum(), j), s=8, marker="|", color="black")
    bottom.set_yticks(range(len(levels)))
    bottom.set_yticklabels([f"{c} = {v}" for c, v in levels], fontsize=7)
    bottom.set_xlabel("specification (sorted by effect)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
