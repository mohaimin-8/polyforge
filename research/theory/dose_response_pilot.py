"""Dose-response pilot (programme 2, WP4). EXPLORATORY.

How does the gain of joint over per-knob search grow with the actuation
width (replicas per interval) and with the coupling between the AI knobs and
the replica pool (the share of an AI request's work that lands on the
replicas)? And which per-decision quantity predicts it: the interaction
index, the quantile index, the realised one-step loss, or the share of
costly disagreements?

Design: widths {1, 2, 4, 8, 16} (cache radius 1, 1, 2, any, any) x coupling
kappa = model_form wu_ai_scale in {0, 0.25, 0.5, 1, 2} on the published plant
(kappa = 1 is the published coupling); 25 regimes. Each regime runs the joint
and the decoupled planner (anchored, convergent) on the 20 AI design points
of the large cluster (5 AI workloads x 4 mixes), replication 0, with one
demand seed per design point shared by every regime and arm.

    python dose_response_pilot.py     # writes data/dose_response_runs.csv.gz and DOSE_RESPONSE_PILOT.md
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (HERE, REPO / "research" / "control2", REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from controller_index import IndexedController, execute_indexed  # noqa: E402
from harness import config as hconfig  # noqa: E402
from width import CACHE_RADIUS_FOR_WIDTH, WidthController  # noqa: E402

EPS = 1e-9
WIDTHS = (1, 2, 4, 8, 16)
KAPPAS = (0.0, 0.25, 0.5, 1.0, 2.0)
WORKLOADS = ["ai_cacheable", "ai_uncacheable", "agentic", "joint_stress", "mmpp_mixed"]
MIXES = ["uniform", "premium_heavy", "besteffort_heavy", "whale"]
SIZE = "large"
BASE = {"anchor_moves": True, "converge_sweeps": True}


class IndexedWidthController(IndexedController, WidthController):
    """Width-settable planner that logs the index of its decisions."""


def arm(width: int, search: str) -> str:
    return f"x_w{width}_{search}"


def register_arms() -> None:
    """In-process registry entries for the pilot's arms (never committed to
    the harness registry: exploratory)."""
    from harness.systems import SYSTEMS, SystemSpec

    for w in WIDTHS:
        radii = {"replica_radius": w, "cache_radius": CACHE_RADIUS_FOR_WIDTH[w]}
        SYSTEMS[arm(w, "joint")] = SystemSpec("jcac", params={**BASE, **radii})
        SYSTEMS[arm(w, "decoupled")] = SystemSpec(
            "jcac", params={**BASE, **radii, "knob_search": "decoupled", "log_index": False})


def design_seed(workload: str, mix: str, size: str, rep: int) -> int:
    """One demand seed per design point, shared by every regime and arm."""
    digest = hashlib.sha256(f"wp4_pilot|{workload}|{mix}|{size}|{rep}".encode()).hexdigest()
    return int(digest[:12], 16) % (2**31 - 1)


def jobs() -> list:
    out = []
    for kappa in KAPPAS:
        spec = hconfig.ExperimentSpec(
            name=f"wp4_pilot_k{kappa:g}", steps=120, reps=1,
            systems=[arm(w, s) for w in WIDTHS for s in ("joint", "decoupled")],
            workloads=WORKLOADS, tenant_mixes=MIXES, cluster_sizes=[SIZE],
            shared_seeds=True, timeseries_reps=0, model_form={"wu_ai_scale": kappa})
        for run in hconfig.expand(spec):
            seed = design_seed(run.workload, run.tenant_mix, run.cluster_size, run.rep)
            out.append((kappa, dataclasses.replace(run, seed=seed)))
    # The slowest runs (widest lattices) first, so the pool finishes evenly.
    out.sort(key=lambda j: -int(j[1].system.split("_")[1][1:]))
    return out


def objective_j(m: dict) -> float:
    """The published composite J (cost per 119 x 8 tenant-steps), as WIDTH scored it."""
    return m["total_cost_usd"] / (119 * 8) / 0.01 + 2.0 * m["mean_violation"] + 0.5 * (1.0 - m["mean_jain"])


def run_one(job: tuple) -> dict:
    kappa, run = job
    register_arms()
    t0 = time.time()
    result, rows = execute_indexed(run, IndexedWidthController)
    rows = [r for r in rows if r["cycle"] <= run.steps]
    applies = [r for r in rows if r["theorem1_applies"]]
    width, search = int(run.system.split("_")[1][1:]), run.system.split("_")[2]
    out = {"kappa": kappa, "width": width, "search": search, "workload": run.workload,
           "tenant_mix": run.tenant_mix, "seed": run.seed, "J": objective_j(result["metrics"]),
           "wall_s": time.time() - t0, "decisions": len(rows), "theorem_applies": len(applies),
           "theorem_violations": sum(1 for r in applies if r["loss"] < -EPS or r["loss"] > r["bound"] + EPS
                                     or r["bound"] > r["index"] + EPS)}
    if rows:
        def mean(key):
            vals = [r[key] for r in rows if r[key] is not None]
            return float(np.mean(vals)) if vals else math.nan
        out.update(index=mean("index"), quantile_index=mean("quantile_index"),
                   loss=float(np.mean([r["loss"] for r in applies])) if applies else math.nan,
                   costly=float(np.mean([r["disagree_with_loss"] for r in rows])),
                   per_knob_infeasible=float(np.mean([not r["per_knob_feasible"] for r in rows])))
    return out


def spearman(a, b) -> float:
    return float(pd.Series(a).rank().corr(pd.Series(b).rank()))


def slope(x, y) -> float:
    """Least-squares slope of log y on log x over the positive points."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = (x > 0) & (y > 0)
    return float(np.polyfit(np.log(x[ok]), np.log(y[ok]), 1)[0]) if ok.sum() >= 2 else math.nan


