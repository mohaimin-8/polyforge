"""Worked example 3 (log mode): decisions of the WP17 planners on the Kubernetes pair.

Runs the twin of the Kubernetes horizontal-with-vertical system (research/hvpa:
replicas x CPU per pod, on the plant calibrate_service.py measured and
calibration.py fitted) with the joint planner (A4) or the per-knob one (A3)
on one of the twin's own draws (twin_pilot.draws, so the decisions are those
of the committed prediction runs). For every decision it writes the box
(replicas within the width, CPU one level down, the same or up), the anchor
(the current allocation), the planner's objective of every candidate (J =
cost + KAPPA x late requests over the next 30 s at the last rate seen; null
where the point does not fit the node) and the planner's choice, in
index-probe's JSON-lines format, with the planner's own index beside it.

    python kubernetes_log.py [--arm A4] [--width 2] [--shape step_up] [--sigma 0.2] [--seed 0]
    python ../index_probe.py log kubernetes_log.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
HVPA = REPO / "research" / "hvpa"
if str(HVPA) not in sys.path:
    sys.path.insert(0, str(HVPA))

import lattice  # noqa: E402
import twin  # noqa: E402
import twin_pilot  # noqa: E402
import workloads  # noqa: E402
from service_plant import from_calibration  # noqa: E402


class LoggingPlanner(twin.Planner):
    """The twin's planner, writing every decision's whole box as one JSON line."""

    def __init__(self, joint: bool, width: int) -> None:
        super().__init__(joint, width)
        self.sink: list[dict] = []

    def decide(self, plant, rate: float, r: int, c: float) -> tuple[tuple, dict]:
        nxt, info = super().decide(plant, rate, r, c)
        rs, cs = lattice.box(plant, r, c, self.width)
        phi = [[[rr, cc], lattice.lattice_j(plant, rate, rr, cc)
                if lattice.fits(rr, cc, plant.node_cpu) or (rr, cc) == (r, c) else None]
               for rr in rs for cc in cs]       # the current allocation is scored even if it does not fit
        self.sink.append({"id": f"{self.name}@{len(self.sink) * lattice.PLAN_S:g}s", "values": [rs, cs],
                          "anchor": [r, c], "phi": phi, "chosen": list(nxt), "rate_rps": rate,
                          "planner_index": info["index"]})
        return nxt, info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=("A3", "A4"), default="A4")
    ap.add_argument("--width", type=int, choices=twin_pilot.WIDTHS, default=2)
    ap.add_argument("--shape", choices=workloads.all_shapes(), default="step_up")
    ap.add_argument("--sigma", type=float, choices=workloads.VOLATILITIES, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--minutes", type=float, default=60.0, help="the first minutes of the hour-long draw")
    ap.add_argument("--calibration", type=Path, default=HVPA / "calibration.json")
    ap.add_argument("--out", type=Path, default=HERE / "kubernetes_log.jsonl")
    args = ap.parse_args()
    plant = from_calibration(args.calibration)
    rates = next(r for shape, _, r in twin_pilot.draws(plant, args.width, args.sigma, [args.seed])
                 if shape == args.shape)
    planner = LoggingPlanner(args.arm == "A4", args.width)
    twin.run(planner, plant, rates[: int(round(args.minutes * 60 / twin.DT_S))])
    with args.out.open("w", encoding="utf-8") as f:
        for d in planner.sink:
            f.write(json.dumps(d) + "\n")
    print(f"{len(planner.sink)} decisions -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
