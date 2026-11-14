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
the best static plan and the clairvoyant plan UB, both made with hindsight;
the better of the two is the reference H (UB's search can end above the best
static plan when given few iterations). Per workload:
  regret     J - H
  gap share  (J - H) / (static - H): 0 = as good as the best hindsight plan,
             1 = as good as the best fixed plan, above 1 = worse than it
             (undefined when the best fixed plan is the best hindsight plan)
Summary: means over workloads with seeded bootstrap 95 % intervals (the
workload is the unit; plan, Part D). References need the quality weight 0.

    python bench.py --controller baselines:HPAController --params '{"target_rho": 0.6}' --plant B --workloads 12
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib
import json
import math
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


def bootstrap(x: np.ndarray, reps: int = 2000, seed: int = BOOT_SEED) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(reps, len(x)), replace=True).mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def score(factory: Callable, params: dict, plant: str = "B", mix: str = "native", workloads: int | None = 12,
          steps: int = 120, quality_weight: float = 0.0, reference_iterations: int = 120) -> pd.DataFrame:
    from harness import sim_backend

    names = protocol.pick_workloads("evaluation", workloads)
    with tempfile.TemporaryDirectory() as tmp:
        args = tune.parse_args(["--plant", plant, "--mix", mix, "--steps", str(steps), "--out", tmp])
    runs = tune.build_runs(args, names)
    rows = []
    with registered(factory, params) as key:
        for name, run in runs.items():
            result = sim_backend.execute(dataclasses.replace(run, system=key))
            row = {"workload": name, **protocol.objective(result["metrics"], result["tenants"], quality_weight)}
            if quality_weight == 0.0:
                ref = tune._reference({"run": run, "iterations": reference_iterations})
                best = min(ref["ub"], ref["static"])   # both are feasible hindsight plans
                gap = ref["static"] - best
                row.update(static=ref["static"], ub=ref["ub"], hindsight=best, regret=row["J"] - best,
                           gap_share=(row["J"] - best) / gap if gap > 0 else math.nan)
            rows.append(row)
    return pd.DataFrame(rows)


def summary(df: pd.DataFrame) -> dict:
    out = {"workloads": int(len(df))}
    for col in ("J", "regret", "gap_share"):
        if col in df and df[col].notna().any():
            x = df[col].dropna().to_numpy()
            out[col] = {"mean": float(x.mean()), "ci95": bootstrap(x)}
    return out


def load_factory(spec: str) -> Callable:
    """module:Class, the module importable from research/jcac_sim or the path."""
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--controller", required=True, help="module:Class")
    ap.add_argument("--params", default="{}", help="JSON keyword arguments for the controller")
    ap.add_argument("--plant", default="B", choices=("A", "B"))
    ap.add_argument("--mix", default="native", choices=("native", "own"))
    ap.add_argument("--workloads", type=int, default=12, help="first N evaluation workloads, by class")
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--out", type=Path, default=None, help="write per-workload rows here (CSV)")
    args = ap.parse_args(argv)
    df = score(load_factory(args.controller), json.loads(args.params), args.plant, args.mix, args.workloads, args.steps)
    if args.out:
        df.to_csv(args.out, index=False)
    print(json.dumps(summary(df), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
