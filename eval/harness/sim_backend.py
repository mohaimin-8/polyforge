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
from .systems import SYSTEMS, base_params, global_mix_transform, lru_miss_cost_factor
from . import workloads

# The jitter stream must not correlate with the phase-offset stream drawn
# from the same seed in workloads.build.
_JITTER_SALT = 0x5F3759DF


def _freeze_knobs(configs: dict, knobs: frozenset[str]) -> dict:
    """Pin the named knobs at the initial world for every tenant — the sim
    analogue of the Policy CRD min==max pin the live cache-only / tier-only
    ablations use (PREREG_WAVE4_LIVE_PLANE.md §Arms). Replicas pin via
    replica_min==replica_max (apply_action already clamps to that band);
    cache and tier pin via the bounds knob_admits enforces in the candidate
    lattice. Called only for arms that declare knob_freeze, so every other
    arm's configs are untouched and its records replay bit-identically."""
    from dataclasses import replace

    init = model.TenantState()
    out = {}
    for tid, cfg in configs.items():
        kw = {}
        if "replicas" in knobs:
            kw.update(replica_min=init.replicas, replica_max=init.replicas)
        if "cache" in knobs:
            kw.update(cache_min=init.cache_mb, cache_max=init.cache_mb)
        if "tier" in knobs:
            kw.update(tier_min=init.tier, tier_max=init.tier)
        out[tid] = replace(cfg, **kw) if kw else cfg
    return out


def _apply_tier_floor(configs: dict, floor: str) -> dict:
    """Raise every tenant's `tier_min` to `floor`, excluding every tier below
    it from the candidate lattice (the sim analogue of a Policy CRD tier
    floor) -- the opt-in `jcac_tier_floor` arm's "never shed to none"
    constraint. The published infeasible-lattice fallback constructs
    `TenantState(tier="none")` directly, outside `knob_admits`, so a floor
    alone does not stop shedding: `jcac_tier_floor` pairs it with
    `clamped_fallback`, whose shed serves on the cheapest admitted tier.
    Called only for arms that declare `tier_min`, so every other arm's
    configs are untouched and its records replay bit-identically."""
    from dataclasses import replace

    return {tid: replace(cfg, tier_min=floor) for tid, cfg in configs.items()}


def apply_arm_bounds(spec, configs: dict) -> dict:
    """An arm's per-tenant knob bounds -- its `knob_freeze` pins, then its
    `tier_min` floor -- applied to `configs`. Every substrate that runs an
    arm calls this, so an arm means the same thing on the matrix, on the
    real-trace replays and in the live-vs-sim comparison. Both attributes
    are empty on every published arm, so this returns `configs` unchanged
    there (R4); SystemSpec refuses a tier floor over a tier pin."""
    if spec.knob_freeze:
        configs = _freeze_knobs(configs, spec.knob_freeze)
    if spec.tier_min:
        configs = _apply_tier_floor(configs, spec.tier_min)
    return configs


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


def _apply_model_form(form: tuple) -> None:
    """Set the structural model form for this run (Wave 5). Called
    unconditionally, same statelessness contract as _apply_economy: an
    empty override restores the published forms exactly."""
    f = dict(form)
    p95_tail = None
    if "p95_tail_f0" in f:
        p95_tail = (f["p95_tail_f0"], f["p95_tail_b"])
    wu = {
        tier: f[key]
        for tier, key in (("mid", "wu_tier_mid"), ("large", "wu_tier_large"))
        if key in f
    }
    model.set_model_form(
        congestion_exponent=f.get("congestion_exponent"),
        p95_tail=p95_tail,
        mixture_p95=bool(f.get("mixture_p95", 0)),
        wu_tier_factor=wu or None,
        replica_capacity_wu=f.get("replica_capacity_wu"),
        wu_ai_scale=f.get("wu_ai_scale"),
        crud_base_scale=f.get("crud_base_scale"),
        cacheable_uniform=bool(f.get("cacheable_uniform", 0)),
        tier_latency_ms=({t: f[f"tier_latency_{t}"] for t in ("small", "mid", "large")}
                         if "tier_latency_small" in f else None),
    )


def execute(run: RunSpec) -> dict:
    """Run one cell. Returns the standardized result dict the writer
    stores; raises on execution failure (the runner owns retry)."""
    spec = SYSTEMS[run.system]
    _apply_economy(run.economy)
    _apply_model_form(run.model_form)
    tenant_ids, buckets, configs, limits = workloads.build(
        run.workload, run.tenant_mix, run.cluster_size, run.seed, run.steps
    )
    configs = apply_arm_bounds(spec, configs)

    params = base_params(spec, run.cluster_size)
    # Chaos settings are engine-level, not controller knobs: the controller
    # must not know (PREREG_CHAOS_SIM.md).
    chaos_outage = params.pop("chaos_planner_outage", None)
    chaos_kill = params.pop("chaos_replica_kill", None)
    # Eviction overhead is a property of the cache policy, not a control knob:
    # the controller must not be able to plan around its own bookkeeping cost
    # (PREREG_EVICTION_PARITY EP-H4, same rule as the chaos params above).
    evict_overhead_us = params.pop("evict_overhead_us", None)
    if "isocost" in params:
        from .isocost import resolve as isocost_resolve

        params = isocost_resolve(params, run)
    if spec.seeded:
        params["seed"] = run.seed

    # An arm's own weights reach the controller; unset fields keep the
    # published defaults, and an arm that sets none passes None (R4). beta was
    # forwarded only by trace_matrix.run_one, so the registered beta-ladder
    # arms (jcac_b4..b64) ran here as plain jcac (final check-up, 2026-10-13).
    weight_kw = {name: value for name, value in (("beta", spec.beta), ("gamma", spec.gamma))
                 if value is not None}
    weights = Weights(**weight_kw) if weight_kw else None

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
        initial_cache_mb=spec.static_cache_mb,
        evict_overhead_ms=(evict_overhead_us / 1000.0) if evict_overhead_us else None,
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
            # PREREG_EVICTION_PARITY reporting pair: what mean_violation's
            # saturation at 1.0 hides (unbounded severity, and AI shed to a
            # 30 s outage). Nullable in the DB, so old records are unaffected.
            "mean_excess": result.mean_excess,
            "tier_none_step_share": result.tier_none_step_share,
            # Guard metrics (separation campaign): reporting-only, nullable.
            "budget_overrun_step_share": result.budget_overrun_step_share,
            "clamp_violation_step_share": result.clamp_violation_step_share,
            "plan_fallback_share": result.plan_fallback_share,
            "plan_repaired_share": result.plan_repaired_share,
            "capped_cycle_share": result.capped_cycle_share,
            "mean_quality_loss": result.mean_quality_loss,
            "coordination_disagreement_share": result.coordination_disagreement_share,
            "steps": result.steps,
        },
        "timeseries": result.rows,
    }
