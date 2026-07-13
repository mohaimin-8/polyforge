"""Effect-size-first restatement of the real-trace replay campaigns.

PRESENTATION AID, NOT A NEW EXPERIMENT: this script recomputes paired
effect sizes with bootstrap confidence intervals from the two *committed*
run tables (`eval/results/trace_replay_runs.csv`, n=16, and
`eval/results/trace_replay2_runs.csv`, n=96). No simulator run is
executed, no hypothesis is added or reopened, and no prereg verdict
changes: RESULTS_TRACE.md and RESULTS_TRACE2.md stand as measured.

WHY THIS EXISTS: with hundreds of paired simulated runs, p-values become
astronomically small and say more about the simulator's determinism than
about the effect. The defensible citation unit is the paired effect size
with an interval. This file gives, for every treatment-baseline pair and
metric: mean paired difference, Cohen's d_z, and 95% percentile bootstrap
CIs for both (B = 10,000, seed 42, resampling windows with replacement —
the window is the pairing unit, so the bootstrap respects the design).

Reading rules (stated once, here):
- HT2's confirmatory comparison is `jcac` vs each of hpa/keda/firm on J
  (PREREG_TRACE2 §3); `jcac_v2` rows are the declared secondary.
- The n=16 table is the underpowered first sample; it is shown for
  transparency, not citation. Cite the n=96 intervals.
- A CI that excludes 0 at the 95% level is consistent with — not a
  replacement for — the prereg's paired-t verdicts.

    python effect_sizes.py            # writes EFFECT_SIZES.md
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "EFFECT_SIZES.md"
TABLES = {
    "first sample (n=16, underpowered — transparency only)":
        ROOT / "eval" / "results" / "trace_replay_runs.csv",
    "powered sample (n=96, confirmatory — cite these)":
        ROOT / "eval" / "results" / "trace_replay2_runs.csv",
}
TREATMENTS = ("jcac", "jcac_v2")
BASELINES = ("hpa", "keda", "firm")
METRICS = (("J", "J"), ("total_cost_usd", "cost $"), ("mean_violation", "violation"))
B = 10_000
SEED = 42


def boot_ci(diffs: np.ndarray, stat, rng) -> tuple[float, float]:
    n = len(diffs)
    idx = rng.integers(0, n, size=(B, n))
    samples = diffs[idx]
    vals = stat(samples)
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def mean_stat(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=1)


def dz_stat(samples: np.ndarray) -> np.ndarray:
    sd = samples.std(axis=1, ddof=1)
    return samples.mean(axis=1) / np.where(sd == 0, np.nan, sd)


def main() -> None:
    rng = np.random.default_rng(SEED)
    lines = ["# Effect sizes with bootstrap CIs — real-trace replay campaigns", ""]
    w = lines.append
    w("Recomputed from the committed run tables (no new runs); see the "
      "docstring of `effect_sizes.py` for reading rules. d_z = mean paired "
      "diff / SD of paired diffs; 95% percentile bootstrap, "
      f"B = {B:,}, seed {SEED}, windows resampled as the pairing unit. "
      "Negative diffs favor the treatment (lower J/cost/violation).")
    w("")
    for label, path in TABLES.items():
        df = pd.read_csv(path)
        wide = df.pivot(index="window", columns="system")
        n = wide.shape[0]
        w(f"## {label}")
        w("")
        w("| treatment vs baseline | metric | mean diff [95% CI] | "
          "d_z [95% CI] |")
        w("|---|---|---|---|")
        for treatment in TREATMENTS:
            for baseline in BASELINES:
                for col, name in METRICS:
                    diffs = (wide[(col, treatment)] - wide[(col, baseline)]).to_numpy()
                    diffs = diffs[~np.isnan(diffs)]
                    if len(diffs) != n:
                        continue
                    md = float(diffs.mean())
                    sd = float(diffs.std(ddof=1))
                    dz = md / sd if sd else float("nan")
                    mlo, mhi = boot_ci(diffs, mean_stat, rng)
                    dlo, dhi = boot_ci(diffs, dz_stat, rng)
                    tag = " (confirmatory)" if (
                        treatment == "jcac" and col == "J") else ""
                    w(f"| {treatment} vs {baseline}{tag} | {name} | "
                      f"{md:+.3f} [{mlo:+.3f}, {mhi:+.3f}] | "
                      f"{dz:+.2f} [{dlo:+.2f}, {dhi:+.2f}] |")
        w("")
    w("Citation guidance: report the n=96 d_z with its CI; give the "
      "paired-t p-value once as the prereg gate outcome and do not "
      "headline p-values below ~1e-6 — at this many paired simulated "
      "runs they measure determinism, not effect.")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
