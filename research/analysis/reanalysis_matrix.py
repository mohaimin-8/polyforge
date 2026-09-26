"""Design-point re-analysis of the composite objective, including the fair
comparators -> REANALYSIS_MATRIX.md.

EXPLORATORY. Not pre-registered; it changes no registered verdict. It answers
two questions the audit of 2026-09-26 raised about the committed data:

1. **Unit of analysis.** The headline tests pool 300 (design point x rep)
   pairs into one t-test. The 5 reps of a design point share its workload,
   tenant mix and cluster size, so they are not independent replicates of the
   population the claim is about, and p shrinks with the rep count. Here the
   unit is the design point (60): reps are averaged within each, then paired.

2. **Fair comparators.** The composite J was scored only against the
   originally tuned baselines, which the project's own eviction-parity audit
   found carried an LRU charge and a pinned 128 MB cache that no JCAC arm
   paid. Here J is also scored against `hpa_fair` / `keda_fair`, within the
   eviction-parity campaign (whose JCAC runs are its own: the seed includes
   the experiment name), and on both real-trace parity campaigns.

A confirmatory test of (2) on fresh seeds is Phase 3's pre-registration; this
record is what that registration is written against, and says so.

    python reanalysis_matrix.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from stats import composite_objective, record_path  # noqa: E402

RESULTS = HERE.parents[1] / "eval" / "results"
RECORD = "REANALYSIS_MATRIX.md"
DESIGN = ["workload", "tenant_mix", "cluster_size"]
BOOT_N = 10_000
BOOT_SEED = 20260926
AZURE_BLOCK = 8          # 8 x 3 h windows = one day: Azure windows are back-to-back
CAMPAIGNS = [
    ("headline matrix (as published)", "metrics_full.csv.gz",
     ["hpa", "keda", "firm", "static", "gptcache"]),
    ("eviction-parity matrix (fair comparators)", "metrics_matrix_eviction_parity.csv.gz",
     ["hpa", "keda", "hpa_fair", "keda_fair"]),
]
TRACES = [("Azure LLM 2024", "trace_parity_azure_runs.csv", AZURE_BLOCK),
          ("BurstGPT", "trace_parity_burstgpt_runs.csv", 1)]
TRACE_ARMS = ["hpa", "keda", "hpa_fair", "keda_fair"]


def bootstrap_ci(x: np.ndarray, block: int = 1, n: int = BOOT_N,
                 seed: int = BOOT_SEED) -> tuple[float, float]:
    """95% percentile CI of the mean; `block` > 1 is a moving-block bootstrap
    for serially dependent units (consecutive trace windows)."""
    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(seed)
    m = len(x)
    if block <= 1:
        means = x[rng.integers(0, m, size=(n, m))].mean(axis=1)
    else:
        starts = rng.integers(0, m - block + 1, size=(n, -(-m // block)))
        idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, :m]
        means = x[idx].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _design_means(df: pd.DataFrame, system: str, metric: str) -> pd.Series:
    return df[df.system == system].groupby(DESIGN)[metric].mean()


def compare(df: pd.DataFrame, a: str, b: str, metric: str) -> dict:
    """a − b on `metric`, one paired difference per design point."""
    d = (_design_means(df, a, metric) - _design_means(df, b, metric)).dropna()
    base = float(_design_means(df, b, metric).loc[d.index].mean())
    sd = float(d.std(ddof=1))
    lo, hi = bootstrap_ci(d.to_numpy())
    return {"n": len(d), "mean_diff": float(d.mean()),
            "rel": float(d.mean() / base) if base else float("nan"),
            "dz": float(d.mean() / sd) if sd else 0.0, "ci": (lo, hi),
            "p_wilcoxon": float(sps.wilcoxon(d).pvalue) if d.abs().sum() else 1.0,
            "better_share": float((d < 0).mean())}


def per_workload(df: pd.DataFrame, a: str, b: str, metric: str) -> list[dict]:
    d = (_design_means(df, a, metric) - _design_means(df, b, metric)).dropna()
    rows = []
    for wl, sub in d.groupby(level="workload"):
        lo, hi = bootstrap_ci(sub.to_numpy())
        rows.append({"workload": wl, "n": len(sub), "mean_diff": float(sub.mean()),
                     "ci": (lo, hi), "better": int((sub < 0).sum())})
    return rows


def pooled(df: pd.DataFrame, a: str, b: str) -> dict:
    """The published reading: 300 (design point x rep) pairs, one t-test."""
    keys = DESIGN + ["rep"]
    m = df[df.system == a].merge(df[df.system == b], on=keys, suffixes=("_a", "_b"))
    d = m.J_a - m.J_b
    return {"n": len(d), "dz": float(d.mean() / d.std(ddof=1)),
            "p": float(sps.ttest_1samp(d, 0.0).pvalue)}


def decompose(df: pd.DataFrame, a: str, b: str) -> dict:
    """Mean design-point ΔJ split into the objective's three terms."""
    terms = df.assign(t_cost=df.total_cost_usd / (119 * 8) / 0.01,
                      t_viol=2.0 * df.mean_violation, t_fair=0.5 * (1.0 - df.mean_jain))
    return {t: compare(terms, a, b, t)["mean_diff"] for t in ("t_cost", "t_viol", "t_fair")}


