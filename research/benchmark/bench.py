"""PolyForge benchmark (programme 2, WP16): any controller, scored by regret.

A controller is the simulator's own kind: built as Factory(configs, **params)
with configs = {tenant id: model.TenantConfig}, and planning with
plan(states, demands) -> {tenant id: model.TenantState} (research/jcac_sim/
baselines.py holds many). `score` runs it on the workload population's
EVALUATION half (research/population; the tuning half is for tuning, never
for scoring) under plant 2 (mode A or B, research/plant2/plant.py) with a
tenant mix (native = real-arrival replay, own = MMPP2), scores each run with
the WP9 objective J (research/comparators/protocol.objective), and puts each
workload's J against WP1's references (research/theory/clairvoyant.bounds):
the lower bounds LB0 (any controller) and LB2 (the planner's move limits and
the cluster caps), the best static plan inside an equal share of the caps
and the clairvoyant plan UB, the last two made with hindsight; the better of
those two is H (UB's search can end above the best static plan when given
few iterations). Per workload:
  regret                      J - H
  cap_overflow_step_share     scored steps whose plan holds more replicas or
                              cache than the cluster's caps
  clamp_violation_step_share  tenant-steps that moved more than the planner's
                              limits (+-2 replicas, one cache level)
  budget_overrun_step_share   tenant-steps billed above the tenant's budget
Plant 2's simulator applies whatever plan a controller returns and enforces
none of the three. The references obey the caps (LB2, UB, the static plan)
and the move limits (LB2, UB), none of them a budget, so the benchmark's
game is WP9's with `--budget plant` (research/comparators/tune.py, whose arms
all keep the caps). A controller that overflows the caps is not scored
against plans that could have done the same: its J is not comparable with
the references or with WP9's arms.
Summary, the workload being the unit (plan, Part D), each with a seeded
bootstrap 95 % interval over workloads: the means of J, regret and the three
shares, and WP9's normalised regret (comparators/report.py, its reference
lines), the ratio of means (J - LB) / (static - LB) for LB = LB2 and LB0:
0 = as good as the lower bound, 1 = as good as the best static plan. Not a
per-workload ratio against H: on the published plant H is the static plan,
or within about 1e-5 of it (J near 0.2), on most workloads, which leaves
that ratio noise over noise. References need the quality weight 0.

    python bench.py --controller baselines:HPAController --params '{"target_rho": 0.6}' --plant B --workloads 12
    python bench.py --controller path/to/mine.py:MyController --plant B --workloads 0

`--controller` takes module:Class, the module importable from research/
jcac_sim, research/comparators, eval or PYTHONPATH, or file.py:Class for a
controller in a file of its own (loaded under a name of its own, so a file
called model.py cannot stand in for the simulator's model).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO / "research" / "comparators", REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import protocol  # noqa: E402
import tune  # noqa: E402

NAME = "bench_controller"
BOOT_SEED = 20261114
BOOT_REPS = 2000
SHARES = ("cap_overflow_step_share", "clamp_violation_step_share", "budget_overrun_step_share")
LOWER_BOUNDS = (("vs_lb2", "lb2"), ("vs_lb0", "lb0"))


@contextlib.contextmanager
def registered(factory: Callable, params: dict):
    """The controller as a harness system for the duration: make_baseline
    builds `factory` for NAME and defers to the published registry otherwise."""
    import baselines
    import model
    from harness.systems import SYSTEMS, SystemSpec

    original = baselines.make_baseline

    def make_baseline(name: str, configs: dict, **kw):
        return factory(configs, **kw) if name == NAME else original(name, configs, **kw)

    baselines.make_baseline = make_baseline
    SYSTEMS[NAME] = SystemSpec(NAME, params=dict(params), description="benchmark controller")
    try:
        yield NAME
    finally:
        SYSTEMS.pop(NAME, None)
        baselines.make_baseline = original
        model.set_economy()
        model.set_model_form()


def bootstrap(x: np.ndarray, reps: int = BOOT_REPS, seed: int = BOOT_SEED) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(reps, len(x)), replace=True).mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def normalised_regret(j: np.ndarray, static: np.ndarray, low: np.ndarray,
                      reps: int = BOOT_REPS, seed: int = BOOT_SEED) -> dict | None:
    """(mean J - mean LB) / (mean static - mean LB), WP9's ratio of means, with
    a seeded bootstrap over workloads (J, static and LB resampled together).
    None when the static plan is no worse than the bound on average."""
    def ratio(jm, sm, lm):
        den = sm - lm
        return np.where(den > 0, (jm - lm) / np.where(den > 0, den, 1.0), np.nan)

    point = float(ratio(j.mean(), static.mean(), low.mean()))
    if not np.isfinite(point):
        return None
    idx = np.random.default_rng(seed).integers(0, len(j), size=(reps, len(j)))
    draws = ratio(j[idx].mean(axis=1), static[idx].mean(axis=1), low[idx].mean(axis=1))
    lo, hi = np.nanquantile(draws, [0.025, 0.975])
    return {"mean": point, "ci95": (float(lo), float(hi))}


def cap_overflow_share(rows: list[dict], limits) -> float:
    """Share of scored steps whose plan, summed over tenants, holds more
    replicas or more cache than the cluster's caps (reaching a cap is not)."""
    totals: dict[int, list[int]] = {}
    for r in rows:
        t = totals.setdefault(r["step"], [0, 0])
        t[0] += r["replicas"]
        t[1] += r["cache_mb"]
    if not totals:
        raise ValueError("the run stored no plan rows")
    return sum(rep > limits.replicas or mb > limits.cache_mb for rep, mb in totals.values()) / len(totals)


