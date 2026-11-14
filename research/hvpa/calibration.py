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
SLO_FACTOR x s(1 core), the twin's choice (10 x the CPU per request).

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


def level(curve_dir: Path) -> dict:
    meta = json.loads((curve_dir / "load_curve_meta.json").read_text(encoding="utf-8"))
    req = pd.read_csv(curve_dir / "load_curve_requests.csv", float_precision="round_trip")
    mu = 0.5 * (meta["mu_pre_rps"] + meta["mu_post_rps"])
    levels = fits.remeasured_levels(req, meta)
    power, tail = fits.fit_power_free(levels, mu), fits.fit_tail(levels, mu)
    cpu = float(meta["cpu"])
    return {"cpu": cpu, "mu_rps": mu, "w_s": cpu / mu, "s_s": float(meta["l0_s"]), "a": power["a"],
            "l0_fitted_s": power["l0"], "f0": tail["f0"], "b": tail["b"]}


def fit(curves: Path) -> dict:
    rows = sorted((level(d) for d in curves.iterdir() if (d / "load_curve_meta.json").exists()),
                  key=lambda r: r["cpu"])
    if not rows:
        raise ValueError(f"no curves under {curves}")
    w = statistics.median(r["w_s"] for r in rows)
    worst = max(abs(r["w_s"] / w - 1) for r in rows)
    one_core = min(rows, key=lambda r: abs(r["cpu"] - 1.0))
    return {"cpu_per_request_s": w, "congestion_exponent": statistics.median(r["a"] for r in rows),
            "tail_f0": max(1.0, statistics.median(r["f0"] for r in rows)),
            "tail_b": max(0.0, statistics.median(r["b"] for r in rows)),
            "slo_s": SLO_FACTOR * one_core["s_s"], "linear_within_10pct": worst <= LINEAR_TOLERANCE,
            "worst_w_deviation": worst, "levels": rows,
            "rule": f"SLO = {SLO_FACTOR:g} x the unloaded latency at {one_core['cpu']:g} core"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--curves", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=HERE / "calibration.json")
    args = ap.parse_args(argv)
    cal = fit(args.curves)
    args.out.write_text(json.dumps(cal, indent=1), encoding="utf-8")
    print(f"w {cal['cpu_per_request_s'] * 1000:.1f} ms, a {cal['congestion_exponent']:.2f}, SLO {cal['slo_s']:.3f} s; "
          f"linear within 10 %: {cal['linear_within_10pct']} (worst {cal['worst_w_deviation']:.1%})")
    return 0 if cal["linear_within_10pct"] else 3


if __name__ == "__main__":
    sys.exit(main())