def _fmt_ci(ci: tuple[float, float]) -> str:
    return f"[{ci[0]:+.4f}, {ci[1]:+.4f}]"


def _p(p: float) -> str:
    return f"{p:.2e}" if p < 1e-3 else f"{p:.3f}"


def build() -> str:
    L = ["# Design-point re-analysis of the composite objective, with the fair comparators", "",
         "Generated by `reanalysis_matrix.py` from committed exports. **Exploratory: not pre-registered, "
         "and it changes no registered verdict.** It re-reads committed data in two ways the audit of "
         "2026-09-26 asked for: at the level of the independent unit (the design point: workload x tenant "
         "mix x cluster size, reps averaged), and against the fair comparators (`hpa_fair`, `keda_fair`: "
         "no LRU charge, 512 MB pre-sized cache). A confirmatory test of the second question on fresh "
         "seeds is a separate registration, written against this record.", "",
         f"`J` is the project's own `composite_objective` (`stats.py`). Intervals are 95% percentile "
         f"bootstraps over design points ({BOOT_N:,} resamples, seed {BOOT_SEED}). Negative = JCAC better.", ""]
    for title, fname, arms in CAMPAIGNS:
        df = pd.read_csv(RESULTS / fname)
        df = df.assign(J=composite_objective(df))
        L += [f"## {title}", "", f"Source: `eval/results/{fname}`.", "",
              "| JCAC vs | design points | ΔJ | Δ% | d_z | 95% CI of ΔJ | Wilcoxon p | JCAC better in | "
              "published (pooled) d_z, p |",
              "|---|---:|---:|---:|---:|---|---:|---:|---|"]
        for arm in arms:
            r, pl = compare(df, "jcac", arm, "J"), pooled(df, "jcac", arm)
            L.append(f"| `{arm}` | {r['n']} | {r['mean_diff']:+.4f} | {r['rel']:+.1%} | {r['dz']:+.2f} | "
                     f"{_fmt_ci(r['ci'])} | {_p(r['p_wilcoxon'])} | {r['better_share']:.0%} | "
                     f"{pl['dz']:+.2f}, {_p(pl['p'])} (n={pl['n']}) |")
        L += ["", "Where ΔJ comes from (mean design-point difference per objective term):", "",
              "| JCAC vs | cost term | violation term | fairness term |", "|---|---:|---:|---:|"]
        for arm in arms:
            dc = decompose(df, "jcac", arm)
            L.append(f"| `{arm}` | {dc['t_cost']:+.4f} | {dc['t_viol']:+.4f} | {dc['t_fair']:+.4f} |")
        focus = [a for a in arms if a.endswith("_fair")] or arms[:1]
        for arm in focus:
            L += ["", f"Per workload, JCAC vs `{arm}` (12 design points each):", "",
                  "| workload | ΔJ | 95% CI | JCAC better in |", "|---|---:|---|---:|"]
            for r in per_workload(df, "jcac", arm, "J"):
                L.append(f"| `{r['workload']}` | {r['mean_diff']:+.4f} | {_fmt_ci(r['ci'])} | "
                         f"{r['better']}/{r['n']} |")
        L.append("")
    L += ["## Real-trace parity campaigns", "",
          "The unit is the replay window, paired across arms by window (shared arrival jitter). Azure's 72 "
          f"windows are back-to-back 3 h slices of one 9-day trace, so its interval is a moving-block "
          f"bootstrap with blocks of {AZURE_BLOCK} windows (one day); BurstGPT's windows do not overlap and "
          "use the i.i.d. bootstrap.", "",
          "| trace | JCAC vs | windows | ΔJ | Δ% | 95% CI of ΔJ | median ΔJ | JCAC better in |",
          "|---|---|---:|---:|---:|---|---:|---:|"]
    for title, fname, block in TRACES:
        df = pd.read_csv(RESULTS / fname)
        for arm in TRACE_ARMS:
            m = df[df.system == "jcac"].merge(df[df.system == arm], on="window", suffixes=("_a", "_b"))
            m = m.sort_values("window")
            d = (m.J_a - m.J_b).to_numpy()
            lo, hi = bootstrap_ci(d, block=block)
            L.append(f"| {title} | `{arm}` | {len(d)} | {d.mean():+.4f} | {d.mean() / m.J_b.mean():+.1%} | "
                     f"{_fmt_ci((lo, hi))} | {np.median(d):+.4f} | {np.mean(d < 0):.0%} |")
    L += ["", "## Reading", "",
          "* Against the originally tuned baselines the composite win holds at the design-point level too; "
          "compare its d_z with the pooled one printed beside it.",
          "* Against the fair comparators, read the ΔJ intervals and the per-workload rows directly: an "
          "interval that includes 0 is not a win, and a positive one is a loss.",
          "* Nothing here is confirmatory. The registered records stand as committed.", ""]
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path(RECORD)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
