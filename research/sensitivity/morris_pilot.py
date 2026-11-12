"""Morris screening of plant 2 (mode B) for the coordination gain
(programme 2, WP5 step 1; EXPLORATORY).

Which measured or swept plant constants move the gain of joint over
per-knob search, and the joint planner's own score? Factors: every mode-B
constant of `research/plant2/plant.py` with a WP5 range. Design: Morris
trajectories (Morris 1991; mu* of Campolongo, Cariboni and Saltelli 2007) on a
4-level grid, step 2/3 of the range. Each sampled plant runs the joint and
the per-knob planner (both anchored and convergent) on 20 workloads of the
population's tuning half, large cluster, shared seeds.

Mode B has no quality price yet (WP7), so its tier knob has no trade-off
(the smaller tier is both faster and cheaper); the coupling left is cache x
replicas, through the load a hit removes from the model-server pool.

    python morris_pilot.py [--trajectories 10] [--workers 6]
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO / "research" / "plant2", REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import plant  # noqa: E402
from harness import config as hconfig  # noqa: E402
from harness import population_workloads as pw  # noqa: E402

SEED = 20261112
LEVELS = 4
N_WORKLOADS = 20
BASE = {"anchor_moves": True, "converge_sweeps": True}


def factors() -> list[plant.Constant]:
    return [c for c in plant.constants() if "B" in c.modes.split(",") and c.sweep is not None]


def trajectories(k: int, r: int, rng: np.random.Generator) -> np.ndarray:
    """r Morris trajectories in [0, 1]^k: (r, k+1, k) grid points."""
    delta = LEVELS / (2.0 * (LEVELS - 1))
    grid = np.arange(LEVELS) / (LEVELS - 1)
    out = np.empty((r, k + 1, k))
    for t in range(r):
        x = rng.choice(grid, size=k)  # every level, so steps go down from the upper ones
        out[t, 0] = x
        for step, j in enumerate(rng.permutation(k), start=1):
            x = x.copy()
            x[j] = x[j] + delta if x[j] + delta <= 1.0 + 1e-9 else x[j] - delta
            out[t, step] = x
    return out


def register_arms() -> None:
    from harness.systems import SYSTEMS, SystemSpec

    SYSTEMS["x_joint"] = SystemSpec("jcac", params=dict(BASE))
    SYSTEMS["x_decoupled"] = SystemSpec("jcac", params={**BASE, "knob_search": "decoupled"})


def objective_j(m: dict) -> float:
    return m["total_cost_usd"] / (119 * 8) / 0.01 + 2.0 * m["mean_violation"] + 0.5 * (1.0 - m["mean_jain"])


def run_one(job: tuple) -> dict:
    plant_id, values, run = job
    from harness import sim_backend

    register_arms()
    t0 = time.time()
    m = sim_backend.execute(run)["metrics"]
    return {"plant": plant_id, "system": run.system, "workload": run.workload, "J": objective_j(m),
            "cost": m["total_cost_usd"], "violation": m["mean_violation"], "wall_s": time.time() - t0, **values}


def jobs(points: np.ndarray, names: list[str], fac: list) -> tuple[list, list[dict]]:
    workloads = pw.names("tuning")[:N_WORKLOADS]
    out, plants = [], []
    for pid, x in enumerate(points.reshape(-1, points.shape[-1])):
        values = {c.name: c.sweep[0] + float(u) * (c.sweep[1] - c.sweep[0]) for c, u in zip(fac, x)}
        form, econ = plant.mode_b(**values)
        spec = hconfig.ExperimentSpec(
            name=f"wp5_morris_{pid}", steps=120, reps=1, systems=["x_joint", "x_decoupled"],
            workloads=workloads, tenant_mixes=["own"], cluster_sizes=["large"],
            shared_seeds=True, timeseries_reps=0, model_form=form, economy=econ)
        for run in hconfig.expand(spec):
            out.append((pid, values, run))
        plants.append({"plant": pid, **values})
    return out, plants


def elementary_effects(per_plant: pd.DataFrame, traj: np.ndarray, names: list[str], col: str) -> pd.DataFrame:
    r, k1, k = traj.shape
    effects = {n: [] for n in names}
    y = per_plant.set_index("plant")[col]
    for t in range(r):
        for s in range(1, k1):
            j = int(np.flatnonzero(traj[t, s] != traj[t, s - 1])[0])
            dx = traj[t, s, j] - traj[t, s - 1, j]
            effects[names[j]].append((y[t * k1 + s] - y[t * k1 + s - 1]) / dx)
    return pd.DataFrame({"factor": names,
                         "mu_star": [np.mean(np.abs(effects[n])) for n in names],
                         "mu": [np.mean(effects[n]) for n in names],
                         "sigma": [np.std(effects[n], ddof=1) for n in names]}).sort_values("mu_star", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trajectories", type=int, default=10)
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 2)))
    ap.add_argument("--report-only", action="store_true",
                    help="rebuild the report from data/morris_runs.csv.gz without running anything")
    args = ap.parse_args()
    fac = factors()
    names = [c.name for c in fac]
    traj = trajectories(len(fac), args.trajectories, np.random.default_rng(SEED))
    todo, _ = jobs(traj, names, fac)
    print(f"{len(fac)} factors, {traj.shape[0] * traj.shape[1]} plants, {len(todo)} runs", flush=True)
    t0 = time.time()
    (HERE / "data").mkdir(exist_ok=True)
    if args.report_only:
        return write_report(pd.read_csv(HERE / "data" / "morris_runs.csv.gz"), traj, names, fac, args, t0)
    # Resumable: every finished run is appended at once, and a restart skips
    # the runs already on disk (a killed session must not lose finished work).
    partial = HERE / "data" / f"morris_runs_r{args.trajectories}.partial.csv"
    done = set()
    if partial.exists():
        prev = pd.read_csv(partial)
        done = set(zip(prev.plant, prev.system, prev.workload))
    left = [j for j in todo if (j[0], j[2].system, j[2].workload) not in done]
    print(f"{len(done)} runs already on disk, {len(left)} to go", flush=True)
    with Pool(args.workers) as pool:
        for i, row in enumerate(pool.imap_unordered(run_one, left, chunksize=1), start=1):
            pd.DataFrame([row]).to_csv(partial, mode="a", header=not partial.exists(), index=False)
            if i % 100 == 0:
                print(f"  {i}/{len(left)} runs, {time.time() - t0:.0f}s", flush=True)
    runs = pd.read_csv(partial)
    expected = {(j[0], j[2].system, j[2].workload) for j in todo}
    if set(zip(runs.plant, runs.system, runs.workload)) != expected:
        raise RuntimeError("the partial file does not hold exactly the design's runs")
    runs.to_csv(HERE / "data" / "morris_runs.csv.gz", index=False)
    return write_report(runs, traj, names, fac, args, t0)


SCORED_STEPS = 120  # every run scores all 120 steps (checked: metrics["steps"]); 8 tenants


def write_report(runs: pd.DataFrame, traj, names, fac, args, t0) -> int:
    # run_one stored J with programme 1's normalisation (cost per 119 x 8
    # tenant-steps); programme 2 normalises by the steps actually scored.
    runs = runs.copy()
    runs["J"] = runs.J - runs.cost / (119 * 8) / 0.01 + runs.cost / (SCORED_STEPS * 8) / 0.01
    wide = runs.pivot_table(index=["plant", "workload"], columns="system", values="J").reset_index()
    wide["gain"] = wide.x_decoupled - wide.x_joint
    per_plant = wide.groupby("plant").agg(gain=("gain", "mean"), J_joint=("x_joint", "mean")).reset_index()
    L = ["# Morris screening of plant 2, mode B (WP5 step 1; exploratory)", "",
         "Generated by `research/sensitivity/morris_pilot.py`. "
         f"{len(fac)} factors (every mode-B constant with a WP5 range), {args.trajectories} trajectories, "
         f"{LEVELS}-level grid; {per_plant.shape[0]} plants x {N_WORKLOADS} population workloads (tuning half) "
         "x joint and per-knob planners, large cluster. mu* = mean absolute elementary effect over the "
         "factor's full range (scaled to [0, 1]); sigma = its spread (interaction or non-linearity).", "",
         f"Across plants: gain (per-knob J minus joint J) mean {per_plant.gain.mean():+.4f}, "
         f"range {per_plant.gain.min():+.4f} to {per_plant.gain.max():+.4f}; joint J mean "
         f"{per_plant.J_joint.mean():.4f}.", ""]
    for col, title in (("gain", "coordination gain"), ("J_joint", "joint planner's J")):
        ee = elementary_effects(per_plant, traj, names, col)
        L += [f"## {title}", "", "| factor | mu* | mu | sigma |", "|---|---:|---:|---:|"]
        L += [f"| `{r.factor}` | {r.mu_star:.4f} | {r.mu:+.4f} | {r.sigma:.4f} |" for r in ee.itertuples()]
        L.append("")
    L += ["Caveats: exploratory; one replication; no quality price, so the tier knob is degenerate in "
          "mode B; the per-layer comparators of WP9 are not in this screen.", ""]
    (HERE / "MORRIS_PILOT.md").write_text("\n".join(L), encoding="utf-8")
    (HERE / "data" / "morris_design.json").write_text(
        json.dumps({"factors": names, "trajectories": traj.tolist()}), encoding="utf-8")
    print("\n".join(L))
    print(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
