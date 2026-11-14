"""The WP17 twin's predictions on the calibrated plant, written before any live
run (programme 2; plan WP17 method 4-5).

Same grid and arms as twin_pilot.py, on the plant calibration.py fitted from
the real service (service_plant.from_calibration): regimes = step width
1, 2, 4 x volatility 0.05, 0.2, 0.4; per regime every shape x SEEDS draws;
A1 and A2 with the default and the fast VPA at targets tuned on separate
draws; A3 and A4 at the regime's width; best static; clairvoyant bound.

Predicted per regime: the mean gain of A4 over A3 and over A1 (tuned,
default VPA), 1 - J_A4 / J_arm, with the per-knob planner's mean interaction
index. Rules fixed here, before any live run:
  live regimes   the regimes with the smallest and the largest predicted gain
                 of A4 over A3, and the median one (ties: smaller width, then
                 smaller volatility);
  live targets   A1 and A2 run at the targets tuned here;
  H8 reading     across the regimes, the per-knob planner's mean index ranks
                 the predicted gain of A4 over A3 (Spearman, reported here
                 for the twin; the live check uses the live regimes).

    python twin_predict.py --calibration calibration.json --out predictions.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import twin_pilot as tp  # noqa: E402
import workloads  # noqa: E402
from service_plant import from_calibration  # noqa: E402


def spearman(a, b) -> float:
    ra, rb = pd.Series(a).rank(), pd.Series(b).rank()
    return float(np.corrcoef(ra, rb)[0, 1])


def predict(plant, widths=tp.WIDTHS, sigmas=workloads.VOLATILITIES) -> dict:
    tuned = {s: tp.tuned_targets(plant, s) for s in sigmas}
    df = pd.DataFrame([r for w in widths for s in sigmas for r in tp.regime_runs(plant, w, s, tuned[s])])
    df = df.assign(arm=df.arm.str.replace(r"_w\d$", "", regex=True))
    J = df.pivot_table(index=["width", "sigma", "shape", "seed"], columns="arm", values="J")
    gains = pd.DataFrame({"vs_A3": 1 - J["A4_joint"] / J["A3_perknob"],
                          "vs_A1": 1 - J["A4_joint"] / J["A1_hpa_cpu_vpa_default_tuned"],
                          "vs_A1_fast": 1 - J["A4_joint"] / J["A1_hpa_cpu_vpa_fast_tuned"]})
    by = gains.groupby(level=["width", "sigma"]).mean()
    index = df[df.arm == "A3_perknob"].groupby(["width", "sigma"])["mean_index"].mean()
    regimes = [{"width": int(w), "sigma": float(s), "gain_vs_A3": float(by.loc[(w, s), "vs_A3"]),
                "gain_vs_A1": float(by.loc[(w, s), "vs_A1"]), "gain_vs_A1_fast": float(by.loc[(w, s), "vs_A1_fast"]),
                "mean_index": float(index.loc[(w, s)])} for w, s in by.index]
    order = sorted(regimes, key=lambda r: (r["gain_vs_A3"], r["width"], r["sigma"]))
    live = [order[0], order[len(order) // 2], order[-1]]
    return {"regimes": regimes,
            "live_regimes": [{"width": r["width"], "sigma": r["sigma"]} for r in live],
            "tuned_targets": {str(s): {f"{m}_{v}": t for (m, v), t in tuned[s].items()} for s in sigmas},
            "index_gain_spearman": spearman([r["mean_index"] for r in regimes], [r["gain_vs_A3"] for r in regimes]),
            "plant": {k: getattr(plant, k) for k in ("cpu_per_request_s", "congestion_exponent", "tail_f0", "tail_b",
                                                      "slo_s", "status")},
            "draws": {"shapes": list(workloads.all_shapes()), "seeds": tp.SEEDS, "tuning_seeds": list(tp.TUNING_SEEDS)}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--calibration", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=HERE / "predictions.json")
    args = ap.parse_args(argv)
    plant = from_calibration(args.calibration)
    if not plant.status.startswith("measured"):
        raise SystemExit("predictions need the measured plant")
    out = predict(plant)
    args.out.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {args.out}; live regimes {out['live_regimes']}; commit it before any live run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
