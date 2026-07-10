"""Baseline controllers JCAC is compared against (W30).

Each baseline controls a strict subset of the knobs, or controls them all
but independently — the thesis claim is that per-layer local optima do not
compose into a joint optimum, so the interesting baseline is `layered`.

All baselines share the system model in model.py and the same clamped
transition (`apply_action`), so no baseline can cheat the guardrails.
"""

from __future__ import annotations

import math

from model import (
    AI_KINDS,
    CACHE_LEVELS_MB,
    CACHEABLE_FRACTION,
    REPLICA_CAPACITY_WU,
    SLO_BASE_MS,
    SLO_CLASS_FACTOR,
    Demand,
    TenantConfig,
    TenantState,
    apply_action,
    evaluate_step,
    hit_rate,
)


class StaticController:
    """Fixed configuration, the do-nothing floor."""

    name = "static"

    def __init__(self, configs: dict[str, TenantConfig]):
        self.configs = configs

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        return dict(states)


class HPAController:
    """Kubernetes HPA semantics on utilization only: desired = ceil(current ·
    ρ/ρ_target), clamped to ±2 per interval. Cache and tier stay fixed."""

    name = "hpa"
    target_rho = 0.6

    def __init__(self, configs: dict[str, TenantConfig]):
        self.configs = configs

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            demand = demands.get(tid, Demand())
            rho = demand.work_units(state.cache_mb) / max(1, state.replicas * REPLICA_CAPACITY_WU)
            desired = math.ceil(state.replicas * rho / self.target_rho) if rho > 0 else config.replica_min
            delta = max(-2, min(2, desired - state.replicas))
            out[tid] = apply_action(config, state, delta, state.cache_mb, state.tier)
        return out


class KEDAController:
    """Event-driven scaling on raw request rate (KEDA's rps scaler):
    replicas = ceil(rps / per-replica target). Cache and tier stay fixed."""

    name = "keda"
    rps_per_replica = 8.0

    def __init__(self, configs: dict[str, TenantConfig]):
        self.configs = configs

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            rps = demands.get(tid, Demand()).total_rps()
            desired = max(config.replica_min, math.ceil(rps / self.rps_per_replica))
            delta = max(-2, min(2, desired - state.replicas))
            out[tid] = apply_action(config, state, delta, state.cache_mb, state.tier)
        return out


class LayeredController:
    """Every knob has its own well-tuned local controller, none of them
    talk to each other, and none of them see cost or fairness:

    - replicas: HPA rule (utilization target)
    - cache: grow a level when there is meaningful cacheable traffic and
      headroom in the hit-rate curve, shrink when the cache is idle
    - tier: reactive rule on last observed AI p95 vs the tenant's target

    This is the strongest realistic non-joint baseline — it is what a
    competent team gets from stock components without a joint optimizer.
    """

    name = "layered"
    target_rho = 0.6

    def __init__(self, configs: dict[str, TenantConfig]):
        self.configs = configs

    def plan(self, states: dict[str, TenantState], demands: dict[str, Demand]):
        out = {}
        for tid, state in states.items():
            config = self.configs[tid]
            demand = demands.get(tid, Demand())

            rho = demand.work_units(state.cache_mb) / max(1, state.replicas * REPLICA_CAPACITY_WU)
            desired = math.ceil(state.replicas * rho / self.target_rho) if rho > 0 else config.replica_min
            delta = max(-2, min(2, desired - state.replicas))

            cache_mb = self._cache_rule(state, demand)
            tier = self._tier_rule(config, state, demand)
            out[tid] = apply_action(config, state, delta, cache_mb, tier)
        return out

    def _cache_rule(self, state: TenantState, demand: Demand) -> int:
        cacheable_rps = sum(
            demand.rps.get(k, 0.0) * CACHEABLE_FRACTION[k] for k in AI_KINDS
        )
        i = CACHE_LEVELS_MB.index(state.cache_mb) if state.cache_mb in CACHE_LEVELS_MB else 0
        if cacheable_rps > 0.5 and hit_rate(state.cache_mb) < 0.7 and i < len(CACHE_LEVELS_MB) - 1:
            return CACHE_LEVELS_MB[i + 1]
        if cacheable_rps < 0.05 and i > 0:
            return CACHE_LEVELS_MB[i - 1]
        return state.cache_mb

    def _tier_rule(self, config: TenantConfig, state: TenantState, demand: Demand) -> str:
        ai_rps = sum(demand.rps.get(k, 0.0) for k in AI_KINDS)
        if ai_rps <= 0.0:
            return state.tier
        target = SLO_BASE_MS["ai"] * SLO_CLASS_FACTOR[config.slo_class]
        metrics = evaluate_step(config, state, demand)
        order = ["small", "mid", "large"]
        current = state.tier if state.tier in order else "small"
        i = order.index(current)
        if metrics.ai_p95_ms > target and i < len(order) - 1:
            return order[i + 1]
        if metrics.ai_p95_ms < 0.3 * target and i > 0:
            return order[i - 1]
        return current


def make_baseline(name: str, configs: dict[str, TenantConfig]):
    for cls in (StaticController, HPAController, KEDAController, LayeredController):
        if cls.name == name:
            return cls(configs)
    raise ValueError(f"unknown baseline {name!r}")
