"""The 15-second demo: what "normal use" costs vs PolyForge, live.

Runs the *same* multi-tenant scenario (default: AI-uncacheable traffic on
a medium cluster — agent chains plus chat, the case where every knob
matters) under three controllers:

  static  — "throw money at it": over-provisioned to peak, the no-ops floor
  hpa     — "normal use": stock Kubernetes autoscaling, tuned (rho=0.3)
  jcac    — PolyForge: joint control of replicas + cache + model tier

and prints a side-by-side table with deltas vs the HPA norm. Nothing is
pre-recorded: the simulator solves every control step as you watch.

Usage (from eval/):  python scripts/demo_compare.py [--workload X] [--mix Y]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

from harness import workloads  # noqa: E402  (wires the sim path)
from harness.systems import SYSTEMS, lru_miss_cost_factor, tuned_params  # noqa: E402
import simulate  # noqa: E402

CONTENDERS = [
    ("static", "Over-provision (peak)"),
    ("hpa", "Kubernetes HPA (tuned)"),
    ("jcac", "PolyForge (JCAC)"),
]

ROWS = [
    ("total_cost_usd", "cost for the 20-min window", "${:,.2f}", False),
    ("mean_violation", "mean SLO violation", "{:.3f}", False),
    ("violation_step_share", "tenant-steps violating", "{:.1%}", False),
    ("mean_jain", "Jain fairness index", "{:.3f}", True),
    ("cache_hit_rate", "AI cache hit rate", "{:.1%}", True),
]


def run_one(name: str, tenant_ids, buckets, configs, limits, seed: int):
    spec = SYSTEMS[name]
    params = dict(tuned_params().get(spec.controller, {}))
    params.update(spec.params)
    started = time.time()
    result = simulate.run(
        spec.controller, tenant_ids, buckets, configs=configs, limits=limits,
        collect_rows=False, controller_params=params or None,
        jitter_seed=seed, miss_cost_factor=lru_miss_cost_factor() if spec.lru_eviction else 1.0,
    )
    return result, time.time() - started


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workload", default="ai_uncacheable",
                    choices=sorted(workloads.WORKLOAD_CLASSES))
    ap.add_argument("--mix", default="uniform", choices=sorted(workloads.TENANT_MIXES))
    ap.add_argument("--cluster", default="medium", choices=sorted(workloads.CLUSTER_SIZES))
    ap.add_argument("--steps", type=int, default=120)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    tenant_ids, buckets, configs, limits = workloads.build(
        args.workload, args.mix, args.cluster, args.seed, args.steps
    )
    print(f"\nScenario: {len(tenant_ids)} tenants sharing a {args.cluster} cluster | "
          f"{args.workload} traffic | '{args.mix}' tenant mix | "
          f"{args.steps} control intervals (20 simulated minutes)\n")

    results = {}
    for name, label in CONTENDERS:
        result, wall = run_one(name, tenant_ids, buckets, configs, limits, args.seed)
        results[name] = result
        print(f"  ran {label:26s} in {wall:5.1f}s")

    hpa = results["hpa"]
    print()
    header = f"{'':34s}" + "".join(f"{label:>26s}" for _, label in CONTENDERS)
    print(header)
    print("-" * len(header))
    for attr, label, fmt, higher_better in ROWS:
        cells = ""
        for name, _ in CONTENDERS:
            value = getattr(results[name], attr)
            cell = fmt.format(value)
            base = getattr(hpa, attr)
            # A percentage against a near-zero base is noise, not signal.
            if name != "hpa" and abs(base) > 1e-4:
                delta = (value - base) / abs(base)
                arrow = ("+" if delta >= 0 else "-") + f"{abs(delta):.0%}"
                good = (delta > 0) == higher_better if abs(delta) > 0.005 else None
                mark = "" if good is None else (" *" if good else "")
                cell = f"{cell} ({arrow}{mark})"
            cells += f"{cell:>26s}"
        print(f"{label:34s}{cells}")

    jc, st = results["jcac"], results["static"]
    # The SLO clause is computed, never asserted: say only what the table
    # on screen just showed.
    if jc.mean_violation <= hpa.mean_violation:
        slo_clause = "while violating SLOs less"
    else:
        slo_clause = (f"trading {jc.mean_violation - hpa.mean_violation:.1%} points "
                      "of SLO headroom for it")
    hit_clause = ""
    if hpa.cache_hit_rate > 1e-4 and jc.cache_hit_rate > hpa.cache_hit_rate:
        hit_clause = (f" and serving {jc.cache_hit_rate / hpa.cache_hit_rate:,.1f}x "
                      "more AI traffic from cache")
    print(f"""
The sentence to say out loud:
  "Same tenants, same traffic, same cluster: stock Kubernetes autoscaling
   spends ${hpa.total_cost_usd:,.2f} and over-provisioning spends ${st.total_cost_usd:,.2f};
   PolyForge spends ${jc.total_cost_usd:,.2f} -- {1 - jc.total_cost_usd / hpa.total_cost_usd:.0%} less than the norm --
   {slo_clause}{hit_clause}."

Full evidence: 1,800-run matrix + statistics in research/analysis/RESULTS.md,
figures in eval/results/figures/ (fig03 = this table as a picture).
""")


if __name__ == "__main__":
    main()
