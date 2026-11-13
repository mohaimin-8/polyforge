"""Our planner's index and one-step loss as shares of its own objective (WP15, the common scale).

INDEX_PILOT.md and DOSE_RESPONSE_PILOT.md report the index and the one-step loss in the planner's
objective units, without the objective's level. This script re-runs a few design points of their
regimes for a few control intervals with a logger that keeps, per tenant decision, phi at the joint
optimum, phi at the anchor and the spread of phi over the box, next to the index and the loss
(computed exactly as research/theory/controller_index.py does). EXPLORATORY: short runs, first
design points only; the shares are a scale, not a result.

    python our_scale.py --regime width/published/wide --points 2 --steps 12   # appends to data/our_scale.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for p in (REPO / "research" / "theory", REPO / "research" / "control2", REPO / "research" / "jcac_sim",
          REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from controller_index import (IndexedController, execute_indexed, tenant_box,  # noqa: E402
                              tenant_objective)
from interaction_index import analyse_table, remainder_table  # noqa: E402

OUT = HERE / "data" / "our_scale.json"
DOSE = {"dose/k1/w1": (1.0, 1), "dose/k1/w16": (1.0, 16), "dose/k2/w16": (2.0, 16)}


def _scale_plan(self, states, demands, interference=None):
    plans = super(IndexedController, self).plan(states, demands, interference)   # skip the default log
    chosen = self._last_plan_ctx[0]
    with self._believed():
        for tid in sorted(chosen):
            phi, base, anchor = tenant_objective(self, tid)
            box = tenant_box(self, tid, base)
            table = remainder_table(box, phi, anchor)
            a = analyse_table(box, table)
            feas = [v for v in table.phi.values() if v is not None]
            self.index_rows.append({"cycle": self._cycle, "index": a.oscillation, "loss": a.loss,
                                    "phi_joint": a.phi_joint, "phi_anchor": a.phi_anchor,
                                    "spread": (max(feas) - min(feas)) if feas else None})
    return plans


class ScaleController(IndexedController):
    plan = _scale_plan


def _jobs(regime: str) -> list:
    if regime in DOSE:
        import dose_response_pilot as dr
        kappa, width = DOSE[regime]
        dr.register_arms()

        class ScaleWidthController(dr.IndexedWidthController):
            plan = _scale_plan

        return [(run, ScaleWidthController) for k, run in dr.jobs()
                if k == kappa and run.system == dr.arm(width, "joint")]
    import index_pilot as ip
    return [(run, ScaleController) for r, run in ip.jobs() if r == regime]


def _summary(rows: list[dict]) -> dict:
    def mean(key, f=lambda r: True):
        v = [r[key] for r in rows if r[key] is not None and f(r)]
        return statistics.fmean(v) if v else None

    ok = [r for r in rows if r["index"] is not None and r["phi_joint"]]
    return {"decisions": len(rows), "index_mean": mean("index"), "loss_mean": mean("loss"),
            "phi_joint_mean": mean("phi_joint"), "spread_mean": mean("spread"),
            "index_share_of_phi_joint_mean": statistics.fmean(r["index"] / r["phi_joint"] for r in ok),
            "index_share_of_phi_joint_median": statistics.median(r["index"] / r["phi_joint"] for r in ok),
            "loss_share_of_phi_joint_mean": statistics.fmean(r["loss"] / r["phi_joint"] for r in ok
                                                             if r["loss"] is not None),
            "index_share_of_spread_mean": statistics.fmean(r["index"] / r["spread"] for r in ok if r["spread"]),
            "index_positive_share": sum(1 for r in ok if r["index"] > 1e-12) / len(ok)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regime", action="append", required=True)
    ap.add_argument("--points", type=int, default=2)
    ap.add_argument("--steps", type=int, default=12)
    args = ap.parse_args()
    store = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    for regime in args.regime:
        every = _jobs(regime)
        if not every:
            raise SystemExit(f"no runs for regime {regime!r}")
        jobs = every[::max(1, len(every) // args.points)][:args.points]   # spread over the design points
        rows, t0, points = [], time.time(), []
        for run, cls in jobs:
            _, r = execute_indexed(dataclasses.replace(run, steps=args.steps), cls)
            rows += [x for x in r if x["cycle"] <= args.steps]
            points.append(f"{run.workload}/{run.tenant_mix}/{run.cluster_size}")
        store[regime] = {**_summary(rows), "points": points, "steps": args.steps}
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(json.dumps(store, indent=1, sort_keys=True), encoding="utf-8")   # after every regime
        print(f"{regime}: {len(rows)} decisions in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
