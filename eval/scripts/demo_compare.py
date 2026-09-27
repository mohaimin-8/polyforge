"""The live demo: several controllers, the same tenants and traffic, side by side.

Runs one multi-tenant scenario under several controllers and prints a
side-by-side table with deltas vs the baseline. Nothing is pre-recorded:
every arm runs through the pre-registered campaigns' own code path as you
watch. Logic lives in harness.demo; this is the CLI.

The default cast is the fair one (audit 2026-09-26): HPA and KEDA with a
competently sized cache, the tuned layered three-knob stack, and PolyForge.

Usage (from eval/):
    python scripts/demo_compare.py                     # the fair cast, one scenario
    python scripts/demo_compare.py --plot trace.png    # ... and draw every knob over time
    python scripts/demo_compare.py --sweep             # J for all five workload classes
    python scripts/demo_compare.py --published         # the pre-audit cast, for contrast
    python scripts/demo_compare.py --workload agentic --mix whale
    python scripts/demo_compare.py --json              # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import demo, workloads  # noqa: E402

RECORDS_STEPS = 120
RECORDS_TENANTS = 8


def _parse() -> argparse.Namespace:
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
    cast = ap.add_mutually_exclusive_group()
    cast.add_argument("--published", action="store_true",
                      help="the pre-audit cast (static, published HPA, published PolyForge)")
    cast.add_argument("--systems", help="comma-separated registry names (first is the baseline "
                                        "unless --baseline is given)")
    ap.add_argument("--baseline", help="system the deltas are computed against")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--sweep", action="store_true",
                      help="J for each of the five headline workload classes")
    mode.add_argument("--plot", metavar="PNG", help="also draw every arm's knobs over time")
    mode.add_argument("--json", action="store_true", help="emit JSON instead of the table")
    return ap.parse_args()


def _cast(args: argparse.Namespace) -> tuple[tuple[str, ...], str]:
    if args.published:
        systems, baseline = demo.PUBLISHED_SYSTEMS, demo.PUBLISHED_BASELINE
    elif args.systems:
        systems = tuple(s.strip() for s in args.systems.split(",") if s.strip())
        if not systems:
            raise SystemExit("--systems must name at least one registry system")
        baseline = systems[0]
    else:
        systems, baseline = demo.DEFAULT_SYSTEMS, demo.DEFAULT_BASELINE
    return systems, args.baseline or baseline


def _print_notes(args: argparse.Namespace, tenants: int) -> None:
    if args.published:
        print("\nThis is the PRE-AUDIT cast: the published HPA paid an LRU inference "
              "charge and kept a\npinned 128 MB cache. Rerun without --published for "
              "the fair comparison.")
    if args.steps != RECORDS_STEPS or tenants != RECORDS_TENANTS:
        print(f"\nNote: J's cost term is normalised for {RECORDS_TENANTS} tenants x "
              f"{RECORDS_STEPS} steps, so this J is not on the records' scale.")


def main() -> None:
    args = _parse()
    systems, baseline = _cast(args)
    scenario = dict(workload=args.workload, tenant_mix=args.mix, cluster_size=args.cluster,
                    steps=args.steps, seed=args.seed)

    if args.sweep:
        print(f"Running {len(systems)} controllers on each of "
              f"{len(demo.HEADLINE_WORKLOADS)} workload classes...")
        rest = {k: v for k, v in scenario.items() if k != "workload"}
        comparisons = demo.sweep(systems=systems, baseline=baseline, **rest)
        print(f"\n{comparisons[0].tenants} tenants | '{args.mix}' mix | {args.cluster} "
              f"cluster | {args.steps} control intervals | seed {args.seed}\n")
        print(demo.render_sweep(comparisons))
        print(f"\nOne seed per row. The pre-registered test over 60 design points x 5 "
              f"seeds is {demo.EVIDENCE_RECORD}.")
        _print_notes(args, comparisons[0].tenants)
        return

    def narrate(outcome: demo.SystemOutcome) -> None:
        if not args.json:
            print(f"  ran {outcome.label:24s} in {outcome.wall_s:5.1f}s")

    comparison = demo.compare(systems=systems, baseline=baseline, on_progress=narrate,
                              collect_rows=bool(args.plot), **scenario)
    if args.json:
        print(json.dumps(comparison.to_dict(), indent=2))
        return

    print(f"\nScenario: {comparison.tenants} tenants sharing a {args.cluster} cluster | "
          f"{args.workload} traffic | '{args.mix}' tenant mix | {args.steps} control "
          f"intervals | seed {args.seed}\n")
    print(demo.render_table(comparison))
    takeaway = demo.render_takeaway(comparison)
    if takeaway:
        print("\nWhat this shows:\n  " + takeaway)
    _print_notes(args, comparison.tenants)
    if args.plot:
        print(f"\nKnob trace written to {demo.plot_trace(comparison, args.plot)}")


if __name__ == "__main__":
    main()
