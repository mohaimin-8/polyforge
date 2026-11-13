"""Blind predictions of the coordination gain from the index (WP22 item 4).

Protocol, in order, each step a separate command so the record shows the order:

1. `predict`: draw N fresh regimes (a seed the search never used), run ONLY
   the joint planner on each regime's panel, log the per-decision index and
   one-step loss along its trajectory, and predict the gain
   G = J(per-knob) - J(joint) from them with a rule fitted beforehand on the
   adversarial search's random (unselected) half. Writes
   data/blind_predictions.json; it is committed before step 2 runs.
2. `score`: run the per-knob planner on the same regimes and seeds, and score
   the predictions: Spearman of predicted against observed gain (one-sided
   test, H2-blind holds if positive at p < 0.05), mean absolute error
   against a null that predicts the random half's median gain for every
   regime.

The prediction rule (fixed in this file before any new run): ordinary least
squares of G on the mean one-step loss and the mean index, fitted on the
random half of data/adversarial_search_regimes.csv.

    python blind_predictions.py predict [--n 40] [--workers 6]
    python blind_predictions.py score   [--workers 6]
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import random
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import adversarial_search as adv  # noqa: E402
import controls_common as cc  # noqa: E402

SEED = 20261114  # never used by the search (its seed is 20261112)
PANEL, STEPS, REP = 4, 60, 0
OUT = HERE / "data" / "blind_predictions.json"


def rule() -> dict:
    """OLS of G on (loss, I) over the search's random half; its median gain."""
    r = pd.read_csv(HERE / "data" / "adversarial_search_regimes.csv", float_precision="round_trip")
    r = r[r.phase == "random"].dropna(subset=["G", "loss", "I"])
    a = np.column_stack([np.ones(len(r)), r.loss, r.I])
    coef, *_ = np.linalg.lstsq(a, r.G, rcond=None)
    return {"intercept": float(coef[0]), "loss": float(coef[1]), "index": float(coef[2]),
            "null_median_G": float(r.G.median()), "fitted_on": int(len(r))}


def regimes(n: int) -> list[dict]:
    rng = random.Random(SEED)
    return [adv.sample(rng) for _ in range(n)]


def _run(job: tuple) -> dict:
    """One design point of one regime, one arm (`joint` logs the index)."""
    from controller_index import execute_indexed

    rid, theta, (workload, mix, size), arm = job
    form, economy = adv.plant_of(theta)
    seed = cc.design_seed(adv.SALT, workload, mix, size, REP)
    runs = cc.run_specs("wp22_blind", workload, mix, size, STEPS, seed, economy, form)
    arms = cc.arm_pair(cc.width_params(theta["width"]), track=False)
    name = cc.JOINT if arm == "joint" else cc.DECOUPLED
    with cc.registered_arms(arms):
        result, rows = execute_indexed(runs[name], cc.IndexedWidthController)
    out = {"rid": rid, "workload": workload, "arm": arm,
           "J": cc.objective_j(result["metrics"], result["tenants"])}
    if arm == "joint":
        s = cc.summarise_rows(rows, STEPS)
        out.update(index=s["index_mean"], loss=s["loss_mean"])
    return out


def _jobs(thetas: list[dict], arm: str) -> list[tuple]:
    return [(rid, th, point, arm) for rid, th in enumerate(thetas) for point in adv.panel_of(th, PANEL)]


def predict(n: int, workers: int) -> int:
    if OUT.exists():
        raise SystemExit(f"{OUT} exists: predictions are made once")
    fit = rule()
    thetas = regimes(n)
    with Pool(workers) as pool:
        rows = pd.DataFrame(pool.map(_run, _jobs(thetas, "joint"), chunksize=1))
    per = rows.groupby("rid").agg(J_joint=("J", "mean"), index=("index", "mean"), loss=("loss", "mean"))
    per["G_pred"] = fit["intercept"] + fit["loss"] * per.loss + fit["index"] * per["index"]
    OUT.write_text(json.dumps({
        "made": datetime.datetime.now().isoformat(timespec="seconds"), "seed": SEED, "panel": PANEL,
        "steps": STEPS, "rule": fit, "regimes": thetas,
        "predictions": per.reset_index().to_dict(orient="records"),
        "joint_points": rows.to_dict(orient="records")}, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT}: {len(per)} regimes; commit it before running `score`")
    return 0


def score(workers: int) -> int:
    from scipy.stats import spearmanr

    data = json.loads(OUT.read_text(encoding="utf-8"))
    thetas = data["regimes"]
    with Pool(workers) as pool:
        dec = pd.DataFrame(pool.map(_run, _jobs(thetas, "per_knob"), chunksize=1))
    joint = pd.DataFrame(data["joint_points"])
    m = joint.merge(dec, on=["rid", "workload"], suffixes=("_joint", "_dec"))
    obs = (m.J_dec - m.J_joint).groupby(m.rid).mean().rename("G")
    pred = pd.DataFrame(data["predictions"]).set_index("rid")
    both = pred.join(obs, how="inner")
    rho, p_two = spearmanr(both.G_pred, both.G)
    p_one = p_two / 2 if rho > 0 else 1 - p_two / 2
    mae = float((both.G_pred - both.G).abs().mean())
    mae_null = float((data["rule"]["null_median_G"] - both.G).abs().mean())
    rho_loss = spearmanr(both.loss, both.G)[0]
    rho_index = spearmanr(both["index"], both.G)[0]
    L = ["# Blind predictions of the coordination gain (WP22 item 4; exploratory)", "",
         f"Predictions made {data['made']} from the joint planner alone and committed before the per-knob "
         f"runs (git history). {len(both)} fresh regimes (seed {data['seed']}), panel {data['panel']}, "
         f"{data['steps']} steps. Rule fitted on the search's random half: G = {data['rule']['intercept']:+.4f} "
         f"{data['rule']['loss']:+.3f} x loss {data['rule']['index']:+.4f} x index.", "",
         f"- Spearman(predicted, observed G) = {rho:+.2f}, one-sided p = {p_one:.4f}: H2-blind "
         f"{'holds' if (rho > 0 and p_one < 0.05) else 'does not hold'} at 0.05.",
         f"- Mean absolute error {mae:.4f} against {mae_null:.4f} for the null that predicts the random "
         f"half's median gain everywhere.",
         f"- Spearman with G of the one-step loss alone {rho_loss:+.2f}, of the index alone {rho_index:+.2f}.",
         f"- Observed G: mean {both.G.mean():+.4f}, median {both.G.median():+.4f}, max {both.G.max():+.4f}.", ""]
    (HERE / "BLIND_PREDICTIONS.md").write_text("\n".join(L), encoding="utf-8")
    both.reset_index().to_csv(HERE / "data" / "blind_predictions_scored.csv", index=False)
    print("\n".join(L))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("predict")
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--workers", type=int, default=6)
    s = sub.add_parser("score")
    s.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    return predict(args.n, args.workers) if args.cmd == "predict" else score(args.workers)


if __name__ == "__main__":
    sys.exit(main())
