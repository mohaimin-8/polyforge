"""Every registered non-inferiority hypothesis under ONE rule
-> REANALYSIS_NONINFERIORITY.md.

EXPLORATORY re-scoring; the registered verdicts stand as committed and are
printed beside the unified reading. The audit of 2026-09-26 found the same
inherited 0.05 margin tested three different ways:

* EP-H3: the point estimate compared with the margin -- no test at all;
* TP-H3a/b, BP-H2a/b, MM-H2: a one-sided Wilcoxon signed-rank test on
  (difference - margin). A rank test on shifted differences can pass while
  the MEAN difference exceeds the margin (Azure TP-H3a: mean +0.0666;
  BurstGPT BP-H2: mean 0.104, median exactly 0), and its registered
  justification ("differences are not symmetric") is backwards -- the
  signed-rank test assumes symmetry.

The unified rule is the standard CI form: a treatment is non-inferior on a
lower-is-better metric iff the one-sided 95% upper confidence bound of the
mean difference (treatment - comparator) is below the margin. The bound is a
percentile bootstrap over the independent unit: the design point for the
simulator matrices (reps averaged), the replay window for the traces, with
day-long moving blocks for Azure, whose windows are back-to-back.

    python reanalysis_noninferiority.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from reanalysis_matrix import AZURE_BLOCK, DESIGN  # noqa: E402
from stats import record_path  # noqa: E402

RESULTS = HERE.parents[1] / "eval" / "results"
RECORD = "REANALYSIS_NONINFERIORITY.md"
MARGIN = 0.05
METRIC = "mean_excess"
BOOT_N = 10_000
BOOT_SEED = 20260926
BELIEFS = [("cap", "0.75", "75"), ("cap", "1.25", "125"), ("tier", "0.75", "75"),
           ("tier", "1.25", "125"), ("cache", "0.75", "75"), ("cache", "1.25", "125")]

# (id, record, record section, data file, unit, treatment, comparator, registered rule)
SPECS = [("EP-H3", "RESULTS_EVICTION_PARITY.md", None, "metrics_matrix_eviction_parity.csv.gz",
          "design", "jcac", "hpa_fair", "point estimate <= margin, no test")]
for trace, fname, unit in (("BurstGPT", "trace_parity_burstgpt_runs.csv", "window"),
                           ("Azure", "trace_parity_azure_runs.csv", "block")):
    for suffix, comp in (("a", "hpa_fair"), ("b", "keda_fair")):
        SPECS.append((f"TP-H3{suffix}", "RESULTS_TRACE_PARITY.md", trace, fname, unit,
                      "jcac", comp, "shifted one-sided Wilcoxon, Holm family of 4"))
for trace, fname, unit in (("BurstGPT", "budget_parity_burstgpt_runs.csv", "window"),
                           ("Azure", "budget_parity_azure_runs.csv", "block")):
    for suffix, comp in (("a", "hpa_fair"), ("b", "keda_fair")):
        SPECS.append((f"BP-H2{suffix}", "RESULTS_BUDGET_PARITY.md", trace, fname, unit,
                      "jcac_nobudget", comp, "shifted one-sided Wilcoxon, Holm family of 4"))
for short, scale, tag in BELIEFS:
    SPECS.append((f"MM-H2[{short}{scale}]", "RESULTS_MODEL_MISMATCH.md", None,
                  "metrics_matrix_model_mismatch.csv.gz", "design", f"jcac_belief_{short}{tag}",
                  "hpa_fair", "shifted one-sided Wilcoxon, Holm family of 12"))


def upper_bound(d: np.ndarray, block: int, n: int = BOOT_N, seed: int = BOOT_SEED) -> float:
    """One-sided 95% upper confidence bound of the mean (percentile bootstrap;
    moving blocks when `block` > 1)."""
    rng = np.random.default_rng(seed)
    m = len(d)
    if block <= 1:
        means = d[rng.integers(0, m, size=(n, m))].mean(axis=1)
    else:
        starts = rng.integers(0, m - block + 1, size=(n, -(-m // block)))
        idx = (starts[:, :, None] + np.arange(block)).reshape(n, -1)[:, :m]
        means = d[idx].mean(axis=1)
    return float(np.percentile(means, 95.0))


def unified(d: np.ndarray, block: int) -> dict:
    d = np.asarray(d, dtype=float)
    ucb = upper_bound(d, block)
    return {"n": len(d), "mean": float(d.mean()), "median": float(np.median(d)),
            "over": float((d > MARGIN).mean()), "ucb": ucb, "ni": ucb < MARGIN}


def differences(df: pd.DataFrame, unit: str, treat: str, comp: str) -> np.ndarray:
    if unit == "design":
        a = df[df.system == treat].groupby(DESIGN)[METRIC].mean()
        b = df[df.system == comp].groupby(DESIGN)[METRIC].mean()
        return (a - b).dropna().to_numpy()
    m = df[df.system == treat].merge(df[df.system == comp], on="window", suffixes=("_t", "_c"))
    m = m.sort_values("window")
    return (m[f"{METRIC}_t"] - m[f"{METRIC}_c"]).to_numpy()


def registered(record: str, hid: str, section: str | None) -> str:
    """The verdict the committed record gives `hid` (first word of the last
    table cell), within the `## ` section whose title contains `section`."""
    text = (HERE / record).read_text(encoding="utf-8")
    if section is not None:
        parts = re.split(r"(?m)^## ", text)
        text = next(p for p in parts if p.startswith(section) or p.split("\n", 1)[0].find(section) >= 0)
    row = re.search(r"(?m)^\| (?:\*\*)?" + re.escape(hid) + r"(?:\*\*)? \|.*\| ([^|]+) \|$", text)
    if row is None:
        raise KeyError(f"{hid} not found in {record} section {section}")
    return row.group(1).strip().split()[0]


def build() -> str:
    L = ["# Every registered non-inferiority hypothesis under one rule", "",
         "Generated by `reanalysis_noninferiority.py`. **Exploratory re-scoring; the registered verdicts "
         "stand as committed and are printed beside the unified reading.**", "",
         f"**The rule.** On `{METRIC}` (lower is better) a treatment is non-inferior to a comparator iff "
         f"the one-sided 95% upper confidence bound of the mean difference (treatment − comparator) is below "
         f"the margin {MARGIN}. The bound is a percentile bootstrap ({BOOT_N:,} resamples, seed {BOOT_SEED}) "
         "over the independent unit: the design point for the simulator matrices (reps averaged), the replay "
         f"window for the traces, with moving blocks of {AZURE_BLOCK} windows (one day) for Azure, whose "
         "windows are back-to-back.", "",
         "**Why.** The margin was tested three ways: EP-H3 by the point estimate alone; TP-H3, BP-H2 and "
         "MM-H2 by a one-sided Wilcoxon on shifted differences, which can pass when the mean difference is "
         "above the margin, and whose registered justification (\"the differences are not symmetric\") is "
         "backwards: the signed-rank test assumes symmetry.", "",
         "| id | data | treatment vs comparator | units | mean diff | median diff | units above margin | "
         "95% upper bound | unified | registered (rule) | agree? |",
         "|---|---|---|---:|---:|---:|---:|---:|---|---|---|"]
    cache: dict[str, pd.DataFrame] = {}
    flips = []
    for hid, record, section, fname, unit, treat, comp, rule in SPECS:
        df = cache.setdefault(fname, pd.read_csv(RESULTS / fname))
        res = unified(differences(df, unit, treat, comp), AZURE_BLOCK if unit == "block" else 1)
        reg = registered(record, hid, section)
        uni = "PASS" if res["ni"] else "FAIL"
        agree = "yes" if uni == reg else "**NO**"
        if uni != reg:
            flips.append(f"{hid} ({section or fname.split('.')[0]})")
        where = section or "synthetic matrix"
        L.append(f"| {hid} | {where} | `{treat}` vs `{comp}` | {res['n']} | {res['mean']:+.4f} | "
                 f"{res['median']:+.4f} | {res['over']:.0%} | {res['ucb']:+.4f} | **{uni}** | "
                 f"{reg} ({rule}) | {agree} |")
    L += ["", "## Reading", ""]
    if flips:
        L.append(f"**{len(flips)} verdict(s) change under the unified rule:** {', '.join(flips)}. The "
                 "registered verdicts stand as committed; a claim resting on them should cite this re-scoring "
                 "beside them.")
    else:
        L.append("Every registered verdict survives the unified rule.")
    L += ["", "Nothing here is confirmatory. A future registration should use the unified rule.", ""]
    return "\n".join(L) + "\n"


def main() -> int:
    out = record_path(RECORD)
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