def score(factory: Callable, params: dict, plant: str = "B", mix: str = "native", workloads: int | None = 12,
          steps: int = 120, quality_weight: float = 0.0, reference_iterations: int = 120) -> pd.DataFrame:
    from harness import sim_backend
    from harness import workloads as harness_workloads

    names = protocol.pick_workloads("evaluation", workloads)
    with tempfile.TemporaryDirectory() as tmp:
        args = tune.parse_args(["--plant", plant, "--mix", mix, "--steps", str(steps), "--out", tmp])
    runs = tune.build_runs(args, names)
    rows = []
    with registered(factory, params) as key:
        for name, run in runs.items():
            result = sim_backend.execute(dataclasses.replace(run, system=key, store_timeseries=True))
            limits = harness_workloads.build(run.workload, run.tenant_mix, run.cluster_size, run.seed, run.steps)[3]
            m = result["metrics"]
            row = {"workload": name, **protocol.objective(m, result["tenants"], quality_weight),
                   "cap_overflow_step_share": cap_overflow_share(result["timeseries"], limits),
                   "clamp_violation_step_share": m["clamp_violation_step_share"],
                   "budget_overrun_step_share": m["budget_overrun_step_share"]}
            if quality_weight == 0.0:
                ref = tune._reference({"run": run, "iterations": reference_iterations})
                best = min(ref["ub"], ref["static"])   # both are feasible hindsight plans
                row.update(ref, hindsight=best, regret=row["J"] - best)
            rows.append(row)
    return pd.DataFrame(rows)


def summary(df: pd.DataFrame) -> dict:
    out = {"workloads": int(len(df))}
    for col in ("J", "regret", *SHARES):
        if col in df and df[col].notna().any():
            x = df[col].dropna().to_numpy(float)
            out[col] = {"mean": float(x.mean()), "ci95": bootstrap(x)}
    if {"J", "static", "lb2", "lb0"} <= set(df):
        for key, low in LOWER_BOUNDS:
            out[key] = normalised_regret(*(df[c].to_numpy(float) for c in ("J", "static", low)))
    return out


def load_factory(spec: str) -> Callable:
    """module:Class or file.py:Class (the last colon splits, so a drive letter
    stays in the path); a refusal names what was not found."""
    source, _, attr = spec.rpartition(":")
    if not source or not attr:
        raise SystemExit(f"--controller {spec!r}: give module:Class or file.py:Class")
    if source.endswith(".py"):
        path = Path(source).resolve()
        if not path.is_file():
            raise SystemExit(f"--controller: no file {path}")
        name = f"{NAME}_{path.stem}"                    # never one of the simulator's module names
        loader = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(loader)
        sys.modules[name] = module                      # dataclasses and pickling look it up
        loader.loader.exec_module(module)
    else:
        try:
            module = importlib.import_module(source)
        except ModuleNotFoundError as exc:
            raise SystemExit(f"--controller: cannot import {source!r} ({exc}); give file.py:Class for "
                             "a controller in a file of its own, or put its folder on PYTHONPATH") from exc
    factory = getattr(module, attr, None)
    if not callable(factory):
        raise SystemExit(f"--controller: {source} has no callable {attr!r}")
    return factory


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--controller", required=True, help="module:Class or file.py:Class")
    ap.add_argument("--params", default="{}", help="JSON keyword arguments for the controller")
    ap.add_argument("--plant", default="B", choices=("A", "B"))
    ap.add_argument("--mix", default="native", choices=("native", "own"))
    ap.add_argument("--workloads", type=int, default=12,
                    help="first N evaluation workloads, stratified by class; 0 = all of them")
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--out", type=Path, default=None, help="write per-workload rows here (CSV)")
    args = ap.parse_args(argv)
    if args.workloads < 0:
        ap.error("--workloads must be 0 (all) or more")
    df = score(load_factory(args.controller), json.loads(args.params), args.plant, args.mix,
               args.workloads or None, args.steps)
    if args.out:
        df.to_csv(args.out, index=False)
    print(json.dumps(summary(df), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
