"""Score the simulator's committed predictions against the testbed's runs (WP11).

Per cell, the measured value of each metric is the mean over its valid runs
(run_cells.py marks a run invalid when the load generator fell behind or the
posture did not read back):
  mean_latency_s, p95_latency_s  answered (status 200), scored (not warm-up) requests
  attainment                      answered within the cell's SLO over ALL scored requests
                                  (check_run.summarise's definition: a failure is a miss)
  throughput_rps                  answered requests / scored seconds
  cost                            pod-cores x price x duration, as predicted (fixed pools)
Per metric across cells: median absolute percentage error, Spearman rank
correlation, and Bland-Altman (mean difference, 95% limits of agreement;
validation_bland_altman.png). Targets (cells.json, fixed before any run):
rank correlation >= 0.8 and median absolute error <= 25% on attainment and
cost. Reported beside them, without targets: the cache hit rate (predicted
by the replay of each cell's prompts against the gateway's own count) and
the declared secondary predictions `steady_cache` and `measured_capacity`.

    python score.py --runs ../out/validation      # cellNN/runK/{run.json, k6-samples.csv.gz}
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

from check_run import quantile, read_k6_requests, summarise  # noqa: E402

METRICS = ("mean_latency_s", "p95_latency_s", "attainment", "throughput_rps", "cost")


def measure_run(path: Path, slo_ms: float, scored_s: float) -> dict:
    reqs = read_k6_requests(path)
    s = summarise(reqs, slo_ms)
    ok = [r["latency_ms"] for r in reqs if not r["warmup"] and r["status"] == "200"]
    return {"mean_latency_s": float(np.mean(ok)) / 1000 if ok else math.nan,
            "p95_latency_s": quantile(ok, 0.95) / 1000 if ok else math.nan,
            "attainment": s["attainment"], "throughput_rps": len(ok) / scored_s, "hit_rate": s["hit_rate"],
            "failed_rate": s["failed_rate"]}


def average_ranks(x) -> np.ndarray:
    """Ranks 1..n with each group of tied values given the mean of its ranks."""
    x = np.asarray(x, dtype=float)
    ranks = np.empty(len(x))
    ranks[np.argsort(x, kind="stable")] = np.arange(1, len(x) + 1)
    _, group, size = np.unique(x, return_inverse=True, return_counts=True)
    return (np.bincount(group, weights=ranks) / size)[group]


def spearman(a, b) -> float:
    """Spearman's rank correlation: Pearson's on average ranks, so tied values (a bootstrap
    resample's repeated cells, cells with the same hit rate) do not depend on the input order."""
    return float(np.corrcoef(average_ranks(a), average_ranks(b))[0, 1])


def on_mains(info: dict) -> bool:
    """As run_cells.power_problem: on AC power at the run's start and end (unknown counts as AC)."""
    return info.get("ac_power") is not False and info.get("ac_power_end") is not False


def valid_runs(cell_dir: Path) -> list[Path]:
    out = []
    for run in sorted(cell_dir.glob("run*")):
        path = run / "run.json"
        if not path.exists() or not (run / "k6-samples.csv.gz").exists():
            continue
        info = json.loads(path.read_text(encoding="utf-8"))
        if info.get("valid") and on_mains(info):
            out.append(run / "k6-samples.csv.gz")
    return out


def agreement(xy: list[tuple[float, float]]) -> dict:
    pred, obs = np.array(xy, dtype=float).T
    keep = ~(np.isnan(pred) | np.isnan(obs))
    pred, obs = pred[keep], obs[keep]
    ape = np.abs(pred - obs) / np.maximum(np.abs(obs), 1e-12)
    d = pred - obs
    sd = d.std(ddof=1) if len(d) > 1 else math.nan
    rho = spearman(pred, obs) if len(d) > 2 and np.ptp(obs) > 0 and np.ptp(pred) > 0 else math.nan
    return {"cells": int(len(d)), "mape_median": float(np.median(ape)) if len(d) else math.nan, "spearman": rho,
            "mean_diff": float(d.mean()) if len(d) else math.nan, "loa": (float(d.mean() - 1.96 * sd),
                                                                       float(d.mean() + 1.96 * sd)),
            "pred": pred.tolist(), "obs": obs.tolist()}


def table_rows(results: dict, targets: dict) -> list[str]:
    rows = ["| metric | cells | median abs. % error | Spearman | Bland-Altman mean diff [95% limits] | target met |",
            "|---|---:|---:|---:|---|---|"]
    for m, a in results.items():
        if a["cells"] < 3:
            continue
        met = ""
        if m in targets["metrics_with_targets"]:
            met = "yes" if (a["mape_median"] <= targets["median_abs_pct_error_max"]
                            and (math.isnan(a["spearman"]) or a["spearman"] >= targets["rank_correlation_min"])) \
                else "NO"
        rows.append(f"| {m} | {a['cells']} | {a['mape_median']:.1%} | {a['spearman']:+.2f} | {a['mean_diff']:+.4g} "
                    f"[{a['loa'][0]:+.4g}, {a['loa'][1]:+.4g}] | {met} |")
    return rows


def plot(results: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    shown = [m for m in METRICS if m != "cost" and results[m]["cells"] >= 3]
    if not shown:
        return
    fig, axes = plt.subplots(1, len(shown), figsize=(3.4 * len(shown), 3.0), squeeze=False)
    for ax, m in zip(axes[0], shown):
        a = results[m]
        pred, obs = np.array(a["pred"]), np.array(a["obs"])
        mean, diff = (pred + obs) / 2, pred - obs
        ax.scatter(mean, diff, s=14, color="#2f5d8a")
        for y, style in ((a["mean_diff"], "-"), (a["loa"][0], "--"), (a["loa"][1], "--")):
            ax.axhline(y, color="#555555", linestyle=style, linewidth=0.8)
        ax.set_title(m.replace("_", " "), fontsize=9)
        ax.set_xlabel("mean of predicted and measured", fontsize=8)
        ax.set_ylabel("predicted - measured", fontsize=8)
        ax.tick_params(labelsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def scaling_note(doc: dict) -> str:
    """How the primary prediction scaled each tier's capacity with replicas, read from its own rules."""
    if doc["rules"].get("replica_scaling", "linear") == "linear":
        return "The primary prediction keeps the simulator's capacity, linear in replicas"
    betas = ", ".join(f"{tier} {c['replica_scaling_exponent']:.3f}" for tier, c in doc["calibration"].items())
    return ("The primary prediction scales each tier's capacity with replicas as n ** beta, beta fitted to the "
            f"calibration's curves ({betas})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--predictions", type=Path, default=HERE / "predictions.json")
    ap.add_argument("--cells", type=Path, default=HERE / "cells.json")
    ap.add_argument("--report", type=Path, default=HERE / "VALIDATION.md")
    ap.add_argument("--plot", type=Path, default=HERE / "validation_bland_altman.png")
    args = ap.parse_args()
    design = json.loads(args.cells.read_text(encoding="utf-8"))
    doc = json.loads(args.predictions.read_text(encoding="utf-8"))
    preds = {p["cell"]: p for p in doc["predictions"]}
    pairs = {m: [] for m in (*METRICS, "hit_rate")}
    steady = {m: [] for m in METRICS}
    measured = {m: [] for m in METRICS}
    per_cell = []
    for cell in design["cells"]:
        p = preds[cell["cell"]]
        runs = valid_runs(args.runs / f"cell{cell['cell']:02d}")
        if not runs:
            continue
        meas = [measure_run(r, p["slo_ms"], p["scored_s"]) for r in runs]
        obs = {m: (p["cost"] if m == "cost" else float(np.nanmean([x[m] for x in meas])))
               for m in (*METRICS, "hit_rate")}
        for m in pairs:
            pairs[m].append((p[m], obs[m]))
        for m in steady:
            steady[m].append((p["steady_cache"][m], obs[m]))
            if "measured_capacity" in p:
                measured[m].append((p["measured_capacity"][m], obs[m]))
        per_cell.append(f"| {cell['cell']} | {len(runs)} | {p['attainment']:.3f} | {obs['attainment']:.3f} | "
                        f"{p['mean_latency_s']:.2f} | {obs['mean_latency_s']:.2f} | {p['hit_rate']:.3f} | "
                        f"{obs['hit_rate']:.3f} | {np.nanmean([x['failed_rate'] for x in meas]):.3f} |")
    t = design["targets"]
    primary = {m: agreement(xy) for m, xy in pairs.items() if xy}
    secondary = {m: agreement(xy) for m, xy in steady.items() if xy}
    secondary_2 = {m: agreement(xy) for m, xy in measured.items() if xy}
    L = ["# WP11: the simulator against the testbed", "",
         f"Generated by `testbed/validation/score.py`. Predictions committed before the runs; "
         f"{len(per_cell)} cells scored over their valid runs. Targets: rank correlation >= "
         f"{t['rank_correlation_min']} and median absolute error <= {t['median_abs_pct_error_max']:.0%} on "
         f"{', '.join(t['metrics_with_targets'])}.", "", "## Primary prediction (cache hits replayed)", ""]
    L += table_rows(primary, t)
    L += ["", "Cost is not a model output in fixed-pool cells (pods x cores x price x time on both sides), "
          "so its agreement is by construction; the informative rows are latency, attainment and throughput. "
          "hit_rate compares the replay's predicted hits with the gateway's own X-PolyForge-Cache count.", "",
          "## Declared secondary: steady-state cache curve", ""]
    L += table_rows({m: a for m, a in secondary.items()}, {**t, "metrics_with_targets": []})
    if secondary_2:
        L += ["", "## Declared secondary: capacity measured at each pool size", "",
              f"{scaling_note(doc)}; this one takes each pool's measured closed-loop rate at the cell's own "
              "replica count, so the difference between the two tables is the replica-scaling part of the error.",
              ""]
        L += table_rows(secondary_2, {**t, "metrics_with_targets": []})
    L += ["", "## Per cell", "",
          "| cell | valid runs | attainment pred | attainment meas | mean s pred | mean s meas | hit pred | "
          "hit meas | failed share |", "|---:|---:|---:|---:|---:|---:|---:|---:|---:|", *per_cell, ""]
    args.report.write_text("\n".join(L), encoding="utf-8")
    if primary:
        plot(primary, args.plot)
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
