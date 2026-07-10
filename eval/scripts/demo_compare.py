"""The 15-second demo: what "normal use" costs vs PolyForge, live.

Runs the *same* multi-tenant scenario under several controllers and
prints a side-by-side table with deltas vs the norm (stock Kubernetes
HPA, tuned). Nothing is pre-recorded: the simulator solves every control
step as you watch. Logic lives in harness.demo; this is the CLI.

Usage (from eval/):
    python scripts/demo_compare.py                       # static vs HPA vs PolyForge
    python scripts/demo_compare.py --all                 # every baseline
    python scripts/demo_compare.py --workload agentic --mix whale
    python scripts/demo_compare.py --json                # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import demo, workloads  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--workload", default=demo.DEFAULT_SCENARIO["workload"],
                    choices=sorted(workloads.WORKLOAD_CLASSES))
    ap.add_argument("--mix", default=demo.DEFAULT_SCENARIO["tenant_mix"],
                    choices=sorted(workloads.TENANT_MIXES))
    ap.add_argument("--cluster", default=demo.DEFAULT_SCENARIO["cluster_size"],
                    choices=sorted(workloads.CLUSTER_SIZES))
    ap.add_argument("--steps", type=int, default=demo.DEFAULT_SCENARIO["steps"])
    ap.add_argument("--seed", type=int, default=demo.DEFAULT_SCENARIO["seed"])
    ap.add_argument("--all", action="store_true",
                    help="compare against every tuned baseline, not just static+HPA")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the table")
    args = ap.parse_args()

    systems = ("static", "hpa", "keda", "firm", "gptcache", "jcac") if args.all \
        else demo.DEFAULT_SYSTEMS

    def narrate(outcome: demo.SystemOutcome) -> None:
        if not args.json:
            print(f"  ran {outcome.label:26s} in {outcome.wall_s:5.1f}s")

    comparison = demo.compare(
        systems=systems, on_progress=narrate,
        workload=args.workload, tenant_mix=args.mix, cluster_size=args.cluster,
        steps=args.steps, seed=args.seed,
    )

    if args.json:
        print(json.dumps(comparison.to_dict(), indent=2))
        return

    spec = comparison.scenario
    print(f"\nScenario: {comparison.tenants} tenants sharing a {spec['cluster_size']} "
          f"cluster | {spec['workload']} traffic | '{spec['tenant_mix']}' tenant mix | "
          f"{spec['steps']} control intervals\n")
    print(demo.render_table(comparison))
    takeaway = demo.render_takeaway(comparison)
    if takeaway:
        print("\nThe sentence to say out loud:\n  " + takeaway)
    print("\nFull evidence: 1,800-run matrix + statistics in "
          "research/analysis/RESULTS.md,\nfigures in eval/results/figures/ "
          "(fig03 = this table as a picture).")


if __name__ == "__main__":
    main()