def report(runs: pd.DataFrame) -> str:
    keys = ["kappa", "width", "workload", "tenant_mix"]
    joint = runs[runs.search == "joint"].set_index(keys)
    dec = runs[runs.search == "decoupled"].set_index(keys)
    paired = joint.join(dec[["J"]].rename(columns={"J": "J_dec"}), how="inner")
    paired["gain"] = paired.J_dec - paired.J
    reg = paired.groupby(level=["kappa", "width"]).agg(
        gain=("gain", "mean"), index=("index", "mean"), quantile_index=("quantile_index", "mean"),
        loss=("loss", "mean"), costly=("costly", "mean"), J=("J", "mean"), n=("gain", "size")).reset_index()
    rng = np.random.default_rng(20261112)
    cis = []
    for (k, w), s in paired.groupby(level=["kappa", "width"]):
        g = s.gain.to_numpy()
        means = g[rng.integers(0, len(g), size=(5000, len(g)))].mean(axis=1)
        cis.append((k, w, np.percentile(means, 2.5), np.percentile(means, 97.5)))
    reg = reg.merge(pd.DataFrame(cis, columns=["kappa", "width", "lo", "hi"]), on=["kappa", "width"])
    total_viol = int(runs.theorem_violations.sum())
    L = [
        "# Dose-response pilot: coordination gain against step width and coupling (exploratory)",
        "",
        "Generated by `research/theory/dose_response_pilot.py`. Programme 2, WP4. Published plant; "
        "kappa is the share of an AI request's work that lands on the replicas (model_form "
        "wu_ai_scale; 1 = published); width is the replica move per interval (cache radius 1, 1, 2, "
        "any, any). 20 AI design points of the large cluster, replication 0, one demand seed per "
        "design point shared by all regimes. Gain = J(decoupled) - J(joint), positive = joint better.",
        "",
        f"Theorem 1 over all {int(runs.decisions.sum()):,} logged decisions: it applies (anchor, per-knob "
        f"and joint choices feasible, both remainders defined) to {int(runs.theorem_applies.sum()):,}, "
        f"{total_viol} violations.",
        "",
        "## Gain by regime (mean over design points, 95% bootstrap CI)",
        "",
        "| kappa \\ width | " + " | ".join(str(w) for w in WIDTHS) + " |",
        "|---|" + "---:|" * len(WIDTHS),
    ]
    for k in KAPPAS:
        cells = []
        for w in WIDTHS:
            r = reg[(reg.kappa == k) & (reg.width == w)]
            cells.append("—" if r.empty else f"{r.gain.iloc[0]:+.4f} [{r.lo.iloc[0]:+.4f}, {r.hi.iloc[0]:+.4f}]")
        L.append(f"| {k:g} | " + " | ".join(cells) + " |")
    L += ["", "## Mean index and mean one-step loss by regime", "",
          "| kappa \\ width | " + " | ".join(str(w) for w in WIDTHS) + " |",
          "|---|" + "---:|" * len(WIDTHS)]
    for k in KAPPAS:
        cells = []
        for w in WIDTHS:
            r = reg[(reg.kappa == k) & (reg.width == w)]
            cells.append("—" if r.empty else f"{r['index'].iloc[0]:.3f} / {r.loss.iloc[0]:.5f}")
        L.append(f"| {k:g} | " + " | ".join(cells) + " |")
    L += ["", "## Scaling with width (log-log slope over widths, per kappa)", "",
          "| kappa | gain | mean index | mean one-step loss |", "|---|---:|---:|---:|"]
    for k in KAPPAS:
        s = reg[reg.kappa == k]
        L.append(f"| {k:g} | {slope(s.width, s.gain):.2f} | {slope(s.width, s['index']):.2f} | "
                 f"{slope(s.width, s.loss):.2f} |")
    L += ["", "## Which quantity predicts the regime's gain? (25 regimes, Spearman)", "",
          "| candidate | rank correlation with gain | span (max / min) |", "|---|---:|---:|"]
    for col, label in (("index", "worst-case index"), ("quantile_index", "quantile index"),
                       ("loss", "realised one-step loss"), ("costly", "share of costly disagreements")):
        lo = reg[col].min()
        span = f"{reg[col].max() / lo:.0f}x" if lo > 0 else "n/a"
        L.append(f"| {label} | {spearman(reg[col], reg.gain):+.2f} | {span} |")
    L += ["", "Caveats: published plant (its agent latency row and price ladder are assumptions); one "
          "replication; 20 design points per regime; the regimes chosen here are exploratory and will "
          "not be reused as confirmatory evidence.", ""]
    return "\n".join(L)


def main() -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 2)))
    args = ap.parse_args()
    todo = jobs()
    print(f"{len(todo)} runs on {args.workers} workers", flush=True)
    t0 = time.time()
    with Pool(args.workers) as pool:
        rows = pool.map(run_one, todo, chunksize=1)
    runs = pd.DataFrame(rows)
    (HERE / "data").mkdir(exist_ok=True)
    runs.to_csv(HERE / "data" / "dose_response_runs.csv.gz", index=False)
    (HERE / "DOSE_RESPONSE_PILOT.md").write_text(report(runs), encoding="utf-8")
    print(f"done in {time.time() - t0:.0f}s; theorem violations: {int(runs.theorem_violations.sum())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
