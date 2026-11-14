"""Measure the WP11 calibration curves on the testbed, in order (programme 2).

Each tier at one replica gets the full protocol with the calibration prompts
(measure/load_curve.py --phases all --prompts): the predictor's shape and
level. The small tier at 2 and 3 replicas and the mid tier at 2 get the
closed-loop capacities alone (--phases capacity --prompts): the cells' load
targets (cell_schedules.py reads mu_trace_rps). Before each measurement the
pools are set through the actuator, every pod is ready and no model server
holds a request. Pools stay inside the 5-core budget (bounds.json). A finished
measurement (its load_curve_meta.json present) is skipped on a rerun.

    python calibrate.py --prompts ../out/calibration_prompts.json --out ../out/curves
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent

import run_cells  # noqa: E402  (the actuator, waits and constants)

PLAN = [  # (directory, tier, pools (small, mid), phases)
    ("small_r1", "small", (1, 1), "all"),
    ("mid_r1", "mid", (1, 1), "all"),
    ("small_r2", "small", (2, 1), "capacity"),
    ("small_r3", "small", (3, 1), "capacity"),
    ("mid_r2", "mid", (1, 2), "capacity"),
]
PORT = {"small": 30091, "mid": 30092}
DEPLOYMENT = {"small": "llama-small", "mid": "llama-mid"}
CORES = {"small": 1, "mid": 2}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prompts", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    act = run_cells.actuate
    bounds = act.Bounds.load(act.DEFAULT_BOUNDS)
    actuator = act.Actuator(bounds=bounds, kube=act.KubectlKube(bounds.namespace, context=run_cells.CONTEXT),
                            knobs=None, log=act.jsonl_logger(args.out / "actuator.jsonl"))
    query = run_cells.prometheus_query(run_cells.PROMETHEUS)
    for name, tier, (small, mid), phases in PLAN:
        out = args.out / name
        if (out / "load_curve_meta.json").exists():
            print(f"{name}: done before, skipped", flush=True)
            continue
        actuator.apply(act.Decision(step=None, t_s=0.0, replicas={"small": small, "mid": mid}))
        run_cells.wait_ready(actuator)
        run_cells.wait_quiet(query)
        replicas = small if tier == "small" else mid
        t0 = time.time()
        rc = subprocess.call([sys.executable, str(TESTBED / "measure" / "load_curve.py"), "--tier", tier,
                              "--replicas", str(replicas), "--threads", str(CORES[tier]),
                              "--base-url", f"http://127.0.0.1:{PORT[tier]}", "--phases", phases,
                              "--prompts", str(args.prompts), "--deployment", DEPLOYMENT[tier],
                              "--out", str(out)])
        print(f"{name}: exit {rc} in {time.time() - t0:.0f} s", flush=True)
        if rc != 0:
            return rc
    actuator.apply(act.Decision(step=None, t_s=0.0, replicas={"small": 1, "mid": 1}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
