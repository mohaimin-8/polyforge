"""The tolerances testbed/REPLICATION.md proposes for site B, computed from the committed records.

Part A (WP11b): attainment's median absolute percentage error and Spearman rank correlation, the
per-cell values exactly as score.py builds VALIDATION_V2.md (predictions_v2.json against the mean
of each cell's valid runs in runs_v2/), resampled over cells (10,000 times, seed 20261120). Two
intervals: the bootstrap percentile interval of our own estimate (what REPLICATION.md proposes),
and that interval widened by sqrt(2) on each side, for a second estimate as noisy as ours: a site
whose true values equal ours falls outside our own 95% interval about one time in six per metric.
Part B (WP12, low regime): the 95% prediction interval for a second mean of as many pairs,
mean_diff +- t(0.975, n - 1) * sigma_d * sqrt(2 / n), over the joint arm's mean J; n - 1 because
sigma_d is estimated from our n pair differences (testbed/wp12/pilot.json).

    python testbed/validation/replication_tolerances.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import score  # noqa: E402

SEED, RESAMPLES = 20261120, 10_000
PILOT = HERE.parent / "wp12" / "pilot.json"


def wp11b_pairs(runs: Path = HERE / "runs_v2", cells: Path = HERE / "cells_v2.json",
                predictions: Path = HERE / "predictions_v2.json") -> list[tuple[float, float]]:
    """(predicted, measured) attainment of every scored cell, paired as score.py pairs them."""
    design = json.loads(cells.read_text(encoding="utf-8"))
    preds = {p["cell"]: p for p in json.loads(predictions.read_text(encoding="utf-8"))["predictions"]}
    pairs = []
    for cell in design["cells"]:
        p = preds[cell["cell"]]
        valid = score.valid_runs(runs / f"cell{cell['cell']:02d}")
        if valid:
            measured = [score.measure_run(r, p["slo_ms"], p["scored_s"])["attainment"] for r in valid]
            pairs.append((p["attainment"], float(np.nanmean(measured))))
    return pairs


def widened(point: float, lo: float, hi: float) -> tuple[float, float]:
    """Each side of [lo, hi] stretched by sqrt(2) away from the point: a second estimate's interval."""
    return point - math.sqrt(2) * (point - lo), point + math.sqrt(2) * (hi - point)


def part_a(pairs: list[tuple[float, float]], seed: int = SEED, resamples: int = RESAMPLES) -> dict:
    pred, obs = np.array(pairs, dtype=float).T
    rng = np.random.default_rng(seed)
    error, rho = [], []
    for _ in range(resamples):
        i = rng.integers(0, len(pred), len(pred))
        a = score.agreement(list(zip(pred[i], obs[i])))
        error.append(a["mape_median"])
        rho.append(a["spearman"])
    error, rho = np.array(error), np.array(rho)
    rho = rho[np.isfinite(rho)]          # a resample of one distinct cell has no rank correlation
    point = score.agreement(pairs)
    e, r = point["mape_median"], point["spearman"]
    error_ci = (float(np.percentile(error, 2.5)), float(np.percentile(error, 97.5)))
    rho_ci = (float(np.percentile(rho, 2.5)), float(np.percentile(rho, 97.5)))
    e_lo, e_hi = widened(e, *error_ci)
    r_lo, r_hi = widened(r, *rho_ci)
    return {"cells": len(pairs), "error": e, "rho": r, "error_ci": error_ci, "rho_ci": rho_ci,
            "error_pi": (max(0.0, e_lo), e_hi), "rho_pi": (max(-1.0, r_lo), min(1.0, r_hi))}


def part_b(pilot: Path = PILOT, regime: str = "low") -> dict:
    j = json.loads(pilot.read_text(encoding="utf-8"))["summary"][regime]["J"]
    n, joint = j["pairs"], j["joint_mean"]
    half = float(stats.t.ppf(0.975, n - 1)) * j["sigma_d"] * math.sqrt(2 / n)
    return {"pairs": n, "df": n - 1, "mean_diff": j["mean_diff"], "sigma_d": j["sigma_d"], "joint_mean": joint,
            "relative": j["mean_diff"] / joint, "pi": ((j["mean_diff"] - half) / joint, (j["mean_diff"] + half) / joint)}


def report(a: dict, b: dict) -> list[str]:
    return [f"Part A, {a['cells']} cells: error {a['error']:.1%}, rank correlation {a['rho']:+.2f}",
            f"  our 95% interval (proposed):   error in [{a['error_ci'][0]:.1%}, {a['error_ci'][1]:.1%}] and "
            f"rank correlation in [{a['rho_ci'][0]:+.3f}, {a['rho_ci'][1]:+.3f}]",
            f"  a second estimate's interval:  error in [{a['error_pi'][0]:.1%}, {a['error_pi'][1]:.1%}] and "
            f"rank correlation in [{a['rho_pi'][0]:+.3f}, {a['rho_pi'][1]:+.3f}]",
            f"Part B, low regime, {b['pairs']} pairs: J(per-knob) - J(joint) = {b['mean_diff']:+.4f} with sigma_d "
            f"{b['sigma_d']:.4f}, on a joint mean of {b['joint_mean']:.4f}, that is {b['relative']:+.2%}",
            f"  95% prediction interval for a second mean of {b['pairs']} pairs (t with {b['df']} degrees of "
            f"freedom): [{b['pi'][0]:+.2%}, {b['pi'][1]:+.2%}]"]


def main() -> int:
    print("\n".join(report(part_a(wp11b_pairs()), part_b())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
