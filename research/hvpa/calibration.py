"""Fit the WP17 plant from the service's measured curves (programme 2, WP17 step 3).

Input: one directory per CPU level (testbed/hvpa/calibrate_service.py), each
with research/plant2's two files. Per level c:
  mu(c)     the mean of the closed-loop capacities before and after;
  w(c)      CPU-seconds per request = c / mu(c);
  s(c)      the unloaded baseline's mean latency;
  a, f0, b  the power form (L0 fitted) and the tail, research/plant2/fits.py.
The plan's check: capacity is linear in CPU when every w(c) lies within
+-10 % of their median; then the plant uses w = that median (service_plant.Plant's
form), otherwise the check fails and is reported (the measured curve must
then replace the linear form before the twin predicts). a, f0 and b are the
medians over levels. Rule fixed here, before any live run: the SLO is
SLO_FACTOR x s(1 core), the twin's choice (10 x the CPU per request). With
--node-cpu-file, the node's free CPU (calibrate_service.py measures it) is
the twin's node size.

Horizontal scaling (added before any WP17 measurement, after WP11 found the
testbed's pods sub-linear and its tiers coupled through the one host): the
capacity-only points under curves/replicas/ (r pods at c cores each) give
eta = mu(r, c) / (r x c / w). The same rule as for CPU, applied to replicas:
when every eta lies within +-10 % of 1 the plant stays linear, otherwise
the measured table (mean eta per total of requested cores) becomes the
plant's `efficiency`. A point whose load generator used half its core or
more is kept out (the plan's check that the generator is not the bottleneck).

    python calibration.py --curves ../../testbed/out/hvpa/curves --out calibration.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "plant2"))

import fits  # noqa: E402

SLO_FACTOR = 10.0
LINEAR_TOLERANCE = 0.10
GENERATOR_CPU_MAX = 0.5   # cores: the load generator holds one core and must stay below half of it


def level(curve_dir: Path) -> dict:
    meta = json.loads((curve_dir / "load_curve_meta.json").read_text(encoding="utf-8"))
    req = pd.read_csv(curve_dir / "load_curve_requests.csv", float_precision="round_trip")
    mu = 0.5 * (meta["mu_pre_rps"] + meta["mu_post_rps"])
    levels = fits.remeasured_levels(req, meta)
    power, tail = fits.fit_power_free(levels, mu), fits.fit_tail(levels, mu)
    cpu = float(meta["cpu"])
    return {"cpu": cpu, "mu_rps": mu, "w_s": cpu / mu, "s_s": float(meta["l0_s"]), "a": power["a"],
            "l0_fitted_s": power["l0"], "f0": tail["f0"], "b": tail["b"]}


def replica_points(curves: Path, w: float) -> list[dict]:
    """The capacity-only points (curves/replicas/r<R>c<M>m) and their eta."""
    root, out = curves / "replicas", []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not (d / "load_curve_meta.json").exists():
            continue
        meta = json.loads((d / "load_curve_meta.json").read_text(encoding="utf-8"))
        r, c, mu = int(meta["replicas"]), float(meta["cpu"]), float(meta["mu_pre_rps"])
        gen = meta.get("generator_cpu_cores")
        used = gen is not None and float(gen) < GENERATOR_CPU_MAX
        out.append({"replicas": r, "cpu": c, "total_cpu": r * c, "mu_rps": mu, "eta": mu / (r * c / w),
                    "generator_cpu_cores": gen, "used": used,
                    **({} if used else {"why": "no generator CPU recorded" if gen is None
                                        else "the load generator used half its core or more"})})
    return out


def efficiency_table(points: list[dict]) -> list[list[float]]:
    """[total requested cores, mean eta] over the used points, by total."""
    by: dict[float, list[float]] = {}
    for p in points:
        if p["used"]:
            by.setdefault(p["total_cpu"], []).append(p["eta"])
    return [[x, statistics.fmean(v)] for x, v in sorted(by.items())]


def horizontal(curves: Path, w: float) -> dict:
    """The replica check and, when it fails, the plant's efficiency table."""
    points = replica_points(curves, w)
    table = efficiency_table(points)
    if not table:
        return {"replica_points": points} if points else {}
    linear = all(abs(e - 1.0) <= LINEAR_TOLERANCE for _, e in table)
    return {"replica_points": points, "efficiency_measured": table, "linear_in_replicas_within_10pct": linear,
            **({} if linear else {"efficiency": table})}


def fit(curves: Path, node_cpu_file: Path | None = None) -> dict:
    rows = sorted((level(d) for d in curves.iterdir() if (d / "load_curve_meta.json").exists()),
                  key=lambda r: r["cpu"])
    if not rows:
        raise ValueError(f"no curves under {curves}")
    w = statistics.median(r["w_s"] for r in rows)
    worst = max(abs(r["w_s"] / w - 1) for r in rows)
    one_core = min(rows, key=lambda r: abs(r["cpu"] - 1.0))
    return {**horizontal(curves, w),
            "cpu_per_request_s": w, "congestion_exponent": statistics.median(r["a"] for r in rows),
            "tail_f0": max(1.0, statistics.median(r["f0"] for r in rows)),
            "tail_b": max(0.0, statistics.median(r["b"] for r in rows)),
            "slo_s": SLO_FACTOR * one_core["s_s"], "linear_within_10pct": worst <= LINEAR_TOLERANCE,
            "worst_w_deviation": worst, "levels": rows,
            "rule": f"SLO = {SLO_FACTOR:g} x the unloaded latency at {one_core['cpu']:g} core",
            **({"node_cpu": json.loads(node_cpu_file.read_text(encoding="utf-8"))["node_cpu"]}
               if node_cpu_file else {})}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--curves", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=HERE / "calibration.json")
    ap.add_argument("--node-cpu-file", type=Path, default=None,
                    help="node_cpu.json from calibrate_service.py: the cores the node leaves for the service")
    args = ap.parse_args(argv)
    cal = fit(args.curves, args.node_cpu_file)
    args.out.write_text(json.dumps(cal, indent=1), encoding="utf-8")
    print(f"w {cal['cpu_per_request_s'] * 1000:.1f} ms, a {cal['congestion_exponent']:.2f}, SLO {cal['slo_s']:.3f} s; "
          f"linear within 10 %: {cal['linear_within_10pct']} (worst {cal['worst_w_deviation']:.1%})")
    if "efficiency_measured" in cal:
        print("eta by total cores: " + ", ".join(f"{x:g}: {e:.3f}" for x, e in cal["efficiency_measured"]) +
              f"; linear in replicas within 10 %: {cal['linear_in_replicas_within_10pct']}"
              + ("" if cal["linear_in_replicas_within_10pct"] else " -> the plant uses the measured table"))
    return 0 if cal["linear_within_10pct"] else 3


if __name__ == "__main__":
    sys.exit(main())
