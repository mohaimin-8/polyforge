"""Trace-replay simulation engine (W30).

Replays a normalized trace (W25b schema) through a controller, one
CONTROL_INTERVAL_S bucket at a time, and records what the system model says
each tenant experienced. The controller only ever sees demand up to the
current bucket; the engine evaluates its choice against the bucket that
actually arrives next — forecast error is part of the score.

Usage:
    python simulate.py --trace ../traces/out/azure_synth.csv.gz \
        --controller jcac --alpha 1 --beta 2 --gamma 0.5 \
        --out ../results/jcac/azure_jcac
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path

import baselines
import model
from controller import ClusterLimits, JCACController, Weights
from model import CRUD_KINDS, Demand, TenantConfig, TenantState, evaluate_step, jain_index

SLO_CLASS_CYCLE = ("premium", "standard", "best-effort")


def load_trace_buckets(path: Path, interval_s: int = model.CONTROL_INTERVAL_S):
    """Bucket a trace into per-interval, per-tenant Demand objects.

    Returns (tenant_ids, buckets) where buckets[t][tenant_id] -> Demand.
    """
    counts: dict[int, dict[str, dict[str, float]]] = {}
    latency_sum: dict[int, dict[str, float]] = {}
    latency_n: dict[int, dict[str, int]] = {}
    tenants: set[str] = set()

    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", newline="") as f:
        for row in csv.DictReader(f):
            step = int(row["timestamp_ms"]) // (interval_s * 1000)
            tid = row["tenant_id"]
            kind = row["request_kind"]
            tenants.add(tid)
            counts.setdefault(step, {}).setdefault(tid, {}).setdefault(kind, 0.0)
            counts[step][tid][kind] += 1.0
            if kind in CRUD_KINDS:
                latency_sum.setdefault(step, {}).setdefault(tid, 0.0)
                latency_n.setdefault(step, {}).setdefault(tid, 0)
                latency_sum[step][tid] += float(row["expected_latency_ms"])
                latency_n[step][tid] += 1

    tenant_ids = sorted(tenants)
    steps = range(min(counts), max(counts) + 1) if counts else range(0)
    buckets = []
    for step in steps:
        per_tenant = {}
        for tid in tenant_ids:
            kinds = counts.get(step, {}).get(tid, {})
            rps = {k: n / interval_s for k, n in kinds.items()}
            n = latency_n.get(step, {}).get(tid, 0)
            base = latency_sum[step][tid] / n if n else 50.0
            per_tenant[tid] = Demand(rps=rps, crud_base_ms=base)
        buckets.append(per_tenant)
    return tenant_ids, buckets


def default_configs(tenant_ids: list[str]) -> dict[str, TenantConfig]:
    """Deterministic tenant setup: SLO classes cycle so every class is
    represented regardless of which trace is replayed."""
    return {
        tid: TenantConfig(
            tenant_id=tid,
            slo_class=SLO_CLASS_CYCLE[i % len(SLO_CLASS_CYCLE)],
            hourly_budget_usd=5.0,
        )
        for i, tid in enumerate(tenant_ids)
    }


@dataclass
class RunResult:
    controller: str
    total_cost_usd: float = 0.0
    mean_violation: float = 0.0
    violation_step_share: float = 0.0  # share of tenant-steps with any violation
    mean_jain: float = 0.0
    steps: int = 0
    rows: list = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "controller": self.controller,
            "steps": self.steps,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "mean_violation": round(self.mean_violation, 4),
            "violation_step_share": round(self.violation_step_share, 4),
            "mean_jain": round(self.mean_jain, 4),
        }


def run(
    controller_name: str,
    tenant_ids: list[str],
    buckets: list[dict[str, Demand]],
    configs: dict[str, TenantConfig] | None = None,
    weights: Weights | None = None,
    limits: ClusterLimits | None = None,
    max_steps: int | None = None,
    collect_rows: bool = True,
) -> RunResult:
    configs = configs or default_configs(tenant_ids)
    if controller_name == "jcac":
        ctl = JCACController(configs, weights=weights, limits=limits)
    else:
        ctl = baselines.make_baseline(controller_name, configs)

    states = {tid: TenantState() for tid in tenant_ids}
    result = RunResult(controller=controller_name)
    viol_sum = jain_sum = 0.0
    viol_steps = tenant_steps = 0

    horizon = buckets[:max_steps] if max_steps else buckets
    for step, demands in enumerate(horizon):
        # Controller decides on this bucket's observed demand...
        plans = ctl.plan(states, demands)
        states = {tid: (p.state if hasattr(p, "state") else p) for tid, p in plans.items()}
        # ...and is scored against the next bucket that actually arrives.
        if step + 1 >= len(horizon):
            break
        actual = horizon[step + 1]
        satisfactions = []
        for tid in tenant_ids:
            m = evaluate_step(configs[tid], states[tid], actual[tid])
            result.total_cost_usd += m.cost_usd
            viol_sum += m.violation
            viol_steps += 1 if m.violation > 0.0 else 0
            tenant_steps += 1
            satisfactions.append(1.0 - m.violation)
            if collect_rows:
                result.rows.append({
                    "step": step + 1,
                    "tenant": tid,
                    "replicas": states[tid].replicas,
                    "cache_mb": states[tid].cache_mb,
                    "tier": states[tid].tier,
                    "cost_usd": round(m.cost_usd, 6),
                    "violation": round(m.violation, 4),
                    "crud_p95_ms": round(m.crud_p95_ms, 1),
                    "ai_p95_ms": round(m.ai_p95_ms, 1),
                })
        jain_sum += jain_index(satisfactions)
        result.steps += 1

    if tenant_steps:
        result.mean_violation = viol_sum / tenant_steps
        result.violation_step_share = viol_steps / tenant_steps
    if result.steps:
        result.mean_jain = jain_sum / result.steps
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--controller", default="jcac",
                    choices=["jcac", "static", "hpa", "keda", "layered"])
    ap.add_argument("--alpha", type=float, default=1.0)
    ap.add_argument("--beta", type=float, default=2.0)
    ap.add_argument("--gamma", type=float, default=0.5)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--out", default=None, help="output prefix (writes .csv and .json)")
    args = ap.parse_args()

    tenant_ids, buckets = load_trace_buckets(Path(args.trace))
    weights = Weights(alpha=args.alpha, beta=args.beta, gamma=args.gamma)
    result = run(args.controller, tenant_ids, buckets,
                 weights=weights, max_steps=args.max_steps)

    print(json.dumps(result.summary(), indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(f"{out}.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(result.rows[0].keys()))
            writer.writeheader()
            writer.writerows(result.rows)
        with open(f"{out}.json", "w") as f:
            json.dump(result.summary(), f, indent=2)


if __name__ == "__main__":
    main()
