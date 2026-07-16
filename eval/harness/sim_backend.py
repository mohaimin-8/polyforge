"""Sim backend: execute one RunSpec against the jcac_sim replay engine.

This backend runs anywhere Python runs — it is how the full matrix
executes on a laptop with no Docker. The cluster backend produces the same
result schema from a live kind cluster; analysis code cannot tell them
apart (and must not: `runs.backend` records which one produced each row).
"""

from __future__ import annotations

import time

import model  # research/jcac_sim via harness sys.path
import simulate
from controller import Weights

from .config import RunSpec
from .systems import SYSTEMS, global_mix_transform, lru_miss_cost_factor, tuned_params
from . import workloads

# The jitter stream must not correlate with the phase-offset stream drawn
# from the same seed in workloads.build.
_JITTER_SALT = 0x5F3759DF


def _apply_economy(economy: tuple) -> None:
    """Set the model economy for this run. Called unconditionally so a
    pooled worker process is stateless: an empty override restores the
    published constants exactly (model.set_economy resets first)."""
    e = dict(economy)
    tier_cost = {
        tier: e[key]
        for tier, key in (("small", "tier_cost_small"), ("mid", "tier_cost_mid"),
                          ("large", "tier_cost_large"))
        if key in e
    }
    model.set_economy(
        tier_cost_usd_per_req=tier_cost or None,
        cache_hit_max=e.get("cache_hit_max"),
        cache_half_mb=e.get("cache_half_mb"),
    )


def execute(run: RunSpec) -> dict:
    """Run one cell. Returns the standardized result dict the writer
    stores; raises on execution failure (the runner owns retry)."""
    spec = SYSTEMS[run.system]
    _apply_economy(run.economy)
    tenant_ids, buckets, configs, limits = workloads.build(
        run.workload, run.tenant_mix, run.cluster_size, run.seed, run.steps
    )

    params = dict(tuned_params().get(spec.controller, {}))
    params.update(spec.params)
    # Chaos settings are engine-level, not controller knobs: the controller
    # must not know (PREREG_CHAOS_SIM.md).
    chaos_outage = params.pop("chaos_planner_outage", None)
    chaos_kill = params.pop("chaos_replica_kill", None)
    if "isocost" in params:
        from .isocost import resolve as isocost_resolve

        params = isocost_resolve(params, run)
    if spec.seeded:
        params["seed"] = run.seed

    weights = None
    if spec.gamma is not None:
        weights = Weights(gamma=spec.gamma)

    started = time.time()
    result = simulate.run(
        spec.controller,
        tenant_ids,
        buckets,
        configs=configs,
        weights=weights,
        limits=limits,
        collect_rows=run.store_timeseries,
        controller_params=params or None,
        jitter_seed=run.seed ^ _JITTER_SALT,
        plan_demand_transform=global_mix_transform if spec.blind_classifier else None,
        miss_cost_factor=lru_miss_cost_factor() if spec.lru_eviction else 1.0,
        transition_costs=run.transition_costs,
        interference_injection=run.interference,
        chaos_planner_outage=chaos_outage,
        chaos_replica_kill=chaos_kill,
    )
    wall_s = time.time() - started

    return {
        "run": run,
        "wall_s": wall_s,
        "tenants": len(tenant_ids),
        "metrics": {
            "total_cost_usd": result.total_cost_usd,
            "mean_violation": result.mean_violation,
            "violation_step_share": result.violation_step_share,
            "mean_jain": result.mean_jain,
            "cache_hit_rate": result.cache_hit_rate,
            "crud_p95_ms": result.crud_p95_ms,
            "ai_p95_ms": result.ai_p95_ms,
            "steps": result.steps,
        },
        "timeseries": result.rows,
    }
