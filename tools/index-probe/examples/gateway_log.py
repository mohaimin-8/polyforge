"""Worked example 2 (log mode): decisions of the gateway's joint planner.

Runs the simulator's joint planner (anchored, convergent) on one design
point and writes, for every tenant decision, its box, its anchor, the
planner's own objective value of every candidate (other tenants held at
their plan) and the planner's choice, in index-probe's JSON-lines format.

    python gateway_log.py [--steps 10] [--out gateway_log.jsonl]
    python ../index_probe.py log gateway_log.jsonl
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for p in (REPO / "research" / "theory", REPO / "research" / "jcac_sim", REPO / "eval"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from controller_index import IndexedController, tenant_box, tenant_objective  # noqa: E402


class LoggingController(IndexedController):
    """Writes every tenant decision of every plan as one JSON line."""

    sink: list = []

    def plan(self, states, demands, interference=None):
        plans = super().plan(states, demands, interference)
        chosen = self._last_plan_ctx[0]
        with self._believed():
            for tid in sorted(chosen):
                phi, base, anchor = tenant_objective(self, tid)
                box = tenant_box(self, tid, base)
                LoggingController.sink.append({
                    "id": f"{tid}@{self._cycle}", "values": box, "anchor": list(anchor),
                    "phi": [[list(x), phi(x)] for x in itertools.product(*box)],
                    "chosen": [chosen[tid].replicas, chosen[tid].cache_mb, chosen[tid].tier]})
        return plans


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--steps", type=int, default=10)
    ap.add_argument("--out", type=Path, default=HERE / "gateway_log.jsonl")
    args = ap.parse_args()
    from controller_index import execute_indexed
    from harness import config as hconfig

    spec = hconfig.load(REPO / "eval" / "experiments" / "matrix_level2.yaml")
    run = next(r for r in hconfig.expand(spec) if r.system == "jcac_level_ratchet" and r.workload == "agentic"
               and r.tenant_mix == "whale" and r.cluster_size == "large" and r.rep == 0)
    LoggingController.sink = []
    execute_indexed(dataclasses.replace(run, steps=args.steps), LoggingController)
    with args.out.open("w", encoding="utf-8") as f:
        for d in LoggingController.sink:
            f.write(json.dumps(d) + "\n")
    print(f"{len(LoggingController.sink)} decisions -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
