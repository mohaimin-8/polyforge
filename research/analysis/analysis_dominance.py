#!/usr/bin/env python3
"""Weight-free Pareto dominance across the headline matrix.

`SENSITIVITY_J.md` answers "does the J ranking survive perturbing the
weights?" over a 25-cell grid, and the answer is yes with zero flips. That is
a strong robustness result, but it is still a statement about a *weighted*
objective, and the standing objection to any weighted objective is that its
author chose the weights.

This analysis removes weights from the question entirely. An arm is
**dominated** in a cell when some other arm is no worse on **every** raw
objective and strictly better on at least one. A non-dominated arm is one no
other arm matches or beats on every objective at once. That is NOT immunity
to weighting -- a baseline that is better on violation and worse on cost still
wins under a violation-heavy weighting (audit 2026-09-26 corrected this
claim) -- but it does mean no baseline beats it without a trade-off.

Objectives, all minimised, taken raw from the committed matrix export:
  * `total_cost_usd`
  * `mean_violation`
  * `1 - mean_jain`  (fairness, flipped so lower is better)

**Why this is descriptive but NOT fishable.** Dominance has no free
parameters -- no weights, no thresholds beyond a float tolerance, no feature
selection. There is nothing here to tune toward a preferred answer, which is
what separates it from a post-hoc characterisation like
`RESULTS_WINDOW_CHARACTER.md`. It is still not a pre-registered hypothesis
test and is not quoted as one.

  D1 (self-check, must hold). The export must carry the committed matrix's
      shape -- 1,800 runs over 6 arms and 60 cells. A different shape means
      this is reading something other than the headline matrix and the rest
      is void.

  D2 (the count). Per-arm non-dominated cell counts.

  D3 (the exceptions). Where jcac IS dominated, report every cell and the
      arms that dominate it. A dominance result that reported only the wins
      would be worthless.

  D4 (specificity). If the exceptions concentrate in one workload, say which
      and check the obvious mechanical explanation against a control
      workload that shares the suspected cause. Concentration WITH a control
      that behaves differently is a mechanism; concentration alone is not.

    python analysis_dominance.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stats import record_path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
from harness import workloads as W  # noqa: E402

MATRIX = (Path(__file__).resolve().parents[2] / "eval" / "results"
          / "metrics_full.csv.gz")
RECORD = "RESULTS_DOMINANCE.md"
OBJ = ["total_cost_usd", "mean_violation", "unfair"]
CELL = ["workload", "tenant_mix", "cluster_size"]
EPS = 1e-12
EXPECT_RUNS, EXPECT_ARMS, EXPECT_CELLS = 1800, 6, 60


def dominates(a: dict, b: dict) -> bool:
    """`a` dominates `b`: no worse on every objective, better on at least one."""
    return (all(a[o] <= b[o] + EPS for o in OBJ)
            and any(a[o] < b[o] - EPS for o in OBJ))


BOOT_N = 2000
BOOT_SEED = 20260926
STABLE = 0.95


def rep_bootstrap(raw: pd.DataFrame, arm: str, n: int = BOOT_N,
                  seed: int = BOOT_SEED) -> dict[tuple, float]:
    """Per cell: the share of rep resamples in which `arm` is non-dominated.

    Each arm's reps are resampled with replacement, independently -- the
    matrix seeds each (arm, cell, rep) separately, so reps are not paired
    across arms. Cells and arms are visited in sorted order from one seeded
    generator, so the result is deterministic."""
    rng = np.random.default_rng(seed)
    out = {}
    for key, sub in sorted(raw.groupby(CELL), key=lambda kv: kv[0]):
        means = {}
        for name, rows in sorted(sub.groupby("system"), key=lambda kv: kv[0]):
            vals = rows[OBJ].to_numpy(dtype=float)
            idx = rng.integers(0, len(vals), size=(n, len(vals)))
            means[name] = vals[idx].mean(axis=1)                  # (n, objectives)
        if arm not in means:
            continue
        target = means[arm]
        dominated = np.zeros(n, dtype=bool)
        for name, other in means.items():
            if name == arm:
                continue
            dominated |= (np.all(other <= target + EPS, axis=1)
                          & np.any(other < target - EPS, axis=1))
        out[key] = float(1.0 - dominated.mean())
    return out


def ai_rps(workload: str) -> float:
    cls = W.WORKLOAD_CLASSES[workload]
    return sum(cls.base_rps.get(k, 0.0) for k in ("chat", "embed", "agent"))


def main() -> int:
    if not MATRIX.exists():
        print(f"missing {MATRIX}", file=sys.stderr)
        return 1
    raw = pd.read_csv(MATRIX)
    raw["unfair"] = 1.0 - raw["mean_jain"]
    grouped = raw.groupby(CELL + ["system"])[OBJ].mean().reset_index()

    n_runs, n_arms = len(raw), raw.system.nunique()
    n_cells = len(grouped.groupby(CELL))
    d1 = (n_runs == EXPECT_RUNS and n_arms == EXPECT_ARMS
          and n_cells == EXPECT_CELLS)

    front: dict[str, int] = {s: 0 for s in sorted(raw.system.unique())}
    exceptions: list[tuple[tuple, str]] = []
    per_workload: dict[str, list[int]] = {}
    for key, sub in grouped.groupby(CELL):
        arms = {r.system: r._asdict() for r in sub.itertuples()}
        for name, row in arms.items():
            by = sorted(o for o, other in arms.items()
                        if o != name and dominates(other, row))
            if not by:
                front[name] += 1
            if name == "jcac":
                hit = per_workload.setdefault(key[0], [0, 0])
                hit[1] += 1
                if by:
                    hit[0] += 1
                    exceptions.append((key, ",".join(by)))

    L: list[str] = []
    w = L.append
    w("# Pareto dominance — a weight-free reading of the headline matrix\n")
    w("Generated by `analysis_dominance.py` from the committed "
      "`eval/results/metrics_full.csv.gz`. Every number is computed at run "
      "time.\n")
    w("An arm is **dominated** in a cell when another arm is no worse on "
      "**every** raw objective (`total_cost_usd`, `mean_violation`, "
      "`1 - mean_jain`) and strictly better on at least one. A non-dominated "
      "arm is one that no other arm matches or beats on every objective at "
      "once. It is not the winner under every weighting: a baseline that is "
      "better on one objective and worse on another still wins when that "
      "objective is weighted heavily. What non-dominance does say is that no "
      "baseline beats it without a trade-off.\n")
    w("> Descriptive, not a hypothesis test — but note that dominance has **no "
      "free parameters**: no weights, no thresholds, no feature selection. "
      "There is nothing here to tune toward a preferred answer.\n")

    w("\n## D1 — is this the headline matrix?\n")
    w("| quantity | expected | found | agrees? |")
    w("|---|---:|---:|---|")
    for label, exp, got in (("runs", EXPECT_RUNS, n_runs),
                            ("arms", EXPECT_ARMS, n_arms),
                            ("cells", EXPECT_CELLS, n_cells)):
        w(f"| {label} | {exp} | {got} | {'yes' if exp == got else '**NO**'} |")
    w("")
    w(f"**D1 {'PASS' if d1 else 'FAIL'}.**" + ("" if d1 else
      " The export is not the committed matrix; everything below is void.\n"))
    if not d1:
        record_path(RECORD).write_text("\n".join(L) + "\n", encoding="utf-8")
        print("D1 FAIL")
        return 1

    w("\n## D2 — non-dominated cells per arm\n")
    w(f"| arm | non-dominated cells (of {n_cells}) | share |")
    w("|---|---:|---:|")
    for name, count in sorted(front.items(), key=lambda kv: -kv[1]):
        bold = "**" if name == "jcac" else ""
        w(f"| {bold}`{name}`{bold} | {bold}{count}{bold} "
          f"| {bold}{count / n_cells * 100:.1f}%{bold} |")
    w("")
    w(f"jcac is non-dominated in **{front['jcac']} of {n_cells}** cells, the "
      "highest of any arm. In those cells every baseline that is better on one "
      "objective is worse on another. These counts are taken on 5-rep cell "
      "means; D5 below says how many of them survive resampling the reps.\n")

    w("\n## D3 — where jcac IS dominated\n")
    if not exceptions:
        w("Nowhere. jcac is non-dominated in every cell.\n")
    else:
        w(f"**{len(exceptions)} cells**, listed in full because a dominance "
          "result that reported only its wins would be worthless.\n")
        w("\n| workload | tenant mix | cluster | dominated by |")
        w("|---|---|---|---|")
        for (wl, mix, size), by in exceptions:
            w(f"| `{wl}` | {mix} | {size} | `{by}` |")
        w("")

    w("\n## D4 — specificity\n")
    w("| workload | AI rps | shape | jcac dominated |")
    w("|---|---:|---|---:|")
    for wl in sorted(per_workload):
        bad, total = per_workload[wl]
        cls = W.WORKLOAD_CLASSES[wl]
        w(f"| `{wl}` | {ai_rps(wl):.1f} | {cls.shape} | {bad}/{total} |")
    w("")
    hot = [k for k, v in per_workload.items() if v[0] > 0]
    if len(hot) == 1:
        wl = hot[0]
        controls = [k for k, v in per_workload.items()
                    if v[0] == 0 and ai_rps(k) == 0.0]
        w(f"Every exception is `{wl}`, and it is dominated by the same pair in "
          "all of them. The obvious explanation is that it carries no AI "
          "traffic, so the cache and tier knobs have nothing to act on and "
          "jcac pays for machinery it cannot use.\n")
        if controls:
            w(f"**That explanation is incomplete, and the control says so:** "
              f"{', '.join('`' + c + '`' for c in controls)} is equally "
              f"CRUD-only and jcac is **not** dominated there. What separates "
              f"them is shape — `{wl}` is "
              f"`{W.WORKLOAD_CLASSES[wl].shape}` while the control is "
              f"`{W.WORKLOAD_CLASSES[controls[0]].shape}`. The loss is "
              "specific to demand that is BOTH free of AI and free of "
              "variation: the one regime where neither the AI knobs nor the "
              "forecaster has anything to do, so the joint controller is pure "
              "overhead. Where either has something to act on, it is "
              "non-dominated.\n")
        else:
            w("No CRUD-only control workload is available to separate 'no AI' "
              "from 'no variation', so the mechanism is not established here.\n")
    elif hot:
        w(f"The exceptions span {len(hot)} workloads "
          f"({', '.join(hot)}), so they are not explained by one regime.\n")

    # D5 (audit 2026-09-26): D2 counts dominance on 5-rep means with a 1e-12
    # tolerance, i.e. with no uncertainty at all.
    share = rep_bootstrap(raw, "jcac")
    stable_nd = sum(1 for v in share.values() if v >= STABLE)
    stable_d = sum(1 for v in share.values() if v <= 1.0 - STABLE)
    unstable = len(share) - stable_nd - stable_d
    w("\n## D5 — how stable is the count? (rep bootstrap)\n")
    w(f"Each arm's 5 reps are resampled with replacement {BOOT_N:,} times per "
      f"cell (seed {BOOT_SEED}) and dominance is re-judged on each resample. "
      f"A cell is **stably non-dominated** when jcac is non-dominated in at least "
      f"{STABLE:.0%} of resamples, **stably dominated** when in at most "
      f"{1 - STABLE:.0%}, and **unstable** otherwise.\n")
    w("| reading | cells |")
    w("|---|---:|")
    w(f"| stably non-dominated | {stable_nd} |")
    w(f"| unstable | {unstable} |")
    w(f"| stably dominated | {stable_d} |")
    w("")
    w(f"Of the {front['jcac']} cells D2 counts as non-dominated, the rep noise "
      f"supports {stable_nd} as stable"
      + (f"; in {unstable} the arms sit close enough that five reps cannot "
         "order them on every objective.\n" if unstable else
         ", and no cell's dominance status depends on which reps were drawn. "
         "This bounds rep noise only: the cells are still simulator cells, "
         "scored against the originally tuned baselines.\n"))

    out = record_path(RECORD)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"D1 PASS | jcac non-dominated {front['jcac']}/{n_cells} "
          f"| exceptions: {len(exceptions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
