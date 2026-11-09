"""Select the PREREG_LEVEL treatment on the tuning slice (development data).

The comparators of PREREG_LEVEL are tuned per size on tune.py's slice and
seeds (tune_arms.py --level). To give the joint controller the same chance,
a FIXED, pre-declared set of variants -- each one a documented fix of a
defect the final audit (2026-11-09) found -- is scored on that same slice
under the same LEVEL objective, and the variant with the lowest mean over the
three cluster sizes becomes the treatment. One variant for all sizes (the
comparators get one value per size). The slice's seeds are disjoint from
every experiment's, so the confirmatory campaign tests the choice on fresh
demand. The selection and every score are written to select_level_treatment.csv
and disclosed in PREREG_LEVEL.md.

Run from eval/:  python baselines/select_level_treatment.py --workers 6
"""

from __future__ import annotations

import csv
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import tune_arms  # noqa: E402

BASE = {"anchor_moves": True, "converge_sweeps": True}
# Declared before any score was seen. V1 removes the free outage (the
# planner pays the small-tier price for refused requests) and replaces the
# shed-only fallback with graceful degradation; V2 adds the corrected
# hysteresis the code documents (SWITCH_PENALTY_HALF_REPLICA, the B1'
# correction against the replica ratchet); V3 adds the damped-Holt forecaster
# that beat the trend default on the real BurstGPT trace.
VARIANTS: dict[str, dict] = {
    "V0_published": {},
    "V1_priced_degrade": {"shed_price_tier": "small", "degrade_gracefully": True},
    "V2_plus_half_penalty": {"shed_price_tier": "small", "degrade_gracefully": True,
                             "switch_penalty": "half_replica"},
    "V3_plus_holt": {"shed_price_tier": "small", "degrade_gracefully": True,
                     "switch_penalty": "half_replica", "forecast_method": "holt"},
}
OUT = Path(__file__).resolve().parent / "select_level_treatment.csv"


def resolve(params: dict) -> dict:
    import controller  # sim path set by tune_arms' import of tune
    out = {**BASE, **params}
    if out.get("switch_penalty") == "half_replica":
        out["switch_penalty"] = controller.SWITCH_PENALTY_HALF_REPLICA
    return out


def _score(job: tuple) -> dict:
    name, size = job
    row = tune_arms.score_controller("jcac", resolve(VARIANTS[name]), size, level=True)
    return {"variant": name, "size": size, **row}


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args(argv)
    jobs = [(name, size) for name in VARIANTS for size in tune_arms.SIZES]
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as ex:
        rows = list(ex.map(_score, jobs))
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    means = {name: sum(r["mean_objective"] for r in rows if r["variant"] == name) / len(tune_arms.SIZES)
             for name in VARIANTS}
    for r in rows:
        print(f"  {r['variant']:22s} {r['size']:6s} J_level {r['mean_objective']:.5f}  {r}")
    for name, m in sorted(means.items(), key=lambda kv: kv[1]):
        print(f"{name:22s} mean J_level {m:.5f}")
    print("selected:", min(means, key=means.get))


if __name__ == "__main__":
    main()
